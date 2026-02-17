import re
from astropy.coordinates import SkyCoord, FK5
from astropy import units as u
from astropy.time import Time
from datetime import timedelta
from astroquery.simbad import Simbad
from astroquery.jplhorizons import Horizons

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
        try:
            obj = Horizons(id=obj_name, location=location_code, epochs=obs_time.jd, id_type='smallbody')
            result = obj.ephemerides(closest_apparition=True)
        except Exception:
            try:
                # Fallback 1: try without id_type (allows search strings like "4 Vesta")
                obj = Horizons(id=obj_name, location=location_code, epochs=obs_time.jd)
                result = obj.ephemerides(closest_apparition=True)
            except Exception:
                # Fallback 2: Regex extraction for specific formats (e.g. "235P/LINEAR" -> "235P")
                match = re.search(r"^(\d+[PDCX]|P/\d{4} [A-Z0-9]+|C/\d{4} [A-Z0-9]+|\d+)", obj_name)
                if match:
                    short_id = match.group(1)
                    try:
                        obj = Horizons(id=short_id, location=location_code, epochs=obs_time.jd, id_type='smallbody')
                        result = obj.ephemerides(closest_apparition=True)
                    except Exception:
                        try:
                            obj = Horizons(id=short_id, location=location_code, epochs=obs_time.jd, id_type='designation')
                            result = obj.ephemerides(closest_apparition=True)
                        except Exception:
                            obj = Horizons(id=short_id, location=location_code, epochs=obs_time.jd)
                            result = obj.ephemerides(closest_apparition=True)
                else:
                    raise

        ra = result['RA'][0] * u.deg
        dec = result['DEC'][0] * u.deg
        sky_coord = SkyCoord(ra=ra, dec=dec, frame='icrs')
        
        return obj_name, sky_coord
    except Exception as e:
        raise RuntimeError(f"JPL Horizons lookup failed for {obj_name}: {e}")

def get_horizons_ephemerides(obj_name, start_time, duration_minutes=240, step_minutes=10, location_code='500'):
    """Queries JPL Horizons for a range of times to get dynamic coordinates."""
    try:
        # Generate time steps exactly like core.py to ensure alignment
        steps = int(duration_minutes / step_minutes) + 1
        jd_list = []
        
        for i in range(steps):
            t = start_time + timedelta(minutes=i*step_minutes)
            # astropy Time handles timezone-aware datetimes correctly
            jd_list.append(Time(t).jd)

        # Query Horizons with list of epochs
        try:
            obj = Horizons(id=obj_name, location=location_code, epochs=jd_list, id_type='smallbody')
            result = obj.ephemerides(closest_apparition=True)
        except Exception:
            try:
                # Fallback 1: Search
                obj = Horizons(id=obj_name, location=location_code, epochs=jd_list)
                result = obj.ephemerides(closest_apparition=True)
            except Exception:
                # Fallback 2: Regex
                match = re.search(r"^(\d+[PDCX]|P/\d{4} [A-Z0-9]+|C/\d{4} [A-Z0-9]+|\d+)", obj_name)
                if match:
                    short_id = match.group(1)
                    try:
                        obj = Horizons(id=short_id, location=location_code, epochs=jd_list, id_type='smallbody')
                        result = obj.ephemerides(closest_apparition=True)
                    except Exception:
                        try:
                            obj = Horizons(id=short_id, location=location_code, epochs=jd_list, id_type='designation')
                            result = obj.ephemerides(closest_apparition=True)
                        except Exception:
                            obj = Horizons(id=short_id, location=location_code, epochs=jd_list)
                            result = obj.ephemerides(closest_apparition=True)
                else:
                    raise

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
        steps = int(duration_minutes / step_minutes) + 1
        jd_list = []
        
        for i in range(steps):
            t = start_time + timedelta(minutes=i*step_minutes)
            jd_list.append(Time(t).jd)

        obj = Horizons(id=obj_name, location=location_code, epochs=jd_list, id_type='majorbody')
        result = obj.ephemerides()

        coords = [SkyCoord(ra=row['RA']*u.deg, dec=row['DEC']*u.deg, frame='icrs') for row in result]
        return coords
    except Exception as e:
        raise RuntimeError(f"JPL Horizons planetary ephemeris lookup failed: {e}")