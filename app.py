import streamlit as st
import yaml
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

try:
    from github import Github
except ImportError:
    Github = None

# Import from local modules
from backend.resolvers import resolve_simbad, resolve_horizons, get_horizons_ephemerides, resolve_planet, get_planet_ephemerides
from backend.core import compute_trajectory, calculate_planning_info
from backend.scrape import scrape_unistellar_table

st.set_page_config(page_title="Astro Coordinates", page_icon="🔭", layout="wide", initial_sidebar_state="expanded")

@st.cache_data(ttl=3600, show_spinner="Calculating planetary visibility...")
def get_planet_summary(lat, lon, start_time):
    planet_map = {
        "Mercury": "199", "Venus": "299", "Mars": "499", "Jupiter": "599",
        "Saturn": "699", "Uranus": "799", "Neptune": "899", "Pluto": "999"
    }
    location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
    utc_start = start_time.astimezone(pytz.utc)
    obs_time_str = utc_start.strftime('%Y-%m-%d %H:%M:%S')
    
    data = []
    for p_name, p_id in planet_map.items():
        try:
            _, sky_coord = resolve_planet(p_id, obs_time_str=obs_time_str)
            details = calculate_planning_info(sky_coord, location, start_time)
            
            row = {
                "Name": p_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
            }
            row.update(details)
            data.append(row)
        except Exception:
            continue
    return pd.DataFrame(data)

