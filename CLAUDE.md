# CLAUDE.md — Astro Coordinates Planner

Developer guide for Claude. Auto-loaded every session — quick reference only.
For deep patterns, functions, and pipelines, read the sub-files listed at the bottom.

**Available tools (already installed):** Scrapling, Playwright, Astropy, Streamlit, ReportLab, Altair, Anthropic SDK, Ollama — see [`../AVAILABLE_TOOLS.md`](../AVAILABLE_TOOLS.md) for usage patterns.

---

## Architecture at a Glance

```
app.py (Streamlit UI + all section logic)
  ├── backend/core.py          (rise/set/transit calculations)
  ├── backend/app_logic.py     (pure business logic extracted from app.py, no Streamlit)
  ├── backend/resolvers.py     (SIMBAD + JPL Horizons API calls)
  ├── backend/config.py        (pure YAML/JSON file I/O, no Streamlit)
  ├── backend/github.py        (GitHub Issue creation, no Streamlit)
  ├── backend/scrape.py        (Scrapling scrapers for Unistellar pages)
  └── backend/sbdb.py          (SBDB cascade resolver — SPK-ID lookup with multi-match disambiguation)

tests/                         (pytest unit tests)
  ├── test_core.py             (azimuth_to_compass, moon_sep_deg, calculate_planning_info)
  ├── test_app_logic.py        (az_in_selected, get_moon_status, _check_row_observability, _sort_df_like_chart, build_night_plan, _sanitize_csv_df, _add_peak_alt_session, _apply_night_plan_filters)
  ├── test_config.py           (read_comets_config, read_comet_catalog, read_asteroids_config, read_dso_config)
  ├── test_ephemeris_cache.py  (lookup_cached_position, ephemeris cache integrity)
  ├── test_jpl_resolution.py   (JPL Horizons name resolution + fallback chain)
  ├── test_populate_jpl_cache.py (jpl_id_cache population guards)
  └── test_sbdb.py             (SBDB lookup, cascading, multi-match)

scripts/                       (standalone CLI tools run by GitHub Actions)
  ├── update_comet_catalog.py  (MPC download → comets_catalog.json)
  ├── check_new_comets.py      (JPL SBDB query → _new_comets.json)
  ├── open_comet_issues.py     (_new_comets.json → GitHub Issues)
  ├── check_unistellar_priorities.py  (scrape priorities → _priority_changes.json)
  ├── open_priority_issues.py  (_priority_changes.json → GitHub Issues)
  ├── update_ephemeris_cache.py (30-day Horizons batch → ephemeris_cache.json, runs daily)
  ├── download_dso_images.py   (Aladin hips2fits → assets/dso_images/, self-contained)
  ├── populate_jpl_cache.py    (SBDB ID → jpl_id_cache.json, weekly)
  └── diagnose_jpl.py          (debug utility — not run by CI)

assets/
  └── dso_images/              ← 167 pre-downloaded JPEG thumbnails (400×400, ~5MB)

Data files (tracked in git):
  comets.yaml            ← curated watchlist (~24 comets)
  comets_catalog.json    ← MPC archive snapshot (~865 comets, updated weekly)
  asteroids.yaml         ← curated watchlist
  dso_targets.yaml       ← static DSO catalog (Messier + stars + favorites)
  targets.yaml           ← Cosmic Cataclysm priorities/blocklist
  ephemeris_cache.json   ← 30-day pre-computed positions (updated daily by GH Actions)
  jpl_id_cache.json      ← SBDB SPK-IDs for Horizons queries (updated weekly)
  jpl_id_overrides.yaml  ← manual SBDB ID overrides for problematic names
```

---

## Sections in app.py

Each of the six target modes follows the same structure:

