import streamlit as st
import os
import pandas as pd
import geocoder
import pytz
from datetime import datetime
from timezonefinder import TimezoneFinder
from astropy.coordinates import EarthLocation, SkyCoord, FK5
from astropy import units as u
from astropy.time import Time

try:
    from streamlit_js_eval import get_geolocation
except ImportError:
    get_geolocation = None

try:
    from streamlit_searchbox import st_searchbox
except ImportError:
    st_searchbox = None

# Import from local modules
from resolvers import resolve_simbad, resolve_horizons, get_horizons_ephemerides
from core import compute_trajectory
from scrape import scrape_unistellar_table

st.set_page_config(page_title="Astro Coordinates", page_icon="🔭", layout="wide")

st.title("🔭 Astro Coordinates Planner")
st.markdown("Plan your astrophotography sessions with visibility predictions.")

with st.expander("ℹ️ How to Use"):
    st.markdown("""
    ### 1. Set Location & Time (Sidebar)
    *   **Location:** Search for a city, use Browser GPS, or enter coordinates manually.
    *   **Time:** Set your observation start date and time.
    *   **Duration:** Choose how long you plan to image.

    ### 2. Choose a Target
    Select one of the five modes:
    *   **🌌 Star/Galaxy/Nebula:** Enter a name (e.g., `M42`, `Vega`).
    *   **☄️ Comet:** Select from popular comets or search JPL Horizons.
    *   **🪨 Asteroid:** Select major asteroids or search by name.
    *   **💥 Cosmic Cataclysm:** Live alerts for transient events. **(New: Report & filter false/concluded events)**.
    *   **✍️ Manual:** Enter RA/Dec directly.

    ### 3. Calculate & Analyze
    *   Click **🚀 Calculate Visibility**.
    *   View the **Altitude Chart** to see if the object is high enough.
    *   **Download CSV** for detailed minute-by-minute data.
    """)

# ---------------------------
# SIDEBAR: Location & Time
# ---------------------------
st.sidebar.header("📍 Location & Time")

# 1. Location
# Initialize session state with empty location
if 'lat' not in st.session_state:
    st.session_state.lat = None
if 'lon' not in st.session_state:
    st.session_state.lon = None

def search_address():
    if st.session_state.addr_search:
        try:
            g = geocoder.arcgis(st.session_state.addr_search, timeout=10)
            if g.ok:
                st.session_state.lat = g.latlng[0]
                st.session_state.lon = g.latlng[1]
        except:
            pass

if get_geolocation:
    if st.sidebar.checkbox("📍 Use Browser GPS"):
        loc = get_geolocation()
        if loc:
            st.session_state.lat = loc['coords']['latitude']
            st.session_state.lon = loc['coords']['longitude']
else:
    st.sidebar.info("Install `streamlit-js-eval` for GPS support.")

def search_osm(search_term):
    if not search_term: return []
    try:
        g = geocoder.arcgis(search_term, maxRows=5, timeout=10)
        return [(r.address, r.latlng) for r in g] if g.ok else []
    except:
        return []

if st_searchbox:
    with st.sidebar:
        selected_loc = st_searchbox(
            search_osm,
            key="addr_search_box",
            label="Search Address"
        )
    if selected_loc:
        st.session_state.lat = selected_loc[0]
        st.session_state.lon = selected_loc[1]
else:
    st.sidebar.text_input("Search Address", key="addr_search", on_change=search_address, help="Enter city or address to update coordinates")
    st.sidebar.caption("Install `streamlit-searchbox` for autocomplete.")

lat = st.sidebar.number_input("Latitude", key="lat", format="%.4f")
lon = st.sidebar.number_input("Longitude", key="lon", format="%.4f")

# 2. Timezone
tf = TimezoneFinder()
timezone_str = "UTC"
try:
    if lat is not None and lon is not None:
        timezone_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
except:
    pass
st.sidebar.caption(f"Timezone: {timezone_str}")
local_tz = pytz.timezone(timezone_str)

# Track timezone changes to update time automatically
if 'last_timezone' not in st.session_state:
    st.session_state.last_timezone = timezone_str

# If timezone changed (e.g. user updated location), update the default time to current local time
if st.session_state.last_timezone != timezone_str:
    st.session_state.last_timezone = timezone_str
    now_local = datetime.now(local_tz)
    st.session_state.selected_date = now_local.date()
    st.session_state.selected_time = now_local.time()
    # Update widget keys to reflect changes immediately
    st.session_state['_new_date'] = now_local.date()
    st.session_state['_new_time'] = now_local.time()

