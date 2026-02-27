# backend/config.py
"""Pure file I/O for YAML/JSON config files — no Streamlit dependency."""

import os
import yaml
import json


def read_comets_config(path):
    """Load comets YAML → dict with default keys."""
    if os.path.exists(path):
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    data.setdefault("comets", [])
    data.setdefault("unistellar_priority", [])
    data.setdefault("priorities", {})
    data.setdefault("cancelled", [])
    return data


def read_comet_catalog(path):
    """Load comets_catalog.json → (updated_str, entries_list)."""
    if not os.path.exists(path):
        return None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("updated"), data.get("comets", [])
    except Exception:
        return None, []


def read_asteroids_config(path):
    """Load asteroids YAML → dict with default keys."""
    if os.path.exists(path):
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    data.setdefault("asteroids", [])
    data.setdefault("unistellar_priority", [])
    data.setdefault("priorities", {})
    data.setdefault("cancelled", [])
    return data


def read_dso_config(path):
    """Load dso_targets YAML → dict with default keys."""
    if os.path.exists(path):
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    data.setdefault("messier", [])
    data.setdefault("bright_stars", [])
    data.setdefault("astrophotography_favorites", [])
    return data


def read_jpl_overrides(path):
    """Load jpl_id_overrides.yaml → dict with 'comets' and 'asteroids' keys."""
    if os.path.exists(path):
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    data.setdefault("comets", {})
    data.setdefault("asteroids", {})
    return data


def write_jpl_overrides(path, data):
    """Write jpl_id_overrides.yaml."""
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def read_jpl_cache(path):
    """Load jpl_id_cache.json → dict with 'comets', 'asteroids', 'notified' keys."""
    if not os.path.exists(path):
        return {"comets": {}, "asteroids": {}, "notified": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("comets", {})
        data.setdefault("asteroids", {})
        data.setdefault("notified", [])
        return data
    except Exception:
        return {"comets": {}, "asteroids": {}, "notified": []}


def write_jpl_cache(path, data):
    """Write jpl_id_cache.json. Silently ignores write errors (non-fatal)."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def read_ephemeris_cache(path):
    """Load ephemeris_cache.json → dict, or {} if missing/corrupt."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def lookup_cached_position(cache, section, name, target_date_str):
    """Return (ra, dec) from pre-computed ephemeris cache for a given object+date, or None."""
    obj = cache.get(section, {}).get(name)
    if not obj:
        return None
    for pos in obj.get('positions', []):
        if pos['date'] == target_date_str:
            return pos['ra'], pos['dec']
    return None
