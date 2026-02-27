from astropy.coordinates import AltAz, SkyCoord
from astropy.time import Time
from astropy import units as u
import pytz
import math
from datetime import timedelta

try:
    from astropy.coordinates import get_moon as _get_moon
except ImportError:
    from astropy.coordinates import get_body
    def _get_moon(time, location=None, ephemeris=None):
        return get_body("moon", time, location, ephemeris=ephemeris)


def moon_sep_deg(target_coord, moon_coord):
    """Angular separation in degrees between a target and the Moon.

    get_body('moon') returns a 3D GCRS coordinate (with distance).
    Calling target.separation(moon) across ICRS↔GCRS with 3D coords
    produces wrong results due to non-rotation transformation artifacts.
    Stripping the distance turns it into a direction-only coordinate,
    giving the correct great-circle angular separation.
    """
    moon_dir = SkyCoord(ra=moon_coord.ra, dec=moon_coord.dec, frame=moon_coord.frame)
    return target_coord.separation(moon_dir).degree

def azimuth_to_compass(az):
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((az + 11.25) / 22.5) % 16
    return directions[ix]

def compute_trajectory(sky_coord, location, start_time_local, duration_minutes=240, step_minutes=10, ephemeris_coords=None):
    """Computes the AltAz trajectory of a target."""
    results = []
    time_steps = [start_time_local + timedelta(minutes=i) for i in range(0, duration_minutes + 1, step_minutes)]
    
    # If no dynamic ephemeris provided, use the fixed sky_coord for all steps
    constellation = ""
    if not ephemeris_coords:
        constellation = sky_coord.get_constellation()

    for i, t in enumerate(time_steps):
        # Determine target coordinate for this specific time step
        if ephemeris_coords and i < len(ephemeris_coords):
            target_coord = ephemeris_coords[i]
            # Update constellation for moving objects (though it changes slowly)
            constellation = target_coord.get_constellation()
        else:
            target_coord = sky_coord

        t_utc = t.astimezone(pytz.utc)
        time_utc = Time(t_utc)
        altaz_frame = AltAz(obstime=time_utc, location=location)
        altaz = target_coord.transform_to(altaz_frame)
        compass_dir = azimuth_to_compass(altaz.az.degree)

        try:
            moon_sky = _get_moon(time_utc, location)
            moon_sep_val = round(moon_sep_deg(target_coord, moon_sky), 1)
        except Exception:
            moon_sep_val = None

        results.append({
            "Local Time": t.strftime('%Y-%m-%d %H:%M:%S'),
            "RA": target_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
            "Dec": target_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
            "Azimuth (°)": round(altaz.az.degree, 2),
            "Altitude (°)": round(altaz.alt.degree, 2),
            "Direction": compass_dir,
            "Constellation": constellation,
            "Moon Sep (°)": moon_sep_val,
        })
    return results

