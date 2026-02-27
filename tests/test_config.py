import os
import yaml
import json
import tempfile
import pytest
from backend.config import read_comets_config, read_comet_catalog, read_asteroids_config, read_dso_config


def test_read_comets_config_missing_file():
    result = read_comets_config("/nonexistent/path.yaml")
    assert result["comets"] == []
    assert result["unistellar_priority"] == []
    assert result["priorities"] == {}
    assert result["cancelled"] == []


def test_read_comets_config_existing_file():
    data = {"comets": ["C/2023 A3"], "unistellar_priority": ["C/2023 A3"]}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(data, f)
        path = f.name
    try:
        result = read_comets_config(path)
        assert result["comets"] == ["C/2023 A3"]
        assert "priorities" in result   # default key added
    finally:
        os.unlink(path)


def test_read_comets_config_all_defaults_added():
    """Empty YAML file still gets all four default keys."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({}, f)
        path = f.name
    try:
        result = read_comets_config(path)
        assert result["comets"] == []
        assert result["unistellar_priority"] == []
        assert result["priorities"] == {}
        assert result["cancelled"] == []
    finally:
        os.unlink(path)


def test_read_comet_catalog_missing_file():
    updated, comets = read_comet_catalog("/nonexistent.json")
    assert updated is None
    assert comets == []


def test_read_comet_catalog_valid_file():
    data = {"updated": "2025-01-01", "comets": [{"name": "1P/Halley"}]}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        updated, comets = read_comet_catalog(path)
        assert updated == "2025-01-01"
        assert len(comets) == 1
    finally:
        os.unlink(path)


def test_read_comet_catalog_corrupted_file():
    """Malformed JSON returns (None, []) instead of raising."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("not valid json {{{")
        path = f.name
    try:
        updated, comets = read_comet_catalog(path)
        assert updated is None
        assert comets == []
    finally:
        os.unlink(path)


def test_read_asteroids_config_missing_file():
    result = read_asteroids_config("/nonexistent/path.yaml")
    assert result["asteroids"] == []
    assert "priorities" in result


def test_read_asteroids_config_existing_file():
    data = {"asteroids": ["433 Eros"], "unistellar_priority": ["433 Eros"]}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(data, f)
        path = f.name
    try:
        result = read_asteroids_config(path)
        assert result["asteroids"] == ["433 Eros"]
        assert result["unistellar_priority"] == ["433 Eros"]
        assert result["priorities"] == {}
        assert result["cancelled"] == []
    finally:
        os.unlink(path)


def test_read_dso_config_missing_file():
    result = read_dso_config("/nonexistent/path.yaml")
    assert result["messier"] == []
    assert result["bright_stars"] == []
    assert result["astrophotography_favorites"] == []


def test_read_dso_config_existing_file():
    data = {"messier": [{"name": "M31"}], "bright_stars": []}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(data, f)
        path = f.name
    try:
        result = read_dso_config(path)
        assert result["messier"] == [{"name": "M31"}]
        assert result["bright_stars"] == []
        assert result["astrophotography_favorites"] == []  # default added
    finally:
        os.unlink(path)


def test_read_dso_config_empty_file():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({}, f)
        path = f.name
    try:
        result = read_dso_config(path)
        assert result["messier"] == []
        assert result["bright_stars"] == []
        assert result["astrophotography_favorites"] == []
    finally:
        os.unlink(path)


# --- jpl_id_overrides and jpl_id_cache tests ---
from backend.config import read_jpl_overrides, read_jpl_cache, write_jpl_cache, write_jpl_overrides


def test_read_jpl_overrides_missing_file():
    result = read_jpl_overrides("/nonexistent/path.yaml")
    assert result["comets"] == {}
    assert result["asteroids"] == {}


def test_read_jpl_overrides_existing_file():
    data = {"comets": {"C/2025 N1 (ATLAS)": "3I"}, "asteroids": {}}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(data, f)
        path = f.name
    try:
        result = read_jpl_overrides(path)
        assert result["comets"]["C/2025 N1 (ATLAS)"] == "3I"
        assert result["asteroids"] == {}
    finally:
        os.unlink(path)


def test_read_jpl_cache_missing_file():
    result = read_jpl_cache("/nonexistent.json")
    assert result["comets"] == {}
    assert result["asteroids"] == {}
    assert result["notified"] == []


def test_read_jpl_cache_corrupted_file():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("not valid json {{{")
        path = f.name
    try:
        result = read_jpl_cache(path)
        assert result["comets"] == {}
        assert result["asteroids"] == {}
        assert result["notified"] == []
    finally:
        os.unlink(path)


def test_write_and_read_jpl_cache_roundtrip():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        path = f.name
    try:
        data = {"comets": {"C/2025 Q3 (ATLAS)": "90004812"}, "asteroids": {}, "notified": []}
        write_jpl_cache(path, data)
        result = read_jpl_cache(path)
        assert result["comets"]["C/2025 Q3 (ATLAS)"] == "90004812"
        assert result["notified"] == []
    finally:
        os.unlink(path)


def test_write_and_read_jpl_overrides_roundtrip():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        path = f.name
    try:
        data = {"comets": {"C/2025 N1 (ATLAS)": "3I"}, "asteroids": {"433 Eros": "433"}}
        write_jpl_overrides(path, data)
        result = read_jpl_overrides(path)
        assert result["comets"]["C/2025 N1 (ATLAS)"] == "3I"
        assert result["asteroids"]["433 Eros"] == "433"
    finally:
        os.unlink(path)
