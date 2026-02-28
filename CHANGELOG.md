# Changelog

Bug fixes, discoveries, and notable changes. See CLAUDE.md for architecture and patterns.

---

## 2026-03-01 — Fix: vmag slider isinstance guard, _cat_df staleness, lat=0.0 guards

**Branch:** `fix/location-state-guards` + direct main hotfix
**Tests:** 96 pass

### Issue A — vmag slider isinstance guard (hotfix to main, commits fbb3405 + 2cd52cf)

Same crash class as `31812df`. The magnitude slider in `_render_night_plan_builder`
used `value=` + `key=` without an isinstance guard. Stale scalar state (from
a Streamlit Cloud version-mismatch session) would cause `_apply_night_plan_filters`
to crash at `vmag_range[0]` with `TypeError: 'float' object is not subscriptable`.

Fix: pop `{section_key}_vmag` from session state before the slider renders if it
holds a non-tuple. Mirrors the win_range slider fix (`31812df`). Also reuses
`_vmag_ss_key` as the `key=` argument to eliminate a repeated string literal.

Rule: Any range slider managed with `key=` must have an isinstance guard clearing
non-tuple state before the `st.slider()` call.

### Issue B — `_cat_df` not invalidated on location change

The Explore Catalog comet DataFrame was cached in session state without
tracking which lat/lon it was calculated at. Moving to a new location showed
stale rise/set times from the previous location.

Fix: store `_cat_df_lat`/`_cat_df_lon` alongside `_cat_df`; compare against
current sidebar values on each render; clear + show re-calculate prompt if different.

Rule: Any session-state DataFrame that depends on lat/lon must store and validate
its source coordinates.

### Issue C — lat=0.0 guard inconsistency

`lat` is always a `float` after the first render (number_input returns 0.0 as
default, never None). Two callsites guarded with `is not None` (always True),
causing the Moon panel and Catalog Calculate button to run at Null Island (0N, 0E)
when no location was set. Also added `moon_illum = 0` and `location = None`
pre-initializations (previously undefined if the block was skipped).

Note: `'moon_illum' in locals()` guards at ~line 4161/4170 (trajectory view)
are now logically dead (always True since `moon_illum` is always initialized at
module scope). They cause no incorrect output — the surrounding `moon_loc` guards
prevent the trajectory moon-status code from running without a real location.
Clean-up deferred to a future pass.

Fix: added `not (lat == 0.0 and lon == 0.0)` to both guards.

Rule: Use `lat is not None and lon is not None and not (lat == 0.0 and lon == 0.0)`
as the standard location guard. `is not None` alone is not sufficient since
number_input always produces a float (0.0 when unset).

---

## 2026-03-01 — Hotfix: Night Plan Builder session window slider crashes on Streamlit Cloud

**Commit:** `31812df`
**Branch:** `main` (hotfix — pushed directly)
**Tests:** 96 pass

### Bug
`TypeError: 'datetime.datetime' object is not subscriptable` at `app.py:673` — crashed all sections (DSO, Comet, etc.) whenever the Night Plan Builder was rendered on Streamlit Cloud.

### Root cause
The slider sync guard:
```python
if (st.session_state.get(_last_key) != _st_rounded
        or st.session_state.get(_last_dur_key) != duration_minutes):
    st.session_state[_ss_key] = (_slider_default_start, _slider_default_end)
```
…only fired when the sidebar start-time or duration changed. It never checked whether `_ss_key` itself held a valid tuple. On Streamlit Cloud, WebSocket sessions can persist across deploys. A user whose session carried over a stale scalar `datetime` in `_ss_key` (from a version mismatch or prior state) would never trigger the reset — so the slider rendered as single-handle (scalar mode) instead of a range, then `_win_range[0]` crashed.

### Fix
Added a third condition to the guard:
```python
or not isinstance(st.session_state.get(_ss_key), (tuple, list))
```
Any non-tuple state is now corrected to a `(_start, _end)` tuple before the slider renders, regardless of whether start-time or duration changed.

### Rule
When a Streamlit widget's session state key is managed manually, the reset guard must check **both** the triggering values AND the type/shape of the stored value. A stale or wrong-type value in session state silently corrupts widget mode (scalar vs range) without raising an error until the value is used downstream.

---

## 2026-03-01 — Docs: How to Use rewrite — steps now match in-app numbering

**Commits:** `ddc6d54`, `b46a1d3`
**Tests:** 66 pass

### Problem
The "How to Use" expander listed steps 1–4 in a different order and with different content than the in-app step headers (also 1–4), causing confusion:

| How to Use said | In-app label |
|----------------|--------------|
| Step 2: Choose a Target | **1.** Choose Target |
| Step 3: Calculate & Analyze | **4.** Trajectory Results |
| Step 4: Night Plan Builder | **2.** 📅 Night Plan Builder |

Step 3 of the actual flow ("Select Target for Trajectory") was missing from the guide entirely. "minute-by-minute" was also inaccurate — trajectory CSV uses 10-minute steps.

### Fix
- Sidebar setup → unnumbered `### Setup (Sidebar)` pre-step (sidebar has no in-app number)
- Steps 1–4 now mirror in-app labels exactly: Choose Target → Night Plan Builder → Select Target for Trajectory → Trajectory Results
- Added missing Step 3 description (Select Target for Trajectory)
- Fixed "minute-by-minute" → "10-minute step" in Step 4 CSV bullet

