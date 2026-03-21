#!/usr/bin/env python3
"""
Geocode remaining un-geocoded tournament events using:
1. A large dictionary of known fishing locations
2. Fuzzy matching against the known dictionary
3. Nominatim (OpenStreetMap) API as fallback

Usage:
    python3 geocode_remaining.py --workspace /workspace/castline
"""

import argparse
import csv
import json
import os
import re
import time
import urllib.request
import urllib.parse
from pathlib import Path

import pandas as pd

# ── Known fishing locations (lat, lon) ──────────────────────────────────────
KNOWN_LOCATIONS = {
    # Major bass tournament lakes
    "chesapeake bay": (38.98, -76.30),
    "rend lake": (38.05, -88.98),
    "arkansas river": (35.39, -94.40),
    "arkansas river, muskogee, ok": (35.75, -95.37),
    "ohio river": (38.58, -84.77),
    "lake oconee": (33.58, -83.42),
    "kerr": (36.60, -78.35),
    "kerr lake": (36.60, -78.35),
    "lake russell": (34.08, -82.62),
    "lake ferguson": (33.42, -91.05),
    "ohio river rocky point": (38.73, -84.85),
    "kissimmee river": (27.30, -81.05),
    "indian lake": (40.46, -83.72),
    "grand river": (42.97, -80.95),
    "lake hamilton": (34.48, -93.07),
    "tennessee river, knoxville, tn": (35.96, -83.93),
    "lake monroe": (39.14, -86.51),
    "lake patoka": (38.42, -86.68),
    "lake toho": (28.22, -81.38),
    "lake keowee": (34.80, -82.90),
    "ohio river maysville": (38.64, -83.74),
    "lake wateree": (34.36, -80.72),
    "ohio river tanner s creek": (39.10, -84.82),
    "ohio river golconda": (37.37, -88.49),
    "lake oahe, mobridge, sd": (45.54, -100.43),
    "saginaw bay, bay city, mi": (43.65, -83.89),
    "lacombe, la": (30.31, -89.94),
    "ohio river tanners creek": (39.10, -84.82),
    "lake cherokee": (32.38, -94.68),
    "lake roosevelt": (47.95, -118.95),
    "lake oahe, pierre, sd": (44.37, -100.35),
    "south holston reservoir": (36.52, -82.07),
    "south holston": (36.52, -82.07),
    "delaware river, philadelphia, pa": (39.95, -75.14),
    "chesapeake bay, cecil county, md": (39.53, -76.00),
    "cherryvale, ks": (37.27, -95.55),
    "saranac lake, ny": (44.33, -74.13),
    "oil city, la": (32.74, -92.15),
    "breaux bridge, la": (30.27, -91.90),
    "new roads, la": (30.69, -91.44),
    "hurricane, ut": (37.17, -113.29),
    "grove, ok": (36.59, -94.77),
    "ferriday, la": (31.63, -91.55),
    "tightwad, mo": (38.55, -93.57),
    "clark hill": (33.66, -82.20),
    "fort collins, co": (40.59, -105.08),
    "wolf river chain": (44.17, -88.87),
    "mosquito lake": (41.31, -80.76),
    "rocky point": (38.73, -84.85),
    "buggs island, mecklenburg county, va": (36.60, -78.35),
    "leech lake, walker, mn": (47.12, -94.54),
    "leesburg, fl": (28.81, -81.88),
    "lake oahe": (44.37, -100.35),
    "kissimmee, fl": (28.30, -81.42),
    "hayes, la": (30.10, -92.78),
    "camdenton, mo": (37.99, -92.74),
    "huntingtington beach, ca": (33.66, -118.00),
    "huntington beach, ca": (33.66, -118.00),
    "williamsport, md": (39.60, -77.82),
    "sturgeon bay, door county, wi": (44.84, -87.38),
    "wateree": (34.36, -80.72),
    "laurel river lake": (36.97, -84.27),
    "aransas bay, port aransas, tx": (27.83, -97.06),
    "watauga lake, carter county, tn": (36.32, -82.12),
    "upper chesapeake bay, north east, md": (39.60, -75.94),
    "brunswick, md": (39.31, -77.63),
    "fort madison": (40.63, -91.32),
    "zwolle, la": (31.63, -93.65),
    "niagara river, buffalo, ny": (42.88, -78.88),
    "fort loudoun tellico lakes": (35.72, -84.23),
    "ft loudoun tellico lakes": (35.72, -84.23),
    "1000 island": (44.30, -76.00),
    "muskegon": (43.23, -86.25),
    "laurel lake": (36.97, -84.27),
    "green bay": (44.51, -88.02),
    "devils lake": (48.11, -99.71),
    "shreveport, la": (32.53, -93.75),
    "sandusky": (41.45, -82.71),
    "akers, la": (30.95, -92.72),
    "lake mitchell": (32.90, -87.05),
    "la grange, tx": (29.91, -96.88),
    "clinton, mo": (38.37, -93.77),
    "tombigbee river, columbus, ms": (33.50, -88.43),
    "apalachicola bay, apalachicola, fl": (29.72, -84.98),
    "tims ford lake, winchester, tn": (35.15, -86.13),
    "lake winnebago, menasha, wi": (44.01, -88.43),
    "upper chesapeake bay, cecil county, md": (39.53, -76.00),
    "buggs island reservoir, clarksville, va": (36.60, -78.35),
    "lake chatuge, hiawassee, ga": (34.94, -83.79),
    "san diego, ca": (32.72, -117.16),
    "santee, sc": (33.48, -80.11),
    "lake travis, jonestown, tx": (30.49, -97.93),
    "pokegama lake, grand rapids, mn": (47.22, -93.55),
    "anacoco, la": (31.25, -93.35),
    "port aransas, tx": (27.83, -97.06),
    "wolf river chain of lakes": (44.17, -88.87),
    "roosevelt lake": (33.67, -111.16),
    "anderson, sc": (34.50, -82.65),
    "anderson , sc": (34.50, -82.65),
    "scottsboro, al": (34.67, -86.03),
    "scottsboro , al": (34.67, -86.03),
    "oakwood, ga": (34.23, -83.88),
    "alabama river, prattville, al": (32.46, -86.46),
    "atchafalaya basin, morgan city, la": (29.69, -91.21),
    "lake greenwood, greenwood, sc": (34.17, -82.13),
    "ohio river tell city": (37.95, -86.76),
    "badin lake": (35.40, -80.12),
    "lake mills, wi": (43.08, -88.91),
    "forsyth, mo": (36.69, -93.12),
    "tanners creek lawrenceburg": (39.10, -84.87),
    "leech lake": (47.12, -94.54),
    "tunica": (34.68, -90.38),
    "keowee": (34.80, -82.90),
    "ohio river carrollton": (38.68, -85.18),
    "bays de noc": (45.75, -87.00),
    "bays de noc, escanaba, mi": (45.75, -87.00),
    "burt mullett": (44.75, -84.55),
    "burt mullet": (44.75, -84.55),
    "burt mullett lakes": (44.75, -84.55),
    "patoka lake": (38.42, -86.68),
    "chalmette, la": (29.94, -89.96),
    "bay city, mi": (43.59, -83.89),
    "bainbridge, ga": (30.90, -84.58),
    "bainbridge , ga": (30.90, -84.58),
    "chatham, la": (32.30, -92.27),
    "many, la": (31.57, -93.47),
    "eufala, ga": (31.89, -85.15),
    "eufala, al": (31.89, -85.15),
    "lake eufaula": (31.93, -85.10),
    "parry sound, on": (45.35, -80.04),
    "carroll county 1000 acre recreational lake, huntingdon, tn": (35.99, -88.43),
    "port aransas, port aransas, tx": (27.83, -97.06),
    "saginaw bay, bangor charter township, mi": (43.68, -83.87),
    "sturgeon bay, wi": (44.84, -87.38),
    "lake of the pines": (32.78, -94.95),
    "chautauqua lake": (42.15, -79.38),
    "chautauqua": (42.15, -79.38),
    "la crosse, wi": (43.80, -91.24),
    "kissimmee": (28.30, -81.42),
    "westpoint": (32.88, -85.18),
    "millwood": (33.73, -94.07),
    "lake millwood": (33.73, -94.07),
    "millwood lake": (33.73, -94.07),
    "perryville, md": (39.56, -76.07),
    "waukesha, wi": (43.01, -88.23),
    "lake pleasant": (33.87, -112.27),
    "hamilton": (34.48, -93.07),
    "atchafalaya basin": (29.69, -91.21),
    "woodworth, la": (31.14, -92.50),
    "bismarck, ar": (34.32, -93.16),
    "shell knob, mo": (36.63, -93.63),
    "saginaw bay, mi": (43.65, -83.89),
    "saginaw bay": (43.65, -83.89),
    "saginaw": (43.65, -83.89),
    "muskego, wi": (42.91, -88.14),
    "winter haven, fl": (28.02, -81.73),
    "crown point, ny": (43.94, -73.44),
    "watertown, ny": (43.97, -75.91),
    "clewiston, fl": (26.75, -80.93),
    "rush city, mn": (45.69, -92.97),
    "falls of rough, ky": (37.56, -86.55),
    "chattanooga, tn": (35.05, -85.31),
    "clearlake, ca": (38.96, -122.63),
    "sneads, fl": (30.71, -84.92),
    "chipley, fl": (30.78, -85.54),
    "chipley , fl": (30.78, -85.54),
    "astor, deland, fl": (29.16, -81.53),
    "windsor, on": (42.30, -83.02),
    "buchanan, va": (37.53, -79.68),
    "burkesville, ky": (36.79, -85.37),
    "ticonderoga, ny": (43.85, -73.42),
    "hancock, md": (39.70, -78.18),
    "joaquin, tx": (31.96, -94.05),
    "fort hill, pa": (39.80, -78.90),
    "gardner, la": (30.97, -91.97),
    "fox lake, wi": (43.56, -88.90),
    "hemphill, tx": (31.34, -93.85),
    "hemphill, la": (31.34, -93.85),
    "viola, wv": (38.95, -81.08),
    "prairie du chien, wi": (43.05, -91.14),
    "saranac, ny": (44.73, -73.76),
    "fort scott, ks": (37.84, -94.71),
    "baton rouge, la": (30.45, -91.19),
    "plattsburgh, ny": (44.70, -73.45),
    "hot springs, ar": (34.50, -93.06),
    "hot springs national park, ar": (34.51, -93.05),
    "jp coleman state park": (34.82, -88.08),
    "columbus": (33.50, -88.43),
    "great sacandaga lake": (43.13, -74.10),
    "cross lake": (32.55, -93.80),
    "mt island lake": (35.35, -80.95),
    "illinois river": (35.98, -94.78),
    "miss river pool 3 lake st croix": (44.75, -92.80),
    "missouri river": (44.37, -100.35),
    "bayou black": (29.55, -90.75),
    "lake winneconne": (44.10, -88.73),
    "lake winnebago": (44.01, -88.43),
    "lake winnebago oshkosh wi": (44.01, -88.43),
    "nickajack lake": (35.03, -85.63),
    "carlyle lake": (38.63, -89.33),
    "st john s river": (29.16, -81.53),
    "bledsoe creek state park, gallatin, tn": (36.34, -86.40),
    "decateur, al": (34.61, -86.98),
    "wake forest, nc": (35.98, -78.51),
    "rogers, ar": (36.33, -94.12),
    "paris, tn": (36.30, -88.33),
    "st. stephen, sc": (33.40, -79.92),
    "lakeside marblehead, oh": (41.54, -82.73),
    "bude, ms": (31.46, -90.85),
    "bude , ms": (31.46, -90.85),
    "lorton, va": (38.70, -77.23),
    "gardner, wi": (44.59, -87.78),
    "gardner , wi": (44.59, -87.78),
    "gardner   , wi": (44.59, -87.78),
    "madison heights, va": (37.44, -79.12),
    "willis, tx": (30.42, -95.48),
    "lewes, de": (38.77, -75.14),
    "hopedale, la": (29.83, -89.67),
    "fort lyon, co": (38.00, -102.90),
    "la plata, md": (38.53, -76.97),
    "crystal springs, ar": (36.39, -93.62),
    "crystal springs , ar": (36.39, -93.62),
    "smithville, tn": (35.96, -85.81),
    "slidell, la": (30.28, -89.78),
    "del rio, tx": (29.36, -100.90),
    "belleville, ontario": (44.17, -77.38),
    "lake odessa, mi": (42.78, -85.14),
    "fayetteville, ar": (36.08, -94.17),
    "lindsay, ontario": (44.35, -78.74),
    "st. joseph, la": (31.92, -91.23),
    "st joseph, la": (31.92, -91.23),
    "madison, wi": (43.07, -89.40),
    "kelseyville, ca": (38.98, -122.84),
    "st. paul, mn": (44.94, -93.09),
    "fort smith, ar": (35.39, -94.40),
    "covington, la": (30.48, -90.10),
    "russell": (34.08, -82.62),
    "hudson river": (42.75, -73.69),
    "fort loudoun": (35.81, -84.22),
    "lake sharpe": (44.15, -99.47),
    "cass lake": (47.38, -94.60),
    "chickahominy river": (37.39, -77.05),
    "jonesville, la": (31.63, -91.82),
    "long beach, ca": (33.77, -118.19),
    "jasper, al": (33.83, -87.28),
    "jasper, tx": (30.92, -94.00),
    "counce, tn": (35.04, -88.28),
    "mooresville, nc": (35.59, -80.81),
    "lexington, nc": (35.82, -80.25),
    "apex, nc": (35.73, -78.85),
    "gilbert, sc": (33.92, -81.39),
    "moneta, va": (37.18, -79.65),
    "auburn, ny": (42.93, -76.57),
    "brewerton, ny": (43.24, -76.14),
    "lafayette, la": (30.22, -92.02),
    "hasty, co": (38.13, -102.82),
    "napanee, on": (44.25, -76.95),
    "gainesville, ga": (34.30, -83.82),
    "leesville, la": (31.14, -93.26),
    "gallatin, tn": (36.39, -86.45),
    "newellton, la": (32.07, -91.24),
    "appling, ga": (33.56, -82.32),
    "manning, sc": (33.70, -80.21),
    "pierre part, la": (29.96, -91.20),
    "mandeville, la": (30.36, -90.07),
    "alabama river": (32.46, -86.46),
    "ft gibson": (35.80, -95.25),
    "ft gibson lake": (35.80, -95.25),
    "toho": (28.22, -81.38),
    "lake travis": (30.49, -97.93),
    "cherokee": (32.38, -94.68),
    "lake demopolis": (32.52, -87.88),
    "dumas": (33.88, -91.49),
    "pine bluff": (34.23, -91.99),
    "ark river pine bluff": (34.23, -91.99),
    "okee tannie": (26.97, -80.80),
    "pascagoula river": (30.37, -88.56),
    "patoka": (38.42, -86.68),
    "tanners creek": (39.10, -84.87),
    "florence, al": (34.80, -87.68),
    "yantis, tx": (32.93, -95.58),
    "deville, la": (31.36, -92.17),
    "east park lake ramp, nd": (48.11, -99.71),
    "harrison township, mi": (42.59, -82.83),
    "chanhassen, mn": (44.86, -93.53),
    "newport beach, ca": (33.62, -117.93),
    "grand st marys": (40.54, -84.52),
    "grand": (40.54, -84.52),
    "bolivar, mo": (37.61, -93.41),
    "branson, mo": (36.64, -93.22),
    "donalsonville, ga": (31.04, -84.88),
    "bastrop, la": (32.78, -91.91),
    "lecompte, la": (31.09, -92.40),
    "columbia, sc": (34.00, -81.04),
    "lake toho": (28.22, -81.38),  # alias
    "monroe": (32.51, -92.12),
    "lake pleasant  ": (33.87, -112.27),
    "forrest wood cup": (35.96, -83.93),  # typically Knoxville area
    "quachita": (34.48, -93.06),  # Lake Ouachita
    "lake ten killer": (35.63, -94.97),
    "lake belton": (31.10, -97.48),
    "lake kerr": (29.34, -81.76),
    "lake mohave": (35.20, -114.57),
    "lake minnetonka": (44.93, -93.57),
    "lake winneconne wolf river chain": (44.10, -88.73),
    "leech lake walker mn": (47.12, -94.54),
    "walleye championship bismarck nd": (46.81, -100.78),
}

