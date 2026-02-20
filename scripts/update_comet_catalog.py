"""
update_comet_catalog.py
=======================
Downloads the MPC (Minor Planet Center) comet orbital elements and saves a
filtered snapshot to comets_catalog.json in the repo root.

Run manually:
    python scripts/update_comet_catalog.py

Or triggered automatically every Sunday by the GitHub Actions workflow at
.github/workflows/update-comet-catalog.yml
"""

import json
import os
import sys
import requests
from datetime import datetime, timedelta, timezone

MPC_URL = "https://minorplanetcenter.net/Extended_Files/CometEls.json"
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "comets_catalog.json")


def _get_designation(entry):
    return (entry.get("Designation_and_name") or entry.get("Name") or "").strip()


def _get_perihelion_time(entry):
    """Returns perihelion time as YYYYMMDD string.

    MPC extended JSON splits perihelion into three fields:
      Year_of_perihelion, Month_of_perihelion, Day_of_perihelion (float)
    Falls back to single-field keys (T, Tp, etc.) for any future format changes.
    """
    year = entry.get("Year_of_perihelion")
    month = entry.get("Month_of_perihelion")
    day = entry.get("Day_of_perihelion")
    if year and month and day is not None:
        try:
            return f"{int(year)}{int(month):02d}{int(float(day)):02d}"
        except Exception:
            pass
    # Fallback: single-field formats
    for key in ("T", "Tp", "T_peri", "Perihelion_time"):
        val = entry.get(key)
        if val:
            return str(val).strip()
    return ""


def _get_orbit_type(entry, designation):
    """Infer orbit type from designation prefix (C/, P/, I/, X/, D/) or stored field."""
    for key in ("Orbit_type", "orbit_type", "type"):
        val = entry.get(key)
        if val:
            return str(val).strip()
    # Infer from designation prefix
    for prefix in ("C/", "P/", "I/", "X/", "D/", "A/"):
        if designation.startswith(prefix):
            return prefix.rstrip("/")
    return ""


def _parse_perihelion_date(T_str):
    """Parse MPC perihelion time string to a datetime.
    MPC uses YYYYMMDD.ddd (e.g. '20250415.123') or ISO format."""
    if not T_str:
        return None
    T_str = T_str.strip()
    # Format: YYYYMMDD.ddd
    try:
        return datetime.strptime(T_str[:8], "%Y%m%d")
    except ValueError:
        pass
    # ISO-like: YYYY-MM-DD
    try:
        return datetime.strptime(T_str[:10], "%Y-%m-%d")
    except ValueError:
        pass
    return None


def download_and_save():
    print(f"Downloading MPC comet catalog from {MPC_URL} ...")
    try:
        resp = requests.get(MPC_URL, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"ERROR: Failed to download catalog: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        raw = resp.json()
    except Exception as e:
        print(f"ERROR: Could not parse JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(raw, list):
        # Some MPC endpoints wrap in a dict; try common keys
        raw = raw.get("data", raw.get("comets", []))

    print(f"  Downloaded {len(raw)} raw entries.")

    # Print the first entry's keys so field names are transparent
    if raw:
        print(f"  Sample fields from first entry: {list(raw[0].keys())}")

    catalog = []
    skipped = 0
    for entry in raw:
        try:
            desig = _get_designation(entry)
            if not desig:
                skipped += 1
                continue

            T_str = _get_perihelion_time(entry)
            orbit_type = _get_orbit_type(entry, desig)

            # Parse numeric fields with fallback
            q = entry.get("q") or entry.get("Perihelion_dist") or 0
            e = entry.get("e") or entry.get("Eccentricity") or 0
            i = entry.get("i") or entry.get("Inclination") or 0
            H = entry.get("H") or entry.get("Abs_magnitude")

            catalog.append({
                "designation": desig,
                "T_peri": T_str,
                "q": round(float(q), 4) if q else 0.0,
                "e": round(float(e), 6) if e else 0.0,
                "i": round(float(i), 4) if i else 0.0,
                "H": float(H) if H is not None else None,
                "orbit_type": orbit_type,
            })
        except Exception:
            skipped += 1
            continue

    print(f"  Parsed {len(catalog)} comets ({skipped} skipped).")

    output = {
        "updated": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S") + "Z",
        "source": MPC_URL,
        "count": len(catalog),
        "comets": catalog,
    }

    out_path = os.path.normpath(OUTPUT)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"  Saved to {out_path}")

    # Stats for info
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    window_1yr_past = today - timedelta(days=365)
    window_1yr_future = today + timedelta(days=365)
    near_peri = []
    for c in catalog:
        dt = _parse_perihelion_date(c["T_peri"])
        if dt and window_1yr_past <= dt <= window_1yr_future:
            near_peri.append(c["designation"])
    print(f"  {len(near_peri)} comets with perihelion within ±1 year of today.")


if __name__ == "__main__":
    download_and_save()
