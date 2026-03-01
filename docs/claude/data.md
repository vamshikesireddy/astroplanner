# AstroPlanner — Data Pipelines & CI Reference

> Read this file when touching: GH Actions workflows, data pipelines, scraping,
> data files (YAML/JSON), or secrets configuration.
> See `CLAUDE.md` for architecture overview.

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

## Data Files — What Changes and What Doesn't

| File | Changes by | How |
|---|---|---|
| `comets.yaml` | Admin panel (app) | `save_comets_config()` + GitHub push |
| `comets_catalog.json` | GitHub Actions | `update-comet-catalog.yml` (weekly) |
| `asteroids.yaml` | Admin panel (app) | `save_asteroids_config()` + GitHub push |
| `targets.yaml` | Admin panel (app) | Direct file write + GitHub push |
| `dso_targets.yaml` | Manually only | Static, no automated updates |
| `_new_comets.json` | `check_new_comets.py` | Temp file, gitignored, deleted each run |
| `ephemeris_cache.json` | `update_ephemeris_cache.py` (daily CI) | 30-day batch positions; zero JPL calls for dates ≤30 days out |
| `jpl_id_cache.json` | `populate_jpl_cache.py` (weekly CI) | SBDB SPK-IDs for Horizons queries |
| `jpl_id_overrides.yaml` | Manually only | Manual SBDB ID overrides for problematic names |

---

## Secrets (`.streamlit/secrets.toml` — NOT in git)

```toml
GITHUB_TOKEN = "ghp_..."          # fine-grained PAT: contents read/write, issues write
ADMIN_PASSWORD = "..."            # gates all admin panels
GITHUB_REPO = "vamshikesireddy/astroplanner"
```

GitHub Actions uses the automatic `secrets.GITHUB_TOKEN` — no manual PAT needed for workflows.

---

## GitHub Actions Workflow Schedule

| Workflow | Cron | Purpose |
|---|---|---|
| `update-comet-catalog.yml` | Sun 02:00 UTC | MPC archive → `comets_catalog.json` |
| `update-ephemeris-cache.yml` | Daily 07:00 UTC | 30-day positions → `ephemeris_cache.json` |
| `update-jpl-cache.yml` | Sun 06:00 UTC | SBDB ID resolve → `jpl_id_cache.json` (weekly) |
| `check-new-comets.yml` | Mon/Thu 06:00 UTC | JPL SBDB new discovery alerts |
| `check-unistellar-priorities.yml` | Mon/Thu 07:00 UTC | Unistellar priority sync |
| `download-dso-images.yml` | On push to main (dso_targets.yaml changed) | Auto-download new DSO thumbnails |

---
