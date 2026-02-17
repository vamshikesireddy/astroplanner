# 🔭 Astro Coordinates Planner

**Live App:** [https://astro-coordinates.streamlit.app/](https://astro-coordinates.streamlit.app/)

## Overview
The **Astro Coordinates Planner** is a web application designed for astrophotographers and astronomers. It helps you determine **if** and **when** a specific celestial object will be visible from your location tonight.

Instead of guessing, you can calculate the exact **Altitude** (height above horizon) and **Azimuth** (compass direction) of stars, comets, asteroids, or transient events over a specific time window.

## Key Features
*   **Precise Location & Time:** Automatically detects timezones based on your latitude/longitude.
*   **Deep Sky Resolver (SIMBAD):** Instantly find coordinates for millions of stars, galaxies, and nebulae.
*   **Solar System Objects (JPL Horizons):** Accurate ephemerides for planets, comets, and asteroids.
*   **Cosmic Cataclysms:** Live scraping of transient events (novae, supernovae) from Unistellar alerts. Includes a reporting system to filter out invalid/cancelled events or suggest target priorities.
*   **Visibility Charts:** Visual graphs showing how high an object climbs in the sky.
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

### 2. Set Location & Time (Sidebar)
*   **Location:** Search for a city, use Browser GPS, or enter coordinates manually.
*   **Time:** Set your observation start date and time.
*   **Duration:** Choose how long you plan to image.

### 3. Choose a Target
Select one of the five modes:
*   **🌌 Star/Galaxy/Nebula:** Enter a name (e.g., `M42`, `Vega`).
    *   **🪐 Planet:** Select a major planet.
*   **☄️ Comet:** Select from popular comets or search JPL Horizons.
*   **🪨 Asteroid:** Select major asteroids or search by name.
*   **💥 Cosmic Cataclysm:** Scrape live alerts for transient events. Use the "Report" feature to flag invalid/cancelled targets or suggest priorities.
*   **✍️ Manual:** Enter RA/Dec directly.

### 4. Calculate & Analyze
*   Click **🚀 Calculate Visibility**.
*   View the **Altitude Chart** to see if the object is high enough.
*   **Download CSV** for detailed minute-by-minute data.

## Project Structure
*   `app.py`: Main Streamlit web application.
*   `scrape.py`: Selenium scraper for Unistellar alerts.
*   `core.py`: Trajectory calculation logic.
*   `resolvers.py`: Interfaces for SIMBAD and JPL Horizons.
*   `Dockerfile`: Configuration for containerized deployment.