### Rule
When adding or renumbering in-app step headers, update the How to Use expander at `app.py:1743` to match.

---

## 2026-03-01 — Fix: Peak Alt integer formatting in night plan PDF

**Commit:** `0712527`
**Tests:** 66 pass

### Problem
PDF night plan export showed raw float precision for Peak Alt (°) column — e.g. `27.892918595366687` instead of `28°`. The cell renderer in `generate_plan_pdf` hit the generic `str(row.get(col))` branch for numeric columns, bypassing any formatting.

### Fix
Added an explicit `elif col == 'Peak Alt (°)':` branch in the PDF cell renderer loop (inside `generate_plan_pdf`) that formats the value as `f"{float(val):.0f}°"`. Falls back to `—` on exception. Matches the `%.0f°` format already used in the on-screen column config.

### Rule
Any numeric column added to `generate_plan_pdf` needs an explicit formatting branch in the cell renderer loop — the generic `str()` fallback does not apply Python format specs.

---

## 2026-03-01 — UI polish: step numbering, Peak Alt exports, Peak Alt (session) overview column

**Branch:** `feature/peak-alt-session-column` (10 commits)
**Tests:** 66 pass

### Changes

**A. Consistent in-app step numbering**
- Night Plan Builder expander was unnumbered, causing confusion about where it fit in the flow
- Renumbered all in-app steps to a clear 1→2→3→4 sequence:
  - `1. Choose Target` — unchanged (batch table + Gantt is step 1 output)
  - `2. 📅 Night Plan Builder` — was unlabeled
  - `3. Select X for Trajectory` — was `2.`
  - `4. Trajectory Results` — was `3.`
- README step 5 updated: `"5. Calculate a Trajectory"` → `"5. Explore a Trajectory"`; body references updated from step 2/3 to step 3/4

**B. Peak Alt (°) included in CSV and PDF exports**
- Bug: Night Plan Builder showed `Peak Alt (°)` on-screen but stripped it from CSV (`.drop(columns=['Peak Alt (°)'])`) and omitted it from the PDF column list
- Fix: removed the drop from the CSV download button; added `'Peak Alt (°)'` to `generate_plan_pdf` column order and `_W` width dict (1.2 cm)

**C. Peak Alt (session) column in all section overview tables**
- New column `"Peak Alt (session)"` added to the overview table (below Gantt chart) in all 5 sections
- Shows the highest altitude the object reaches during the user's chosen observation window (sidebar Start Time + Duration)
- Uses existing `compute_peak_alt_in_window()` at 5 sample points — fast enough for observable subsets
- Column header makes session dependency explicit; tooltip explains the 5-point sampling
- New module-level helper `_add_peak_alt_session(df, location, win_start_tz, win_end_tz, n_steps=5)` — thin wrapper, falls back to None on missing location/coordinates
- Config entry added to `_MOON_SEP_COL_CONFIG` so formatting applies to all 5 sections automatically
- Cosmic Cataclysm: updated `hidden_cols` to except `_peak_alt_session` alongside `_dec_deg`
- Column position: between `Status` and `Moon Sep (°)` in all sections

### Rule
When adding a column that uses `_`-prefix convention in Cosmic Cataclysm, always add it to the `hidden_cols` exception at `if c.startswith('_') and c not in (...)`.

---

## 2026-03-01 — Night Plan altitude-aware filter, Peak Alt column, and Parameters summary

**Commits:** `f2a07dc` → `3ae5129` (9 commits)
**Tests:** 66 pass (3 new in `tests/test_core.py`)

### Problem solved

Night Plan Builder included objects that were below `min_alt` during the actual session window. The observability check ran at sidebar `start_time` (e.g. 15:00 when an object is near transit) — so an object with altitude 30° passed the `min_alt=20°` check and entered `df_obs`. The window filter then only checked horizon-crossing times (`_rise < win_end` AND `_set > win_start`), not actual altitude during the window.

**Example:** 99942 Apophis transits at ~15:00 (alt 30°), passes `min_alt=20°`, enters `df_obs`. Session window 20:00→01:00. At 20:00 it is already at 14° and declining — never reaches 20° during the window. Was incorrectly included in the Night Plan.

### What changed

**A. `compute_peak_alt_in_window()` — new function in `backend/core.py`**
- Samples altitude at ~30-min intervals across the session window using Astropy AltAz
- Returns peak altitude in degrees (can be negative if object is always below horizon)
- `n_steps` auto-computed from window duration (one per 30 min, min 2); override-able for tests
- Used by `_apply_night_plan_filters()` to decide whether to include each target

**B. `_ra_deg` added to all 5 section summary DataFrames**
- `get_comet_summary`, `get_asteroid_summary`, `get_dso_summary`, planet inline loop, cosmic inline loop
- Stored as numeric degrees — same pattern as existing `_dec_deg`; hidden column (not shown in table)
- Enables `compute_peak_alt_in_window()` to reconstruct `SkyCoord` without re-parsing formatted RA strings
- Stub/failure rows include `_ra_deg: 0.0` (same convention as `_dec_deg: 0.0`)

