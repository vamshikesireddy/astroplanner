"""backend/app_logic.py — Pure business logic extracted from app.py.

No Streamlit imports. All functions are independently testable.
Imported by app.py via: from backend.app_logic import <name>
"""

import pytz
import pandas as pd
from datetime import datetime, timedelta
from astropy.coordinates import AltAz
from astropy.time import Time
from backend.core import moon_sep_deg, compute_peak_alt_in_window

# ── Azimuth direction filter ───────────────────────────────────────────────

_AZ_OCTANTS = {
    "N":  [(337.5, 360.0), (0.0, 22.5)],
    "NE": [(22.5,  67.5)],
    "E":  [(67.5,  112.5)],
    "SE": [(112.5, 157.5)],
    "S":  [(157.5, 202.5)],
    "SW": [(202.5, 247.5)],
    "W":  [(247.5, 292.5)],
    "NW": [(292.5, 337.5)],
}
_AZ_LABELS   = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
_AZ_CAPTIONS = {
    "N":  "337.5–22.5°",
    "NE": "22.5–67.5°",
    "E":  "67.5–112.5°",
    "SE": "112.5–157.5°",
    "S":  "157.5–202.5°",
    "SW": "202.5–247.5°",
    "W":  "247.5–292.5°",
    "NW": "292.5–337.5°",
}


def az_in_selected(az_deg: float, selected_dirs: set) -> bool:
    """Return True if az_deg falls within any of the selected compass octants."""
    for d in selected_dirs:
        for lo, hi in _AZ_OCTANTS[d]:
            if lo <= az_deg < hi:
                return True
    return False


# ── Moon status ────────────────────────────────────────────────────────────

_MOON_DARK_SKY_ILLUM = 15   # illumination % below which it's "Dark Sky"
_MOON_AVOID_SEP      = 30   # separation ° below which it's "Avoid"
_MOON_CAUTION_SEP    = 60   # separation ° below which it's "Caution"


def get_moon_status(illumination: float, separation: float) -> str:
    """Return moon status emoji string for a given illumination % and separation °."""
    if illumination < _MOON_DARK_SKY_ILLUM:
        return "🌑 Dark Sky"
    elif separation < _MOON_AVOID_SEP:
        return "⛔ Avoid"
    elif separation < _MOON_CAUTION_SEP:
        return "⚠️ Caution"
    else:
        return "✅ Safe"


# ── Row observability check ─────────────────────────────────────────────────

def _check_row_observability(sc, row_status, location, check_times, moon_loc, moon_locs_chk,
                              moon_illum, min_alt, max_alt, az_dirs, min_moon_sep):
    """Compute observability for a single target row.

    Args:
        sc:            SkyCoord of the target.
        row_status:    Value of the 'Status' column (string, e.g. "Never Rises").
        location:      EarthLocation of the observer.
        check_times:   List of 3 datetime objects (start, mid, end of window).
        moon_loc:      Moon coordinate at start time (or None if unavailable).
        moon_locs_chk: List of moon coordinates at each check_time (or []).
        moon_illum:    Moon illumination 0-100 float.
        min_alt:       Minimum altitude filter (degrees).
        max_alt:       Maximum altitude filter (degrees).
        az_dirs:       Set of selected compass octant labels (empty = no filter).
        min_moon_sep:  Minimum moon separation filter (degrees).

    Returns:
        (obs: bool, reason: str, moon_sep_str: str, moon_status_str: str)
    """
    _seps = [moon_sep_deg(sc, ml) for ml in moon_locs_chk] if moon_locs_chk else []
    _min_sep = min(_seps) if _seps else (moon_sep_deg(sc, moon_loc) if moon_loc else 0.0)
    _max_sep = max(_seps) if _seps else _min_sep
    moon_sep_str    = f"{_min_sep:.1f}°–{_max_sep:.1f}°" if moon_loc else "–"
    moon_status_str = get_moon_status(moon_illum, _min_sep) if moon_loc else ""

    if str(row_status) == "Never Rises":
        return False, "Never Rises", moon_sep_str, moon_status_str

    obs, reason = False, "Not visible during window"
    for i_t, t_chk in enumerate(check_times):
        aa = sc.transform_to(AltAz(obstime=Time(t_chk), location=location))
        if min_alt <= aa.alt.degree <= max_alt and (not az_dirs or az_in_selected(aa.az.degree, az_dirs)):
            sep_ok = (not moon_locs_chk) or (moon_sep_deg(sc, moon_locs_chk[i_t]) >= min_moon_sep)
            if sep_ok:
                obs, reason = True, ""
                break
    return obs, reason, moon_sep_str, moon_status_str


# ── DataFrame sort helpers ───────────────────────────────────────────────────

