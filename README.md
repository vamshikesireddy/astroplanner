# astro_coordinates

This tool helps astrophotographers plan their sessions by calculating the altitude and azimuth of celestial targets over time for a specific location.

## Features

*   **Manual Input:** Define RA/Dec in `coordinates.py`.
*   **SIMBAD Lookup:** Resolve stars, galaxies, and nebulae by name.
*   **JPL Horizons:** Resolve comets and asteroids.
*   **Trajectory Calculation:** Computes visibility (Altitude/Azimuth) for the next 4 hours.

## Installation

Install the required Python packages:

```bash
pip install astropy astroquery geocoder timezonefinder pandas pytz
```

## Usage

1.  **Run the main script:**
    ```bash
    python main.py
    ```
2.  **Select a mode:**
    *   `1`: Uses the target defined in `coordinates.py`.
    *   `2`: Enter a name (e.g., "Andromeda") to look up via SIMBAD.
    *   `3`: Enter a name (e.g., "Halley") to look up via JPL Horizons.

## Project Structure

*   `main.py`: The entry point of the application.
*   `coordinates.py`: Configuration file for manual target input.
*   `core.py`: Trajectory calculations.
*   `resolvers.py`: SIMBAD and JPL Horizons interfaces.
*   `utils.py`: Helper functions.
