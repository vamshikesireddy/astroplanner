from astropy.coordinates import AltAz
from astropy.time import Time
from astropy import units as u
import pytz
from datetime import timedelta
from utils import azimuth_to_compass

def compute_trajectory(sky_coord, location, start_time_local, duration_minutes=240, step_minutes=10):
    """Computes the AltAz trajectory of a target."""
    results = []
    time_steps = [start_time_local + timedelta(minutes=i) for i in range(0, duration_minutes + 1, step_minutes)]

    for t in time_steps:
        t_utc = t.astimezone(pytz.utc)
        time_utc = Time(t_utc)
        altaz_frame = AltAz(obstime=time_utc, location=location)
        altaz = sky_coord.transform_to(altaz_frame)
        lst = time_utc.sidereal_time('apparent', longitude=location.lon)
        compass_dir = azimuth_to_compass(altaz.az.degree)

        results.append({
            "Local Time": t.strftime('%Y-%m-%d %H:%M:%S'),
            "UTC Time": t_utc.strftime('%Y-%m-%d %H:%M:%S'),
            "LST": lst.to_string(sep=':', precision=2),
            "Azimuth (°)": round(altaz.az.degree, 2),
            "Altitude (°)": round(altaz.alt.degree, 2),
            "Direction": compass_dir
        })
    return results