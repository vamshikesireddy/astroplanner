# CLAUDE.md — Astro Coordinates Planner

Developer guide for Claude (and human contributors) working on this codebase.

---

## Architecture at a Glance

```
app.py (Streamlit UI + all section logic)
  ├── backend/core.py          (rise/set/transit calculations)
  ├── backend/resolvers.py     (SIMBAD + JPL Horizons API calls)
  └── backend/scrape.py        (Selenium scrapers for Unistellar pages)

scripts/                       (standalone CLI tools run by GitHub Actions)
  ├── update_comet_catalog.py  (MPC download → comets_catalog.json)
  ├── check_new_comets.py      (JPL SBDB query → _new_comets.json)
  └── open_comet_issues.py     (_new_comets.json → GitHub Issues)

Data files (tracked in git):
  comets.yaml            ← curated watchlist (~24 comets)
  comets_catalog.json    ← MPC archive snapshot (~865 comets, updated weekly)
  asteroids.yaml         ← curated watchlist
  dso_targets.yaml       ← static DSO catalog (Messier + stars + favorites)
  targets.yaml           ← Cosmic Cataclysm priorities/blocklist
```

---

## Sections in app.py

Each of the six target modes follows the same structure:

1. **Batch summary** — `get_X_summary()` returns a DataFrame (cached `ttl=3600`)
2. **Observability check** — loop sets `is_observable` + `filter_reason` per row
3. **Dec filter** — applied AFTER step 2 (see pattern below)
4. **Observable / Unobservable tabs** — split by `is_observable` column
5. **Gantt chart** — `plot_visibility_timeline(df, ...)`
6. **Trajectory picker** — user selects one object → `resolve_*()` → altitude chart

Sections: DSO (Star/Galaxy/Nebula), Planet, Comet, Asteroid, Cosmic Cataclysm, Manual.

---

## Critical Patterns — Read Before Editing

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

"Always Up" objects (Status contains "Always Up") are always placed at the **bottom** of the chart for Earliest Set and Earliest Rise sorts, sorted among themselves by transit time ascending. For Default Order / Priority Order / Order By Discovery Date, they stay in their original data position.

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

### 5. Gantt Chart Sort Labels and Priority Sorting

`plot_visibility_timeline(df, obs_start, obs_end, default_sort_label, priority_col)` accepts two optional parameters to control the third sort radio button:

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `default_sort_label` | `str` | `"Default Order"` | Label shown for the third sort option |
| `priority_col` | `str \| None` | `None` | Column name used to rank rows when the third sort is active |

**Per-section sort label assignments:**

| Section | `default_sort_label` | `priority_col` | Behaviour |
|---|---|---|---|
| DSO (Stars/Galaxies/Nebulae) | `"Default Order"` | — | Preserves source/watchlist order |
| Planets | `"Default Order"` | — | Preserves natural planet order |
| Comets — My List | `"Priority Order"` | `"Priority"` | URGENT → HIGH → LOW → ⭐ PRIORITY → unassigned, then natural order |
| Comets — Explore Catalog | `"Priority Order"` | — | Preserves catalog order |
| Asteroids | `"Priority Order"` | `"Priority"` | URGENT → HIGH → LOW → ⭐ PRIORITY → unassigned, then natural order |
| Cosmic Cataclysm | `"Order By Discovery Date"` | — | Preserves scrape order (Unistellar lists by discovery date) |

When `priority_col` is provided and the third sort is active, rows are ranked:
- `URGENT` → 0, `HIGH` → 1, `LOW` → 2, any other non-empty value (e.g. `⭐ PRIORITY`) → 3, blank/null → 4
- Ties broken by original row order (`kind='mergesort'` — stable sort)
- "Always Up" objects are **not** pushed to the bottom for this sort (they stay in priority-ranked position)

**Priority Legend placement:** Legends appear **below** the dataframe (not between chart and table) so they are visually associated with the table rows, not the Gantt chart. This applies to the Comet, Asteroid, and Cosmic sections.

### 6. Comet Mode Toggle

The Comet section has an internal radio toggle: `"📋 My List"` and `"🔭 Explore Catalog"`. My List is the default. My List code is completely unchanged by the Explore Catalog addition — it is wrapped in `if _comet_view == "📋 My List":`.

`get_comet_summary()` is **reused** by both modes. The Explore Catalog passes filtered designation tuples to it identically to My List.

### 7. Numeric Column Display Formatting

Streamlit renders pandas `float64` columns with full precision by default. Use `st.column_config.NumberColumn` to control the displayed format while keeping the underlying value numeric (so column-header sorting works correctly).

A shared config dict is defined at module level near the constants:
```python
_MOON_SEP_COL_CONFIG = {
    "Moon Sep (°)": st.column_config.NumberColumn("Moon Sep (°)", format="%.1f°"),
}
```

This is passed as `column_config=_MOON_SEP_COL_CONFIG` to every `st.dataframe` call that shows Moon Sep (DSO, Comet My List, Comet Catalog, Asteroid, Planet, Cosmic observable tables).

