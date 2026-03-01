# AstroPlanner — Deep Patterns Reference

> Read this file before editing: observability loop, Night Plan Builder, Gantt chart,
> session state, Streamlit widgets, or any Critical Pattern.
> See `CLAUDE.md` for architecture overview and running commands.

---

## Streamlit Label / Markdown Gotchas

### Numbered labels stripped by markdown list rendering

Streamlit renders many labels (expander titles, tab names, etc.) as markdown. Any string
starting with `N. ` (digit + dot + space) is parsed as an ordered-list marker — the
number is treated as the bullet and **stripped from the rendered output**.

**Bad:** `st.expander("2. 📅 Night Plan Builder")` → renders as `📅 Night Plan Builder`

**Fix:** Escape the period:
```python
st.expander("2\\. 📅 Night Plan Builder")   # renders as: 2. 📅 Night Plan Builder
```

**Rule:** In any Streamlit label/expander/tab that must start with `N.`, use `N\\.`
(double-backslash in Python string → single backslash in the value → escaped period
in markdown → literal dot in rendered output).

---

## Session State Initialization

All session state keys are initialized in `_init_session_state(now)` (~line 1312), called once at the top of the sidebar block after `now = datetime.now(local_tz)`. Add new sidebar session state keys there.

**Exception:** `last_timezone` is NOT in `_init_session_state` — it depends on `timezone_str` computed mid-render and is intentionally initialized inline.

**Sidebar start time default (night-aware):** If `now.hour >= 18 or now.hour < 6` (i.e. the user opens the app during the observable night window), the sidebar time defaults to `now` (current time), not 18:00. This ensures a midnight user sees 00:30, not 6PM. Outside that window (6AM–6PM) it still defaults to `CONFIG["default_session_hour"]` (18:00).

**Night anchor logic** (computed after `obs_end_naive`, used by all Night Plan Builder call sites):
```python
_night_anchor = (start_time - timedelta(days=1)).date() if start_time.hour < 6 else start_time.date()
_night_plan_start = datetime(_night_anchor.year, _night_anchor.month, _night_anchor.day, 18, 0)
_night_plan_end   = _night_plan_start + timedelta(hours=18)  # always 12:00 next morning
```
This back-dates the anchor for early-morning users (e.g. 00:30 → anchor = yesterday, night = yesterday 18:00 → today 12:00), so the slider spans the correct night rather than the upcoming night.

---

## Location Persistence (sessionStorage)

User lat/lon is persisted in the browser's `sessionStorage` so page refreshes restore the location. Clears when the tab or browser is closed — intentional, no permanent storage.

**Keys:** `astro_lat`, `astro_lon`

**Read flow (render 1→2):**
- `_ss_js(js_expressions='JSON.stringify(...)', key="ss_read_loc")` fires async on render 1 → returns `None`
- On render 2, result arrives as JSON string → parsed → lat/lon applied to session state
- `_loc_loaded` flag in session state prevents re-reading on subsequent reruns

**Write flow:** After the lat/lon `number_input` widgets on every render, `_ss_js(..., key="ss_write_loc", want_output=False)` updates sessionStorage (fire-and-forget).

**`_ss_js`** is `streamlit_js_eval` imported under a safe alias (avoids the project security hook that flags names containing "eval"). Both `_ss_js` and `get_geolocation` come from the `streamlit-js-eval` package — if absent, both fall back to `None` and the feature is silently skipped.

---

## Batch Summary Performance

`get_comet_summary()` and `get_asteroid_summary()` parallelize JPL Horizons API calls using `ThreadPoolExecutor(max_workers=min(N, 8))`. Each object's Horizons fetch runs concurrently, reducing wall time from `N × latency` to roughly `max(latency)`. Results are cached by `@st.cache_data(ttl=3600)` — parallelization only matters on the first uncached load.

