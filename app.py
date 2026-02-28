import streamlit as st
import warnings
import sys
import yaml
import json
import os
import math
import pandas as pd
import geocoder
import pytz
from concurrent.futures import ThreadPoolExecutor
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
    from streamlit_js_eval import get_geolocation, streamlit_js_eval as _ss_js
except ImportError:
    get_geolocation = None  # optional: GPS checkbox in sidebar disabled without this
    _ss_js = None           # optional: sessionStorage location persistence disabled without this

try:
    from streamlit_searchbox import st_searchbox
except ImportError:
    st_searchbox = None     # optional: address autocomplete falls back to plain text_input

try:
    from github import Github
except ImportError:
    Github = None           # optional: admin panel GitHub sync disabled without this

# Import from local modules
from backend.resolvers import resolve_simbad, resolve_horizons, get_horizons_ephemerides, resolve_planet, get_planet_ephemerides
from backend.core import compute_trajectory, calculate_planning_info, azimuth_to_compass, moon_sep_deg, compute_peak_alt_in_window
from backend.scrape import scrape_unistellar_table, scrape_unistellar_priority_comets, scrape_unistellar_priority_asteroids
from backend.github import create_issue as _gh_create_issue

# Suppress Astropy warnings about coordinate frame transformations (Geocentric vs Topocentric)
warnings.filterwarnings("ignore", message=".*transforming other coordinates.*")

# ── App-wide configuration constants ──────────────────────────────────────
CONFIG = {
    # Gantt chart sizing
    "gantt_row_height":    60,    # px per row
    "gantt_min_height":   250,    # minimum chart height px
    # Sidebar defaults
    "default_alt_min":     20,    # altitude filter lower bound
    "default_session_hour":18,    # default observation start hour
    "default_dur_idx":      8,    # duration selectbox default index (720 min)
}

from backend.app_logic import (
    _AZ_OCTANTS, _AZ_LABELS, _AZ_CAPTIONS, az_in_selected,
    get_moon_status, _check_row_observability,
    _sort_df_like_chart, build_night_plan,
    _sanitize_csv_df, _add_peak_alt_session,
    _apply_night_plan_filters,
)


st.set_page_config(page_title="AstroPlanner", page_icon="🔭", layout="wide", initial_sidebar_state="expanded")

def _location_needed():
    """Consistent placeholder shown in every section that requires a location."""
    st.info("📍 Set your location in the sidebar to see results here.")

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
    except Exception:
        moon_loc = None
        moon_illum = 0
    
    data = []
    for p_name, p_id in planet_map.items():
        try:
            _, sky_coord = resolve_planet(p_id, obs_time_str=obs_time_str)
            details = calculate_planning_info(sky_coord, location, start_time)
            
            moon_sep = 0.0
            if moon_loc:
                moon_sep = moon_sep_deg(sky_coord, moon_loc)
            
            row = {
                "Name": p_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": sky_coord.dec.degree,
                "_ra_deg":  sky_coord.ra.deg,
                "Moon Sep (°)": round(moon_sep, 1) if moon_loc else 0,
                "Moon Status": get_moon_status(moon_illum, moon_sep) if moon_loc else "",
            }
            row.update(details)
            data.append(row)
        except Exception:
            continue
    return pd.DataFrame(data)

def plot_visibility_timeline(df, obs_start=None, obs_end=None, default_sort_label="Default Order", priority_col=None):
    """Generates a Gantt-style chart showing Rise to Set times.

    obs_start / obs_end: naive local datetimes for the observation window overlay.
    When provided, a shaded region + dashed start/end lines are drawn on the chart.

    default_sort_label: label for the third sort radio option (e.g. "Default Order",
        "Priority Order"). Defaults to "Default Order".
    priority_col: if provided, the "Priority Order" sort will place rows with a
        non-empty value in this column first (ranked URGENT > HIGH > LOW > other),
        then remaining rows in their natural order.
    """
    # Filter for objects with valid rise/set times
    chart_data = df.dropna(subset=['_rise_datetime', '_set_datetime']).copy()

    if chart_data.empty:
        return None

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
        ["Earliest Set", "Earliest Rise", "Earliest Transit", default_sort_label],
        horizontal=True,
        label_visibility="collapsed"
    )

    # For Earliest Rise / Earliest Set: Always Up objects move to the bottom
    # (sorted by earliest transit), regular objects sort by the chosen criterion.
    # For Default/Priority Order: preserve original data order (with optional
    # priority-column ranking when priority_col is supplied).
    _au_mask = chart_data['Status'].str.contains('Always Up', na=False)
    _au_df = chart_data[_au_mask]
    _reg_df = chart_data[~_au_mask]

    # Always Up group — sorted by earliest transit time
    if '_transit_naive' in _au_df.columns:
        _au_sorted_names = _au_df.sort_values('_transit_naive', ascending=True, na_position='last')['Name'].tolist()
    else:
        _au_sorted_names = _au_df['Name'].tolist()

    if sort_option == "Earliest Rise":
        _reg_sorted_names = _reg_df.sort_values('_rise_naive', ascending=True)['Name'].tolist()
        sort_arg = _reg_sorted_names + _au_sorted_names   # Always Up at bottom
    elif sort_option == "Earliest Set":
        _reg_sorted_names = _reg_df.sort_values('_set_naive', ascending=True)['Name'].tolist()
        sort_arg = _reg_sorted_names + _au_sorted_names   # Always Up at bottom
    elif sort_option == "Earliest Transit":
        _reg_sorted_names = _reg_df.sort_values('_transit_naive', ascending=True, na_position='last')['Name'].tolist()
        sort_arg = _reg_sorted_names + _au_sorted_names   # Always Up at bottom
    else:  # Default Order / Priority Order — preserve source order, optionally rank by priority
        if priority_col and priority_col in chart_data.columns:
            _PRI_RANK = {"URGENT": 0, "HIGH": 1, "LOW": 2}

            def _rank_priority(val):
                v = str(val).upper() if pd.notna(val) else ""
                for k, r in _PRI_RANK.items():
                    if k in v:
                        return r
                if v.strip():
                    return 3  # has some priority label (e.g. ⭐ PRIORITY)
                return 4      # no priority assigned

            _tmp = chart_data.copy()
            _tmp['_sort_rank'] = _tmp[priority_col].apply(_rank_priority)
            sort_arg = _tmp.sort_values('_sort_rank', kind='mergesort')['Name'].tolist()
        else:
            sort_arg = list(chart_data['Name'])

    # Dynamic height: Ensure minimum height to prevent clipping of axis/title
    row_height = CONFIG["gantt_row_height"]
    chart_height = max(len(chart_data) * row_height, CONFIG["gantt_min_height"])

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
            color='#ffd700', fontSize=9, dy=-20, align='center', fontWeight='bold'
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
            alt.Chart(obs_df).mark_rect(opacity=0.15, color='#5588ff').encode(
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

    return sort_option



# GITHUB_TOKEN must be a fine-grained PAT with:
#   - Contents: Read and Write  (to push YAML file updates)
#   - Issues: Write             (to create admin notification issues)
# Do NOT use a classic token with full repo or admin scopes.
def _send_github_notification(title, body):
    """Creates a GitHub Issue to notify admin. Reusable across all sections."""
    try:
        _gh_create_issue(
            st.secrets.get("GITHUB_TOKEN"),
            st.secrets.get("GITHUB_REPO"),
            title,
            body,
        )
    except Exception as e:
        print(f"Failed to send notification: {e}")


def _notify_jpl_failure(name, jpl_id_tried, error_msg):
    """Fire a GitHub Issue for a JPL resolution failure — once per session per name."""
    notified = st.session_state.setdefault("_jpl_notified", set())
    if name in notified:
        return
    notified.add(name)
    title = f"⚠️ JPL resolution failed: {name}"
    body = (
        f"**Object:** `{name}`\n"
        f"**JPL ID tried:** `{jpl_id_tried}`\n"
        f"**Error:** {error_msg}\n\n"
        f"Set a permanent fix in `jpl_id_overrides.yaml` or use the admin panel override.\n"
    )
    _send_github_notification(title, body)



def generate_plan_pdf(df_plan, night_start, night_end,
                      target_col, link_col, dur_col, pri_col, ra_col, dec_col,
                      vmag_col=None):
    """Return PDF bytes of the night plan with priority-coloured rows and
    clickable deeplinks, or None if reportlab is not installed."""
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors as rl_colors
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                        Paragraph, Spacer)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
    except ImportError:
        return None

    import io

    _PRI_BG = {
        "URGENT": rl_colors.HexColor('#ef5350'),
        "HIGH":   rl_colors.HexColor('#ffb74d'),
        "MEDIUM": rl_colors.HexColor('#fff59d'),
        "LOW":    rl_colors.HexColor('#c8e6c9'),
    }
    _PRI_FG = {
        "URGENT": rl_colors.white,
        "HIGH":   rl_colors.black,
        "MEDIUM": rl_colors.black,
        "LOW":    rl_colors.black,
    }

    def _pri_colors(val):
        v = str(val).upper().strip()
        for k in _PRI_BG:
            if k in v:
                return _PRI_BG[k], _PRI_FG[k]
        return None, rl_colors.black

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        rightMargin=1.2*cm, leftMargin=1.2*cm,
        topMargin=1.5*cm,  bottomMargin=1.2*cm,
    )
    styles = getSampleStyleSheet()
    title_s = ParagraphStyle('t', parent=styles['Title'], fontSize=15, spaceAfter=4)
    sub_s   = ParagraphStyle('s', parent=styles['Normal'], fontSize=9,
                              spaceAfter=10, textColor=rl_colors.grey)
    hdr_s   = ParagraphStyle('h', parent=styles['Normal'], fontSize=8,
                              fontName='Helvetica-Bold')
    cell_s  = ParagraphStyle('c', parent=styles['Normal'], fontSize=7)
    link_s  = ParagraphStyle('l', parent=styles['Normal'], fontSize=7,
                              textColor=rl_colors.HexColor('#1565C0'))
    name_link_s = ParagraphStyle(
        'nl', parent=styles['Normal'], fontSize=7,
        textColor=rl_colors.HexColor('#1565C0'),
        underlineWidth=0.5,
    )

    elems = [
        Paragraph("Night Observation Plan", title_s),
        Paragraph(
            f"Session: {night_start.strftime('%Y-%m-%d %H:%M')} → "
            f"{night_end.strftime('%H:%M')} local  |  "
            f"{len(df_plan)} target{'s' if len(df_plan) != 1 else ''} scheduled",
            sub_s,
        ),
    ]

    # Re-detect the link column directly from df_plan so the column is never
    # missed even if the caller passes link_col=None.
    _link_col = next(
        (c for c in df_plan.columns if 'link' in c.lower()),
        link_col,
    )

    # Column order for the PDF
    display_cols = ['#']
    for c in [target_col, pri_col, 'Type',
              'Rise', 'Transit', 'Set', dur_col,
              vmag_col, ra_col, dec_col, 'Constellation',
              'Status', 'Peak Alt (°)', 'Moon Sep (°)', 'Moon Status']:
        if c and c in df_plan.columns and c not in display_cols:
            display_cols.append(c)

    # Column widths in cm — tuned to fit landscape A4 (~27 cm usable)
    _W = {
        '#': 0.6,
        target_col: 3.2, pri_col: 1.5, 'Type': 1.2,
        'Rise': 1.6, 'Transit': 1.6, 'Set': 1.6,
        dur_col: 1.2, vmag_col: 1.0, ra_col: 1.9, dec_col: 1.7,
        'Constellation': 1.6, 'Status': 1.7, 'Peak Alt (°)': 1.2, 'Moon Sep (°)': 1.6, 'Moon Status': 1.4,
    }
    col_widths = [_W.get(c, 1.5) * cm for c in display_cols]

    # Header row
    data = [[Paragraph(c, hdr_s) for c in display_cols]]

    for i, (_, row) in enumerate(df_plan.iterrows()):
        cells = []
        for col in display_cols:
            if col == '#':
                cells.append(Paragraph(str(i + 1), cell_s))
            elif col == target_col:
                name_val = str(row.get(col, '') or '')
                url = str(row.get(_link_col, '') or '') if _link_col else ''
                if url:
                    safe_url = url.replace('&', '&amp;')
                    cells.append(Paragraph(
                        f'<link href="{safe_url}">{name_val}</link>',
                        name_link_s,
                    ))
                else:
                    cells.append(Paragraph(name_val, cell_s))
            elif col == dur_col:
                try:
                    cells.append(Paragraph(f"{float(row.get(col, 0)):.1f} min", cell_s))
                except Exception:
                    cells.append(Paragraph(str(row.get(col, '')), cell_s))
            elif col == 'Peak Alt (°)':
                try:
                    cells.append(Paragraph(f"{float(row.get(col, '')):.0f}°", cell_s))
                except Exception:
                    cells.append(Paragraph('—', cell_s))
            else:
                cells.append(Paragraph(str(row.get(col, '') or ''), cell_s))
        data.append(cells)

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    ts = TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0), rl_colors.HexColor('#4472C4')),
        ('TEXTCOLOR',     (0, 0), (-1, 0), rl_colors.white),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1),
         [rl_colors.HexColor('#f5f5f5'), rl_colors.white]),
        ('GRID',          (0, 0), (-1, -1), 0.4, rl_colors.HexColor('#cccccc')),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ])
    for i, (_, row) in enumerate(df_plan.iterrows()):
        bg, fg = _pri_colors(row.get(pri_col, '') if pri_col else '')
        if bg:
            ts.add('BACKGROUND', (0, i + 1), (-1, i + 1), bg)
            ts.add('TEXTCOLOR',  (0, i + 1), (-1, i + 1), fg)
    tbl.setStyle(ts)

    elems.append(tbl)
    elems.append(Spacer(1, 0.5 * cm))
    footer_s = ParagraphStyle('f', parent=styles['Normal'], fontSize=7,
                               textColor=rl_colors.grey)
    elems.append(Paragraph(
        f"Generated by Astro Coordinates Planner • "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')} • "
        "Tip: Load this PDF before connecting your telescope to WiFi.",
        footer_s,
    ))

    doc.build(elems)
    buf.seek(0)
    return buf.getvalue()


