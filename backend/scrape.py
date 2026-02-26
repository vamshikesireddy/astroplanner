import re
import sys
import asyncio
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from scrapling.fetchers import StealthyFetcher

logger = logging.getLogger(__name__)

_browser_ready = False


def _ensure_browser():
    """Install Patchright Chromium if not already present (idempotent, runs once per session)."""
    global _browser_ready
    if _browser_ready:
        return
    try:
        subprocess.run(
            ["patchright", "install", "chromium"],
            check=True, capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        logger.warning(f"Browser auto-install note: {e}")
    _browser_ready = True


def _fetch_page(url, **kwargs):
    """Run StealthyFetcher.fetch in a worker thread to avoid event loop conflicts.

    Fixes two issues:
    - Streamlit's running asyncio loop conflicts with Playwright's sync API
    - Windows SelectorEventLoop doesn't support subprocess (needed by Playwright)

    set_event_loop_policy is process-wide, which is needed because Playwright
    creates its OWN event loop in a background thread via new_event_loop().
    """
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    def _worker():
        return StealthyFetcher.fetch(url, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_worker).result()


def _deep_text(element):
    """Get ALL text from a Scrapling element, including child elements.

    Scrapling's .text only returns the element's direct text node.
    This helper concatenates all descendant text via the ::text pseudo-selector
    to match Selenium's .text behaviour.
    """
    parts = element.css("::text")
    if parts:
        return " ".join(p.text.strip() for p in parts if p.text.strip())
    return element.text.strip()


def scrape_unistellar_table():
    url = "https://alerts.unistellaroptics.com/transient/events.html"

    try:
        logger.debug("Connecting to Unistellar Alerts...")
        _ensure_browser()
        page = _fetch_page(url, headless=True, network_idle=True)

        # Get headers
        headers = [_deep_text(h).replace('\n', ' ') for h in page.css("table th")]
        if headers and not headers[0]:
            headers[0] = "DeepLink"

        # Get all rows
        rows = page.css("table tbody tr")
        data = []

        logger.debug(f"Found {len(rows)} targets. Extracting data...")

        for row in rows:
            cells = row.css("td")
            if len(cells) < 2:
                continue

            row_data = []
            for i, cell in enumerate(cells):
                if i == 0:  # Handle the deep link icon
                    link_el = cell.css("a")
                    row_data.append(link_el[0].attrib.get("href", "") if link_el else "")
                else:
                    row_data.append(_deep_text(cell))
            data.append(row_data)

        # Create DataFrame
        df = pd.DataFrame(data, columns=headers)

        logger.debug(f"Extracted {len(df)} rows from Unistellar Alerts.")
        return df

    except Exception as e:
        logger.error(f"An error occurred scraping Unistellar Alerts: {e}")
        return None


_COMET_PATTERN = re.compile(
    r'(?:'
    r'[CAPI]/\d{4}\s+[A-Z]\d*\s+\([^)]+\)|'       # C/2025 N1 (ATLAS), A/..., P/2010 H2 (Vales)
    r'\d+[A-Za-z]/[A-Za-z][A-Za-z0-9\-]+(?:\s+\d+)?'  # 29P/Schwassmann-Wachmann 1, 3I/ATLAS, 235P/LINEAR
    r')'
)


def scrape_unistellar_priority_comets():
    """Scrapes the Unistellar comet missions page to extract active priority comet designations."""
    url = "https://science.unistellar.com/comets/missions/"

    try:
        _ensure_browser()
        page = _fetch_page(url, headless=True, network_idle=True)

        # Collect text from headings and content sections (Divi theme structure)
        elements = page.css("h1,h2,h3,h4,p,.et_pb_text_inner")
        text = " ".join(re.sub(r'\s+', ' ', _deep_text(el)) for el in elements if _deep_text(el))

        # Extract and deduplicate comet designations
        found = list(dict.fromkeys(_COMET_PATTERN.findall(text)))
        return found
    except Exception as e:
        logger.error(f"Failed to scrape Unistellar missions page: {e}")
        return []


_ASTEROID_PATTERN = re.compile(
    r'(?:'
    r'\(\d+\)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|'  # (2033) Basilea, (3260) Vizbor — IAU parenthesized format
    r'\d{1,4}\s+\([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\)|'  # 2033 (Basilea), 3260 (Vizbor) — Unistellar format
    r'\d{5,}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|'   # 99942 Apophis, 101955 Bennu
    r'\d{1,4}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|'  # 433 Eros, 16 Psyche, 2033 Basilea
    r'\d{4}\s+[A-Z]{1,2}\d+|'                       # 2024 YR4, 1994 PC1
    r'\d{4}\s+[A-Z]{2}\d*'                          # 2001 SN263, 2001 FD58
    r')'
)

_PAREN_NUM_RE = re.compile(r'^\((\d+)\)\s+')
_NUM_PAREN_NAME_RE = re.compile(r'^(\d+)\s+\(([^)]+)\)$')


def _normalize_asteroid_match(name):
    """Normalize asteroid name to 'NUMBER Name' format.

    Handles:
      '(2033) Basilea' → '2033 Basilea'   (IAU parenthesized number)
      '2033 (Basilea)' → '2033 Basilea'   (Unistellar parenthesized name)
    """
    m = _NUM_PAREN_NAME_RE.match(name)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return _PAREN_NUM_RE.sub(r'\1 ', name)


_BARE_NAME_ALIASES = {
    "Eros": "433 Eros",
    "Apophis": "99942 Apophis",
    "Bennu": "101955 Bennu",
    "Psyche": "16 Psyche",
    "Phaethon": "3200 Phaethon",
}

# Headings to skip — these are page structure, not asteroid targets
_SKIP_HEADINGS = {
    "missions", "near-earth asteroid campaigns", "main-belt campaigns",
    "new here?", "astro llm",
}


def scrape_unistellar_priority_asteroids():
    """Scrapes the Unistellar planetary defense missions page to extract active priority asteroid designations."""
    url = "https://science.unistellar.com/planetary-defense/missions/"

    try:
        _ensure_browser()
        page = _fetch_page(url, headless=True, network_idle=True)

        # Each mission target is an <h3> heading on the page
        found = []
        for el in page.css("h3"):
            name = _deep_text(el).strip()
            if not name or name.lower() in _SKIP_HEADINGS:
                continue

            # Try regex match first (handles numbered designations)
            m = _ASTEROID_PATTERN.search(name)
            if m:
                found.append(_normalize_asteroid_match(m.group()))
            elif name in _BARE_NAME_ALIASES:
                found.append(_BARE_NAME_ALIASES[name])
            else:
                # Unknown bare name — include as-is so it surfaces in diff
                found.append(name)

        return list(dict.fromkeys(found))
    except Exception as e:
        logger.error(f"Failed to scrape Unistellar planetary defense page: {e}")
        return []


_EXOPLANET_RE = re.compile(
    r'\b(?:'
    r'HAT-P-\d+\s+[a-z]\b|'          # HAT-P-12 b
    r'WASP-\d+\s+[a-z]\b|'           # WASP-43 b
    r'TOI-\d+\s+[a-z]\b|'            # TOI-1431 b
    r'Kepler-\d+\s+[a-z]\b|'         # Kepler-7 b
    r'GJ\s+\d+\w*\s+[a-z]\b|'        # GJ 3470 b
    r'HD\s+\d+\w*\s+[a-z]\b|'        # HD 73583 b
    r'L\s+\d+-\d+\s+[a-z]\b|'        # L 98-59 b
    r'K2-\d+\s+[a-z]\b|'             # K2-18 b
    r'LTT\s+\d+\w*\s+[a-z]\b|'       # LTT 9779 b
    r'XO-\d+\s+[a-z]\b|'             # XO-3 b
    r'TrES-\d+\s+[a-z]\b|'           # TrES-3 b
    r'CoRoT-\d+\s+[a-z]\b'           # CoRoT-2 b
    r')',
    re.IGNORECASE
)


def scrape_unistellar_exoplanets():
    """Scrape Unistellar exoplanet missions page → list of active planet name strings.

    Returns [] on failure (network error, scrape error, no matches).
    Uses same Scrapling/StealthyFetcher pattern as other scrapers.
    """
    url = "https://science.unistellar.com/exoplanets/missions/"
    try:
        _ensure_browser()
        page = _fetch_page(url, headless=True, network_idle=True)
        if page is None:
            return []
        # Gather text from all heading + text elements
        elements = page.css("h1,h2,h3,h4,h5,p,.et_pb_text_inner,div")
        text = " ".join(_deep_text(el) for el in elements)
        found = list(dict.fromkeys(_EXOPLANET_RE.findall(text)))
        return found
    except Exception as e:
        logger.error(f"Failed to scrape Unistellar exoplanets page: {e}")
        return []


if __name__ == "__main__":
    scrape_unistellar_table()