**C. Altitude-aware window filter in `_apply_night_plan_filters()`**
- New params: `location=None, min_alt=0`
- Replaced `df.apply(_in_obs_window, axis=1)` closure with explicit `iterrows()` loop building `_keep` mask and `_peak_alt_window` list simultaneously (allows per-row mutation)
- Logic: horizon overlap check first (cheap) → altitude sampling only if horizon overlap passes
- "Always Up" objects get `_peak_alt_window=90.0` and always pass
- Objects with missing rise/set times fall back to "keep" (safe default)
- Objects missing `_ra_deg`/`_dec_deg`, or when `location=None`, fall back to horizon-only check

**D. `Peak Alt (°)` column in Night Plan table (on-screen only)**
- `_peak_alt_window` renamed to `Peak Alt (°)` after `_scheduled` is built
- Formatted with `st.column_config.NumberColumn(format="%.0f°")`
- Stripped from CSV export (`.drop(columns=['Peak Alt (°)'], errors='ignore')`)
- Not included in PDF export (hidden `_` prefix columns already stripped)

**E. Parameters summary block in `_render_night_plan_builder()`**
- New params: `min_moon_sep=0, az_dirs=None` (in addition to `location`, `min_alt`)
- `st.info()` line rendered immediately above the Build Plan / CSV button row
- Always shows: session window range + duration, Min alt, Moon sep ≥ N°
- Conditionally shows: Az directions (only when fewer than all 8 selected), Priority selection (only when active), Moon Status filter (only when not all statuses selected)
- All 6 call sites updated to pass `location`, `min_alt`, `min_moon_sep`, `az_dirs`

### UI improvements

- All 6 Night Plan Builder `st.expander()` calls changed to `expanded=True` (open by default)
- `st.markdown("---")` separator added before Night Plan Builder in Comet, Asteroid, Planet, Cosmic, and Manual sections (DSO already had one)
- Trajectory picker: `st.caption()` added immediately after subheader in all 6 sections, listing all active sidebar filters (altitude, azimuth, moon separation, duration) so users understand what drives results
- Duplicate trajectory caption removed from `st.header("3. Trajectory Results")`
- Section numbering unified across all sections: **1. Choose Target** → **2. Select X for Trajectory** → **3. Trajectory Results** (consistent 3-step flow)

### Bug fixed: asteroid priority false-positive (ADDED + REMOVED for same object)

**Symptom:** `2001 FD58` appeared in both ADDED and REMOVED alerts simultaneously.

**Root cause:** Scraped name `2001 FD58` (bare provisional) did not match YAML name `162882 (2001 FD58)` (numbered form with provisional in parens). Exact uppercase comparison: scraped name absent from YAML → ADDED alert. YAML name absent from scraped set → REMOVED alert. Both fired for the same physical object.

**Fix:** `_build_priority_provisionals(priority_set)` helper extracts the provisional designation from parentheses in YAML names, building a reverse lookup `{"2001 FD58": "162882 (2001 FD58)"}`. ADDED check skips a scraped name if its provisional form resolves to a YAML entry. REMOVED check uses a `scraped_via_provisional` set so the numbered YAML name is not flagged as removed when matched via provisional.

**Rule:** When comparing scraped names to YAML names for priority diff, always check both exact match and provisional-within-parentheses match. SBDB can return bare provisionals `"2001 FD58"` while YAML stores numbered forms `"162882 (2001 FD58)"`.

### Bugs fixed (during testing)

**Bug: `st.session_state.az_N` modified after widget instantiated (StreamlitAPIException)**
- "Clear All" az directions button set `st.session_state[f"az_{d}"] = False` after checkboxes were already rendered in the same render cycle.
- Fix: moved state mutation into an `on_click=_clear_az_dirs` callback so state is set before the next render cycle.
- Rule: never set session state keys that correspond to already-rendered widgets from outside an `on_click`/`on_change` callback.

**Bug: `ImportError: cannot import name 'compute_peak_alt_in_window' from 'backend.core'`**
- Stale Streamlit server process (PID 15052) was running old code from before the `compute_peak_alt_in_window` function was added.
- Fix: killed stale process, restarted. No code change needed.

### Tests added (`tests/test_core.py`)
- `test_compute_peak_alt_in_window_below_horizon` — object always below horizon returns negative peak
- `test_compute_peak_alt_in_window_high_object` — Polaris at 50°N returns peak > 40°
- `test_compute_peak_alt_in_window_n_steps_minimum` — `n_steps=1` samples only the window start

### Files modified
`backend/core.py` (new function `compute_peak_alt_in_window`), `app.py` (`_ra_deg` in 9 places, `_apply_night_plan_filters` altitude logic, `_render_night_plan_builder` parameters summary, Peak Alt column display and CSV strip, `_build_priority_provisionals` helper, all 6 call sites, UI improvements), `tests/test_core.py` (3 new tests), `CHANGELOG.md`, `README.md`.

---

## 2026-03-01 — Pre-computed ephemeris cache: zero live JPL calls at render time

**Branch:** `feature/ephemeris-cache` — 9 commits + 2 post-merge hotfixes, merged to main
**Tests:** 63 pass (11 new in `tests/test_ephemeris_cache.py`, 3 new in `tests/test_populate_jpl_cache.py`, 3 new in `tests/test_config.py`)

### Problem solved