# ── Fuzzy matching helpers ──────────────────────────────────────────────────

def clean_location(loc: str) -> str:
    """Normalize location string for matching."""
    if not isinstance(loc, str):
        return ""
    # Remove HTML/whitespace junk
    loc = re.sub(r'[\n\r\t]+', ' ', loc)
    loc = re.sub(r'\s+', ' ', loc).strip()
    # Remove leading numbers
    loc = re.sub(r'^\d+\s*', '', loc)
    return loc.strip()

def normalize_for_match(loc: str) -> str:
    """Further normalize for dictionary matching."""
    loc = clean_location(loc).lower()
    # Remove common suffixes/prefixes
    loc = re.sub(r'\s+(regional|championship|rescheduled|invitational).*$', '', loc)
    loc = re.sub(r'^(lake\s+|river\s+)', '', loc)
    loc = loc.strip()
    return loc


def try_match_known(location: str) -> tuple:
    """Try to match a location string against the known dictionary."""
    cleaned = clean_location(location).lower().strip()

    # Direct match
    if cleaned in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[cleaned]

    # Try without trailing whitespace variations
    cleaned2 = re.sub(r'\s+', ' ', cleaned).strip()
    if cleaned2 in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[cleaned2]

    # Try extracting just the core lake/river name
    # "LAKE FERGUSON\n\nWASHINGTON" -> "lake ferguson"
    parts = re.split(r'[,\n\r\t]+', cleaned)
    core = parts[0].strip()
    if core in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[core]

    # Try "Lake X" -> look up "lake x"
    # Try "X Lake" -> look up "lake x"
    for prefix in ['lake ', 'river ']:
        if core.startswith(prefix):
            without = core[len(prefix):].strip()
            # Try both forms
            if f"{prefix}{without}" in KNOWN_LOCATIONS:
                return KNOWN_LOCATIONS[f"{prefix}{without}"]
            if without in KNOWN_LOCATIONS:
                return KNOWN_LOCATIONS[without]

    # Try adding "lake" prefix
    if f"lake {core}" in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[f"lake {core}"]

    return None


