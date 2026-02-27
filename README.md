# 🔭 AstroPlanner

**Live App:** [https://astroplanner.streamlit.app/](https://astroplanner.streamlit.app/)

## Overview
The **Astro Coordinates Planner** is a web application designed for astrophotographers and astronomers. It helps you determine **if** and **when** a specific celestial object will be visible from your location tonight.

Instead of guessing, you can calculate the exact **Altitude** (height above horizon) and **Azimuth** (compass direction) of stars, comets, asteroids, or transient events over a specific time window.

## Key Features
*   **Precise Location & Time:** Automatically detects timezones based on your latitude/longitude.
*   **Deep Sky Objects (SIMBAD):** Browse the full Messier catalog (110 objects), Bright Stars (33), or Astrophotography Favorites (24 iconic nebulae, clusters, and galaxies) with batch visibility tables, Observable/Unobservable tabs, Gantt timeline, and magnitude-sorted results. Filter by object type. Select any target for a full trajectory, or search SIMBAD for any custom object by name.
*   **Solar System Objects (JPL Horizons):** Accurate ephemerides for planets, comets, and asteroids.
*   **Comet Tracking (My List):** Batch visibility for all tracked comets with Observable/Unobservable tabs, Gantt timeline, and ⭐ Priority highlighting sourced from the Unistellar Citizen Science missions page (checked daily). Includes user add-requests (JPL-verified) and an admin approval panel that syncs to GitHub.
*   **Comet Catalog (Explore Mode):** Browse the full MPC comet archive (~865 comets) with filters for orbit type (Long-period, Short-period), perihelion window, and estimated magnitude. Narrowed subsets are passed to JPL Horizons for batch visibility — no extra dependencies needed.
*   **New Comet Discovery Alerts:** A GitHub Actions workflow queries JPL SBDB twice weekly (Monday + Thursday) for comets discovered in the last 30 days. Any comet not already on the watchlist triggers a GitHub Issue for admin review. Deduplication prevents repeated alerts.
*   **Asteroid Tracking:** Same batch visibility system as comets with Unistellar Planetary Defense priority targets, observation windows for close-approach events (e.g. Apophis 2029), and smart JPL ID resolution for both numbered and provisional designations.
*   **Planet Visibility:** All 8 planets shown simultaneously with Observable/Unobservable tabs, Gantt timeline, and Dec filter integration. Select any planet for a full trajectory.
*   **Cosmic Cataclysms:** Live scraping of transient events (novae, supernovae, GRBs, variable stars) from Unistellar alerts. Includes a reporting system to filter out invalid/cancelled events or suggest target priorities. Features a **Night Plan Builder** that generates an optimized, sequential observation schedule for the night — see below.
*   **Observational Filters:** Filter targets based on Altitude (Min/Max), Azimuth, Declination, and Moon Separation. Declination-filtered objects are marked as Unobservable with a reason (rather than removed), so they remain visible in the Unobservable tab.
*   **Moon Separation:** Every overview table (DSO, Planet, Comet, Asteroid, Cosmic) shows a **Moon Sep (°)** column (`min°–max°` range across the observation window) and a **Moon Status** column (🌑 Dark Sky / ✅ Safe / ⚠️ Caution / ⛔ Avoid). Both columns are included in all CSV exports and the Night Plan PDF. The individual **trajectory Detailed Data table** shows the exact Moon Sep angle at every 10-minute step.
*   **Visibility Charts:** Gantt-style timeline chart (rise → set window per object) with transit time tick + gold label, and an optional observation window overlay (blue-tinted shaded region). Sort by Earliest Set (default), Earliest Rise, or Natural Order. Circumpolar ("Always Up") objects are grouped at the bottom. Altitude vs Time trajectory chart for every target mode.
*   **Night Plan Builder (all sections):** Every section's Observable tab has an open **📅 Night Plan Builder**. Choose to sort by **Set Time** or **Transit Time** — the same column drives both the filter threshold and the final sort order (earliest first). **Altitude-aware filtering** ensures only objects that actually reach your `min_alt` threshold *during the session window* are included (not just objects that passed the alt check at the sidebar start time). Additional filters: priority level, magnitude range, event class, discovery recency, and Moon Status. A **Parameters summary** line above the plan table shows all active filter settings (window, min alt, moon sep, azimuth directions, priority, moon status) at a glance. The plan table shows a **Peak Alt (°)** column — the highest altitude each object reaches during the session window. Priority rows are colour-coded (URGENT red / HIGH orange / LOW green) for at-a-glance scanning without affecting sort order. Exports the plan as **CSV** or **PDF** (Peak Alt column is on-screen only). The PDF is landscape A4 and priority-colour-coded. For Cosmic Cataclysm events the PDF also includes the full `unistellar://` deeplink URL for each target — designed to be loaded on a tablet before connecting the telescope to WiFi.
*   **Data Export:** Download trajectory data as CSV (includes Moon Sep per 10-min step) or overview tables as CSV (includes Moon Sep range). Night Plan PDF includes the Moon Sep range column.

## Installation

1.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **System Requirements (for Scraper):**
    Ensure Chrome/Chromium is installed if running locally.
    *   *Linux/Docker:* `apt-get install chromium chromium-driver`

## How to Use

### 1. Run the App
    ```bash
    streamlit run app.py
    ```

### 2. Set Location, Time & Filters (Sidebar)
*   **Location:** Search for a city, use Browser GPS, or enter coordinates manually. If GPS fails (e.g. Safari with Location Services off), a friendly message explains the cause and suggests the address search as a fallback.
*   **Time:** Set your observation start date and time.
*   **Duration:** Choose the length of your imaging session. Toggle between **hrs** and **min** display formats — the selected value is preserved when switching formats. The observation window is overlaid on all Gantt charts so you can immediately see which objects are up during your session.
*   **Filters:** Set Altitude (Min/Max), Azimuth, Declination, and Moon Separation limits to match your viewing site and conditions.

### 3. Choose a Target
Select one of the six modes:
*   **🌌 Star/Galaxy/Nebula:** Browse Messier, Bright Stars, or Astrophotography Favorites with batch visibility tables, Gantt timeline, and type filter. The trajectory target picker has its own independent catalog and type selector — change it without affecting the batch table above. Or enter any custom name (SIMBAD lookup).
*   **🪐 Planet:** View all planets at once — Observable/Unobservable tabs with Gantt timeline and Dec filter — or select one for a full trajectory.
*   **☄️ Comet:** Two modes via toggle:
    *   **📋 My List** — Batch visibility for all tracked comets. Priority targets from Unistellar missions page are highlighted. Select any comet for a full trajectory.
    *   **🔭 Explore Catalog** — Filter the full MPC archive by orbit type, perihelion window, and magnitude. Calculate batch visibility for the filtered subset.
*   **🪨 Asteroid:** Batch visibility for all tracked asteroids. Priority targets from Unistellar Planetary Defense highlighted, with observation windows for close-approach events. Select any asteroid for a full trajectory.
*   **💥 Cosmic Cataclysm:** Scrape live alerts for transient events. Use the "Report" feature to flag invalid/cancelled targets or suggest priorities.
*   **✍️ Manual:** Enter RA/Dec directly.

### 4. Build a Night Plan
Each section's Observable tab has a **📅 Night Plan Builder** open by default:
*   Set the **Session window** slider to your actual imaging start and end times.
*   The plan automatically excludes objects that don't reach your **Min Alt** threshold during that window (not just at sidebar start time).
*   A **Parameters summary** line shows all active filters — useful for understanding why an object may be missing from the plan.
*   The **Peak Alt (°)** column shows how high each object peaks during your window.
*   Export the final plan as **CSV** or **PDF**.

### 5. Explore a Trajectory
*   In the **3. Select X for Trajectory** picker, choose any target from the section.
*   Click **🚀 Calculate Visibility** (or **🚀 Calculate Trajectory**).
*   View the **Altitude Chart** (step 4) to see if the object is high enough during your session.
*   **Download CSV** for detailed 10-minute-step data including Moon Sep at each step.

## Project Structure
*   `app.py`: Main Streamlit web application.
*   `targets.yaml`: Cosmic Cataclysm event priorities, blocklist, and too-faint list.
*   `comets.yaml`: Comet watchlist, Unistellar priority targets, admin overrides, and cancelled list.
*   `comets_catalog.json`: MPC comet archive snapshot (~865 comets). Auto-updated weekly by GitHub Actions. Used by the Explore Catalog mode.
*   `asteroids.yaml`: Asteroid list, Unistellar Planetary Defense priority targets (with optional observation windows), admin overrides, and cancelled list.
*   `dso_targets.yaml`: Curated catalog — full Messier catalog (M1–M110), 33 bright stars, and 24 Astrophotography Favorites with pre-stored J2000 coordinates.
*   `backend/scrape.py`: [Scrapling](https://github.com/D4Vinci/Scrapling) (`StealthyFetcher`) scrapers for Unistellar alerts, comet missions page, and asteroid planetary defense page. Cloudflare-resistant; no ChromeDriver management needed.
*   `backend/core.py`: Trajectory calculation logic, rise/set/transit approximations, moon separation helper, and `compute_peak_alt_in_window()` (samples peak altitude during a session window for Night Plan altitude filtering).
*   `backend/resolvers.py`: Interfaces for SIMBAD and JPL Horizons.
*   `scripts/update_comet_catalog.py`: Downloads MPC comet orbital elements and saves to `comets_catalog.json`. Run by the weekly GitHub Actions workflow.
*   `scripts/check_new_comets.py`: Queries JPL SBDB for comets discovered in the last 30 days and compares against `comets.yaml`. Writes `_new_comets.json` if new comets are found (file is gitignored).
*   `scripts/open_comet_issues.py`: Reads `_new_comets.json` and creates GitHub Issues via the REST API for admin review. Deduplicates against open issues.
*   `.github/workflows/update-comet-catalog.yml`: Runs every Sunday at 02:00 UTC — downloads MPC catalog and commits `comets_catalog.json` if changed.
*   `.github/workflows/check-new-comets.yml`: Runs Monday + Thursday at 06:00 UTC — checks JPL SBDB for newly discovered comets and opens GitHub Issues for any not on the watchlist.
*   `Dockerfile`: Configuration for containerized deployment.