def calculate_planning_info(sky_coord, location, start_time):
    """
    Calculates summary planning info (Rise, Transit, Set) for a target.
    Uses geometric approximation for speed.
    """
    t_utc = start_time.astimezone(pytz.utc)
    astro_time = Time(t_utc)
    
    # 2. Constellation
    constellation = sky_coord.get_constellation(short_name=True)

    # 3. Rise/Set/Transit Approximation
    # Calculate Local Sidereal Time (LST)
    lst = astro_time.sidereal_time('mean', longitude=location.lon)
    
    # Hour Angle (HA) for Transit (when HA = 0, i.e., LST = RA)
    # RA is in degrees, LST is in hourangle. Convert RA to hourangle.
    ra_ha = sky_coord.ra.hour
    lst_ha = lst.hour
    
    # Time difference to transit (in hours)
    diff_hours = (ra_ha - lst_ha) % 24
    if diff_hours > 12: diff_hours -= 24
    
    transit_time = start_time + timedelta(hours=diff_hours)
    tz_str = start_time.strftime("%Z")
    
    # Calculate semi-diurnal arc (time from rise to transit)
    # cos(H) = (sin(alt) - sin(lat)sin(dec)) / (cos(lat)cos(dec))
    # Geometric rise is alt = 0 (ignoring refraction for planning speed)
    lat_rad = location.lat.rad
    dec_rad = sky_coord.dec.rad
    
    try:
        cos_h = (math.sin(-0.01) - math.sin(lat_rad) * math.sin(dec_rad)) / (math.cos(lat_rad) * math.cos(dec_rad))
        
        time_fmt = f"%m-%d %H:%M {tz_str}"

        if cos_h < -1:
            status = "Always Up (Circumpolar)"
            # For visualization, anchor to start_time to prevent graph skew
            rise_time = start_time
            set_time = start_time + timedelta(hours=24)

            return {
                "Constellation": constellation,
                "Transit": transit_time.strftime(time_fmt),
                "Rise": "Always Up",
                "Set": "Always Up",
                "Status": status,
                "_rise_datetime": rise_time,
                "_set_datetime": set_time,
                "_transit_datetime": transit_time
            }
        elif cos_h > 1:
            status = "Never Rises"
        else:
            h_rad = math.acos(cos_h)
            h_hours = math.degrees(h_rad) / 15.0

            rise_time = transit_time - timedelta(hours=h_hours)
            set_time = transit_time + timedelta(hours=h_hours)

            # If the event has already finished before the start time, show the next cycle
            if set_time < start_time:
                transit_time += timedelta(hours=24)
                rise_time += timedelta(hours=24)
                set_time += timedelta(hours=24)

            return {
                "Constellation": constellation,
                "Transit": transit_time.strftime(time_fmt),
                "Rise": rise_time.strftime(time_fmt),
                "Set": set_time.strftime(time_fmt),
                "Status": "Visible",
                "_rise_datetime": rise_time,
                "_set_datetime": set_time,
                "_transit_datetime": transit_time
            }
            
    except Exception:
        status = "Error"

    return {
        "Constellation": constellation,
        "Transit": transit_time.strftime(f"%m-%d %H:%M {tz_str}"),
        "Rise": "---",
        "Set": "---",
        "Status": status if 'status' in locals() else "Error",
        "_rise_datetime": None,
        "_set_datetime": None,
        "_transit_datetime": transit_time
    }


def compute_peak_alt_in_window(ra_deg, dec_deg, location, win_start_dt, win_end_dt, n_steps=None):
    """Return the peak altitude (degrees) of an object during an observation window.

    Samples altitude at uniform intervals across the window. n_steps defaults
    to one sample per 30 minutes, minimum 2.

    Parameters
    ----------
    ra_deg, dec_deg : float
        ICRS coordinates in decimal degrees.
    location : EarthLocation
    win_start_dt, win_end_dt : datetime (tz-aware)
        Start and end of the observation window.
    n_steps : int | None
        Number of altitude samples. Auto-computed from window duration if None
        (one per 30 min, minimum 2). Passing n_steps=1 explicitly samples only
        the window start.

    Returns
    -------
    float
        Peak altitude in degrees. Can be negative if always below horizon.
    """
    sc = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame='icrs')
    window_secs = (win_end_dt - win_start_dt).total_seconds()
    if n_steps is None:
        n_steps = max(2, int(window_secs / 1800) + 1)  # one per 30 min, min 2

    peak = -90.0
    for i in range(n_steps):
        frac = i / max(n_steps - 1, 1)
        t_sample = win_start_dt + timedelta(seconds=frac * window_secs)
        t_utc = Time(
            t_sample.astimezone(pytz.utc).replace(tzinfo=None),
            scale='utc',
        )
        aa = sc.transform_to(AltAz(obstime=t_utc, location=location))
        if aa.alt.deg > peak:
            peak = aa.alt.deg

    return float(peak)