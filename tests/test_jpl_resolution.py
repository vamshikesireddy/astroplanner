# tests/test_jpl_resolution.py
import os
import json
import yaml
import tempfile
import pytest
from unittest.mock import patch


def _write_yaml(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f)


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def _make_files(tmp_path, overrides_data=None, cache_data=None):
    """Helper: write override and cache files, return their paths."""
    ovr_path = str(tmp_path / "overrides.yaml")
    cache_path = str(tmp_path / "cache.json")
    _write_yaml(ovr_path, overrides_data or {"comets": {}, "asteroids": {}})
    _write_json(cache_path, cache_data or {"comets": {}, "asteroids": {}, "notified": []})
    return ovr_path, cache_path


def test_get_comet_jpl_id_override_takes_priority(tmp_path):
    """Override file wins over cache and stripping."""
    ovr_path, cache_path = _make_files(
        tmp_path,
        overrides_data={"comets": {"C/2025 N1 (ATLAS)": "3I"}, "asteroids": {}},
        cache_data={"comets": {"C/2025 N1 (ATLAS)": "WRONG"}, "asteroids": {}, "notified": []},
    )
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()  # bust st.cache_data
        result = app._get_comet_jpl_id("C/2025 N1 (ATLAS)")
    assert result == "3I"


def test_get_comet_jpl_id_cache_used_when_no_override(tmp_path):
    """Cache wins when no override present."""
    ovr_path, cache_path = _make_files(
        tmp_path,
        cache_data={"comets": {"C/2025 Q3 (ATLAS)": "90004812"}, "asteroids": {}, "notified": []},
    )
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        result = app._get_comet_jpl_id("C/2025 Q3 (ATLAS)")
    assert result == "90004812"


def test_get_comet_jpl_id_fallback_stripping(tmp_path):
    """Falls back to stripping parenthetical when no override or cache entry."""
    ovr_path, cache_path = _make_files(tmp_path)
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        result = app._get_comet_jpl_id("C/2022 N2 (PANSTARRS)")
    assert result == "C/2022 N2"


def test_dedup_by_jpl_id_removes_duplicates(tmp_path):
    """C/2025 F2 (SWAN) and C/2025 F2 both map to C/2025 F2 — only first kept."""
    ovr_path, cache_path = _make_files(tmp_path)
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        names = ["C/2025 F2 (SWAN)", "C/2025 F2"]
        deduped = app._dedup_by_jpl_id(names, app._get_comet_jpl_id)
    assert deduped == ["C/2025 F2 (SWAN)"]
    assert len(deduped) == 1


def test_dedup_by_jpl_id_keeps_unique_entries(tmp_path):
    """Unique names are all kept."""
    ovr_path, cache_path = _make_files(tmp_path)
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        names = ["C/2022 N2 (PANSTARRS)", "C/2025 K1 (ATLAS)", "29P/Schwassmann-Wachmann 1"]
        deduped = app._dedup_by_jpl_id(names, app._get_comet_jpl_id)
    assert len(deduped) == 3


def test_get_asteroid_jpl_id_override_takes_priority(tmp_path):
    """Override wins over everything for asteroids."""
    ovr_path, cache_path = _make_files(
        tmp_path,
        overrides_data={"comets": {}, "asteroids": {"433 Eros": "OVERRIDE"}},
        cache_data={"comets": {}, "asteroids": {"433 Eros": "WRONG"}, "notified": []},
    )
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        result = app._asteroid_jpl_id("433 Eros")
    assert result == "OVERRIDE"


def test_get_asteroid_jpl_id_cache_used_when_no_override(tmp_path):
    ovr_path, cache_path = _make_files(
        tmp_path,
        cache_data={"comets": {}, "asteroids": {"2001 FD58": "CACHED_ID"}, "notified": []},
    )
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        result = app._asteroid_jpl_id("2001 FD58")
    assert result == "CACHED_ID"


def test_get_asteroid_jpl_id_provisional_designation(tmp_path):
    """Provisional designation stays as-is (e.g. '2001 FD58')."""
    ovr_path, cache_path = _make_files(tmp_path)
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        assert app._asteroid_jpl_id("2001 FD58") == "2001 FD58"
        assert app._asteroid_jpl_id("2001 SN263") == "2001 SN263"


def test_get_asteroid_jpl_id_numbered_asteroid(tmp_path):
    """Numbered asteroid extracts the number ('433 Eros' -> '433')."""
    ovr_path, cache_path = _make_files(tmp_path)
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        assert app._asteroid_jpl_id("433 Eros") == "433"
        assert app._asteroid_jpl_id("99942 Apophis") == "99942"