1. **Batch summary** — `get_X_summary()` returns a DataFrame (cached `ttl=3600`)
2. **Observability check** — loop sets `is_observable` + `filter_reason` per row
3. **Dec filter** — applied AFTER step 2 (see pattern in `docs/claude/patterns.md`)
4. **Observable / Unobservable tabs** — split by `is_observable` column
5. **Gantt chart** — `plot_visibility_timeline(df, ...)`
6. **Trajectory picker** — user selects one object → `resolve_*()` → altitude chart

Sections: DSO (Star/Galaxy/Nebula), Planet, Comet, Asteroid, Cosmic Cataclysm, Manual.

---

## App-wide Constants (CONFIG dict)

All magic numbers live in `CONFIG = {...}` at the top of `app.py` (~line 47). Always use these keys instead of bare literals:

| Key | Value | Used for |
|-----|-------|----------|
| `"gantt_row_height"` | 60 | Pixels per row in Gantt chart |
| `"gantt_min_height"` | 250 | Minimum Gantt chart height (px) |
| `"default_alt_min"` | 20 | Altitude filter lower bound default |
| `"default_session_hour"` | 18 | Default observation start hour |
| `"default_dur_idx"` | 8 | Duration selectbox default index (720 min) |

---

## Gotchas — Quick Reference

Full code examples in `docs/claude/patterns.md`.

- **Numbered labels:** use `"2\\. text"` not `"2. text"` — Streamlit strips the digit (markdown ordered list parsing)
- **Moon separation:** always `moon_sep_deg(target, moon)`, never `.separation()` — 3D GCRS distance artifact produces wrong results (e.g. 4.5° for objects 98° apart)
- **Unit suffixes:** use `st.column_config.NumberColumn(format="%d min")`, never `col.astype(str) + " min"` — string conversion breaks column-header sorting
- **Dec filter:** mark rows `is_observable=False`, do NOT delete them — objects must appear in Unobservable tab with a reason
- **Horizons column names:** `Tmag` (comet total mag, no hyphen), `V` (asteroid visual) — verify exact names before adding any new magnitude column
- **Range sliders:** add `isinstance(st.session_state.get(key), (tuple, list))` guard before render — stale scalar causes `TypeError: 'float' is not subscriptable` on `range[0]`
- **Hidden-column exemptions:** `_peak_alt_session` and `_dec_deg` must be in the Cosmic section's `hidden_cols` exception list or they disappear from the table

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
4. **Include `Moon Sep (°)` and `Moon Status` in `display_cols_*`**
5. **Add a Night Plan Builder** — call `_render_night_plan_builder()` with a unique `section_key`. See `docs/claude/patterns.md` §8 for full parameter reference.

---

## Running Locally

```bash
pip install -r requirements.txt
python -m streamlit run app.py        # use python -m if 'streamlit' is not in PATH
python -m py_compile app.py           # syntax check before committing
python -m pytest tests/ -v            # run unit tests (111 tests across 7 test files)

# Full pre-commit verification:
python -m py_compile app.py backend/core.py backend/app_logic.py backend/resolvers.py backend/config.py backend/github.py && python -m pytest tests/ -v
```

Scripts (safe, read-only):
```bash
python scripts/update_comet_catalog.py           # downloads MPC catalog → comets_catalog.json
python scripts/check_new_comets.py               # queries JPL SBDB → _new_comets.json (if new found)
python scripts/check_unistellar_priorities.py    # scrapes Unistellar, diffs vs YAML → _priority_changes.json
```

`open_comet_issues.py` and `open_priority_issues.py` require `GITHUB_TOKEN` and `GITHUB_REPOSITORY` — intended for CI only.

---

## Sub-file Reference

Read the relevant sub-file before editing anything in that domain.

| File | When to read |
|---|---|
| `docs/claude/patterns.md` | Editing observability loop, Night Plan Builder, Gantt chart, session state, Streamlit widgets, or any Critical Pattern |
| `docs/claude/functions.md` | Looking up where a function lives or planning where new logic goes |
| `docs/claude/data.md` | Touching GH Actions workflows, data pipelines, scraping, data files, or secrets |
