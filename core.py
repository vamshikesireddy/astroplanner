from astropy.coordinates import AltAz
from astropy.time import Time
from astropy import units as u
import pytz
from datetime import timedelta
from utils import azimuth_to_compass

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