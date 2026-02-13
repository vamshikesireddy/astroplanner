import geocoder
import pytz
import pandas as pd
from datetime import datetime
from timezonefinder import TimezoneFinder
from astropy.coordinates import EarthLocation, SkyCoord, FK5
from astropy import units as u
from astropy.time import Time

# Import from local modules
from resolvers import resolve_simbad, resolve_horizons
from core import compute_trajectory

def get_user_location():
    g = geocoder.ip('me')
    lat, lon = g.latlng
    print(f"Lat: {lat}, Lon: {lon}")
    
    tf = TimezoneFinder()
    local_tz = tf.timezone_at(lat=lat, lng=lon)
    timezone = pytz.timezone(local_tz)
    print(f"Timezone: {local_tz}")
    
    return EarthLocation(lat=lat*u.deg, lon=lon*u.deg), timezone

def main():
    print("Choose mode:")
    print("1 - Manual RA/Dec Input (from coordinates.py)")
    print("2 - Lookup (Star, Galaxy, Planet via SIMBAD/CDS)")
    print("3 - Comet or Asteroid via JPL Horizons")
    mode = input("Enter option 1, 2, or 3: ").strip()

    name = "Unknown"
    sky_coord = None

    if mode == "1":
        try:
            import coordinates as user_config
            name = user_config.name
            # Parse user input
            sky_coord = SkyCoord(user_config.user_ra, user_config.user_dec, frame=FK5, unit=(u.hourangle, u.deg), equinox=Time.now())
            print(f"Loaded {name} from coordinates.py")
        except ImportError:
            print("❌ coordinates.py not found in current directory.")
            return
        except Exception as e:
            print(f"❌ Error parsing coordinates: {e}")
            return

    elif mode == "2":
        obj_name = input("Enter object name (e.g., Vega, M31, Mars): ").strip()
        try:
            name, sky_coord = resolve_simbad(obj_name)
            print(f"Resolved object: {name} at RA: {sky_coord.ra}, Dec: {sky_coord.dec}")
        except Exception as e:
            print(e)
            return

    elif mode == "3":
        obj_name = input("Enter comet or asteroid name (e.g., 1P/Halley): ").strip()
        try:
            print(f"🔭 Using JPL Horizons to resolve '{obj_name}'...")
            name, sky_coord = resolve_horizons(obj_name)
            print(f"Resolved {name} at RA: {sky_coord.ra}, Dec: {sky_coord.dec}")
        except Exception as e:
            print(e)
            return

    else:
        print("❌ Invalid mode. Exiting.")
        return

    # Location and Time
    location, timezone = get_user_location()
    
    # Time Window Setup
    start_local = timezone.localize(datetime(2026, 2, 13, 19, 0, 0))
    
    # Compute
    results = compute_trajectory(sky_coord, location, start_local)
    
    # Output
    df = pd.DataFrame(results)
    cols = ["Local Time", "UTC Time", "LST", "Azimuth (°)", "Altitude (°)", "Direction"]
    print(f"\nTarget: {name}")
    print(df[cols])

if __name__ == "__main__":
    main()
