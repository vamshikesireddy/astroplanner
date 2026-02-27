#!/usr/bin/env python3
"""
populate_jpl_cache.py — Pre-populate jpl_id_cache.json with SBDB SPK-IDs.

Run locally before deploy:
    python scripts/populate_jpl_cache.py

Run in CI (GitHub Actions) with notification support:
    GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo python scripts/populate_jpl_cache.py

Skips entries already in jpl_id_overrides.yaml (admin overrides take priority).
Compares against existing jpl_id_cache.json and reports changes.
"""

import json
import os
import sys
import re

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.sbdb import sbdb_lookup
from backend.config import (
    read_jpl_overrides, read_jpl_cache, write_jpl_cache,
    read_comets_config, read_asteroids_config,
)
from backend.github import create_issue

OVERRIDES_FILE = "jpl_id_overrides.yaml"
CACHE_FILE = "jpl_id_cache.json"
COMETS_FILE = "comets.yaml"
ASTEROIDS_FILE = "asteroids.yaml"


def _comet_stripped(name):
    """Strip parenthetical from comet display name: 'C/2024 G3 (ATLAS)' → 'C/2024 G3'."""
    return name.split('(')[0].strip()


def _asteroid_stripped(name):
    """Extract JPL query ID from asteroid display name."""
    if name and re.match(r'^\d{4}\s+[A-Z]{1,2}\d', name):
        return name  # Provisional designation: '2001 FD58'
    if name and name[0].isdigit():
        return name.split(' ')[0]  # Numbered: '433 Eros' → '433'
    return name


def resolve_all(names, section, strip_fn, overrides):
    """
    Attempt to resolve every name via SBDB. Skip names that have admin overrides.

    Returns:
        resolved: dict[name → spk_id]  (successfully resolved)
        failed:   list[name]           (could not resolve)
    """
    resolved = {}
    failed = []
    override_section = overrides.get(section, {})

    for name in names:
        if name in override_section:
            print(f"  SKIP (override): {name!r} -> {override_section[name]!r}")
            continue
        query = strip_fn(name)
        spk_id = sbdb_lookup(query)
        if spk_id is None and query != name:
            spk_id = sbdb_lookup(name)   # also try full display name
        if spk_id:
            print(f"  OK:   {name!r} -> SPK-ID {spk_id!r} (queried: {query!r})")
            resolved[name] = spk_id
        else:
            print(f"  FAIL: {name!r} (queried: {query!r})")
            failed.append(name)

    return resolved, failed


def main():
    print("=== populate_jpl_cache.py ===")

    overrides = read_jpl_overrides(OVERRIDES_FILE)
    old_cache = read_jpl_cache(CACHE_FILE)

    comet_config = read_comets_config(COMETS_FILE)
    asteroid_config = read_asteroids_config(ASTEROIDS_FILE)

    comet_names = comet_config.get("comets", [])
    asteroid_names = [
        (e["name"] if isinstance(e, dict) else e)
        for e in asteroid_config.get("asteroids", [])
    ]

    print(f"\n--- Comets ({len(comet_names)} total) ---")
    comet_resolved, comet_failed = resolve_all(comet_names, "comets", _comet_stripped, overrides)

    print(f"\n--- Asteroids ({len(asteroid_names)} total) ---")
    asteroid_resolved, asteroid_failed = resolve_all(asteroid_names, "asteroids", _asteroid_stripped, overrides)

    # Build new cache by merging into old cache
    new_cache = {
        "comets": {**old_cache.get("comets", {}), **comet_resolved},
        "asteroids": {**old_cache.get("asteroids", {}), **asteroid_resolved},
    }
    # Preserve the notified list from old cache
    new_cache["notified"] = old_cache.get("notified", [])

    # Compute diff
    all_failed = comet_failed + asteroid_failed
    new_entries = []
    changed_entries = []
    for section in ("comets", "asteroids"):
        for name, spk_id in new_cache[section].items():
            old_spk = old_cache.get(section, {}).get(name)
            if old_spk is None:
                new_entries.append((section, name, spk_id))
            elif old_spk != spk_id:
                changed_entries.append((section, name, old_spk, spk_id))

    # Write updated cache
    write_jpl_cache(CACHE_FILE, new_cache)
    print(f"\n=== Cache written to {CACHE_FILE} ===")
    print(f"  New entries:     {len(new_entries)}")
    print(f"  Changed entries: {len(changed_entries)}")
    print(f"  Failures:        {len(all_failed)}")

    # GitHub notifications (CI only — requires env vars)
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if token and repo and (all_failed or changed_entries):
        lines = []
        if all_failed:
            lines.append("## ⚠️ Unresolved objects (add to jpl_id_overrides.yaml)\n")
            for name in all_failed:
                lines.append(f"- `{name}`")
        if changed_entries:
            lines.append("\n## ⚠️ SPK-ID changes detected\n")
            for section, name, old_id, new_id in changed_entries:
                lines.append(f"- `{name}` ({section}): `{old_id}` → `{new_id}`")
        body = "\n".join(lines) + "\n\n_Generated by `populate_jpl_cache.py`_"
        create_issue(token, repo, "⚠️ JPL cache: resolution failures or SPK-ID changes", body,
                     labels=["jpl-resolution-failure"])
        print(f"\nGitHub Issue created for {len(all_failed)} failures + {len(changed_entries)} changes.")

    if new_entries and token and repo:
        lines = ["## ✅ New SPK-IDs resolved\n"]
        for section, name, spk_id in new_entries:
            lines.append(f"- `{name}` ({section}): `{spk_id}`")
        body = "\n".join(lines) + "\n\n_Generated by `populate_jpl_cache.py`_"
        create_issue(token, repo, "✅ JPL cache: new SPK-IDs resolved", body,
                     labels=["jpl-cache-update"])

    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
