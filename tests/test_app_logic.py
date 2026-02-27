"""Tests for backend/app_logic.py — pure business logic."""
import pytest
from backend.app_logic import az_in_selected, _AZ_OCTANTS, _AZ_LABELS


def test_az_in_selected_single_dir():
    assert az_in_selected(90.0, {"E"}) is True      # E = 67.5–112.5
    assert az_in_selected(180.0, {"E"}) is False

def test_az_in_selected_empty_dirs_should_raise_or_return_false():
    # Empty set = no filter at call site, but function itself should return False
    assert az_in_selected(90.0, set()) is False

def test_az_in_selected_north_wrap():
    # N spans 337.5–360 AND 0–22.5 (wrap-around case)
    assert az_in_selected(350.0, {"N"}) is True
    assert az_in_selected(10.0, {"N"}) is True
    assert az_in_selected(180.0, {"N"}) is False

def test_az_in_selected_boundary_exclusive():
    # NE = [22.5, 67.5)
    assert az_in_selected(22.5, {"NE"}) is True
    assert az_in_selected(67.5, {"NE"}) is False   # upper bound exclusive

def test_az_in_selected_multiple_dirs():
    assert az_in_selected(90.0, {"E", "S"}) is True   # in E
    assert az_in_selected(180.0, {"E", "S"}) is True  # in S
    assert az_in_selected(270.0, {"E", "S"}) is False  # in W

def test_az_labels_order():
    assert _AZ_LABELS == ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

def test_az_octants_all_dirs_present():
    for d in _AZ_LABELS:
        assert d in _AZ_OCTANTS


from backend.app_logic import get_moon_status

def test_get_moon_status_dark_sky():
    assert get_moon_status(5, 90) == "🌑 Dark Sky"   # illumination < 15

def test_get_moon_status_avoid():
    assert get_moon_status(50, 20) == "⛔ Avoid"      # illum >= 15, sep < 30

def test_get_moon_status_caution():
    assert get_moon_status(50, 45) == "⚠️ Caution"   # sep 30–60

def test_get_moon_status_safe():
    assert get_moon_status(50, 90) == "✅ Safe"       # sep > 60

def test_get_moon_status_boundary_illum():
    # At exactly illum=15, dark-sky threshold is NOT met
    assert get_moon_status(15, 90) == "✅ Safe"

def test_get_moon_status_boundary_sep():
    assert get_moon_status(50, 30) == "⚠️ Caution"   # sep == 30 → Caution (not Avoid)
    assert get_moon_status(50, 60) == "✅ Safe"        # sep == 60 → Safe (not Caution)


import pytz
from datetime import datetime, timedelta
from astropy.coordinates import EarthLocation, SkyCoord
from astropy import units as u
from backend.app_logic import _check_row_observability


def _make_check_times():
    tz = pytz.utc
    start = datetime(2025, 6, 15, 22, 0, tzinfo=tz)
    return [start + timedelta(minutes=m) for m in (0, 120, 240)]


def test_check_row_observability_never_rises():
    loc = EarthLocation(lat=80 * u.deg, lon=0 * u.deg)
    sc  = SkyCoord(ra=0 * u.deg, dec=-80 * u.deg, frame='icrs')
    obs, reason, ms, mst = _check_row_observability(
        sc, "Never Rises", loc, _make_check_times(),
        None, [], 5.0, 10, 90, set(), 0
    )
    assert obs is False
    assert reason == "Never Rises"

def test_check_row_observability_passes_no_az_filter():
    """Object visible at alt >= 10 with no azimuth filter -> observable."""
    loc = EarthLocation(lat=40 * u.deg, lon=-74 * u.deg)
    sc = SkyCoord(ra=279.23 * u.deg, dec=38.78 * u.deg, frame='icrs')
    tz = pytz.utc
    times = [datetime(2025, 7, 15, 3, 0, tzinfo=tz) + timedelta(hours=i) for i in range(3)]
    obs, reason, ms, mst = _check_row_observability(
        sc, "Visible", loc, times, None, [], 5.0, 10, 90, set(), 0
    )
    assert obs is True

def test_check_row_observability_does_not_raise_with_az_filter():
    """Az filter applied -> result is bool, no exception raised."""
    loc = EarthLocation(lat=40 * u.deg, lon=-74 * u.deg)
    sc = SkyCoord(ra=279.23 * u.deg, dec=38.78 * u.deg, frame='icrs')
    tz = pytz.utc
    times = [datetime(2025, 7, 15, 3, 0, tzinfo=tz)]
    obs, reason, _, _ = _check_row_observability(
        sc, "Visible", loc, times, None, [], 5.0, 10, 90, {"N"}, 0
    )
    assert isinstance(obs, bool)


import pandas as pd
from datetime import datetime, timezone
from backend.app_logic import _sort_df_like_chart, build_night_plan


def _make_sort_df():
    """Minimal DataFrame with Status + datetime columns."""
    t1 = datetime(2025, 6, 15, 22, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 6, 15, 23, 0, tzinfo=timezone.utc)
    t3 = datetime(2025, 6, 16,  0, 0, tzinfo=timezone.utc)
    return pd.DataFrame({
        "Name": ["A", "B", "C"],
        "Status": ["Visible", "Visible", "Always Up (Circumpolar)"],
        "_rise_datetime":    [t1, t2, None],
        "_set_datetime":     [t3, t3, None],
        "_transit_datetime": [t2, t1, t1],
    })


def test_sort_df_earliest_rise_always_up_at_bottom():
    df = _make_sort_df()
    result = _sort_df_like_chart(df, "Earliest Rise")
    assert result["Name"].tolist()[-1] == "C"  # Always Up must be last

def test_sort_df_earliest_set_always_up_at_bottom():
    df = _make_sort_df()
    result = _sort_df_like_chart(df, "Earliest Set")
    assert result["Name"].tolist()[-1] == "C"

def test_sort_df_priority_order():
    df = pd.DataFrame({
        "Name": ["X", "Y", "Z"],
        "Status": ["Visible", "Visible", "Visible"],
        "Priority": ["LOW", "URGENT", "HIGH"],
    })
    result = _sort_df_like_chart(df, "Priority Order", priority_col="Priority")
    assert result["Name"].tolist() == ["Y", "Z", "X"]   # URGENT, HIGH, LOW

def test_sort_df_none_returns_unchanged():
    df = _make_sort_df()
    result = _sort_df_like_chart(df, None)
    assert result["Name"].tolist() == ["A", "B", "C"]

def test_build_night_plan_sort_by_transit_b_first():
    df = _make_sort_df().iloc[:2].copy()  # A and B only
    result = build_night_plan(df, sort_by="transit")
    # B transits at t1 (22:00), A at t2 (23:00) — B should be first
    assert result["Name"].tolist()[0] == "B"

def test_build_night_plan_returns_copy_not_original():
    df = _make_sort_df().iloc[:2].copy()
    result = build_night_plan(df, sort_by="set")
    assert result is not df  # must be a copy
