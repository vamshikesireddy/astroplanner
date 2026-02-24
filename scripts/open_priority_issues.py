"""
open_priority_issues.py
=======================
Reads _priority_changes.json (produced by check_unistellar_priorities.py) and
creates GitHub Issues for each priority change — both ADDED and REMOVED objects.

Skips any change whose designation already appears in an open issue title
to avoid duplicate notifications across back-to-back workflow runs.

Requires environment variables (automatically set in GitHub Actions):
    GITHUB_TOKEN       — personal access token or Actions GITHUB_TOKEN
    GITHUB_REPOSITORY  — e.g. "vamshikesireddy/astro_coordinates"

NOT intended for manual local use (needs GitHub credentials).
"""

import json
import os
import sys
import urllib.parse

import requests

INPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "_priority_changes.json")
GH_API = "https://api.github.com"
GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def open_issues(changes, token, repo):
    GH_HEADERS["Authorization"] = f"Bearer {token}"

    created = 0
    skipped = 0

    for c in changes:
        desig = c["designation"]
        category = c["category"]
        change = c["change"]

        if change == "ADDED":
            issue_title = f"Unistellar priority added: {desig} ({category})"
            label = "priority-added"
            body = _build_added_body(desig, category)
        else:
            issue_title = f"Unistellar priority removed: {desig} ({category})"
            label = "priority-removed"
            body = _build_removed_body(desig, category)

        # --- Deduplication: skip if an open issue already mentions this designation ---
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

        # --- Create the issue ---
        try:
            cr = requests.post(
                f"{GH_API}/repos/{repo}/issues",
                headers=GH_HEADERS,
                json={"title": issue_title, "body": body, "labels": [label]},
                timeout=15,
            )
            cr.raise_for_status()
            issue_url = cr.json().get("html_url", "")
            print(f"  Created issue for {desig}: {issue_url}")
            created += 1
        except Exception as e:
            print(f"  ERROR creating issue for {desig}: {e}", file=sys.stderr)

    print(f"\n  Done — {created} issue(s) created, {skipped} skipped (already open).")


def _build_added_body(desig, category):
    yaml_file = "comets.yaml" if category == "comet" else "asteroids.yaml"
    return f"""## New Unistellar Priority: {desig}

Unistellar has **added** `{desig}` to their active {category} missions page, but it is not yet in our `unistellar_priority` list.

### Action needed
1. Verify the object is on our watchlist in `{yaml_file}` — if not, add it under `{category}s:`
2. Add `{desig}` to `unistellar_priority:` in `{yaml_file}`
3. Close this issue once updated

**Source:** [Unistellar {category} missions]({'https://science.unistellar.com/comets/missions/' if category == 'comet' else 'https://science.unistellar.com/planetary-defense/missions/'})
"""


def _build_removed_body(desig, category):
    yaml_file = "comets.yaml" if category == "comet" else "asteroids.yaml"
    return f"""## Unistellar Priority Removed: {desig}

Unistellar has **removed** `{desig}` from their active {category} missions page, but it is still in our `unistellar_priority` list.

### Action needed
1. Remove `{desig}` from `unistellar_priority:` in `{yaml_file}`
2. Optionally keep it in the main `{category}s:` watchlist if you still want to track it
3. Close this issue once updated

**Source:** [Unistellar {category} missions]({'https://science.unistellar.com/comets/missions/' if category == 'comet' else 'https://science.unistellar.com/planetary-defense/missions/'})
"""


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print(
            "ERROR: GITHUB_TOKEN and GITHUB_REPOSITORY environment variables must be set.\n"
            "This script is intended to run inside GitHub Actions.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.exists(INPUT_FILE):
        print("No _priority_changes.json found — nothing to do.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        changes = json.load(f)

    if not changes:
        print("_priority_changes.json is empty — nothing to do.")
        return

    print(f"Opening GitHub Issues for {len(changes)} priority change(s) in {repo} ...")
    open_issues(changes, token, repo)


if __name__ == "__main__":
    main()
