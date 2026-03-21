#!/usr/bin/env python3
"""Geocode missing locations in cpue_v15_location_stats.json using Nominatim."""

import json
import os
import re
import time
import sys

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

STATS_PATH = os.path.join(os.path.dirname(__file__), "..", "castline", "models", "cpue_v15_location_stats.json")
CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), "..", "castline", "models", "geocode_checkpoint.json")

# US state abbreviation to full name
STATE_MAP = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Canadian provinces
PROVINCE_MAP = {
    "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia", "AB": "Alberta",
    "MB": "Manitoba", "SK": "Saskatchewan", "NB": "New Brunswick",
    "NS": "Nova Scotia", "PE": "Prince Edward Island", "NL": "Newfoundland",
}


def expand_state(abbr):
    """Convert state abbreviation to full name."""
    abbr = abbr.strip()
    if abbr in STATE_MAP:
        return STATE_MAP[abbr]
    if abbr in PROVINCE_MAP:
        return PROVINCE_MAP[abbr]
    # Already a full name
    return abbr


def build_queries(location_name):
    """Build a list of search queries to try, best first."""
    queries = []
    name = location_name.strip()

    # Parse pattern: "Name, City, ST" or "Name, ST" or "Name, State" or "Name"
    parts = [p.strip() for p in name.split(",")]

    if len(parts) >= 2:
        last = parts[-1].strip()
        state_full = expand_state(last)
        is_us = last in STATE_MAP or state_full in STATE_MAP.values()
        is_ca = last in PROVINCE_MAP or state_full in PROVINCE_MAP.values()
        country = "USA" if is_us else ("Canada" if is_ca else "")

        if len(parts) == 3:
            # "Lake Name, City, ST"
            lake = parts[0]
            city = parts[1]
            # Try: lake + state + country
            queries.append(f"{lake}, {state_full}, {country}".strip(", "))
            # Try: lake + city + state + country
            queries.append(f"{lake}, {city}, {state_full}, {country}".strip(", "))
            # Try: just lake name + country
            queries.append(f"{lake}, {country}".strip(", "))
        elif len(parts) == 2:
            # "Lake Name, ST" or "Lake Name, State"
            lake = parts[0]
            queries.append(f"{lake}, {state_full}, {country}".strip(", "))
            # Also try the raw name
            queries.append(f"{lake}, {country}".strip(", "))
        else:
            # 4+ parts, just try as-is with expanded state
            expanded = ", ".join(parts[:-1]) + f", {state_full}"
            if country:
                expanded += f", {country}"
            queries.append(expanded)
    else:
        # Single part, no comma
        queries.append(f"{name}, USA")
        queries.append(name)

    # Deduplicate while preserving order
    seen = set()
    result = []
    for q in queries:
        q = q.strip(", ")
        if q and q not in seen:
            seen.add(q)
            result.append(q)
    return result


def geocode_location(geolocator, location_name):
    """Try to geocode a location, returns (lat, lon) or None."""
    queries = build_queries(location_name)

    for query in queries:
        try:
            result = geolocator.geocode(query, timeout=10, exactly_one=True,
                                         country_codes=["us", "ca"])
            if result:
                return (round(result.latitude, 4), round(result.longitude, 4))
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            print(f"    Error for '{query}': {e}")
            time.sleep(2)  # Extra delay on error
        except Exception as e:
            print(f"    Unexpected error for '{query}': {e}")

        time.sleep(1.0)  # Rate limit between attempts

    return None


def main():
    # Load stats
    with open(STATS_PATH) as f:
        stats = json.load(f)

    # Load checkpoint if exists
    checkpoint = {}
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH) as f:
            checkpoint = json.load(f)
        print(f"Loaded checkpoint with {len(checkpoint)} previously geocoded entries")

    # Find locations missing coords
    missing = [k for k, v in stats.items() if v.get("lat") is None or v.get("lon") is None]
    print(f"Total locations: {len(stats)}")
    print(f"Missing coordinates: {len(missing)}")
    print(f"Already in checkpoint: {len(checkpoint)}")

    # Filter out ones already in checkpoint
    to_geocode = [k for k in missing if k not in checkpoint]
    print(f"Remaining to geocode: {len(to_geocode)}")

    if not to_geocode:
        print("Nothing to geocode. Applying checkpoint to stats...")
        _apply_and_save(stats, checkpoint)
        return

    geolocator = Nominatim(user_agent="CASTLINE/1.0 (fishing research project)")

    success = 0
    failed = 0
    start_time = time.time()

    for i, loc in enumerate(to_geocode):
        result = geocode_location(geolocator, loc)

        if result:
            checkpoint[loc] = {"lat": result[0], "lon": result[1]}
            success += 1
        else:
            checkpoint[loc] = {"lat": None, "lon": None}
            failed += 1

        # Progress
        done = i + 1
        if done % 50 == 0 or done == len(to_geocode):
            elapsed = time.time() - start_time
            rate = done / elapsed if elapsed > 0 else 0
            eta_min = (len(to_geocode) - done) / rate / 60 if rate > 0 else 0
            pct = (success / done) * 100
            print(f"[{done}/{len(to_geocode)}] success={success} failed={failed} "
                  f"({pct:.0f}% hit) ETA={eta_min:.1f}min", flush=True)
            # Save checkpoint
            with open(CHECKPOINT_PATH, "w") as f:
                json.dump(checkpoint, f)

        time.sleep(1.0)  # Nominatim rate limit

    # Final save
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(checkpoint, f)

    print(f"\nGeocoding complete: {success} found, {failed} failed out of {len(to_geocode)}")
    _apply_and_save(stats, checkpoint)


def _apply_and_save(stats, checkpoint):
    """Apply checkpoint results to stats and save."""
    applied = 0
    for loc, coords in checkpoint.items():
        if loc in stats and coords.get("lat") is not None:
            stats[loc]["lat"] = coords["lat"]
            stats[loc]["lon"] = coords["lon"]
            applied += 1

    total_with_coords = sum(1 for v in stats.values() if v.get("lat") is not None)
    print(f"Applied {applied} new coordinates")
    print(f"Total with coordinates: {total_with_coords}/{len(stats)} ({total_with_coords/len(stats)*100:.1f}%)")

    with open(STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Saved updated stats to {STATS_PATH}")


if __name__ == "__main__":
    main()
