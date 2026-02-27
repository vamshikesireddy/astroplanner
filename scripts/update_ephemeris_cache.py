#!/usr/bin/env python3
"""
update_ephemeris_cache.py — Pre-compute 30-day RA/Dec ephemerides for all
watchlist comets and asteroids. Run daily via GitHub Actions.

Output: ephemeris_cache.json (committed to repo)
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astropy.time import Time
from backend.config import (
    read_comets_config, read_asteroids_config, read_jpl_overrides,
)
from backend.resolvers import _horizons_query
from backend.github import create_issue

COMETS_FILE = "comets.yaml"
ASTEROIDS_FILE = "asteroids.yaml"
OVERRIDES_FILE = "jpl_id_overrides.yaml"
OUTPUT_FILE = "ephemeris_cache.json"
HORIZON_DAYS = 30
REQUEST_DELAY = 0.5  # seconds between requests — polite to JPL


def _strip_comet_id(name):
    """Strip parenthetical for Horizons query: 'C/2024 G3 (ATLAS)' → 'C/2024 G3'."""
    return name.split('(')[0].strip()


def _strip_asteroid_id(name):
    """Extract Horizons-compatible ID from asteroid display name."""
    if name and re.match(r'^\d{4}\s+[A-Z]{1,2}\d', name):
        return name          # Provisional: '2001 FD58'
    if name and name[0].isdigit():
        return name.split(' ')[0]   # Numbered: '433 Eros' → '433'
    return name


def _build_epochs(days=HORIZON_DAYS):
    """Return (start_str, epochs_dict) for today → today+days."""
    today = datetime.now(timezone.utc).date()
    start = today.strftime('%Y-%m-%d')
    stop = (today + timedelta(days=days)).strftime('%Y-%m-%d')
    return start, {'start': start, 'stop': stop, 'step': '1d'}


def _extract_positions(result):
    """Convert Horizons result rows → list of {date, ra, dec} dicts."""
    positions = []
    for row in result:
        t = Time(float(row['datetime_jd']), format='jd', scale='utc')
        positions.append({
            'date': t.datetime.strftime('%Y-%m-%d'),
            'ra':   round(float(row['RA']),  6),
            'dec':  round(float(row['DEC']), 6),
        })
    return positions


def _lookup_cached_position(cache, section, name, target_date_str):
    """Return (ra, dec) from cache for a given object+date, or None."""
    obj = cache.get(section, {}).get(name)
    if not obj:
        return None
    for pos in obj.get('positions', []):
        if pos['date'] == target_date_str:
            return pos['ra'], pos['dec']
    return None


def _validate_name(stored_name, section):
    """Query SBDB for current canonical fullname. Returns changed canonical or None."""
    import requests
    query = _strip_comet_id(stored_name) if section == 'comets' else _strip_asteroid_id(stored_name)
    try:
        resp = requests.get(
            'https://ssd-api.jpl.nasa.gov/sbdb.api',
            params={'sstr': query, 'full-prec': '0'},
            timeout=10,
        )
        if resp.status_code == 200:
            canonical = resp.json().get('object', {}).get('fullname')
            if canonical:
                # SBDB appends provisional designation in parens: '433 Eros (A898 PA)'
                # Strip it before comparing so we only flag genuine renames.
                # Also strip parens from stored_name (comet discoverer names like
                # 'C/2024 G3 (ATLAS)' are stored with parens in YAML but SBDB
                # returns them without — these are not renames).
                canonical_base = canonical.split('(')[0].strip()
                stored_base = stored_name.split('(')[0].strip()
                return canonical_base if canonical_base != stored_base else None
    except Exception:
        pass
    return None


def _fetch_object(name, section, overrides):
    """Fetch 30-day ephemeris for one object. Returns (positions, error_or_None)."""
    override_id = overrides.get(section, {}).get(name)
    if override_id:
        horizons_id = override_id
    elif section == 'comets':
        horizons_id = _strip_comet_id(name)
    else:
        horizons_id = _strip_asteroid_id(name)

    _start_str, epochs = _build_epochs()
    try:
        result = _horizons_query(
            horizons_id, '500', epochs,
            closest_apparition=(section == 'comets'),
        )
        return _extract_positions(result), None
    except Exception as exc:
        return [], str(exc)[:200]


def main():
    print("=== update_ephemeris_cache.py ===")

    overrides = read_jpl_overrides(OVERRIDES_FILE)
    comet_cfg = read_comets_config(COMETS_FILE)
    asteroid_cfg = read_asteroids_config(ASTEROIDS_FILE)

    comet_names = comet_cfg.get('comets', [])
    asteroid_names = [
        (e['name'] if isinstance(e, dict) else e)
        for e in asteroid_cfg.get('asteroids', [])
    ]

    output = {
        'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
        'horizon_days': HORIZON_DAYS,
        'comets': {},
        'asteroids': {},
        'name_changes': [],
        'failures': [],
    }

    all_objects = (
        [(n, 'comets') for n in comet_names] +
        [(n, 'asteroids') for n in asteroid_names]
    )

    for name, section in all_objects:
        print(f"  Fetching {section[:-1]}: {name!r} ...", end=' ', flush=True)
        positions, error = _fetch_object(name, section, overrides)
        if error:
            print(f"FAIL: {error}")
            output['failures'].append({'section': section, 'name': name, 'error': error})
        else:
            print(f"OK ({len(positions)} days)")
            output[section][name] = {'positions': positions}

            # Name validation — check SBDB canonical name
            canonical = _validate_name(name, section)
            if canonical and canonical != name:
                print(f"    WARNING Name change: {name!r} -> {canonical!r}")
                output['name_changes'].append({
                    'section': section,
                    'stored': name,
                    'canonical': canonical,
                })

        time.sleep(REQUEST_DELAY)

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)
    print(f"\n=== Written to {OUTPUT_FILE} ===")
    print(f"  Comets:       {len(output['comets'])}")
    print(f"  Asteroids:    {len(output['asteroids'])}")
    print(f"  Name changes: {len(output['name_changes'])}")
    print(f"  Failures:     {len(output['failures'])}")

    # GitHub notification for name changes or failures
    token = os.environ.get('GITHUB_TOKEN')
    repo  = os.environ.get('GITHUB_REPOSITORY')
    issues = output['name_changes'] + output['failures']
    if token and repo and issues:
        lines = []
        if output['name_changes']:
            lines.append('## WARNING: Object name changes detected\n')
            for nc in output['name_changes']:
                lines.append(f"- `{nc['stored']}` -> `{nc['canonical']}` ({nc['section']})")
            lines.append('\nUpdate the relevant YAML file and re-run.')
        if output['failures']:
            lines.append('\n## Ephemeris fetch failures\n')
            for fail in output['failures']:
                lines.append(f"- `{fail['name']}` ({fail['section']}): {fail['error']}")
        body = '\n'.join(lines) + '\n\n_Generated by `update_ephemeris_cache.py`_'
        create_issue(token, repo,
                     'WARNING: Ephemeris cache: name changes or fetch failures', body,
                     labels=['ephemeris-cache'])
        print(f"\nGitHub Issue created.")

    return 1 if output['failures'] else 0


if __name__ == '__main__':
    sys.exit(main())