# --- Hide Streamlit Branding & Toolbar ---
hide_st_style = """
            <style>
            #MainMenu {visibility: visible;}
            footer {visibility: hidden;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

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
    *   **🪐 Planet:** Select a major planet.
    *   **☄️ Comet:** Select from popular comets or search JPL Horizons.
    *   **🪨 Asteroid:** Select major asteroids or search by name.
    *   **💥 Cosmic Cataclysm:** Live alerts for transient events. **(New: Report invalid/cancelled events or suggest priorities)**.
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
            if 'coords' in loc:
                st.session_state.lat = loc['coords']['latitude']
                st.session_state.lon = loc['coords']['longitude']
            else:
                error = loc.get('error')
                if isinstance(error, dict) and error.get('code') == 1:
                    st.sidebar.error("⚠️ Permission Denied. Please allow location access in your browser settings or Type Address to get coordinates.")
                else:
                    st.sidebar.error(f"GPS Error: {error}")
                    st.sidebar.write(loc)
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
if (lat is None or lat == 0.0) and (lon is None or lon == 0.0):
    st.info("📍 **Mobile Users:** Tap the arrow `>` (top-left) to open the sidebar and set your Location!")

st.header("1. Choose Target")

target_mode = st.radio(
    "Select Object Type:",
    ["Star/Galaxy/Nebula (SIMBAD)", "Planet (JPL Horizons)", "Comet (JPL Horizons)", "Asteroid (JPL Horizons)", "Cosmic Cataclysm", "Manual RA/Dec"],
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

elif target_mode == "Planet (JPL Horizons)":
    planet_map = {
        "Mercury": "199",
        "Venus": "299",
        "Mars": "499",
        "Jupiter": "599",
        "Saturn": "699",
        "Uranus": "799",
        "Neptune": "899",
        "Pluto": "999"
    }
    
    if lat is not None and lon is not None:
        df_planets = get_planet_summary(lat, lon, start_time)
        if not df_planets.empty:
            st.caption("Visibility for tonight:")
            cols = ["Name", "Constellation", "Rise", "Transit", "Set", "RA", "Dec", "Status"]
            st.dataframe(df_planets[cols], hide_index=True, width="stretch")
    else:
        st.info("Set location in sidebar to see visibility summary for all planets.")

    selected_target = st.selectbox("Select a Planet", list(planet_map.keys()))
    
    # Use JPL Horizons IDs to avoid ambiguity (e.g. Mercury vs Mercury Barycenter)
    obj_name = planet_map[selected_target]
    
    if obj_name:
        try:
            with st.spinner(f"Querying JPL Horizons for {selected_target}..."):
                utc_start = start_time.astimezone(pytz.utc)
                _, sky_coord = resolve_planet(obj_name, obs_time_str=utc_start.strftime('%Y-%m-%d %H:%M:%S'))
            
            name = selected_target
            st.success(f"✅ Resolved: **{name}**")
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
    status_msg = st.empty()
    status_msg.info("Fetching latest alerts from Unistellar...")
    
    # --- Global Configuration (YAML) ---
    TARGETS_FILE = "targets.yaml"
    PENDING_FILE = "pending_requests.txt"

    def load_targets_config():
        if os.path.exists(TARGETS_FILE):
            with open(TARGETS_FILE, "r") as f:
                return yaml.safe_load(f) or {}
        return {"priorities": {}, "cancelled": [], "too_faint": []}

    def save_targets_config(config):
        # 1. Save locally (for immediate use)
        with open(TARGETS_FILE, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
            
        # 2. Sync to GitHub (for persistence)
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("GITHUB_REPO")
        
        if token and repo_name and Github:
            try:
                g = Github(token)
                repo = g.get_repo(repo_name)
                yaml_str = yaml.dump(config, default_flow_style=False)
                
                try:
                    contents = repo.get_contents(TARGETS_FILE)
                    repo.update_file(contents.path, "Update targets.yaml (Admin)", yaml_str, contents.sha)
                    st.toast("✅ targets.yaml pushed to GitHub")
                except Exception:
                    repo.create_file(TARGETS_FILE, "Create targets.yaml (Admin)", yaml_str)
                    st.toast("✅ targets.yaml created on GitHub")
            except Exception as e:
                st.error(f"GitHub Sync Error: {e}")

    def send_notification(title, body):
        """Creates a GitHub Issue to notify admin of new requests."""
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("GITHUB_REPO")
        
        if token and repo_name and Github:
            try:
                g = Github(token)
                repo = g.get_repo(repo_name)
                # Assign to self (token owner) to ensure visibility
                me = g.get_user()
                repo.create_issue(title=title, body=body, assignee=me.login)
            except Exception as e:
                print(f"Failed to send notification: {e}")

    # 1. Report UI (Public)
    with st.expander("🚩 Report Invalid/Cancelled Event / Suggest Priority"):
        st.caption("Report invalid events or suggest priority changes.")
        
        tab_block, tab_pri = st.tabs(["🚫 Block Target", "⭐ Suggest Priority"])
        
        with tab_block:
            c1, c2 = st.columns([2, 1])
            b_name = c1.text_input("Event Name", key="rep_b_name")
            b_reason = c2.selectbox("Reason", ["Cancelled", "Too Faint"], key="rep_b_reason")
            if st.button("Submit Block Report", key="btn_block"):
                if b_name:
                    with open(PENDING_FILE, "a") as f:
                        f.write(f"{b_name}|{b_reason}\n")
                    
                    send_notification(f"🚫 Block Request: {b_name}", f"**Target:** {b_name}\n**Reason:** {b_reason}\n\n_Submitted via Astro Planner App_")
                    st.success(f"Report for '{b_name}' submitted.")
        
        with tab_pri:
            c1, c2 = st.columns([2, 1])
            p_name = c1.text_input("Event Name", key="rep_p_name")
            p_val = c2.selectbox("New Priority", ["LOW", "MEDIUM", "HIGH", "URGENT", "REMOVE"], key="rep_p_val")
            if st.button("Submit Priority", key="btn_pri"):
                if p_name:
                    with open(PENDING_FILE, "a") as f:
                        f.write(f"{p_name}|Priority: {p_val}\n")
                    
                    send_notification(f"⭐ Priority Request: {p_name}", f"**Target:** {p_name}\n**New Priority:** {p_val}\n\n_Submitted via Astro Planner App_")
                    st.success(f"Priority for '{p_name}' submitted.")

    # Display Active Priority Overrides
    current_config = load_targets_config()
    if current_config.get("priorities"):
        with st.expander("⭐Target Priorities"):
            st.caption("These targets have manually assigned priorities:")
            p_items = list(current_config["priorities"].items())
            p_df = pd.DataFrame(p_items, columns=["Target", "Priority"])
            st.dataframe(p_df, hide_index=True, width="stretch")

    # 2. Admin UI (Restricted)
    with st.sidebar:
        st.markdown("---")
        with st.expander("🔐 Admin Review"):
            admin_pass = st.text_input("Admin Password", type="password", key="admin_pass_input")
            correct_pass = st.secrets.get("ADMIN_PASSWORD")
            
            if correct_pass and admin_pass == correct_pass:
                # --- Pending Requests ---
                st.markdown("### Pending Requests")
                if os.path.exists(PENDING_FILE):
                    with open(PENDING_FILE, "r") as f:
                        lines = [l.strip() for l in f if l.strip()]
                else:
                    lines = []

                if not lines:
                    st.info("No pending requests.")
                
                for i, line in enumerate(lines):
                    parts = line.split('|')
                    if len(parts) != 2: continue
                    r_name, r_reason = parts
                    
                    st.text(f"{r_name} ({r_reason})")
                    c1, c2 = st.columns(2)
                    
                    if c1.button("✅ Accept", key=f"acc_{i}_{r_name}"):
                        config = load_targets_config()
                        
                        if r_reason.startswith("Priority:"):
                            # Handle Priority
                            val = r_reason.split(":")[1].strip()
                            if val == "REMOVE":
                                if "priorities" in config and r_name in config["priorities"]:
                                    del config["priorities"][r_name]
                            else:
                                if "priorities" not in config: config["priorities"] = {}
                                config["priorities"][r_name] = val
                        else:
                            # Handle Block
                            key = "cancelled" if r_reason == "Cancelled" else "too_faint"
                            if key not in config: config[key] = []
                            if r_name not in config[key]:
                                config[key].append(r_name)
                        
                        save_targets_config(config)
                        
                        # Remove from pending
                        remaining = [l for l in lines if l != line]
                        with open(PENDING_FILE, "w") as f: f.write("\n".join(remaining) + "\n")
                        st.rerun()
                        
                    if c2.button("❌ Reject", key=f"rej_{i}_{r_name}"):
                        remaining = [l for l in lines if l != line]
                        with open(PENDING_FILE, "w") as f: f.write("\n".join(remaining) + "\n")
                        st.rerun()
                
                # --- Priority Management ---
                st.markdown("---")
                st.markdown("### Manage Priorities")
                
                # List existing priorities with delete option
                config = load_targets_config()
                if config.get("priorities"):
                    st.caption("Current Priorities:")
                    for t_name, t_pri in list(config["priorities"].items()):
                        pc1, pc2 = st.columns([3, 1])
                        pc1.text(f"{t_name}: {t_pri}")
                        if pc2.button("🗑️", key=f"del_pri_{t_name}"):
                            del config["priorities"][t_name]
                            save_targets_config(config)
                            st.rerun()
                
                st.caption("Add New Manually:")
                p_name = st.text_input("Target Name for Priority")
                p_val = st.selectbox("New Priority", ["LOW", "MEDIUM", "HIGH", "URGENT"])
                if st.button("Update Priority"):
                    if p_name:
                        config = load_targets_config()
                        if "priorities" not in config: config["priorities"] = {}
                        config["priorities"][p_name] = p_val
                        save_targets_config(config)
                        st.success(f"Set {p_name} to {p_val}")

    @st.cache_data(ttl=3600, show_spinner="Scraping data...")
    def get_scraped_data():
        return scrape_unistellar_table()

    # Check location first
    if lat is None or lon is None:
        status_msg.empty()
        st.warning("⚠️ Please set your **Latitude** and **Longitude** in the sidebar first. We need this to calculate Rise/Set times for the targets.")
        df_alerts = None
    else:
        df_alerts = get_scraped_data()
        status_msg.empty()
    
    if df_alerts is not None and not df_alerts.empty:
        # Try to identify columns dynamically
        df_alerts.columns = df_alerts.columns.str.strip()
        cols = df_alerts.columns.tolist()
        
        # Look for 'Name' (preferred) or 'Target'
        target_col = next((c for c in cols if c.lower() in ['name', 'target', 'object']), None)
        ra_col = next((c for c in cols if c.lower() in ['ra', 'r.a.']), None)
        dec_col = next((c for c in cols if c.lower() in ['dec', 'declination']), None)
        
        if target_col:
            # --- Apply Configuration (Blocklist & Priorities) ---
            config = load_targets_config()
            
            # 1. Blocking
            blocked_targets = config.get("cancelled", []) + config.get("too_faint", [])
            if blocked_targets:
                # Filter out rows where target name contains any blocked string (case-insensitive)
                df_alerts = df_alerts[~df_alerts[target_col].astype(str).apply(lambda x: any(b.lower() in x.lower() for b in blocked_targets))]
            
            # 2. Priorities
            # Find Priority column (e.g., 'Pri', 'Priority')
            pri_col = next((c for c in cols if c.lower().startswith('pri')), None)
            if pri_col and "priorities" in config:
                for p_name, p_val in config["priorities"].items():
                    # Update rows where target name contains the priority key
                    mask = df_alerts[target_col].astype(str).apply(lambda x: p_name.lower() in x.lower())
                    if mask.any():
                        df_alerts.loc[mask, pri_col] = p_val

            # --- Calculate Planning Info for Table ---
            st.caption(f"Calculating visibility for {len(df_alerts)} targets based on your location...")
            
            planning_data = []
            location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
            
            # Create a progress bar if there are many targets
            progress_bar = st.progress(0)
            total_rows = len(df_alerts)

            for idx, row in df_alerts.iterrows():
                # Update progress
                if idx % 5 == 0: progress_bar.progress(min(idx / total_rows, 1.0))
                
                try:
                    # Parse coordinates
                    ra_val = row[ra_col]
                    dec_val = row[dec_col]
                    # Handle potential string formatting issues
                    sc = SkyCoord(str(ra_val), str(dec_val), frame='icrs')
                    
                    # Calculate details
                    details = calculate_planning_info(sc, location, start_time)
                    
                    # Merge row data with details
                    row_dict = row.to_dict()
                    row_dict.update(details)
                    planning_data.append(row_dict)
                except Exception:
                    # If coord parsing fails, just keep original row
                    planning_data.append(row.to_dict())
            
            progress_bar.empty()
            
            # Create new enriched DataFrame
            df_display = pd.DataFrame(planning_data)
            
            # Add 'sec' to Duration column
            dur_col = next((c for c in df_display.columns if 'dur' in c.lower()), None)
            if dur_col:
                df_display[dur_col] = df_display[dur_col].astype(str) + " sec"
            
            # Reorder columns to put Name and Planning info first
            priority_cols = [target_col, 'Constellation', 'Rise', 'Transit', 'Set']
            
            # Ensure Priority is visible and upfront
            if pri_col and pri_col in df_display.columns:
                priority_cols.insert(1, pri_col)

            other_cols = [c for c in df_display.columns if c not in priority_cols]
            df_display = df_display[priority_cols + other_cols]

            targets = df_display[target_col].unique()
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
            
            # Filter columns for display
            cols_to_remove_keywords = ['link', 'deeplink', 'exposure', 'cadence', 'gain', 'exp', 'cad']
            actual_cols_to_drop = [
                col for col in df_display.columns 
                if any(keyword in col.lower() for keyword in cols_to_remove_keywords)
            ]
            
            final_table = df_display.drop(columns=actual_cols_to_drop, errors='ignore')
            
            st.dataframe(final_table, width="stretch")

            st.download_button(
                label="Download Scraped Data (CSV)",
                data=df_alerts.to_csv(index=False).encode('utf-8'),
                file_name="unistellar_targets.csv",
                mime="text/csv"
            )
        else:
            st.error(f"Could not find 'Name' column. Found: {cols}")
            st.dataframe(df_alerts, width="stretch")
    elif lat is not None and lon is not None:
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
    elif target_mode == "Planet (JPL Horizons)":
        with st.spinner("Fetching planetary ephemerides from JPL..."):
            try:
                ephem_coords = get_planet_ephemerides(obj_name, start_time, duration_minutes=duration, step_minutes=10)
            except Exception as e:
                st.warning(f"Could not fetch planetary ephemerides ({e}). Using fixed coordinates.")

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