def _sort_df_like_chart(df, sort_option, priority_col=None):
    """Reorder a DataFrame to match the Gantt chart sort selection.

    For Earliest Rise/Set/Transit, 'Always Up' objects are pushed to the bottom
    (sorted by transit among themselves), matching the Gantt chart behaviour.
    """
    if not sort_option:
        return df

    _au = df['Status'].str.contains('Always Up', na=False) if 'Status' in df.columns else pd.Series(False, index=df.index)

    if sort_option == "Earliest Rise" and '_rise_datetime' in df.columns:
        reg = df[~_au].sort_values('_rise_datetime', ascending=True, na_position='last')
        au = df[_au].sort_values('_transit_datetime', ascending=True, na_position='last') if '_transit_datetime' in df.columns else df[_au]
        return pd.concat([reg, au])
    elif sort_option == "Earliest Set" and '_set_datetime' in df.columns:
        reg = df[~_au].sort_values('_set_datetime', ascending=True, na_position='last')
        au = df[_au].sort_values('_transit_datetime', ascending=True, na_position='last') if '_transit_datetime' in df.columns else df[_au]
        return pd.concat([reg, au])
    elif sort_option == "Earliest Transit" and '_transit_datetime' in df.columns:
        reg = df[~_au].sort_values('_transit_datetime', ascending=True, na_position='last')
        au = df[_au].sort_values('_transit_datetime', ascending=True, na_position='last')
        return pd.concat([reg, au])
    elif priority_col and priority_col in df.columns:
        _PRI_RANK = {"URGENT": 0, "HIGH": 1, "LOW": 2}

        def _rank(val):
            v = str(val).upper() if pd.notna(val) else ""
            for k, r in _PRI_RANK.items():
                if k in v:
                    return r
            if v.strip():
                return 3
            return 4

        tmp = df.copy()
        tmp['_sort_rank'] = tmp[priority_col].apply(_rank)
        return tmp.sort_values('_sort_rank', kind='mergesort').drop(columns=['_sort_rank'])
    else:
        return df


def build_night_plan(df_obs, sort_by="set"):
    """Build a time-sorted target list for tonight.

    Args:
        df_obs:   Observable targets DataFrame.
        sort_by:  'set' (default) sorts by _set_datetime;
                  'transit' sorts by _transit_datetime.
                  Ascending in both cases, NaT last.

    Returns the sorted DataFrame. Priority colour-coding is handled by the caller.
    """
    df = df_obs.copy()

    sort_col = '_transit_datetime' if sort_by == 'transit' else '_set_datetime'
    if sort_col in df.columns:
        df['_time_sort'] = pd.to_datetime(df[sort_col], errors='coerce', utc=True)
        df = df.sort_values('_time_sort', ascending=True, na_position='last')
        df = df.drop(columns=['_time_sort'])

    return df


# ── CSV sanitisation ────────────────────────────────────────────────────────

def _sanitize_csv_df(df: pd.DataFrame) -> pd.DataFrame:
    """Escape leading formula characters in string columns for safe CSV export."""
    _FORMULA_PREFIXES = ('=', '+', '-', '@')
    df_safe = df.copy()
    for col in df_safe.select_dtypes(include='object').columns:
        df_safe[col] = df_safe[col].apply(
            lambda x: f"'{x}" if isinstance(x, str) and x and x[0] in _FORMULA_PREFIXES else x
        )
    return df_safe


# ── Peak altitude helper ────────────────────────────────────────────────────

def _add_peak_alt_session(df, location, win_start_tz, win_end_tz, n_steps=5):
    """Add _peak_alt_session column (peak altitude during obs window) to df in-place.

    Uses compute_peak_alt_in_window at n_steps sample points.
    Falls back to None when location or coordinates are missing.
    Returns df for chaining.
    """
    if location is None or df.empty or '_ra_deg' not in df.columns or '_dec_deg' not in df.columns:
        df['_peak_alt_session'] = None
        return df
    peaks = []
    for _, row in df.iterrows():
        ra = row.get('_ra_deg')
        dec = row.get('_dec_deg')
        if pd.notnull(ra) and pd.notnull(dec):
            try:
                peaks.append(compute_peak_alt_in_window(
                    float(ra), float(dec), location, win_start_tz, win_end_tz, n_steps=n_steps
                ))
            except Exception:
                peaks.append(None)
        else:
            peaks.append(None)
    df['_peak_alt_session'] = peaks
    return df


# ── Night plan filter chain ───────────────────────────────────────────────────

