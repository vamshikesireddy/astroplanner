# CLAUDE.md — Astro Coordinates Planner

Developer guide for Claude (and human contributors) working on this codebase.

**Available tools (already installed):** Scrapling, Playwright, Astropy, Streamlit, ReportLab, Altair, Anthropic SDK, Ollama — see [`../AVAILABLE_TOOLS.md`](../AVAILABLE_TOOLS.md) for usage patterns.

---

## Architecture at a Glance

```
app.py (Streamlit UI + all section logic)
  ├── backend/core.py          (rise/set/transit calculations)
  ├── backend/resolvers.py     (SIMBAD + JPL Horizons API calls)
  └── backend/scrape.py        (Scrapling scrapers for Unistellar pages)

scripts/                       (standalone CLI tools run by GitHub Actions)
  ├── update_comet_catalog.py  (MPC download → comets_catalog.json)
  ├── check_new_comets.py      (JPL SBDB query → _new_comets.json)
  ├── open_comet_issues.py     (_new_comets.json → GitHub Issues)
  ├── check_unistellar_priorities.py  (scrape priorities → _priority_changes.json)
  └── open_priority_issues.py  (_priority_changes.json → GitHub Issues)

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

`plot_visibility_timeline(df, obs_start, obs_end, default_sort_label, priority_col)` accepts two optional parameters to control the fourth sort radio button (the section-specific option) and **returns the selected sort option string** (e.g. `"Earliest Set"`, `"Priority Order"`).

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `default_sort_label` | `str` | `"Default Order"` | Label shown for the fourth sort option |
| `priority_col` | `str \| None` | `None` | Column name used to rank rows when the fourth sort is active |

**Return value:** `str | None` — the selected radio button label, or `None` if the chart had no data (empty `chart_data`).

The first three sort options are always present: **Earliest Set**, **Earliest Rise**, **Earliest Transit**. The fourth option is section-specific and controlled by these parameters.

**Per-section sort label assignments:**

| Section | `default_sort_label` | `priority_col` | Behaviour |
|---|---|---|---|
| DSO (Stars/Galaxies/Nebulae) | `"Default Order"` | — | Preserves source/watchlist order |
| Planets | `"Default Order"` | — | Preserves natural planet order |
| Comets — My List | `"Priority Order"` | `"Priority"` | URGENT → HIGH → LOW → ⭐ PRIORITY → unassigned, then natural order |
| Comets — Explore Catalog | `"Priority Order"` | — | Preserves catalog order |
| Asteroids | `"Priority Order"` | `"Priority"` | URGENT → HIGH → LOW → ⭐ PRIORITY → unassigned, then natural order |
| Cosmic Cataclysm | `"Order By Discovery Date"` | — | Preserves scrape order (Unistellar lists by discovery date) |

When `priority_col` is provided and the fourth sort is active, rows are ranked:
- `URGENT` → 0, `HIGH` → 1, `LOW` → 2, any other non-empty value (e.g. `⭐ PRIORITY`) → 3, blank/null → 4
- Ties broken by original row order (`kind='mergesort'` — stable sort)
- "Always Up" objects are **not** pushed to the bottom for this sort (they stay in priority-ranked position)

**Priority Legend placement:** Legends appear **below** the dataframe (not between chart and table) so they are visually associated with the table rows, not the Gantt chart. This applies to the Comet, Asteroid, and Cosmic sections.

#### Table–Chart Sort Sync

The overview table below the Gantt chart reorders to match the chart's sort selection. This is achieved by:

1. `plot_visibility_timeline()` returns the `sort_option` string
2. `_sort_df_like_chart(df, sort_option, priority_col=None)` reorders the DataFrame accordingly
3. The sorted DataFrame is passed to the display helper (or `st.dataframe()` directly)

```python
_chart_sort = plot_visibility_timeline(df_obs, ...)
_df_sorted = _sort_df_like_chart(df_obs, _chart_sort, priority_col="Priority") if _chart_sort else df_obs
display_table(_df_sorted)
```

**`_sort_df_like_chart()` behaviour:**
- **Earliest Rise/Set/Transit:** Sorts by the corresponding `_rise_datetime` / `_set_datetime` / `_transit_datetime` column (ascending, NaT at bottom). "Always Up" objects are pushed to the bottom, sorted among themselves by transit time — mirroring the Gantt chart's Always Up handling.
- **Priority Order:** Uses the same URGENT→HIGH→LOW→⭐→blank ranking as the chart. Always Up objects stay in priority-ranked position (not pushed to bottom).
- **Default Order / Discovery Date:** Returns the DataFrame as-is (original order).
- **`None` return (empty chart):** Caller skips sorting — table stays in default order.

All 6 sections (DSO, Planet, Comet My List, Comet Catalog, Asteroid, Cosmic) are wired up. The `display_dso_table()` helper no longer does its own magnitude sort internally — it receives the pre-sorted DataFrame from the caller.

### 6. Comet Mode Toggle

The Comet section has an internal radio toggle: `"📋 My List"` and `"🔭 Explore Catalog"`. My List is the default. My List code is completely unchanged by the Explore Catalog addition — it is wrapped in `if _comet_view == "📋 My List":`.

`get_comet_summary()` is **reused** by both modes. The Explore Catalog passes filtered designation tuples to it identically to My List.

### 7. Numeric Column Display Formatting

Streamlit renders pandas `float64` columns with full precision by default. Use `st.column_config.NumberColumn` to control the displayed format while keeping the underlying value numeric (so column-header sorting works correctly).

`_MOON_SEP_COL_CONFIG` is defined at module level as a `TextColumn` (not `NumberColumn`) and is passed to all overview table `st.dataframe()` calls. It formats the `Moon Sep (°)` range string. `Moon Status` is intentionally excluded from all display code.

The Cosmic Duration column is sourced in seconds from the scraper and **converted to minutes** immediately after `df_display` is built:
```python
if dur_col and dur_col in df_display.columns:
    df_display[dur_col] = pd.to_numeric(df_display[dur_col], errors='coerce') / 60
