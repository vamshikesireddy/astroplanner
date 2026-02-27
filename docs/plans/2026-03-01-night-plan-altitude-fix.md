# Night Plan Altitude-Aware Filter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the Night Plan Builder so it only includes objects that actually reach `min_alt` during the session window, and show the user a parameters summary above the Build Plan button.

**Architecture:** Add `compute_peak_alt_in_window()` to `backend/core.py` (testable unit), store `_ra_deg` in all section DataFrames alongside existing `_dec_deg`, thread `location`/`min_alt`/`min_moon_sep`/`az_dirs` into `_render_night_plan_builder` and `_apply_night_plan_filters`, replace the horizon-only window filter with an altitude-sampling loop, add a `Peak Alt (°)` column to the plan table, and render a one-line `st.info()` parameters summary above the Build Plan button.

**Tech Stack:** Astropy (SkyCoord, AltAz, Time, EarthLocation), pandas, Streamlit, pytest

---

## Context: What's Broken and Where

- **Bug:** `_apply_night_plan_filters` (app.py:654) uses `_rise_datetime`/`_set_datetime` (horizon-crossing at alt=0°) for the window check. An object like Apophis can be at 14° and declining at window start (20:00) but still pass because `set_time(21:16) > win_start(20:00)`.
- **Root cause:** The observability check runs at sidebar `start_time` (e.g. 15:00 when Apophis is near transit at ~30°), not at the Night Plan Builder's session window.
- **Fix:** Sample altitude at N points across the window (every 30 min); only include if peak altitude ≥ `min_alt`.
- **New feature:** Show active filter parameters in a `st.info()` block above the Build Plan button.

---

## Task 1: Add `compute_peak_alt_in_window()` to `backend/core.py`

**Files:**
- Modify: `backend/core.py` (append to end of file)
- Test: `tests/test_core.py` (append 3 new tests)

### Step 1: Write the failing tests

Append to `tests/test_core.py`:

```python
# ── compute_peak_alt_in_window ────────────────────────────────────────────────

def test_compute_peak_alt_in_window_returns_valid_range():
    """Peak altitude is always a float in [-90, 90]."""
    from datetime import datetime
    import pytz
    from astropy.coordinates import EarthLocation
    import astropy.units as u
    from backend.core import compute_peak_alt_in_window

    loc = EarthLocation(lat=40.7 * u.deg, lon=-74.0 * u.deg)
    tz  = pytz.timezone('America/New_York')
    win_start = tz.localize(datetime(2026, 7, 1, 21, 0))
    win_end   = tz.localize(datetime(2026, 7, 1, 23, 0))
    # Vega: RA=279.23, Dec=+38.78
    peak = compute_peak_alt_in_window(279.23, 38.78, loc, win_start, win_end)
    assert isinstance(peak, float)
    assert -90.0 <= peak <= 90.0


def test_compute_peak_alt_in_window_high_object():
    """Object transiting near zenith returns altitude well above 30°."""
    from datetime import datetime
    import pytz
    from astropy.coordinates import EarthLocation
    import astropy.units as u
    from backend.core import compute_peak_alt_in_window

    # Vega (Dec ≈ +38.8°) transits at altitude ≈ 88° from lat=40.7°N
    loc = EarthLocation(lat=40.7 * u.deg, lon=-74.0 * u.deg)
    tz  = pytz.timezone('America/New_York')
    win_start = tz.localize(datetime(2026, 7, 1, 21, 0))
    win_end   = tz.localize(datetime(2026, 7, 1, 23, 0))
    peak = compute_peak_alt_in_window(279.23, 38.78, loc, win_start, win_end)
    assert peak > 30.0, f"Vega near transit should exceed 30°, got {peak:.1f}°"


def test_compute_peak_alt_in_window_n_steps_minimum():
    """n_steps=2 (minimum) still returns a valid float."""
    from datetime import datetime
    import pytz
    from astropy.coordinates import EarthLocation
    import astropy.units as u
    from backend.core import compute_peak_alt_in_window

    loc = EarthLocation(lat=40.7 * u.deg, lon=-74.0 * u.deg)
    tz  = pytz.timezone('America/New_York')
    win_start = tz.localize(datetime(2026, 7, 1, 21, 0))
    win_end   = tz.localize(datetime(2026, 7, 1, 23, 0))
    peak = compute_peak_alt_in_window(279.23, 38.78, loc, win_start, win_end, n_steps=2)
    assert isinstance(peak, float)
    assert -90.0 <= peak <= 90.0
```