# 3. Date & Time
st.sidebar.subheader("🕒 Observation Start")
now = datetime.now(local_tz)

# Initialize session state for date and time
if 'selected_date' not in st.session_state:
    st.session_state['selected_date'] = now.date()
if 'selected_time' not in st.session_state:
    st.session_state['selected_time'] = now.time()

def update_date():
    st.session_state.selected_date = st.session_state._new_date
def update_time():
    st.session_state.selected_time = st.session_state._new_time

# Ensure widget keys are initialized in session state to avoid warnings when value is omitted
if '_new_date' not in st.session_state:
    st.session_state['_new_date'] = st.session_state.selected_date
if '_new_time' not in st.session_state:
    st.session_state['_new_time'] = st.session_state.selected_time

selected_date = st.sidebar.date_input("Date", key='_new_date', on_change=update_date)
selected_time = st.sidebar.time_input("Time", key='_new_time', on_change=update_time)

# Combine to timezone-aware datetime
start_time = datetime.combine(st.session_state.selected_date, st.session_state.selected_time)
start_time = local_tz.localize(start_time)

# 4. Duration
st.sidebar.subheader("⏳ Duration")
duration_options = [60, 120, 180, 240, 300, 360, 480, 600, 720]
duration = st.sidebar.selectbox("Minutes", options=duration_options, index=3) # Default 240

# ---------------------------
# MAIN: Target Selection
# ---------------------------
st.header("1. Choose Target")

target_mode = st.radio(
    "Select Object Type:",
    ["Star/Galaxy/Nebula (SIMBAD)", "Comet (JPL Horizons)", "Asteroid (JPL Horizons)", "Cosmic Cataclysm", "Manual RA/Dec"],
    horizontal=True
)

name = "Unknown"
sky_coord = None
resolved = False


if target_mode == "Star/Galaxy/Nebula (SIMBAD)":
    obj_name = st.text_input("Enter Object Name (e.g., M31, Vega, Pleiades)", value="M42")
    if obj_name:
        try:
            with st.spinner(f"Resolving {obj_name}..."):
                name, sky_coord = resolve_simbad(obj_name)
            st.success(f"✅ Resolved: **{name}** (RA: {sky_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, Dec: {sky_coord.dec.to_string(sep=':', precision=1)})")
            resolved = True
        except Exception as e:
            st.error(f"Could not resolve object: {e}")

elif target_mode == "Comet (JPL Horizons)":
    # Pre-defined list of popular/bright comets
    comet_targets = [
        "235P/LINEAR",
        "24P/Schaumasse",
        "29P/Schwassmann-Wachmann 1",
        "3D/Biela",
        "88P/Howell",
        "C/2022 N2 (PANSTARRS)",
        "C/2022 QE78 (ATLAS)",
        "C/2023 R1 (PANSTARRS)",
        "C/2024 E1 (Wierzchos)",
        "C/2024 J3 (ATLAS)",
        "C/2024 T5 (ATLAS)",
        "C/2025 A6 (Lemmon)",
        "C/2025 J1 (Borisov)",
        "C/2025 L1 (ATLAS)",
        "C/2025 N1 (ATLAS)",
        "C/2025 Q3 (ATLAS)",
        "C/2025 R3 (PANSTARRS)",
        "C/2026 A1 (MAPS)",
        "P/2010 H2 (Vales)",
        "Custom Comet..."
    ]
    
    selected_target = st.selectbox("Select a Comet", comet_targets)
    st.markdown("ℹ️ *Target not listed? Find the exact designation in the [JPL Small-Body Database](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html) and use 'Custom Comet...'*")
    
    if selected_target == "Custom Comet...":
        obj_name = st.text_input("Enter Comet Name (e.g., C/2020 F3)", value="")
    else:
        obj_name = selected_target.split('(')[0].strip()

    if obj_name:
        try:
            with st.spinner(f"Querying JPL Horizons for {obj_name}..."):
                utc_start = start_time.astimezone(pytz.utc)
                name, sky_coord = resolve_horizons(obj_name, obs_time_str=utc_start.strftime('%Y-%m-%d %H:%M:%S'))
            st.success(f"✅ Resolved: **{name}**")
            resolved = True
        except Exception as e:
            st.error(f"Could not resolve object: {e}")

