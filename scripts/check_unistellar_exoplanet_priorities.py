"""
check_unistellar_exoplanet_priorities.py
=========================================
Scrapes the Unistellar exoplanet missions page and compares against exoplanets.yaml.

Detects four kinds of changes:
  ADDED:            New planet on Unistellar not in our YAML active list
  REMOVED:          Planet in YAML active list no longer on Unistellar page
  PRIORITY_ADDED:   Planet now featured as heading but not in YAML priorities
  PRIORITY_REMOVED: Planet in YAML priorities no longer featured as heading

Writes _exoplanet_priority_changes.json for open_exoplanet_priority_issues.py.

Run manually (safe — read-only locally):
    pip install "scrapling[fetchers]>=0.4.1"
    python scripts/check_unistellar_exoplanet_priorities.py
"""

import json
import os
import sys

import yaml

EXOPLANETS_FILE = os.path.join(os.path.dirname(__file__), "..", "exoplanets.yaml")
OUTPUT_FILE     = os.path.join(os.path.dirname(__file__), "..", "_exoplanet_priority_changes.json")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.scrape import scrape_unistellar_exoplanets


def load_exoplanets_yaml(filepath):
    """Load exoplanets.yaml → (active_list, priorities_dict)."""
    if not os.path.exists(filepath):
        print(f"WARNING: {filepath} not found — treating as empty.", file=sys.stderr)
        return [], {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        active     = [str(p).strip() for p in cfg.get("active", [])]
        priorities = cfg.get("priorities", {}) or {}
        return active, priorities
    except Exception as e:
        print(f"WARNING: Could not parse {filepath}: {e}", file=sys.stderr)
        return [], {}


def _normalize(name):
    return name.lower().strip()


def _in_list(name, lst):
    n = _normalize(name)
    return any(
        n == _normalize(x) or n in _normalize(x) or _normalize(x) in n
        for x in lst
    )


def diff_exoplanet_priorities(scraped_active, scraped_priority, yaml_active, yaml_priorities):
    """Compare scraped lists vs YAML. Returns list of change dicts."""
    changes = []

    for planet in scraped_active:
        if not _in_list(planet, yaml_active):
            changes.append({"designation": planet, "change": "ADDED"})

    for planet in yaml_active:
        if not _in_list(planet, scraped_active):
            changes.append({"designation": planet, "change": "REMOVED"})

    for planet in scraped_priority:
        if not any(
            _normalize(planet) in _normalize(k) or _normalize(k) in _normalize(planet)
            for k in yaml_priorities
        ):
            changes.append({"designation": planet, "change": "PRIORITY_ADDED"})

    for planet in yaml_priorities:
        if not _in_list(planet, scraped_priority):
            changes.append({"designation": planet, "change": "PRIORITY_REMOVED"})

    return changes


def main():
    print("Scraping Unistellar exoplanet missions page...")
    scraped = scrape_unistellar_exoplanets()
    scraped_active   = scraped.get("active", [])
    scraped_priority = scraped.get("priority", [])
    print(f"  Scraped: {len(scraped_active)} active, {len(scraped_priority)} priority")

    if not scraped_active:
        print("  WARNING: Scrape returned 0 planets — skipping (page may be down).")
        return

    yaml_active, yaml_priorities = load_exoplanets_yaml(EXOPLANETS_FILE)
    print(f"  YAML:    {len(yaml_active)} active, {len(yaml_priorities)} priority")

    changes = diff_exoplanet_priorities(
        scraped_active, scraped_priority, yaml_active, yaml_priorities
    )

    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)

    if not changes:
        print("No changes — exoplanet priorities are in sync.")
        return

    print(f"\n{len(changes)} change(s) detected:")
    for c in changes:
        symbol = "+" if c["change"] in ("ADDED", "PRIORITY_ADDED") else "-"
        print(f"  {symbol} [{c['change']}] {c['designation']}")

    out_path = os.path.normpath(OUTPUT_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(changes, f, indent=2)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
