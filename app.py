import streamlit as st
import warnings
import yaml
import os
import math
import pandas as pd
import geocoder
import pytz
from datetime import datetime, timedelta
from timezonefinder import TimezoneFinder
import altair as alt
from astropy.coordinates import EarthLocation, SkyCoord, FK5, AltAz
try:
    from astropy.coordinates import get_moon, get_sun
except ImportError:
    from astropy.coordinates import get_body
    def get_moon(time, location=None, ephemeris=None): return get_body("moon", time, location, ephemeris=ephemeris)
    def get_sun(time): return get_body("sun", time)
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
from backend.core import compute_trajectory, calculate_planning_info, azimuth_to_compass
from backend.scrape import scrape_unistellar_table, scrape_unistellar_priority_comets, scrape_unistellar_priority_asteroids

# Suppress Astropy warnings about coordinate frame transformations (Geocentric vs Topocentric)
warnings.filterwarnings("ignore", message=".*transforming other coordinates.*")

st.set_page_config(page_title="Astro Coordinates", page_icon="🔭", layout="wide", initial_sidebar_state="expanded")

def get_moon_status(illumination, separation):
    if illumination < 15:
        return "🌑 Dark Sky"
    elif separation < 30:
        return "⛔ Avoid"
    elif separation < 60:
        return "⚠️ Caution"
    else:
        return "✅ Safe"

@st.cache_data(ttl=3600, show_spinner="Calculating planetary visibility...")
def get_planet_summary(lat, lon, start_time):
    planet_map = {
        "Mercury": "199", "Venus": "299", "Mars": "499", "Jupiter": "599",
        "Saturn": "699", "Uranus": "799", "Neptune": "899", "Pluto": "999"
    }
    location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
    utc_start = start_time.astimezone(pytz.utc)
    obs_time_str = utc_start.strftime('%Y-%m-%d %H:%M:%S')
    
    # Calculate Moon info
    t_moon = Time(start_time)
    try:
        moon_loc = get_moon(t_moon, location)
        sun_loc = get_sun(t_moon)
        elongation = sun_loc.separation(moon_loc)
        moon_illum = float(0.5 * (1 - math.cos(elongation.rad))) * 100
    except:
        moon_loc = None
        moon_illum = 0
    
    data = []
    for p_name, p_id in planet_map.items():
        try:
            _, sky_coord = resolve_planet(p_id, obs_time_str=obs_time_str)
            details = calculate_planning_info(sky_coord, location, start_time)
            
            moon_sep = 0.0
            if moon_loc:
                moon_sep = sky_coord.separation(moon_loc).degree
            
            row = {
                "Name": p_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": sky_coord.dec.degree,
                "Moon Sep (°)": round(moon_sep, 1) if moon_loc else 0,
                "Moon Status": get_moon_status(moon_illum, moon_sep) if moon_loc else "",
            }
            row.update(details)
            data.append(row)
        except Exception:
            continue
    return pd.DataFrame(data)

def plot_visibility_timeline(df, obs_start=None, obs_end=None):
    """Generates a Gantt-style chart showing Rise to Set times.

    obs_start / obs_end: naive local datetimes for the observation window overlay.
    When provided, a shaded region + dashed start/end lines are drawn on the chart.
    """
    # Filter for objects with valid rise/set times
    chart_data = df.dropna(subset=['_rise_datetime', '_set_datetime']).copy()

    if chart_data.empty:
        return

    # Convert to naive datetime to display "Wall Clock" time on the chart
    chart_data['_rise_naive'] = chart_data['_rise_datetime'].apply(lambda x: x.replace(tzinfo=None) if pd.notnull(x) else None)
    chart_data['_set_naive'] = chart_data['_set_datetime'].apply(lambda x: x.replace(tzinfo=None) if pd.notnull(x) else None)
    if '_transit_datetime' in chart_data.columns:
        chart_data['_transit_naive'] = chart_data['_transit_datetime'].apply(lambda x: x.replace(tzinfo=None) if pd.notnull(x) else None)
        chart_data['transit_time_label'] = chart_data['_transit_naive'].apply(
            lambda x: x.strftime('%H:%M') if pd.notnull(x) else ''
        )
    else:
        chart_data['_transit_naive'] = None
        chart_data['transit_time_label'] = ''

    # Clamp "Always Up" (circumpolar) bars to the visible chart window so they
    # don't stretch the x-axis to a full 24 hours beyond other objects.
    # If an obs_window is provided, always-up bars span that window exactly.
    always_up_mask = chart_data['Status'].str.contains('Always Up', na=False)
    non_always_up = chart_data[~always_up_mask]
    # Always anchor "Always Up" bars to the full data range of other objects.
    # The obs window overlay is a separate visual layer and must not shrink these bars.
    if not non_always_up.empty:
        x_min = non_always_up['_rise_naive'].min()
        x_max = non_always_up['_set_naive'].max()
    elif obs_start is not None and obs_end is not None:
        # All objects are circumpolar — fall back to the observation window
        x_min = obs_start
        x_max = obs_end
    else:
        x_min = chart_data['_rise_naive'].min()
        x_max = x_min + pd.Timedelta(hours=12)
    if always_up_mask.any():
        chart_data.loc[always_up_mask, '_rise_naive'] = x_min
        chart_data.loc[always_up_mask, '_set_naive'] = x_max

    # Create label columns: Show "Always Up" for circumpolar objects, otherwise show time
    chart_data['rise_label'] = chart_data.apply(lambda x: "Always Up" if "Always Up" in str(x['Status']) else x['_rise_naive'].strftime('%m-%d %H:%M'), axis=1)
    chart_data['set_label'] = chart_data.apply(lambda x: "" if "Always Up" in str(x['Status']) else x['_set_naive'].strftime('%m-%d %H:%M'), axis=1)

    # Sort Toggle
    sort_option = st.radio(
        "Sort Graph By:",
        ["Default", "Earliest Rise", "Earliest Set"],
        horizontal=True,
        label_visibility="collapsed"
    )

    if sort_option == "Earliest Rise":
        sort_arg = alt.EncodingSortField(field='_rise_naive', order='ascending')
    elif sort_option == "Earliest Set":
        sort_arg = alt.EncodingSortField(field='_set_naive', order='ascending')
    else:
        sort_arg = list(chart_data['Name'])

    # Dynamic height: Ensure minimum height to prevent clipping of axis/title
    row_height = 60
    chart_height = max(len(chart_data) * row_height, 250)

    # Base Chart
    base = alt.Chart(chart_data).encode(
        y=alt.Y('Name', sort=sort_arg, title=None, axis=alt.Axis(labelOverlap=False, labelLimit=300)),
        tooltip=['Name', 'Rise', 'Transit', 'Set', 'Constellation', 'Status']
    )

    # Bars
    bars = base.mark_bar(cornerRadius=3, height=30).encode(
        x=alt.X('_rise_naive', title='Local Time', axis=alt.Axis(format='%m-%d %H:%M', orient='top')),
        x2='_set_naive',
        color=alt.Color('Name', legend=None)
    )

    # Text Labels (Rise & Set times on the bars)
    text_rise = base.mark_text(align='left', baseline='middle', dx=5, color='white').encode(
        x='_rise_naive', text=alt.Text('rise_label')
    )
    text_set = base.mark_text(align='right', baseline='middle', dx=-5, color='white').encode(
        x='_set_naive', text=alt.Text('set_label')
    )

    # Transit notch: white tick mark + time label displayed above the bar (no hover needed)
    transit_data = chart_data.dropna(subset=['_transit_naive'])
    transit_layers = []
    if not transit_data.empty:
        transit_layers.append(alt.Chart(transit_data).mark_tick(
            color='white', thickness=2, size=28, opacity=0.9
        ).encode(
            x=alt.X('_transit_naive:T'),
            y=alt.Y('Name:N', sort=sort_arg),
            tooltip=[alt.Tooltip('Name'), alt.Tooltip('Transit', title='Transit')]
        ))
        transit_layers.append(alt.Chart(transit_data).mark_text(
            color='white', fontSize=9, dy=-20, align='center', fontWeight='bold'
        ).encode(
            x=alt.X('_transit_naive:T'),
            y=alt.Y('Name:N', sort=sort_arg),
            text=alt.Text('transit_time_label:N')
        ))

    # Observation window overlay: shaded rect + dashed start/end lines
    obs_layers = []
    obs_caption = ""
    if obs_start is not None and obs_end is not None:
        # Pre-format labels as strings so Altair shows HH:MM, not just the date
        obs_df = pd.DataFrame([{
            "obs_start": obs_start,
            "obs_end": obs_end,
            "start_tip": f"Obs Start: {obs_start.strftime('%m-%d %H:%M')}",
            "end_tip": f"Obs End:   {obs_end.strftime('%m-%d %H:%M')}",
        }])
        obs_layers.append(
            alt.Chart(obs_df).mark_rect(opacity=0.07, color='#ffff66').encode(
                x=alt.X('obs_start:T'), x2=alt.X2('obs_end:T'),
                tooltip=[alt.Tooltip('start_tip:N', title=''), alt.Tooltip('end_tip:N', title='')]
            )
        )
        obs_layers.append(
            alt.Chart(obs_df).mark_rule(color='#00e676', strokeDash=[6, 4], strokeWidth=2, opacity=0.9).encode(
                x=alt.X('obs_start:T'),
                tooltip=alt.Tooltip('start_tip:N', title='')
            )
        )
        obs_layers.append(
            alt.Chart(obs_df).mark_rule(color='#ff5252', strokeDash=[6, 4], strokeWidth=2, opacity=0.9).encode(
                x=alt.X('obs_end:T'),
                tooltip=alt.Tooltip('end_tip:N', title='')
            )
        )
        caption_parts = [
            f"🟩 **{obs_start.strftime('%H:%M')}** = obs start",
            f"🟥 **{obs_end.strftime('%H:%M')}** = obs end",
        ]
        if transit_layers:
            caption_parts.append("⬜ white tick = transit")
        caption_parts.append("*(lines update automatically with sidebar settings)*")
        obs_caption = " &nbsp;|&nbsp; ".join(caption_parts)
    else:
        obs_caption = "⬜ white tick = transit" if transit_layers else ""

    # Compose layers: obs_rect first (behind bars), rules last (on top)
    title_str = "Visibility Window (Rise → Set)" + (" — white tick = transit" if transit_layers else "")
    layers = obs_layers[:1] + [bars, text_rise, text_set] + transit_layers + obs_layers[1:]
    chart = alt.layer(*layers).properties(title=title_str, height=chart_height)

    if len(chart_data) > 10:
        with st.container(height=500):
            st.altair_chart(chart, width='stretch')
    else:
        st.altair_chart(chart, width='stretch')

    if obs_caption:
        st.caption(obs_caption)


def _send_github_notification(title, body):
    """Creates a GitHub Issue to notify admin. Reusable across all sections."""
    token = st.secrets.get("GITHUB_TOKEN")
    repo_name = st.secrets.get("GITHUB_REPO")
    if token and repo_name and Github:
        try:
            g = Github(token)
            repo = g.get_repo(repo_name)
            me = g.get_user()
            repo.create_issue(title=title, body=body, assignee=me.login)
        except Exception as e:
            print(f"Failed to send notification: {e}")