```
It is formatted with `st.column_config.NumberColumn(format="%d min")` inside `display_styled_table` — displays as `10 min` (integer, no decimal). It stays numeric (float) internally so sorting works.

`build_night_plan` reads the Duration column as **minutes** and schedules slots with `timedelta(minutes=dur_min)` (default `5.0` min).

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

**Status thresholds** (`get_moon_status(illumination, separation)`):
- 🌑 Dark Sky: illumination < 15%
- ⛔ Avoid: illumination ≥ 15% and sep < 30°
- ⚠️ Caution: illumination ≥ 15% and sep 30°–60°
- ✅ Safe: illumination ≥ 15% and sep > 60°

Note: thresholds do not scale with illumination above 15% — a full moon at 65° shows Safe. May be refined later.

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

### 8. Night Plan Builder (all sections)

Every section has a Night Plan Builder in a collapsible `st.expander("📅 Night Plan Builder")` inside the Observable tab. The builder filters observable targets, sorts them by priority + set-time, and exports as CSV/PDF.

#### Three helper functions (module-level)

**`build_night_plan(df_obs, pri_col, dur_col) → DataFrame`**
Sorts observable targets URGENT → HIGH → LOW → unassigned, then by ascending `_set_datetime` within each tier. Returns a priority-sorted DataFrame — the user decides when to observe each target.

**`generate_plan_pdf(df_plan, night_start, night_end, target_col, link_col, dur_col, pri_col, ra_col, dec_col, vmag_col=None) → bytes | None`**
Requires `reportlab`. Returns landscape A4 PDF bytes. Re-detects link column internally. Header row `#4472C4` blue. Priority rows colour-coded. Link column renders raw URL as plain text.

**`_render_night_plan_builder(df_obs, start_time, night_end, local_tz, ...) → None`**
Shared UI function that renders the full Night Plan Builder inside an already-open `st.expander`. Adapts filter layout to available columns.

```python
_render_night_plan_builder(
    df_obs, start_time, night_end, local_tz,
    target_col="Name", ra_col="RA", dec_col="Dec",
    pri_col=None, dur_col=None, vmag_col=None,
    type_col=None, disc_col=None, link_col=None,
    csv_label="All Targets (CSV)", csv_data=None,
    csv_filename="targets.csv", section_key="",
)
```

| Parameter | Purpose |
|---|---|
| `pri_col` | Priority column — shows Row 1 priority multiselect + priority highlighting |
| `vmag_col` | Magnitude column — shows magnitude slider filter |
| `type_col` | Type/class column — shows type multiselect filter |
| `disc_col` | Discovery date column — shows discovery recency slider |
| `link_col` | Link column — shown in table + PDF as TextColumn |
| `dur_col` | Duration column — formatted as `%d min` |
| `csv_data` | DataFrame for "All" CSV button (defaults to `df_obs` if `None`) |
| `section_key` | Unique prefix for Streamlit widget keys (prevents duplicate key errors) |

#### Adaptive filter layout (two-row split)

Filters are split across two rows to avoid cramped columns:

- **Row A** (data-specific filters): Magnitude slider, Type multiselect, Discovery recency — only shown if the section has these columns. Sections without data-specific filters skip this row entirely.
- **Row B** (always present): Set time + Moon Status — every section gets this row.