def _render_night_plan_builder(
    df_obs, start_time, night_plan_start, night_plan_end, local_tz,
    target_col="Name", ra_col="RA", dec_col="Dec",
    pri_col=None, dur_col=None, vmag_col=None,
    type_col=None, disc_col=None, link_col=None,
    csv_label="All Targets (CSV)", csv_data=None,
    csv_filename="targets.csv", section_key="",
    duration_minutes=None,
    location=None,
    min_alt=0,
    min_moon_sep=0,
    az_dirs=None,
):
    """Render a Night Plan Builder UI inside an already-open st.expander.

    Adapts filter layout to available columns — sections with fewer data
    columns get fewer filter widgets. All sections get Set-time and Moon
    Status filters at minimum.
    """
    # ── Detect actual priority levels from data ─────────────────────
    _has_pri = pri_col and pri_col in df_obs.columns
    _pri_options = []
    if _has_pri:
        _raw_vals = df_obs[pri_col].dropna().astype(str).str.strip()
        _raw_vals = _raw_vals[_raw_vals != '']
        _unique_pri = sorted(_raw_vals.unique().tolist())
        _pri_options = _unique_pri + ["(unassigned)"]

    # Caption rendered after sort radio (see Row 2b below)

    # ── Row 1: priority (only if priority column exists) ──────────────
    _sel_pri = None
    if _has_pri and _pri_options:
        _sel_pri = st.multiselect(
            "Include priority levels:",
            options=_pri_options,
            default=_pri_options,
            key=f"{section_key}_pri",
            help=(
                "Deselect a level to exclude those targets.  "
                "'(unassigned)' catches targets with no priority label."
            ),
        )

    # ── Row 2: adaptive filter columns (split into two rows if 4+) ───
    st.caption("**Refine candidate pool** *(optional — defaults include everything)*")
    _has_vmag = vmag_col and not df_obs.empty and vmag_col in df_obs.columns
    _has_type = type_col and not df_obs.empty and type_col in df_obs.columns
    _has_disc = disc_col and not df_obs.empty and disc_col in df_obs.columns
    _has_moon = 'Moon Status' in df_obs.columns

    _vmag_range = None
    _sel_types = None
    _disc_days = None
    _sel_moon = None
    _all_moon_statuses = ["🌑 Dark Sky", "✅ Safe", "⚠️ Caution", "⛔ Avoid"]

    # Count data-specific filters (before the always-present set_time + moon)
    _data_filters = []
    if _has_vmag:
        _data_filters.append("vmag")
    if _has_type:
        _data_filters.append("type")
    if _has_disc:
        _data_filters.append("disc")

    # Row 2a: data-specific filters (magnitude, type, discovery) — own row
    if _data_filters:
        _row_a = st.columns(len(_data_filters))
        _col_idx = 0
        if _has_vmag:
            with _row_a[_col_idx]:
                _vmag_numeric = pd.to_numeric(df_obs[vmag_col], errors='coerce').dropna()
                if not _vmag_numeric.empty:
                    _vmag_lo = round(float(_vmag_numeric.min()), 1)
                    _vmag_hi = round(float(_vmag_numeric.max()), 1)
                    if _vmag_lo < _vmag_hi:
                        # Reset stale non-tuple state (same guard as session window slider).
                        # A persisted scalar would cause _apply_night_plan_filters to crash
                        # at vmag_range[0] with TypeError: 'float' is not subscriptable.
                        _vmag_ss_key = f"{section_key}_vmag"
                        if not isinstance(st.session_state.get(_vmag_ss_key), (tuple, list)):
                            st.session_state.pop(_vmag_ss_key, None)
                        _vmag_range = st.slider(
                            f"Magnitude ({vmag_col})",
                            min_value=_vmag_lo,
                            max_value=_vmag_hi,
                            value=(_vmag_lo, _vmag_hi),
                            step=0.1,
                            key=_vmag_ss_key,
                            help="Lower magnitude = brighter.",
                        )
                    else:
                        st.caption(f"Mag {_vmag_lo} (all targets same — filter unavailable)")
            _col_idx += 1
        if _has_type:
            with _row_a[_col_idx]:
                _all_types = sorted(
                    df_obs[type_col].dropna().astype(str).unique().tolist()
                )
                _sel_types = st.multiselect(
                    f"Type ({type_col})",
                    options=_all_types,
                    default=_all_types,
                    key=f"{section_key}_type",
                    help="Filter by object type or event class.",
                )
            _col_idx += 1
        if _has_disc:
            with _row_a[_col_idx]:
                _disc_days = st.slider(
                    "Discovered within last N days",
                    min_value=1, max_value=365, value=365,
                    key=f"{section_key}_disc",
                    help="365 = no restriction. Lower to focus on fresh events.",
                )

    # Moon Status (before observation window)
    if _has_moon:
        _sel_moon = st.multiselect(
            "Moon Status",
            options=_all_moon_statuses,
            default=_all_moon_statuses,
            key=f"{section_key}_moon",
            help="Deselect '⛔ Avoid' to exclude targets too close to the Moon.",
        )

    # Row 2b: unified night session window slider
    _st_naive = start_time.replace(tzinfo=None)
    # Round sidebar start down to nearest 30 min for slider alignment.
    _st_rounded = _st_naive.replace(
        minute=(_st_naive.minute // 30) * 30, second=0, microsecond=0
    )
    # Slider min: earlier of sidebar time or 18:00 night start, so pre-18:00
    # sidebar times (e.g. 14:30) aren't silently clamped to 18:00.
    _slider_min = min(_st_rounded, night_plan_start)
    _slider_default_start = min(_st_rounded, night_plan_end - timedelta(minutes=30))
    # Default right handle: start + imaging duration (from sidebar) capped at
    # night_plan_end, so the slider pre-fills the user's stated session window.
    if duration_minutes:
        _slider_default_end = min(
            _st_naive.replace(second=0, microsecond=0) + timedelta(minutes=duration_minutes),
            night_plan_end,
        )
    else:
        _slider_default_end = night_plan_end

    # Sync slider to sidebar: when the sidebar time OR duration changes, reset
    # the stored slider value so the handles track the new sidebar values.
    # Always manage state via session_state — never pass value= to st.slider
    # alongside a manual st.session_state assignment (causes Streamlit warning).
    _ss_key = f"{section_key}_win_range"
    _last_key = f"{section_key}_last_start"
    _last_dur_key = f"{section_key}_last_dur"
    if (st.session_state.get(_last_key) != _st_rounded
            or st.session_state.get(_last_dur_key) != duration_minutes
            or not isinstance(st.session_state.get(_ss_key), (tuple, list))):
        st.session_state[_last_key] = _st_rounded
        st.session_state[_last_dur_key] = duration_minutes
        st.session_state[_ss_key] = (_slider_default_start, _slider_default_end)

    _win_range = st.slider(
        "Session window",
        min_value=_slider_min,
        max_value=night_plan_end,
        step=timedelta(minutes=30),
        format="MMM DD HH:mm",
        key=_ss_key,
        help="Drag the handles to set the start and end of your observing session.",
    )
    _win_start_dt = local_tz.localize(_win_range[0])
    _win_end_dt = local_tz.localize(_win_range[1])
    _win_hours = max(0, int((_win_end_dt - _win_start_dt).total_seconds() / 3600))
    st.caption(
        f"Window: **{_win_range[0].strftime('%b %d %H:%M')}** → "
        f"**{_win_range[1].strftime('%b %d %H:%M')}** — **{_win_hours} hrs**"
    )

    # Sort radio
    _sort_by = st.radio(
        "Sort plan by",
        options=["Set Time", "Transit Time"],
        index=0,
        horizontal=True,
        key=f"{section_key}_sortby",
        help="Order the planned targets by when they set or when they transit.",
    )

    # Dynamic caption — rendered after radio so it reflects the live choice
    if _sort_by == "Set Time":
        st.caption(
            "Plan includes targets visible during the observation window, "
            "sorted by **Set Time** — targets that set soonest appear first."
        )
    else:
        st.caption(
            "Plan includes targets visible during the observation window, "
            "sorted by **Transit Time** — targets that transit soonest appear first."
        )

    # ── Parameters summary ─────────────────────────────────────────────
    _summary_parts = [
        (f"Window: {_win_range[0].strftime('%b %d %H:%M')} → "
         f"{_win_range[1].strftime('%b %d %H:%M')} ({_win_hours} hrs)"),
        f"Min alt: {min_alt}°",
        f"Moon sep: ≥ {min_moon_sep}°",
    ]
    if az_dirs:
        _az_ordered = [d for d in _AZ_LABELS if d in az_dirs]
        _summary_parts.append(f"Az: {', '.join(_az_ordered)}")
    if pri_col and _sel_pri:
        _summary_parts.append(f"Priority: {', '.join(_sel_pri)}")
    if _sel_moon is not None and len(_sel_moon) < len(_all_moon_statuses):
        _summary_parts.append(f"Moon: {', '.join(_sel_moon)}")
    st.info("📋 " + "  •  ".join(_summary_parts))

    # ── Row 3: action buttons ─────────────────────────────────────────
    _bc1, _bc2 = st.columns(2)
    with _bc1:
        _do_build = st.button(
            "🗓 Build Plan", type="primary", use_container_width=True,
            key=f"{section_key}_build",
        )
    with _bc2:
        _csv_src = csv_data if csv_data is not None else df_obs
        st.download_button(
            csv_label,
            data=_sanitize_csv_df(_csv_src).to_csv(index=False).encode('utf-8'),
            file_name=csv_filename,
            mime="text/csv",
            use_container_width=True,
            key=f"{section_key}_csv_all",
            help="Download the full unfiltered target list as CSV.",
        )

    if _do_build:
        if df_obs.empty:
            st.warning("No observable targets to plan.")
        else:
            _plan_src = _apply_night_plan_filters(
                df=df_obs,
                pri_col=pri_col,        sel_pri=_sel_pri,
                vmag_col=vmag_col,      vmag_range=_vmag_range,
                type_col=type_col,      sel_types=_sel_types,
                disc_col=disc_col,      disc_days=_disc_days,
                win_start_dt=_win_start_dt, win_end_dt=_win_end_dt,
                sel_moon=_sel_moon,     all_moon_statuses=_all_moon_statuses,
                location=location,
                min_alt=min_alt,
            )

            if _plan_src.empty:
                st.warning("No observable targets match the selected filters.")
            else:
                _scheduled = build_night_plan(
                    _plan_src,
                    sort_by='transit' if _sort_by == 'Transit Time' else 'set',
                )

                if _scheduled.empty:
                    st.warning("No targets matched after sorting.")
                else:
                    st.metric("Targets Planned", len(_scheduled))

                    _plan_link_col = next(
                        (c for c in _scheduled.columns if 'link' in c.lower()),
                        link_col,
                    )

                    # Expose peak-alt column for display
                    _peak_alt_display_col = None
                    if '_peak_alt_window' in _scheduled.columns:
                        _peak_alt_display_col = 'Peak Alt (°)'
                        _scheduled = _scheduled.rename(
                            columns={'_peak_alt_window': 'Peak Alt (°)'}
                        )

                    # Build display column list
                    _plan_show = []
                    for _c in [target_col, pri_col, 'Type',
                               'Rise', 'Transit', 'Set', dur_col,
                               vmag_col, ra_col, dec_col, 'Constellation',
                               'Status', 'Peak Alt (°)', 'Moon Sep (°)', 'Moon Status',
                               _plan_link_col]:
                        if _c and _c in _scheduled.columns and _c not in _plan_show:
                            _plan_show.append(_c)
                    _plan_display = _scheduled[
                        [c for c in _plan_show if c in _scheduled.columns]
                    ].copy()

                    # Column config
                    _plan_cfg = {}
                    if dur_col and dur_col in _plan_display.columns:
                        _plan_cfg[dur_col] = st.column_config.NumberColumn(
                            dur_col, format="%d min"
                        )
                    if _peak_alt_display_col and _peak_alt_display_col in _plan_display.columns:
                        _plan_cfg[_peak_alt_display_col] = st.column_config.NumberColumn(
                            'Peak Alt (°)', format="%.0f°"
                        )
                    if 'Moon Sep (°)' in _plan_display.columns:
                        _plan_cfg['Moon Sep (°)'] = st.column_config.TextColumn("Moon Sep (°)")
                    if 'Moon Status' in _plan_display.columns:
                        _plan_cfg['Moon Status'] = st.column_config.TextColumn("Moon Status")
                    if _plan_link_col and _plan_link_col in _plan_display.columns:
                        _plan_cfg[_plan_link_col] = st.column_config.LinkColumn(
                            "🔭 Open", display_text="🔭 Open"
                        )

                    # Priority row colouring
                    if pri_col and pri_col in _plan_display.columns:
                        def _plan_hl(row):
                            v = str(row[pri_col]).upper().strip()
                            if "URGENT" in v:
                                s = "background-color:#ef5350;color:white;font-weight:bold"
                            elif "HIGH" in v:
                                s = "background-color:#ffb74d;color:black;font-weight:bold"
                            elif "MEDIUM" in v:
                                s = "background-color:#fff59d;color:black"
                            elif "LOW" in v:
                                s = "background-color:#c8e6c9;color:black"
                            else:
                                s = ""
                            return [s] * len(row)
                        st.dataframe(
                            _plan_display.style.apply(_plan_hl, axis=1),
                            hide_index=True, width="stretch",
                            column_config=_plan_cfg,
                        )
                    else:
                        st.dataframe(
                            _plan_display, hide_index=True,
                            width="stretch", column_config=_plan_cfg,
                        )

                    # Export buttons
                    _dl1, _dl2 = st.columns(2)
                    with _dl1:
                        st.download_button(
                            "📥 Download Plan (CSV)",
                            data=_sanitize_csv_df(
                                _plan_display
                            ).to_csv(index=False).encode('utf-8'),
                            file_name=f"night_plan_{start_time.strftime('%Y%m%d_%H%M')}.csv",
                            mime="text/csv",
                            use_container_width=True,
                            key=f"{section_key}_csv_plan",
                        )
                    with _dl2:
                        _pdf = generate_plan_pdf(
                            _scheduled, _win_start_dt, _win_end_dt,
                            target_col, _plan_link_col, dur_col, pri_col,
                            ra_col, dec_col, vmag_col,
                        )
                        if _pdf:
                            st.download_button(
                                "📄 Download Plan (PDF)",
                                data=_pdf,
                                file_name=f"night_plan_{start_time.strftime('%Y%m%d_%H%M')}.pdf",
                                mime="application/pdf",
                                use_container_width=True,
                                key=f"{section_key}_pdf_plan",
                                help="PDF export of the plan.",
                            )
                        else:
                            st.info("Install `reportlab` to enable PDF export.")


COMETS_FILE = "comets.yaml"
COMET_PENDING_FILE = "comet_pending_requests.txt"
COMET_CATALOG_FILE = "comets_catalog.json"

# Standard column display configs reused across all sections
_MOON_SEP_COL_CONFIG = {
    "Moon Sep (°)": st.column_config.TextColumn("Moon Sep (°)"),
    "Moon Status": st.column_config.TextColumn("Moon Status"),
    "_dec_deg": st.column_config.NumberColumn("Dec", format="%+.2f°"),
    "_peak_alt_session": st.column_config.NumberColumn(
        "Peak Alt (session)",
        format="%.0f°",
        help="Highest altitude this object reaches during your observation window "
             "(sidebar Start Time + Duration). Sampled at 5 points.",
    ),
}

# Aliases for comets that appear under alternate designations on external pages
COMET_ALIASES = {
    "3I/ATLAS": "C/2025 N1 (ATLAS)",
}

JPL_OVERRIDES_FILE = "jpl_id_overrides.yaml"
JPL_CACHE_FILE = "jpl_id_cache.json"


@st.cache_data(ttl=3600, show_spinner=False)
def _load_jpl_overrides():
    """Load jpl_id_overrides.yaml (cached 1h). Call .clear() after admin saves an override."""
    from backend.config import read_jpl_overrides
    return read_jpl_overrides(JPL_OVERRIDES_FILE)


def _load_jpl_cache():
    """Load jpl_id_cache.json — NOT st.cache_data so it reflects live writes."""
    from backend.config import read_jpl_cache
    return read_jpl_cache(JPL_CACHE_FILE)


EPHEMERIS_CACHE_FILE = "ephemeris_cache.json"

@st.cache_data(ttl=3600, show_spinner=False)
def _load_ephemeris_cache():
    """Load pre-computed ephemeris_cache.json (cached 1h). Returns {} if missing."""
    from backend.config import read_ephemeris_cache
    return read_ephemeris_cache(EPHEMERIS_CACHE_FILE)


def _save_jpl_cache_entry(section, name, jpl_id):
    """Persist a newly SBDB-resolved JPL ID to jpl_id_cache.json.

    Guards against SBDB internal-format SPK-IDs in [20M, 30M) which JPL
    Horizons rejects.  SBDB returns 20000000 + catalog_number for numbered
    bodies (e.g. 20000433 for Eros, 20015091 for 88P/Howell).  Caching
    these causes every subsequent batch query to fail.

    Valid ID ranges we keep:
      - Numbered bodies:    < 10_000_000  (1, 433, 99942 …)
      - Comet SPK-IDs:      ~1_003_000 – 1_004_999  (1003861, 1004111 …)
      - Fragment IDs:       ~90_000_000+  (90001202, 90001203 …)
    """
    try:
        _id_int = int(jpl_id)
        if 20_000_000 <= _id_int < 30_000_000:
            return  # SBDB internal ID — Horizons rejects these; skip caching
    except (ValueError, TypeError):
        pass  # non-numeric IDs (designations like "C/2025 F2", "3I") are fine
    from backend.config import read_jpl_cache, write_jpl_cache
    cache = read_jpl_cache(JPL_CACHE_FILE)
    cache.setdefault(section, {})[name] = jpl_id
    write_jpl_cache(JPL_CACHE_FILE, cache)


def _dedup_by_jpl_id(names, id_fn):
    """Return names list with duplicates removed by resolved JPL ID (first occurrence wins)."""
    seen, out = set(), []
    for name in names:
        jid = id_fn(name)
        if jid not in seen:
            seen.add(jid)
            out.append(name)
    return out


def _resolve_comet_alias(name):
    """Returns canonical name (from COMET_ALIASES) and uppercases for comparison."""
    return COMET_ALIASES.get(name, name).upper()


def _get_comet_jpl_id(name):
    """Three-layer JPL ID lookup for comets.
    1. jpl_id_overrides.yaml  (admin-committed permanent fixes, cached 1h)
    2. jpl_id_cache.json      (SBDB auto-resolved at runtime)
    3. Strip parenthetical    (e.g. 'C/2025 N1 (ATLAS)' → 'C/2025 N1')
    """
    overrides = _load_jpl_overrides()
    if name in overrides.get("comets", {}):
        return overrides["comets"][name]
    cache = _load_jpl_cache()
    if name in cache.get("comets", {}):
        return cache["comets"][name]
    return name.split('(')[0].strip()


@st.cache_data(ttl=3600, show_spinner=False)
def load_comets_config():
    from backend.config import read_comets_config
    return read_comets_config(COMETS_FILE)


@st.cache_data(ttl=3600, show_spinner=False)
def load_comet_catalog():
    """Loads the MPC comet catalog snapshot for Explore Catalog mode.
    Returns (updated_str, entries_list) or (None, []) if not downloaded yet."""
    from backend.config import read_comet_catalog
    return read_comet_catalog(COMET_CATALOG_FILE)


def save_comets_config(config):
    load_comets_config.clear()          # invalidate cache after write
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
            st.error(f"GitHub Sync Error: {e}")  # admin panel — full error OK


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
    # --- Thread-safe: load @st.cache_data maps BEFORE spawning workers ---
    _overrides = _load_jpl_overrides()   # @st.cache_data — safe here (main thread)
    _jpl_cache = _load_jpl_cache()       # plain file read, always safe
    _ephem = _load_ephemeris_cache()

    def _comet_id_local(name):
        """Resolve comet display name → JPL ID using pre-loaded maps (no Streamlit cache calls)."""
        if name in _overrides.get("comets", {}):
            return _overrides["comets"][name]
        if name in _jpl_cache.get("comets", {}):
            return _jpl_cache["comets"][name]
        return name.split('(')[0].strip()

    def _fetch(comet_name):
        import time as _time
        from backend.sbdb import sbdb_lookup
        from backend.config import lookup_cached_position

        # ── Fast path: use pre-computed ephemeris if available ──
        target_date = start_time.date().isoformat()   # e.g. "2026-03-05"
        cached_pos = lookup_cached_position(_ephem, "comets", comet_name, target_date)
        if cached_pos is not None:
            ra_deg, dec_deg = cached_pos
            sky_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame='icrs')
            details = calculate_planning_info(sky_coord, location, start_time)
            moon_sep = moon_sep_deg(sky_coord, moon_loc_inner) if moon_loc_inner else 0.0
            row = {
                "Name": comet_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": sky_coord.dec.degree,
                "_ra_deg":  sky_coord.ra.deg,
                "Moon Sep (°)": round(moon_sep, 1),
                "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
                "_jpl_id_used": "(ephemeris cache)",
            }
            row.update(details)
            return row

        # ── Fallback: live JPL query (date > 30 days out or object not in cache) ──
        jpl_id = _comet_id_local(comet_name)
        try:
            try:
                _, sky_coord = resolve_horizons(jpl_id, obs_time_str=obs_time_str)
            except Exception:
                _time.sleep(1.5)  # one retry after backoff — JPL rate-limits parallel requests
                _, sky_coord = resolve_horizons(jpl_id, obs_time_str=obs_time_str)
            details = calculate_planning_info(sky_coord, location, start_time)
            moon_sep = moon_sep_deg(sky_coord, moon_loc_inner) if moon_loc_inner else 0.0
            row = {
                "Name": comet_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": sky_coord.dec.degree,
                "_ra_deg":  sky_coord.ra.deg,
                "Moon Sep (°)": round(moon_sep, 1),
                "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
                "_jpl_id_used": jpl_id,
            }
            row.update(details)
            return row
        except Exception as first_exc:
            # Try full display name first, then stripped jpl_id
            sbdb_id = sbdb_lookup(comet_name)
            if sbdb_id is None and jpl_id != comet_name:
                sbdb_id = sbdb_lookup(jpl_id)
            if sbdb_id and sbdb_id != jpl_id:
                try:
                    _, sky_coord = resolve_horizons(sbdb_id, obs_time_str=obs_time_str)
                    _save_jpl_cache_entry("comets", comet_name, sbdb_id)
                    details = calculate_planning_info(sky_coord, location, start_time)
                    moon_sep = moon_sep_deg(sky_coord, moon_loc_inner) if moon_loc_inner else 0.0
                    row = {
                        "Name": comet_name,
                        "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                        "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                        "_dec_deg": sky_coord.dec.degree,
                        "_ra_deg":  sky_coord.ra.deg,
                        "Moon Sep (°)": round(moon_sep, 1),
                        "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
                        "_jpl_id_used": sbdb_id,
                    }
                    row.update(details)
                    return row
                except Exception:
                    pass
            # All resolution attempts failed — return stub row (never None)
            return {
                "Name": comet_name,
                "RA": "—", "Dec": "—", "_dec_deg": 0.0, "_ra_deg": 0.0,
                "Rise": "—", "Transit": "—", "Set": "—",
                "Status": "—", "Constellation": "—",
                "_rise_datetime": pd.NaT, "_set_datetime": pd.NaT, "_transit_datetime": pd.NaT,
                "Moon Sep (°)": "—", "Moon Status": "—",
                "_resolve_error": True,
                "_jpl_id_tried": jpl_id,
                "_jpl_id_used": jpl_id,
                "_jpl_error": str(first_exc)[:200],
            }

    deduped_comets = _dedup_by_jpl_id(list(comet_tuple), _comet_id_local)
    # Cap at 3 workers — JPL Horizons rate-limits aggressively under high concurrency;
    # sequential tests always pass, 8 parallel workers caused ~50% failures.
    with ThreadPoolExecutor(max_workers=max(1, min(len(deduped_comets), 3))) as executor:
        results = list(executor.map(_fetch, deduped_comets))
    return pd.DataFrame(results)   # every entry is a row — no filter(None)


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