### Step 2: Run tests to verify they fail

```bash
cd C:\Users\vamsh\Desktop\projects\astro_coordinates
python -m pytest tests/test_core.py::test_compute_peak_alt_in_window_returns_valid_range -v
```
Expected: `FAILED` with `ImportError: cannot import name 'compute_peak_alt_in_window'`

### Step 3: Implement the function

First check what is already imported at the top of `backend/core.py` — it already imports `SkyCoord`, `AltAz`, `EarthLocation`, `Time`, `u` (astropy.units). Do **not** re-import them.

Append to the **end** of `backend/core.py`:

```python
def compute_peak_alt_in_window(ra_deg, dec_deg, location, win_start_dt, win_end_dt, n_steps=None):
    """Return the peak altitude (degrees) of an object during an observation window.

    Samples altitude at uniform intervals across the window. n_steps defaults
    to one sample per 30 minutes, minimum 2.

    Parameters
    ----------
    ra_deg, dec_deg : float
        ICRS coordinates in decimal degrees.
    location : EarthLocation
    win_start_dt, win_end_dt : datetime (tz-aware)
        Start and end of the observation window.
    n_steps : int | None
        Number of altitude samples. Auto-computed from window duration if None.

    Returns
    -------
    float
        Peak altitude in degrees. Can be negative if the object is always
        below the horizon during the window.
    """
    import pytz
    sc = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame='icrs')
    window_secs = (win_end_dt - win_start_dt).total_seconds()
    if n_steps is None:
        n_steps = max(2, int(window_secs / 1800) + 1)  # one per 30 min, min 2

    peak = -90.0
    for i in range(n_steps):
        frac = i / max(n_steps - 1, 1)
        t_sample = win_start_dt + timedelta(seconds=frac * window_secs)
        t_utc = Time(
            t_sample.astimezone(pytz.utc).replace(tzinfo=None),
            scale='utc',
        )
        aa = sc.transform_to(AltAz(obstime=t_utc, location=location))
        if aa.alt.deg > peak:
            peak = aa.alt.deg

    return float(peak)
```

### Step 4: Verify tests pass

```bash
python -m pytest tests/test_core.py -v
```
Expected: all 3 new tests PASS, all previous core tests still PASS (≥11 tests total in this file).

### Step 5: Syntax check and commit

```bash
python -m py_compile backend/core.py && echo OK
python -m pytest tests/ -v --tb=short
git add backend/core.py tests/test_core.py
git commit -m "feat: add compute_peak_alt_in_window to backend/core.py"
```

---

## Task 2: Add `_ra_deg` to all section summary DataFrames

**Files:**
- Modify: `app.py` — 5 locations

The existing `_dec_deg` column is already stored in all 5 sections. Add `_ra_deg` in the same place.

### Step 1: Read the 5 locations in app.py

Before editing, verify the line numbers by searching for each `_dec_deg` assignment.

**`get_dso_summary` (line ~1575):**
```python
# BEFORE:
"_dec_deg": dec_deg,
# AFTER (add _ra_deg on the line immediately before or after):
"_dec_deg": dec_deg,
"_ra_deg":  sky_coord.ra.deg,
```
Note: in `get_dso_summary` the variable may be `sc` instead of `sky_coord` — check and use whichever name holds the `SkyCoord` object.

**`get_comet_summary` (line ~1256):**
```python
# BEFORE:
"_dec_deg": sky_coord.dec.degree,
# AFTER:
"_dec_deg": sky_coord.dec.degree,
"_ra_deg":  sky_coord.ra.deg,
```

**`get_asteroid_summary` (line ~1455):**
```python
# BEFORE:
"_dec_deg": sky_coord.dec.degree,
# AFTER:
"_dec_deg": sky_coord.dec.degree,
"_ra_deg":  sky_coord.ra.deg,
```

**Planet inline loop (line ~184):**
```python
# BEFORE:
"_dec_deg": sky_coord.dec.degree,
# AFTER:
"_dec_deg": sky_coord.dec.degree,
"_ra_deg":  sky_coord.ra.deg,
```

