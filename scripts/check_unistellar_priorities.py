"""
check_unistellar_priorities.py
==============================
Scrapes the Unistellar comet and asteroid priority mission pages and compares
them against the local YAML watchlists (comets.yaml / asteroids.yaml).

Detects two kinds of changes:
  - ADDED:   Objects on Unistellar's live page that are NOT in our unistellar_priority list
  - REMOVED: Objects in our unistellar_priority list that are NO LONGER on Unistellar's page

If any changes are found, writes _priority_changes.json for open_priority_issues.py.

Run manually (safe — read-only locally, scrapes are GET-only):
    pip install "scrapling[fetchers]"
    python scripts/check_unistellar_priorities.py
"""

import json
import os
import re
import sys

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
COMETS_FILE = os.path.join(os.path.dirname(__file__), "..", "comets.yaml")
ASTEROIDS_FILE = os.path.join(os.path.dirname(__file__), "..", "asteroids.yaml")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "..", "_priority_changes.json")

# ---------------------------------------------------------------------------
# Import scrapers (reuse production code from backend/)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.scrape import scrape_unistellar_priority_comets, scrape_unistellar_priority_asteroids


_AKA_RE = re.compile(r'#\s*aka\s+(.+)', re.IGNORECASE)


def load_yaml_priority(filepath, priority_key="unistellar_priority"):
    """Load the unistellar_priority list from a YAML file.

    Also parses '# aka ...' comments on each line to build an alias map.
    Example YAML line:
        - C/2025 N1 (ATLAS)  # aka 3I/ATLAS (redesignated interstellar)
    Returns (names, aliases) where aliases maps "C/2025 N1 (ATLAS)" → ["3I/ATLAS"].
    """
    if not os.path.exists(filepath):
        print(f"WARNING: {filepath} not found — treating as empty.", file=sys.stderr)
        return [], {}
    try:
        # Parse YAML values
        with open(filepath, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        names = [str(c).strip() for c in cfg.get(priority_key, [])]

        # Parse raw lines for '# aka ...' aliases
        aliases = {}
        in_section = False
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{priority_key}:"):
                    in_section = True
                    continue
                if in_section:
                    if stripped.startswith("- "):
                        m = _AKA_RE.search(line)
                        if m:
                            # Extract the YAML value (before the comment)
                            val = stripped.split("#")[0].lstrip("- ").strip()
                            # Extract alias — take just the designation, strip parenthetical notes
                            aka_raw = m.group(1).strip()
                            # Remove trailing parenthetical like "(redesignated interstellar)"
                            aka = re.sub(r'\s*\([^)]*\)\s*$', '', aka_raw).strip()
                            if val and aka:
                                aliases.setdefault(val, []).append(aka)
                    elif not stripped.startswith("#") and stripped and not stripped.startswith("-"):
                        in_section = False  # hit next YAML key

        return names, aliases
    except Exception as e:
        print(f"WARNING: Could not parse {filepath}: {e}", file=sys.stderr)
        return [], {}


def normalize_for_compare(name):
    """Lowercase and strip whitespace for fuzzy matching."""
    return name.lower().strip()


def find_match(needle, haystack, aliases=None):
    """Check if needle matches any entry in haystack (case-insensitive, substring).

    Also checks aliases: if a haystack entry has '# aka X' and needle matches X,
    that counts as a match.
    """
    aliases = aliases or {}
    n = normalize_for_compare(needle)
    for h in haystack:
        hl = normalize_for_compare(h)
        if n == hl or n in hl or hl in n:
            return h
        # Check aliases for this haystack entry
        for aka in aliases.get(h, []):
            al = normalize_for_compare(aka)
            if n == al or n in al or al in n:
                return h
    return None


def diff_priorities(scraped, yaml_list, category, aliases=None):
    """Compare scraped list vs YAML priority list. Returns (added, removed) lists."""
    aliases = aliases or {}
    added = []
    for s in scraped:
        if not find_match(s, yaml_list, aliases):
            added.append({"designation": s, "category": category, "change": "ADDED"})

    removed = []
    for y in yaml_list:
        # Build reverse aliases: scraped names should also match via aliases
        if not find_match(y, scraped) and not any(
            find_match(aka, scraped) for aka in aliases.get(y, [])
        ):
            removed.append({"designation": y, "category": category, "change": "REMOVED"})

    return added, removed


def main():
    all_changes = []

    # --- Comets ---
    print("Scraping Unistellar comet missions page...")
    scraped_comets = scrape_unistellar_priority_comets()
    yaml_comets, comet_aliases = load_yaml_priority(COMETS_FILE)
    print(f"  Scraped: {len(scraped_comets)} comets  |  YAML priority: {len(yaml_comets)} comets")
    if comet_aliases:
        print(f"  Aliases: {comet_aliases}")

    if scraped_comets:
        added, removed = diff_priorities(scraped_comets, yaml_comets, "comet", comet_aliases)
        all_changes.extend(added)
        all_changes.extend(removed)

        if added:
            print(f"  NEW on Unistellar (not in our priority list):")
            for a in added:
                print(f"    + {a['designation']}")
        if removed:
            print(f"  REMOVED from Unistellar (still in our priority list):")
            for r in removed:
                print(f"    - {r['designation']}")
        if not added and not removed:
            print(f"  No changes — comet priorities are in sync.")
    else:
        print("  WARNING: Scrape returned 0 comets — skipping comparison (page may be down).")

    # --- Asteroids ---
    print("\nScraping Unistellar asteroid missions page...")
    scraped_asteroids = scrape_unistellar_priority_asteroids()
    yaml_asteroids, asteroid_aliases = load_yaml_priority(ASTEROIDS_FILE)
    print(f"  Scraped: {len(scraped_asteroids)} asteroids  |  YAML priority: {len(yaml_asteroids)} asteroids")
    if asteroid_aliases:
        print(f"  Aliases: {asteroid_aliases}")

    if scraped_asteroids:
        added, removed = diff_priorities(scraped_asteroids, yaml_asteroids, "asteroid", asteroid_aliases)
        all_changes.extend(added)
        all_changes.extend(removed)

        if added:
            print(f"  NEW on Unistellar (not in our priority list):")
            for a in added:
                print(f"    + {a['designation']}")
        if removed:
            print(f"  REMOVED from Unistellar (still in our priority list):")
            for r in removed:
                print(f"    - {r['designation']}")
        if not added and not removed:
            print(f"  No changes — asteroid priorities are in sync.")
    else:
        print("  WARNING: Scrape returned 0 asteroids — skipping comparison (page may be down).")

    # --- Output ---
    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)

    if not all_changes:
        print("\nAll Unistellar priorities are in sync with local watchlists.")
        return

    print(f"\n{len(all_changes)} change(s) detected:")
    for c in all_changes:
        symbol = "+" if c["change"] == "ADDED" else "-"
        print(f"  {symbol} [{c['category']}] {c['designation']}")

    out_path = os.path.normpath(OUTPUT_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_changes, f, indent=2)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