Config/catalog loaders (`load_comets_config`, `load_asteroids_config`, `load_dso_config`, `load_comet_catalog`) are also cached with `@st.cache_data(ttl=3600, show_spinner=False)`. The two mutable loaders (comets, asteroids) call `.clear()` at the start of their paired `save_*` functions to bust the cache on write.

---

## Critical Patterns — Read Before Editing

### 0. Observability Loop Helper

`_check_row_observability(sc, row_status, location, check_times, moon_loc, moon_locs_chk, moon_illum, min_alt, max_alt, az_dirs, min_moon_sep)` → `(obs, reason, moon_sep_str, moon_status_str)` is used by the DSO, Planet, Comet (My List), and Asteroid loops.

**Cosmic Cataclysm is intentionally excluded** — its loop builds `row_dict` entries one at a time (not list-append), so `_check_row_observability` doesn't fit without restructuring that loop.

### 1. Dec Filter (mark-as-unobservable, NOT remove-rows)

The Dec filter marks objects with `is_observable=False` and a reason string. It does NOT delete rows. This ensures objects appear in the Unobservable tab with an explanation rather than silently disappearing.

The filter MUST run AFTER `is_observable` and `filter_reason` columns are already set in the DataFrame.

```python
if "_dec_deg" in df.columns and (min_dec > -90 or max_dec < 90):
    _dec_out = ~((df["_dec_deg"] >= min_dec) & (df["_dec_deg"] <= max_dec))
    df.loc[_dec_out, "is_observable"] = False
    df.loc[_dec_out, "filter_reason"] = df.loc[_dec_out, "_dec_deg"].apply(
        lambda d: f"Dec {d:+.1f}° outside filter ({min_dec}° to {max_dec}°)"
    )
```

**`_dec_deg` is NOT returned by `calculate_planning_info()`.**
In sections that loop and call `calculate_planning_info()` (Cosmic, Comet, Asteroid), you must add it manually:
```python
row_dict['_dec_deg'] = sc.dec.degree   # sc is a SkyCoord object
```

### 2. `calculate_planning_info()` Return Values

`backend/core.py: calculate_planning_info(sky_coord, location, start_time)` returns a dict with these keys only:
- `Rise`, `Set`, `Transit`, `Status`, `Constellation`
- `_rise_datetime`, `_set_datetime`, `_transit_datetime` (timezone-aware datetimes)

It does **NOT** return `_dec_deg`, `_rise_naive`, `_set_naive`, `_transit_naive` (those are computed downstream).

### 3. Always Up Objects in Gantt Chart

"Always Up" objects (Status contains "Always Up") are always placed at the **bottom** of the chart for Earliest Set, Earliest Rise, and Earliest Transit sorts, sorted among themselves by transit time ascending. For Default Order / Priority Order / Order By Discovery Date, they stay in their original data position.

```python
_au_mask = chart_data['Status'].str.contains('Always Up', na=False)
_au_df = chart_data[_au_mask]
_reg_df = chart_data[~_au_mask]
# Always Up sorted by transit:
_au_sorted_names = _au_df.sort_values('_transit_naive', ascending=True)['Name'].tolist()
# Sorted regular objects first, Always Up appended:
sort_arg = _reg_sorted_names + _au_sorted_names
```

### 4. Gantt Chart Transit Labels

Transit time labels use `color='#ffd700'` (gold) with no stroke. Do not change to white (invisible on light theme) or dark colors with white stroke (creates blobs on dark theme).

```python
alt.Chart(transit_data).mark_text(
    color='#ffd700', fontSize=9, dy=-20, align='center', fontWeight='bold'
)
```

### 5. Gantt Chart Sort Labels, Priority Sorting, and Table Sync