`get_comet_summary()` and `get_asteroid_summary()` called JPL Horizons live at render time for every object. With 25 comets + 16 asteroids this caused:
- Up to 30s load time on first uncached render
- ~30% intermittent failure rate from JPL rate limiting (3 parallel workers each)
- Three comets (`24P/Schaumasse`, `235P/LINEAR`, `88P/Howell`) always failing due to stale bad IDs in `jpl_id_cache.json`

### Architecture: pre-computed ephemeris (GitHub Actions daily)

GitHub Actions runs `scripts/update_ephemeris_cache.py` daily at 07:00 UTC. The script:
- Reads `comets.yaml` and `asteroids.yaml` for the full watchlist
- Makes **41 total Horizons requests** (one per object, each covering today→today+30 with `step='1d'`)
- Runs sequentially with 0.5s delay — no burst, no rate limiting; completes in ~2 min
- Validates object names against SBDB `fullname` daily; creates GitHub Issue on genuine renames
- Outputs `ephemeris_cache.json` (committed to repo, ~60KB)

App fast path in `_fetch()` (both comet and asteroid):
```python
cached_pos = lookup_cached_position(_ephem, "comets", name, target_date)
if cached_pos is not None:
    ra_deg, dec_deg = cached_pos
    sky_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame='icrs')
    # → local astropy rise/set, no JPL call
# fallback: existing live JPL path (dates >30 days out, or object missing from cache)
```

Result: **0 JPL calls at render time for the 99% case** (dates ≤30 days). Load time drops from ~30s to ~0.1s.

### Bugs found and fixed (during implementation + code review)

**Bug: stale `[20M, 30M)` SBDB internal IDs in `jpl_id_cache.json`**
- Root cause: `populate_jpl_cache.py` wrote SBDB's internal SPK-IDs (`20000000+N` for numbered bodies, e.g. `"433 Eros": "20000433"`) before the `_save_jpl_cache_entry` guard existed.
- JPL Horizons does not accept these IDs → 3 comets and ALL 16 asteroids failed on every cache hit.
- Fix 1: Removed all `[20M, 30M)` entries from `jpl_id_cache.json`.
- Fix 2: Same guard added to `populate_jpl_cache.py` — script now prints `SKIP (SBDB internal ID)` and never writes bad entries.
- Rule: SBDB and Horizons use different ID namespaces. Always validate that a cached ID is accepted by Horizons, not just returned by SBDB.

**Bug (code review): `_validate_name()` caused false-positive "name changes" for every numbered body**
- SBDB `fullname` field always appends the provisional designation in parens (e.g. `"433 Eros (A898 PA)"`).
- Naive string comparison → flagged every numbered asteroid as a rename on every daily run.
- Fix: strip the parenthetical suffix from BOTH the canonical SBDB name and the stored YAML name before comparing. Only genuine base-name differences (not format differences) trigger the GitHub Issue.
- Rule: always strip `(...)` suffixes when comparing SBDB `fullname` to stored display names.

**Bug (code review): `lookup_cached_position` defined in `scripts/` — imported by `app.py`**
- `app.py` importing from `scripts/` violates the architectural boundary (scripts = CLI tools, not library code).
- Fix: moved to `backend/config.py` as a public function. Both `app.py` call sites updated; test imports updated.
- Rule: `app.py` and `backend/` must never import from `scripts/`. Scripts import from `backend/`, not the other way.

**Bug (code review): workflow missing `git pull --rebase` before push**
- `update-ephemeris-cache.yml` and `check-unistellar-priorities.yml` both run Mon/Thu around 07:00 UTC.
- If both commit concurrently, the second push would be rejected without a rebase.
- Fix: added `git pull --rebase origin main` between `git commit` and `git push` in the workflow.

**Bug: Windows `UnicodeEncodeError` in `update_ephemeris_cache.py`**
- Script used Unicode arrows (`→`) and emoji (`⚠️`) in `print()` calls.
- Windows CP1252 console cannot encode these → `UnicodeEncodeError` on local runs.
- Fix: replaced all non-ASCII characters with ASCII equivalents (`->`, `WARNING`, `FAIL`).

### YAML renames detected on first run (name validation working)

The first run of `update_ephemeris_cache.py` detected 3 genuine renames via SBDB `fullname` comparison:

| Stored name (old) | Canonical SBDB name | Section |
|---|---|---|
| `240P-B` | `240P/NEAT-B` | comets |
| `2001 FD58` | `162882 (2001 FD58)` | asteroids |
| `2001 SN263` | `153591 (2001 SN263)` | asteroids |

All three were updated in `comets.yaml`, `asteroids.yaml`, and `jpl_id_cache.json`.

### Post-merge hotfix: `240P/NEAT-B` failed Horizons fetch after rename

After renaming `240P-B` → `240P/NEAT-B` in `comets.yaml`, the corresponding override in `jpl_id_overrides.yaml` still used the old key `"240P-B"`. The override lookup returned `None` → all 3 Horizons fallback attempts failed → `240P/NEAT-B` appeared in `failures` list.

- Fix: renamed the key from `"240P-B"` to `"240P/NEAT-B"` in `jpl_id_overrides.yaml` (value `"90001203"` unchanged).
- Confirmed: next workflow run shows `Failures: 0`, `240P/NEAT-B → OK (31 days)`.
- Rule: when renaming an object in `comets.yaml` / `asteroids.yaml`, always check `jpl_id_overrides.yaml` for a matching key and update it to match.

