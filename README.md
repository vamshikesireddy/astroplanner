# 🔭 Astro Coordinates Planner

**Live App:** [https://astro-coordinates.streamlit.app/](https://astro-coordinates.streamlit.app/)

## Overview
The **Astro Coordinates Planner** is a web application designed for astrophotographers and astronomers. It helps you determine **if** and **when** a specific celestial object will be visible from your location tonight.

Instead of guessing, you can calculate the exact **Altitude** (height above horizon) and **Azimuth** (compass direction) of stars, comets, asteroids, or transient events over a specific time window.

## Key Features
*   **Precise Location & Time:** Automatically detects timezones based on your latitude/longitude.
*   **Deep Sky Objects (SIMBAD):** Browse the full Messier catalog (110 objects), Bright Stars (33), or Astrophotography Favorites (24 iconic nebulae, clusters, and galaxies) with batch visibility tables, Observable/Unobservable tabs, Gantt timeline, and magnitude-sorted results. Filter by object type. Select any target for a full trajectory, or search SIMBAD for any custom object by name.
*   **Solar System Objects (JPL Horizons):** Accurate ephemerides for planets, comets, and asteroids.
*   **Comet Tracking:** Batch visibility for all tracked comets with Observable/Unobservable tabs, Gantt timeline, and ⭐ Priority highlighting sourced from the Unistellar Citizen Science missions page (checked daily). Includes user add-requests (JPL-verified) and an admin approval panel that syncs to GitHub.
*   **Asteroid Tracking:** Same batch visibility system as comets with Unistellar Planetary Defense priority targets, observation windows for close-approach events (e.g. Apophis 2029), and smart JPL ID resolution for both numbered and provisional designations.
*   **Cosmic Cataclysms:** Live scraping of transient events (novae, supernovae) from Unistellar alerts. Includes a reporting system to filter out invalid/cancelled events or suggest target priorities.
*   **Observational Filters:** Filter targets based on Altitude (Min/Max), Azimuth, Declination, and Moon Separation.
*   **Moon Interference:** Automatically calculates Moon phase, separation, and compass direction. Assigns status (Safe/Caution/Avoid) to targets.
*   **Visibility Charts:** Gantt-style timeline chart (rise → set window per object) with transit time tick + label, and an optional observation window overlay (shaded region + start/end lines) that updates live with your sidebar settings. Circumpolar ("Always Up") objects span the full chart width. Altitude vs Time trajectory chart for every target mode.
*   **Data Export:** Download trajectory data as CSV for use in telescope mount software.

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
*   **🪐 Planet:** View all planets at once — Observable/Unobservable tabs with Gantt timeline, or select one for a full trajectory.
*   **☄️ Comet:** Batch visibility for all tracked comets. Priority targets from Unistellar missions page are highlighted. Select any comet for a full trajectory + visibility window chart.
*   **🪨 Asteroid:** Batch visibility for all tracked asteroids. Priority targets from Unistellar Planetary Defense highlighted, with observation windows for close-approach events. Select any asteroid for a full trajectory.
*   **💥 Cosmic Cataclysm:** Scrape live alerts for transient events. Use the "Report" feature to flag invalid/cancelled targets or suggest priorities.
*   **✍️ Manual:** Enter RA/Dec directly.

### 4. Calculate & Analyze
*   Click **🚀 Calculate Visibility**.
*   View the **Altitude Chart** to see if the object is high enough.
*   **Download CSV** for detailed minute-by-minute data.

## Project Structure
*   `app.py`: Main Streamlit web application.
*   `targets.yaml`: Cosmic Cataclysm event priorities, blocklist, and too-faint list.
*   `comets.yaml`: Comet list, Unistellar priority targets, admin overrides, and cancelled list.
*   `asteroids.yaml`: Asteroid list, Unistellar Planetary Defense priority targets (with optional observation windows), admin overrides, and cancelled list.
*   `dso_targets.yaml`: Curated catalog — full Messier catalog (M1–M110), 33 bright stars, and 24 Astrophotography Favorites with pre-stored J2000 coordinates.
*   `backend/scrape.py`: Selenium scrapers for Unistellar alerts, comet missions page, and asteroid planetary defense page.
*   `backend/core.py`: Trajectory calculation logic.
*   `backend/resolvers.py`: Interfaces for SIMBAD and JPL Horizons.
*   `Dockerfile`: Configuration for containerized deployment.