def _build_priority_provisionals(priority_set):
    """Build a mapping of provisional designation → full YAML name.

    For entries like "162882 (2001 FD58)", extracts "2001 FD58" as a key.
    Used so scraped bare provisionals ("2001 FD58") match their numbered
    YAML counterparts without requiring a manual alias entry.
    """
    import re as _re
    _prov_re = _re.compile(r'\(([^)]+)\)')
    result = {}
    for name in priority_set:
        m = _prov_re.search(name)
        if m:
            result[m.group(1).upper()] = name
    return result


def _asteroid_priority_name(entry):
    return entry["name"] if isinstance(entry, dict) else entry


def _asteroid_jpl_id(name):
    """Three-layer JPL ID lookup for asteroids.
    1. jpl_id_overrides.yaml  (admin-committed permanent fixes, cached 1h)
    2. jpl_id_cache.json      (SBDB auto-resolved at runtime)
    3. Number-extraction logic (e.g. '433 Eros' → '433', '2001 FD58' stays as-is)
    """
    overrides = _load_jpl_overrides()
    if name in overrides.get("asteroids", {}):
        return overrides["asteroids"][name]
    cache = _load_jpl_cache()
    if name in cache.get("asteroids", {}):
        return cache["asteroids"][name]
    import re as _re
    if name and _re.match(r'^\d{4}\s+[A-Z]{1,2}\d', name):
        return name  # Provisional: e.g. '2001 FD58', '2001 SN263'
    if name and name[0].isdigit():
        return name.split(' ')[0]  # Numbered: '433 Eros' → '433'
    return name


@st.cache_data(ttl=3600, show_spinner=False)
def load_asteroids_config():
    from backend.config import read_asteroids_config
    return read_asteroids_config(ASTEROIDS_FILE)


def save_asteroids_config(config):
    load_asteroids_config.clear()       # invalidate cache after write
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
            st.error(f"GitHub Sync Error: {e}")  # admin panel — full error OK


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
    # --- Thread-safe: load @st.cache_data maps BEFORE spawning workers ---
    _overrides = _load_jpl_overrides()   # @st.cache_data — safe here (main thread)
    _jpl_cache = _load_jpl_cache()       # plain file read, always safe
    _ephem = _load_ephemeris_cache()

    def _asteroid_id_local(name):
        """Resolve asteroid display name → JPL ID using pre-loaded maps (no Streamlit cache calls)."""
        import re as _re
        if name in _overrides.get("asteroids", {}):
            return _overrides["asteroids"][name]
        if name in _jpl_cache.get("asteroids", {}):
            return _jpl_cache["asteroids"][name]
        if name and _re.match(r'^\d{4}\s+[A-Z]{1,2}\d', name):
            return name  # Provisional: e.g. '2001 FD58'
        if name and name[0].isdigit():
            return name.split(' ')[0]  # Numbered: '433 Eros' → '433'
        return name

    def _fetch(asteroid_name):
        import time as _time
        from backend.sbdb import sbdb_lookup
        from backend.config import lookup_cached_position

        # ── Fast path: use pre-computed ephemeris if available ──
        target_date = start_time.date().isoformat()
        cached_pos = lookup_cached_position(_ephem, "asteroids", asteroid_name, target_date)
        if cached_pos is not None:
            ra_deg, dec_deg = cached_pos
            sky_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame='icrs')
            details = calculate_planning_info(sky_coord, location, start_time)
            moon_sep = moon_sep_deg(sky_coord, moon_loc_inner) if moon_loc_inner else 0.0
            row = {
                "Name": asteroid_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": sky_coord.dec.degree,
                "_ra_deg":  sky_coord.ra.deg,
                "Moon Sep (°)": round(moon_sep, 1),
                "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
                "_jpl_id_used": "(ephemeris cache)",
            }
            row.update(details)
            return row

        # ── Fallback: live JPL query (date > 30 days out or object not in cache) ──
        jpl_id = _asteroid_id_local(asteroid_name)
        try:
            try:
                _, sky_coord = resolve_horizons(jpl_id, obs_time_str=obs_time_str)
            except Exception:
                _time.sleep(1.5)
                _, sky_coord = resolve_horizons(jpl_id, obs_time_str=obs_time_str)
            details = calculate_planning_info(sky_coord, location, start_time)
            moon_sep = moon_sep_deg(sky_coord, moon_loc_inner) if moon_loc_inner else 0.0
            row = {
                "Name": asteroid_name,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": sky_coord.dec.degree,
                "_ra_deg":  sky_coord.ra.deg,
                "Moon Sep (°)": round(moon_sep, 1),
                "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
                "_jpl_id_used": jpl_id,
            }
            row.update(details)
            return row
        except Exception as first_exc:
            sbdb_id = sbdb_lookup(asteroid_name)
            if sbdb_id is None and jpl_id != asteroid_name:
                sbdb_id = sbdb_lookup(jpl_id)
            if sbdb_id and sbdb_id != jpl_id:
                try:
                    _, sky_coord = resolve_horizons(sbdb_id, obs_time_str=obs_time_str)
                    _save_jpl_cache_entry("asteroids", asteroid_name, sbdb_id)
                    details = calculate_planning_info(sky_coord, location, start_time)
                    moon_sep = moon_sep_deg(sky_coord, moon_loc_inner) if moon_loc_inner else 0.0
                    row = {
                        "Name": asteroid_name,
                        "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                        "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                        "_dec_deg": sky_coord.dec.degree,
                        "_ra_deg":  sky_coord.ra.deg,
                        "Moon Sep (°)": round(moon_sep, 1),
                        "Moon Status": get_moon_status(moon_illum_inner, moon_sep) if moon_loc_inner else "",
                        "_jpl_id_used": sbdb_id,
                    }
                    row.update(details)
                    return row
                except Exception:
                    pass
            return {
                "Name": asteroid_name,
                "RA": "—", "Dec": "—", "_dec_deg": 0.0, "_ra_deg": 0.0,
                "Rise": "—", "Transit": "—", "Set": "—",
                "Status": "—", "Constellation": "—",
                "_rise_datetime": pd.NaT, "_set_datetime": pd.NaT, "_transit_datetime": pd.NaT,
                "Moon Sep (°)": "—", "Moon Status": "—",
                "_resolve_error": True,
                "_jpl_id_tried": jpl_id,
                "_jpl_id_used": jpl_id,
                "_jpl_error": str(first_exc)[:200],
            }

    deduped_asteroids = _dedup_by_jpl_id(list(asteroid_tuple), _asteroid_id_local)
    # Cap at 3 workers — JPL Horizons rate-limits aggressively under high concurrency.
    with ThreadPoolExecutor(max_workers=max(1, min(len(deduped_asteroids), 3))) as executor:
        results = list(executor.map(_fetch, deduped_asteroids))
    return pd.DataFrame(results)   # every entry is a row — no filter(None)


@st.cache_data(ttl=86400, show_spinner=False)
def get_unistellar_scraped_asteroids():
    """Fetches the current priority asteroid list from the Unistellar planetary defense page (cached 24h)."""
    try:
        return scrape_unistellar_priority_asteroids()
    except Exception:
        return []


DSO_FILE = "dso_targets.yaml"