### Files added
- `scripts/update_ephemeris_cache.py` — daily ephemeris fetch + name validation
- `.github/workflows/update-ephemeris-cache.yml` — GH Actions workflow (daily 07:00 UTC)
- `ephemeris_cache.json` — pre-computed positions (tracked in git)
- `tests/test_ephemeris_cache.py` — 11 unit tests
- `tests/test_populate_jpl_cache.py` — 3 unit tests for bad-ID guard

### Files modified
`backend/config.py` (added `read_ephemeris_cache`, `lookup_cached_position`), `app.py` (added `_load_ephemeris_cache`, fast paths in both summary functions), `jpl_id_cache.json` (removed bad entries, renamed `240P-B` key), `comets.yaml` (renamed `240P-B` → `240P/NEAT-B`), `asteroids.yaml` (renamed 2 objects), `jpl_id_overrides.yaml` (renamed `240P-B` key), `scripts/populate_jpl_cache.py` (bad-ID guard added), `CLAUDE.md`, `CHANGELOG.md`.

---

## 2026-02-27 — JPL name resolution: all bugs fixed, 46 tests pass (final)

**Branch:** `feature/jpl-name-resolution` — 25 commits, merged to main

### Additional runtime bugs fixed (session 2, 2026-02-27)

**Bug: JPL Horizons rate-limits 8 parallel workers → ~50% of batch queries fail**
- `ThreadPoolExecutor(max_workers=8)` fires simultaneous HTTP requests to JPL Horizons.
- JPL rejects some under load even though each ID resolves fine when called sequentially.
- Symptom: random ~50% failure on every fresh page load; diagnose_jpl.py (sequential) always passed 41/41.
- Fix 1: Reduced `max_workers` from 8 → 3 (both comets and asteroids).
- Fix 2: Added one retry with 1.5s backoff inside `_fetch()`. On first failure: sleep → retry once. If retry also fails, falls through to existing SBDB fallback. Applied to both comet and asteroid `_fetch()`.
- Rule: JPL Horizons = public rate-limited API. Never fire more than 3 concurrent requests. Always retry once before giving up.

**Bug: Stale `@st.cache_data` serves old failure rows after fixes applied**
- `get_comet_summary()`, `get_asteroid_summary()`, and `_load_jpl_overrides()` are all `@st.cache_data(ttl=3600)`.
- Pressing "Always rerun" does NOT clear server-side cache. Only killing the process does.
- New override entries (e.g. `"3 Juno": "3;"`) invisible to batch until cache cleared.
- Fix: "🔄 Refresh JPL Data" button added to both Comet Admin and Asteroid Admin panels. Clears all three caches and reruns. One button clears both sections.
- **Deferred (next iteration):** Don't cache failure stub rows at all — strip before `@st.cache_data` return, re-query fresh each render. Branch: `refactor/jpl-cache-no-stale-failures`.

**Bug: Trajectory picker shows bare JPL ID instead of display name**
- `_asteroid_jpl_id("2 Pallas")` → `"2"`. `resolve_horizons("2")` returns `("2", sc)`. Banner showed "Resolved: 2".
- Fix: After successful resolve, if user selected from the list (not Custom entry), `name = selected_target`. Applied to comet My List and asteroid trajectory pickers.

### Documentation convention established
- **CHANGELOG.md** is the canonical record for every bug, root cause, and fix. Update at end of every session.
- **Rule:** If you encounter a bug and fix it, add it to CHANGELOG immediately with root cause + fix + rule-of-thumb. Future sessions read this before digging into code.

### Deferred to next iteration
| Item | Branch | Description |
|------|--------|-------------|
| Don't cache stub rows | `refactor/jpl-cache-no-stale-failures` | Strip `_resolve_error=True` rows from `@st.cache_data` return; re-query fresh each render |
| threading.Lock in `_save_jpl_cache_entry` | — | Benign race condition; low priority |

### What was built
Three-layer JPL ID lookup system: `jpl_id_overrides.yaml` → `jpl_id_cache.json` → name fallback (strip parenthetical / extract number / provisional passthrough). SBDB fallback when Horizons rejects the initial ID. Admin panel shows JPL failures with per-object override input and save button.

### Bugs found and fixed

**Bug: SBDB returns `2000xxxx` SPK-IDs that Horizons rejects**
- SBDB returns `20000000 + catalog_number` for numbered bodies (e.g. `20000433` for Eros, `20015091` for 88P/Howell).
- JPL Horizons does not accept these IDs. Every asteroid and several periodic comets failed.
- `diagnose_jpl.py` (new script) identified the pattern by testing all 41 objects against live Horizons.
- Fix 1: Purged all `>= 20_000_000` entries from `jpl_id_cache.json`.
- Fix 2: Guard added to `_save_jpl_cache_entry()` — rejects IDs in `[20M, 30M)` before writing, so bad IDs can never re-enter the cache from SBDB. Fragment IDs (`9000xxxx`) and comet SPK-IDs (`100xxxx`) are correctly allowed through.
- Test added: `test_save_jpl_cache_entry_rejects_sbdb_internal_ids` + `test_save_jpl_cache_entry_accepts_valid_ids`.

