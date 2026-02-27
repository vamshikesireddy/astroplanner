# backend/sbdb.py
"""JPL Small Body Database name lookup — no Streamlit dependency."""
import requests

SBDB_API = "https://ssd-api.jpl.nasa.gov/sbdb.api"


def sbdb_lookup(name, timeout=10, _depth=0):
    """Query JPL SBDB for a small body name -> SPK-ID string, or None if not found.

    Returns the SPK-ID as a string (e.g. '90004812'), or None on any failure.
    Handles HTTP 300 (multiple matches) by picking the primary object and recursing.
    """
    try:
        # full-prec=0 suppresses extended orbital element data — only object identity needed
        resp = requests.get(SBDB_API, params={"sstr": name, "full-prec": "0"}, timeout=timeout)
        if resp.status_code == 300:
            # Multiple matches — pick the primary (first in list) and recurse once
            if _depth > 1:
                return None
            data = resp.json()
            matches = data.get("list", [])
            if not matches:
                return None
            primary_pdes = matches[0].get("pdes")
            if not primary_pdes:
                return None
            return sbdb_lookup(primary_pdes, timeout=timeout, _depth=_depth + 1)
        resp.raise_for_status()
        data = resp.json()
        if "object" in data and "spkid" in data["object"]:
            return str(data["object"]["spkid"])
        return None
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None
