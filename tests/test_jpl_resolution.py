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
