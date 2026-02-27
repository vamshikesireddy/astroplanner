"""backend/app_logic.py — Pure business logic extracted from app.py.

No Streamlit imports. All functions are independently testable.
Imported by app.py via: from backend.app_logic import <name>
"""

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