For the Cosmic section's `display_styled_table`, it is merged into the existing per-call `col_config`:
```python
col_config = dict(_MOON_SEP_COL_CONFIG)   # start with shared defaults
# then add section-specific entries (DeepLink, Duration)
```

The Cosmic Duration column is sourced in seconds from the scraper and **converted to minutes** immediately after `df_display` is built:
```python
if dur_col and dur_col in df_display.columns:
    df_display[dur_col] = pd.to_numeric(df_display[dur_col], errors='coerce') / 60
```
It is then formatted with `st.column_config.NumberColumn(format="%.1f min")` inside `display_styled_table` — it stays numeric (float), not a string, so sorting works.

`build_night_plan` reads the Duration column as **minutes** and schedules slots with `timedelta(minutes=dur_min)` (default `5.0` min).

**Rule:** Never convert a column to string just to add a unit suffix (e.g. `col.astype(str) + " min"`). That breaks column-header sorting. Always use `column_config` instead.

### 8. Night Plan Builder (Cosmic Cataclysm section)

The Night Plan Builder lives in a collapsible `st.expander` after the Observable / Unobservable tabs. It is **Cosmic-section-only** — other modes do not have it.

#### Two helper functions (module-level, before the Cosmic section)

**`build_night_plan(df_obs, start_time, end_time, pri_col, dur_col) → DataFrame`**

Sorts observable targets URGENT → HIGH → LOW → unassigned, then by ascending `_set_datetime` within each tier. Slots targets sequentially from `start_time`; breaks when the next target would exceed `end_time`; skips any target whose `_set_datetime` is already past `current_time`. Returns a DataFrame with `Obs Start`, `Obs End`, `_sched_start`, `_sched_end` columns added.

**`generate_plan_pdf(df_plan, night_start, night_end, target_col, link_col, dur_col, pri_col, ra_col, dec_col, vmag_col=None) → bytes | None`**

Requires `reportlab` (in `requirements.txt`). Returns landscape A4 PDF bytes. Re-detects the link column from `df_plan.columns` internally (`'link' in c.lower()`) so it is never lost if the caller passes `link_col=None`. Header row uses `#4472C4` (medium blue) for print readability. Priority rows are colour-coded. The link column renders the raw URL (e.g. `unistellar://science/transient?…`) as plain text so the full deeplink is visible and copyable.

#### Column detection in the Cosmic section

Four optional columns are detected from `df_display` after the main enrichment loop:

```python
link_col  = next((c for c in df_display.columns if 'link' in c.lower()), None)
vmag_col  = next((c for c in df_display.columns if 'mag' in c.lower()), None)
type_col  = next((c for c in df_display.columns if c.lower() in ('type','class','category') or 'event type' in c.lower()), None)
disc_col  = next((c for c in df_display.columns if 'disc' in c.lower() or ('date' in c.lower() and 'update' not in c.lower())), None)
```

**Important:** `link_col` matches `"Link"`, `"DeepLink"`, `"Deep Link"` etc. The Unistellar table names this column `"Link"`. Do **not** change the pattern back to `'deeplink'`.

#### Night Plan Builder UI layout

```
Row 1 — Priority multiselect (full width)
Row 2 — Refine candidate pool (4 columns):
         [Magnitude slider] [Event class multiselect] [Discovered last N days slider] [Sets no earlier than time input]
Row 3 — [🗓 Build Plan (primary)] [📊 All Alerts CSV]
```

Priority multiselect options: `["URGENT", "HIGH", "LOW", "(unassigned)"]` — MEDIUM has been removed from assignable priorities. Existing rows with MEDIUM still display with yellow highlighting for backward compatibility.

After clicking Build Plan, five sequential filters are applied to `df_obs` before scheduling:
1. Priority match
2. Magnitude range (`pd.to_numeric`, unknown magnitudes pass through)
3. Event class (`isin` match)
4. Discovery recency (`pd.to_datetime(..., utc=True)` vs cutoff; unknown dates pass through)
5. Minimum set time (compares `_set_datetime` against a tz-aware datetime; `pd.isnull` targets — Always Up — always pass)

The plan table shows: `Obs Start · Obs End · Name · Priority · Type · Rise · Transit · Set · Duration · Vmag · RA · Dec · Constellation · Moon Sep (°) · Moon Status · Status · Link`.

The link column is configured as `TextColumn` (not `LinkColumn`) in **both** the observable table (`display_styled_table`) and the Night Plan Builder table. `LinkColumn` only handles `http/https` URLs — using it for `unistellar://` deep links opens a blank page. `TextColumn` displays the full URL as plain text so it is visible and copyable.

Inside the Night Plan Builder, `_plan_link_col` is re-detected directly from `_scheduled.columns` (`'link' in c.lower()`) before building the display table and the PDF — this is the authoritative source, not the outer `link_col` variable.

#### Export formats
- **CSV** — `_plan_display.to_csv()` (all visible columns, no hidden `_` columns)
- **PDF** — `generate_plan_pdf(_scheduled, ...)` — passes the full `_scheduled` DataFrame (including hidden cols needed for PDF column detection), with `_plan_link_col` as the link argument

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

---

## Two Comet Data Pipelines