COMETS_FILE = "comets.yaml"
COMET_PENDING_FILE = "comet_pending_requests.txt"

# Aliases for comets that appear under alternate designations on external pages
COMET_ALIASES = {
    "3I/ATLAS": "C/2025 N1 (ATLAS)",
}

# SPK-ID overrides: comets that must be queried by their JPL SPK-ID (not designation)
COMET_SPK_IDS = {
    "240P": "90001202",    # 240P/NEAT Fragment A (primary body)
    "240P-B": "90001203",  # 240P/NEAT Fragment B
}


def _resolve_comet_alias(name):
    """Returns canonical name (from COMET_ALIASES) and uppercases for comparison."""
    return COMET_ALIASES.get(name, name).upper()


def _get_comet_jpl_id(name):
    """Return the correct JPL Horizons query ID for a comet name.
    SPK-ID overrides take priority; otherwise strip parenthetical suffix."""
    if name in COMET_SPK_IDS:
        return COMET_SPK_IDS[name]
    return name.split('(')[0].strip()


def load_comets_config():
    if os.path.exists(COMETS_FILE):
        with open(COMETS_FILE, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    data.setdefault("comets", [])
    data.setdefault("unistellar_priority", [])
    data.setdefault("priorities", {})
    data.setdefault("cancelled", [])
    return data


def save_comets_config(config):
    with open(COMETS_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    token = st.secrets.get("GITHUB_TOKEN")
    repo_name = st.secrets.get("GITHUB_REPO")
    if token and repo_name and Github:
        try:
            g = Github(token)
            repo = g.get_repo(repo_name)
            yaml_str = yaml.dump(config, default_flow_style=False)
            try:
                contents = repo.get_contents(COMETS_FILE)
                repo.update_file(contents.path, "Update comets.yaml (Admin)", yaml_str, contents.sha)
                st.toast("✅ comets.yaml pushed to GitHub")
            except Exception:
                repo.create_file(COMETS_FILE, "Create comets.yaml (Admin)", yaml_str)
                st.toast("✅ comets.yaml created on GitHub")
        except Exception as e:
            st.error(f"GitHub Sync Error: {e}")


@st.cache_data(ttl=3600, show_spinner="Calculating comet visibility...")
def get_comet_summary(lat, lon, start_time, comet_tuple):
    """Batch-calculate rise/set/moon info for all comets in the list."""
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    utc_start = start_time.astimezone(pytz.utc)
    obs_time_str = utc_start.strftime('%Y-%m-%d %H:%M:%S')
    t_moon = Time(start_time)
    try:
        moon_loc_inner = get_moon(t_moon, location)
        sun_loc_inner = get_sun(t_moon)
        elongation = sun_loc_inner.separation(moon_loc_inner)
        moon_illum_inner = float(0.5 * (1 - math.cos(elongation.rad))) * 100
    except Exception:
        moon_loc_inner = None
        moon_illum_inner = 0
    data = []
    for comet_name in comet_tuple:
        jpl_id = _get_comet_jpl_id(comet_name)
        try:
            _, sky_coord = resolve_horizons(jpl_id, obs_time_str=obs_time_str)
            details = calculate_planning_info(sky_coord, location, start_time)
            moon_sep = sky_coord.separation(moon_loc_inner).degree if moon_loc_inner else 0.0
            row = {
                "Name": comet_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": sky_coord.dec.degree,
                "Moon Sep (°)": round(moon_sep, 1),
                "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
            }
            row.update(details)
            data.append(row)
        except Exception:
            continue
    return pd.DataFrame(data)


@st.cache_data(ttl=86400, show_spinner=False)
def get_unistellar_scraped_comets():
    """Fetches the current priority comet list from the Unistellar missions page (cached 24h)."""
    try:
        return scrape_unistellar_priority_comets()
    except Exception:
        return []


ASTEROIDS_FILE = "asteroids.yaml"
ASTEROID_PENDING_FILE = "asteroid_pending_requests.txt"

ASTEROID_ALIASES = {
    "Apophis": "99942 Apophis",
    "Bennu": "101955 Bennu",
    "Ryugu": "162173 Ryugu",
}


def _resolve_asteroid_alias(name):
    return ASTEROID_ALIASES.get(name, name).upper()


def _asteroid_priority_name(entry):
    return entry["name"] if isinstance(entry, dict) else entry


def _asteroid_jpl_id(name):
    """Return the correct JPL Horizons ID for an asteroid name.
    - Provisional designations (e.g. '2001 FD58', '2024 YR4') → use full string.
    - Numbered named asteroids (e.g. '433 Eros') → use just the number.
    """
    import re as _re
    if name and _re.match(r'^\d{4}\s+[A-Z]{1,2}\d', name):
        return name  # Provisional: year + letter-number combo
    if name and name[0].isdigit():
        return name.split(' ')[0]  # Numbered: "433 Eros" → "433"
    return name


def load_asteroids_config():
    if os.path.exists(ASTEROIDS_FILE):
        with open(ASTEROIDS_FILE, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    data.setdefault("asteroids", [])
    data.setdefault("unistellar_priority", [])
    data.setdefault("priorities", {})
    data.setdefault("cancelled", [])
    return data


def save_asteroids_config(config):
    with open(ASTEROIDS_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    token = st.secrets.get("GITHUB_TOKEN")
    repo_name = st.secrets.get("GITHUB_REPO")
    if token and repo_name and Github:
        try:
            g = Github(token)
            repo = g.get_repo(repo_name)
            yaml_str = yaml.dump(config, default_flow_style=False)
            try:
                contents = repo.get_contents(ASTEROIDS_FILE)
                repo.update_file(contents.path, "Update asteroids.yaml (Admin)", yaml_str, contents.sha)
                st.toast("✅ asteroids.yaml pushed to GitHub")
            except Exception:
                repo.create_file(ASTEROIDS_FILE, "Create asteroids.yaml (Admin)", yaml_str)
                st.toast("✅ asteroids.yaml created on GitHub")
        except Exception as e:
            st.error(f"GitHub Sync Error: {e}")


@st.cache_data(ttl=3600, show_spinner="Calculating asteroid visibility...")
def get_asteroid_summary(lat, lon, start_time, asteroid_tuple):
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    utc_start = start_time.astimezone(pytz.utc)
    obs_time_str = utc_start.strftime('%Y-%m-%d %H:%M:%S')
    t_moon = Time(start_time)
    try:
        moon_loc_inner = get_moon(t_moon, location)
        sun_loc_inner = get_sun(t_moon)
        elongation = sun_loc_inner.separation(moon_loc_inner)
        moon_illum_inner = float(0.5 * (1 - math.cos(elongation.rad))) * 100
    except Exception:
        moon_loc_inner = None
        moon_illum_inner = 0
    data = []
    for asteroid_name in asteroid_tuple:
        jpl_id = _asteroid_jpl_id(asteroid_name)
        try:
            _, sky_coord = resolve_horizons(jpl_id, obs_time_str=obs_time_str)
            details = calculate_planning_info(sky_coord, location, start_time)
            moon_sep = sky_coord.separation(moon_loc_inner).degree if moon_loc_inner else 0.0
            row = {
                "Name": asteroid_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": sky_coord.dec.degree,
                "Moon Sep (°)": round(moon_sep, 1),
                "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
            }
            row.update(details)
            data.append(row)
        except Exception:
            continue
    return pd.DataFrame(data)


@st.cache_data(ttl=86400, show_spinner=False)
def get_unistellar_scraped_asteroids():
    """Fetches the current priority asteroid list from the Unistellar planetary defense page (cached 24h)."""
    try:
        return scrape_unistellar_priority_asteroids()
    except Exception:
        return []


DSO_FILE = "dso_targets.yaml"


def load_dso_config():
    """Load curated DSO catalog (Messier, Bright Stars, Astrophotography Favorites) from YAML."""
    if os.path.exists(DSO_FILE):
        with open(DSO_FILE, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    data.setdefault("messier", [])
    data.setdefault("bright_stars", [])
    data.setdefault("astrophotography_favorites", [])
    return data


@st.cache_data(ttl=3600, show_spinner="Calculating DSO visibility...")
def get_dso_summary(lat, lon, start_time, dso_tuple):
    """Batch-calculate rise/set/moon info for all DSOs using pre-stored coordinates.
    dso_tuple: tuple of (name, ra_deg, dec_deg, obj_type, magnitude, common_name)
    """
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    t_moon = Time(start_time)
    try:
        moon_loc_inner = get_moon(t_moon, location)
        sun_loc_inner = get_sun(t_moon)
        elongation = sun_loc_inner.separation(moon_loc_inner)
        moon_illum_inner = float(0.5 * (1 - math.cos(elongation.rad))) * 100
    except Exception:
        moon_loc_inner = None
        moon_illum_inner = 0
    data = []
    for entry in dso_tuple:
        d_name, ra_deg, dec_deg, obj_type, magnitude, common_name = entry
        try:
            sky_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame='icrs')
            details = calculate_planning_info(sky_coord, location, start_time)
            moon_sep = sky_coord.separation(moon_loc_inner).degree if moon_loc_inner else 0.0
            row = {
                "Name": d_name,
                "Common Name": common_name,
                "Type": obj_type,
                "Magnitude": magnitude,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": dec_deg,
                "Moon Sep (°)": round(moon_sep, 1),
                "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
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
            /* Reduce metric font size */
            [data-testid="stMetricValue"] {
                font-size: 1.25rem !important;
            }
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

st.title("🔭 Astro Coordinates Planner")
st.markdown("Plan your astrophotography sessions with visibility predictions.")

with st.expander("ℹ️ How to Use"):
    st.markdown("""
    ### 1. Set Location, Time & Filters (Sidebar)
    *   **Location:** Search for a city, use Browser GPS, or enter coordinates manually.
    *   **Time:** Set your observation start date and time.
    *   **Duration:** Choose how long you plan to image.
    *   **Observational Filters:** Set Altitude range (Min/Max), Azimuth, and Moon Separation to filter targets.

    ### 2. Choose a Target
    Select one of the six modes:
    *   **🌌 Star/Galaxy/Nebula:** Browse the full Messier catalog, Bright Stars, or Astrophotography Favorites with batch visibility (Observable/Unobservable tabs + Gantt chart). Filter by object type. Select any target for a full trajectory, or use 'Custom Object...' to search SIMBAD for any object by name.
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

_GPS_ERROR_MESSAGES = {
    1: ("Location access was denied by the browser.",
        "Go to your browser / system Settings → Privacy → Location and allow this site, then reload."),
    2: ("Your device could not determine its location.",
        "This can happen in Safari when Location Services are off (Settings → Privacy → Location Services), "
        "or when the page is served over HTTP instead of HTTPS. "
        "Try enabling Location Services, then reload — or enter your city/address in the search box above instead."),
    3: ("Location request timed out.",
        "The browser took too long to get a GPS fix. "
        "Try again, or enter your city/address in the search box above instead."),
}

if get_geolocation:
    if st.sidebar.checkbox("📍 Use Browser GPS"):
        loc = get_geolocation()
        if loc:
            if 'coords' in loc:
                st.session_state.lat = loc['coords']['latitude']
                st.session_state.lon = loc['coords']['longitude']
            else:
                error = loc.get('error') or {}
                code = error.get('code') if isinstance(error, dict) else None
                title, hint = _GPS_ERROR_MESSAGES.get(
                    code,
                    ("GPS is unavailable.", "Try entering your city or coordinates manually using the fields above.")
                )
                st.sidebar.warning(f"📍 **{title}**\n\n{hint}")
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
    if now_local.hour >= 18:
        st.session_state.selected_time = now_local.time()
    else:
        st.session_state.selected_time = now_local.replace(hour=18, minute=0, second=0, microsecond=0).time()

    # Update widget keys to reflect changes immediately
    st.session_state['_new_date'] = st.session_state.selected_date
    st.session_state['_new_time'] = st.session_state.selected_time

# 3. Date & Time
st.sidebar.subheader("🕒 Observation Start")
now = datetime.now(local_tz)

# Initialize session state for date and time
if 'selected_date' not in st.session_state:
    st.session_state['selected_date'] = now.date()
if 'selected_time' not in st.session_state:
    if now.hour >= 18:
        st.session_state['selected_time'] = now.time()
    else:
        st.session_state['selected_time'] = now.replace(hour=18, minute=0, second=0, microsecond=0).time()

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
st.sidebar.caption("Length of your imaging session starting from the time above.")

_duration_options_min = [60, 120, 180, 240, 300, 360, 480, 600, 720, 840, 960, 1080, 1200, 1320, 1440]

# Persist selected index so it survives a format toggle without resetting the value
if 'dur_idx' not in st.session_state:
    st.session_state.dur_idx = 8  # default: 720 min = 12 hrs

_dur_fmt = st.sidebar.radio("Display as", ["hrs", "min"], horizontal=True, key="dur_fmt")
if _dur_fmt == "hrs":
    _dur_labels = [f"{m // 60} hr" if m // 60 == 1 else f"{m // 60} hrs" for m in _duration_options_min]
else:
    _dur_labels = [f"{m} min" for m in _duration_options_min]

_sel_label = st.sidebar.selectbox("Session length", options=_dur_labels,
                                   index=st.session_state.dur_idx, label_visibility="collapsed")
_sel_idx = _dur_labels.index(_sel_label)
st.session_state.dur_idx = _sel_idx
duration = _duration_options_min[_sel_idx]
show_obs_window = st.sidebar.checkbox("Show observation window on charts", value=True, key="show_obs_window", help="Draws a shaded region and start/end lines on all Gantt charts matching your selected observation time and duration.")

# Pre-compute naive datetimes for the observation window overlay (used in plot_visibility_timeline)
obs_start_naive = start_time.replace(tzinfo=None)
obs_end_naive = (start_time + timedelta(minutes=duration)).replace(tzinfo=None)

# 5. Observational Filters
st.sidebar.subheader("🔭 Observational Filters")
st.sidebar.caption("Applies to lists and visibility warnings.")
alt_range = st.sidebar.slider("Altitude Window (°)", 0, 90, (20, 90), help="Target must be within this altitude range (Min to Max).")
min_alt, max_alt = alt_range
az_range = st.sidebar.slider("Azimuth Window (°)", 0, 360, (0, 360), help="Target must be within this compass direction (0=N, 90=E, 180=S, 270=W).")
dec_range = st.sidebar.slider("Declination Window (°)", -90, 90, (-90, 90), help="Filter targets by declination. Set a range to exclude objects too far north or south for your site.")
min_dec, max_dec = dec_range
min_moon_sep = st.sidebar.slider("Min Moon Separation Filter (°)", 0, 180, 0, help="Optional: Hide targets closer than this to the Moon. Default 0 shows all.")
st.sidebar.markdown("""
<small>
<b>Why this matters:</b> Moonlight washes out faint details.<br>
• <b>< 30°</b>: High risk (avoid for galaxies/nebulae).<br>
• <b>30°-60°</b>: Okay for clusters or narrowband.<br>
• <b>> 60°</b>: Ideal dark skies.
</small>
""", unsafe_allow_html=True)

# Calculate Moon Info
moon_loc = None
if lat is not None and lon is not None:
    try:
        location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
        t_moon = Time(start_time)
        moon_loc = get_moon(t_moon, location)
        sun_loc = get_sun(t_moon)
        elongation = sun_loc.separation(moon_loc)
        moon_illum = float(0.5 * (1 - math.cos(elongation.rad))) * 100
        
        moon_altaz = moon_loc.transform_to(AltAz(obstime=t_moon, location=location))
        moon_alt = moon_altaz.alt.degree
        moon_az_deg = moon_altaz.az.degree
        moon_direction = azimuth_to_compass(moon_az_deg)

        st.sidebar.markdown("---")
        st.sidebar.markdown(f"""
        **🌑 Moon Status:**
        *   Illumination: **{moon_illum:.0f}%**
        *   Altitude: **{moon_alt:.0f}°**
        *   Direction: **{moon_direction}** ({moon_az_deg:.0f}°)
        """)
        st.sidebar.markdown("""
        <small>
        <b>Legend:</b><br>
        🌑 <b>Dark Sky</b>: Moon < 15% illum.<br>
        ⛔ <b>Avoid</b>: Moon > 15% & Sep < 30°.<br>
        ⚠️ <b>Caution</b>: Moon > 15% & Sep 30°-60°.<br>
        ✅ <b>Safe</b>: Moon > 15% & Sep > 60°.
        </small>
        """, unsafe_allow_html=True)
    except Exception:
        pass

# Feedback sidebar
st.sidebar.markdown("---")
with st.sidebar.expander("💬 Feedback / Feature Request"):
    fb_title = st.text_input("Short summary", key="fb_title", placeholder="e.g. Add NGC catalog support")
    fb_body = st.text_area("Details (optional)", key="fb_body", height=80)
    if st.button("Submit Feedback", key="btn_feedback"):
        if fb_title:
            _send_github_notification(
                f"💬 User Feedback: {fb_title}",
                f"**Summary:** {fb_title}\n\n**Details:**\n{fb_body or '(none provided)'}\n\n_Submitted via Astro Planner App_"
            )
            st.success("✅ Feedback submitted — thank you!")
        else:
            st.warning("Please enter a short summary before submitting.")

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
    dso_config = load_dso_config()

    # --- Category & Type Filters ---
    col_cat, col_type = st.columns([1, 2])
    with col_cat:
        category = st.selectbox(
            "Catalog",
            ["Messier", "Bright Stars", "Astrophotography Favorites", "All"],
            key="dso_category"
        )
    if category == "Messier":
        dso_list = dso_config.get("messier", [])
    elif category == "Bright Stars":
        dso_list = dso_config.get("bright_stars", [])
    elif category == "Astrophotography Favorites":
        dso_list = dso_config.get("astrophotography_favorites", [])
    else:
        seen = set()
        dso_list = []
        for entry in (dso_config.get("messier", []) + dso_config.get("bright_stars", []) + dso_config.get("astrophotography_favorites", [])):
            if entry["name"] not in seen:
                seen.add(entry["name"])
                dso_list.append(entry)

    with col_type:
        all_types = sorted(set(d.get("type", "Unknown") for d in dso_list))
        selected_types = st.multiselect("Filter by Type", all_types, default=[], key="dso_type_filter",
                                        placeholder="All types shown — select to narrow")
    if selected_types:
        dso_list = [d for d in dso_list if d.get("type") in selected_types]

    # --- Batch Visibility Table ---
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        st.info("Set location in sidebar to see batch visibility for all objects in this catalog.")
    elif dso_list:
        dso_tuple = tuple(
            (d["name"], float(d["ra"]), float(d["dec"]),
             d.get("type", ""), float(d.get("magnitude", 0) or 0),
             d.get("common_name", ""))
            for d in dso_list
        )
        df_dsos = get_dso_summary(lat, lon, start_time, dso_tuple)

        if not df_dsos.empty:
            # Dec filter
            if "_dec_deg" in df_dsos.columns and (min_dec > -90 or max_dec < 90):
                df_dsos = df_dsos[(df_dsos["_dec_deg"] >= min_dec) & (df_dsos["_dec_deg"] <= max_dec)]

            # Observability check (same pattern as comet/asteroid sections)
            location_d = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            is_obs_list, reason_list = [], []
            for _, row in df_dsos.iterrows():
                try:
                    sc = SkyCoord(row['RA'], row['Dec'], frame='icrs')
                    check_times = [
                        start_time,
                        start_time + timedelta(minutes=duration / 2),
                        start_time + timedelta(minutes=duration)
                    ]
                    moon_locs_chk = []
                    if moon_loc:
                        try:
                            moon_locs_chk = [get_moon(Time(t), location_d) for t in check_times]
                        except Exception:
                            moon_locs_chk = [moon_loc] * 3
                    obs, reason = False, "Not visible during window"
                    if str(row.get('Status', '')) == "Never Rises":
                        reason = "Never Rises"
                    else:
                        for i_t, t_chk in enumerate(check_times):
                            aa = sc.transform_to(AltAz(obstime=Time(t_chk), location=location_d))
                            if min_alt <= aa.alt.degree <= max_alt and az_range[0] <= aa.az.degree <= az_range[1]:
                                sep_ok = (not moon_locs_chk) or (sc.separation(moon_locs_chk[i_t]).degree >= min_moon_sep)
                                if sep_ok:
                                    obs, reason = True, ""
                                    break
                    is_obs_list.append(obs)
                    reason_list.append(reason)
                except Exception:
                    is_obs_list.append(False)
                    reason_list.append("Parse Error")

            df_dsos["is_observable"] = is_obs_list
            df_dsos["filter_reason"] = reason_list

            df_obs_d = df_dsos[df_dsos["is_observable"]].copy()
            df_filt_d = df_dsos[~df_dsos["is_observable"]].copy()

            display_cols_d = ["Name", "Common Name", "Type", "Magnitude", "Constellation",
                              "Rise", "Transit", "Set", "Moon Status", "Moon Sep (°)", "RA", "Dec", "Status"]

            def display_dso_table(df_in):
                show = [c for c in display_cols_d if c in df_in.columns]
                # Sort observable tab by magnitude (brightest first) by default
                if "Magnitude" in df_in.columns:
                    df_sorted = df_in[show].sort_values("Magnitude", ascending=True)
                else:
                    df_sorted = df_in[show]
                st.dataframe(df_sorted, hide_index=True, width="stretch")

            tab_obs_d, tab_filt_d = st.tabs([
                f"🎯 Observable ({len(df_obs_d)})",
                f"👻 Unobservable ({len(df_filt_d)})"
            ])

            with tab_obs_d:
                st.subheader(f"Observable — {category}")
                plot_visibility_timeline(df_obs_d, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None)
                st.caption("Sorted by magnitude (brightest first). Use the Gantt chart sort options to reorder by rise/set time.")
                display_dso_table(df_obs_d)

            with tab_filt_d:
                st.caption("Objects not meeting your filters (Altitude/Azimuth/Moon) during the observation window.")
                if not df_filt_d.empty:
                    filt_show = [c for c in ["Name", "Type", "Magnitude", "filter_reason", "Rise", "Transit", "Set", "Status"] if c in df_filt_d.columns]
                    st.dataframe(df_filt_d[filt_show], hide_index=True, width="stretch")

            st.download_button(
                "Download DSO Data (CSV)",
                data=df_dsos.drop(columns=["is_observable", "filter_reason", "_rise_datetime", "_set_datetime"], errors="ignore").to_csv(index=False).encode("utf-8"),
                file_name=f"dso_{category.lower().replace(' ', '_')}_visibility.csv",
                mime="text/csv"
            )

    # --- Select Target for Trajectory ---
    st.markdown("---")
    st.subheader("Select Target for Trajectory")
    st.caption("Independent from the batch table above — pick any catalog to find your trajectory target.")

    col_tcat, col_ttype = st.columns([1, 2])
    with col_tcat:
        traj_category = st.selectbox(
            "Catalog",
            ["Messier", "Bright Stars", "Astrophotography Favorites", "All"],
            key="dso_traj_category"
        )
    if traj_category == "Messier":
        traj_dso_list = dso_config.get("messier", [])
    elif traj_category == "Bright Stars":
        traj_dso_list = dso_config.get("bright_stars", [])
    elif traj_category == "Astrophotography Favorites":
        traj_dso_list = dso_config.get("astrophotography_favorites", [])
    else:
        _seen_t = set()
        traj_dso_list = []
        for _entry in (dso_config.get("messier", []) + dso_config.get("bright_stars", []) + dso_config.get("astrophotography_favorites", [])):
            if _entry["name"] not in _seen_t:
                _seen_t.add(_entry["name"])
                traj_dso_list.append(_entry)

    with col_ttype:
        traj_all_types = sorted(set(d.get("type", "Unknown") for d in traj_dso_list))
        traj_selected_types = st.multiselect("Filter by Type", traj_all_types, default=[], key="dso_traj_type_filter",
                                             placeholder="All types shown — select to narrow")
    if traj_selected_types:
        traj_dso_list = [d for d in traj_dso_list if d.get("type") in traj_selected_types]

    batch_options = []
    for d in traj_dso_list:
        label = d["name"]
        if d.get("common_name"):
            label = f"{d['name']} — {d['common_name']}"
        batch_options.append(label)
    target_options = batch_options + ["Custom Object..."]

    selected_dso = st.selectbox("Select Object", target_options, key="dso_traj_sel")
    st.markdown("ℹ️ *Not in the list? Choose 'Custom Object...' to search SIMBAD for any star, galaxy, or nebula.*")

    if selected_dso == "Custom Object...":
        obj_name_custom = st.text_input("Enter Object Name (e.g., M31, Vega, NGC 891)", value="", key="dso_custom_input")
        if obj_name_custom:
            try:
                with st.spinner(f"Resolving {obj_name_custom} via SIMBAD..."):
                    name, sky_coord = resolve_simbad(obj_name_custom)
                st.success(f"✅ Resolved: **{name}** (RA: {sky_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, Dec: {sky_coord.dec.to_string(sep=':', precision=1)})")
                resolved = True
            except Exception as e:
                st.error(f"Could not resolve object: {e}")
    elif traj_dso_list:
        sel_idx = target_options.index(selected_dso)
        if sel_idx < len(traj_dso_list):
            dso_entry = traj_dso_list[sel_idx]
            sky_coord = SkyCoord(ra=float(dso_entry["ra"]) * u.deg, dec=float(dso_entry["dec"]) * u.deg, frame='icrs')
            name = dso_entry["name"]
            st.success(
                f"✅ Selected: **{name}**"
                + (f" — {dso_entry['common_name']}" if dso_entry.get("common_name") else "")
                + f" (RA: {sky_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, Dec: {sky_coord.dec.to_string(sep=':', precision=1)})"
            )
            resolved = True

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
            # Dec filter
            if "_dec_deg" in df_planets.columns and (min_dec > -90 or max_dec < 90):
                df_planets = df_planets[(df_planets["_dec_deg"] >= min_dec) & (df_planets["_dec_deg"] <= max_dec)]

            # --- Filter Planets by Observational Criteria ---
            location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
            visible_indices = []
            
            for idx, row in df_planets.iterrows():
                try:
                    # Parse coordinate strings back to SkyCoord
                    sc = SkyCoord(row['RA'], row['Dec'], frame='icrs')
                    
                    # Check visibility at start, mid, end
                    is_visible = False
                    check_times = [start_time, start_time + timedelta(minutes=duration/2), start_time + timedelta(minutes=duration)]
                    moon_locs = []
                    
                    # Pre-calculate Moon positions for these times
                    if moon_loc:
                        try:
                            moon_locs = [get_moon(Time(t), location) for t in check_times]
                        except:
                            moon_locs = [moon_loc] * 3

                    for i, t_check in enumerate(check_times):
                        frame = AltAz(obstime=Time(t_check), location=location)
                        aa = sc.transform_to(frame)
                        if min_alt <= aa.alt.degree <= max_alt and (az_range[0] <= aa.az.degree <= az_range[1]):
                            # Check Moon
                            if moon_locs:
                                current_moon = moon_locs[i]
                                sep = sc.separation(current_moon).degree
                                if sep >= min_moon_sep:
                                    is_visible = True
                                    break
                            else:
                                is_visible = True
                                break
                    
                    if is_visible:
                        visible_indices.append(idx)
                except:
                    visible_indices.append(idx) # Keep on error
            
            df_planets_filtered = df_planets.loc[visible_indices]
            
            if not df_planets_filtered.empty:
                st.caption("Visibility for tonight (Filtered by Altitude/Azimuth):")
                cols = ["Name", "Constellation", "Rise", "Transit", "Set", "Moon Status", "Moon Sep (°)", "RA", "Dec", "Status"]
                st.dataframe(df_planets_filtered[cols], hide_index=True, width="stretch")
                plot_visibility_timeline(df_planets_filtered, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None)
            else:
                st.warning(f"No planets meet your criteria (Alt [{min_alt}°, {max_alt}°], Az {az_range}, Moon Sep > {min_moon_sep}°) during the selected window.")
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
    comet_config = load_comets_config()
    active_comets = [c for c in comet_config["comets"] if c not in comet_config.get("cancelled", [])]
    priority_set = set(
        e["name"] if isinstance(e, dict) else e
        for e in comet_config.get("unistellar_priority", [])
    )
    comet_priority_windows = {
        e["name"]: (e.get("window_start", ""), e.get("window_end", ""))
        for e in comet_config.get("unistellar_priority", [])
        if isinstance(e, dict) and "window_start" in e
    }
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Auto-notification: alert admin if any priority comet is missing from list, and check missions page (once per session)
    if 'comet_priority_notified' not in st.session_state:
        # 1. Static check: priority comets not in active comet list
        missing_priority = [c for c in comet_config.get("unistellar_priority", []) if c not in comet_config["comets"]]
        if missing_priority:
            _send_github_notification(
                "🚨 Auto-Alert: Missing Priority Comets",
                "The following priority comets are missing from the comet list:\n\n"
                + "\n".join(f"- {c}" for c in missing_priority)
                + "\n\nPlease add them via the Admin Panel.\n\n_Auto-detected by Astro Planner_"
            )

        # 2. Semi-automatic: scrape Unistellar missions page and notify if new comets detected
        scraped = get_unistellar_scraped_comets()
        st.session_state.comet_scraped_priority = scraped
        if scraped:
            priority_set_upper = {c.upper() for c in priority_set}
            new_from_page = [c for c in scraped if _resolve_comet_alias(c) not in priority_set_upper]
            if new_from_page:
                # Write to pending file so it shows in the admin panel
                existing_pending = []
                if os.path.exists(COMET_PENDING_FILE):
                    with open(COMET_PENDING_FILE, "r") as f:
                        existing_pending = [l.strip() for l in f if l.strip()]
                existing_names = {l.split('|')[0] for l in existing_pending}
                with open(COMET_PENDING_FILE, "a") as f:
                    for c in new_from_page:
                        if c not in existing_names:
                            f.write(f"{c}|Add|Auto-detected from Unistellar missions page\n")
                _send_github_notification(
                    "🔍 Auto-Detected: New Unistellar Priority Comets",
                    "The following comets were found on the Unistellar missions page "
                    "but are not in the current priority list:\n\n"
                    + "\n".join(f"- {c}" for c in new_from_page)
                    + "\n\nPlease review and update `comets.yaml` if needed.\n\n"
                    "_Auto-detected by Astro Planner (daily scrape)_"
                )

        st.session_state.comet_priority_notified = True

    # User: request a comet addition
    with st.expander("➕ Request a Comet Addition"):
        st.caption("Is a comet missing from the list? Submit a request — it will be verified with JPL Horizons before admin review.")
        req_comet = st.text_input("Comet designation (e.g., C/2025 X1 or 29P)", key="req_comet_name")
        req_note = st.text_area("Optional note / reason", key="req_comet_note", height=60)
        if st.button("Submit Comet Request", key="btn_comet_req"):
            if req_comet:
                jpl_id = req_comet.split('(')[0].strip()
                with st.spinner(f"Verifying '{jpl_id}' with JPL Horizons..."):
                    try:
                        utc_check = start_time.astimezone(pytz.utc)
                        resolve_horizons(jpl_id, obs_time_str=utc_check.strftime('%Y-%m-%d %H:%M:%S'))
                        with open(COMET_PENDING_FILE, "a") as f:
                            f.write(f"{req_comet}|Add|{req_note or 'No note'}\n")
                        _send_github_notification(
                            f"☄️ Comet Add Request: {req_comet}",
                            f"**Comet:** {req_comet}\n**JPL ID:** {jpl_id}\n**Status:** ✅ JPL Verified\n**Note:** {req_note or 'None'}\n\n_Submitted via Astro Planner_"
                        )
                        st.success(f"✅ '{req_comet}' verified and request submitted for admin review.")
                    except Exception as e:
                        st.error(f"❌ JPL could not resolve '{jpl_id}': {e}")

    # Display active Unistellar priority targets
    if priority_set:
        with st.expander("⭐ Priority Comets"):
            st.caption(
                "Top-priority targets from Unistellar Citizen Science. "
                "🔄 *Missions page is checked daily for new additions.*"
            )
            scraped = st.session_state.get("comet_scraped_priority", [])
            if scraped:
                priority_set_upper = {c.upper() for c in priority_set}
                new_from_page = [c for c in scraped if _resolve_comet_alias(c) not in priority_set_upper]
                if new_from_page:
                    st.info(
                        f"🔍 **{len(new_from_page)} new comet(s)** detected on the Unistellar missions page "
                        f"not yet in the priority list: {', '.join(new_from_page)}. Admin has been notified."
                    )
            pri_rows_c = []
            for _c_entry in comet_config.get("unistellar_priority", []):
                _c_name = _c_entry["name"] if isinstance(_c_entry, dict) else _c_entry
                _w_start = _c_entry.get("window_start", "") if isinstance(_c_entry, dict) else ""
                _w_end = _c_entry.get("window_end", "") if isinstance(_c_entry, dict) else ""
                _window_str = ""
                if _w_start and _w_end:
                    _window_str = f"{_w_start} → {_w_end}"
                    if _w_start <= today_str <= _w_end:
                        _window_str = f"✅ ACTIVE: {_window_str}"
                pri_rows_c.append({"Comet": _c_name, "Observation Window": _window_str})
            st.dataframe(pd.DataFrame(pri_rows_c), hide_index=True, width="stretch")

    # Admin panel (sidebar)
    with st.sidebar:
        st.markdown("---")
        with st.expander("☄️ Comet Admin"):
            admin_pass_comet = st.text_input("Admin Password", type="password", key="comet_admin_pass")
            correct_pass_comet = st.secrets.get("ADMIN_PASSWORD")
            if correct_pass_comet and admin_pass_comet == correct_pass_comet:
                st.markdown("### Pending Requests")
                if os.path.exists(COMET_PENDING_FILE):
                    with open(COMET_PENDING_FILE, "r") as f:
                        c_lines = [l.strip() for l in f if l.strip()]
                else:
                    c_lines = []
                if not c_lines:
                    st.info("No pending requests.")
                for i, line in enumerate(c_lines):
                    parts = line.split('|')
                    if len(parts) < 2:
                        continue
                    c_name, c_action = parts[0], parts[1]
                    c_note = parts[2] if len(parts) > 2 else ""
                    st.text(f"{c_name} ({c_action})")
                    if c_note and c_note != "No note":
                        st.caption(c_note)
                    ca1, ca2 = st.columns(2)
                    if ca1.button("✅ Accept", key=f"cacc_{i}_{c_name}"):
                        cfg = load_comets_config()
                        if c_action == "Add" and c_name not in cfg["comets"]:
                            cfg["comets"].append(c_name)
                        # Auto-detected comets from the missions page scrape also go into unistellar_priority
                        if "Auto-detected from Unistellar missions page" in c_note and c_name not in cfg["unistellar_priority"]:
                            cfg["unistellar_priority"].append(c_name)
                        save_comets_config(cfg)
                        remaining = [l for l in c_lines if l != line]
                        with open(COMET_PENDING_FILE, "w") as f:
                            f.write("\n".join(remaining) + "\n")
                        st.rerun()
                    if ca2.button("❌ Reject", key=f"crej_{i}_{c_name}"):
                        remaining = [l for l in c_lines if l != line]
                        with open(COMET_PENDING_FILE, "w") as f:
                            f.write("\n".join(remaining) + "\n")
                        st.rerun()

                st.markdown("---")
                st.markdown("### Priority Overrides")
                cfg = load_comets_config()
                if cfg.get("priorities"):
                    for c_n, c_p in list(cfg["priorities"].items()):
                        pc1, pc2 = st.columns([3, 1])
                        pc1.text(f"{c_n}: {c_p}")
                        if pc2.button("🗑️", key=f"del_cpri_{c_n}"):
                            del cfg["priorities"][c_n]
                            save_comets_config(cfg)
                            st.rerun()
                np_name = st.text_input("Comet Name", key="new_cpri_name")
                np_val = st.selectbox("Priority", ["LOW", "MEDIUM", "HIGH", "URGENT"], key="new_cpri_val")
                if st.button("Set Priority", key="btn_set_cpri"):
                    if np_name:
                        cfg["priorities"][np_name] = np_val
                        save_comets_config(cfg)
                        st.success(f"Set {np_name} to {np_val}")

                st.markdown("---")
                st.markdown("### Remove from List")
                if cfg.get("comets"):
                    rem = st.selectbox("Select to remove", cfg["comets"], key="comet_rem_sel")
                    if st.button("Remove", key="btn_rem_comet"):
                        cfg["comets"] = [c for c in cfg["comets"] if c != rem]
                        save_comets_config(cfg)
                        st.rerun()

                st.markdown("---")
                st.markdown("### Remove Priority Target")
                pri_names_c = [
                    e["name"] if isinstance(e, dict) else e
                    for e in cfg.get("unistellar_priority", [])
                ]
                if pri_names_c:
                    rem_pri_c = st.selectbox("Select priority target to remove", pri_names_c, key="comet_rem_pri_sel")
                    if st.button("Remove from Priority", key="btn_rem_cpri"):
                        cfg["unistellar_priority"] = [
                            e for e in cfg["unistellar_priority"]
                            if (e["name"] if isinstance(e, dict) else e) != rem_pri_c
                        ]
                        save_comets_config(cfg)
                        st.rerun()
                else:
                    st.caption("No priority targets set.")

                st.markdown("---")
                st.markdown("### Add Priority Target")
                new_cpri_name = st.text_input("Comet Name", key="new_cpri_add_name", placeholder="e.g. C/2025 X1 (ATLAS)")
                new_cpri_ws = st.text_input("Window Start (YYYY-MM-DD, optional)", key="new_cpri_ws", placeholder="e.g. 2026-01-01")
                new_cpri_we = st.text_input("Window End (YYYY-MM-DD, optional)", key="new_cpri_we", placeholder="e.g. 2026-12-31")
                if st.button("Add to Priority List", key="btn_add_cpri"):
                    if new_cpri_name:
                        cfg = load_comets_config()
                        existing_pri = [e["name"] if isinstance(e, dict) else e for e in cfg["unistellar_priority"]]
                        if new_cpri_name not in existing_pri:
                            if new_cpri_ws and new_cpri_we:
                                cfg["unistellar_priority"].append({"name": new_cpri_name, "window_start": new_cpri_ws, "window_end": new_cpri_we})
                            else:
                                cfg["unistellar_priority"].append(new_cpri_name)
                            save_comets_config(cfg)
                            st.success(f"Added '{new_cpri_name}' to priority list.")
                            st.rerun()
                        else:
                            st.warning(f"'{new_cpri_name}' is already in the priority list.")

                st.markdown("---")
                st.markdown("### Add Comet to Tracking List")
                st.caption("Directly add a comet (bypasses the pending request queue).")
                new_comet_direct = st.text_input("Comet Designation", key="admin_comet_direct_add", placeholder="e.g. C/2026 A1 (MAPS)")
                if st.button("Add to List", key="btn_admin_add_comet"):
                    if new_comet_direct:
                        cfg = load_comets_config()
                        if new_comet_direct not in cfg["comets"]:
                            cfg["comets"].append(new_comet_direct)
                            save_comets_config(cfg)
                            st.success(f"Added '{new_comet_direct}' to comets list and pushed to GitHub.")
                            st.rerun()
                        else:
                            st.warning(f"'{new_comet_direct}' is already in the list.")

    # Batch visibility table
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        st.info("Set location in sidebar to see visibility summary for all comets.")
    elif active_comets:
        df_comets = get_comet_summary(lat, lon, start_time, tuple(active_comets))

        if not df_comets.empty:
            # Dec filter
            if "_dec_deg" in df_comets.columns and (min_dec > -90 or max_dec < 90):
                df_comets = df_comets[(df_comets["_dec_deg"] >= min_dec) & (df_comets["_dec_deg"] <= max_dec)]

            # Priority column: admin override > unistellar priority > empty
            df_comets["Priority"] = df_comets["Name"].apply(
                lambda n: comet_config["priorities"].get(n,
                    "⭐ PRIORITY" if n in priority_set else "")
            )

            # Observation window column
            def _comet_window_status(name):
                if name not in comet_priority_windows:
                    return ""
                w_start, w_end = comet_priority_windows[name]
                if w_start and w_end:
                    label = f"{w_start} → {w_end}"
                    return f"✅ ACTIVE: {label}" if w_start <= today_str <= w_end else f"⏳ {label}"
                return ""
            df_comets["Window"] = df_comets["Name"].apply(_comet_window_status)

            # Observability check (same pattern as planet section)
            location_c = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            is_obs_list, reason_list = [], []
            for _, row in df_comets.iterrows():
                try:
                    sc = SkyCoord(row['RA'], row['Dec'], frame='icrs')
                    check_times = [
                        start_time,
                        start_time + timedelta(minutes=duration / 2),
                        start_time + timedelta(minutes=duration)
                    ]
                    moon_locs_chk = []
                    if moon_loc:
                        try:
                            moon_locs_chk = [get_moon(Time(t), location_c) for t in check_times]
                        except Exception:
                            moon_locs_chk = [moon_loc] * 3

                    obs, reason = False, "Not in window (Alt/Az/Moon)"
                    if str(row.get('Status', '')) == "Never Rises":
                        reason = "Never Rises"
                    else:
                        for i, t_chk in enumerate(check_times):
                            aa = sc.transform_to(AltAz(obstime=Time(t_chk), location=location_c))
                            if min_alt <= aa.alt.degree <= max_alt and az_range[0] <= aa.az.degree <= az_range[1]:
                                sep_ok = (not moon_locs_chk) or (sc.separation(moon_locs_chk[i]).degree >= min_moon_sep)
                                if sep_ok:
                                    obs, reason = True, ""
                                    break
                    is_obs_list.append(obs)
                    reason_list.append(reason)
                except Exception:
                    is_obs_list.append(False)
                    reason_list.append("Parse Error")

            df_comets["is_observable"] = is_obs_list
            df_comets["filter_reason"] = reason_list

            df_obs_c = df_comets[df_comets["is_observable"]].copy()
            df_filt_c = df_comets[~df_comets["is_observable"]].copy()

            display_cols_c = ["Name", "Priority", "Window", "Constellation", "Rise", "Transit", "Set",
                              "Moon Status", "Moon Sep (°)", "RA", "Dec", "Status"]

            def display_comet_table(df_in):
                show = [c for c in display_cols_c if c in df_in.columns]

                def hi_comet(row):
                    val = str(row.get("Priority", "")).upper()
                    if "URGENT" in val:
                        return ["background-color: #ef5350; color: white; font-weight: bold"] * len(row)
                    if "HIGH" in val:
                        return ["background-color: #ffb74d; color: black; font-weight: bold"] * len(row)
                    if "MEDIUM" in val:
                        return ["background-color: #fff59d; color: black"] * len(row)
                    if "LOW" in val:
                        return ["background-color: #c8e6c9; color: black"] * len(row)
                    if "PRIORITY" in val:
                        return ["background-color: #e3f2fd; color: #0d47a1; font-weight: bold"] * len(row)
                    return [""] * len(row)

                st.dataframe(df_in[show].style.apply(hi_comet, axis=1), hide_index=True, width="stretch")

            tab_obs_c, tab_filt_c = st.tabs([
                f"🎯 Observable ({len(df_obs_c)})",
                f"👻 Unobservable ({len(df_filt_c)})"
            ])

            with tab_obs_c:
                st.subheader("Observable Comets")
                plot_visibility_timeline(df_obs_c, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None)
                st.markdown(
                    "**Legend:** <span style='background-color: #e3f2fd; color: #0d47a1; "
                    "padding: 2px 6px; border-radius: 4px; font-weight: bold;'>⭐ PRIORITY</span>"
                    " = Unistellar Citizen Science priority target",
                    unsafe_allow_html=True
                )
                display_comet_table(df_obs_c)

            with tab_filt_c:
                st.caption("Comets not meeting your filters within the observation window.")
                if not df_filt_c.empty:
                    filt_show = [c for c in ["Name", "filter_reason", "Rise", "Transit", "Set", "Status"] if c in df_filt_c.columns]
                    st.dataframe(df_filt_c[filt_show], hide_index=True, width="stretch")

            st.download_button(
                "Download Comet Data (CSV)",
                data=df_comets.drop(columns=["is_observable", "filter_reason", "_rise_datetime", "_set_datetime"], errors="ignore").to_csv(index=False).encode("utf-8"),
                file_name="comets_visibility.csv",
                mime="text/csv"
            )

    # Select comet for trajectory
    st.markdown("---")
    st.subheader("Select Comet for Trajectory")
    comet_options = active_comets + ["Custom Comet..."]
    selected_target = st.selectbox("Select a Comet", comet_options, key="comet_traj_sel")
    st.markdown("ℹ️ *Target not listed? Use 'Custom Comet...' or submit a request above.*")

    if selected_target == "Custom Comet...":
        st.caption("Search [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) to find the comet's exact designation or SPK-ID, then enter it below.")
        obj_name = st.text_input("Enter Comet Designation or SPK-ID (e.g., C/2020 F3, 90001202)", value="", key="comet_custom_input")
    else:
        obj_name = _get_comet_jpl_id(selected_target)

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
    asteroid_config = load_asteroids_config()
    active_asteroids = [a for a in asteroid_config["asteroids"] if a not in asteroid_config.get("cancelled", [])]
    priority_set = set(_asteroid_priority_name(e) for e in asteroid_config.get("unistellar_priority", []))
    priority_windows = {
        e["name"]: (e.get("window_start", ""), e.get("window_end", ""))
        for e in asteroid_config.get("unistellar_priority", [])
        if isinstance(e, dict) and "window_start" in e
    }
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Auto-notification: once per session
    if 'asteroid_priority_notified' not in st.session_state:
        missing_priority = [n for n in priority_set if n not in asteroid_config["asteroids"]]
        if missing_priority:
            _send_github_notification(
                "🚨 Auto-Alert: Missing Priority Asteroids",
                "The following priority asteroids are missing from the asteroid list:\n\n"
                + "\n".join(f"- {a}" for a in missing_priority)
                + "\n\nPlease add them via the Admin Panel.\n\n_Auto-detected by Astro Planner_"
            )
        scraped = get_unistellar_scraped_asteroids()
        st.session_state.asteroid_scraped_priority = scraped
        if scraped:
            priority_set_upper = {n.upper() for n in priority_set}
            new_from_page = [a for a in scraped if _resolve_asteroid_alias(a) not in priority_set_upper]
            if new_from_page:
                existing_pending = []
                if os.path.exists(ASTEROID_PENDING_FILE):
                    with open(ASTEROID_PENDING_FILE, "r") as f:
                        existing_pending = [l.strip() for l in f if l.strip()]
                existing_names = {l.split('|')[0] for l in existing_pending}
                with open(ASTEROID_PENDING_FILE, "a") as f:
                    for a in new_from_page:
                        if a not in existing_names:
                            f.write(f"{a}|Add|Auto-detected from Unistellar planetary defense page\n")
                _send_github_notification(
                    "🔍 Auto-Detected: New Unistellar Priority Asteroids",
                    "The following asteroids were found on the Unistellar planetary defense missions page "
                    "but are not in the current priority list:\n\n"
                    + "\n".join(f"- {a}" for a in new_from_page)
                    + "\n\nPlease review and update `asteroids.yaml` if needed.\n\n"
                    "_Auto-detected by Astro Planner (daily scrape)_"
                )
        st.session_state.asteroid_priority_notified = True

    # User: request an asteroid addition
    with st.expander("➕ Request an Asteroid Addition"):
        st.caption("Is an asteroid missing from the list? Submit a request — it will be verified with JPL Horizons before admin review.")
        req_asteroid = st.text_input("Asteroid designation (e.g., 99942 Apophis, 433 Eros)", key="req_asteroid_name")
        req_a_note = st.text_area("Optional note / reason", key="req_asteroid_note", height=60)
        if st.button("Submit Asteroid Request", key="btn_asteroid_req"):
            if req_asteroid:
                jpl_id = _asteroid_jpl_id(req_asteroid)
                with st.spinner(f"Verifying '{jpl_id}' with JPL Horizons..."):
                    try:
                        utc_check = start_time.astimezone(pytz.utc)
                        resolve_horizons(jpl_id, obs_time_str=utc_check.strftime('%Y-%m-%d %H:%M:%S'))
                        with open(ASTEROID_PENDING_FILE, "a") as f:
                            f.write(f"{req_asteroid}|Add|{req_a_note or 'No note'}\n")
                        _send_github_notification(
                            f"🪨 Asteroid Add Request: {req_asteroid}",
                            f"**Asteroid:** {req_asteroid}\n**JPL ID:** {jpl_id}\n**Status:** ✅ JPL Verified\n**Note:** {req_a_note or 'None'}\n\n_Submitted via Astro Planner_"
                        )
                        st.success(f"✅ '{req_asteroid}' verified and request submitted for admin review.")
                    except Exception as e:
                        st.error(f"❌ JPL could not resolve '{jpl_id}': {e}")

    # Priority asteroids expander
    if priority_set:
        with st.expander("⭐ Priority Asteroids (Unistellar Planetary Defense)"):
            st.caption(
                "Top-priority targets from Unistellar Planetary Defense. "
                "🔄 *Missions page is checked daily for new additions.*"
            )
            scraped_a = st.session_state.get("asteroid_scraped_priority", [])
            if scraped_a:
                priority_set_upper = {n.upper() for n in priority_set}
                new_from_page = [a for a in scraped_a if _resolve_asteroid_alias(a) not in priority_set_upper]
                if new_from_page:
                    st.info(
                        f"🔍 **{len(new_from_page)} new asteroid(s)** detected on the Unistellar missions page "
                        f"not yet in the priority list: {', '.join(new_from_page)}. Admin has been notified."
                    )
            pri_rows = []
            for entry in asteroid_config.get("unistellar_priority", []):
                a_name = _asteroid_priority_name(entry)
                w_start = entry.get("window_start", "") if isinstance(entry, dict) else ""
                w_end = entry.get("window_end", "") if isinstance(entry, dict) else ""
                window_str = ""
                if w_start and w_end:
                    window_str = f"{w_start} → {w_end}"
                    if w_start <= today_str <= w_end:
                        window_str = f"✅ ACTIVE: {window_str}"
                pri_rows.append({"Asteroid": a_name, "Observation Window": window_str})
            st.dataframe(pd.DataFrame(pri_rows), hide_index=True, width="stretch")

    # Admin panel (sidebar)
    with st.sidebar:
        st.markdown("---")
        with st.expander("🪨 Asteroid Admin"):
            admin_pass_a = st.text_input("Admin Password", type="password", key="asteroid_admin_pass")
            correct_pass_a = st.secrets.get("ADMIN_PASSWORD")
            if correct_pass_a and admin_pass_a == correct_pass_a:
                st.markdown("### Pending Requests")
                if os.path.exists(ASTEROID_PENDING_FILE):
                    with open(ASTEROID_PENDING_FILE, "r") as f:
                        a_lines = [l.strip() for l in f if l.strip()]
                else:
                    a_lines = []
                if not a_lines:
                    st.info("No pending requests.")
                for i, line in enumerate(a_lines):
                    parts = line.split('|')
                    if len(parts) < 2:
                        continue
                    a_name, a_action = parts[0], parts[1]
                    a_note = parts[2] if len(parts) > 2 else ""
                    st.text(f"{a_name} ({a_action})")
                    if a_note and a_note != "No note":
                        st.caption(a_note)
                    aa1, aa2 = st.columns(2)
                    if aa1.button("✅ Accept", key=f"aacc_{i}_{a_name}"):
                        cfg = load_asteroids_config()
                        if a_action == "Add" and a_name not in cfg["asteroids"]:
                            cfg["asteroids"].append(a_name)
                        if "Auto-detected from Unistellar planetary defense page" in a_note:
                            priority_names = [_asteroid_priority_name(e) for e in cfg["unistellar_priority"]]
                            if a_name not in priority_names:
                                cfg["unistellar_priority"].append(a_name)
                        save_asteroids_config(cfg)
                        remaining = [l for l in a_lines if l != line]
                        with open(ASTEROID_PENDING_FILE, "w") as f:
                            f.write("\n".join(remaining) + "\n")
                        st.rerun()
                    if aa2.button("❌ Reject", key=f"arej_{i}_{a_name}"):
                        remaining = [l for l in a_lines if l != line]
                        with open(ASTEROID_PENDING_FILE, "w") as f:
                            f.write("\n".join(remaining) + "\n")
                        st.rerun()

                st.markdown("---")
                st.markdown("### Priority Overrides")
                cfg = load_asteroids_config()
                if cfg.get("priorities"):
                    for a_n, a_p in list(cfg["priorities"].items()):
                        pa1, pa2 = st.columns([3, 1])
                        pa1.text(f"{a_n}: {a_p}")
                        if pa2.button("🗑️", key=f"del_apri_{a_n}"):
                            del cfg["priorities"][a_n]
                            save_asteroids_config(cfg)
                            st.rerun()
                nap_name = st.text_input("Asteroid Name", key="new_apri_name")
                nap_val = st.selectbox("Priority", ["LOW", "MEDIUM", "HIGH", "URGENT"], key="new_apri_val")
                if st.button("Set Priority", key="btn_set_apri"):
                    if nap_name:
                        cfg["priorities"][nap_name] = nap_val
                        save_asteroids_config(cfg)
                        st.success(f"Set {nap_name} to {nap_val}")

                st.markdown("---")
                st.markdown("### Remove from List")
                if cfg.get("asteroids"):
                    arem = st.selectbox("Select to remove", cfg["asteroids"], key="asteroid_rem_sel")
                    if st.button("Remove", key="btn_rem_asteroid"):
                        cfg["asteroids"] = [a for a in cfg["asteroids"] if a != arem]
                        save_asteroids_config(cfg)
                        st.rerun()

                st.markdown("---")
                st.markdown("### Remove Priority Target")
                pri_names_a = [_asteroid_priority_name(e) for e in cfg.get("unistellar_priority", [])]
                if pri_names_a:
                    rem_pri_a = st.selectbox("Select priority target to remove", pri_names_a, key="asteroid_rem_pri_sel")
                    if st.button("Remove from Priority", key="btn_rem_apri"):
                        cfg["unistellar_priority"] = [
                            e for e in cfg["unistellar_priority"]
                            if _asteroid_priority_name(e) != rem_pri_a
                        ]
                        save_asteroids_config(cfg)
                        st.rerun()
                else:
                    st.caption("No priority targets set.")

                st.markdown("---")
                st.markdown("### Add Priority Target")
                new_apri_name = st.text_input("Asteroid Name", key="new_apri_add_name", placeholder="e.g. 99942 Apophis")
                new_apri_ws = st.text_input("Window Start (YYYY-MM-DD, optional)", key="new_apri_ws", placeholder="e.g. 2026-01-01")
                new_apri_we = st.text_input("Window End (YYYY-MM-DD, optional)", key="new_apri_we", placeholder="e.g. 2026-12-31")
                if st.button("Add to Priority List", key="btn_add_apri"):
                    if new_apri_name:
                        cfg = load_asteroids_config()
                        existing_pri_a = [_asteroid_priority_name(e) for e in cfg["unistellar_priority"]]
                        if new_apri_name not in existing_pri_a:
                            if new_apri_ws and new_apri_we:
                                cfg["unistellar_priority"].append({"name": new_apri_name, "window_start": new_apri_ws, "window_end": new_apri_we})
                            else:
                                cfg["unistellar_priority"].append(new_apri_name)
                            save_asteroids_config(cfg)
                            st.success(f"Added '{new_apri_name}' to priority list.")
                            st.rerun()
                        else:
                            st.warning(f"'{new_apri_name}' is already in the priority list.")

                st.markdown("---")
                st.markdown("### Add Asteroid to Tracking List")
                st.caption("Directly add an asteroid (bypasses the pending request queue).")
                new_asteroid_direct = st.text_input("Asteroid Designation", key="admin_asteroid_direct_add", placeholder="e.g. 2024 YR4")
                if st.button("Add to List", key="btn_admin_add_asteroid"):
                    if new_asteroid_direct:
                        cfg = load_asteroids_config()
                        if new_asteroid_direct not in cfg["asteroids"]:
                            cfg["asteroids"].append(new_asteroid_direct)
                            save_asteroids_config(cfg)
                            st.success(f"Added '{new_asteroid_direct}' to asteroids list and pushed to GitHub.")
                            st.rerun()
                        else:
                            st.warning(f"'{new_asteroid_direct}' is already in the list.")

    # Batch visibility table
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        st.info("Set location in sidebar to see visibility summary for all asteroids.")
    elif active_asteroids:
        df_asteroids = get_asteroid_summary(lat, lon, start_time, tuple(active_asteroids))

        if not df_asteroids.empty:
            # Dec filter
            if "_dec_deg" in df_asteroids.columns and (min_dec > -90 or max_dec < 90):
                df_asteroids = df_asteroids[(df_asteroids["_dec_deg"] >= min_dec) & (df_asteroids["_dec_deg"] <= max_dec)]

            df_asteroids["Priority"] = df_asteroids["Name"].apply(
                lambda n: asteroid_config["priorities"].get(n,
                    "⭐ PRIORITY" if n in priority_set else "")
            )

            def _window_status(name):
                if name not in priority_windows:
                    return ""
                w_start, w_end = priority_windows[name]
                if w_start and w_end:
                    label = f"{w_start} → {w_end}"
                    return f"✅ ACTIVE: {label}" if w_start <= today_str <= w_end else f"⏳ {label}"
                return ""
            df_asteroids["Window"] = df_asteroids["Name"].apply(_window_status)

            location_a = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            is_obs_list, reason_list = [], []
            for _, row in df_asteroids.iterrows():
                try:
                    sc = SkyCoord(row['RA'], row['Dec'], frame='icrs')
                    check_times = [
                        start_time,
                        start_time + timedelta(minutes=duration / 2),
                        start_time + timedelta(minutes=duration)
                    ]
                    moon_locs_chk = []
                    if moon_loc:
                        try:
                            moon_locs_chk = [get_moon(Time(t), location_a) for t in check_times]
                        except Exception:
                            moon_locs_chk = [moon_loc] * 3
                    obs, reason = False, "Not visible during window"
                    if str(row.get('Status', '')) == "Never Rises":
                        reason = "Never Rises"
                    else:
                        for i_t, t_chk in enumerate(check_times):
                            aa = sc.transform_to(AltAz(obstime=Time(t_chk), location=location_a))
                            if min_alt <= aa.alt.degree <= max_alt and az_range[0] <= aa.az.degree <= az_range[1]:
                                sep_ok = (not moon_locs_chk) or (sc.separation(moon_locs_chk[i_t]).degree >= min_moon_sep)
                                if sep_ok:
                                    obs, reason = True, ""
                                    break
                    is_obs_list.append(obs)
                    reason_list.append(reason)
                except Exception:
                    is_obs_list.append(False)
                    reason_list.append("Parse Error")

            df_asteroids["is_observable"] = is_obs_list
            df_asteroids["filter_reason"] = reason_list

            df_obs_a = df_asteroids[df_asteroids["is_observable"]].copy()
            df_filt_a = df_asteroids[~df_asteroids["is_observable"]].copy()

            display_cols_a = ["Name", "Priority", "Window", "Constellation", "Rise", "Transit", "Set",
                              "Moon Status", "Moon Sep (°)", "RA", "Dec", "Status"]

            def display_asteroid_table(df_in):
                show = [c for c in display_cols_a if c in df_in.columns]

                def hi_asteroid(row):
                    val = str(row.get("Priority", "")).upper()
                    if "URGENT" in val:
                        return ["background-color: #ef5350; color: white; font-weight: bold"] * len(row)
                    if "HIGH" in val:
                        return ["background-color: #ffb74d; color: black; font-weight: bold"] * len(row)
                    if "MEDIUM" in val:
                        return ["background-color: #fff59d; color: black"] * len(row)
                    if "LOW" in val:
                        return ["background-color: #c8e6c9; color: black"] * len(row)
                    if "PRIORITY" in val:
                        return ["background-color: #e3f2fd; color: #0d47a1; font-weight: bold"] * len(row)
                    return [""] * len(row)

                st.dataframe(df_in[show].style.apply(hi_asteroid, axis=1), hide_index=True, width="stretch")

            tab_obs_a, tab_filt_a = st.tabs([
                f"🎯 Observable ({len(df_obs_a)})",
                f"👻 Unobservable ({len(df_filt_a)})"
            ])

            with tab_obs_a:
                st.subheader("Observable Asteroids")
                plot_visibility_timeline(df_obs_a, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None)
                st.markdown(
                    "**Legend:** <span style='background-color: #e3f2fd; color: #0d47a1; "
                    "padding: 2px 6px; border-radius: 4px; font-weight: bold;'>⭐ PRIORITY</span>"
                    " = Unistellar Planetary Defense priority target",
                    unsafe_allow_html=True
                )
                display_asteroid_table(df_obs_a)

            with tab_filt_a:
                st.caption("Asteroids not meeting your filters within the observation window.")
                if not df_filt_a.empty:
                    filt_show = [c for c in ["Name", "filter_reason", "Rise", "Transit", "Set", "RA", "Dec", "Status"] if c in df_filt_a.columns]
                    st.dataframe(df_filt_a[filt_show], hide_index=True, width="stretch")

            st.download_button(
                "Download Asteroid Data (CSV)",
                data=df_asteroids.drop(columns=["is_observable", "filter_reason", "_rise_datetime", "_set_datetime"], errors="ignore").to_csv(index=False).encode("utf-8"),
                file_name="asteroids_visibility.csv",
                mime="text/csv"
            )

    # Select asteroid for trajectory
    st.markdown("---")
    st.subheader("Select Asteroid for Trajectory")
    asteroid_options = active_asteroids + ["Custom Asteroid..."]
    selected_target = st.selectbox("Select an Asteroid", asteroid_options, key="asteroid_traj_sel")
    st.markdown("ℹ️ *Target not listed? Use 'Custom Asteroid...' or submit a request above. Find the exact designation in the [JPL Small-Body Database](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html).*")

    if selected_target == "Custom Asteroid...":
        st.caption("Search [JPL Small-Body Database](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html) or [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) to find the exact designation, then enter it below.")
        obj_name = st.text_input("Enter Asteroid Name or Designation (e.g., Eros, 2024 YR4, 99942)", value="", key="asteroid_custom_input")
    else:
        obj_name = _asteroid_jpl_id(selected_target)

    if obj_name:
        try:
            with st.spinner(f"Querying JPL Horizons for {obj_name}..."):
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

    # Display Blocked/Cancelled Targets
    if current_config.get("cancelled") or current_config.get("too_faint"):
        with st.expander("🚫 Invalid & Cancelled Events"):
            st.caption("These targets are hidden from the main list:")
            
            blocked_data = []
            for t in current_config.get("cancelled", []):
                blocked_data.append({"Target": t, "Reason": "Cancelled"})
            for t in current_config.get("too_faint", []):
                blocked_data.append({"Target": t, "Reason": "Invalid (Too Faint)"})
            
            if blocked_data:
                st.dataframe(pd.DataFrame(blocked_data), hide_index=True, width="stretch")

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

                st.markdown("---")
                st.markdown("### Add Manual Event")
                st.caption("Add a transient event not on the Unistellar page (e.g. nova, supernova). It will appear in the table alongside scraped targets.")
                me_name = st.text_input("Event Name", key="cosmic_me_name", placeholder="e.g. Nova Her 2026")
                me_ra = st.text_input("RA (e.g. 18h 07m 24s)", key="cosmic_me_ra")
                me_dec = st.text_input("Dec (e.g. +45° 31' 00\")", key="cosmic_me_dec")
                me_type = st.text_input("Type (optional)", key="cosmic_me_type", placeholder="e.g. Nova")
                if st.button("Add Manual Event", key="btn_add_manual_event"):
                    if me_name and me_ra and me_dec:
                        config = load_targets_config()
                        config.setdefault("manual_events", [])
                        existing_names = [e.get("name", "") for e in config["manual_events"]]
                        if me_name not in existing_names:
                            config["manual_events"].append({"name": me_name, "ra": me_ra, "dec": me_dec, "type": me_type or "Manual"})
                            save_targets_config(config)
                            st.success(f"Added '{me_name}' to manual events and pushed to GitHub.")
                            st.rerun()
                        else:
                            st.warning(f"'{me_name}' already exists in manual events.")
                    else:
                        st.warning("Name, RA, and Dec are required.")

                st.markdown("---")
                st.markdown("### Remove Manual Event")
                config_me = load_targets_config()
                manual_events_list = config_me.get("manual_events", [])
                if manual_events_list:
                    me_to_remove = st.selectbox("Select event to remove", [e["name"] for e in manual_events_list], key="cosmic_rem_me_sel")
                    if st.button("Remove Manual Event", key="btn_rem_manual_event"):
                        config_me["manual_events"] = [e for e in manual_events_list if e["name"] != me_to_remove]
                        save_targets_config(config_me)
                        st.rerun()
                else:
                    st.caption("No manual events added.")

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

    # Inject manual events from targets.yaml into df_alerts
    if df_alerts is not None:
        _manual_cfg = load_targets_config()
        _manual_events = _manual_cfg.get("manual_events", [])
        if _manual_events:
            _me_rows = [{"Name": e["name"], "RA": e.get("ra", ""), "DEC": e.get("dec", ""), "Type": e.get("type", "Manual")} for e in _manual_events]
            _me_df = pd.DataFrame(_me_rows)
            df_alerts = pd.concat([df_alerts, _me_df], ignore_index=True)

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
            
            # If not found, create it so we can display priorities
            if not pri_col:
                pri_col = "Priority"
                df_alerts[pri_col] = ""

            if "priorities" in config:
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
                    
                    # --- Observability Check ---
                    is_obs = True
                    filt_reason = ""

                    # Moon Check (Start Time for display)
                    moon_sep = 0.0
                    moon_status = ""
                    if moon_loc:
                        moon_sep = sc.separation(moon_loc).degree
                        
                        # Determine status based on illumination and separation
                        if 'moon_illum' in locals():
                             moon_status = get_moon_status(moon_illum, moon_sep)
                    
                    # Pre-calculated moon_locs logic is needed here too

                    # 1. Basic Status
                    if is_obs:
                        if details['Status'] == "Never Rises":
                            is_obs = False
                            filt_reason = "Never Rises"
                        elif details['Status'] == "Error":
                            is_obs = False
                            filt_reason = "Coord Error"

                    # 2. Advanced Filters (Alt/Az)
                    if is_obs:
                        # Check Start, Mid, End of window
                        check_times = [start_time, start_time + timedelta(minutes=duration/2), start_time + timedelta(minutes=duration)]
                        
                        # Reuse or calculate moon positions for these times
                        moon_locs_dynamic = []
                        if moon_loc:
                            try:
                                moon_locs_dynamic = [get_moon(Time(t), location) for t in check_times]
                            except:
                                moon_locs_dynamic = [moon_loc] * 3

                        passed_checks = False
                        for i, t_check in enumerate(check_times):
                            # Quick AltAz check
                            frame = AltAz(obstime=Time(t_check), location=location)
                            aa = sc.transform_to(frame)
                            if min_alt <= aa.alt.degree <= max_alt and (az_range[0] <= aa.az.degree <= az_range[1]):
                                # Check Moon dynamically
                                if moon_locs_dynamic:
                                    sep_dyn = sc.separation(moon_locs_dynamic[i]).degree
                                    if sep_dyn >= min_moon_sep:
                                        passed_checks = True
                                        break
                                else:
                                    passed_checks = True
                                    break
                        if not passed_checks:
                            is_obs = False
                            filt_reason = f"Filters failed (Alt/Az or Moon < {min_moon_sep}°) during window"

                    # Merge row data with details
                    row_dict = row.to_dict()
                    row_dict.update(details)
                    row_dict['is_observable'] = is_obs
                    row_dict['filter_reason'] = filt_reason
                    row_dict['Moon Sep (°)'] = round(moon_sep, 1) if moon_loc else 0
                    row_dict['Moon Status'] = moon_status
                    planning_data.append(row_dict)
                except Exception:
                    # If coord parsing fails, just keep original row
                    d = row.to_dict()
                    d['is_observable'] = False
                    d['filter_reason'] = "Data/Parse Error"
                    planning_data.append(d)
            
            progress_bar.empty()
            
            # Create new enriched DataFrame
            df_display = pd.DataFrame(planning_data)
            
            # Add 'sec' to Duration column
            dur_col = next((c for c in df_display.columns if 'dur' in c.lower()), None)
            if dur_col:
                df_display[dur_col] = df_display[dur_col].astype(str) + " sec"
            
            # Identify DeepLink column
            link_col = next((c for c in df_display.columns if 'deeplink' in c.lower().replace(" ", "")), None)

            # Reorder columns to put Name and Planning info first
            priority_cols = [target_col, 'Constellation', 'Rise', 'Transit', 'Set', 'Moon Status', 'Moon Sep (°)', 'Status']
            
            # Ensure Priority is visible and upfront
            if pri_col and pri_col in df_display.columns:
                priority_cols.insert(1, pri_col)

            other_cols = [c for c in df_display.columns if c not in priority_cols and c != link_col]
            
            final_order = priority_cols + other_cols
            if link_col:
                final_order.append(link_col)
            
            df_display = df_display[final_order]

            # Split Data
            df_obs = df_display[df_display['is_observable'] == True].copy()
            df_filt = df_display[df_display['is_observable'] == False].copy()
            
            # Filter columns for display
            cols_to_remove_keywords = ['exposure', 'cadence', 'gain', 'exp', 'cad']
            actual_cols_to_drop = [
                col for col in df_display.columns 
                if any(keyword in col.lower() for keyword in cols_to_remove_keywords) or col in ['is_observable', 'filter_reason']
            ]
            # Also drop hidden columns used for plotting
            hidden_cols = [c for c in df_display.columns if c.startswith('_')]
            
            # Helper to style and display
            def display_styled_table(df_in):
                final_table = df_in.drop(columns=actual_cols_to_drop + hidden_cols, errors='ignore')
                
                # Force DeepLink to the very end
                curr_cols = final_table.columns.tolist()
                p_cols = [c for c in priority_cols if c in curr_cols]
                l_cols = [c for c in curr_cols if c == link_col]
                o_cols = [c for c in curr_cols if c not in p_cols and c not in l_cols]
                
                # Order: Priority -> Others -> DeepLink
                new_order = p_cols + o_cols + l_cols
                final_table = final_table[new_order]
                
                # Configure DeepLink column
                col_config = {}
                if link_col and link_col in final_table.columns:
                    col_config[link_col] = st.column_config.LinkColumn(
                        "Deep Link", display_text="Open App"
                    )

                if pri_col and pri_col in final_table.columns:
                    def highlight_row(row):
                        val = str(row[pri_col]).upper().strip()
                        style = ""
                        if "URGENT" in val: style = "background-color: #ef5350; color: white; font-weight: bold"
                        elif "HIGH" in val: style = "background-color: #ffb74d; color: black; font-weight: bold"
                        elif "MEDIUM" in val: style = "background-color: #fff59d; color: black"
                        elif "LOW" in val: style = "background-color: #c8e6c9; color: black"
                        return [style] * len(row)
                    st.dataframe(final_table.style.apply(highlight_row, axis=1), width="stretch", column_config=col_config)
                else:
                    st.dataframe(final_table, width="stretch", column_config=col_config)

            # Tabs
            tab_obs, tab_filt = st.tabs([f"🎯 Observable ({len(df_obs)})", f"👻 Unobservable ({len(df_filt)})"])
            
            with tab_obs:
                st.subheader("Available Targets")
                
                plot_visibility_timeline(df_obs, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None)

                # Legend
                st.markdown("""
                **Priority Legend:** 
                <span style='background-color: #ef5350; color: white; padding: 2px 6px; border-radius: 4px;'>URGENT</span> 
                <span style='background-color: #ffb74d; color: black; padding: 2px 6px; border-radius: 4px;'>HIGH</span> 
                <span style='background-color: #fff59d; color: black; padding: 2px 6px; border-radius: 4px;'>MEDIUM</span> 
                <span style='background-color: #c8e6c9; color: black; padding: 2px 6px; border-radius: 4px;'>LOW</span>
                """, unsafe_allow_html=True)
                
                st.info("ℹ️ **Note:** The 'DeepLink' column is for Unistellar telescopes only. For other equipment, please use the RA/Dec coordinates.")
                
                display_styled_table(df_obs)
            
            with tab_filt:
                st.caption("Targets hidden because they do not meet criteria within the **Observation Window** (Start Time + Duration) selected in the sidebar.")
                if not df_filt.empty:
                    # Show reason, timing context, and coordinates
                    base_cols = ['Name', 'filter_reason', 'Rise', 'Transit', 'Set']
                    if ra_col:
                        base_cols.append(ra_col)
                    if dec_col:
                        base_cols.append(dec_col)
                    show_cols = [c for c in base_cols if c in df_filt.columns]
                    if pri_col and pri_col in df_filt.columns:
                        show_cols.append(pri_col)
                    st.dataframe(df_filt[show_cols], hide_index=True, width="stretch")

            st.download_button(
                label="Download Scraped Data (CSV)",
                data=df_alerts.to_csv(index=False).encode('utf-8'),
                file_name="unistellar_targets.csv",
                mime="text/csv"
            )

            st.markdown("---")
            st.subheader("Select Target for Trajectory")
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
    
    # --- Add Moon Columns to Detailed Data ---
    if lat is not None and lon is not None:
        try:
            # Generate time steps matching compute_trajectory (10 min steps)
            steps = len(df)
            times_utc = [(start_time + timedelta(minutes=i*10)).astimezone(pytz.utc) for i in range(steps)]
            t_grid = Time(times_utc)
            
            # Get Moon positions for all steps
            moon_locs = get_moon(t_grid, location)
            
            # Calculate Illumination (approx constant for session)
            sun_loc = get_sun(Time(start_time))
            elongation = sun_loc.separation(moon_locs[0])
            moon_illum = float(0.5 * (1 - math.cos(elongation.rad))) * 100
            
            sep_list = []
            status_list = []
            
            for i in range(steps):
                t_coord = ephem_coords[i] if ephem_coords and i < len(ephem_coords) else sky_coord
                sep = t_coord.separation(moon_locs[i]).degree
                sep_list.append(round(sep, 1))
                status_list.append(get_moon_status(moon_illum, sep))
            
            df["Moon Sep (°)"] = sep_list
            df["Moon Status"] = status_list
        except Exception:
            pass

    # --- Moon Check ---
    current_moon_sep = None
    moon_status_text = "N/A"
    if moon_loc and sky_coord:
        sep = sky_coord.separation(moon_loc).degree
        current_moon_sep = sep
        if sep < min_moon_sep:
             st.warning(f"⚠️ **Moon Warning:** Target is {sep:.1f}° from the Moon (Limit: {min_moon_sep}°).")

        if 'moon_illum' in locals():
             status = get_moon_status(moon_illum, sep)
             moon_status_text = f"{sep:.1f}° ({status})"

    # --- Observational Filter Check ---
    # Check if any point in the trajectory meets the criteria
    visible_points = df[
        (df["Altitude (°)"].between(min_alt, max_alt)) & 
        (df["Azimuth (°)"].between(az_range[0], az_range[1]))
    ]
    
    if visible_points.empty:
        st.warning(f"⚠️ **Visibility Warning:** Target does not meet filters (Alt [{min_alt}°, {max_alt}°], Az {az_range}) during window.")
    
    # Metrics
    max_alt = df["Altitude (°)"].max()
    best_time = df.loc[df["Altitude (°)"].idxmax()]["Local Time"]
    constellation = df["Constellation"].iloc[0]
    
    m1, m2, m3, m4, m5 = st.columns([1, 1, 1, 1, 2])
    m1.metric("Max Altitude", f"{max_alt}°")
    m2.metric("Best Time", best_time.split(" ")[1])
    m3.metric("Direction at Max", df.loc[df["Altitude (°)"].idxmax()]["Direction"])
    m4.metric("Constellation", constellation)
    m5.metric("Moon Sep", moon_status_text)

    # Visibility Window (Rise → Set Gantt) alongside trajectory
    try:
        planning_info = calculate_planning_info(sky_coord, location, start_time)
        planning_info["Name"] = name
        df_plan = pd.DataFrame([planning_info])
        st.subheader("Visibility Window")
        plot_visibility_timeline(df_plan, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None)
    except Exception:
        pass

    # Chart
    st.subheader("Altitude vs Time")

    chart_data = df.copy()
    chart_data["Local Time"] = pd.to_datetime(chart_data["Local Time"])

    chart = alt.Chart(chart_data).mark_line(point=True).encode(
        x=alt.X('Local Time', axis=alt.Axis(format='%H:%M')),
        y=alt.Y('Altitude (°)'),
        tooltip=[alt.Tooltip('Local Time', format='%Y-%m-%d %H:%M'), 'Altitude (°)', 'Azimuth (°)', 'Direction']
    ).interactive()
    
    st.altair_chart(chart, width='stretch')

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