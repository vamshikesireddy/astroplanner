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


# ── _sanitize_csv_df and _add_peak_alt_session tests ──────────────────────────

import pandas as pd
from backend.app_logic import _sanitize_csv_df, _add_peak_alt_session


def test_sanitize_csv_df_escapes_formula_prefixes():
    df = pd.DataFrame({"A": ["=SUM(1)", "+PROFIT", "normal", "@risk", "-val"]})
    result = _sanitize_csv_df(df)
    assert result["A"].tolist() == ["'=SUM(1)", "'+PROFIT", "normal", "'@risk", "'-val"]

def test_sanitize_csv_df_leaves_numeric_columns_alone():
    df = pd.DataFrame({"A": [1, 2.5, None], "B": ["=bad", "ok", "ok"]})
    result = _sanitize_csv_df(df)
    # pandas upcasts int→float when None is present; NaN == NaN is False so
    # check non-null values and dtype instead of exact list equality
    assert result["A"].dtype.kind == 'f'            # numeric (float) — not object
    assert result["A"].iloc[0] == 1.0
    assert result["A"].iloc[1] == 2.5
    assert pd.isna(result["A"].iloc[2])             # None → NaN, still numeric
    assert result["B"].iloc[0] == "'=bad"           # string escaped

def test_add_peak_alt_session_no_location_returns_none_column():
    df = pd.DataFrame({"_ra_deg": [10.0], "_dec_deg": [20.0]})
    result = _add_peak_alt_session(df, location=None, win_start_tz=None, win_end_tz=None)
    assert "_peak_alt_session" in result.columns
    assert result["_peak_alt_session"].isna().all()

def test_add_peak_alt_session_missing_coord_cols_returns_none_column():
    df = pd.DataFrame({"Name": ["X"]})  # no _ra_deg / _dec_deg
    result = _add_peak_alt_session(df, location=None, win_start_tz=None, win_end_tz=None)
    assert "_peak_alt_session" in result.columns
    assert result["_peak_alt_session"].isna().all()


# ── _apply_night_plan_filters tests ───────────────────────────────────────────

import pytz
from datetime import datetime, timedelta
import pandas as pd
from backend.app_logic import _apply_night_plan_filters


def _base_obs_df():
    tz = pytz.utc
    t_rise = datetime(2025, 6, 15, 20, 0, tzinfo=tz)
    t_set  = datetime(2025, 6, 16,  4, 0, tzinfo=tz)
    t_tra  = datetime(2025, 6, 16,  0, 0, tzinfo=tz)
    return pd.DataFrame({
        "Name": ["A", "B", "C"],
        "Status": ["Visible", "Visible", "Visible"],
        "Priority": ["URGENT", "LOW", "HIGH"],
        "Moon Status": ["✅ Safe", "⚠️ Caution", "✅ Safe"],
        "_rise_datetime":    [t_rise, t_rise, t_rise],
        "_set_datetime":     [t_set,  t_set,  t_set],
        "_transit_datetime": [t_tra,  t_tra,  t_tra],
        "_ra_deg":  [100.0, 150.0, 200.0],
        "_dec_deg": [ 30.0,  30.0,  30.0],
    })


def _win(hour_start=22, hour_end=2):
    tz = pytz.utc
    s = datetime(2025, 6, 15, hour_start, 0, tzinfo=tz)
    e = datetime(2025, 6, 16, hour_end,   0, tzinfo=tz)
    return s, e


def test_apply_filters_priority_keeps_only_urgent():
    df = _base_obs_df()
    win_s, win_e = _win()
    result = _apply_night_plan_filters(
        df, pri_col="Priority", sel_pri=["URGENT"],
        vmag_col=None, vmag_range=None,
        type_col=None, sel_types=None,
        disc_col=None, disc_days=None,
        win_start_dt=win_s, win_end_dt=win_e,
        sel_moon=None, all_moon_statuses=["✅ Safe", "⚠️ Caution"],
    )
    assert list(result["Name"]) == ["A"]

def test_apply_filters_moon_status_excludes_caution():
    df = _base_obs_df()
    win_s, win_e = _win()
    all_statuses = ["✅ Safe", "⚠️ Caution"]
    result = _apply_night_plan_filters(
        df, pri_col=None, sel_pri=None,
        vmag_col=None, vmag_range=None,
        type_col=None, sel_types=None,
        disc_col=None, disc_days=None,
        win_start_dt=win_s, win_end_dt=win_e,
        sel_moon=["✅ Safe"], all_moon_statuses=all_statuses,
    )
    assert "B" not in result["Name"].tolist()

