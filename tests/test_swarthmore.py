# tests/test_swarthmore.py
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock
from backend.swarthmore import fetch_transit_windows, _parse_swarthmore_csv

# Fixture matches real Swarthmore CSV format
SAMPLE_CSV = """Name,coords(J2000),V,depth(ppt),duration(hours),start time,mid time,end time,el_start,el_mid,el_end,moon_dist_deg,percent_transit_observable
HAT-P-12 b,13 57 33  +43 29 36,12.8,19.0,2:14,2026-03-01 02:30,2026-03-01 03:35,2026-03-01 04:40,45,52,38,85.2,100
WASP-43 b,10 19 38  -09 47 22,12.4,27.0,1:12,2026-03-02 20:15,2026-03-02 20:51,2026-03-02 21:27,22,28,15,42.5,70
"""

def test_parse_returns_dataframe():
    df = _parse_swarthmore_csv(SAMPLE_CSV)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2

def test_parse_columns_present():
    df = _parse_swarthmore_csv(SAMPLE_CSV)
    assert "Planet" in df.columns       # renamed from Name
    assert "_ingress_dt" in df.columns
    assert "_egress_dt" in df.columns
    assert "_mid_dt" in df.columns
    assert "Duration_hr" in df.columns  # float parsed from H:MM

def test_parse_completeness():
    df = _parse_swarthmore_csv(SAMPLE_CSV)
    assert df.loc[df["Planet"] == "HAT-P-12 b", "Completeness"].iloc[0] == "Complete"
    assert df.loc[df["Planet"] == "WASP-43 b",  "Completeness"].iloc[0] == "Partial"

def test_parse_duration_float():
    df = _parse_swarthmore_csv(SAMPLE_CSV)
    dur = df.loc[df["Planet"] == "HAT-P-12 b", "Duration_hr"].iloc[0]
    assert abs(dur - (2 + 14/60)) < 0.01

def test_parse_min_alt():
    df = _parse_swarthmore_csv(SAMPLE_CSV)
    # min of el_start=45, el_mid=52, el_end=38 → 38
    alt = df.loc[df["Planet"] == "HAT-P-12 b", "Min_Alt_During_Transit"].iloc[0]
    assert alt == 38.0

def test_fetch_filters_by_planet_names():
    with patch("backend.swarthmore.requests.get") as mock_get:
        mock_get.return_value = MagicMock(text=SAMPLE_CSV, status_code=200, raise_for_status=lambda: None)
        df = fetch_transit_windows(
            lat=37.7, lon=-122.4, tz_name="America/Los_Angeles",
            planet_names=["HAT-P-12 b"], days=7,
        )
    assert len(df) == 1
    assert df.iloc[0]["Planet"] == "HAT-P-12 b"

def test_fetch_empty_planet_names_returns_empty():
    df = fetch_transit_windows(lat=37.7, lon=-122.4, tz_name="America/Los_Angeles",
                               planet_names=[], days=7)
    assert df.empty

def test_fetch_network_error_returns_empty():
    with patch("backend.swarthmore.requests.get", side_effect=Exception("timeout")):
        df = fetch_transit_windows(lat=37.7, lon=-122.4, tz_name="America/Los_Angeles",
                                   planet_names=["HAT-P-12 b"], days=7)
    assert df.empty

def test_fetch_builds_correct_observatory_string():
    """West longitude -122.4 should become 360 + (-122.4) = 237.6 in the URL."""
    with patch("backend.swarthmore.requests.get") as mock_get:
        mock_get.return_value = MagicMock(text=SAMPLE_CSV, status_code=200, raise_for_status=lambda: None)
        fetch_transit_windows(lat=37.7, lon=-122.4, tz_name="America/Los_Angeles",
                              planet_names=["HAT-P-12 b"], days=7)
    call_kwargs = mock_get.call_args
    # observatory_string should contain 237.6 (360 + (-122.4))
    obs_str = call_kwargs[1]["params"]["observatory_string"]
    assert "237.6" in obs_str
    assert ";" in obs_str   # semicolons not commas
