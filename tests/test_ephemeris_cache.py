import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- _strip_comet_id ---

def test_strip_comet_id_with_parens():
    from scripts.update_ephemeris_cache import _strip_comet_id
    assert _strip_comet_id("C/2024 G3 (ATLAS)") == "C/2024 G3"

def test_strip_comet_id_no_parens():
    from scripts.update_ephemeris_cache import _strip_comet_id
    assert _strip_comet_id("24P/Schaumasse") == "24P/Schaumasse"

def test_strip_comet_id_short():
    from scripts.update_ephemeris_cache import _strip_comet_id
    assert _strip_comet_id("88P") == "88P"


# --- _strip_asteroid_id ---

def test_strip_asteroid_id_numbered():
    from scripts.update_ephemeris_cache import _strip_asteroid_id
    assert _strip_asteroid_id("433 Eros") == "433"

def test_strip_asteroid_id_provisional():
    from scripts.update_ephemeris_cache import _strip_asteroid_id
    assert _strip_asteroid_id("2001 FD58") == "2001 FD58"

def test_strip_asteroid_id_bare_name():
    from scripts.update_ephemeris_cache import _strip_asteroid_id
    assert _strip_asteroid_id("Ceres") == "Ceres"


# --- _extract_positions ---

def test_extract_positions_returns_list():
    """_extract_positions pulls RA/Dec and date from each row."""
    from scripts.update_ephemeris_cache import _extract_positions

    class MockRow:
        def __init__(self, jd, ra, dec):
            self._data = {"datetime_jd": jd, "RA": ra, "DEC": dec}
        def __getitem__(self, key):
            return self._data[key]

    rows = [MockRow(2461481.5, 123.456, -12.345),
            MockRow(2461482.5, 124.100, -13.010)]

    positions = _extract_positions(rows)
    assert len(positions) == 2
    assert positions[0]["ra"] == 123.456
    assert positions[0]["dec"] == -12.345
    assert "date" in positions[0]
    assert len(positions[0]["date"]) == 10   # YYYY-MM-DD
    assert positions[0]["date"][4] == "-"


# --- _lookup_cached_position ---

def test_lookup_cached_position_hit():
    from backend.config import lookup_cached_position as _lookup_cached_position
    cache = {
        "comets": {
            "C/2024 G3 (ATLAS)": {
                "positions": [{"date": "2026-03-05", "ra": 99.9, "dec": 10.1}]
            }
        }
    }
    result = _lookup_cached_position(cache, "comets", "C/2024 G3 (ATLAS)", "2026-03-05")
    # Old entry has no vmag field → returns (ra, dec, None)
    assert result == (99.9, 10.1, None)

def test_lookup_cached_position_miss():
    from backend.config import lookup_cached_position as _lookup_cached_position
    assert _lookup_cached_position({}, "comets", "X", "2026-03-05") is None

def test_lookup_cached_position_wrong_date():
    from backend.config import lookup_cached_position as _lookup_cached_position
    cache = {"comets": {"X": {"positions": [{"date": "2026-03-01", "ra": 1.0, "dec": 2.0}]}}}
    assert _lookup_cached_position(cache, "comets", "X", "2026-04-15") is None


# --- _build_epochs ---

def test_build_epochs_date_format():
    from scripts.update_ephemeris_cache import _build_epochs
    from datetime import datetime, timezone
    start, epochs = _build_epochs(days=30)
    today = datetime.now(timezone.utc).date().isoformat()
    assert start == today
    assert epochs['start'] == today
    assert len(epochs['stop']) == 10   # YYYY-MM-DD
    assert epochs['stop'] > today
    assert epochs['step'] == '1d'


def test_extract_positions_includes_vmag_comet():
    """Comet rows: vmag comes from Tmag column (Horizons uses no hyphen)."""
    from scripts.update_ephemeris_cache import _extract_positions

    class MockRow:
        def __init__(self, jd, ra, dec, tmag):
            self._data = {"datetime_jd": jd, "RA": ra, "DEC": dec, "Tmag": tmag}
        def __getitem__(self, key):
            return self._data[key]

    rows = [MockRow(2461481.5, 123.456, -12.345, 8.5)]
    positions = _extract_positions(rows, section='comets')
    assert positions[0]["vmag"] == 8.5


def test_extract_positions_includes_vmag_asteroid():
    """Asteroid rows: vmag comes from V column."""
    from scripts.update_ephemeris_cache import _extract_positions

    class MockRow:
        def __init__(self, jd, ra, dec, v):
            self._data = {"datetime_jd": jd, "RA": ra, "DEC": dec, "V": v}
        def __getitem__(self, key):
            return self._data[key]

    rows = [MockRow(2461481.5, 99.0, 5.0, 12.3)]
    positions = _extract_positions(rows, section='asteroids')
    assert positions[0]["vmag"] == 12.3


def test_extract_positions_vmag_none_on_missing_column():
    """Missing Tmag/V column → vmag is None, no crash."""
    from scripts.update_ephemeris_cache import _extract_positions

    class MockRow:
        def __init__(self, jd, ra, dec):
            self._data = {"datetime_jd": jd, "RA": ra, "DEC": dec}
        def __getitem__(self, key):
            if key not in self._data:
                raise KeyError(key)
            return self._data[key]

    rows = [MockRow(2461481.5, 1.0, 2.0)]
    positions = _extract_positions(rows, section='comets')
    assert positions[0]["vmag"] is None


def test_extract_positions_old_signature_still_works():
    """Existing call with no section arg returns vmag=None (backwards compat)."""
    from scripts.update_ephemeris_cache import _extract_positions

    class MockRow:
        def __init__(self, jd, ra, dec):
            self._data = {"datetime_jd": jd, "RA": ra, "DEC": dec}
        def __getitem__(self, key):
            return self._data[key]

    rows = [MockRow(2461481.5, 1.0, 2.0)]
    positions = _extract_positions(rows)   # no section
    assert "vmag" in positions[0]
    assert positions[0]["vmag"] is None


def test_lookup_cached_position_hit_with_vmag():
    """Cache entry with vmag → returns (ra, dec, vmag)."""
    from backend.config import lookup_cached_position as _lcp
    cache = {
        "comets": {
            "C/2024 G3 (ATLAS)": {
                "positions": [{"date": "2026-03-05", "ra": 99.9, "dec": 10.1, "vmag": 7.5}]
            }
        }
    }
    result = _lcp(cache, "comets", "C/2024 G3 (ATLAS)", "2026-03-05")
    assert result == (99.9, 10.1, 7.5)


def test_lookup_cached_position_hit_without_vmag_returns_none():
    """Old cache entry (no vmag field) → vmag part is None, no crash."""
    from backend.config import lookup_cached_position as _lcp
    cache = {
        "comets": {
            "X": {"positions": [{"date": "2026-03-05", "ra": 1.0, "dec": 2.0}]}
        }
    }
    result = _lcp(cache, "comets", "X", "2026-03-05")
    assert result == (1.0, 2.0, None)
