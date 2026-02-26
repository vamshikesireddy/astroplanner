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