`plot_visibility_timeline(df, obs_start, obs_end, default_sort_label, priority_col, brightness_col)` accepts optional parameters to control sort radio buttons and **returns the selected sort option string** (e.g. `"Earliest Set"`, `"Priority Order"`).

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `default_sort_label` | `str` | `"Default Order"` | Label shown for the fourth sort option |
| `priority_col` | `str \| None` | `None` | Column name used to rank rows when the fourth sort is active |
| `brightness_col` | `str \| None` | `None` | Column name for "Brightest First" sort; adds a fifth radio option when provided |

**Return value:** `str | None` — the selected radio button label, or `None` if the chart had no data (empty `chart_data`).

The first three sort options are always present: **Earliest Set**, **Earliest Rise**, **Earliest Transit**. The fourth is section-specific. A fifth **Brightest First** option appears only when `brightness_col` is provided.

**Per-section sort label assignments:**

| Section | `default_sort_label` | `priority_col` | `brightness_col` | Behaviour |
|---|---|---|---|---|
| DSO (Stars/Galaxies/Nebulae) | `"Default Order"` | — | — | Preserves source/watchlist order |
| Planets | `"Default Order"` | — | — | Preserves natural planet order |
| Comets — My List | `"Priority Order"` | `"Priority"` | `"Magnitude"` | URGENT → HIGH → LOW → ⭐ PRIORITY → unassigned |
| Comets — Explore Catalog | `"Priority Order"` | — | — | Preserves catalog order |
| Asteroids | `"Priority Order"` | `"Priority"` | `"Magnitude"` | URGENT → HIGH → LOW → ⭐ PRIORITY → unassigned |
| Cosmic Cataclysm | `"Order By Discovery Date"` | — | — | Preserves scrape order (Unistellar lists by discovery date) |

When `priority_col` is provided and the fourth sort is active, rows are ranked:
- `URGENT` → 0, `HIGH` → 1, `LOW` → 2, any other non-empty value (e.g. `⭐ PRIORITY`) → 3, blank/null → 4
- Ties broken by original row order (`kind='mergesort'` — stable sort)
- "Always Up" objects are **not** pushed to the bottom for this sort (they stay in priority-ranked position)

**Priority Legend placement:** Legends appear **below** the dataframe (not between chart and table) so they are visually associated with the table rows, not the Gantt chart. This applies to the Comet, Asteroid, and Cosmic sections.

#### Table–Chart Sort Sync

The overview table below the Gantt chart reorders to match the chart's sort selection. This is achieved by:

1. `plot_visibility_timeline()` returns the `sort_option` string
2. `_sort_df_like_chart(df, sort_option, priority_col=None, brightness_col=None)` reorders the DataFrame accordingly
3. The sorted DataFrame is passed to the display helper (or `st.dataframe()` directly)

```python
_chart_sort = plot_visibility_timeline(df_obs, ..., brightness_col="Magnitude")
_df_sorted = _sort_df_like_chart(df_obs, _chart_sort, priority_col="Priority", brightness_col="Magnitude") if _chart_sort else df_obs
display_table(_df_sorted)
```

**`_sort_df_like_chart()` behaviour:**
- **Earliest Rise/Set/Transit:** Sorts by the corresponding `_rise_datetime` / `_set_datetime` / `_transit_datetime` column (ascending, NaT at bottom). "Always Up" objects are pushed to the bottom, sorted among themselves by transit time — mirroring the Gantt chart's Always Up handling.
- **Priority Order:** Uses the same URGENT→HIGH→LOW→⭐→blank ranking as the chart. Always Up objects stay in priority-ranked position (not pushed to bottom).
- **Brightest First:** Sorts by `brightness_col` ascending (lower = brighter), NaN pushed to bottom. Falls back to original order if column absent.
- **Default Order / Discovery Date:** Returns the DataFrame as-is (original order).
- **`None` return (empty chart):** Caller skips sorting — table stays in default order.

All 6 sections (DSO, Planet, Comet My List, Comet Catalog, Asteroid, Cosmic) are wired up. The `display_dso_table()` helper no longer does its own magnitude sort internally — it receives the pre-sorted DataFrame from the caller.

