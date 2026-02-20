"""
open_comet_issues.py
====================
Reads _new_comets.json (produced by check_new_comets.py) and creates one
GitHub Issue per new comet using the GitHub REST API.

Skips any comet whose designation already appears in an open issue title
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

INPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "_new_comets.json")
GH_API = "https://api.github.com"
GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def open_issues(new_comets, token, repo):
    GH_HEADERS["Authorization"] = f"Bearer {token}"

    created = 0
    skipped = 0

    for c in new_comets:
        desig = c["designation"]
        pdes = c.get("pdes", desig)
        disc = c.get("disc", "unknown")
        H = c.get("H")
        q = c.get("q")
        e = c.get("e")

        issue_title = f"New comet: {desig} (discovered {disc})"

        # --- Deduplication: skip if an open issue already mentions this pdes ---
        search_url = (
            f"{GH_API}/search/issues"
            f"?q=repo:{repo}+is:issue+is:open+in:title"
            f"+{urllib.parse.quote(pdes)}"
        )
        try:
            sr = requests.get(search_url, headers=GH_HEADERS, timeout=15)
            sr.raise_for_status()
            if sr.json().get("total_count", 0) > 0:
                print(f"  [{pdes}] open issue already exists — skipping.")
                skipped += 1
                continue
        except Exception as e:
            print(f"  WARNING: Dedup check failed for {pdes}: {e}", file=sys.stderr)

        # --- Build issue body ---
        sbdb_url = (
            f"https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html"
            f"#/?sstr={urllib.parse.quote(pdes)}"
        )
        horizons_url = (
            f"https://ssd.jpl.nasa.gov/horizons/app.html"
            f"#/?sstr={urllib.parse.quote(pdes)}"
        )

        table_rows = [
            f"| Designation | `{pdes}` |",
            f"| Discovered | {disc} |",
        ]
        if H is not None:
            table_rows.append(f"| Abs. magnitude (H) | {H} |")
        if q is not None:
            table_rows.append(f"| Perihelion dist. (q) | {q} AU |")
        if e is not None:
            table_rows.append(f"| Eccentricity (e) | {e} |")

        table = "\n".join(table_rows)

        body = f"""## New Comet Discovery: {desig}

A comet not yet on the watchlist was recently discovered and may be worth tracking.

| Field | Value |
|---|---|
{table}

**Links:**
- [JPL Small Body Database]({sbdb_url})
- [JPL Horizons]({horizons_url})

---
To add to the watchlist, edit `comets.yaml` and add `{desig}` under `comets:`.
Close this issue once reviewed (whether added or skipped).
"""

        # --- Create the issue ---
        try:
            cr = requests.post(
                f"{GH_API}/repos/{repo}/issues",
                headers=GH_HEADERS,
                json={"title": issue_title, "body": body},
                timeout=15,
            )
            cr.raise_for_status()
            issue_url = cr.json().get("html_url", "")
            print(f"  Created issue for {desig}: {issue_url}")
            created += 1
        except Exception as e:
            print(f"  ERROR creating issue for {desig}: {e}", file=sys.stderr)

    print(f"\n  Done — {created} issue(s) created, {skipped} skipped (already open).")


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
        print("No _new_comets.json found — nothing to do.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        new_comets = json.load(f)

    if not new_comets:
        print("_new_comets.json is empty — nothing to do.")
        return

    print(f"Opening GitHub Issues for {len(new_comets)} new comet(s) in {repo} ...")
    open_issues(new_comets, token, repo)


if __name__ == "__main__":
    main()
