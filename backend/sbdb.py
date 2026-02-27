# backend/sbdb.py
"""JPL Small Body Database name lookup — no Streamlit dependency."""
import requests

SBDB_API = "https://ssd-api.jpl.nasa.gov/sbdb.api"


def sbdb_lookup(name, timeout=10):
    """Query JPL SBDB for a small body name -> SPK-ID string, or None if not found.

    Returns the SPK-ID as a string (e.g. '90004812'), or None on any failure.
    """
    try:
        resp = requests.get(SBDB_API, params={"sstr": name, "full-prec": "0"}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if "object" in data and "spkid" in data["object"]:
            return str(data["object"]["spkid"])
        return None
    except Exception:
        return None