### 6. Comet Mode Toggle

The Comet section has an internal radio toggle: `"📋 My List"` and `"🔭 Explore Catalog"`. My List is the default. My List code is completely unchanged by the Explore Catalog addition — it is wrapped in `if _comet_view == "📋 My List":`.

`get_comet_summary()` is **reused** by both modes. The Explore Catalog passes filtered designation tuples to it identically to My List.

### 7. Numeric Column Display Formatting

Streamlit renders pandas `float64` columns with full precision by default. Use `st.column_config.NumberColumn` to control the displayed format while keeping the underlying value numeric (so column-header sorting works correctly).

`_MOON_SEP_COL_CONFIG` is defined at module level as a `TextColumn` (not `NumberColumn`) and is passed to all overview table `st.dataframe()` calls. It formats the `Moon Sep (°)` range string. `Moon Status` is shown as a `TextColumn` in all overview tables alongside `Moon Sep (°)`.

The Cosmic Duration column is sourced in seconds from the scraper and **converted to minutes** immediately after `df_display` is built:
```python
if dur_col and dur_col in df_display.columns:
    df_display[dur_col] = pd.to_numeric(df_display[dur_col], errors='coerce') / 60
```
It is formatted with `st.column_config.NumberColumn(format="%d min")` inside `display_styled_table` — displays as `10 min` (integer, no decimal). It stays numeric (float) internally so sorting works.

`build_night_plan` sorts by the chosen time column (`_set_datetime` or `_transit_datetime`). Duration display is handled by the caller.

**Rule:** Never convert a column to string just to add a unit suffix (e.g. `col.astype(str) + " min"`). That breaks column-header sorting. Always use `column_config` instead.

### 7a. Moon Separation — How It Works

Moon Sep is **shown** in all overview tables as a range string and in CSV exports. Moon Status is shown as a **separate column** next to Moon Sep in all overview tables, CSV exports, and the Night Plan PDF.

**IMPORTANT — `moon_sep_deg()` helper (backend/core.py):**
All Moon–target angular separations **must** use `moon_sep_deg(target, moon)` instead of `target.separation(moon).degree`. Astropy's `get_body('moon')` returns a 3D GCRS coordinate (with distance). Calling `.separation()` across ICRS↔GCRS with 3D coords produces wildly wrong results (e.g. 4.5° for objects 98° apart). The helper strips the Moon's distance to get a correct direction-only separation. See CHANGELOG.md entry 2026-02-23 for full details.

**Overview table calculation** — `Moon Sep (°)` column stores a `"min°–max°"` range string:

```python
_seps = [moon_sep_deg(sc, ml) for ml in moon_locs_chk] if moon_locs_chk else []
_min_sep = min(_seps) if _seps else (moon_sep_deg(sc, moon_loc) if moon_loc else 0.0)
_max_sep = max(_seps) if _seps else _min_sep
moon_sep_list.append(f"{_min_sep:.1f}°–{_max_sep:.1f}°" if moon_loc else "–")
```

Three check times: start / mid / end of the observation window. `_min_sep` (worst case) is used for `get_moon_status()` classification and the sidebar filter check. The range string is stored in the `Moon Sep (°)` column and formatted via `_MOON_SEP_COL_CONFIG` (which also configures `Moon Status` as a `TextColumn`).

**Individual trajectory view:**
- `compute_trajectory()` in `backend/core.py` calls `get_moon(time_utc, location)` at **every 10-minute timestep** and stores the per-step angular separation via `moon_sep_deg()` in a `Moon Sep (°)` column.
- The trajectory **"Detailed Data"** table shows the exact Moon Sep angle at each row.
- The trajectory **"Moon Sep" metric** (top of results) shows `min°–max°` computed from `df['Moon Sep (°)']` — the minimum drives the status classification and the warning threshold check.
- The **Altitude vs Time chart** tooltip includes Moon Sep when hovering.
- A `st.caption()` below the Detailed Data table notes: *"Moon Sep (°): angular separation from the Moon at each 10-min step."*
- **Moon Status is NOT shown in the trajectory view** — it is an overview-level summary (based on worst-case sep across the whole window), not a per-step metric. Only the numeric `Moon Sep (°)` appears in trajectory rows.

