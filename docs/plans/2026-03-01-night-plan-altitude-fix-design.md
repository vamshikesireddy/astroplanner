# Design: Night Plan Builder — Altitude-Aware Window Filter + Parameters Summary

**Date:** 2026-03-01
**Status:** Approved

---

## Problem

### Bug: objects below min_alt included in the Night Plan

The Night Plan Builder window filter uses horizon-crossing times (`_rise_datetime`, `_set_datetime` at alt=0°), not min-altitude-crossing times. An object can pass the window filter while never reaching `min_alt` during the session window.

**Example:** 99942 Apophis with sidebar start=15:00, session window=20:00→01:00, min_alt=20°.
- Observability check runs at sidebar start (15:00). Apophis transits at 15:00 → altitude ~30° → passes min_alt=20° → ends up in `df_obs`.
- Window filter: `_set_datetime(21:16) > win_start(20:00)` → true → included in plan.
- Reality: at 20:00 Apophis is at 14° and declining, never reaches 20° during the window. Should be excluded.

### Missing: user visibility into active filter parameters

After pressing "Build Plan", users have no way to know what parameters were applied. If an object they expected isn't in the plan, they don't know whether it was excluded by altitude, moon separation, azimuth, or window overlap.

---

## Solution

### Component 1 — Altitude-aware window filter + Peak Alt column

#### A. Add `_ra_deg` to all section summary DataFrames

Every observability loop already has a `SkyCoord` (`sc`) per object. Add one line to store the numeric RA alongside the existing `_dec_deg`:

```python
row['_ra_deg'] = sc.ra.deg   # numeric degrees; hidden column (not shown in table)
```

**Sections to update:** `get_dso_summary`, `get_comet_summary`, `get_asteroid_summary`, Planet inline loop, Cosmic inline loop.

#### B. Pass `location` and `min_alt` into `_render_night_plan_builder`

Two new parameters:

```python
def _render_night_plan_builder(
    df_obs, start_time, night_plan_start, night_plan_end, local_tz,
    ...,
    location=None,    # NEW: astropy EarthLocation
    min_alt=0,        # NEW: sidebar min altitude threshold (degrees)
):
```

All 6 call sites already have `location` and `min_alt` in scope — add them to the call.

#### C. Replace the window filter in `_apply_night_plan_filters`

New parameters:
```python
def _apply_night_plan_filters(
    df,
    ...,
    location=None,    # NEW
    min_alt=0,        # NEW
):
```

Replace the `_in_obs_window` helper:

```python
def _in_obs_window(row):
    status = str(row.get('Status', ''))
    if 'Always Up' in status:
        row['_peak_alt_window'] = 90.0
        return True
    r = row.get('_rise_datetime')
    s = row.get('_set_datetime')
    if pd.isnull(r) or pd.isnull(s):
        return True  # keep if timing unknown

    # Basic horizon overlap (existing check)
    try:
        if not (r < win_end_dt and s > win_start_dt):
            return False
    except (TypeError, ValueError):
        return True

    # NEW: altitude check across window
    if location is not None and '_ra_deg' in row.index and '_dec_deg' in row.index:
        ra_deg = row['_ra_deg']
        dec_deg = row['_dec_deg']
        if pd.notnull(ra_deg) and pd.notnull(dec_deg):
            sc = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame='icrs')
            window_secs = (win_end_dt - win_start_dt).total_seconds()
            n_steps = max(2, int(window_secs / 1800) + 1)  # sample every 30 min
            peak = -90.0
            for i in range(n_steps):
                t_naive = win_start_dt + timedelta(seconds=i * window_secs / (n_steps - 1))
                t_utc = Time(t_naive.astimezone(pytz.utc).replace(tzinfo=None), scale='utc')
                aa = sc.transform_to(AltAz(obstime=t_utc, location=location))
                if aa.alt.deg > peak:
                    peak = aa.alt.deg
            row['_peak_alt_window'] = round(peak, 1)
            return peak >= min_alt

    return True  # no location: fall back to horizon-only check
```

**Note:** `_in_obs_window` needs to mutate `row['_peak_alt_window']` — switch from `df.apply(..., axis=1)` (which passes a copy) to a loop that builds a mask and a new column simultaneously. See implementation plan for exact pattern.

#### D. Show `Peak Alt (°)` column in the plan table

After `_scheduled` is built, rename `_peak_alt_window` → `"Peak Alt (°)"` for display:

```python
if '_peak_alt_window' in _scheduled.columns:
    _display_scheduled = _scheduled.rename(columns={'_peak_alt_window': 'Peak Alt (°)'})
else:
    _display_scheduled = _scheduled
```

Rendered with `st.column_config.NumberColumn("Peak Alt (°)", format="%.0f°")`.

Not included in CSV/PDF export — the `_` prefix columns are already stripped before export. The renamed `"Peak Alt (°)"` column IS shown in the on-screen table only.

---

### Component 2 — Parameters summary block

A single `st.info()` line rendered **immediately above the Build Plan / CSV button row**, always visible. Dynamically built from active filter state at render time:

```
📋 Window: Feb 27 20:00 → Feb 28 01:00 (5 hrs)  •  Min alt: 20°  •  Moon sep: ≥ 30°  •  Az: All 360°  •  Priority: ⭐ PRIORITY, (unassigned)
```

**Rules for what appears:**
| Field | Shown when |
|---|---|
| Window | Always (always set) |
| Min alt | Always |
| Moon sep | Always |
| Az | Only when fewer than 8 directions selected |
| Priority | Only when section has a `pri_col` |
| Magnitude | Only when `vmag_col` and range is not the full default |
| Moon Status | Only when not all statuses are selected |

Implementation:
```python
_summary_parts = [
    f"Window: {_win_range[0].strftime('%b %d %H:%M')} → {_win_range[1].strftime('%b %d %H:%M')} ({_win_hrs:.0f} hrs)",
    f"Min alt: {min_alt}°",
    f"Moon sep: ≥ {min_moon_sep}°",   # NEW param passed in
]
if az_dirs:  # non-empty = filtered
    _summary_parts.append(f"Az: {', '.join(az_dirs)}")
if pri_col and _sel_pri:
    _summary_parts.append(f"Priority: {', '.join(_sel_pri)}")
...
st.info("📋 " + "  •  ".join(_summary_parts))
```

`min_moon_sep` and `az_dirs` become two more new parameters to `_render_night_plan_builder`.

---

## Parameters added to `_render_night_plan_builder`

| New param | Type | Source at call site |
|---|---|---|
| `location` | `EarthLocation \| None` | `location` (main app body) |
| `min_alt` | `int` | `min_alt` (from `alt_range` slider) |
| `min_moon_sep` | `int` | `min_moon_sep` (from sidebar slider) |
| `az_dirs` | `list[str]` | `az_dirs` (from compass grid session state) |

---

## What this fixes

| Issue | Before | After |
|---|---|---|
| Apophis at 14° included in plan | ✓ included (wrong) | ✗ excluded (correct) |
| Rising object at 15°→35° in window | ✓ included (correct) | ✓ included (correct, via peak alt) |
| User doesn't know why object is missing | No info | Parameters block above Build Plan button |
| Peak altitude during window not visible | Not shown | `Peak Alt (°)` column in plan table |

---

## Out of Scope

- PDF / CSV export does not include the parameters summary (on-screen only for now)
- `Peak Alt (°)` column is on-screen only, not in PDF/CSV
- No changes to the main observability loop (that uses sidebar start_time — intentional)
