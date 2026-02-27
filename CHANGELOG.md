# Changelog

Bug fixes, discoveries, and notable changes. See CLAUDE.md for architecture and patterns.

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