### Pipeline 1 — Explore Catalog (MPC archive)
- Source: `comets_catalog.json` (generated from MPC CometEls.json)
- Coverage: Comets with well-determined orbits — effectively up to ~2016
- Update cadence: Weekly (GitHub Actions, Sunday 02:00 UTC)
- Use case: Browsing established comets with filters (orbit type, perihelion, magnitude)
- Position data: Still fetched from JPL Horizons at runtime (positions change daily)

### Pipeline 2 — New Discovery Alerts (JPL SBDB live)
- Source: JPL SBDB API queried for discoveries in last 30 days
- Coverage: Newly discovered comets (not yet in MPC archive)
- Update cadence: Twice weekly (Mon + Thu 06:00 UTC)
- Use case: Admin notification when a new comet is discovered that isn't on the watchlist
- Output: GitHub Issues created via REST API; `_new_comets.json` is gitignored

These pipelines are independent. New discoveries do NOT automatically appear in Explore Catalog.

---

## Key Functions Reference

| Function | File | Purpose |
|---|---|---|
| `calculate_planning_info()` | `backend/core.py` | Rise/Set/Transit + Status per object |
| `compute_trajectory()` | `backend/core.py` | Altitude/Az over time range |
| `resolve_simbad()` | `backend/resolvers.py` | SIMBAD name lookup → SkyCoord |
| `resolve_horizons()` | `backend/resolvers.py` | JPL Horizons comet/asteroid position |
| `resolve_planet()` | `backend/resolvers.py` | JPL Horizons planet position |
| `plot_visibility_timeline()` | `app.py` | Gantt chart (all sections) |
| `get_comet_summary()` | `app.py` | Batch comet visibility (cached) |
| `get_asteroid_summary()` | `app.py` | Batch asteroid visibility (cached) |
| `get_dso_summary()` | `app.py` | Batch DSO visibility (cached, no API) |
| `get_planet_summary()` | `app.py` | Batch planet visibility |
| `build_night_plan()` | `app.py` | Build sequential night observation schedule |
| `generate_plan_pdf()` | `app.py` | Render night plan as downloadable PDF |
| `load_comet_catalog()` | `app.py` | Load comets_catalog.json |
| `load_comets_config()` | `app.py` | Load + parse comets.yaml |
| `save_comets_config()` | `app.py` | Save comets.yaml + GitHub push |
| `_send_github_notification()` | `app.py` | Create GitHub Issue (admin alerts) |
| `scrape_unistellar_table()` | `backend/scrape.py` | Scrape Cosmic Cataclysm alerts |
| `scrape_unistellar_priority_comets()` | `backend/scrape.py` | Scrape comet missions page |
| `scrape_unistellar_priority_asteroids()` | `backend/scrape.py` | Scrape planetary defense page |

---

## Data Files — What Changes and What Doesn't

| File | Changes by | How |
|---|---|---|
| `comets.yaml` | Admin panel (app) | `save_comets_config()` + GitHub push |
| `comets_catalog.json` | GitHub Actions | `update-comet-catalog.yml` (weekly) |
| `asteroids.yaml` | Admin panel (app) | `save_asteroids_config()` + GitHub push |
| `targets.yaml` | Admin panel (app) | Direct file write + GitHub push |
| `dso_targets.yaml` | Manually only | Static, no automated updates |
| `_new_comets.json` | `check_new_comets.py` | Temp file, gitignored, deleted each run |

---

## Secrets (`.streamlit/secrets.toml` — NOT in git)

```toml
GITHUB_TOKEN = "ghp_..."          # fine-grained PAT: contents read/write, issues write
ADMIN_PASSWORD = "..."            # gates all admin panels
GITHUB_REPO = "vamshikesireddy/astro_coordinates"
```

GitHub Actions uses the automatic `secrets.GITHUB_TOKEN` — no manual PAT needed for workflows.

---

## Adding a New Section

1. Add `get_X_summary(lat, lon, start_time, ...)` with `@st.cache_data(ttl=3600)`
2. In the section UI:
   - Run summary → set `is_observable`, `filter_reason`, `_dec_deg` per row
   - Apply Dec filter (mark-as-unobservable, after observability is set)
   - Split into Observable / Unobservable DataFrames
   - Call `plot_visibility_timeline()` in Observable tab
   - Add trajectory picker at bottom
3. Follow the same tab structure: `st.tabs(["🎯 Observable (N)", "👻 Unobservable (M)"])`
4. Pass `column_config=_MOON_SEP_COL_CONFIG` to every `st.dataframe` call that shows Moon Sep. For extra numeric columns (e.g. a unit-suffixed value), add them to a copy of the dict: `col_config = dict(_MOON_SEP_COL_CONFIG); col_config["My Col"] = st.column_config.NumberColumn(format="%d units")`

---

## Running Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Scripts (safe, read-only):
```bash
python scripts/update_comet_catalog.py    # downloads MPC catalog → comets_catalog.json
python scripts/check_new_comets.py        # queries JPL SBDB → _new_comets.json (if new found)
```

`open_comet_issues.py` requires `GITHUB_TOKEN` and `GITHUB_REPOSITORY` — intended for CI only.
