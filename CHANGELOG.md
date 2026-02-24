# Changelog

Bug fixes, discoveries, and notable changes. See CLAUDE.md for architecture and patterns.

---

## 2026-02-24 — Migrate scrapers from Selenium to Scrapling + add priority removal detection

**Change 1 — Scraper migration:** Replaced Selenium + webdriver-manager with [Scrapling](https://github.com/D4Vinci/Scrapling) (`StealthyFetcher`) in `backend/scrape.py`. Scrapling uses Patchright (Playwright fork) — no more ChromeDriver version mismatches. Added `_deep_text()` helper because Scrapling's `.text` only returns direct text nodes (Selenium's `.text` returns all descendant text). Tested side-by-side: identical output across all 3 scrapers (transient events 78/78 rows, comet missions 7/7, asteroid missions 2/2). Scrapling also bypasses Cloudflare browser checks that block headless Selenium.

**Change 2 — Priority removal detection:** The app already detected when Unistellar *added* new priority targets. Now it also detects *removals* — objects in our `unistellar_priority` list that are no longer on Unistellar's missions page. Removals appear as pending requests in the admin panel (Accept removes from YAML priority list, Reject dismisses). Orange warning banners shown in the Priority expanders.

**Change 3 — GitHub Actions priority sync:** New workflow `check-unistellar-priorities.yml` (Mon + Thu 07:00 UTC) scrapes both Unistellar mission pages, compares with YAML, and creates GitHub Issues with `priority-added` (green) or `priority-removed` (red) labels. Supports `# aka 3I/ATLAS` YAML comments for redesignated objects (avoids false add/remove pairs when an object gets a new designation).

**Change 4 — Watchlist sync:** Updated `comets.yaml` (tagged C/2025 N1 as `# aka 3I/ATLAS`, removed 24P/Schaumasse from priority) and `asteroids.yaml` (removed 433 Eros, 2033 Basilea, 3260 Vizbor from priority — no longer on Unistellar's page).

**Files changed:** `backend/scrape.py`, `app.py`, `requirements.txt`, `comets.yaml`, `asteroids.yaml`, `.gitignore`, `CLAUDE.md`. **New files:** `scripts/check_unistellar_priorities.py`, `scripts/open_priority_issues.py`, `.github/workflows/check-unistellar-priorities.yml`.

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