**Bug: NaN-truthy check flags ALL rows as JPL failures**
- `@st.cache_data` summary functions (`get_comet_summary` / `get_asteroid_summary`) mix success rows (no `_resolve_error` key) and stub rows (`_resolve_error: True`) into a single DataFrame.
- Pandas fills missing column values with `NaN` (float). `bool(float('nan'))` is `True` in Python.
- `if row.get("_resolve_error"):` → `if NaN:` → `True` → **every row** flagged as a JPL failure, even successful ones.
- Symptom: all comets and asteroids appeared Unobservable with "JPL lookup failed (tried: nan)".
- Fix: `if row.get("_resolve_error") is True:` in both the comet (line ~2572) and asteroid (line ~3260) observability loops.
- Test added: `test_nan_resolve_error_not_truthy` — documents that NaN is truthy and confirms `is True` is the correct guard. The test intentionally asserts BOTH the buggy and fixed behaviour so future readers understand why `is True` is required.

**Bug: Stale `@st.cache_data` serving old failure results after fixes**
- `get_comet_summary()` and `get_asteroid_summary()` are `@st.cache_data(ttl=3600)`. Results from the first broken run were cached for 1 hour.
- `_load_jpl_overrides()` is also `@st.cache_data` — new override entries (e.g. `"3 Juno": "3;"`) were invisible to the batch until server restart.
- "Always rerun" in Streamlit does NOT clear `@st.cache_data`. Only killing and restarting the process does.
- Fix: "🔄 Refresh JPL Data" button added to both Comet Admin and Asteroid Admin panels. Calls `_load_jpl_overrides.clear()`, `get_comet_summary.clear()`, `get_asteroid_summary.clear()`, then reruns.
- **Deferred (next iteration):** Don't cache failure stub rows at all — strip them from the `@st.cache_data` result and re-query them fresh on each render. Eliminates the entire class of stale-failure bugs.

**Bug: `3 Juno` bare number `3` is ambiguous in Horizons**
- Horizons rejects bare `3` (conflicts with other designations).
- Fix: override `"3 Juno": "3;"` in `jpl_id_overrides.yaml` — trailing semicolon forces small-body search.

**Bug: Trajectory picker shows bare JPL ID instead of display name**
- `resolve_horizons("2")` returns `("2", sc)`. The "Resolved:" banner showed `2` instead of `2 Pallas`.
- Fix: after a successful resolve, if the user selected from the list (not a custom entry), `name = selected_target` overrides the raw JPL query string. Applied to both comet My List and asteroid trajectory pickers.

### New scripts and tests
- `scripts/diagnose_jpl.py` — standalone CLI: tests all comets and asteroids against live JPL Horizons API, prints pass/fail per object with the ID source (override / cache / stripped / number-extracted). Result after all fixes: 41/41 resolved OK.
- 3 new tests in `tests/test_jpl_resolution.py` (total: 46 tests).

### Files changed
`app.py`, `jpl_id_cache.json`, `jpl_id_overrides.yaml`, `scripts/diagnose_jpl.py`, `tests/test_jpl_resolution.py`, `CHANGELOG.md`.

---

## 2026-02-25 — Night Plan Builder: sort by Set Time or Transit Time

**Change:** The Night Plan Builder's sort order is no longer driven by priority. Priority colour-coding (URGENT red / HIGH orange / LOW green) remains for visual scanning, but the plan is now sorted purely by time.

**New UX — Row B radio:**
- "Sort & filter plan by: ● Set Time  ○ Transit Time"
- Selecting one changes both the time-input label ("Sets no earlier than" → "Transits no earlier than") and the sort column (`_set_datetime` → `_transit_datetime`), so the filter threshold and sort always match.
- A dynamic caption below the radio states the active sort: *"Plan sorted by **Set Time** — targets that set soonest appear first."*

**`build_night_plan()` simplified:**
- Removed `pri_col`, `dur_col` dead parameters
- Added `sort_by="set"|"transit"` parameter
- Sorts ascending by the chosen datetime column, NaT last
- Drops the internal `_time_sort` temp column before returning

**Scope:** All 6 Night Plan Builder sections (DSO, Planet, Comet My List, Comet Catalog, Asteroid, Cosmic).

**Files changed:** `app.py`, `CLAUDE.md`, `CHANGELOG.md`, `README.md`.

---

## 2026-02-24 — Night Plan Builder UI polish (layout + dynamic priorities)

**Fix 1 — Two-row filter layout:** Sections with 4+ filters (DSO, Cosmic) had cramped columns with truncated multiselect labels. Filters are now split into two rows: Row A for data-specific filters (Magnitude, Type, Discovery) and Row B for Set time + Moon Status. DSO gets 2+2, Cosmic gets 3+2, simpler sections keep a single 2-column row.

**Fix 2 — Dynamic priority options:** The priority multiselect was hardcoded to URGENT/HIGH/LOW/(unassigned), but Comet and Asteroid sections only use `⭐ PRIORITY` (binary flag). Now detects actual priority values from the DataFrame. Comets/Asteroids show `[⭐ PRIORITY, (unassigned)]`. Cosmic shows `[URGENT, HIGH, LOW, (unassigned)]`. Caption text adapts accordingly.

**Files changed:** `app.py`, `CLAUDE.md`, `CHANGELOG.md`.

**Branch:** `feature/night-plan-all-sections`

---

## 2026-02-24 — Night Plan Builder expanded to all sections