elif target_mode == "Asteroid (JPL Horizons)":
    # Pre-defined list of popular asteroids
    asteroid_targets = [
        "1 Ceres",
        "2 Pallas",
        "3 Juno",
        "4 Vesta",
        "10 Hygiea",
        "16 Psyche",
        "433 Eros",
        "704 Interamnia",
        "Custom Asteroid..."
    ]
    
    selected_target = st.selectbox("Select an Asteroid", asteroid_targets)
    st.markdown("ℹ️ *Target not listed? Find the exact designation in the [JPL Small-Body Database](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html) and use 'Custom Asteroid...'*")
    
    if selected_target == "Custom Asteroid...":
        obj_name = st.text_input("Enter Asteroid Name (e.g., Eros, Psyche)", value="")
    else:
        # Extract just the ID (number) from strings like "4 Vesta"
        obj_name = selected_target.split(' ')[0]

    if obj_name:
        try:
            with st.spinner(f"Querying JPL Horizons for {obj_name}..."):
                # Pass UTC time for ephemeris lookup
                utc_start = start_time.astimezone(pytz.utc)
                name, sky_coord = resolve_horizons(obj_name, obs_time_str=utc_start.strftime('%Y-%m-%d %H:%M:%S'))
            st.success(f"✅ Resolved: **{name}**")
            resolved = True
        except Exception as e:
            st.error(f"Could not resolve object: {e}")

elif target_mode == "Cosmic Cataclysm":
    st.info("Fetching latest alerts from Unistellar (via Selenium scrape)...")
    
    # --- Global Blocklist & Admin Logic ---
    BLOCKLIST_FILE = "blocklist.txt"
    PENDING_FILE = "pending_blocks.txt"

    def get_file_list(filepath):
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                return [line.strip() for line in f if line.strip()]
        return []

    # 1. Report UI (Public)
    with st.expander("🚩 Report False/Invalid Event/Concluded Events"):
        st.caption("Found a false or concluded event? Submit it for review. It will be hidden for everyone once approved.")
        report_name = st.text_input("Event Name to Block")
        if st.button("Submit Report"):
            if report_name:
                # Append to pending file
                with open(PENDING_FILE, "a") as f:
                    f.write(f"{report_name}\n")
                st.success(f"Report for '{report_name}' submitted for review.")
            else:
                st.warning("Please enter a name.")

    # 2. Admin UI (Restricted)
    with st.sidebar:
        st.markdown("---")
        with st.expander("🔐 Admin Review"):
            admin_pass = st.text_input("Admin Password", type="password", key="admin_pass_input")
            
            # Securely retrieve password from Streamlit secrets
            # If not set, returns None, making login impossible (secure default)
            correct_pass = st.secrets.get("ADMIN_PASSWORD")
            
            if correct_pass and admin_pass == correct_pass:
                st.markdown("### Pending Requests")
                pending_reqs = get_file_list(PENDING_FILE)
                if not pending_reqs:
                    st.info("No pending requests.")
                for req in pending_reqs:
                    st.text(req)
                    c1, c2 = st.columns(2)
                    if c1.button("✅ Accept", key=f"acc_{req}"):
                        with open(BLOCKLIST_FILE, "a") as f: f.write(f"{req}\n")
                        # Remove from pending (rewrite file excluding this req)
                        remaining = [r for r in pending_reqs if r != req]
                        with open(PENDING_FILE, "w") as f: f.write("\n".join(remaining) + "\n")
                        st.rerun()
                    if c2.button("❌ Reject", key=f"rej_{req}"):
                        remaining = [r for r in pending_reqs if r != req]
                        with open(PENDING_FILE, "w") as f: f.write("\n".join(remaining) + "\n")
                        st.rerun()

    @st.cache_data(ttl=3600, show_spinner="Scraping data...")
    def get_scraped_data():
        return scrape_unistellar_table()

    df_alerts = get_scraped_data()
    
    if df_alerts is not None and not df_alerts.empty:
        # Try to identify columns dynamically
        df_alerts.columns = df_alerts.columns.str.strip()
        cols = df_alerts.columns.tolist()
        
        # Look for 'Name' (preferred) or 'Target'
        target_col = next((c for c in cols if c.lower() in ['name', 'target', 'object']), None)
        ra_col = next((c for c in cols if c.lower() in ['ra', 'r.a.']), None)
        dec_col = next((c for c in cols if c.lower() in ['dec', 'declination']), None)
        
        if target_col:
            # --- Apply Global Blocklist Filter ---
            blocked_targets = get_file_list(BLOCKLIST_FILE)
            if blocked_targets:
                # Filter out rows where target name contains any blocked string (case-insensitive)
                df_alerts = df_alerts[~df_alerts[target_col].astype(str).apply(lambda x: any(b.lower() in x.lower() for b in blocked_targets))]
            
            targets = df_alerts[target_col].unique()
            obj_name = st.selectbox("Select Target", targets)
            
            if obj_name:
                row = df_alerts[df_alerts[target_col] == obj_name].iloc[0]
                name = obj_name
                
                if ra_col and dec_col:
                    ra_val = row[ra_col]
                    dec_val = row[dec_col]
                    st.caption(f"Coordinates: RA {ra_val}, Dec {dec_val}")
                    
                    try:
                        # Handle potential string formatting issues
                        sky_coord = SkyCoord(str(ra_val), str(dec_val), frame='icrs')
                        resolved = True
                        st.success(f"✅ Resolved: **{name}**")
                    except Exception as e:
                        st.error(f"Error parsing coordinates: {e}")
            
            # Display data
            st.subheader("Available Targets")
            display_df = df_alerts.copy()
            
            # Remove columns that are not useful for target selection
            cols_to_remove_keywords = ['link', 'deeplink', 'exposure', 'cadence', 'gain', 'exp', 'cad']
            actual_cols_to_drop = [
                col for col in display_df.columns 
                if any(keyword in col.lower() for keyword in cols_to_remove_keywords)
            ]
            if actual_cols_to_drop:
                display_df = display_df.drop(columns=actual_cols_to_drop)
            
            st.dataframe(display_df)

            st.download_button(
                label="Download Scraped Data (CSV)",
                data=df_alerts.to_csv(index=False).encode('utf-8'),
                file_name="unistellar_targets.csv",
                mime="text/csv"
            )
        else:
            st.error(f"Could not find 'Name' column. Found: {cols}")
            st.dataframe(df_alerts)
    else:
        st.error("Failed to scrape data. Please check the scraper logs.")

