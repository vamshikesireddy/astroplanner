import re
from astropy.coordinates import SkyCoord, FK5
from astropy import units as u
from astropy.time import Time
from datetime import timedelta
from astroquery.simbad import Simbad
from astroquery.jplhorizons import Horizons

def _horizons_query(obj_name, location_code, epochs, closest_apparition=True):
    """Query JPL Horizons with 3-level fallback.

    epochs: float (JD) for single-time queries, or dict with start/stop/step for ranges.
    Returns the ephemerides result table.
    Raises RuntimeError if all attempts fail.
    """
    ca_kwargs = {"closest_apparition": True} if closest_apparition else {}

    # Attempt 1: id_type='smallbody'
    try:
        obj = Horizons(id=obj_name, location=location_code, epochs=epochs, id_type='smallbody')
        return obj.ephemerides(**ca_kwargs)
    except Exception:
        pass

    # Attempt 2: no id_type (generic search string)
    try:
        obj = Horizons(id=obj_name, location=location_code, epochs=epochs)
        return obj.ephemerides(**ca_kwargs)
    except Exception:
        pass

    # Attempt 3: regex-extracted short ID with three sub-fallbacks
    match = re.search(r"^(\d+[PDCX]|P/\d{4} [A-Z0-9]+|C/\d{4} [A-Z0-9]+|\d+)", obj_name)
    if not match:
        raise RuntimeError(f"All Horizons attempts failed for {obj_name!r}")

    short_id = match.group(1)
    for id_type in ('smallbody', 'designation', None):
        try:
            kw = {"id_type": id_type} if id_type is not None else {}
            obj = Horizons(id=short_id, location=location_code, epochs=epochs, **kw)
            return obj.ephemerides(**ca_kwargs)
        except Exception:
            pass

    raise RuntimeError(f"All Horizons attempts failed for {obj_name!r} (short: {short_id!r})")

def resolve_simbad(obj_name):
    """Resolves an object name using SIMBAD."""
    try:
        # Get ICRS coordinates from SIMBAD
        icrs_coord = SkyCoord.from_name(obj_name)
        
        # Transform to FK5 with current epoch
        t = Time.now()
        fk5_coord = icrs_coord.transform_to(FK5(equinox=t))
        
        custom_simbad = Simbad()
        custom_simbad.TIMEOUT = 10
        result_table = custom_simbad.query_object(obj_name)

        resolved_name = obj_name
        if result_table is not None and 'MAIN_ID' in result_table.colnames:
            main_id = result_table['MAIN_ID'][0]
            resolved_name = main_id.decode('utf-8') if isinstance(main_id, bytes) else str(main_id)
        
        return resolved_name, fk5_coord
    except Exception as e:
        raise RuntimeError(f"SIMBAD lookup failed for {obj_name}: {e}")

def resolve_horizons(obj_name, obs_time_str="2026-02-13 00:30:00", location_code='500'):
    """Resolves a solar system body using JPL Horizons."""
    try:
        obs_time = Time(obs_time_str)
        result = _horizons_query(obj_name, location_code, obs_time.jd)
        ra  = result['RA'][0]  * u.deg
        dec = result['DEC'][0] * u.deg
        return obj_name, SkyCoord(ra=ra, dec=dec, frame='icrs')
    except Exception as e:
        raise RuntimeError(f"JPL Horizons lookup failed for {obj_name}: {e}")

def get_horizons_ephemerides(obj_name, start_time, duration_minutes=240, step_minutes=10, location_code='500'):
    """Queries JPL Horizons for a range of times to get dynamic coordinates."""
    try:
        # Use start/stop/step to avoid URL length issues with explicit lists
        t_start = Time(start_time)
        end_time = start_time + timedelta(minutes=duration_minutes)
        t_end = Time(end_time)
        
        epochs = {
            'start': t_start.datetime.strftime('%Y-%m-%d %H:%M'),
            'stop': t_end.datetime.strftime('%Y-%m-%d %H:%M'),
            'step': f"{step_minutes}m"
        }

        # Query Horizons with epochs dict
        result = _horizons_query(obj_name, location_code, epochs)

        # Convert result table to list of SkyCoords
        coords = [SkyCoord(ra=row['RA']*u.deg, dec=row['DEC']*u.deg, frame='icrs') for row in result]
        return coords

    except Exception as e:
        raise RuntimeError(f"JPL Horizons ephemeris lookup failed: {e}")

def resolve_planet(obj_name, obs_time_str="2026-02-13 00:30:00", location_code='500'):
    """Resolves a major planet using JPL Horizons."""
    try:
        obs_time = Time(obs_time_str)
        # Use id_type='majorbody' for planets. No closest_apparition needed.
        obj = Horizons(id=obj_name, location=location_code, epochs=obs_time.jd, id_type='majorbody')
        result = obj.ephemerides()

        ra = result['RA'][0] * u.deg
        dec = result['DEC'][0] * u.deg
        sky_coord = SkyCoord(ra=ra, dec=dec, frame='icrs')
        
        return obj_name, sky_coord
    except Exception as e:
        raise RuntimeError(f"JPL Horizons planet lookup failed for {obj_name}: {e}")

def get_planet_ephemerides(obj_name, start_time, duration_minutes=240, step_minutes=10, location_code='500'):
    """Queries JPL Horizons for planetary ephemerides."""
    try:
        t_start = Time(start_time)
        end_time = start_time + timedelta(minutes=duration_minutes)
        t_end = Time(end_time)
        
        epochs = {
            'start': t_start.datetime.strftime('%Y-%m-%d %H:%M'),
            'stop': t_end.datetime.strftime('%Y-%m-%d %H:%M'),
            'step': f"{step_minutes}m"
        }

        obj = Horizons(id=obj_name, location=location_code, epochs=epochs, id_type='majorbody')
        result = obj.ephemerides()

        coords = [SkyCoord(ra=row['RA']*u.deg, dec=row['DEC']*u.deg, frame='icrs') for row in result]
        return coords
    except Exception as e:
        raise RuntimeError(f"JPL Horizons planetary ephemeris lookup failed: {e}")