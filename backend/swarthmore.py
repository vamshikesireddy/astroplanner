# backend/swarthmore.py
"""Swarthmore Transit Finder (TAPIR) integration.

Queries astro.swarthmore.edu/transits/print_transits.cgi (CSV output, print_html=2)
for upcoming exoplanet transit windows at a given observer location.
"""

import io
import re
import requests
import pandas as pd
from datetime import date


_SWARTHMORE_URL = "https://astro.swarthmore.edu/transits/print_transits.cgi"


def _parse_duration_hr(dur_str) -> float:
    """Parse 'H:MM' string into float hours. Returns NaN on failure."""
    try:
        parts = str(dur_str).strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 60.0
        return float(parts[0])
    except Exception:
        return float("nan")


def _parse_swarthmore_csv(csv_text: str) -> pd.DataFrame:
    """Parse raw CSV text from Swarthmore Transit Finder (print_html=2 format).

    Column mapping:
        Name                    -> Planet
        start time              -> _ingress_str (then parsed to _ingress_dt)
        mid time                -> _mid_str (then parsed to _mid_dt)
        end time                -> _egress_str (then parsed to _egress_dt)
        duration(hours)         -> Duration_hr (float, parsed from H:MM)
        el_start/mid/end        -> kept as-is; Min_Alt_During_Transit = min of these
        depth(ppt)              -> Depth_mmag (kept as-is, ppt = mmag)
        percent_transit_observable -> Completeness ("Complete" if >=100, else "Partial")
        moon_dist_deg           -> Moon_Dist_Deg
        coords(J2000)           -> RA_Dec_raw (split into RA/Dec downstream)

    Returns empty DataFrame if parsing fails.
    """
    if not csv_text or not csv_text.strip():
        return pd.DataFrame()

    try:
        df = pd.read_csv(io.StringIO(csv_text.strip()))
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    # Strip whitespace from column names
    df.columns = [c.strip() for c in df.columns]

    # Rename columns to internal names
    rename = {
        "Name":                       "Planet",
        "start time":                 "_ingress_str",
        "mid time":                   "_mid_str",
        "end time":                   "_egress_str",
        "duration(hours)":            "_dur_raw",
        "depth(ppt)":                 "Depth_mmag",
        "percent_transit_observable": "_pct_obs",
        "moon_dist_deg":              "Moon_Dist_Deg",
        "coords(J2000)":              "RA_Dec_raw",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    # Parse datetime columns (timezone-naive local times)
    for src, dest in [("_ingress_str", "_ingress_dt"), ("_mid_str", "_mid_dt"), ("_egress_str", "_egress_dt")]:
        if src in df.columns:
            df[dest] = pd.to_datetime(df[src], errors="coerce")

    # Duration as float hours
    if "_dur_raw" in df.columns:
        df["Duration_hr"] = df["_dur_raw"].apply(_parse_duration_hr)
    else:
        df["Duration_hr"] = float("nan")

    # Completeness — use >= 100 to handle 100.0 floats correctly
    if "_pct_obs" in df.columns:
        pct = pd.to_numeric(df["_pct_obs"], errors="coerce").fillna(0)
        df["Completeness"] = pct.apply(lambda p: "Complete" if p >= 100 else "Partial")
    else:
        df["Completeness"] = "Partial"

    # Min altitude during transit
    elev_cols = [c for c in ["el_start", "el_mid", "el_end"] if c in df.columns]
    if elev_cols:
        df["Min_Alt_During_Transit"] = df[elev_cols].apply(pd.to_numeric, errors="coerce").min(axis=1)
    else:
        df["Min_Alt_During_Transit"] = float("nan")

    # Split coords(J2000) into RA / Dec
    if "RA_Dec_raw" in df.columns:
        def _split_radec(s):
            try:
                s = str(s).strip()
                # Format: "13 57 33  +43 29 36" — split on 2+ spaces
                parts = re.split(r'\s{2,}', s, maxsplit=1)
                if len(parts) == 2:
                    return parts[0].strip(), parts[1].strip()
            except Exception:
                pass
            return "", ""
        coords = df["RA_Dec_raw"].apply(_split_radec)
        df["RA"]  = coords.apply(lambda x: x[0])
        df["Dec"] = coords.apply(lambda x: x[1])

    return df


def fetch_transit_windows(lat: float, lon: float, tz_name: str,
                          planet_names: list, days: int = 7) -> pd.DataFrame:
    """Fetch upcoming transit windows from Swarthmore Transit Finder.

    Args:
        lat:          Observer latitude (degrees, positive=north).
        lon:          Observer longitude (degrees, negative=west).
        tz_name:      IANA timezone name e.g. "America/Los_Angeles".
        planet_names: List of planet name strings to keep (Unistellar targets).
                      If empty, returns empty DataFrame immediately.
        days:         Number of days ahead to search (default 7).

    Returns:
        DataFrame with one row per transit event, filtered to planet_names.
        Empty DataFrame on network error or no results.
    """
    if not planet_names:
        return pd.DataFrame()

    # Swarthmore uses west longitude as positive (360 + west_lon)
    lon_sw = lon if lon >= 0 else 360 + lon
    obs = f"{lat};{lon_sw};{tz_name};My Location"

    today = date.today().strftime("%m-%d-%Y")

    params = {
        "observatory_string": obs,
        "use_utc":            "0",
        "start_date":         today,
        "days":               str(days),
        "minimum_start_elevation": "20",
        "print_html":         "2",  # CSV output
    }

    try:
        resp = requests.get(_SWARTHMORE_URL, params=params, timeout=30)
        resp.raise_for_status()
        df = _parse_swarthmore_csv(resp.text)
    except Exception:
        return pd.DataFrame()

    if df.empty or "Planet" not in df.columns:
        return df

    # Filter to only requested planets (case-insensitive strip match)
    _names_lower = {n.strip().lower() for n in planet_names}
    mask = df["Planet"].str.strip().str.lower().isin(_names_lower)
    return df[mask].reset_index(drop=True)