elif target_mode == "Manual RA/Dec":
    col1, col2, col3 = st.columns(3)
    with col1:
        name = st.text_input("Object Name (optional)", value="Custom Target", help="Provide a name for your custom target.")
    with col2:
        ra_input = st.text_input("RA (e.g., 15h59m30s)", value="15h59m30s")
    with col3:
        dec_input = st.text_input("Dec (e.g., 25d55m13s)", value="25d55m13s")
    
    if ra_input and dec_input:
        try:
            sky_coord = SkyCoord(ra_input, dec_input, frame=FK5, unit=(u.hourangle, u.deg))
            st.success(f"✅ Coordinates parsed successfully.")
            resolved = True
        except Exception as e:
            st.error(f"Invalid coordinates format: {e}")

# ---------------------------
# MAIN: Calculation & Output
# ---------------------------
st.header("2. Trajectory Results")

if st.button("🚀 Calculate Visibility", type="primary", disabled=not resolved):
    if lat is None or lon is None:
        st.error("Please enter a valid location (Latitude & Longitude) in the sidebar.")
        st.stop()

    location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
    
    ephem_coords = None
    # For moving objects, fetch precise ephemerides for the duration
    if target_mode in ["Comet (JPL Horizons)", "Asteroid (JPL Horizons)"]:
        with st.spinner("Fetching detailed ephemerides from JPL..."):
            try:
                ephem_coords = get_horizons_ephemerides(obj_name, start_time, duration_minutes=duration, step_minutes=10)
            except Exception as e:
                st.warning(f"Could not fetch detailed ephemerides ({e}). Using fixed coordinates.")

    with st.spinner("Calculating trajectory..."):
        results = compute_trajectory(sky_coord, location, start_time, duration_minutes=duration, ephemeris_coords=ephem_coords)
    
    df = pd.DataFrame(results)
    
    # Metrics
    max_alt = df["Altitude (°)"].max()
    best_time = df.loc[df["Altitude (°)"].idxmax()]["Local Time"]
    constellation = df["Constellation"].iloc[0]
    
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Max Altitude", f"{max_alt}°")
    m2.metric("Best Time", best_time.split(" ")[1])
    m3.metric("Direction at Max", df.loc[df["Altitude (°)"].idxmax()]["Direction"])
    m4.metric("Constellation", constellation)

    # Chart
    st.subheader("Altitude vs Time")
    # Create a simple line chart
    chart_data = df[["Local Time", "Altitude (°)"]].copy()
    chart_data["Local Time"] = pd.to_datetime(chart_data["Local Time"])
    st.line_chart(chart_data.set_index("Local Time"))

    # Data Table
    st.subheader("Detailed Data")
    st.dataframe(df, width='stretch')

    # Sanitize filename
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
    date_str = start_time.strftime('%Y-%m-%d')

    st.download_button(
        label="Download CSV",
        data=df.to_csv(index=False).encode('utf-8'),
        file_name=f"{safe_name}_{date_str}_trajectory.csv",
        mime="text/csv",
    )