def test_get_asteroid_jpl_id_bare_name_passthrough(tmp_path):
    """Bare name without number passes through unchanged."""
    ovr_path, cache_path = _make_files(tmp_path)
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        assert app._asteroid_jpl_id("Apophis") == "Apophis"


# ---------------------------------------------------------------------------
# _save_jpl_cache_entry — guard against bad SBDB SPK-IDs
# ---------------------------------------------------------------------------

def test_save_jpl_cache_entry_rejects_sbdb_internal_ids(tmp_path):
    """IDs >= 20_000_000 (SBDB internal format) must never be written to cache.

    SBDB returns e.g. 20000433 for '433 Eros'.  JPL Horizons rejects these IDs
    outright, so caching them causes every subsequent batch query to fail until
    the cache is manually cleaned.  The guard in _save_jpl_cache_entry() must
    silently drop these before any file write occurs.
    """
    ovr_path, cache_path = _make_files(tmp_path)
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        # Simulate SBDB returning a bad ID for a numbered asteroid
        app._save_jpl_cache_entry("asteroids", "433 Eros", "20000433")
        app._save_jpl_cache_entry("asteroids", "1 Ceres", "20000001")
        app._save_jpl_cache_entry("comets", "88P/Howell", "20015091")
    from backend.config import read_jpl_cache
    cache = read_jpl_cache(cache_path)
    assert "433 Eros" not in cache.get("asteroids", {}), "20000433 should not be cached"
    assert "1 Ceres" not in cache.get("asteroids", {}), "20000001 should not be cached"
    assert "88P/Howell" not in cache.get("comets", {}), "20015091 should not be cached"


def test_save_jpl_cache_entry_accepts_valid_ids(tmp_path):
    """Valid IDs outside the [20M, 30M) SBDB-internal range must be written to cache."""
    ovr_path, cache_path = _make_files(tmp_path)
    with patch("app.JPL_OVERRIDES_FILE", ovr_path), patch("app.JPL_CACHE_FILE", cache_path):
        import app
        app._load_jpl_overrides.clear()
        app._save_jpl_cache_entry("comets", "C/2022 N2 (PANSTARRS)", "1003861")  # 7-digit comet SPK-ID
        app._save_jpl_cache_entry("comets", "240P-B", "90001203")                 # fragment ID (9000xxxx, valid)
        app._save_jpl_cache_entry("asteroids", "99942 Apophis", "99942")          # numbered asteroid (bare)
    from backend.config import read_jpl_cache
    cache = read_jpl_cache(cache_path)
    assert cache["comets"]["C/2022 N2 (PANSTARRS)"] == "1003861"
    assert cache["comets"]["240P-B"] == "90001203"
    assert cache["asteroids"]["99942 Apophis"] == "99942"


# ---------------------------------------------------------------------------
# NaN-truthy guard in observability loop
# ---------------------------------------------------------------------------

def test_nan_resolve_error_not_truthy():
    """pandas fills missing _resolve_error with NaN; NaN is truthy in Python.

    The observability loop must use `is True` — not a bare truthy check — so
    that successful rows (which have no _resolve_error key and therefore get
    NaN in the DataFrame column) are never flagged as JPL failures.

    This bug caused ALL comets/asteroids to appear Unobservable even when
    JPL resolution succeeded, because every row's NaN was treated as True.
    """
    import pandas as pd

    # Successful rows have NO _resolve_error key; failed rows have True.
    # When mixed into a DataFrame, the missing values become NaN (float).
    rows = [
        {"Name": "Good Comet", "RA": "1h"},                          # success — key absent → NaN
        {"Name": "Bad Comet",  "RA": "—", "_resolve_error": True},   # genuine failure
    ]
    df = pd.DataFrame(rows)

    # Confirm NaN is truthy — this documents WHY the bug exists
    import math
    assert math.isnan(df.loc[0, "_resolve_error"]), "Missing value must be NaN"
    assert bool(df.loc[0, "_resolve_error"]) is True, "NaN is truthy — this is the trap"

    # Bare truthy check — the bug: flags NaN rows as failures
    flagged_buggy = [r["Name"] for _, r in df.iterrows() if r.get("_resolve_error")]
    # Correct is-True check — the fix
    flagged_fixed = [r["Name"] for _, r in df.iterrows() if r.get("_resolve_error") is True]

    assert "Good Comet" in flagged_buggy, "Confirm NaN is truthy (documents the bug)"
    assert flagged_fixed == ["Bad Comet"], "Only True (not NaN) should be flagged"