| Section | Row A | Row B | Priority row |
|---|---|---|---|
| DSO | Magnitude + Type (2 cols) | Set time + Moon Status (2 cols) | — |
| Planet | — | Set time + Moon Status (2 cols) | — |
| Comet My List | — | Set time + Moon Status (2 cols) | ⭐ PRIORITY / (unassigned) |
| Comet Catalog | — | Set time + Moon Status (2 cols) | — |
| Asteroid | — | Set time + Moon Status (2 cols) | ⭐ PRIORITY / (unassigned) |
| Cosmic | Magnitude + Type + Discovery (3 cols) | Set time + Moon Status (2 cols) | URGENT / HIGH / LOW / (unassigned) |

#### Dynamic priority detection

The priority multiselect options are built from actual DataFrame values, not hardcoded. Comets and Asteroids use `⭐ PRIORITY` (binary flag from `unistellar_priority`), while Cosmic uses tiered URGENT/HIGH/LOW labels from `targets.yaml`. The caption text adapts: tiered sections show "URGENT → HIGH → LOW → unassigned", non-tiered sections show "Priority targets are listed first".

#### Per-section call sites

| Section | `section_key` | `pri_col` | `vmag_col` | `type_col` | `disc_col` | `link_col` |
|---|---|---|---|---|---|---|
| DSO | `"dso_{category}"` | — | `"Magnitude"` | `"Type"` | — | — |
| Planet | `"planet"` | — | — | — | — | — |
| Comet My List | `"comet_mylist"` | `"Priority"` | — | — | — | — |
| Comet Catalog | `"comet_catalog"` | — | — | — | — | — |
| Asteroid | `"asteroid"` | `"Priority"` | — | — | — | — |
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
5. Minimum set time (always — uses `_set_datetime`)
6. Moon Status (always if column exists — only filters when user deselects a status)

#### Export formats
- **CSV** — `_plan_display.to_csv()` (all visible columns, no hidden `_` columns)
- **PDF** — `generate_plan_pdf(_scheduled, ...)` — passes the full `_scheduled` DataFrame

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

**SBDB API note (fixed 2026-02-23):** The SBDB filter API (`sb-cdata`) does not support date-type constraints — the original `disc|d|>date` syntax caused a persistent `400 Bad Request`. Also, `disc` is not a valid SBDB field name. The fix: request `first_obs` (the correct field), sort by `-first_obs` to get newest comets first, and filter locally by comparing `first_obs` against the cutoff date.

These pipelines are independent. New discoveries do NOT automatically appear in Explore Catalog.

### Pipeline 3 — Unistellar Priority Sync (scrape + diff)
- Source: Unistellar comet missions + planetary defense pages (scraped with Scrapling)
- Coverage: Detects additions AND removals against `unistellar_priority` in YAML files
- Update cadence: Twice weekly (Mon + Thu 07:00 UTC via GitHub Actions) + runtime (once per app session)
- Output: GitHub Issues with `priority-added` (green) or `priority-removed` (red) labels; pending requests in admin panel
- Alias support: YAML entries with `# aka 3I/ATLAS` comments are matched against redesignated names
- Runtime flow: app auto-detection writes to `comet_pending_requests.txt` / `asteroid_pending_requests.txt` → admin Accept/Reject in sidebar
- CI flow: `check_unistellar_priorities.py` → `_priority_changes.json` → `open_priority_issues.py` → GitHub Issues

**Scraping library (migrated 2026-02-24):** All scrapers use [Scrapling](https://github.com/D4Vinci/Scrapling) (`StealthyFetcher`) instead of Selenium. Key differences:
- `_deep_text(element)` helper needed: Scrapling's `.text` only returns direct text nodes, not child element text. The helper uses `::text` pseudo-selector to match Selenium's `.text` behaviour.
- No driver management: Scrapling uses Patchright (Playwright fork) — no ChromeDriver version mismatches.
- Anti-bot: `StealthyFetcher` bypasses Cloudflare browser checks that block headless Selenium.
- **Browser auto-install:** `_ensure_browser()` runs `patchright install chromium` once per session before the first scrape. Required for Streamlit Cloud where the browser binary isn't pre-installed. `packages.txt` lists the ~20 system libraries Patchright's Chromium needs on Linux.

**Threading model (`_fetch_page()`):** All `StealthyFetcher.fetch()` calls go through the `_fetch_page(url, **kwargs)` wrapper, which runs the fetch in a `ThreadPoolExecutor(max_workers=1)` worker thread. This solves two problems:
1. **Streamlit's asyncio loop conflict:** Streamlit runs its own asyncio event loop. Playwright's sync API also needs an event loop — running it on Streamlit's main thread causes `RuntimeError: This event loop is already running`.
2. **Windows `SelectorEventLoop` limitation:** On Windows, the default `SelectorEventLoop` doesn't support subprocess creation (needed by Playwright to launch Chromium). `_fetch_page` sets `asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())` **process-wide** on `win32` — this ensures ALL new event loops (including Playwright's internal background thread loop created via `new_event_loop()`) use `ProactorEventLoop`.