**Change:** The Night Plan Builder (previously Cosmic-only) now appears in every section: DSO, Planet, Comet My List, Comet Catalog, Asteroid, and Cosmic. Each section has its own collapsible expander inside the Observable tab.

**Implementation:** Extracted ~297 lines of inline Cosmic Night Plan Builder code into a shared `_render_night_plan_builder()` function. The function adapts its filter layout dynamically based on which columns are available:
- **DSO:** Magnitude slider + Type multiselect + Set time + Moon Status (4 filter columns)
- **Planet:** Set time + Moon Status (2 filter columns)
- **Comet My List / Asteroid:** Priority multiselect + Set time + Moon Status (2 filter columns + priority row)
- **Comet Catalog:** Set time + Moon Status (2 filter columns)
- **Cosmic:** All 5 filters (unchanged behaviour)

All sections support CSV + PDF export. Priority row highlighting works in Comet, Asteroid, and Cosmic sections.

**Widget key uniqueness:** Each call site passes a `section_key` parameter (e.g. `"dso_stars"`, `"planet"`, `"comet_mylist"`) that prefixes all Streamlit widget keys to prevent duplicate key errors.

**Files changed:** `app.py`, `CLAUDE.md`, `CHANGELOG.md`.

**Branch:** `feature/night-plan-all-sections`

---

## 2026-02-24 — Migrate scrapers from Selenium to Scrapling + add priority removal detection