def test_apply_filters_window_excludes_target_after_window():
    tz = pytz.utc
    late_rise = datetime(2025, 6, 16, 5, 0, tzinfo=tz)
    late_set  = datetime(2025, 6, 16, 8, 0, tzinfo=tz)
    df = pd.DataFrame({
        "Name": ["Late"],
        "Status": ["Visible"],
        "_rise_datetime":    [late_rise],
        "_set_datetime":     [late_set],
        "_transit_datetime": [late_rise],
        "_ra_deg": [100.0], "_dec_deg": [30.0],
    })
    win_s, win_e = _win()
    result = _apply_night_plan_filters(
        df, None, None, None, None, None, None, None, None,
        win_start_dt=win_s, win_end_dt=win_e,
        sel_moon=None, all_moon_statuses=[],
    )
    assert result.empty

def test_apply_filters_always_up_passes_window():
    tz = pytz.utc
    df = pd.DataFrame({
        "Name": ["Polaris"],
        "Status": ["Always Up (Circumpolar)"],
        "_rise_datetime":    [None],
        "_set_datetime":     [None],
        "_transit_datetime": [None],
        "_ra_deg": [37.95], "_dec_deg": [89.26],
    })
    win_s, win_e = _win()
    result = _apply_night_plan_filters(
        df, None, None, None, None, None, None, None, None,
        win_start_dt=win_s, win_end_dt=win_e,
        sel_moon=None, all_moon_statuses=[],
    )
    assert len(result) == 1


# ── _get_dso_image_url ────────────────────────────────────────────────────────

from backend.app_logic import _get_dso_image_url

def test_get_dso_image_url_returns_curated_when_set():
    url = _get_dso_image_url(10.685, 41.269, "Galaxy", "https://example.com/m31.jpg")
    assert url == "https://example.com/m31.jpg"

def test_get_dso_image_url_builds_aladin_for_galaxy():
    url = _get_dso_image_url(10.685, 41.269, "Galaxy", None)
    assert "alasky.cds.unistra.fr" in url
    assert "ra=10.685" in url
    assert "dec=41.269" in url
    assert "fov=1.0" in url

def test_get_dso_image_url_uses_narrow_fov_for_star():
    url = _get_dso_image_url(101.287, -16.793, "Star", None)
    assert "fov=0.3" in url

def test_get_dso_image_url_ignores_empty_string_curated():
    url = _get_dso_image_url(10.685, 41.269, "Galaxy", "")
    assert "alasky.cds.unistra.fr" in url


# ── _get_dso_local_image ──────────────────────────────────────────────────────

from pathlib import Path
from backend.app_logic import _get_dso_local_image

def test_get_dso_local_image_found(tmp_path):
    (tmp_path / "M31.jpg").write_bytes(b"fake-image-data")
    result = _get_dso_local_image("M31", base_dir=tmp_path)
    assert result == tmp_path / "M31.jpg"

def test_get_dso_local_image_missing(tmp_path):
    result = _get_dso_local_image("M31", base_dir=tmp_path)
    assert result is None

def test_get_dso_local_image_sanitizes_name(tmp_path):
    (tmp_path / "NGC_7000.jpg").write_bytes(b"fake")
    result = _get_dso_local_image("NGC 7000", base_dir=tmp_path)
    assert result == tmp_path / "NGC_7000.jpg"


# ── _sort_df_like_chart Brightest First tests ─────────────────────────────────

def test_sort_df_brightest_first():
    """'Brightest First' sorts ascending by Magnitude; NaN rows go to bottom."""
    df = pd.DataFrame({
        "Name":      ["Bright", "Faint", "Unknown"],
        "Status":    ["Visible", "Visible", "Visible"],
        "Magnitude": [6.0, 14.5, float("nan")],
    })
    result = _sort_df_like_chart(df, "Brightest First", brightness_col="Magnitude")
    names = result["Name"].tolist()
    assert names[0] == "Bright"
    assert names[1] == "Faint"
    assert names[2] == "Unknown"   # NaN at bottom


def test_sort_df_brightest_first_missing_col_returns_unchanged():
    """'Brightest First' with no Magnitude col -> return df unchanged."""
    df = pd.DataFrame({
        "Name":   ["A", "B"],
        "Status": ["Visible", "Visible"],
    })
    result = _sort_df_like_chart(df, "Brightest First", brightness_col="Magnitude")
    assert result["Name"].tolist() == ["A", "B"]
