import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.populate_jpl_cache import resolve_all

def test_asteroid_sbdb_id_converted(monkeypatch):
    """SBDB internal IDs in [20M, 30M) for asteroids must be converted to 2M+N Horizons SPK-ID."""
    import scripts.populate_jpl_cache as pjc
    monkeypatch.setattr(pjc, "sbdb_lookup", lambda q: "20000433")
    resolved, failed = resolve_all(["433 Eros"], "asteroids", lambda n: n, {})
    assert resolved.get("433 Eros") == "2000433"
    assert "433 Eros" not in failed


def test_comet_bad_spk_id_not_saved(monkeypatch):
    """SBDB internal IDs in [20M, 30M) for comets must be dropped (Horizons rejects them)."""
    import scripts.populate_jpl_cache as pjc
    monkeypatch.setattr(pjc, "sbdb_lookup", lambda q: "20001797")
    resolved, failed = resolve_all(["24P/Schaumasse"], "comets", lambda n: n, {})
    assert "24P/Schaumasse" not in resolved
    assert "24P/Schaumasse" in failed

def test_valid_spk_id_saved(monkeypatch):
    """Valid comet SPK-IDs in [1_000_000, 2_000_000) must be kept."""
    import scripts.populate_jpl_cache as pjc
    monkeypatch.setattr(pjc, "sbdb_lookup", lambda q: "1003993")
    resolved, failed = resolve_all(["C/2024 G3 (ATLAS)"], "comets",
                                   lambda n: n.split('(')[0].strip(), {})
    assert resolved.get("C/2024 G3 (ATLAS)") == "1003993"
    assert failed == []

def test_none_spk_id_fails(monkeypatch):
    """When SBDB returns None, object goes to failed list."""
    import scripts.populate_jpl_cache as pjc
    monkeypatch.setattr(pjc, "sbdb_lookup", lambda q: None)
    resolved, failed = resolve_all(["C/2099 Z9 (UNKNOWN)"], "comets",
                                   lambda n: n.split('(')[0].strip(), {})
    assert resolved == {}
    assert "C/2099 Z9 (UNKNOWN)" in failed
