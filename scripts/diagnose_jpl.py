"""
scripts/diagnose_jpl.py
-----------------------
Diagnostic script — tests JPL resolution for every comet and asteroid in the watchlists.

For each object reports:
  - Which ID was resolved (override / cache / stripped)
  - Whether JPL Horizons accepts that ID
  - Final status: OK / FAILED

Run:  python scripts/diagnose_jpl.py
"""

import json
import sys
import os
import yaml
from datetime import datetime, timezone

# ── paths ────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMETS_YAML      = os.path.join(ROOT, "comets.yaml")
ASTEROIDS_YAML   = os.path.join(ROOT, "asteroids.yaml")
OVERRIDES_YAML   = os.path.join(ROOT, "jpl_id_overrides.yaml")
CACHE_JSON       = os.path.join(ROOT, "jpl_id_cache.json")

sys.path.insert(0, ROOT)
from backend.resolvers import resolve_horizons  # noqa: E402


# ── loaders ──────────────────────────────────────────────────────────────────
def _load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── ID resolution (mirrors app.py logic, no Streamlit) ───────────────────────
def _comet_id(name, overrides, cache):
    if name in overrides.get("comets", {}):
        return overrides["comets"][name], "override"
    if name in cache.get("comets", {}):
        return cache["comets"][name], "cache"
    stripped = name.split("(")[0].strip()
    return stripped, "stripped"


def _asteroid_id(name, overrides, cache):
    if name in overrides.get("asteroids", {}):
        return overrides["asteroids"][name], "override"
    if name in cache.get("asteroids", {}):
        return cache["asteroids"][name], "cache"
    import re
    if name and re.match(r'^\d{4}\s+[A-Z]{1,2}\d', name):
        return name, "provisional"        # e.g. '2001 FD58', '2001 SN263'
    if name and name[0].isdigit():
        return name.split(' ')[0], "number-extracted"   # e.g. '433 Eros' → '433'
    return name, "passthrough"


# ── Horizons test ─────────────────────────────────────────────────────────────
OBS_TIME = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

def _test_horizons(jpl_id):
    try:
        _, sc = resolve_horizons(jpl_id, obs_time_str=OBS_TIME)
        return True, f"RA={sc.ra.deg:.4f}  Dec={sc.dec.deg:.4f}"
    except Exception as e:
        return False, str(e)[:80]


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    overrides = _load_yaml(OVERRIDES_YAML)
    cache     = _load_json(CACHE_JSON)
    comets_cfg    = _load_yaml(COMETS_YAML)
    asteroids_cfg = _load_yaml(ASTEROIDS_YAML)

    comet_names    = comets_cfg.get("comets", [])
    asteroid_names = asteroids_cfg.get("asteroids", [])

    ok_count = fail_count = 0

    # ── comets ────────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  COMETS  ({len(comet_names)} total)   obs_time={OBS_TIME}")
    print(f"{'='*70}")
    print(f"{'Name':<35} {'ID source':<12} {'JPL ID used':<18} {'Result'}")
    print(f"{'-'*35} {'-'*12} {'-'*18} {'-'*30}")

    for name in comet_names:
        jpl_id, source = _comet_id(name, overrides, cache)
        ok, detail = _test_horizons(jpl_id)
        status = "[OK]  " if ok else "[FAIL]"
        if ok:
            ok_count += 1
        else:
            fail_count += 1
        print(f"{name:<35} {source:<12} {str(jpl_id):<18} {status}  {detail}")

    # ── asteroids ─────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  ASTEROIDS  ({len(asteroid_names)} total)")
    print(f"{'='*70}")
    print(f"{'Name':<35} {'ID source':<12} {'JPL ID used':<18} {'Result'}")
    print(f"{'-'*35} {'-'*12} {'-'*18} {'-'*30}")

    for name in asteroid_names:
        jpl_id, source = _asteroid_id(name, overrides, cache)
        ok, detail = _test_horizons(jpl_id)
        status = "[OK]  " if ok else "[FAIL]"
        if ok:
            ok_count += 1
        else:
            fail_count += 1
        print(f"{name:<35} {source:<12} {str(jpl_id):<18} {status}  {detail}")

    # ── summary ───────────────────────────────────────────────────────────────
    total = ok_count + fail_count
    print(f"\n{'='*70}")
    print(f"  SUMMARY:  {ok_count}/{total} resolved [OK]   {fail_count}/{total} failed [FAIL]")
    print(f"{'='*70}\n")

    return fail_count


if __name__ == "__main__":
    sys.exit(main())