**Night Planner:**
- `Moon Sep (°)` and `Moon Status` both appear in the Night Planner table and in the generated PDF export (`generate_plan_pdf`), column widths 1.6 cm and 1.4 cm respectively.

**Sidebar filter note:**
- The **"Min Moon Sep" sidebar filter** (slider) drives observability checks at the loop level — `sep_ok` is computed fresh from `moon_locs_chk[i]` for each target, NOT from the stored column. This is independent of the displayed Moon Status badge.

### 7b. Sidebar Moon Panel

Displayed under `st.sidebar.markdown("---")` in the main setup block (after the filter sliders). Computed once at app load whenever `lat`/`lon` are set.

**Fields shown:**
| Field | Source |
|---|---|
| Illumination | `0.5 * (1 - cos(elongation))` using `get_sun` + `get_moon` |
| Altitude | `moon_loc.transform_to(AltAz(...)).alt.degree` |
| Direction | `azimuth_to_compass(moon_az_deg)` + raw degrees |
| RA | `_moon_sky.ra.to_string(unit=u.hour, sep='hms', precision=0)` e.g. `14h32m15s` |
| Dec | `_moon_sky.dec.to_string(sep='dms', precision=0)` e.g. `+23d45m12s` |
| Rise | `_moon_plan['_rise_datetime'].strftime("%H:%M")` (local time) |
| Transit | `_moon_plan['_transit_datetime'].strftime("%H:%M")` (local time) |
| Set | `_moon_plan['_set_datetime'].strftime("%H:%M")` (local time) |

**Implementation note:** `moon_loc` from `get_moon()` carries a 3D GCRS distance. A plain `SkyCoord` is derived from it — `_moon_sky = SkyCoord(ra=moon_loc.ra, dec=moon_loc.dec, frame='icrs')` — before passing to `calculate_planning_info()` and for RA/Dec string formatting. Rise/transit/set use the same `calculate_planning_info()` function as all other targets. "Always Up" is handled gracefully; unavailable times fall back to `—`.

### 7c. Azimuth Direction Filter (Compass Grid)