**Cosmic inline loop (line ~3917):**
```python
# BEFORE:
row_dict['_dec_deg'] = sc.dec.degree
# AFTER:
row_dict['_dec_deg'] = sc.dec.degree
row_dict['_ra_deg']  = sc.ra.deg
```

### Step 2: Add `compute_peak_alt_in_window` to the existing `backend.core` import in app.py

Find the line in app.py that imports from `backend.core` (search for `from backend.core import`). Add `compute_peak_alt_in_window` to it.

### Step 3: Syntax check

```bash
python -m py_compile app.py && echo OK
```

### Step 4: Run all tests

```bash
python -m pytest tests/ -v --tb=short
```
Expected: 63 tests PASS (no regressions — tests don't test internal dict keys of summary functions directly).

### Step 5: Commit

```bash
git add app.py
git commit -m "feat: add _ra_deg column to all section summary DataFrames"
```

---

## Task 3: Update `_apply_night_plan_filters` — altitude-aware window filter

**Files:**
- Modify: `app.py:654` — `_apply_night_plan_filters` function

### Step 1: Read the current function

Read lines 654–751 of app.py to see the current full function before editing.

### Step 2: Add `location` and `min_alt` parameters to the signature

Current signature (line 654):
```python
def _apply_night_plan_filters(
    df,
    pri_col, sel_pri,
    vmag_col, vmag_range,
    type_col, sel_types,
    disc_col, disc_days,
    win_start_dt, win_end_dt,
    sel_moon, all_moon_statuses,
):
```

Replace with:
```python
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
```

### Step 3: Replace the `_in_obs_window` block

Find the existing block (lines ~729–744):
```python
    def _in_obs_window(row):
        status = str(row.get('Status', ''))
        if 'Always Up' in status:
            return True
        r = row.get('_rise_datetime')
        s = row.get('_set_datetime')
        if pd.isnull(r) or pd.isnull(s):
            return True  # keep if timing unknown
        try:
            # Visible during window if rises before window ends AND sets after window starts
            return r < win_end_dt and s > win_start_dt
        except (TypeError, ValueError):
            return True

    if '_rise_datetime' in out.columns and '_set_datetime' in out.columns:
        out = out[out.apply(_in_obs_window, axis=1)]
```

Replace entirely with:
```python
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
```

### Step 4: Syntax check and run tests

```bash
python -m py_compile app.py && echo OK
python -m pytest tests/ -v --tb=short
```
Expected: all 63 tests PASS. (The function signature change has default values — existing calls without `location`/`min_alt` still work unchanged.)

### Step 5: Commit

```bash
git add app.py
git commit -m "feat: altitude-aware window filter in _apply_night_plan_filters"
```

---

## Task 4: Update `_render_night_plan_builder` — new params + parameters summary block

**Files:**
- Modify: `app.py:754` — `_render_night_plan_builder` function signature and body

### Step 1: Add 4 new parameters to the function signature

Current signature (line 754):
```python
def _render_night_plan_builder(
    df_obs, start_time, night_plan_start, night_plan_end, local_tz,
    target_col="Name", ra_col="RA", dec_col="Dec",
    pri_col=None, dur_col=None, vmag_col=None,
    type_col=None, disc_col=None, link_col=None,
    csv_label="All Targets (CSV)", csv_data=None,
    csv_filename="targets.csv", section_key="",
    duration_minutes=None,
):
```

Replace with:
```python
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
```

### Step 2: Pass `location` and `min_alt` through to `_apply_night_plan_filters`

Find the `_apply_night_plan_filters(` call inside `_render_night_plan_builder` (around line 963). It currently ends with:
```python
            win_start_dt=_win_start_dt, win_end_dt=_win_end_dt,
            sel_moon=_sel_moon,     all_moon_statuses=_all_moon_statuses,
        )
```

Add the two new keyword args:
```python
            win_start_dt=_win_start_dt, win_end_dt=_win_end_dt,
            sel_moon=_sel_moon,     all_moon_statuses=_all_moon_statuses,
            location=location,
            min_alt=min_alt,
        )
```

### Step 3: Add the parameters summary block

Find the section in `_render_night_plan_builder` that renders the sort radio + sort caption (around lines 930–941), which ends just before the `# ── Row 3: action buttons` comment. Insert the parameters summary block between the sort caption and the buttons:

```python
        # ── Parameters summary ─────────────────────────────────────────────
        _win_hrs = (_win_range[1] - _win_range[0]).total_seconds() / 3600
        _summary_parts = [
            (f"Window: {_win_range[0].strftime('%b %d %H:%M')} → "
             f"{_win_range[1].strftime('%b %d %H:%M')} ({_win_hrs:.0f} hrs)"),
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
```

`_AZ_LABELS` is a module-level list in app.py — accessible here without any import.

### Step 4: Syntax check and run tests

```bash
python -m py_compile app.py && echo OK
python -m pytest tests/ -v --tb=short
```
Expected: all 63 tests PASS.

### Step 5: Commit

```bash
git add app.py
git commit -m "feat: parameters summary block in _render_night_plan_builder"
```

---

## Task 5: Show `Peak Alt (°)` in plan table + update all 6 call sites

**Files:**
- Modify: `app.py` — plan table display block + 6 call sites

### Step 1: Add `Peak Alt (°)` to the plan table display

Inside `_render_night_plan_builder`, find the block that builds `_plan_display` from `_scheduled` (look for `_plan_display` and `_plan_cfg`). After `_scheduled` is created by `build_night_plan(...)` and before `_plan_display` is built, insert:

```python
                # Expose peak-alt column for display
                _peak_alt_display_col = None
                if '_peak_alt_window' in _scheduled.columns:
                    _peak_alt_display_col = 'Peak Alt (°)'
                    _scheduled = _scheduled.rename(
                        columns={'_peak_alt_window': 'Peak Alt (°)'}
                    )
```

Then find where `_plan_cfg` is built (a dict of column configs). Add the Peak Alt config:

```python
                if _peak_alt_display_col:
                    _plan_cfg[_peak_alt_display_col] = st.column_config.NumberColumn(
                        'Peak Alt (°)', format="%.0f°"
                    )
```

### Step 2: Update all 6 call sites

Add `location=location, min_alt=min_alt, min_moon_sep=min_moon_sep, az_dirs=az_dirs` to each of the 6 calls. All 6 are in the main app body where these variables are already in scope.

**Call site 1 — DSO (line ~2101):** add before the closing `)`
```python
    location=location, min_alt=min_alt, min_moon_sep=min_moon_sep, az_dirs=az_dirs,
```

**Call site 2 — Planet (line ~2285):** same addition

**Call site 3 — Comet My List (line ~2788):** same addition

**Call site 4 — Comet Catalog (line ~2993):** same addition

**Call site 5 — Asteroid (line ~3492):** same addition

**Call site 6 — Cosmic (line ~4112):** same addition

### Step 3: Verify `az_dirs` is the correct variable name at all 6 call sites

In the main app body, `az_dirs` is computed at line ~1855:
```python
az_dirs = {_d for _d in _AZ_LABELS if st.session_state.get(f"az_{_d}", False)}
```
This is a `set`. It is in scope at all 6 section render blocks. No changes needed.

### Step 4: Full syntax check and test run

```bash
python -m py_compile app.py backend/core.py && echo OK
python -m pytest tests/ -v --tb=short
```
Expected: **63 tests PASS** (no regressions).

### Step 5: Final commit

```bash
git add app.py
git commit -m "feat: show Peak Alt column in night plan table, wire new params to all call sites"
```

---

## Final Verification (manual)

1. Open the app
2. Set sidebar: start time = 15:00, duration = 10 hrs, min alt = 20°
3. Open Asteroid section → Night Plan Builder
4. Set session window to 20:00 → 01:00
5. Press **Build Plan**
6. Verify:
   - **99942 Apophis is NOT in the plan** (peak alt ~14° during 20:00→01:00 < 20°)
   - Objects that are above 20° at some point in the window ARE included
   - `Peak Alt (°)` column is visible in the plan table
   - Parameters summary shows `Window: Feb 27 20:00 → Feb 28 01:00 (5 hrs)  •  Min alt: 20°  •  Moon sep: ≥ 0°`

---

## Summary of Changes

| File | What changes |
|---|---|
| `backend/core.py` | New `compute_peak_alt_in_window()` function |
| `tests/test_core.py` | 3 new tests for `compute_peak_alt_in_window` |
| `app.py` | `_ra_deg` added to 5 section DataFrames; `_apply_night_plan_filters` gains `location`/`min_alt` + new altitude-sampling loop with `_peak_alt_window` column; `_render_night_plan_builder` gains 4 new params + parameters summary block + Peak Alt display; 6 call sites updated |
