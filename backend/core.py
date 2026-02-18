from astropy.coordinates import AltAz
from astropy.time import Time
from astropy import units as u
import pytz
import math
from datetime import timedelta

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

        results.append({
            "Local Time": t.strftime('%Y-%m-%d %H:%M:%S'),
            "RA": target_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
            "Dec": target_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
            "Azimuth (°)": round(altaz.az.degree, 2),
            "Altitude (°)": round(altaz.alt.degree, 2),
            "Direction": compass_dir,
            "Constellation": constellation
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
        
        if cos_h < -1:
            status = "Always Up (Circumpolar)"
            rise_str = "---"
            set_str = "---"
        elif cos_h > 1:
            status = "Never Rises"
            rise_str = "---"
            set_str = "---"
        else:
            h_rad = math.acos(cos_h)
            h_hours = math.degrees(h_rad) / 15.0
            
            rise_time = transit_time - timedelta(hours=h_hours)
            set_time = transit_time + timedelta(hours=h_hours)
            
            # Format times
            time_fmt = f"%m-%d %H:%M {tz_str}"
            return {
                "Constellation": constellation,
                "Transit": transit_time.strftime(time_fmt),
                "Rise": rise_time.strftime(time_fmt),
                "Set": set_time.strftime(time_fmt),
                "Status": "Visible",
                "_rise_datetime": rise_time,
                "_set_datetime": set_time
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
        "_set_datetime": None
    }