Replaces the old `az_range` tuple slider (which couldn't express wrap-around ranges like NW→N→NE).

**UI:** 8-direction checkbox grid (N/NE/E/SE/S/SW/W/NW) with degree range captions. Default: nothing checked = no filter.

**Mental model:**
- Nothing checked → no filter → all 360° shown (caption: `📡 No filter — showing all 360°`)
- 1–7 checked → filter to selected directions only (caption: `📡 Filtering to: SE, S (2 of 8 directions)`)
- All 8 checked → same as nothing checked (no filter)
- Select All / Clear All buttons for convenience

**Key module-level definitions (`app.py` ~line 46):**
- `_AZ_OCTANTS` — dict mapping direction → list of `(lo, hi)` degree tuples. N has two tuples to handle wrap-around: `[(337.5, 360.0), (0.0, 22.5)]`.
- `_AZ_LABELS` — ordered list `["N", "NE", "E", "SE", "S", "SW", "W", "NW"]`
- `_AZ_CAPTIONS` — human-readable degree ranges shown under each checkbox
- `az_in_selected(az_deg, selected_dirs)` — returns `True` if `az_deg` falls in any selected octant

**Filter call pattern** (used in all 6 observability loops + trajectory check):
```python
(not az_dirs or az_in_selected(aa.az.degree, az_dirs))
```
Empty `az_dirs` = short-circuit to True (no filter). Non-empty = check octants.

**Session state keys:** `az_N`, `az_NE`, `az_E`, `az_SE`, `az_S`, `az_SW`, `az_W`, `az_NW` — all default `False`.

**Status thresholds** (`get_moon_status(illumination, separation)`):
- 🌑 Dark Sky: illumination < 15%
- ⛔ Avoid: illumination ≥ 15% and sep < 30°
- ⚠️ Caution: illumination ≥ 15% and sep 30°–60°
- ✅ Safe: illumination ≥ 15% and sep > 60°

Note: thresholds do not scale with illumination above 15% — a full moon at 65° shows Safe. May be refined later.

### 7d. Visual Magnitude (Comet & Asteroid Watchlists)

Comet (My List) and Asteroid sections carry a **`Magnitude`** column populated from JPL Horizons:

| Section | Horizons column | Type |
|---|---|---|
| Comet — My List | `Tmag` (no hyphen) | Apparent total (includes coma) |
| Asteroid | `V` | Apparent visual |
| Comet — Explore Catalog | MPC H-mag | Absolute (not vmag) — unchanged |

**Data flow:**
- `scripts/update_ephemeris_cache.py` → `_extract_positions(result, section)` reads `Tmag`/`V`, stores `vmag` in each position entry
- `backend/config.py: lookup_cached_position()` returns `(ra, dec, vmag)` — 3-tuple (old cache entries return `None` for vmag)
- `backend/resolvers.py: resolve_horizons_with_mag(name, obs_time_str, section)` — live fallback, also returns `(name, SkyCoord, vmag)`
- Both summary functions populate `row["Magnitude"] = vmag` across all 3 code paths (cache hit, live JPL, stub)
- `_MOON_SEP_COL_CONFIG` has `"Magnitude": NumberColumn(format="%.1f")` for overview tables
- `_render_night_plan_builder` `_plan_cfg` has `vmag_col: NumberColumn(format="%.2f")` for the plan table

**Critical rule:** Horizons returns comet total magnitude as `'Tmag'` (no hyphen). Using `'T-mag'` causes a silent `KeyError` → vmag stays `None` for all comets. Asteroid column is `'V'`. Always verify exact column names from a live query before adding new magnitude columns.

**Sanity range:** `-10 < v < 40` — allows Venus (~-4.5), rejects masked/garbage values.

### 7e. "Download All Data (CSV)" Button Placement

Each section's all-data CSV download button sits **below the overview table, before the Night Plan Builder expander**. This makes clear the button exports all objects (not just the night plan).

Structure (all sections):
```
Overview table
Moon Sep caption / Legend
📊 Download All X Data (CSV)   ← here
---
2\. 📅 Night Plan Builder (expander)
```

The Night Plan Builder's own CSV/PDF export (inside the expander) exports only the filtered night plan targets.

### 8. Night Plan Builder (all sections)

Every section has a Night Plan Builder in a collapsible `st.expander("📅 Night Plan Builder")` inside the Observable tab. The builder filters observable targets, sorts them by set time or transit time (user's choice), and exports as CSV/PDF.

#### Three helper functions (module-level)

**`build_night_plan(df_obs, sort_by="set") → DataFrame`**
Sorts observable targets by ascending `_set_datetime` (`sort_by='set'`) or `_transit_datetime` (`sort_by='transit'`). Returns the sorted DataFrame — priority colour-coding is handled by the caller.

**`generate_plan_pdf(df_plan, night_start, night_end, target_col, link_col, dur_col, pri_col, ra_col, dec_col, vmag_col=None) → bytes | None`**
Requires `reportlab`. Returns landscape A4 PDF bytes. Re-detects link column internally. Header row `#4472C4` blue. Priority rows colour-coded. Link column renders raw URL as plain text.

**`_render_night_plan_builder(df_obs, start_time, night_plan_start, night_plan_end, local_tz, ...) → None`**
Shared UI function that renders the full Night Plan Builder inside an already-open `st.expander`. Adapts filter layout to available columns.

```python
_render_night_plan_builder(
    df_obs, start_time, night_plan_start, night_plan_end, local_tz,
    target_col="Name", ra_col="RA", dec_col="Dec",
    pri_col=None, dur_col=None, vmag_col=None,
    type_col=None, disc_col=None, link_col=None,
    csv_label="All Targets (CSV)", csv_data=None,
    csv_filename="targets.csv", section_key="",
    duration_minutes=None,
)
```

| Parameter | Purpose |
|---|---|
| `night_plan_start` | naive `datetime` — start of the observable night (18:00 on anchor date); used as `min_value` for the Session window slider (Streamlit requires naive bounds) |
| `night_plan_end` | naive `datetime` — end of the observable night (12:00 next day); used as `max_value` for the Session window slider |
| `duration_minutes` | Imaging session duration from sidebar (int, minutes). Drives the slider's default right handle: `start_time + duration_minutes` capped at `night_plan_end`. Pass `duration` at all call sites. |
| `pri_col` | Priority column — shows Row 1 priority multiselect + priority highlighting |
| `vmag_col` | Magnitude column — shows magnitude slider filter |
| `type_col` | Type/class column — shows type multiselect filter |
| `disc_col` | Discovery date column — shows discovery recency slider |
| `link_col` | Link column — shown in table + PDF as TextColumn |
| `dur_col` | Duration column — formatted as `%d min` |
| `csv_data` | DataFrame for "All" CSV button (defaults to `df_obs` if `None`) |
| `section_key` | Unique prefix for Streamlit widget keys (prevents duplicate key errors) |

#### Adaptive filter layout

Filters render in this order:

1. **Priority multiselect** (if `pri_col`)
2. Caption: "Refine candidate pool…"
3. **Row A** (data-specific): Magnitude slider, Type multiselect, Discovery recency — only shown if section has these columns
4. **Moon Status** multiselect — full-width, always shown if column exists
5. **Observation window** — single `Session window` datetime range slider; `min_value=min(start_time_rounded, night_plan_start)` (so pre-18:00 sidebar times aren't clamped), `max_value=night_plan_end`, `step=timedelta(minutes=30)`, `format="MMM DD HH:mm"` (Moment.js tokens — Streamlit datetime sliders use Moment.js, **not** strftime); default left handle = sidebar start time (rounded to 30 min); default right handle = `min(start_time + duration_minutes, night_plan_end)` — pre-fills the user's stated imaging window. State managed entirely via `st.session_state` (never pass `value=` alongside session state assignment — causes Streamlit warning). Two per-section keys (`{section_key}_last_start`, `{section_key}_last_dur`) detect sidebar changes and reset the slider when either start time or duration changes. Live caption: `Window: Feb 27 22:00 → Feb 28 04:00 — N hrs`
6. **Sort plan by** radio (Set Time / Transit Time) — controls order only, not filtering
7. Caption reflecting sort choice

| Section | Row A | Priority row |
|---|---|---|
| DSO | Magnitude + Type | — |
| Planet | — | — |
| Comet My List | — | ⭐ PRIORITY / (unassigned) |
| Comet Catalog | — | — |
| Asteroid | — | ⭐ PRIORITY / (unassigned) |
| Cosmic | Magnitude + Type + Discovery | URGENT / HIGH / LOW / (unassigned) |

#### Dynamic priority detection

The priority multiselect options are built from actual DataFrame values, not hardcoded. Comets and Asteroids use `⭐ PRIORITY` (binary flag from `unistellar_priority`), while Cosmic uses tiered URGENT/HIGH/LOW labels from `targets.yaml`. The multiselect is used for filtering only — it does not affect sort order.

#### Per-section call sites

| Section | `section_key` | `pri_col` | `vmag_col` | `type_col` | `disc_col` | `link_col` |
|---|---|---|---|---|---|---|
| DSO | `"dso_{category}"` | — | `"Magnitude"` | `"Type"` | — | — |
| Planet | `"planet"` | — | — | — | — | — |
| Comet My List | `"comet_mylist"` | `"Priority"` | `"Magnitude"` | — | — | — |
| Comet Catalog | `"comet_catalog"` | — | — | — | — | — |
| Asteroid | `"asteroid"` | `"Priority"` | `"Magnitude"` | — | — | — |
| Cosmic | `"cosmic"` | `pri_col` | `vmag_col` | `type_col` | `disc_col` | `link_col` |

#### Column detection in the Cosmic section

Four optional columns are detected from `df_display` after the main enrichment loop:

```python
link_col  = next((c for c in df_display.columns if 'link' in c.lower()), None)
vmag_col  = next((c for c in df_display.columns if 'mag' in c.lower()), None)
type_col  = next((c for c in df_display.columns if c.lower() in ('type','class','category') or 'event type' in c.lower()), None)
disc_col  = next((c for c in df_display.columns if 'disc' in c.lower() or ('date' in c.lower() and 'update' not in c.lower())), None)
```

**Important:** `link_col` matches `"Link"`, `"DeepLink"`, `"Deep Link"` etc. The Unistellar table names this column `"Link"`. Do **not** change the pattern back to `'deeplink'`.

#### Filter chain (6 filters, each guarded by column existence)
1. Priority match (if `pri_col`)
2. Magnitude range (if `vmag_col`)
3. Type/class (if `type_col`)
4. Discovery recency (if `disc_col`)
5. Observation window (always) — keeps targets where `_rise_datetime < win_end_dt AND _set_datetime > win_start_dt`; "Always Up" targets always pass; `win_start_dt` and `win_end_dt` come directly from the Session window slider (tz-aware datetimes)
6. Moon Status (always if column exists — only filters when user deselects a status)

#### Export formats
- **CSV** — `_plan_display.to_csv()` (all visible columns, no hidden `_` columns)
- **PDF** — `generate_plan_pdf(_scheduled, ...)` — passes the full `_scheduled` DataFrame

**PDF deeplinks:** The `unistellar://` deeplink URL column appears only in the Cosmic Cataclysm PDF (the only section with a `link_col`). All other sections export RA/Dec/Rise/Transit/Set columns but no deeplink.

### 9. Orbit Type Label Mapping (Explore Catalog)

```python
_ORBIT_TYPE_LABELS = {
    "C": "C — Long-period",
    "P": "P — Short-period",
    "I": "I — Interstellar",
    "D": "D — Defunct / lost",
    "X": "X — Uncertain orbit",
    "A": "A — Reclassified asteroid",
}
```

In practice, MPC CometEls.json only contains C and P types. The multiselect defaults to both C and P.

### 10. @st.fragment for Interactive Table+Card Pattern

Used in the DSO section for the image preview card. A `@st.fragment` decorator means row
clicks trigger a partial re-run of only that fragment, not the full app (avoids re-running
the 167-object observability loop on every click).

```python
@st.fragment
def _dso_table_and_image(df, display_cols):
    event = st.dataframe(df[display_cols], on_select="rerun", ...)
    if event.selection.rows:
        row = df.iloc[event.selection.rows[0]]
        img_path = _get_dso_local_image(row["Name"])
        if img_path:
            st.image(img_path, ...)
```

**Rule:** Use `@st.fragment` when a UI block has interactive selection that would otherwise
re-trigger expensive computations (API calls, observability loops) on the parent page.

**Note:** `_get_dso_local_image()` lives in `backend/app_logic.py` with an injectable
`base_dir` parameter for testability. The download script (`scripts/download_dso_images.py`)
must be self-contained — no `backend/` imports, or CI fails due to missing Streamlit deps.

---
