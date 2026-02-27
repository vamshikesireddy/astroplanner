import math
import pytest
import pytz
from datetime import datetime
from astropy.coordinates import EarthLocation, SkyCoord
from astropy import units as u
from backend.core import azimuth_to_compass, moon_sep_deg, calculate_planning_info


# ── azimuth_to_compass ────────────────────────────────────────────────────

def test_azimuth_to_compass_cardinal():
    assert azimuth_to_compass(0.0)   == "N"
    assert azimuth_to_compass(90.0)  == "E"
    assert azimuth_to_compass(180.0) == "S"
    assert azimuth_to_compass(270.0) == "W"

def test_azimuth_to_compass_intercardinal():
    assert azimuth_to_compass(45.0)  == "NE"
    assert azimuth_to_compass(135.0) == "SE"
    assert azimuth_to_compass(225.0) == "SW"
    assert azimuth_to_compass(315.0) == "NW"

def test_azimuth_to_compass_near_boundary():
    # 337.5 is the boundary between NNW (316.875–337.5) and N (337.5–360 + 0–22.5)
    # The formula: ix = int((az + 11.25) / 22.5) % 16
    # 337.5 + 11.25 = 348.75 → int(348.75/22.5) = int(15.5) = 15 → directions[15] = "NNW"
    assert azimuth_to_compass(337.5) == "NNW"
    # 22.4 + 11.25 = 33.65 → int(33.65/22.5) = int(1.495...) = 1 → directions[1] = "NNE"
    assert azimuth_to_compass(22.4)  == "NNE"


# ── moon_sep_deg ──────────────────────────────────────────────────────────

def test_moon_sep_deg_returns_float_in_valid_range():
    from astropy.time import Time
    try:
        from astropy.coordinates import get_moon as _gm
    except ImportError:
        from astropy.coordinates import get_body
        def _gm(t, loc=None, eph=None):
            return get_body("moon", t, loc, ephemeris=eph)
    t = Time("2025-06-01 00:00:00")
    loc = EarthLocation(lat=37.7 * u.deg, lon=-122.4 * u.deg)
    moon = _gm(t, loc)
    target = SkyCoord(ra=180 * u.deg, dec=0 * u.deg, frame='icrs')
    sep = moon_sep_deg(target, moon)
    assert isinstance(sep, float)
    assert 0.0 <= sep <= 180.0


# ── calculate_planning_info ───────────────────────────────────────────────

def test_calculate_planning_info_always_up():
    """A circumpolar star at lat=45 N should return Always Up."""
    loc = EarthLocation(lat=45 * u.deg, lon=0 * u.deg)
    sc = SkyCoord(ra=0 * u.deg, dec=80 * u.deg, frame='icrs')  # dec=80, lat=45 → circumpolar
    tz = pytz.utc
    start = datetime(2025, 6, 1, 18, 0, 0, tzinfo=tz)
    result = calculate_planning_info(sc, loc, start)
    assert result["Status"] == "Always Up (Circumpolar)"
    assert result["Rise"]   == "Always Up"
    assert result["_rise_datetime"] is not None
    assert result["_transit_datetime"] is not None

def test_calculate_planning_info_never_rises():
    """A far-southern star seen from far north should Never Rise."""
    loc = EarthLocation(lat=80 * u.deg, lon=0 * u.deg)
    sc = SkyCoord(ra=0 * u.deg, dec=-80 * u.deg, frame='icrs')  # never rises from lat=80N
    tz = pytz.utc
    start = datetime(2025, 6, 1, 18, 0, 0, tzinfo=tz)
    result = calculate_planning_info(sc, loc, start)
    assert result["Status"] == "Never Rises"

def test_calculate_planning_info_visible_returns_datetimes():
    """A non-circumpolar equatorial star should return rise/set datetimes."""
    loc = EarthLocation(lat=45 * u.deg, lon=0 * u.deg)
    sc = SkyCoord(ra=0 * u.deg, dec=20 * u.deg, frame='icrs')
    tz = pytz.utc
    start = datetime(2025, 6, 1, 18, 0, 0, tzinfo=tz)
    result = calculate_planning_info(sc, loc, start)
    assert result["_rise_datetime"] is not None
    assert result["_set_datetime"]  is not None
    assert result["_transit_datetime"] is not None
    assert result["Status"] == "Visible"

def test_calculate_planning_info_set_after_rise():
    """Set time must be after rise time for a visible object."""
    loc = EarthLocation(lat=45 * u.deg, lon=0 * u.deg)
    sc = SkyCoord(ra=90 * u.deg, dec=20 * u.deg, frame='icrs')
    tz = pytz.utc
    start = datetime(2025, 6, 1, 18, 0, 0, tzinfo=tz)
    result = calculate_planning_info(sc, loc, start)
    if result["Status"] == "Visible":
        assert result["_set_datetime"] > result["_rise_datetime"]


# ── compute_peak_alt_in_window ────────────────────────────────────────────────

def test_compute_peak_alt_in_window_below_horizon():
    """Object that never rises from the observer's location returns negative peak altitude."""
    from datetime import datetime
    import pytz
    from astropy.coordinates import EarthLocation
    import astropy.units as u
    from backend.core import compute_peak_alt_in_window

    # Deep southern object (Dec=-70) never rises from New York (lat=40.7N)
    loc = EarthLocation(lat=40.7 * u.deg, lon=-74.0 * u.deg)
    tz  = pytz.timezone('America/New_York')
    win_start = tz.localize(datetime(2026, 7, 1, 21, 0))
    win_end   = tz.localize(datetime(2026, 7, 1, 23, 0))
    # RA=0, Dec=-70 — circumpolar below horizon from lat 40.7N
    peak = compute_peak_alt_in_window(0.0, -70.0, loc, win_start, win_end)
    assert isinstance(peak, float)
    assert peak < 0.0, f"Southern object should be below horizon from 40.7N, got {peak:.1f}°"


def test_compute_peak_alt_in_window_high_object():
    """Object transiting near zenith returns altitude well above 30 degrees."""
    from datetime import datetime
    import pytz
    from astropy.coordinates import EarthLocation
    import astropy.units as u
    from backend.core import compute_peak_alt_in_window

    loc = EarthLocation(lat=40.7 * u.deg, lon=-74.0 * u.deg)
    tz  = pytz.timezone('America/New_York')
    win_start = tz.localize(datetime(2026, 7, 1, 21, 0))
    win_end   = tz.localize(datetime(2026, 7, 1, 23, 0))
    peak = compute_peak_alt_in_window(279.23, 38.78, loc, win_start, win_end)
    assert peak > 30.0, f"Vega near transit should exceed 30 degrees, got {peak:.1f}"


def test_compute_peak_alt_in_window_n_steps_minimum():
    """n_steps=2 (minimum) still returns a valid float."""
    from datetime import datetime
    import pytz
    from astropy.coordinates import EarthLocation
    import astropy.units as u
    from backend.core import compute_peak_alt_in_window

    loc = EarthLocation(lat=40.7 * u.deg, lon=-74.0 * u.deg)
    tz  = pytz.timezone('America/New_York')
    win_start = tz.localize(datetime(2026, 7, 1, 21, 0))
    win_end   = tz.localize(datetime(2026, 7, 1, 23, 0))
    peak = compute_peak_alt_in_window(279.23, 38.78, loc, win_start, win_end, n_steps=2)
    assert isinstance(peak, float)
    assert -90.0 <= peak <= 90.0
