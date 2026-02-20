"""
check_new_comets.py
===================
Queries the JPL Small Body Database (SBDB) for comets discovered in the
last 30 days and compares them against the comets.yaml watchlist.

If new comets are found that are not on the watchlist (or cancelled list),
writes their details to _new_comets.json in the repo root so the
open_comet_issues.py script can create GitHub Issues for admin review.

Run manually (safe — read-only, no API keys needed):
    python scripts/check_new_comets.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import yaml

SBDB_URL = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"
COMETS_FILE = os.path.join(os.path.dirname(__file__), "..", "comets.yaml")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "_new_comets.json")
LOOKBACK_DAYS = 30


def load_watchlist():
    """Return a set of all designation strings from comets.yaml (comets + cancelled)."""
    if not os.path.exists(COMETS_FILE):
        print("WARNING: comets.yaml not found — treating watchlist as empty.", file=sys.stderr)
        return set()
    try:
        with open(COMETS_FILE, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        all_entries = cfg.get("comets", []) + cfg.get("cancelled", [])
        return set(str(c).strip() for c in all_entries)
    except Exception as e:
        print(f"WARNING: Could not parse comets.yaml: {e}", file=sys.stderr)
        return set()


def query_recent_comets(lookback_days=LOOKBACK_DAYS):
    """
    Query JPL SBDB for comets whose discovery date is within the last N days.
    Returns a list of dicts with: designation, pdes, disc, H, q, e
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=lookback_days)
    ).strftime("%Y-%m-%d")

    params = {
        "fields": "pdes,name,disc,H,q,e",
        "sb-kind": "c",
        "sb-cdata": json.dumps({"AND": [f"disc|d|>{cutoff}"]}),
        "full-prec": "false",
        "limit": "500",
    }

    print(f"Querying JPL SBDB for comets discovered after {cutoff} ...")
    try:
        resp = requests.get(SBDB_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ERROR: SBDB query failed: {e}", file=sys.stderr)
        sys.exit(1)

    fields = data.get("fields", [])
    rows = data.get("data", [])
    print(f"  {len(rows)} comet(s) returned from SBDB.")

    result = []
    for row in rows:
        entry = dict(zip(fields, row))
        pdes = (entry.get("pdes") or "").strip()
        name = (entry.get("name") or "").strip()
        disc = (entry.get("disc") or "").strip()
        H_raw = entry.get("H")
        q_raw = entry.get("q")
        e_raw = entry.get("e")

        full_desig = f"{pdes} ({name})" if name else pdes
        result.append({
            "designation": full_desig,
            "pdes": pdes,
            "disc": disc,
            "H": round(float(H_raw), 1) if H_raw is not None else None,
            "q": round(float(q_raw), 3) if q_raw is not None else None,
            "e": round(float(e_raw), 6) if e_raw is not None else None,
        })
    return result


def is_on_watchlist(comet, watchlist):
    """
    Check whether a comet's designation matches any entry in the watchlist.
    Uses case-insensitive substring matching to handle minor formatting differences
    (e.g. 'C/2025 N1' vs 'C/2025 N1 (ATLAS)').
    """
    pdes = comet["pdes"].lower().strip()
    for w in watchlist:
        w_lower = w.lower().strip()
        if pdes and (pdes in w_lower or w_lower.startswith(pdes)):
            return True
    return False


def main():
    watchlist = load_watchlist()
    print(f"  Watchlist has {len(watchlist)} entries.")

    recent = query_recent_comets()

    new_comets = [c for c in recent if not is_on_watchlist(c, watchlist)]

    # Clean up output file from any previous run
    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)

    if not new_comets:
        print("  No new comets to report — all recent discoveries are already on the watchlist.")
        return

    print(f"\n  {len(new_comets)} new comet(s) not on watchlist:")
    for c in new_comets:
        mag_str = f", H={c['H']}" if c["H"] is not None else ""
        q_str = f", q={c['q']} AU" if c["q"] is not None else ""
        print(f"    - {c['designation']} (disc={c['disc']}{mag_str}{q_str})")

    out_path = os.path.normpath(OUTPUT_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(new_comets, f, indent=2)
    print(f"\n  Written to {out_path}")
    print("  Run open_comet_issues.py to create GitHub Issues, or review manually.")


if __name__ == "__main__":
    main()
