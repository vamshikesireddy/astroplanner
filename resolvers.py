from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.time import Time
from astroquery.simbad import Simbad
from astroquery.jplhorizons import Horizons

def resolve_simbad(obj_name):
    """Resolves an object name using SIMBAD."""
    try:
        sky_coord = SkyCoord.from_name(obj_name)
        
        custom_simbad = Simbad()
        custom_simbad.TIMEOUT = 10
        result_table = custom_simbad.query_object(obj_name)

        resolved_name = obj_name
        if result_table is not None and 'MAIN_ID' in result_table.colnames:
            main_id = result_table['MAIN_ID'][0]
            resolved_name = main_id.decode('utf-8') if isinstance(main_id, bytes) else str(main_id)
        
        return resolved_name, sky_coord
    except Exception as e:
        raise RuntimeError(f"SIMBAD lookup failed for {obj_name}: {e}")

def resolve_horizons(obj_name, obs_time_str="2026-02-13 00:30:00", location_code='500'):
    """Resolves a solar system body using JPL Horizons."""
    try:
        obs_time = Time(obs_time_str)
        obj = Horizons(id=obj_name, location=location_code, epochs=obs_time.jd, id_type='smallbody')
        result = obj.ephemerides()

        ra = result['RA'][0] * u.deg
        dec = result['DEC'][0] * u.deg
        sky_coord = SkyCoord(ra=ra, dec=dec, frame='icrs')
        
        return obj_name, sky_coord
    except Exception as e:
        raise RuntimeError(f"JPL Horizons lookup failed for {obj_name}: {e}")