**Change 1 — Scraper migration:** Replaced Selenium + webdriver-manager with [Scrapling](https://github.com/D4Vinci/Scrapling) (`StealthyFetcher`) in `backend/scrape.py`. Scrapling uses Patchright (Playwright fork) — no more ChromeDriver version mismatches. Added `_deep_text()` helper because Scrapling's `.text` only returns direct text nodes (Selenium's `.text` returns all descendant text). Tested side-by-side: identical output across all 3 scrapers (transient events 78/78 rows, comet missions 7/7, asteroid missions 2/2). Scrapling also bypasses Cloudflare browser checks that block headless Selenium.

**Change 2 — Priority removal detection:** The app already detected when Unistellar *added* new priority targets. Now it also detects *removals* — objects in our `unistellar_priority` list that are no longer on Unistellar's missions page. Removals appear as pending requests in the admin panel (Accept removes from YAML priority list, Reject dismisses). Orange warning banners shown in the Priority expanders.

**Change 3 — GitHub Actions priority sync:** New workflow `check-unistellar-priorities.yml` (Mon + Thu 07:00 UTC) scrapes both Unistellar mission pages, compares with YAML, and creates GitHub Issues with `priority-added` (green) or `priority-removed` (red) labels. Supports `# aka 3I/ATLAS` YAML comments for redesignated objects (avoids false add/remove pairs when an object gets a new designation).

**Change 4 — Watchlist sync:** Updated `comets.yaml` (tagged C/2025 N1 as `# aka 3I/ATLAS`, removed 24P/Schaumasse from priority) and `asteroids.yaml` (removed 433 Eros, 2033 Basilea, 3260 Vizbor from priority — no longer on Unistellar's page).

**Files changed:** `backend/scrape.py`, `app.py`, `requirements.txt`, `comets.yaml`, `asteroids.yaml`, `.gitignore`, `CLAUDE.md`. **New files:** `scripts/check_unistellar_priorities.py`, `scripts/open_priority_issues.py`, `.github/workflows/check-unistellar-priorities.yml`.

---

## 2026-02-24 — Fix asteroid priority scraper (find all 5 Unistellar missions)

**Bug:** `scrape_unistellar_priority_asteroids()` only found 2 of 5 priority asteroids (2001 FD58, 1796 Riga). Missing: 433 Eros, 2033 Basilea, 3260 Vizbor.

**Root cause:** The scraper used regex on the full page body text. Unistellar's planetary defense page uses three different naming formats in `<h3>` headings:
- `2001 FD58` — standard provisional designation (regex matched)
- `2033 (Basilea)` — number then parenthesized name (regex missed)
- `Eros` — bare name with no number (regex missed)

**Fix:** Rewrote scraper to extract targets from `<h3>` headings directly instead of regex on body text. Added `_BARE_NAME_ALIASES` dict for well-known bare names (Eros→433 Eros, Apophis→99942 Apophis, etc.), `_NUM_PAREN_NAME_RE` regex to normalize `2033 (Basilea)` → `2033 Basilea`, and `_SKIP_HEADINGS` set to filter page-structure headings. Updated `asteroids.yaml` to include all 5 in `unistellar_priority`.

**Files changed:** `backend/scrape.py`, `asteroids.yaml`, `CLAUDE.md`.

---

## 2026-02-24 — Fix Scrapling browser issues (Streamlit Cloud + Windows)

**Bug 1 — Streamlit Cloud:** Cosmic page scraper failed with "Failed to scrape data". Patchright (Playwright fork) needs a Chromium browser binary installed, which isn't pre-installed on Streamlit Cloud.

**Fix:** Added `_ensure_browser()` that runs `patchright install chromium` once per session before the first scrape. Updated `packages.txt` from Selenium system deps (chromium, chromium-driver) to the ~20 system libraries Patchright's Chromium needs on Linux (libnss3, libgbm1, etc.).

**Bug 2 — Windows local:** `NotImplementedError` from `asyncio.base_events._make_subprocess_transport`. Windows' default `SelectorEventLoop` doesn't support subprocess creation, which Playwright needs to launch Chromium.

**Fix:** Added `_fetch_page(url, **kwargs)` wrapper that runs `StealthyFetcher.fetch()` in a `ThreadPoolExecutor` worker thread and sets `asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())` process-wide on `win32`. The process-wide policy is required because Playwright creates its own event loop in a background thread via `new_event_loop()` — per-thread `set_event_loop()` doesn't affect it.

**Files changed:** `backend/scrape.py`, `packages.txt`.

---

## 2026-02-24 — Simplify Night Plan Builder + add Moon Status filter

**Change 1 — Remove scheduling columns:** Removed Obs Start/End columns from the Night Plan table and PDF. The builder now returns a priority-sorted target list — the user decides when to observe each target. `build_night_plan()` no longer takes `start_time`/`end_time` parameters.

**Change 2 — Remove scheduling metrics:** Removed "Scheduled Time" and "Remaining Window" metrics. A single "Targets Planned" metric is shown above the table.

**Change 3 — Add Moon Status filter:** Added Moon Status multiselect to the Night Plan Builder's filter row (5th column). Options: 🌑 Dark Sky, ✅ Safe, ⚠️ Caution, ⛔ Avoid. All selected by default; only filters when the user deselects at least one status.

**Files changed:** `app.py`, `CLAUDE.md`.

**Branch:** `feature/night-plan-simplify`

---

## 2026-02-24 — Sync Gantt chart sort with overview table sort

**Change:** The Gantt chart sort radio buttons (Earliest Set / Rise / Transit / Default) now also reorder the overview table below. Previously, the chart and table were completely decoupled — the chart visual reordered but the table always stayed in its original order.

**Implementation:**
- `plot_visibility_timeline()` now returns the selected `sort_option` string (was implicit `None`)
- New `_sort_df_like_chart(df, sort_option, priority_col)` helper reorders the DataFrame to match the chart's sort
- All 6 call sites (DSO, Planet, Comet My List, Comet Catalog, Asteroid, Cosmic) capture the return and sort the table DataFrame before display
- `display_dso_table()` no longer sorts by magnitude internally — it receives the pre-sorted DataFrame from the caller

**Always Up handling:** For Earliest Rise/Set/Transit sorts, Always Up objects are pushed to the bottom of the table (sorted by transit among themselves), matching the Gantt chart. For Priority/Default/Discovery Date sorts, they stay in their ranked position.

**Files changed:** `app.py`, `CLAUDE.md`.

**Branch:** `feature/night-plan-simplify`

---

## 2026-02-23 — Moon Separation calculation was completely wrong

**Bug:** Every Moon Sep value across all six sections was incorrect. Example: ZTF25abwjewp (RA 10h 24m, Dec +5°15') showed 4.4°–4.5° when the true separation was ~98°.

**Root cause:** Astropy's `get_body('moon')` returns a 3D GCRS coordinate with distance (~364,000 km). When computing `target.separation(moon)` where the target is in ICRS (at infinity) and the Moon is in GCRS (with finite distance), the cross-frame non-rotation transformation produces garbage angular separations. Astropy emits a `NonRotationTransformationWarning` for this, but it was suppressed by `warnings.filterwarnings("ignore")` in app.py line 44.

**Impact:** All Moon Sep values in all overview tables, trajectory views, Moon Sep filters, and Moon Status classifications were wrong since the feature was introduced. Objects near the Moon could show large separations (hiding Moon interference warnings), and objects far from the Moon could show small separations (incorrectly filtering them out).

**Fix:** Added `moon_sep_deg(target, moon)` helper in `backend/core.py` that strips the Moon's distance before computing separation, turning it into a direction-only unit-sphere coordinate. Replaced all 16 direct `.separation(moon)` calls across `app.py` and `backend/core.py`.

**Verification:**
```
OLD (broken): target.separation(moon_gcrs)           = 4.47°
NEW (fixed):  moon_sep_deg(target, moon_gcrs)        = 98.08°
Manual formula (spherical trig):                      = 98.08°
```

**Files changed:** `backend/core.py` (new helper + trajectory fix), `app.py` (16 call sites), `.gitignore` (added temp/).

**Commit:** `8373d94`

---

## 2026-02-23 — Moon Status column reintroduced in all overview tables

**Change:** `Moon Status` (🌑 Dark Sky / ⛔ Avoid / ⚠️ Caution / ✅ Safe) was previously computed but intentionally hidden because the underlying Moon Sep values were broken. Now that Moon Sep is fixed, Moon Status is shown as a separate column next to `Moon Sep (°)` in all overview tables (DSO, Planet, Comet, Asteroid, Cosmic), CSV exports, and the Night Plan PDF.

**Not included:** The per-step trajectory view (`compute_trajectory`) does not show Moon Status — it only shows the numeric `Moon Sep (°)` at each 10-minute step. Moon Status is an overview-level summary based on worst-case separation, not a per-step metric.

**Scope:** Removed `"Moon Status"` from all `.drop()` calls and column exclusion lists. Added to `display_cols_*` in all 5 sections, `_MOON_SEP_COL_CONFIG`, and `generate_plan_pdf()` column layout.

**Commit:** `b46a031`
