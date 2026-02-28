"""Tests for check_unistellar_exoplanet_priorities diff logic."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _run_diff(active, priority, yaml_active, yaml_priorities):
    from scripts.check_unistellar_exoplanet_priorities import diff_exoplanet_priorities
    return diff_exoplanet_priorities(
        scraped_active=active,
        scraped_priority=priority,
        yaml_active=yaml_active,
        yaml_priorities=yaml_priorities,
    )


def test_added_planet_detected():
    changes = _run_diff(
        active=["WASP-43 b", "HAT-P-12 b"],
        priority=[],
        yaml_active=["WASP-43 b"],
        yaml_priorities={},
    )
    change_types = [c["change"] for c in changes]
    assert "ADDED" in change_types
    added = [c for c in changes if c["change"] == "ADDED"]
    assert added[0]["designation"] == "HAT-P-12 b"


def test_removed_planet_detected():
    changes = _run_diff(
        active=["WASP-43 b"],
        priority=[],
        yaml_active=["WASP-43 b", "HAT-P-12 b"],
        yaml_priorities={},
    )
    change_types = [c["change"] for c in changes]
    assert "REMOVED" in change_types
    removed = [c for c in changes if c["change"] == "REMOVED"]
    assert removed[0]["designation"] == "HAT-P-12 b"


def test_priority_gained_detected():
    changes = _run_diff(
        active=["WASP-43 b"],
        priority=["WASP-43 b"],
        yaml_active=["WASP-43 b"],
        yaml_priorities={},
    )
    change_types = [c["change"] for c in changes]
    assert "PRIORITY_ADDED" in change_types


def test_priority_lost_detected():
    changes = _run_diff(
        active=["WASP-43 b"],
        priority=[],
        yaml_active=["WASP-43 b"],
        yaml_priorities={"WASP-43 b": "⭐ PRIORITY"},
    )
    change_types = [c["change"] for c in changes]
    assert "PRIORITY_REMOVED" in change_types


def test_no_changes_returns_empty():
    changes = _run_diff(
        active=["WASP-43 b"],
        priority=["WASP-43 b"],
        yaml_active=["WASP-43 b"],
        yaml_priorities={"WASP-43 b": "⭐ PRIORITY"},
    )
    assert changes == []