@st.cache_data(ttl=3600, show_spinner=False)
def load_dso_config():
    """Load curated DSO catalog (Messier, Bright Stars, Astrophotography Favorites) from YAML."""
    from backend.config import read_dso_config
    return read_dso_config(DSO_FILE)


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
            moon_sep = moon_sep_deg(sky_coord, moon_loc_inner) if moon_loc_inner else 0.0
            row = {
                "Name": d_name,
                "Common Name": common_name,
                "Type": obj_type,
                "Magnitude": magnitude,
                "RA": sky_coord.ra.to_string(unit=u.hour, sep=('h ', 'm ', 's'), precision=0, pad=True),
                "Dec": sky_coord.dec.to_string(sep=('° ', "' ", '"'), precision=0, alwayssign=True, pad=True),
                "_dec_deg": dec_deg,
                "_ra_deg":  sky_coord.ra.deg,
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
            /* Hide GitHub source link from Streamlit Cloud header toolbar */
            header[data-testid="stHeader"] a[href*="github.com"],
            header[data-testid="stHeader"] a[aria-label*="GitHub"],
            header[data-testid="stHeader"] a[title*="GitHub"] {
                display: none !important;
            }
            /* Reduce metric font size */
            [data-testid="stMetricValue"] {
                font-size: 1.25rem !important;
            }
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

st.title("🔭 AstroPlanner")
st.markdown("Plan your astrophotography sessions with visibility predictions.")

with st.expander("ℹ️ How to Use"):
    st.markdown("""
    ### Setup (Sidebar)
    *   **Location:** Search for a city, use Browser GPS, or enter coordinates manually.
    *   **Time:** Set your observation start date and time.
    *   **Duration:** Choose how long you plan to image.
    *   **Filters:** Set Altitude range (Min/Max), Azimuth compass grid (N/NE/E/SE/S/SW/W/NW — check one or more directions to restrict to your visible sky, e.g. SE only for a south-east facing balcony; nothing checked = all 360°), and Moon Separation.

    ### 1. Choose Target
    Select one of the six modes:
    *   **🌌 Star/Galaxy/Nebula:** Browse the full Messier catalog, Bright Stars, or Astrophotography Favorites with batch visibility (Observable/Unobservable tabs + Gantt chart). Filter by object type. Select any target for a full trajectory, or use 'Custom Object...' to search SIMBAD for any object by name.
    *   **🪐 Planet:** Select a major planet.
    *   **☄️ Comet:** Two modes — **My List** (tracked comets with Unistellar priority highlights) or **Explore Catalog** (full MPC archive with orbit type and magnitude filters).
    *   **🪨 Asteroid:** Batch visibility for tracked asteroids with Unistellar Planetary Defense priority highlights.
    *   **💥 Cosmic Cataclysm:** Live alerts for transient events (novae, supernovae, variable stars). Report invalid/cancelled events or suggest priorities.
    *   **✍️ Manual:** Enter RA/Dec directly.

    ### 2. 📅 Night Plan Builder
    Inside the **Observable** tab, the **Night Plan Builder** is already open. Use it to plan your full night across all visible targets:
    *   **Session window** — Drag the range slider to set your imaging start and end. The slider spans the full night (18:00 tonight → 12:00 next morning); both handles show date and time (e.g. `Feb 27 22:00`). Step is 30 minutes.
    *   **Sort by Set Time or Transit Time** — controls plan order only.
    *   **Filter** by Moon Status, priority level, magnitude, event type, and discovery recency.
    *   **Export** as CSV or PDF (PDF is priority-colour-coded; Cosmic Cataclysm PDFs include `unistellar://` deeplinks).

    ### 3. Select Target for Trajectory
    Below the Observable/Unobservable tabs, pick any individual target from the dropdown to drill into its full altitude/azimuth trajectory for your session window.

    ### 4. Trajectory Results
    *   Click **🚀 Calculate Visibility**.
    *   View the **Altitude Chart** to see how the object moves across your window.
    *   Check **Moon separation** at each time step in the detailed data table.
    *   **Download CSV** for 10-minute step data.
    """)

def _init_session_state(now):
    """Initialize all session state keys that have not yet been set.
    Call once at the top of the sidebar block, after computing `now`."""
    ss = st.session_state
    # Location
    if "lat" not in ss:
        ss.lat = None
    if "lon" not in ss:
        ss.lon = None
    # Observation time
    if "selected_date" not in ss:
        ss["selected_date"] = now.date()
    if "selected_time" not in ss:
        if now.hour >= CONFIG["default_session_hour"] or now.hour < 6:
            # In the active observation window (6PM–6AM) → use current time
            ss["selected_time"] = now.time()
        else:
            ss["selected_time"] = now.replace(
                hour=CONFIG["default_session_hour"], minute=0, second=0, microsecond=0
            ).time()
    # Widget mirror keys (must match selected_* for initial render)
    if "_new_date" not in ss:
        ss["_new_date"] = ss["selected_date"]
    if "_new_time" not in ss:
        ss["_new_time"] = ss["selected_time"]
    # Session duration
    if "dur_idx" not in ss:
        ss.dur_idx = CONFIG["default_dur_idx"]
    # Azimuth filter
    for _d in _AZ_LABELS:
        if f"az_{_d}" not in ss:
            ss[f"az_{_d}"] = False


# ---------------------------
# SIDEBAR: Location & Time
# ---------------------------
st.sidebar.header("📍 Location & Time")

# 1. Location

def search_address():
    if st.session_state.addr_search:
        try:
            g = geocoder.arcgis(st.session_state.addr_search, timeout=10)
            if g.ok:
                st.session_state.lat = g.latlng[0]
                st.session_state.lon = g.latlng[1]
        except Exception:
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

# Restore location from sessionStorage (once per browser session)
if _ss_js and "_loc_loaded" not in st.session_state:
    _js_loc = _ss_js(
        js_expressions='JSON.stringify({lat: sessionStorage.getItem("astro_lat"), lon: sessionStorage.getItem("astro_lon")})',
        key="ss_read_loc",
        want_output=True,
    )
    if _js_loc is not None:
        st.session_state._loc_loaded = True
        try:
            _d = json.loads(_js_loc)
            _lat = float(_d["lat"]) if _d.get("lat") else 0.0
            _lon = float(_d["lon"]) if _d.get("lon") else 0.0
            _cur_lat = float(st.session_state.get("lat") or 0)
            _cur_lon = float(st.session_state.get("lon") or 0)
            if (_lat != 0.0 or _lon != 0.0) and (_cur_lat == 0.0 and _cur_lon == 0.0):
                st.session_state.lat = _lat
                st.session_state.lon = _lon
        except Exception:
            pass

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
    except Exception:
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
# Persist current location to sessionStorage whenever valid coordinates are present
if _ss_js and (lat != 0.0 or lon != 0.0):
    _ss_js(
        js_expressions=f'sessionStorage.setItem("astro_lat", "{lat}"); sessionStorage.setItem("astro_lon", "{lon}")',
        key="ss_write_loc",
        want_output=False,
    )

# 2. Timezone
tf = TimezoneFinder()
timezone_str = "UTC"
try:
    if lat is not None and lon is not None:
        timezone_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
except Exception:
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
    if now_local.hour >= CONFIG["default_session_hour"] or now_local.hour < 6:
        st.session_state.selected_time = now_local.time()
    else:
        st.session_state.selected_time = now_local.replace(hour=CONFIG["default_session_hour"], minute=0, second=0, microsecond=0).time()

    # Update widget keys to reflect changes immediately
    st.session_state['_new_date'] = st.session_state.selected_date
    st.session_state['_new_time'] = st.session_state.selected_time

# 3. Date & Time
st.sidebar.subheader("🕒 Observation Start")
now = datetime.now(local_tz)
_init_session_state(now)

def update_date():
    st.session_state.selected_date = st.session_state._new_date
def update_time():
    st.session_state.selected_time = st.session_state._new_time

selected_date = st.sidebar.date_input("Date", key='_new_date', on_change=update_date)
selected_time = st.sidebar.time_input("Time", key='_new_time', on_change=update_time)

# Combine to timezone-aware datetime
start_time = datetime.combine(st.session_state.selected_date, st.session_state.selected_time)
start_time = local_tz.localize(start_time)

# 4. Duration
st.sidebar.subheader("⏳ Duration")
st.sidebar.caption("Length of your imaging session starting from the time above.")

_duration_options_min = [60, 120, 180, 240, 300, 360, 480, 600, 720, 840, 960, 1080, 1200, 1320, 1440]

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

# Night plan window: 18:00 on the anchor date → 12:00 the next day (18-hour span).
# Anchor = yesterday when start_time is in the early-morning window (midnight–6AM),
# otherwise today. This ensures a midnight user sees last night's full evening session.
_night_anchor = (start_time - timedelta(days=1)).date() if start_time.hour < 6 else start_time.date()
_night_plan_start = datetime(_night_anchor.year, _night_anchor.month, _night_anchor.day, 18, 0)
_night_plan_end = _night_plan_start + timedelta(hours=18)  # → 12:00 next day

# 5. Observational Filters
st.sidebar.subheader("🔭 Observational Filters")
st.sidebar.caption("Applies to lists and visibility warnings.")
alt_range = st.sidebar.slider("Altitude Window (°)", 0, 90, (CONFIG["default_alt_min"], 90), help="Target must be within this altitude range (Min to Max).")
min_alt, max_alt = alt_range
# Compute az_dirs from session state before rendering so the status
# caption can appear directly under the heading (above the checkboxes).
az_dirs = {_d for _d in _AZ_LABELS if st.session_state.get(f"az_{_d}", False)}
_az_selected_count = len(az_dirs)
_az_status = (
    "📡 No filter — showing all 360°"
    if _az_selected_count == 0 or _az_selected_count == len(_AZ_LABELS)
    else f"📡 Filtering to: {', '.join(d for d in _AZ_LABELS if d in az_dirs)} ({_az_selected_count} of {len(_AZ_LABELS)} directions)"
)
st.sidebar.markdown("**🧭 Azimuth Direction**")
if _az_selected_count == 0 or _az_selected_count == len(_AZ_LABELS):
    st.sidebar.caption("📡 All 360° shown by default — check directions to restrict to a specific part of the sky.")
else:
    st.sidebar.caption(_az_status)
_az_cols = st.sidebar.columns(2)
az_dirs = set()
for _i, _d in enumerate(_AZ_LABELS):
    with _az_cols[_i % 2]:
        if st.checkbox(_d, key=f"az_{_d}"):
            az_dirs.add(_d)
        st.caption(_AZ_CAPTIONS[_d])
if _az_selected_count > 0 and _az_selected_count < len(_AZ_LABELS):
    def _clear_az_dirs():
        for _d in _AZ_LABELS:
            st.session_state[f"az_{_d}"] = False
    st.sidebar.button("✕ Clear direction filter", key="az_clear_all", use_container_width=True, on_click=_clear_az_dirs)
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
moon_illum = 0
location = None
if lat is not None and lon is not None and not (lat == 0.0 and lon == 0.0):
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

        # Moon rise/transit/set
        _moon_sky = SkyCoord(ra=moon_loc.ra, dec=moon_loc.dec, frame='icrs')
        _moon_plan = calculate_planning_info(_moon_sky, location, start_time)
        _tfmt = "%H:%M"
        if _moon_plan['Rise'] == 'Always Up':
            _moon_rise_str = "Always Up"
            _moon_set_str = "Always Up"
        elif _moon_plan.get('_rise_datetime'):
            _moon_rise_str = _moon_plan['_rise_datetime'].strftime(_tfmt)
            _moon_set_str = _moon_plan['_set_datetime'].strftime(_tfmt) if _moon_plan.get('_set_datetime') else '—'
        else:
            _moon_rise_str = '—'
            _moon_set_str = '—'
        _moon_transit_str = _moon_plan['_transit_datetime'].strftime(_tfmt) if _moon_plan.get('_transit_datetime') else '—'
        _moon_ra_str = _moon_sky.ra.to_string(unit=u.hour, sep='hms', precision=0)
        _moon_dec_str = _moon_sky.dec.to_string(sep='dms', precision=0)

        st.sidebar.markdown("---")
        st.sidebar.markdown(f"""
        **🌑 Moon Status:**
        *   Illumination: **{moon_illum:.0f}%**
        *   Altitude: **{moon_alt:.0f}°**
        *   Direction: **{moon_direction}** ({moon_az_deg:.0f}°)
        *   RA: **{_moon_ra_str}**
        *   Dec: **{_moon_dec_str}**
        *   Rise: **{_moon_rise_str}**
        *   Transit: **{_moon_transit_str}**
        *   Set: **{_moon_set_str}**
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


def render_dso_section(location, start_time, duration, min_alt, max_alt, az_dirs,
                       min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
                       show_obs_window, obs_start_naive, obs_end_naive, local_tz,
                       lat, lon):
    name = "Unknown"
    sky_coord = None
    resolved = False

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
        _location_needed()
        st.markdown("---")
        with st.expander("2\\. 📅 Night Plan Builder", expanded=False):
            _location_needed()
    elif dso_list:
        dso_tuple = tuple(
            (d["name"], float(d["ra"]), float(d["dec"]),
             d.get("type", ""), float(d.get("magnitude", 0) or 0),
             d.get("common_name", ""))
            for d in dso_list
        )
        df_dsos = get_dso_summary(lat, lon, start_time, dso_tuple)

        if not df_dsos.empty:
            # Observability check (same pattern as comet/asteroid sections)
            location_d = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            is_obs_list, reason_list, moon_sep_list, moon_status_list = [], [], [], []
            for _, row in df_dsos.iterrows():
                try:
                    sc = SkyCoord(row['RA'], row['Dec'], frame='icrs')
                    check_times = [
                        start_time,
                        start_time + timedelta(minutes=duration / 2),
                        start_time + timedelta(minutes=duration)
                    ]
                    _mlocs = []
                    if moon_loc:
                        try:
                            _mlocs = [get_moon(Time(t), location_d) for t in check_times]
                        except Exception:
                            _mlocs = [moon_loc] * 3
                    obs, reason, ms, mst = _check_row_observability(
                        sc, row.get('Status', ''), location_d, check_times,
                        moon_loc, _mlocs, moon_illum, min_alt, max_alt, az_dirs, min_moon_sep
                    )
                    is_obs_list.append(obs)
                    reason_list.append(reason)
                    moon_sep_list.append(ms)
                    moon_status_list.append(mst)
                except Exception as _e:
                    is_obs_list.append(False)
                    reason_list.append("Parse Error")
                    moon_sep_list.append("–")
                    moon_status_list.append("")
                    print(f"[WARN] DSO observability parse error for row: {_e}", file=sys.stderr)

            df_dsos["is_observable"] = is_obs_list
            df_dsos["filter_reason"] = reason_list
            if moon_sep_list:
                df_dsos["Moon Sep (°)"] = moon_sep_list
                df_dsos["Moon Status"] = moon_status_list

            # Dec filter: objects outside range go to Unobservable tab with reason
            if "_dec_deg" in df_dsos.columns and (min_dec > -90 or max_dec < 90):
                _dec_out = ~((df_dsos["_dec_deg"] >= min_dec) & (df_dsos["_dec_deg"] <= max_dec))
                df_dsos.loc[_dec_out, "is_observable"] = False
                df_dsos.loc[_dec_out, "filter_reason"] = df_dsos.loc[_dec_out, "_dec_deg"].apply(
                    lambda d: f"Dec {d:+.1f}° outside filter ({min_dec}° to {max_dec}°)"
                )

            df_obs_d = df_dsos[df_dsos["is_observable"]].copy()
            _add_peak_alt_session(df_obs_d, location, start_time, start_time + timedelta(minutes=duration))
            df_filt_d = df_dsos[~df_dsos["is_observable"]].copy()

            display_cols_d = ["Name", "Common Name", "Type", "Magnitude", "Constellation",
                              "Rise", "Transit", "Set", "RA", "_dec_deg", "Status", "_peak_alt_session", "Moon Sep (°)", "Moon Status"]

            def display_dso_table(df_in):
                show = [c for c in display_cols_d if c in df_in.columns]
                st.dataframe(df_in[show], hide_index=True, width="stretch", column_config=_MOON_SEP_COL_CONFIG)

            tab_obs_d, tab_filt_d = st.tabs([
                f"🎯 Observable ({len(df_obs_d)})",
                f"👻 Unobservable ({len(df_filt_d)})"
            ])

            with tab_obs_d:
                st.subheader(f"Observable — {category}")
                _chart_sort_d = plot_visibility_timeline(df_obs_d, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None, default_sort_label="Default Order")
                _df_sorted_d = _sort_df_like_chart(df_obs_d, _chart_sort_d) if _chart_sort_d else df_obs_d
                display_dso_table(_df_sorted_d)
                st.caption("🌙 **Moon Sep**: angular separation range across the observation window (min°–max°). Computed at start, mid, and end of window.")
                st.markdown("---")
                with st.expander("2\\. 📅 Night Plan Builder", expanded=True):
                    _render_night_plan_builder(
                        df_obs=df_obs_d,
                        start_time=start_time,
                        night_plan_start=_night_plan_start,
                        night_plan_end=_night_plan_end,
                        local_tz=local_tz,
                        target_col="Name", ra_col="RA", dec_col="Dec",
                        vmag_col="Magnitude", type_col="Type",
                        csv_label="📊 All DSO (CSV)",
                        csv_data=df_dsos,
                        csv_filename=f"dso_{category.lower().replace(' ', '_')}_visibility.csv",
                        section_key=f"dso_{category.lower().replace(' ', '_')}",
                        duration_minutes=duration,
                        location=location, min_alt=min_alt, min_moon_sep=min_moon_sep, az_dirs=az_dirs,
                    )

            with tab_filt_d:
                st.caption("Objects not meeting your filters (Altitude/Azimuth/Moon) during the observation window.")
                if not df_filt_d.empty:
                    filt_show = [c for c in ["Name", "Type", "Magnitude", "filter_reason", "Rise", "Transit", "Set", "Status"] if c in df_filt_d.columns]
                    st.dataframe(df_filt_d[filt_show], hide_index=True, width="stretch")

            st.download_button(
                "Download DSO Data (CSV)",
                data=_sanitize_csv_df(df_dsos.drop(columns=["is_observable", "filter_reason", "_rise_datetime", "_set_datetime"], errors="ignore")).to_csv(index=False).encode("utf-8"),
                file_name=f"dso_{category.lower().replace(' ', '_')}_visibility.csv",
                mime="text/csv"
            )

    # --- Select Target for Trajectory ---
    st.markdown("---")
    st.subheader("3. Select Target for Trajectory")
    st.caption(
        "Pick any target to see its full altitude/azimuth trajectory across your observation window. "
        "Uses your **sidebar settings** (location, session start time, duration, altitude window, "
        "azimuth filter, declination window, and moon separation) — adjust those first if needed."
    )

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
        obj_name_custom = st.text_input("Enter Object Name (e.g., M31, Vega, NGC 891)", value="", key="dso_custom_input", max_chars=200)
        if obj_name_custom:
            try:
                with st.spinner(f"Resolving {obj_name_custom} via SIMBAD..."):
                    name, sky_coord = resolve_simbad(obj_name_custom)
                st.success(f"✅ Resolved: **{name}** (RA: {sky_coord.ra.to_string(unit=u.hour, sep=':', precision=1)}, Dec: {sky_coord.dec.to_string(sep=':', precision=1)})")
                resolved = True
            except Exception as e:
                print(f"[ERROR] SIMBAD resolve failed for '{obj_name_custom}': {e}", file=sys.stderr)
                st.error("Could not resolve object name. Check spelling and try again.")
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

    return name, sky_coord, resolved, None


def render_planet_section(location, start_time, duration, min_alt, max_alt, az_dirs,
                          min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
                          show_obs_window, obs_start_naive, obs_end_naive, local_tz,
                          lat, lon):
    name = "Unknown"
    sky_coord = None
    resolved = False

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

    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        _location_needed()
        st.markdown("---")
        with st.expander("2\\. 📅 Night Plan Builder", expanded=False):
            _location_needed()
    else:
        df_planets = get_planet_summary(lat, lon, start_time)
        if not df_planets.empty:
            # --- Observability check ---
            is_obs_list, reason_list, moon_sep_list, moon_status_list = [], [], [], []

            for idx, row in df_planets.iterrows():
                try:
                    sc = SkyCoord(row['RA'], row['Dec'], frame='icrs')
                    check_times = [start_time, start_time + timedelta(minutes=duration/2), start_time + timedelta(minutes=duration)]
                    _mlocs = []
                    if moon_loc:
                        try:
                            _mlocs = [get_moon(Time(t), location) for t in check_times]
                        except Exception:
                            _mlocs = [moon_loc] * 3
                    obs, reason, ms, mst = _check_row_observability(
                        sc, row.get('Status', ''), location, check_times,
                        moon_loc, _mlocs, moon_illum, min_alt, max_alt, az_dirs, min_moon_sep
                    )
                    is_obs_list.append(obs)
                    reason_list.append(reason)
                    moon_sep_list.append(ms)
                    moon_status_list.append(mst)
                except Exception:
                    is_obs_list.append(True)   # keep on error (planets stay visible by default)
                    reason_list.append("")
                    moon_sep_list.append("–")
                    moon_status_list.append("")

            df_planets["is_observable"] = is_obs_list
            df_planets["filter_reason"] = reason_list
            if moon_sep_list:
                df_planets["Moon Sep (°)"] = moon_sep_list
                df_planets["Moon Status"] = moon_status_list

            # Dec filter: objects outside range go to Unobservable tab with reason
            if "_dec_deg" in df_planets.columns and (min_dec > -90 or max_dec < 90):
                _dec_out = ~((df_planets["_dec_deg"] >= min_dec) & (df_planets["_dec_deg"] <= max_dec))
                df_planets.loc[_dec_out, "is_observable"] = False
                df_planets.loc[_dec_out, "filter_reason"] = df_planets.loc[_dec_out, "_dec_deg"].apply(
                    lambda d: f"Dec {d:+.1f}° outside filter ({min_dec}° to {max_dec}°)"
                )

            df_obs_p = df_planets[df_planets["is_observable"]].copy()
            _add_peak_alt_session(df_obs_p, location, start_time, start_time + timedelta(minutes=duration))
            df_filt_p = df_planets[~df_planets["is_observable"]].copy()

            display_cols_p = ["Name", "Constellation", "Rise", "Transit", "Set",
                              "RA", "_dec_deg", "Status", "_peak_alt_session", "Moon Sep (°)", "Moon Status"]

            tab_obs_p, tab_filt_p = st.tabs([
                f"🎯 Observable ({len(df_obs_p)})",
                f"👻 Unobservable ({len(df_filt_p)})"
            ])

            with tab_obs_p:
                if not df_obs_p.empty:
                    _chart_sort_p = plot_visibility_timeline(df_obs_p, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None, default_sort_label="Default Order")
                    _df_sorted_p = _sort_df_like_chart(df_obs_p, _chart_sort_p) if _chart_sort_p else df_obs_p
                    show_p = [c for c in display_cols_p if c in _df_sorted_p.columns]
                    st.dataframe(_df_sorted_p[show_p], hide_index=True, width="stretch", column_config=_MOON_SEP_COL_CONFIG)
                    st.caption("🌙 **Moon Sep**: angular separation range across the observation window (min°–max°). Computed at start, mid, and end of window.")
                    st.markdown("---")
                    with st.expander("2\\. 📅 Night Plan Builder", expanded=True):
                        _render_night_plan_builder(
                            df_obs=df_obs_p,
                            start_time=start_time,
                            night_plan_start=_night_plan_start,
                            night_plan_end=_night_plan_end,
                            local_tz=local_tz,
                            target_col="Name", ra_col="RA", dec_col="Dec",
                            csv_label="📊 All Planets (CSV)",
                            csv_filename="planets_visibility.csv",
                            section_key="planet",
                            duration_minutes=duration,
                            location=location, min_alt=min_alt, min_moon_sep=min_moon_sep, az_dirs=az_dirs,
                        )
                else:
                    _az_order = {d: i for i, d in enumerate(_AZ_LABELS)}
                    _az_dirs_str = ", ".join(sorted(az_dirs, key=lambda d: _az_order[d])) if az_dirs else "All"
                    st.warning(f"No planets meet your criteria (Alt [{min_alt}°, {max_alt}°], Az [{_az_dirs_str}], Moon Sep > {min_moon_sep}°) during the selected window.")

            with tab_filt_p:
                st.caption("Planets not meeting your filters during the observation window.")
                if not df_filt_p.empty:
                    show_filt_p = [c for c in ["Name", "filter_reason", "Rise", "Transit", "Set", "RA", "_dec_deg", "Status"] if c in df_filt_p.columns]
                    st.dataframe(df_filt_p[show_filt_p], hide_index=True, width="stretch", column_config=_MOON_SEP_COL_CONFIG)

    st.markdown("---")
    st.subheader("3. Select Planet for Trajectory")
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
            print(f"[ERROR] JPL planet resolve failed for '{obj_name}': {e}", file=sys.stderr)
            st.error("Could not fetch position data from JPL. Please try again.")

    return name, sky_coord, resolved, obj_name


def render_comet_section(location, start_time, duration, min_alt, max_alt, az_dirs,
                         min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
                         show_obs_window, obs_start_naive, obs_end_naive, local_tz,
                         lat, lon):
    name = "Unknown"
    sky_coord = None
    resolved = False
    obj_name = None

    _comet_view = st.radio(
        "View", ["\U0001f4cb Watchlist", "\U0001f52d Explore Catalog"],
        horizontal=True, key="comet_view_mode"
    )

    if _comet_view == "\U0001f4cb Watchlist":
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

            # 2. Semi-automatic: scrape Unistellar missions page and notify if new comets detected or removed
            scraped = get_unistellar_scraped_comets()
            st.session_state.comet_scraped_priority = scraped
            if scraped:
                scraped_upper = {_resolve_comet_alias(c) for c in scraped}
                priority_set_upper = {c.upper() for c in priority_set}

                # 2a. Detect ADDITIONS — on Unistellar but not in our priority list
                new_from_page = [c for c in scraped if _resolve_comet_alias(c) not in priority_set_upper]
                if new_from_page:
                    # Write to pending file so it shows in the admin panel
                    existing_pending = []
                    if os.path.exists(COMET_PENDING_FILE):
                        with open(COMET_PENDING_FILE, "r") as f:
                            existing_pending = [l.strip() for l in f if l.strip()]
                    existing_names = {l.split('|')[0].strip() for l in existing_pending}
                    truly_new = [c for c in new_from_page if c not in existing_names]
                    with open(COMET_PENDING_FILE, "a") as f:
                        for c in truly_new:
                            f.write(f"{c}|Add|Auto-detected from Unistellar missions page\n")
                    if truly_new:
                        _send_github_notification(
                            "🔍 Auto-Detected: New Unistellar Priority Comets",
                            "The following comets were found on the Unistellar missions page "
                            "but are not in the current priority list:\n\n"
                            + "\n".join(f"- {c}" for c in truly_new)
                            + "\n\nPlease review and update `comets.yaml` if needed.\n\n"
                            "_Auto-detected by Astro Planner (daily scrape)_"
                        )

                # 2b. Detect REMOVALS — in our priority list but no longer on Unistellar
                removed_from_page = [c for c in priority_set if c.upper() not in scraped_upper and _resolve_comet_alias(c) not in scraped_upper]
                if removed_from_page:
                    existing_pending = []
                    if os.path.exists(COMET_PENDING_FILE):
                        with open(COMET_PENDING_FILE, "r") as f:
                            existing_pending = [l.strip() for l in f if l.strip()]
                    existing_names = {l.split('|')[0].strip() for l in existing_pending}
                    truly_removed = [c for c in removed_from_page if c not in existing_names]
                    with open(COMET_PENDING_FILE, "a") as f:
                        for c in truly_removed:
                            f.write(f"{c}|Remove from Priority|Removed from Unistellar missions page\n")
                    if truly_removed:
                        _send_github_notification(
                            "🔻 Auto-Detected: Unistellar Priority Comets Removed",
                            "The following comets are in our priority list but are no longer "
                            "on the Unistellar missions page:\n\n"
                            + "\n".join(f"- {c}" for c in truly_removed)
                            + "\n\nPlease review and remove from `unistellar_priority` in `comets.yaml` if appropriate.\n\n"
                            "_Auto-detected by Astro Planner (daily scrape)_"
                        )
                st.session_state.comet_removed_priority = removed_from_page

            st.session_state.comet_priority_notified = True

        # User: request a comet addition
        with st.expander("➕ Request a Comet Addition"):
            st.caption("Is a comet missing from the list? Submit a request — it will be verified with JPL Horizons before admin review.")
            req_comet = st.text_input("Comet designation (e.g., C/2025 X1 or 29P)", key="req_comet_name", max_chars=200)
            req_note = st.text_area("Optional note / reason", key="req_comet_note", height=60)
            if st.button("Submit Comet Request", key="btn_comet_req"):
                if req_comet:
                    jpl_id = req_comet.split('(')[0].strip()
                    with st.spinner(f"Verifying '{jpl_id}' with JPL Horizons..."):
                        try:
                            utc_check = start_time.astimezone(pytz.utc)
                            resolve_horizons(jpl_id, obs_time_str=utc_check.strftime('%Y-%m-%d %H:%M:%S'))
                            with open(COMET_PENDING_FILE, "a") as f:
                                f.write(f"{req_comet.replace('|', '\\|')}|Add|{(req_note or 'No note').replace('|', '\\|')}\n")
                            _send_github_notification(
                                f"☄️ Comet Add Request: {req_comet}",
                                f"**Comet:** {req_comet}\n**JPL ID:** {jpl_id}\n**Status:** ✅ JPL Verified\n**Note:** {req_note or 'None'}\n\n_Submitted via Astro Planner_"
                            )
                            st.success(f"✅ '{req_comet}' verified and request submitted for admin review.")
                        except Exception as e:
                            print(f"[ERROR] JPL could not resolve comet '{jpl_id}': {e}", file=sys.stderr)
                            st.error("Could not fetch position data from JPL. Please try again.")

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
                removed_c = st.session_state.get("comet_removed_priority", [])
                if removed_c:
                    st.warning(
                        f"🔻 **{len(removed_c)} comet(s)** removed from Unistellar missions page "
                        f"but still in our priority list: {', '.join(removed_c)}. Admin review needed."
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
                            # Remove from Priority: remove from unistellar_priority list
                            if c_action == "Remove from Priority":
                                cfg["unistellar_priority"] = [
                                    e for e in cfg["unistellar_priority"]
                                    if (e["name"] if isinstance(e, dict) else e) != c_name
                                ]
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
                    np_val = st.selectbox("Priority", ["LOW", "HIGH", "URGENT"], key="new_cpri_val")
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

                    st.markdown("---")
                    if st.button("🔄 Refresh JPL Data", key="jpl_refresh_comets",
                                 help="Clears cached JPL results and reloads overrides — use after editing jpl_id_overrides.yaml"):
                        _load_jpl_overrides.clear()
                        get_comet_summary.clear()
                        get_asteroid_summary.clear()
                        st.success("JPL cache cleared — reloading...")
                        st.rerun()
                    # JPL Resolution Failures
                    _comet_failures_df = st.session_state.get("_comet_jpl_failures", None)
                    if _comet_failures_df is not None and not _comet_failures_df.empty:
                        st.markdown("### ⚠️ JPL Resolution Failures")
                        st.caption(f"{len(_comet_failures_df)} comet(s) could not be resolved via JPL Horizons. "
                                   "This only occurs for newly-added objects not yet in the ephemeris cache, "
                                   "or queries beyond the 30-day pre-computed window. "
                                   "Add a permanent fix via jpl_id_overrides.yaml if this persists.")
                        for _, _fail_row in _comet_failures_df.iterrows():
                            _fname = _fail_row["Name"]
                            _ftried = _fail_row.get("_jpl_id_tried", "?")
                            _ferr = _fail_row.get("_jpl_error", "Unknown error")
                            with st.container():
                                st.markdown(f"**{_fname}** — tried `{_ftried}`")
                                st.caption(str(_ferr))
                                _ovr_id = st.text_input(
                                    "Set JPL ID override",
                                    key=f"jpl_comet_ovr_{_fname}",
                                    placeholder="Enter JPL designation or SPK-ID",
                                )
                                if st.button("💾 Save Override", key=f"jpl_comet_btn_{_fname}"):
                                    if _ovr_id.strip():
                                        from backend.config import read_jpl_overrides, write_jpl_overrides
                                        _ovr_data = read_jpl_overrides(JPL_OVERRIDES_FILE)
                                        _ovr_data["comets"][_fname] = _ovr_id.strip()
                                        write_jpl_overrides(JPL_OVERRIDES_FILE, _ovr_data)
                                        _load_jpl_overrides.clear()
                                        get_comet_summary.clear()
                                        st.success(f"Override saved: **{_fname}** → `{_ovr_id.strip()}`")
                                        st.rerun()
                                    else:
                                        st.warning("Enter a JPL ID before saving.")
                                st.divider()
                    elif _comet_failures_df is not None:
                        st.success("✅ All comets resolved via JPL Horizons.")
                    # else: _comet_failures_df is None → data not yet computed, show nothing

        # Batch visibility table
        if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
            _location_needed()
            st.markdown("---")
            with st.expander("2\\. 📅 Night Plan Builder", expanded=False):
                _location_needed()
        elif active_comets:
            df_comets = get_comet_summary(lat, lon, start_time, tuple(active_comets))

            # Store JPL failure rows in session state for admin panel + fire notifications
            if not df_comets.empty and "_resolve_error" in df_comets.columns:
                _cf = df_comets[df_comets["_resolve_error"] == True]
                st.session_state["_comet_jpl_failures"] = _cf
                for _, _fr in _cf.iterrows():
                    _notify_jpl_failure(_fr["Name"], _fr.get("_jpl_id_tried", "?"), _fr.get("_jpl_error", ""))
            else:
                st.session_state["_comet_jpl_failures"] = pd.DataFrame()

            if not df_comets.empty:
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
                is_obs_list, reason_list, moon_sep_list, moon_status_list = [], [], [], []
                for _, row in df_comets.iterrows():
                    # Short-circuit: stub rows from failed JPL lookups
                    # NOTE: use `is True` not truthy check — NaN is truthy and
                    # would incorrectly flag successful rows that lack _resolve_error
                    if row.get("_resolve_error") is True:
                        is_obs_list.append(False)
                        reason_list.append(f"JPL lookup failed (tried: {row.get('_jpl_id_tried', '?')})")
                        moon_sep_list.append("—")
                        moon_status_list.append("")
                        continue
                    try:
                        sc = SkyCoord(row['RA'], row['Dec'], frame='icrs')
                        check_times = [
                            start_time,
                            start_time + timedelta(minutes=duration / 2),
                            start_time + timedelta(minutes=duration)
                        ]
                        _mlocs = []
                        if moon_loc:
                            try:
                                _mlocs = [get_moon(Time(t), location_c) for t in check_times]
                            except Exception:
                                _mlocs = [moon_loc] * 3
                        obs, reason, ms, mst = _check_row_observability(
                            sc, row.get('Status', ''), location_c, check_times,
                            moon_loc, _mlocs, moon_illum, min_alt, max_alt, az_dirs, min_moon_sep
                        )
                        is_obs_list.append(obs)
                        reason_list.append(reason)
                        moon_sep_list.append(ms)
                        moon_status_list.append(mst)
                    except Exception:
                        is_obs_list.append(False)
                        reason_list.append("Parse Error")
                        moon_sep_list.append("–")
                        moon_status_list.append("")

                df_comets["is_observable"] = is_obs_list
                df_comets["filter_reason"] = reason_list
                if moon_sep_list:
                    df_comets["Moon Sep (°)"] = moon_sep_list
                    df_comets["Moon Status"] = moon_status_list

                # Dec filter: objects outside range go to Unobservable tab with reason
                if "_dec_deg" in df_comets.columns and (min_dec > -90 or max_dec < 90):
                    _dec_out = ~((df_comets["_dec_deg"] >= min_dec) & (df_comets["_dec_deg"] <= max_dec))
                    df_comets.loc[_dec_out, "is_observable"] = False
                    df_comets.loc[_dec_out, "filter_reason"] = df_comets.loc[_dec_out, "_dec_deg"].apply(
                        lambda d: f"Dec {d:+.1f}° outside filter ({min_dec}° to {max_dec}°)"
                    )

                df_obs_c = df_comets[df_comets["is_observable"]].copy()
                _add_peak_alt_session(df_obs_c, location, start_time, start_time + timedelta(minutes=duration))
                df_filt_c = df_comets[~df_comets["is_observable"]].copy()

                display_cols_c = ["Name", "Priority", "Window", "Constellation", "Rise", "Transit", "Set",
                                  "RA", "_dec_deg", "Status", "_peak_alt_session", "Moon Sep (°)", "Moon Status"]

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

                    st.dataframe(df_in[show].style.apply(hi_comet, axis=1), hide_index=True, width="stretch", column_config=_MOON_SEP_COL_CONFIG)

                tab_obs_c, tab_filt_c = st.tabs([
                    f"🎯 Observable ({len(df_obs_c)})",
                    f"👻 Unobservable ({len(df_filt_c)})"
                ])

                with tab_obs_c:
                    st.subheader("Observable Comets")
                    _chart_sort_c = plot_visibility_timeline(df_obs_c, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None, default_sort_label="Priority Order", priority_col="Priority")
                    _df_sorted_c = _sort_df_like_chart(df_obs_c, _chart_sort_c, priority_col="Priority") if _chart_sort_c else df_obs_c
                    display_comet_table(_df_sorted_c)
                    st.caption("🌙 **Moon Sep**: angular separation range across the observation window (min°–max°). Computed at start, mid, and end of window.")
                    st.markdown(
                        "**Legend:** <span style='background-color: #e3f2fd; color: #0d47a1; "
                        "padding: 2px 6px; border-radius: 4px; font-weight: bold;'>⭐ PRIORITY</span>"
                        " = Unistellar Citizen Science priority target",
                        unsafe_allow_html=True
                    )
                    st.markdown("---")
                    with st.expander("2\\. 📅 Night Plan Builder", expanded=True):
                        _render_night_plan_builder(
                            df_obs=df_obs_c,
                            start_time=start_time,
                            night_plan_start=_night_plan_start,
                            night_plan_end=_night_plan_end,
                            local_tz=local_tz,
                            target_col="Name", ra_col="RA", dec_col="Dec",
                            pri_col="Priority",
                            csv_label="📊 All Comets (CSV)",
                            csv_filename="comets_visibility.csv",
                            section_key="comet_mylist",
                            duration_minutes=duration,
                            location=location, min_alt=min_alt, min_moon_sep=min_moon_sep, az_dirs=az_dirs,
                        )

                with tab_filt_c:
                    st.caption("Comets not meeting your filters within the observation window.")
                    if not df_filt_c.empty:
                        filt_show = [c for c in ["Name", "filter_reason", "Rise", "Transit", "Set", "Status"] if c in df_filt_c.columns]
                        st.dataframe(df_filt_c[filt_show], hide_index=True, width="stretch")

                st.download_button(
                    "Download Comet Data (CSV)",
                    data=_sanitize_csv_df(df_comets.drop(columns=["is_observable", "filter_reason", "_rise_datetime", "_set_datetime"], errors="ignore")).to_csv(index=False).encode("utf-8"),
                    file_name="comets_visibility.csv",
                    mime="text/csv"
                )

        # Select comet for trajectory
        st.markdown("---")
        st.subheader("3. Select Comet for Trajectory")
        st.caption(
            "Pick any target to see its full altitude/azimuth trajectory across your observation window. "
            "Uses your **sidebar settings** (location, session start time, duration, altitude window, "
            "azimuth filter, declination window, and moon separation) — adjust those first if needed."
        )
        comet_options = active_comets + ["Custom Comet..."]
        selected_target = st.selectbox("Select a Comet", comet_options, key="comet_traj_sel")
        st.markdown("ℹ️ *Target not listed? Use 'Custom Comet...' or submit a request above.*")

        if selected_target == "Custom Comet...":
            st.caption("Search [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) to find the comet's exact designation or SPK-ID, then enter it below.")
            obj_name = st.text_input("Enter Comet Designation or SPK-ID (e.g., C/2020 F3, 90001202)", value="", key="comet_custom_input", max_chars=200)
        else:
            obj_name = _get_comet_jpl_id(selected_target)

        if obj_name:
            try:
                with st.spinner(f"Querying JPL Horizons for {obj_name}..."):
                    utc_start = start_time.astimezone(pytz.utc)
                    name, sky_coord = resolve_horizons(obj_name, obs_time_str=utc_start.strftime('%Y-%m-%d %H:%M:%S'))
                if selected_target != "Custom Comet...":
                    name = selected_target  # show display name ("24P/Schaumasse"), not bare JPL ID
                st.success(f"✅ Resolved: **{name}**")
                resolved = True
            except Exception as e:
                print(f"[ERROR] JPL Horizons comet resolve failed for '{obj_name}': {e}", file=sys.stderr)
                st.error("Could not fetch position data from JPL. Please try again.")

    elif _comet_view == "\U0001f52d Explore Catalog":
        cat_updated, cat_entries = load_comet_catalog()
        if not cat_entries:
            st.info(
                "Catalog not yet downloaded. Run `python scripts/update_comet_catalog.py` "
                "locally, commit `comets_catalog.json`, and push to GitHub. "
                "The GitHub Actions workflow will also update it automatically every Sunday."
            )
        else:
            st.caption(
                f"MPC catalog snapshot: **{len(cat_entries):,}** comets \u2014 "
                f"last updated {cat_updated[:10] if cat_updated else 'unknown'}"
            )

            # --- Filter controls (all local, no API) ---
            # Orbit type descriptive labels (MPC designation prefix meanings)
            _ORBIT_TYPE_LABELS = {
                "C": "C — Long-period",
                "P": "P — Short-period",
                "I": "I — Interstellar",
                "D": "D — Defunct / lost",
                "X": "X — Uncertain orbit",
                "A": "A — Reclassified asteroid",
            }
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                _raw_types_avail = sorted(set(c.get("orbit_type", "") for c in cat_entries if c.get("orbit_type")))
                _label_options = [_ORBIT_TYPE_LABELS.get(t, t) for t in _raw_types_avail]
                _default_labels = [_ORBIT_TYPE_LABELS.get(t, t) for t in ["C", "P"] if t in _raw_types_avail]
                sel_orbit_labels = st.multiselect(
                    "Orbit Type", _label_options,
                    default=_default_labels,
                    key="cat_orbit_type",
                    help=(
                        "Filter by comet orbit classification (MPC designation prefix):\n\n"
                        "**C/** — Long-period comets from the Oort Cloud, period > 200 years or unknown\n\n"
                        "**P/** — Short-period comets, period < 200 years, often from the Kuiper Belt\n\n"
                        "**I/** — Interstellar objects passing through the Solar System\n\n"
                        "**D/** — Defunct or lost comets no longer observed\n\n"
                        "**X/** — Comets with no reliable orbit yet computed\n\n"
                        "**A/** — Objects initially cataloged as comets, later confirmed as asteroids"
                    )
                )
                # Map selected labels back to raw letters for filtering
                _label_to_raw = {v: k for k, v in _ORBIT_TYPE_LABELS.items()}
                sel_orbit_types = [_label_to_raw.get(lbl, lbl[0]) for lbl in sel_orbit_labels]
            with col_f2:
                peri_window = st.selectbox(
                    "Perihelion within",
                    ["6 months", "1 year", "2 years", "3 years"],
                    index=1, key="cat_peri_window"
                )
            with col_f3:
                mag_options = [10, 12, 15, 17, 20, "Any"]
                mag_limit = st.selectbox("Est. magnitude <", mag_options, index=3, key="cat_mag_limit")

            # --- Apply filters locally (no API needed) ---
            _window_map = {"6 months": 180, "1 year": 365, "2 years": 730, "3 years": 1095}
            _days = _window_map[peri_window]
            _today_dt = datetime.now(pytz.utc).replace(tzinfo=None)
            _cutoff_past = _today_dt - timedelta(days=_days)
            _cutoff_future = _today_dt + timedelta(days=_days)

            filtered_cat = []
            for _c in cat_entries:
                if sel_orbit_types and not any(_c.get("orbit_type", "").startswith(t) for t in sel_orbit_types):
                    continue
                _T = _c.get("T_peri", "")
                try:
                    _T_date = datetime.strptime(str(_T).strip()[:8], "%Y%m%d")
                    if not (_cutoff_past <= _T_date <= _cutoff_future):
                        continue
                except Exception:
                    continue
                if mag_limit != "Any" and _c.get("H") is not None:
                    try:
                        if float(_c["H"]) > float(mag_limit):
                            continue
                    except Exception:
                        pass
                filtered_cat.append(_c)

            st.info(f"**{len(filtered_cat)}** comets match the current filters.")

            if filtered_cat:
                with st.expander(f"Show {len(filtered_cat)} matched comet(s)"):
                    for _c in filtered_cat:
                        _H_str = f", H={_c['H']}" if _c.get("H") is not None else ""
                        st.markdown(f"- {_c['designation']}  *(q={_c.get('q', 0):.2f} AU{_H_str})*")

                if st.button("\U0001f52d Calculate Visibility for Filtered Comets", key="cat_calc_btn",
                             disabled=(lat is None or lon is None or (lat == 0.0 and lon == 0.0))):
                    _cat_names = tuple(_c["designation"] for _c in filtered_cat)
                    _df_cat = get_comet_summary(lat, lon, start_time, _cat_names)
                    st.session_state["_cat_df"] = _df_cat
                    st.session_state["_cat_df_lat"] = lat
                    st.session_state["_cat_df_lon"] = lon
                    st.session_state["_cat_df_start"] = start_time.isoformat()

                if "_cat_df" in st.session_state:
                    if (st.session_state.get("_cat_df_lat") != lat
                            or st.session_state.get("_cat_df_lon") != lon
                            or st.session_state.get("_cat_df_start") != start_time.isoformat()):
                        # Location or time changed since this was calculated — clear stale data
                        del st.session_state["_cat_df"]
                        st.info("Location or time changed. Click **Calculate Visibility** to refresh the catalog.")
                    else:
                        _df_cat = st.session_state["_cat_df"]
                        if not _df_cat.empty:
                            _location_cat = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
                            _is_obs_cat, _reason_cat = [], []
                            for _, _row in _df_cat.iterrows():
                                try:
                                    _sc = SkyCoord(_row["RA"], _row["Dec"], frame="icrs")
                                    _check_times = [
                                        start_time,
                                        start_time + timedelta(minutes=duration / 2),
                                        start_time + timedelta(minutes=duration),
                                    ]
                                    _obs, _reason = False, "Not in window (Alt/Az/Moon)"
                                    if str(_row.get("Status", "")) == "Never Rises":
                                        _reason = "Never Rises"
                                    else:
                                        for _t_chk in _check_times:
                                            _aa = _sc.transform_to(AltAz(obstime=Time(_t_chk), location=_location_cat))
                                            if min_alt <= _aa.alt.degree <= max_alt and (not az_dirs or az_in_selected(_aa.az.degree, az_dirs)):
                                                _obs, _reason = True, ""
                                                break
                                    _is_obs_cat.append(_obs)
                                    _reason_cat.append(_reason)
                                except Exception:
                                    _is_obs_cat.append(False)
                                    _reason_cat.append("Parse Error")

                            _df_cat["is_observable"] = _is_obs_cat
                            _df_cat["filter_reason"] = _reason_cat
                            _df_obs_cat = _df_cat[_df_cat["is_observable"]].copy()
                            _add_peak_alt_session(_df_obs_cat, _location_cat, start_time, start_time + timedelta(minutes=duration))
                            _df_filt_cat = _df_cat[~_df_cat["is_observable"]].copy()

                            _tab_obs_cat, _tab_filt_cat = st.tabs([
                                f"\U0001f3af Observable ({len(_df_obs_cat)})",
                                f"\U0001f47b Unobservable ({len(_df_filt_cat)})"
                            ])
                            _show_cols_cat = ["Name", "Constellation", "Rise", "Transit", "Set",
                                              "RA", "_dec_deg", "Status", "_peak_alt_session",
                                              "Moon Sep (°)", "Moon Status"]
                            with _tab_obs_cat:
                                st.subheader("Observable Comets (Catalog)")
                                _chart_sort_cat = plot_visibility_timeline(
                                    _df_obs_cat,
                                    obs_start=obs_start_naive if show_obs_window else None,
                                    obs_end=obs_end_naive if show_obs_window else None,
                                    default_sort_label="Priority Order"
                                )
                                _df_sorted_cat = _sort_df_like_chart(_df_obs_cat, _chart_sort_cat) if _chart_sort_cat else _df_obs_cat
                                st.dataframe(
                                    _df_sorted_cat[[c for c in _show_cols_cat if c in _df_sorted_cat.columns]],
                                    hide_index=True, width="stretch", column_config=_MOON_SEP_COL_CONFIG
                                )
                                st.markdown("---")
                                with st.expander("2\\. 📅 Night Plan Builder", expanded=True):
                                    _render_night_plan_builder(
                                        df_obs=_df_obs_cat,
                                        start_time=start_time,
                                        night_plan_start=_night_plan_start,
                                        night_plan_end=_night_plan_end,
                                        local_tz=local_tz,
                                        target_col="Name", ra_col="RA", dec_col="Dec",
                                        csv_label="📊 Catalog Comets (CSV)",
                                        csv_data=_df_cat,
                                        csv_filename="catalog_comets_visibility.csv",
                                        section_key="comet_catalog",
                                        duration_minutes=duration,
                                        location=location, min_alt=min_alt, min_moon_sep=min_moon_sep, az_dirs=az_dirs,
                                    )
                            with _tab_filt_cat:
                                st.caption("Comets not meeting your filters within the observation window.")
                                if not _df_filt_cat.empty:
                                    _filt_show_cat = [c for c in ["Name", "filter_reason", "Rise", "Transit", "Set", "Status"] if c in _df_filt_cat.columns]
                                    st.dataframe(_df_filt_cat[_filt_show_cat], hide_index=True, width="stretch")

                            st.download_button(
                                "Download Catalog Data (CSV)",
                                data=_sanitize_csv_df(_df_cat.drop(
                                    columns=["is_observable", "filter_reason", "_rise_datetime", "_set_datetime", "Moon Sep (°)", "Moon Status"],
                                    errors="ignore"
                                )).to_csv(index=False).encode("utf-8"),
                                file_name="catalog_comets_visibility.csv",
                                mime="text/csv"
                            )

            # --- Trajectory picker for Catalog mode ---
            st.markdown("---")
            st.subheader("3. Select Catalog Comet for Trajectory")
            st.caption(
                "Pick any target to see its full altitude/azimuth trajectory across your observation window. "
                "Uses your **sidebar settings** (location, session start time, duration, altitude window, "
                "azimuth filter, declination window, and moon separation) — adjust those first if needed."
            )
            if filtered_cat:
                _cat_options = [_c["designation"] for _c in filtered_cat] + ["Custom Comet..."]
                _cat_selected = st.selectbox("Select a Comet", _cat_options, key="cat_traj_sel")
                if _cat_selected == "Custom Comet...":
                    obj_name = st.text_input(
                        "Enter Comet Designation or SPK-ID", value="", key="cat_custom_input"
                    )
                else:
                    obj_name = _get_comet_jpl_id(_cat_selected)

                if obj_name:
                    try:
                        with st.spinner(f"Querying JPL Horizons for {obj_name}..."):
                            _utc_start_cat = start_time.astimezone(pytz.utc)
                            name, sky_coord = resolve_horizons(
                                obj_name,
                                obs_time_str=_utc_start_cat.strftime('%Y-%m-%d %H:%M:%S')
                            )
                        st.success(f"\u2705 Resolved: **{name}**")
                        resolved = True
                    except Exception as _e:
                        st.error(f"Could not resolve object: {_e}")
            else:
                st.caption("Adjust filters above to find comets, then select one for a trajectory.")

    return name, sky_coord, resolved, obj_name


def render_asteroid_section(location, start_time, duration, min_alt, max_alt, az_dirs,
                            min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
                            show_obs_window, obs_start_naive, obs_end_naive, local_tz,
                            lat, lon):
    name = "Unknown"
    sky_coord = None
    resolved = False
    obj_name = None

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
            scraped_upper = {_resolve_asteroid_alias(a) for a in scraped}
            priority_set_upper = {n.upper() for n in priority_set}
            # Map provisional designations extracted from YAML names → full YAML name
            # e.g. "162882 (2001 FD58)" → {"2001 FD58": "162882 (2001 FD58)"}
            priority_provisionals = _build_priority_provisionals(priority_set)

            # Detect ADDITIONS — on Unistellar but not in our priority list
            new_from_page = [a for a in scraped
                             if _resolve_asteroid_alias(a) not in priority_set_upper
                             and a.upper() not in priority_provisionals]
            if new_from_page:
                existing_pending = []
                if os.path.exists(ASTEROID_PENDING_FILE):
                    with open(ASTEROID_PENDING_FILE, "r") as f:
                        existing_pending = [l.strip() for l in f if l.strip()]
                existing_names = {l.split('|')[0].strip() for l in existing_pending}
                truly_new = [a for a in new_from_page if a not in existing_names]
                with open(ASTEROID_PENDING_FILE, "a") as f:
                    for a in truly_new:
                        f.write(f"{a}|Add|Auto-detected from Unistellar planetary defense page\n")
                if truly_new:
                    _send_github_notification(
                        "🔍 Auto-Detected: New Unistellar Priority Asteroids",
                        "The following asteroids were found on the Unistellar planetary defense missions page "
                        "but are not in the current priority list:\n\n"
                        + "\n".join(f"- {a}" for a in truly_new)
                        + "\n\nPlease review and update `asteroids.yaml` if needed.\n\n"
                        "_Auto-detected by Astro Planner (daily scrape)_"
                    )

            # YAML names covered by scraped bare provisionals
            # e.g. scraped "2001 FD58" covers YAML "162882 (2001 FD58)"
            scraped_via_provisional = {
                priority_provisionals[a.upper()]
                for a in scraped if a.upper() in priority_provisionals
            }

            # Detect REMOVALS — in our priority list but no longer on Unistellar
            removed_from_page = [n for n in priority_set
                                  if n.upper() not in scraped_upper
                                  and _resolve_asteroid_alias(n) not in scraped_upper
                                  and n not in scraped_via_provisional]
            if removed_from_page:
                existing_pending = []
                if os.path.exists(ASTEROID_PENDING_FILE):
                    with open(ASTEROID_PENDING_FILE, "r") as f:
                        existing_pending = [l.strip() for l in f if l.strip()]
                existing_names = {l.split('|')[0].strip() for l in existing_pending}
                truly_removed = [a for a in removed_from_page if a not in existing_names]
                with open(ASTEROID_PENDING_FILE, "a") as f:
                    for a in truly_removed:
                        f.write(f"{a}|Remove from Priority|Removed from Unistellar planetary defense page\n")
                if truly_removed:
                    _send_github_notification(
                        "🔻 Auto-Detected: Unistellar Priority Asteroids Removed",
                        "The following asteroids are in our priority list but are no longer "
                        "on the Unistellar planetary defense missions page:\n\n"
                        + "\n".join(f"- {a}" for a in truly_removed)
                        + "\n\nPlease review and remove from `unistellar_priority` in `asteroids.yaml` if appropriate.\n\n"
                        "_Auto-detected by Astro Planner (daily scrape)_"
                    )
            st.session_state.asteroid_removed_priority = removed_from_page

        st.session_state.asteroid_priority_notified = True

    # User: request an asteroid addition
    with st.expander("➕ Request an Asteroid Addition"):
        st.caption("Is an asteroid missing from the list? Submit a request — it will be verified with JPL Horizons before admin review.")
        req_asteroid = st.text_input("Asteroid designation (e.g., 99942 Apophis, 433 Eros)", key="req_asteroid_name", max_chars=200)
        req_a_note = st.text_area("Optional note / reason", key="req_asteroid_note", height=60)
        if st.button("Submit Asteroid Request", key="btn_asteroid_req"):
            if req_asteroid:
                jpl_id = _asteroid_jpl_id(req_asteroid)
                with st.spinner(f"Verifying '{jpl_id}' with JPL Horizons..."):
                    try:
                        utc_check = start_time.astimezone(pytz.utc)
                        resolve_horizons(jpl_id, obs_time_str=utc_check.strftime('%Y-%m-%d %H:%M:%S'))
                        with open(ASTEROID_PENDING_FILE, "a") as f:
                            f.write(f"{req_asteroid.replace('|', '\\|')}|Add|{(req_a_note or 'No note').replace('|', '\\|')}\n")
                        _send_github_notification(
                            f"🪨 Asteroid Add Request: {req_asteroid}",
                            f"**Asteroid:** {req_asteroid}\n**JPL ID:** {jpl_id}\n**Status:** ✅ JPL Verified\n**Note:** {req_a_note or 'None'}\n\n_Submitted via Astro Planner_"
                        )
                        st.success(f"✅ '{req_asteroid}' verified and request submitted for admin review.")
                    except Exception as e:
                        print(f"[ERROR] JPL could not resolve asteroid '{jpl_id}': {e}", file=sys.stderr)
                        st.error("Could not fetch position data from JPL. Please try again.")

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
                priority_provisionals_d = _build_priority_provisionals(priority_set)
                new_from_page = [a for a in scraped_a
                                 if _resolve_asteroid_alias(a) not in priority_set_upper
                                 and a.upper() not in priority_provisionals_d]
                if new_from_page:
                    st.info(
                        f"🔍 **{len(new_from_page)} new asteroid(s)** detected on the Unistellar missions page "
                        f"not yet in the priority list: {', '.join(new_from_page)}. Admin has been notified."
                    )
            removed_a = st.session_state.get("asteroid_removed_priority", [])
            if removed_a:
                st.warning(
                    f"🔻 **{len(removed_a)} asteroid(s)** removed from Unistellar missions page "
                    f"but still in our priority list: {', '.join(removed_a)}. Admin review needed."
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
                        # Remove from Priority: remove from unistellar_priority list
                        if a_action == "Remove from Priority":
                            cfg["unistellar_priority"] = [
                                e for e in cfg["unistellar_priority"]
                                if _asteroid_priority_name(e) != a_name
                            ]
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
                nap_val = st.selectbox("Priority", ["LOW", "HIGH", "URGENT"], key="new_apri_val")
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

                st.markdown("---")
                if st.button("🔄 Refresh JPL Data", key="jpl_refresh_asteroids",
                             help="Clears cached JPL results and reloads overrides — use after editing jpl_id_overrides.yaml"):
                    _load_jpl_overrides.clear()
                    get_comet_summary.clear()
                    get_asteroid_summary.clear()
                    st.success("JPL cache cleared — reloading...")
                    st.rerun()
                # JPL Resolution Failures
                _asteroid_failures_df = st.session_state.get("_asteroid_jpl_failures", None)
                if _asteroid_failures_df is not None and not _asteroid_failures_df.empty:
                    st.markdown("### ⚠️ JPL Resolution Failures")
                    st.caption(f"{len(_asteroid_failures_df)} asteroid(s) could not be resolved via JPL Horizons. "
                               "This only occurs for newly-added objects not yet in the ephemeris cache, "
                               "or queries beyond the 30-day pre-computed window. "
                               "Add a permanent fix via jpl_id_overrides.yaml if this persists.")
                    for _, _fail_row in _asteroid_failures_df.iterrows():
                        _fname = _fail_row["Name"]
                        _ftried = _fail_row.get("_jpl_id_tried", "?")
                        _ferr = _fail_row.get("_jpl_error", "Unknown error")
                        with st.container():
                            st.markdown(f"**{_fname}** — tried `{_ftried}`")
                            st.caption(str(_ferr))
                            _ovr_id = st.text_input(
                                "Set JPL ID override",
                                key=f"jpl_asteroid_ovr_{_fname}",
                                placeholder="Enter JPL designation or SPK-ID",
                            )
                            if st.button("💾 Save Override", key=f"jpl_asteroid_btn_{_fname}"):
                                if _ovr_id.strip():
                                    from backend.config import read_jpl_overrides, write_jpl_overrides
                                    _ovr_data = read_jpl_overrides(JPL_OVERRIDES_FILE)
                                    _ovr_data["asteroids"][_fname] = _ovr_id.strip()
                                    write_jpl_overrides(JPL_OVERRIDES_FILE, _ovr_data)
                                    _load_jpl_overrides.clear()
                                    get_asteroid_summary.clear()
                                    st.success(f"Override saved: **{_fname}** → `{_ovr_id.strip()}`")
                                    st.rerun()
                                else:
                                    st.warning("Enter a JPL ID before saving.")
                            st.divider()
                elif _asteroid_failures_df is not None:
                    st.success("✅ All asteroids resolved via JPL Horizons.")
                # else: _asteroid_failures_df is None → data not yet computed, show nothing

    # Batch visibility table
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        _location_needed()
        st.markdown("---")
        with st.expander("2\\. 📅 Night Plan Builder", expanded=False):
            _location_needed()
    elif active_asteroids:
        df_asteroids = get_asteroid_summary(lat, lon, start_time, tuple(active_asteroids))

        # Store JPL failure rows in session state for admin panel + fire notifications
        if not df_asteroids.empty and "_resolve_error" in df_asteroids.columns:
            _af = df_asteroids[df_asteroids["_resolve_error"] == True]
            st.session_state["_asteroid_jpl_failures"] = _af
            for _, _fr in _af.iterrows():
                _notify_jpl_failure(_fr["Name"], _fr.get("_jpl_id_tried", "?"), _fr.get("_jpl_error", ""))
        else:
            st.session_state["_asteroid_jpl_failures"] = pd.DataFrame()

        if not df_asteroids.empty:
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
            is_obs_list, reason_list, moon_sep_list, moon_status_list = [], [], [], []
            for _, row in df_asteroids.iterrows():
                # Short-circuit: stub rows from failed JPL lookups
                if row.get("_resolve_error") is True:
                    is_obs_list.append(False)
                    reason_list.append(f"JPL lookup failed (tried: {row.get('_jpl_id_tried', '?')})")
                    moon_sep_list.append("—")
                    moon_status_list.append("")
                    continue
                try:
                    sc = SkyCoord(row['RA'], row['Dec'], frame='icrs')
                    check_times = [
                        start_time,
                        start_time + timedelta(minutes=duration / 2),
                        start_time + timedelta(minutes=duration)
                    ]
                    _mlocs = []
                    if moon_loc:
                        try:
                            _mlocs = [get_moon(Time(t), location_a) for t in check_times]
                        except Exception:
                            _mlocs = [moon_loc] * 3
                    obs, reason, ms, mst = _check_row_observability(
                        sc, row.get('Status', ''), location_a, check_times,
                        moon_loc, _mlocs, moon_illum, min_alt, max_alt, az_dirs, min_moon_sep
                    )
                    is_obs_list.append(obs)
                    reason_list.append(reason)
                    moon_sep_list.append(ms)
                    moon_status_list.append(mst)
                except Exception:
                    is_obs_list.append(False)
                    reason_list.append("Parse Error")
                    moon_sep_list.append("–")
                    moon_status_list.append("")

            df_asteroids["is_observable"] = is_obs_list
            df_asteroids["filter_reason"] = reason_list
            if moon_sep_list:
                df_asteroids["Moon Sep (°)"] = moon_sep_list
                df_asteroids["Moon Status"] = moon_status_list

            # Dec filter: objects outside range go to Unobservable tab with reason
            if "_dec_deg" in df_asteroids.columns and (min_dec > -90 or max_dec < 90):
                _dec_out = ~((df_asteroids["_dec_deg"] >= min_dec) & (df_asteroids["_dec_deg"] <= max_dec))
                df_asteroids.loc[_dec_out, "is_observable"] = False
                df_asteroids.loc[_dec_out, "filter_reason"] = df_asteroids.loc[_dec_out, "_dec_deg"].apply(
                    lambda d: f"Dec {d:+.1f}° outside filter ({min_dec}° to {max_dec}°)"
                )

            df_obs_a = df_asteroids[df_asteroids["is_observable"]].copy()
            _add_peak_alt_session(df_obs_a, location, start_time, start_time + timedelta(minutes=duration))
            df_filt_a = df_asteroids[~df_asteroids["is_observable"]].copy()

            display_cols_a = ["Name", "Priority", "Window", "Constellation", "Rise", "Transit", "Set",
                              "RA", "_dec_deg", "Status", "_peak_alt_session", "Moon Sep (°)", "Moon Status"]

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

                st.dataframe(df_in[show].style.apply(hi_asteroid, axis=1), hide_index=True, width="stretch", column_config=_MOON_SEP_COL_CONFIG)

            tab_obs_a, tab_filt_a = st.tabs([
                f"🎯 Observable ({len(df_obs_a)})",
                f"👻 Unobservable ({len(df_filt_a)})"
            ])

            with tab_obs_a:
                st.subheader("Observable Asteroids")
                _chart_sort_a = plot_visibility_timeline(df_obs_a, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None, default_sort_label="Priority Order", priority_col="Priority")
                _df_sorted_a = _sort_df_like_chart(df_obs_a, _chart_sort_a, priority_col="Priority") if _chart_sort_a else df_obs_a
                display_asteroid_table(_df_sorted_a)
                st.caption("🌙 **Moon Sep**: angular separation range across the observation window (min°–max°). Computed at start, mid, and end of window.")
                st.markdown(
                    "**Legend:** <span style='background-color: #e3f2fd; color: #0d47a1; "
                    "padding: 2px 6px; border-radius: 4px; font-weight: bold;'>⭐ PRIORITY</span>"
                    " = Unistellar Planetary Defense priority target",
                    unsafe_allow_html=True
                )
                st.markdown("---")
                with st.expander("2\\. 📅 Night Plan Builder", expanded=True):
                    _render_night_plan_builder(
                        df_obs=df_obs_a,
                        start_time=start_time,
                        night_plan_start=_night_plan_start,
                        night_plan_end=_night_plan_end,
                        local_tz=local_tz,
                        target_col="Name", ra_col="RA", dec_col="Dec",
                        pri_col="Priority",
                        csv_label="📊 All Asteroids (CSV)",
                        csv_filename="asteroids_visibility.csv",
                        section_key="asteroid",
                        duration_minutes=duration,
                        location=location, min_alt=min_alt, min_moon_sep=min_moon_sep, az_dirs=az_dirs,
                    )

            with tab_filt_a:
                st.caption("Asteroids not meeting your filters within the observation window.")
                if not df_filt_a.empty:
                    filt_show = [c for c in ["Name", "filter_reason", "Rise", "Transit", "Set", "RA", "_dec_deg", "Status"] if c in df_filt_a.columns]
                    st.dataframe(df_filt_a[filt_show], hide_index=True, width="stretch", column_config=_MOON_SEP_COL_CONFIG)

            st.download_button(
                "Download Asteroid Data (CSV)",
                data=_sanitize_csv_df(df_asteroids.drop(columns=["is_observable", "filter_reason", "_rise_datetime", "_set_datetime"], errors="ignore")).to_csv(index=False).encode("utf-8"),
                file_name="asteroids_visibility.csv",
                mime="text/csv"
            )

    # Select asteroid for trajectory
    st.markdown("---")
    st.subheader("3. Select Asteroid for Trajectory")
    st.caption(
        "Pick any target to see its full altitude/azimuth trajectory across your observation window. "
        "Uses your **sidebar settings** (location, session start time, duration, altitude window, "
        "azimuth filter, declination window, and moon separation) — adjust those first if needed."
    )
    asteroid_options = active_asteroids + ["Custom Asteroid..."]
    selected_target = st.selectbox("Select an Asteroid", asteroid_options, key="asteroid_traj_sel")
    st.markdown("ℹ️ *Target not listed? Use 'Custom Asteroid...' or submit a request above. Find the exact designation in the [JPL Small-Body Database](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html).*")

    if selected_target == "Custom Asteroid...":
        st.caption("Search [JPL Small-Body Database](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html) or [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) to find the exact designation, then enter it below.")
        obj_name = st.text_input("Enter Asteroid Name or Designation (e.g., Eros, 2024 YR4, 99942)", value="", key="asteroid_custom_input", max_chars=200)
    else:
        obj_name = _asteroid_jpl_id(selected_target)

    if obj_name:
        try:
            with st.spinner(f"Querying JPL Horizons for {obj_name}..."):
                utc_start = start_time.astimezone(pytz.utc)
                name, sky_coord = resolve_horizons(obj_name, obs_time_str=utc_start.strftime('%Y-%m-%d %H:%M:%S'))
            if selected_target != "Custom Asteroid...":
                name = selected_target  # show display name ("2 Pallas"), not bare JPL ID ("2")
            st.success(f"✅ Resolved: **{name}**")
            resolved = True
        except Exception as e:
            print(f"[ERROR] JPL Horizons asteroid resolve failed for '{obj_name}': {e}", file=sys.stderr)
            st.error("Could not fetch position data from JPL. Please try again.")

    return name, sky_coord, resolved, obj_name


def render_cosmic_section(location, start_time, duration, min_alt, max_alt, az_dirs,
                          min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
                          show_obs_window, obs_start_naive, obs_end_naive, local_tz,
                          lat, lon):
    name = "Unknown"
    sky_coord = None
    resolved = False
    obj_name = None

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
                st.error(f"GitHub Sync Error: {e}")  # admin panel — full error OK

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
                        f.write(f"{b_name.replace('|', '\\|')}|{b_reason}\n")

                    send_notification(f"🚫 Block Request: {b_name}", f"**Target:** {b_name}\n**Reason:** {b_reason}\n\n_Submitted via Astro Planner App_")
                    st.success(f"Report for '{b_name}' submitted.")

        with tab_pri:
            c1, c2 = st.columns([2, 1])
            p_name = c1.text_input("Event Name", key="rep_p_name")
            p_val = c2.selectbox("New Priority", ["LOW", "HIGH", "URGENT", "REMOVE"], key="rep_p_val")
            if st.button("Submit Priority", key="btn_pri"):
                if p_name:
                    with open(PENDING_FILE, "a") as f:
                        f.write(f"{p_name.replace('|', '\\|')}|Priority: {p_val}\n")

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
                p_val = st.selectbox("New Priority", ["LOW", "HIGH", "URGENT"])
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
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        status_msg.empty()
        _location_needed()
        df_alerts = None
    else:
        df_alerts = get_scraped_data()
        status_msg.empty()
        if df_alerts is None:
            st.warning("⚠️ Could not load Cosmic Cataclysm targets — network issue or site unavailable. Try again shortly.")

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

                    # Moon positions across window (start / mid / end) — used for both display and filter
                    check_times = [start_time, start_time + timedelta(minutes=duration/2), start_time + timedelta(minutes=duration)]
                    moon_locs_dynamic = []
                    if moon_loc:
                        try:
                            moon_locs_dynamic = [get_moon(Time(t), location) for t in check_times]
                        except Exception:
                            moon_locs_dynamic = [moon_loc] * 3

                    # Moon Sep = range across window (min–max)
                    _seps_dyn = [moon_sep_deg(sc, ml) for ml in moon_locs_dynamic] if moon_locs_dynamic else []
                    moon_sep = min(_seps_dyn) if _seps_dyn else (moon_sep_deg(sc, moon_loc) if moon_loc else 0.0)
                    _moon_sep_max = max(_seps_dyn) if _seps_dyn else moon_sep
                    moon_status = get_moon_status(moon_illum, moon_sep) if moon_loc else ""

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
                        passed_checks = False
                        for i, t_check in enumerate(check_times):
                            # Quick AltAz check
                            frame = AltAz(obstime=Time(t_check), location=location)
                            aa = sc.transform_to(frame)
                            if min_alt <= aa.alt.degree <= max_alt and (not az_dirs or az_in_selected(aa.az.degree, az_dirs)):
                                # Check Moon dynamically
                                if moon_locs_dynamic:
                                    sep_dyn = moon_sep_deg(sc, moon_locs_dynamic[i])
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
                    row_dict['_dec_deg'] = sc.dec.degree   # needed for Dec filter
                    row_dict['_ra_deg']  = sc.ra.deg
                    row_dict['is_observable'] = is_obs
                    row_dict['filter_reason'] = filt_reason
                    row_dict['Moon Sep (°)'] = f"{moon_sep:.1f}°–{_moon_sep_max:.1f}°" if moon_loc else "–"
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

            # Identify Duration column (keep numeric for correct sort; format applied via column_config)
            dur_col = next((c for c in df_display.columns if 'dur' in c.lower()), None)

            # Convert Duration from seconds to minutes (keeps it numeric so sorting works)
            if dur_col and dur_col in df_display.columns:
                df_display[dur_col] = pd.to_numeric(df_display[dur_col], errors='coerce') / 60

            # Identify Link column (may be named "Link", "DeepLink", "Deep Link", etc.)
            link_col = next((c for c in df_display.columns if 'link' in c.lower()), None)

            # Identify optional filter columns (used in Night Plan Builder)
            vmag_col = next((c for c in df_display.columns if 'mag' in c.lower()), None)
            type_col = next(
                (c for c in df_display.columns
                 if c.lower() in ('type', 'class', 'category') or 'event type' in c.lower()),
                None,
            )
            disc_col = next(
                (c for c in df_display.columns
                 if 'disc' in c.lower()
                 or ('date' in c.lower() and 'update' not in c.lower())),
                None,
            )

            # Parse discovery date strings (e.g. "Jul 14") into sortable datetimes.
            # Strings without a year default to the current year; if that puts them in
            # the future we roll back one year so "Jul 14" in Feb 2026 → Jul 2025.
            if disc_col and disc_col in df_display.columns:
                _today_utc = pd.Timestamp.now(tz='UTC').normalize()

                def _parse_disc_date(val):
                    if pd.isna(val) or not str(val).strip():
                        return pd.NaT
                    s = str(val).strip()
                    try:
                        # First try: standard parse ("Jul 14, 2025", "2025-07-14", etc.)
                        dt = pd.to_datetime(s, errors='coerce')
                        if pd.isna(dt):
                            # Second try: month+day only ("Jul 14", "Dec 20")
                            from datetime import datetime as _dt_cls
                            dt_raw = _dt_cls.strptime(f"{s} {_today_utc.year}", '%b %d %Y')
                            dt = pd.Timestamp(dt_raw, tz='UTC')
                        else:
                            if dt.tzinfo is None:
                                dt = dt.tz_localize('UTC')
                        # If parsed date is in the future, it belongs to the prior year
                        if dt > _today_utc + pd.Timedelta(days=1):
                            dt = dt.replace(year=dt.year - 1)
                        return dt
                    except Exception:
                        return pd.NaT

                df_display['_disc_sort'] = df_display[disc_col].apply(_parse_disc_date)

            # Reorder columns to put Name and Planning info first
            priority_cols = [target_col, 'Constellation', 'Rise', 'Transit', 'Set', 'Status']

            # Ensure Priority is visible and upfront
            if pri_col and pri_col in df_display.columns:
                priority_cols.insert(1, pri_col)

            other_cols = [c for c in df_display.columns if c not in priority_cols and c != link_col]

            final_order = priority_cols + other_cols
            if link_col:
                final_order.append(link_col)

            df_display = df_display[final_order]

            # Dec filter: objects outside range go to Unobservable tab with reason
            if "_dec_deg" in df_display.columns and (min_dec > -90 or max_dec < 90):
                _dec_out = ~((df_display["_dec_deg"] >= min_dec) & (df_display["_dec_deg"] <= max_dec))
                df_display.loc[_dec_out, "is_observable"] = False
                df_display.loc[_dec_out, "filter_reason"] = df_display.loc[_dec_out, "_dec_deg"].apply(
                    lambda d: f"Dec {d:+.1f}° outside filter ({min_dec}° to {max_dec}°)"
                )

            # Split Data
            df_obs = df_display[df_display['is_observable'] == True].copy()
            df_filt = df_display[df_display['is_observable'] == False].copy()

            # Add peak altitude during the observation session to the observable slice
            _add_peak_alt_session(df_obs, location, start_time, start_time + timedelta(minutes=duration))

            # Filter columns for display
            cols_to_remove_keywords = ['exposure', 'cadence', 'gain', 'exp', 'cad']
            actual_cols_to_drop = [
                col for col in df_display.columns
                if any(keyword in col.lower() for keyword in cols_to_remove_keywords)
                or col in ['is_observable', 'filter_reason', 'Dec']  # drop DMS Dec; _dec_deg shows as "Dec" via column_config
            ]
            # Drop hidden columns except _dec_deg (shown as numeric "Dec") and _peak_alt_session (shown as "Peak Alt")
            hidden_cols = [c for c in df_display.columns
                           if c.startswith('_') and c not in ('_dec_deg', '_peak_alt_session')]

            # Helper to style and display
            def display_styled_table(df_in):
                _dt_in = df_in.copy()
                # Swap the string disc_col values for parsed datetimes so
                # Streamlit's column-header click sorts chronologically.
                if disc_col and disc_col in _dt_in.columns and '_disc_sort' in _dt_in.columns:
                    _dt_in[disc_col] = _dt_in['_disc_sort']
                final_table = _dt_in.drop(columns=actual_cols_to_drop + hidden_cols, errors='ignore')

                # Force DeepLink to the very end
                curr_cols = final_table.columns.tolist()
                p_cols = [c for c in priority_cols if c in curr_cols]
                l_cols = [c for c in curr_cols if c == link_col]
                o_cols = [c for c in curr_cols if c not in p_cols and c not in l_cols]

                # Order: Priority -> Others -> DeepLink
                new_order = p_cols + o_cols + l_cols
                final_table = final_table[new_order]

                # Configure columns
                col_config = dict(_MOON_SEP_COL_CONFIG)
                if link_col and link_col in final_table.columns:
                    col_config[link_col] = st.column_config.LinkColumn(
                        "🔭 Open", display_text="🔭 Open"
                    )
                if dur_col and dur_col in final_table.columns:
                    col_config[dur_col] = st.column_config.NumberColumn(
                        dur_col, format="%d min"
                    )
                if disc_col and disc_col in final_table.columns:
                    col_config[disc_col] = st.column_config.DatetimeColumn(
                        disc_col, format="MMM DD, YYYY"
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

                _chart_sort_cosmic = plot_visibility_timeline(df_obs, obs_start=obs_start_naive if show_obs_window else None, obs_end=obs_end_naive if show_obs_window else None, default_sort_label="Order By Discovery Date")

                st.info("ℹ️ **Note:** The 'DeepLink' column is for Unistellar telescopes only. For other equipment, please use the RA/Dec coordinates.")

                _df_sorted_cosmic = _sort_df_like_chart(df_obs, _chart_sort_cosmic) if _chart_sort_cosmic else df_obs
                display_styled_table(_df_sorted_cosmic)
                st.caption("🌙 **Moon Sep**: angular separation range across the observation window (min°–max°). Computed at start, mid, and end of window.")

                # Legend (below table so it's clear it belongs to the data, not the chart)
                st.markdown("""
                **Priority Legend:**
                <span style='background-color: #ef5350; color: white; padding: 2px 6px; border-radius: 4px;'>URGENT</span>
                <span style='background-color: #ffb74d; color: black; padding: 2px 6px; border-radius: 4px;'>HIGH</span>
                <span style='background-color: #c8e6c9; color: black; padding: 2px 6px; border-radius: 4px;'>LOW</span>
                """, unsafe_allow_html=True)

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

            # ── Night Plan Builder ─────────────────────────────────────────────
            st.markdown("---")
            with st.expander("2\\. 📅 Night Plan Builder", expanded=True):
                _render_night_plan_builder(
                    df_obs=df_obs,
                    start_time=start_time,
                    night_plan_start=_night_plan_start,
                    night_plan_end=_night_plan_end,
                    local_tz=local_tz,
                    target_col=target_col, ra_col=ra_col, dec_col=dec_col,
                    pri_col=pri_col, dur_col=dur_col,
                    vmag_col=vmag_col, type_col=type_col,
                    disc_col=disc_col, link_col=link_col,
                    csv_label="📊 All Alerts (CSV)",
                    csv_data=df_alerts,
                    csv_filename="unistellar_targets.csv",
                    section_key="cosmic",
                    duration_minutes=duration,
                    location=location, min_alt=min_alt, min_moon_sep=min_moon_sep, az_dirs=az_dirs,
                )

            st.markdown("---")
            st.subheader("3. Select Target for Trajectory")
            st.caption(
                "Pick any target to see its full altitude/azimuth trajectory across your observation window. "
                "Uses your **sidebar location and session start time** — adjust those first if needed."
            )
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
                        print(f"[ERROR] Coordinate parse failed for '{name}' (RA={ra_val}, Dec={dec_val}): {e}", file=sys.stderr)
                        st.error("Calculation failed for this object. Try a different target.")
        else:
            st.error(f"Could not find 'Name' column. Found: {cols}")
            st.dataframe(df_alerts, width="stretch")
    elif lat is not None and lon is not None and not (lat == 0.0 and lon == 0.0):
        st.error("Failed to scrape data. Please check the scraper logs.")

    # Sections 2 & 3 placeholders — shown whenever the data block above didn't render them
    if df_alerts is None or df_alerts.empty:
        st.markdown("---")
        with st.expander("2\\. 📅 Night Plan Builder", expanded=False):
            _location_needed()
        st.markdown("---")
        st.subheader("3. Select Target for Trajectory")
        _location_needed()

    return name, sky_coord, resolved, obj_name


if target_mode == "Star/Galaxy/Nebula (SIMBAD)":
    name, sky_coord, resolved, _ = render_dso_section(
        location, start_time, duration, min_alt, max_alt, az_dirs,
        min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
        show_obs_window, obs_start_naive, obs_end_naive, local_tz, lat, lon
    )

elif target_mode == "Planet (JPL Horizons)":
    name, sky_coord, resolved, obj_name = render_planet_section(
        location, start_time, duration, min_alt, max_alt, az_dirs,
        min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
        show_obs_window, obs_start_naive, obs_end_naive, local_tz, lat, lon
    )

elif target_mode == "Comet (JPL Horizons)":
    name, sky_coord, resolved, obj_name = render_comet_section(
        location, start_time, duration, min_alt, max_alt, az_dirs,
        min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
        show_obs_window, obs_start_naive, obs_end_naive, local_tz, lat, lon
    )

elif target_mode == "Asteroid (JPL Horizons)":
    name, sky_coord, resolved, obj_name = render_asteroid_section(
        location, start_time, duration, min_alt, max_alt, az_dirs,
        min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
        show_obs_window, obs_start_naive, obs_end_naive, local_tz, lat, lon
    )

elif target_mode == "Cosmic Cataclysm":
    name, sky_coord, resolved, obj_name = render_cosmic_section(
        location, start_time, duration, min_alt, max_alt, az_dirs,
        min_moon_sep, min_dec, max_dec, moon_loc, moon_illum,
        show_obs_window, obs_start_naive, obs_end_naive, local_tz, lat, lon
    )

elif target_mode == "Manual RA/Dec":
    st.markdown("---")
    st.subheader("2. Enter Coordinates for Trajectory")
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
            print(f"[ERROR] Invalid manual RA/Dec coordinates (RA='{ra_input}', Dec='{dec_input}'): {e}", file=sys.stderr)
            st.error("Invalid coordinates format. Please use formats like '15h59m30s' for RA and '25d55m13s' for Dec.")

# ---------------------------
# MAIN: Calculation & Output
# ---------------------------
st.header("3. Trajectory Results" if target_mode == "Manual RA/Dec" else "4. Trajectory Results")

_no_location = lat is None or lon is None or (lat == 0.0 and lon == 0.0)
if _no_location:
    _location_needed()

if st.button("🚀 Calculate Visibility", type="primary", disabled=not resolved or _no_location):
    location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
    
    ephem_coords = None
    # For moving objects, fetch precise ephemerides for the duration
    if target_mode in ["Comet (JPL Horizons)", "Asteroid (JPL Horizons)"]:
        with st.spinner("Fetching detailed ephemerides from JPL..."):
            try:
                ephem_coords = get_horizons_ephemerides(obj_name, start_time, duration_minutes=duration, step_minutes=10)
            except Exception as e:
                print(f"[ERROR] Could not fetch detailed ephemerides for '{obj_name}': {e}", file=sys.stderr)
                st.warning("Could not fetch position data from JPL. Please try again. Using fixed coordinates.")
    elif target_mode == "Planet (JPL Horizons)":
        with st.spinner("Fetching planetary ephemerides from JPL..."):
            try:
                ephem_coords = get_planet_ephemerides(obj_name, start_time, duration_minutes=duration, step_minutes=10)
            except Exception as e:
                print(f"[ERROR] Could not fetch planetary ephemerides for '{obj_name}': {e}", file=sys.stderr)
                st.warning("Could not fetch position data from JPL. Please try again. Using fixed coordinates.")

    with st.spinner("Calculating trajectory..."):
        results = compute_trajectory(sky_coord, location, start_time, duration_minutes=duration, ephemeris_coords=ephem_coords)
    
    df = pd.DataFrame(results)
    

    # --- Moon Check (driven from per-step trajectory data) ---
    current_moon_sep = None
    moon_status_text = "N/A"
    if 'Moon Sep (°)' in df.columns and df['Moon Sep (°)'].notna().any():
        _ms_vals = df['Moon Sep (°)'].dropna()
        _ms_min = float(_ms_vals.min())
        _ms_max = float(_ms_vals.max())
        current_moon_sep = _ms_min
        if _ms_min < min_moon_sep:
            st.warning(f"⚠️ **Moon Warning:** Target gets as close as {_ms_min:.1f}° to the Moon during this window (Limit: {min_moon_sep}°).")
        status = get_moon_status(moon_illum, _ms_min)
        moon_status_text = f"{_ms_min:.1f}°–{_ms_max:.1f}° ({status})"
    elif moon_loc and sky_coord:
        # Fallback to single start-time value if trajectory Moon Sep unavailable
        sep = moon_sep_deg(sky_coord, moon_loc)
        current_moon_sep = sep
        if sep < min_moon_sep:
            st.warning(f"⚠️ **Moon Warning:** Target is {sep:.1f}° from the Moon (Limit: {min_moon_sep}°).")
        status = get_moon_status(moon_illum, sep)
        moon_status_text = f"{sep:.1f}° ({status})"

    # --- Observational Filter Check ---
    # Check if any point in the trajectory meets the criteria
    visible_points = df[
        (df["Altitude (°)"].between(min_alt, max_alt)) & 
        (df["Azimuth (°)"].apply(lambda az: not az_dirs or az_in_selected(az, az_dirs)))
    ]
    
    if visible_points.empty:
        _az_order = {d: i for i, d in enumerate(_AZ_LABELS)}
        _az_dirs_str = ", ".join(sorted(az_dirs, key=lambda d: _az_order[d])) if az_dirs else "All"
        st.warning(f"⚠️ **Visibility Warning:** Target does not meet filters (Alt [{min_alt}°, {max_alt}°], Az [{_az_dirs_str}]) during window.")
    
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

    _traj_tooltip = [alt.Tooltip('Local Time', format='%Y-%m-%d %H:%M'), 'Altitude (°)', 'Azimuth (°)', 'Direction']
    if 'Moon Sep (°)' in chart_data.columns:
        _traj_tooltip.append(alt.Tooltip('Moon Sep (°)', title='Moon Sep (°)'))

    chart = alt.Chart(chart_data).mark_line(point=True).encode(
        x=alt.X('Local Time', axis=alt.Axis(format='%H:%M')),
        y=alt.Y('Altitude (°)'),
        tooltip=_traj_tooltip
    ).interactive()
    
    st.altair_chart(chart, width='stretch')

    # Data Table
    st.subheader("Detailed Data")
    st.dataframe(df, width='stretch')
    st.caption("🌙 **Moon Sep (°)**: angular separation from the Moon at each 10-min step.")

    # Sanitize filename
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
    date_str = start_time.strftime('%Y-%m-%d')

    st.download_button(
        label="Download CSV",
        data=_sanitize_csv_df(df).to_csv(index=False).encode('utf-8'),
        file_name=f"{safe_name}_{date_str}_trajectory.csv",
        mime="text/csv",
    )