def nominatim_geocode(location: str) -> tuple:
    """Use Nominatim (OpenStreetMap) to geocode a location. Free, no API key needed."""
    cleaned = clean_location(location)
    if not cleaned or len(cleaned) < 3:
        return None

    # Add fishing context to help disambiguation
    queries = [
        f"{cleaned}, USA",  # Most tournaments are in the US
        cleaned,
    ]

    for query in queries:
        try:
            params = urllib.parse.urlencode({
                'q': query,
                'format': 'json',
                'limit': 1,
                'countrycodes': 'us,ca',  # US and Canada
            })
            url = f"https://nominatim.openstreetmap.org/search?{params}"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'CASTLINE-Fishing-App/1.0 (research@castline.app)',
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if data:
                    lat = float(data[0]['lat'])
                    lon = float(data[0]['lon'])
                    return (lat, lon)
        except Exception as e:
            pass
        time.sleep(1.1)  # Nominatim rate limit: 1 req/sec

    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', default='/workspace/castline')
    args = parser.parse_args()

    raw_dir = Path(args.workspace) / 'raw'

    # Load all tournament files
    all_dfs = []
    for fname in ['all_bassmaster_outcomes.csv', 'elite_outcomes.csv', 'flw_outcomes.csv',
                   'combined_all_outcomes_v2.csv', 'mlf_outcomes.csv']:
        path = raw_dir / fname
        if path.exists():
            df = pd.read_csv(path)
            all_dfs.append(df)
            print(f"Loaded {fname}: {len(df)} rows")

    combined = pd.concat(all_dfs, ignore_index=True).drop_duplicates(subset='event_id')
    print(f"\nTotal unique events: {len(combined)}")

    # Load existing geocoded file
    geocoded_path = raw_dir / 'tournament_events_geocoded.csv'
    if geocoded_path.exists():
        geocoded = pd.read_csv(geocoded_path)
        geo_ids = set(geocoded['event_id'].values)
        print(f"Already geocoded: {len(geo_ids)}")
    else:
        geocoded = pd.DataFrame()
        geo_ids = set()

    # Find missing events
    missing = combined[~combined['event_id'].isin(geo_ids)].copy()
    print(f"Missing geocoding: {len(missing)}")

    if len(missing) == 0:
        print("All events already geocoded!")
        return

    # Phase 1: Try known locations dictionary
    matched_known = 0
    matched_nominatim = 0
    failed = 0
    results = []

    unique_locs = missing['location'].unique()
    print(f"\nUnique missing locations: {len(unique_locs)}")

    # Build a cache: location -> (lat, lon) or None
    loc_cache = {}

    print("\n── Phase 1: Known locations dictionary ──")
    for loc in unique_locs:
        coords = try_match_known(loc)
        if coords:
            loc_cache[loc] = coords
            matched_known += 1

    print(f"Known dictionary matches: {matched_known}/{len(unique_locs)}")

    # Phase 2: Nominatim for remaining
    remaining_locs = [loc for loc in unique_locs if loc not in loc_cache]
    print(f"\n── Phase 2: Nominatim geocoding for {len(remaining_locs)} remaining locations ──")

    # Filter out junk locations
    skip_patterns = [
        r'^(southeastern|northern|western|central|southern)\s+(conference\s+)?championship',
        r'^(national|regional)\s+championship',
        r'^(forrest wood cup|flw|bfl|icast|chevy|walmart|wal\s*mart)',
        r'^(tour|walleye tour)\s+championship',
        r'^championship$',
        r'^(chevy trucks )?wild card$',
        r'^jacobs cup$',
        r'^tbf national',
        r'^2010 bfl',
        r'^bfl (all american|chevy)',
    ]

    for i, loc in enumerate(remaining_locs):
        cleaned = clean_location(loc).lower()

        # Skip obvious non-geographic entries
        skip = False
        for pat in skip_patterns:
            if re.search(pat, cleaned, re.IGNORECASE):
                skip = True
                break

        if skip or len(cleaned) < 3:
            loc_cache[loc] = None
            continue

        coords = nominatim_geocode(loc)
        if coords:
            loc_cache[loc] = coords
            matched_nominatim += 1
            print(f"  [{i+1}/{len(remaining_locs)}] ✓ {clean_location(loc)} -> ({coords[0]:.4f}, {coords[1]:.4f})")
        else:
            loc_cache[loc] = None
            failed += 1
            print(f"  [{i+1}/{len(remaining_locs)}] ✗ {clean_location(loc)}")

    # Apply geocoding to missing events
    new_rows = []
    for _, row in missing.iterrows():
        coords = loc_cache.get(row['location'])
        if coords:
            row_dict = row.to_dict()
            row_dict['lat'] = round(coords[0], 4)
            row_dict['lon'] = round(coords[1], 4)
            new_rows.append(row_dict)

    print(f"\n── Results ──")
    print(f"Known dictionary: {matched_known} locations matched")
    print(f"Nominatim: {matched_nominatim} locations matched")
    print(f"Failed: {failed} locations")
    print(f"New events geocoded: {len(new_rows)}")

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        # Ensure same columns as geocoded
        if len(geocoded) > 0:
            for col in geocoded.columns:
                if col not in new_df.columns:
                    new_df[col] = None
            new_df = new_df[geocoded.columns]

        updated = pd.concat([geocoded, new_df], ignore_index=True)
        updated = updated.drop_duplicates(subset='event_id')
        updated.to_csv(geocoded_path, index=False)
        print(f"\nUpdated geocoded file: {len(updated)} total events (was {len(geocoded)})")
        print(f"Events with lat/lon: {updated['lat'].notna().sum()}")
    else:
        print("\nNo new events to add.")


if __name__ == '__main__':
    main()