def _apply_night_plan_filters(
    df,
    pri_col, sel_pri,
    vmag_col, vmag_range,
    type_col, sel_types,
    disc_col, disc_days,
    win_start_dt, win_end_dt,
    sel_moon, all_moon_statuses,
    location=None,
    min_alt=0,
):
    """Apply all night plan filters to df and return the filtered copy.

    Filter order: priority → magnitude → type → discovery → window → moon status.

    Parameters
    ----------
    df : DataFrame
        Source observable targets (will be copied internally).
    pri_col, sel_pri : str | None, list | None
        Priority column name and selected priority levels.
        Pass ``None`` for either to skip the priority filter.
    vmag_col, vmag_range : str | None, tuple | None
        Magnitude column name and (lo, hi) range tuple.
        Pass ``None`` for either to skip the magnitude filter.
    type_col, sel_types : str | None, list | None
        Type column name and selected type values.
        Pass ``None`` for either to skip the type filter.
    disc_col, disc_days : str | None, int | None
        Discovery-date column name and recency threshold in days.
        Pass ``None`` for either (or ``disc_days=365``) to skip.
    win_start_dt, win_end_dt : datetime (tz-aware)
        Observation window start and end as fully-specified tz-aware datetimes.
        Replaces the old integer-hour + timedelta(days=1) approach.
    sel_moon, all_moon_statuses : list | None, list
        Selected Moon Status labels and the full list of statuses.
        Filtering is skipped when ``sel_moon`` is ``None`` or equals
        ``all_moon_statuses``.
    location : astropy EarthLocation | None
        Observer location used for altitude check inside the window.
        When ``None``, the altitude check is skipped (horizon-overlap only).
    min_alt : float
        Minimum peak altitude (degrees) the target must reach inside the
        observation window.  Ignored when ``location`` is ``None``.
    """
    out = df.copy()

    # Filter: priority
    if pri_col and pri_col in out.columns and sel_pri:
        def _matches_pri(val):
            v = str(val).upper().strip()
            for p in sel_pri:
                if p == "(unassigned)" and v in ('', 'NAN', 'NONE', 'N/A'):
                    return True
                elif p != "(unassigned)" and p in v:
                    return True
            return False
        out = out[out[pri_col].apply(_matches_pri)]

    # Filter: magnitude
    if vmag_col and vmag_col in out.columns and vmag_range is not None:
        _mag_num = pd.to_numeric(out[vmag_col], errors='coerce')
        out = out[
            _mag_num.isna() |
            _mag_num.between(vmag_range[0], vmag_range[1], inclusive='both')
        ]

    # Filter: type/class
    if type_col and type_col in out.columns and sel_types is not None:
        out = out[out[type_col].astype(str).isin(sel_types)]

    # Filter: discovery recency
    if (disc_col and disc_col in out.columns
            and disc_days is not None and disc_days < 365):
        _disc_parsed = pd.to_datetime(out[disc_col], errors='coerce', utc=True)
        _disc_cutoff = pd.Timestamp(
            datetime.now(tz=pytz.utc) - timedelta(days=disc_days)
        )
        out = out[_disc_parsed.isna() | (_disc_parsed >= _disc_cutoff)]

    # Filter: observation window — win_start_dt and win_end_dt are tz-aware datetimes
    # passed directly from the caller (no integer-hour arithmetic needed here).

    if '_rise_datetime' in out.columns and '_set_datetime' in out.columns:
        _keep = []
        _peak_alts = []
        for _, _row in out.iterrows():
            _status = str(_row.get('Status', ''))
            if 'Always Up' in _status:
                _keep.append(True)
                _peak_alts.append(90.0)
                continue
            _r = _row.get('_rise_datetime')
            _s = _row.get('_set_datetime')
            if pd.isnull(_r) or pd.isnull(_s):
                _keep.append(True)
                _peak_alts.append(None)
                continue
            # Horizon-overlap check (fast, no computation)
            try:
                _horizon_ok = _r < win_end_dt and _s > win_start_dt
            except (TypeError, ValueError):
                _keep.append(True)
                _peak_alts.append(None)
                continue
            if not _horizon_ok:
                _keep.append(False)
                _peak_alts.append(None)
                continue
            # Altitude check across window (only for horizon-passing rows)
            _ra  = _row.get('_ra_deg')
            _dec = _row.get('_dec_deg')
            if (location is not None
                    and _ra is not None and pd.notnull(_ra)
                    and _dec is not None and pd.notnull(_dec)):
                _peak = compute_peak_alt_in_window(
                    float(_ra), float(_dec), location, win_start_dt, win_end_dt
                )
                _keep.append(_peak >= min_alt)
                _peak_alts.append(_peak)
            else:
                # No location or coordinates — fall back to horizon-only check
                _keep.append(True)
                _peak_alts.append(None)

        out['_peak_alt_window'] = _peak_alts
        out = out[_keep].copy()

    # Filter: Moon Status
    if (sel_moon is not None and 'Moon Status' in out.columns
            and len(sel_moon) < len(all_moon_statuses)):
        out = out[out['Moon Status'].isin(sel_moon)]

    return out