Per-thread `set_event_loop()` does NOT work because Playwright creates its own event loop in a separate internal thread. The process-wide policy is the only reliable fix.

**Asteroid scraper (fixed 2026-02-24):** `scrape_unistellar_priority_asteroids()` now extracts targets from `<h3>` headings instead of regex on body text. Unistellar uses three naming formats:
- `2001 FD58` — standard provisional designation (regex match)
- `2033 (Basilea)` — number then parenthesized name (normalized via `_NUM_PAREN_NAME_RE`)
- `Eros` — bare name without number (resolved via `_BARE_NAME_ALIASES` lookup)

---

## Key Functions Reference

| Function | File | Purpose |
|---|---|---|
| `calculate_planning_info()` | `backend/core.py` | Rise/Set/Transit + Status per object |
| `moon_sep_deg()` | `backend/core.py` | Moon–target angular separation (strips 3D distance artifact) |
| `compute_trajectory()` | `backend/core.py` | Altitude/Az/RA/Dec/Constellation/Moon Sep (°) per 10-min step |
| `resolve_simbad()` | `backend/resolvers.py` | SIMBAD name lookup → SkyCoord |
| `resolve_horizons()` | `backend/resolvers.py` | JPL Horizons comet/asteroid position |
| `resolve_planet()` | `backend/resolvers.py` | JPL Horizons planet position |
| `plot_visibility_timeline()` | `app.py` | Gantt chart (all sections); returns sort selection string |
| `_sort_df_like_chart()` | `app.py` | Reorder DataFrame to match Gantt chart sort selection |
| `get_comet_summary()` | `app.py` | Batch comet visibility (cached) |
| `get_asteroid_summary()` | `app.py` | Batch asteroid visibility (cached) |
| `get_dso_summary()` | `app.py` | Batch DSO visibility (cached, no API) |
| `get_planet_summary()` | `app.py` | Batch planet visibility |
| `build_night_plan()` | `app.py` | Sort targets by priority + set-time for night plan |
| `generate_plan_pdf()` | `app.py` | Render night plan as downloadable PDF |
| `_render_night_plan_builder()` | `app.py` | Shared Night Plan Builder UI (all sections) |
| `load_comet_catalog()` | `app.py` | Load comets_catalog.json |
| `load_comets_config()` | `app.py` | Load + parse comets.yaml |
| `save_comets_config()` | `app.py` | Save comets.yaml + GitHub push |
| `_send_github_notification()` | `app.py` | Create GitHub Issue (admin alerts) |
| `scrape_unistellar_table()` | `backend/scrape.py` | Scrape Cosmic Cataclysm alerts (Scrapling) |
| `scrape_unistellar_priority_comets()` | `backend/scrape.py` | Scrape comet missions page (Scrapling) |
| `scrape_unistellar_priority_asteroids()` | `backend/scrape.py` | Scrape planetary defense page (Scrapling) |
| `_deep_text()` | `backend/scrape.py` | Get all descendant text from Scrapling element |
| `_fetch_page()` | `backend/scrape.py` | Thread-safe StealthyFetcher wrapper (Streamlit + Windows compat) |
| `_ensure_browser()` | `backend/scrape.py` | Auto-install Patchright Chromium (idempotent, once per session) |
| `check_unistellar_priorities.main()` | `scripts/check_unistellar_priorities.py` | Scrape + diff priorities, write `_priority_changes.json` |
| `open_priority_issues.main()` | `scripts/open_priority_issues.py` | Create GitHub Issues for priority changes |

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
GITHUB_REPO = "vamshikesireddy/astroplanner"
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
4. **Include `Moon Sep (°)` and `Moon Status` in `display_cols_*`** — Moon Sep shows as a `"min°–max°"` range string, Moon Status shows the emoji badge (🌑/⛔/⚠️/✅). Both are included in overview tables, CSV exports, and Night Plan PDF. Configure via `_MOON_SEP_COL_CONFIG` (covers both as `TextColumn`). For extra numeric columns that need a unit suffix, use `st.column_config.NumberColumn(format="%d units")` directly in the `column_config` dict.

---

## Running Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Scripts (safe, read-only):
```bash
python scripts/update_comet_catalog.py           # downloads MPC catalog → comets_catalog.json
python scripts/check_new_comets.py               # queries JPL SBDB → _new_comets.json (if new found)
python scripts/check_unistellar_priorities.py    # scrapes Unistellar, diffs vs YAML → _priority_changes.json
```

`open_comet_issues.py` and `open_priority_issues.py` require `GITHUB_TOKEN` and `GITHUB_REPOSITORY` — intended for CI only.
