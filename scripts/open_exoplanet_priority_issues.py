"""
open_exoplanet_priority_issues.py
==================================
Reads _exoplanet_priority_changes.json and creates GitHub Issues for each change.

Skips any change whose designation already appears in an open issue title.

Requires:
    GITHUB_TOKEN       — personal access token or Actions GITHUB_TOKEN
    GITHUB_REPOSITORY  — e.g. "vamshikesireddy/astroplanner"
"""

import json
import os
import sys
import urllib.parse

import requests

INPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "_exoplanet_priority_changes.json")
GH_API = "https://api.github.com"
GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

_LABEL_MAP = {
    "ADDED":            "priority-added",
    "REMOVED":          "priority-removed",
    "PRIORITY_ADDED":   "priority-added",
    "PRIORITY_REMOVED": "priority-removed",
}


def _build_body(desig, change):
    url = "https://science.unistellar.com/exoplanets/missions/"
    if change == "ADDED":
        return f"""## New Unistellar Exoplanet Mission: {desig}

Unistellar has **added** `{desig}` to their exoplanet missions page, but it is not yet in our `exoplanets.yaml` active list.

### Action needed
1. Add `{desig}` to `active:` in `exoplanets.yaml`
2. Close this issue once updated

**Source:** [Unistellar Exoplanet Missions]({url})
"""
    if change == "REMOVED":
        return f"""## Unistellar Exoplanet Mission Ended: {desig}

Unistellar has **removed** `{desig}` from their exoplanet missions page, but it is still in our `exoplanets.yaml` active list.

### Action needed
1. Remove `{desig}` from `active:` in `exoplanets.yaml`
2. Close this issue once updated

**Source:** [Unistellar Exoplanet Missions]({url})
"""
    if change == "PRIORITY_ADDED":
        return f"""## Unistellar Exoplanet Priority Campaign: {desig}

`{desig}` now appears as a **featured campaign** (heading) on the Unistellar exoplanet missions page, but is not yet in our `priorities` map.

### Action needed
1. Add `{desig}: "⭐ PRIORITY"` to `priorities:` in `exoplanets.yaml`
2. Close this issue once updated

**Source:** [Unistellar Exoplanet Missions]({url})
"""
    return f"""## Unistellar Exoplanet Priority Ended: {desig}

`{desig}` is no longer featured as a heading on the Unistellar exoplanet missions page, but is still in our `priorities` map.

### Action needed
1. Remove `{desig}` from `priorities:` in `exoplanets.yaml` (or keep for manual override)
2. Close this issue once updated

**Source:** [Unistellar Exoplanet Missions]({url})
"""


def open_issues(changes, token, repo):
    GH_HEADERS["Authorization"] = f"Bearer {token}"
    created = skipped = 0

    for c in changes:
        desig  = c["designation"]
        change = c["change"]
        label  = _LABEL_MAP.get(change, "priority-added")
        title  = f"Unistellar exoplanet {change.lower().replace('_', ' ')}: {desig}"
        body   = _build_body(desig, change)

        search_url = (
            f"{GH_API}/search/issues"
            f"?q=repo:{repo}+is:issue+is:open+in:title"
            f"+{urllib.parse.quote(desig)}"
        )
        try:
            sr = requests.get(search_url, headers=GH_HEADERS, timeout=15)
            sr.raise_for_status()
            if sr.json().get("total_count", 0) > 0:
                print(f"  [{desig}] open issue already exists — skipping.")
                skipped += 1
                continue
        except Exception as e:
            print(f"  WARNING: Dedup check failed for {desig}: {e}", file=sys.stderr)

        try:
            cr = requests.post(
                f"{GH_API}/repos/{repo}/issues",
                headers=GH_HEADERS,
                json={"title": title, "body": body, "labels": [label]},
                timeout=15,
            )
            cr.raise_for_status()
            print(f"  Created: {cr.json().get('html_url', '')}")
            created += 1
        except Exception as e:
            print(f"  ERROR creating issue for {desig}: {e}", file=sys.stderr)

    print(f"\n  Done — {created} created, {skipped} skipped.")


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo  = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print(
            "ERROR: GITHUB_TOKEN and GITHUB_REPOSITORY must be set.\n"
            "This script is intended to run inside GitHub Actions.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.exists(INPUT_FILE):
        print("No _exoplanet_priority_changes.json found — nothing to do.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        changes = json.load(f)

    if not changes:
        print("_exoplanet_priority_changes.json is empty — nothing to do.")
        return

    print(f"Opening GitHub Issues for {len(changes)} exoplanet change(s) in {repo} ...")
    open_issues(changes, token, repo)


if __name__ == "__main__":
    main()
