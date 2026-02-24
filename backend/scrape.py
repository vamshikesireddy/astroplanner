import re
import pandas as pd

from scrapling.fetchers import StealthyFetcher


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
        print("Connecting to Unistellar Alerts...")
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)

        # Get headers
        headers = [_deep_text(h).replace('\n', ' ') for h in page.css("table th")]
        if headers and not headers[0]:
            headers[0] = "DeepLink"

        # Get all rows
        rows = page.css("table tbody tr")
        data = []

        print(f"Found {len(rows)} targets. Extracting data...")

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

        print("\nSuccess! Sample of scraped data:")
        print(df.head(3))
        print(f"\nExtracted {len(df)} rows.")
        return df

    except Exception as e:
        print(f"An error occurred: {e}")
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
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)

        # Collect text from headings and content sections (Divi theme structure)
        elements = page.css("h1,h2,h3,h4,p,.et_pb_text_inner")
        text = " ".join(re.sub(r'\s+', ' ', _deep_text(el)) for el in elements if _deep_text(el))

        # Extract and deduplicate comet designations
        found = list(dict.fromkeys(_COMET_PATTERN.findall(text)))
        return found
    except Exception as e:
        print(f"Failed to scrape Unistellar missions page: {e}")
        return []


_ASTEROID_PATTERN = re.compile(
    r'(?:'
    r'\(\d+\)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|'  # (2033) Basilea, (3260) Vizbor — IAU parenthesized format
    r'\d{5,}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|'   # 99942 Apophis, 101955 Bennu
    r'\d{1,4}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|'  # 433 Eros, 16 Psyche, 2033 Basilea
    r'\d{4}\s+[A-Z]{1,2}\d+|'                       # 2024 YR4, 1994 PC1
    r'\d{4}\s+[A-Z]{2}\d*'                          # 2001 SN263, 2001 FD58
    r')'
)

_PAREN_NUM_RE = re.compile(r'^\((\d+)\)\s+')


def _normalize_asteroid_match(name):
    """Convert IAU parenthesized format '(2033) Basilea' → '2033 Basilea'."""
    return _PAREN_NUM_RE.sub(r'\1 ', name)


def scrape_unistellar_priority_asteroids():
    """Scrapes the Unistellar planetary defense missions page to extract active priority asteroid designations."""
    url = "https://science.unistellar.com/planetary-defense/missions/"

    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)

        # Collect text from headings and content sections (Divi theme structure)
        elements = page.css("h1,h2,h3,h4,p,.et_pb_text_inner")
        text = " ".join(re.sub(r'\s+', ' ', _deep_text(el)) for el in elements if _deep_text(el))

        # Extract, normalize parenthesized numbers, and deduplicate
        raw = _ASTEROID_PATTERN.findall(text)
        found = list(dict.fromkeys(_normalize_asteroid_match(m) for m in raw))
        return found
    except Exception as e:
        print(f"Failed to scrape Unistellar planetary defense page: {e}")
        return []


if __name__ == "__main__":
    scrape_unistellar_table()
