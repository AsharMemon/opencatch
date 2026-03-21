#!/usr/bin/env python3
"""
Fetch USACE reservoir data from the Corps Water Management System (CWMS) Data API.
===================================================================================
For each tournament location, searches the CWMS catalog for nearby reservoirs
and fetches time series data for pool elevation, tailwater, inflow, and outflow.

Key parameters:
  - Elev-Pool: pool elevation (ft)
  - Elev-Tailwater: tailwater elevation (ft)
  - Flow-In: inflow (cfs)
  - Flow-Out: outflow/release (cfs)
  - Stor: storage (acre-ft)

CWMS Data API (CDA): https://cwms-data.usace.army.mil/cwms-data
Endpoints:
  /catalog/TIMESERIES  -- list available time series
  /timeseries          -- fetch time series values

Usage:
    python scripts/fetch_usace_reservoir.py
    python scripts/fetch_usace_reservoir.py --resume
"""

import argparse
import json
import math
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "castline" / "validation" / "data" / "raw"
ASSEMBLED_DIR = BASE_DIR / "castline" / "validation" / "data" / "assembled"

DATASET = ASSEMBLED_DIR / "validation_dataset_v16.csv"
OUTPUT_CSV = RAW_DIR / "usace_reservoir_data.csv"
CHECKPOINT_CSV = RAW_DIR / "usace_reservoir_data.checkpoint.csv"
MATCH_CACHE = RAW_DIR / "usace_reservoir_match_cache.json"

# ---------------------------------------------------------------------------
# CWMS API
# ---------------------------------------------------------------------------
CDA_BASE = "https://cwms-data.usace.army.mil/cwms-data"

# USACE district offices to search
OFFICES = [
    "LRL", "LRN", "LRH", "LRP", "LRE", "LRB",  # Great Lakes & Ohio River
    "NWP", "NWS", "NWK", "NWO", "NWW",            # Northwestern
    "SWL", "SWT", "SWF", "SWG",                    # Southwestern
    "MVS", "MVR", "MVP", "MVN", "MVK",             # Mississippi Valley
    "SAJ", "SAM", "SAS", "SAW", "SAC",             # South Atlantic
    "NAB", "NAE", "NAN", "NAP",                    # North Atlantic
    "SPK", "SPN", "SPL",                           # South Pacific
    "POA", "POH",                                  # Pacific Ocean
    "SPA",                                         # extra
]

# Time series parameter patterns we want
TS_PATTERNS = {
    "Elev-Pool":     "pool_elev_ft",
    "Elev-Tailwater": "tailwater_elev_ft",
    "Flow-In":       "inflow_cfs",
    "Flow-Out":      "outflow_cfs",
    "Stor":          "storage_acft",
}

RATE_LIMIT = 0.5
CHECKPOINT_INTERVAL = 50

session = requests.Session()
session.headers.update({
    "User-Agent": "CastlineResearch/1.0 (fishing conditions research)",
    "Accept": "application/json;version=2",
})


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    """Haversine distance in km between two lat/lon points."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def safe_request(url, params=None, retries=3, timeout=30):
    """Make a GET request with retries and rate limiting."""
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp
            elif resp.status_code == 404:
                return None
            elif resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            elif resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            else:
                return None
        except requests.exceptions.Timeout:
            time.sleep(2 * (attempt + 1))
            continue
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return None
    return None


# ---------------------------------------------------------------------------
# CWMS CATALOG: discover reservoirs
# ---------------------------------------------------------------------------
def search_catalog_for_location(location_name, lat, lon, radius_km=50):
    """Search CWMS catalog for time series near a location.

    Strategy:
    1. Extract lake/reservoir name keywords from the location string
    2. Search catalog by text match across offices
    3. Return matched reservoir info with time series IDs
    """
    # Extract search keywords from location name
    keywords = extract_reservoir_keywords(location_name)
    if not keywords:
        return None

    matches = []
    for keyword in keywords:
        result = _catalog_text_search(keyword)
        if result:
            matches.extend(result)

    if not matches:
        return None

    # Deduplicate by project name
    seen = set()
    unique = []
    for m in matches:
        proj = m.get("project", "")
        if proj and proj not in seen:
            seen.add(proj)
            unique.append(m)

    return unique if unique else None


def extract_reservoir_keywords(location_name):
    """Extract plausible reservoir/lake name keywords from a tournament location string.

    Examples:
        'Lake Guntersville, AL' -> ['Guntersville']
        'Sam Rayburn Reservoir, TX' -> ['Rayburn', 'Sam Rayburn']
        'Kentucky Lake, KY' -> ['Kentucky']
        'Table Rock Lake, MO' -> ['Table Rock']
    """
    if not location_name:
        return []

    # Remove state suffix
    name = location_name.split(",")[0].strip()

    # Common prefixes/suffixes to strip
    remove_words = {"lake", "reservoir", "river", "creek", "pond", "bay",
                    "the", "of", "on", "at", "near", "upper", "lower"}

    words = name.split()
    # Try the full name minus generic words
    cleaned = [w for w in words if w.lower() not in remove_words]

    keywords = []
    if cleaned:
        # Full cleaned name
        full = " ".join(cleaned)
        if len(full) >= 3:
            keywords.append(full)
        # If multi-word, also try last significant word (often the proper noun)
        if len(cleaned) > 1:
            keywords.append(cleaned[-1])
    else:
        # All words were generic, try first non-generic word from original
        for w in words:
            if w.lower() not in {"lake", "the", "of", "on", "at"}:
                keywords.append(w)
                break

    return keywords[:3]  # limit search attempts


def _catalog_text_search(keyword):
    """Search CWMS catalog for time series matching a keyword."""
    url = f"{CDA_BASE}/catalog/TIMESERIES"
    params = {
        "like": f".*(?i){keyword}.*",
        "page-size": 500,
    }

    resp = safe_request(url, params=params)
    if resp is None:
        return []

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return []

    entries = data.get("entries", [])
    if not entries:
        return []

    # Group by project and filter to parameters we care about
    projects = {}
    for entry in entries:
        ts_id = entry.get("name", "")
        office = entry.get("office-id", "")

        # Parse time series ID: Project.Param.Type.Interval.Duration.Version
        parts = ts_id.split(".")
        if len(parts) < 3:
            continue

        project = parts[0]
        param = parts[1]

        # Check if this is a parameter we want
        matched_param = None
        for pattern in TS_PATTERNS:
            if pattern in param:
                matched_param = pattern
                break

        if not matched_param:
            continue

        if project not in projects:
            projects[project] = {
                "project": project,
                "office": office,
                "timeseries": {},
            }

        # Prefer daily (1Day) or hourly (1Hour) intervals
        interval = parts[3] if len(parts) > 3 else ""
        existing = projects[project]["timeseries"].get(matched_param, "")
        # Prefer 1Day for our purposes
        if not existing or "1Day" in ts_id:
            projects[project]["timeseries"][matched_param] = ts_id

    return list(projects.values())


def match_location_to_reservoir(location_name, lat, lon, match_cache):
    """Try to match a tournament location to a USACE reservoir.

    Returns dict with project info and time series IDs, or None.
    """
    cache_key = f"{lat:.4f},{lon:.4f}"
    if cache_key in match_cache:
        return match_cache[cache_key]

    matches = search_catalog_for_location(location_name, lat, lon)
    if not matches:
        match_cache[cache_key] = None
        return None

    # Score matches: prefer those with more parameters available
    best = None
    best_score = -1
    for m in matches:
        score = len(m.get("timeseries", {}))
        # Bonus for having pool elevation (most important feature)
        if "Elev-Pool" in m.get("timeseries", {}):
            score += 5
        if score > best_score:
            best_score = score
            best = m

    match_cache[cache_key] = best
    return best


# ---------------------------------------------------------------------------
# FETCH TIME SERIES DATA
# ---------------------------------------------------------------------------
def fetch_timeseries(ts_id, office, start_date, end_date):
    """Fetch time series values from CWMS Data API.

    Returns dict of {date_str: value} or empty dict.
    """
    url = f"{CDA_BASE}/timeseries"
    params = {
        "name": ts_id,
        "office": office,
        "begin": start_date + "T00:00:00Z",
        "end": end_date + "T23:59:59Z",
        "units": "EN",  # English units (ft, cfs)
        "page-size": 5000,
    }

    resp = safe_request(url, params=params)
    if resp is None:
        return {}

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return {}

    values = {}
    # CDA returns values as array of [timestamp_ms, value, quality]
    raw_values = data.get("values", [])
    for entry in raw_values:
        if not entry or len(entry) < 2:
            continue
        ts_ms = entry[0]
        val = entry[1]
        if val is None:
            continue
        try:
            val = float(val)
        except (ValueError, TypeError):
            continue
        # Convert timestamp to date string
        dt = datetime.utcfromtimestamp(ts_ms / 1000.0)
        date_str = dt.strftime("%Y-%m-%d")
        values[date_str] = val

    return values


def fetch_reservoir_data(reservoir_match, start_date, end_date):
    """Fetch all available time series for a reservoir match.

    Returns dict of {param_name: {date_str: value}}.
    """
    if not reservoir_match:
        return {}

    office = reservoir_match.get("office", "")
    ts_map = reservoir_match.get("timeseries", {})
    result = {}

    for param_pattern, ts_id in ts_map.items():
        col_name = TS_PATTERNS.get(param_pattern, param_pattern)
        values = fetch_timeseries(ts_id, office, start_date, end_date)
        if values:
            result[col_name] = values
        time.sleep(RATE_LIMIT)

    return result


# ---------------------------------------------------------------------------
# FEATURE COMPUTATION
# ---------------------------------------------------------------------------
def compute_reservoir_features(reservoir_data, event_date):
    """Compute derived reservoir features for a single event date.

    Args:
        reservoir_data: dict of {param_name: {date_str: value}}
        event_date: datetime date of the event

    Returns:
        dict of feature_name -> value (NaN for missing)
    """
    features = {}
    date_str = event_date.strftime("%Y-%m-%d")

    # Helper to get value for a date offset
    def get_val(param, days_back=0):
        d = (event_date - timedelta(days=days_back)).strftime("%Y-%m-%d")
        vals = reservoir_data.get(param, {})
        return vals.get(d, np.nan)

    def get_vals_range(param, days_back):
        """Get all available values in a range of days back."""
        vals = []
        param_data = reservoir_data.get(param, {})
        for i in range(days_back + 1):
            d = (event_date - timedelta(days=i)).strftime("%Y-%m-%d")
            v = param_data.get(d, np.nan)
            if not np.isnan(v):
                vals.append(v)
        return vals

    # --- Pool elevation features ---
    pool_now = get_val("pool_elev_ft", 0)
    pool_1d = get_val("pool_elev_ft", 1)
    pool_3d = get_val("pool_elev_ft", 3)
    pool_7d = get_val("pool_elev_ft", 7)
    pool_14d = get_val("pool_elev_ft", 14)

    features["pool_elevation_ft"] = pool_now
    features["pool_change_1d_ft"] = pool_now - pool_1d if not (np.isnan(pool_now) or np.isnan(pool_1d)) else np.nan
    features["pool_change_3d_ft"] = pool_now - pool_3d if not (np.isnan(pool_now) or np.isnan(pool_3d)) else np.nan
    features["pool_change_7d_ft"] = pool_now - pool_7d if not (np.isnan(pool_now) or np.isnan(pool_7d)) else np.nan
    features["pool_change_14d_ft"] = pool_now - pool_14d if not (np.isnan(pool_now) or np.isnan(pool_14d)) else np.nan

    # Rising/falling pool
    if not np.isnan(features.get("pool_change_1d_ft", np.nan)):
        features["is_rising_pool"] = 1 if features["pool_change_1d_ft"] > 0.01 else 0
        features["is_falling_pool"] = 1 if features["pool_change_1d_ft"] < -0.01 else 0
    else:
        features["is_rising_pool"] = np.nan
        features["is_falling_pool"] = np.nan

    # Pool stability (std dev over 7 days)
    pool_7d_vals = get_vals_range("pool_elev_ft", 7)
    features["pool_stability_7d"] = np.std(pool_7d_vals) if len(pool_7d_vals) >= 3 else np.nan

    # --- Tailwater features ---
    features["tailwater_elevation_ft"] = get_val("tailwater_elev_ft", 0)
    tw_now = get_val("tailwater_elev_ft", 0)
    tw_1d = get_val("tailwater_elev_ft", 1)
    features["tailwater_change_1d_ft"] = tw_now - tw_1d if not (np.isnan(tw_now) or np.isnan(tw_1d)) else np.nan

    # --- Inflow features ---
    inflow_now = get_val("inflow_cfs", 0)
    inflow_3d = get_val("inflow_cfs", 3)
    features["inflow_cfs"] = inflow_now
    if not (np.isnan(inflow_now) or np.isnan(inflow_3d)) and inflow_3d > 0:
        features["inflow_change_3d_pct"] = ((inflow_now - inflow_3d) / inflow_3d) * 100
    else:
        features["inflow_change_3d_pct"] = np.nan

    # --- Outflow features ---
    outflow_now = get_val("outflow_cfs", 0)
    outflow_3d = get_val("outflow_cfs", 3)
    features["outflow_cfs"] = outflow_now
    if not (np.isnan(outflow_now) or np.isnan(outflow_3d)) and outflow_3d > 0:
        features["outflow_change_3d_pct"] = ((outflow_now - outflow_3d) / outflow_3d) * 100
    else:
        features["outflow_change_3d_pct"] = np.nan

    # --- Storage features ---
    features["storage_acft"] = get_val("storage_acft", 0)

    # --- Flow ratio (inflow/outflow balance) ---
    if not (np.isnan(inflow_now) or np.isnan(outflow_now)) and outflow_now > 0:
        features["inflow_outflow_ratio"] = inflow_now / outflow_now
    else:
        features["inflow_outflow_ratio"] = np.nan

    return features


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Fetch USACE reservoir data for tournament events")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    args = parser.parse_args()

    print("=" * 60)
    print("USACE Reservoir Data Fetcher (CWMS Data API)")
    print("=" * 60)

    # Load dataset
    if not DATASET.exists():
        print(f"ERROR: Dataset not found at {DATASET}")
        sys.exit(1)

    df = pd.read_csv(DATASET, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna() & df["lat"].notna() & df["lon"].notna()].copy()
    print(f"Events: {len(df)}, Locations: {df.location.nunique()}")

    # Get unique locations
    locations = df.groupby("location").agg(
        lat=("lat", "first"),
        lon=("lon", "first"),
        n_events=("date", "count"),
        min_date=("date", "min"),
        max_date=("date", "max"),
    ).reset_index()
    print(f"Unique locations: {len(locations)}")

    # Load match cache
    match_cache = {}
    if MATCH_CACHE.exists():
        try:
            with open(MATCH_CACHE) as f:
                match_cache = json.load(f)
            print(f"Match cache loaded: {len(match_cache)} entries")
        except (json.JSONDecodeError, IOError):
            pass

    # Load checkpoint
    existing_results = []
    processed_keys = set()
    if args.resume and CHECKPOINT_CSV.exists():
        try:
            ckpt = pd.read_csv(CHECKPOINT_CSV)
            existing_results = ckpt.to_dict("records")
            processed_keys = set(zip(ckpt["location"], ckpt["date"]))
            print(f"Checkpoint loaded: {len(existing_results)} rows, "
                  f"{len(processed_keys)} events already processed")
        except Exception as e:
            print(f"Warning: Could not load checkpoint: {e}")

    # ---------------------------------------------------------------------------
    # Phase 1: Match locations to USACE reservoirs
    # ---------------------------------------------------------------------------
    print(f"\n--- Phase 1: Match locations to USACE reservoirs ---")
    matched = 0
    unmatched = 0

    for i, row in locations.iterrows():
        cache_key = f"{row.lat:.4f},{row.lon:.4f}"
        if cache_key in match_cache:
            if match_cache[cache_key] is not None:
                matched += 1
            else:
                unmatched += 1
            continue

        result = match_location_to_reservoir(row.location, row.lat, row.lon, match_cache)
        if result:
            matched += 1
            print(f"  MATCH: {row.location} -> {result['project']} "
                  f"({len(result.get('timeseries', {}))} params)")
        else:
            unmatched += 1

        time.sleep(RATE_LIMIT)

        if (i + 1) % 25 == 0:
            print(f"  Progress: {i+1}/{len(locations)} "
                  f"({matched} matched, {unmatched} no match)")
            # Save match cache periodically
            _save_match_cache(match_cache)

    _save_match_cache(match_cache)
    print(f"\nMatching complete: {matched} matched, {unmatched} unmatched "
          f"out of {len(locations)} locations")

    # ---------------------------------------------------------------------------
    # Phase 2: Fetch time series data for matched locations
    # ---------------------------------------------------------------------------
    print(f"\n--- Phase 2: Fetch reservoir time series data ---")

    results = list(existing_results)
    events_processed = len(processed_keys)
    events_with_data = sum(1 for r in results if not all(
        np.isnan(v) if isinstance(v, float) else False
        for k, v in r.items() if k not in ("location", "date", "usace_project", "usace_office")
    ))

    # Process each event
    total_events = len(df)
    reservoir_data_cache = {}  # project -> {param -> {date -> val}}

    for idx, event in df.iterrows():
        loc = event["location"]
        date = event["date"]
        date_str = date.strftime("%Y-%m-%d")

        # Skip already processed
        if (loc, date_str) in processed_keys:
            continue

        # Find reservoir match
        cache_key = f"{event.lat:.4f},{event.lon:.4f}"
        reservoir = match_cache.get(cache_key)

        row = {
            "location": loc,
            "date": date_str,
            "usace_project": "",
            "usace_office": "",
        }
        # Initialize all feature columns to NaN
        for col in ["pool_elevation_ft", "pool_change_1d_ft", "pool_change_3d_ft",
                     "pool_change_7d_ft", "pool_change_14d_ft", "inflow_cfs",
                     "outflow_cfs", "inflow_change_3d_pct", "outflow_change_3d_pct",
                     "is_rising_pool", "is_falling_pool", "pool_stability_7d",
                     "tailwater_elevation_ft", "tailwater_change_1d_ft",
                     "storage_acft", "inflow_outflow_ratio"]:
            row[col] = np.nan

        if reservoir is not None:
            project = reservoir["project"]
            office = reservoir["office"]
            row["usace_project"] = project
            row["usace_office"] = office

            # Fetch data if not cached for this project
            if project not in reservoir_data_cache:
                # Need data from 14 days before earliest event to latest event for this location
                start = (date - timedelta(days=21)).strftime("%Y-%m-%d")
                end = (date + timedelta(days=1)).strftime("%Y-%m-%d")
                rdata = fetch_reservoir_data(reservoir, start, end)
                reservoir_data_cache[project] = rdata
            else:
                rdata = reservoir_data_cache[project]
                # Check if we need to extend the date range
                needed_start = (date - timedelta(days=21)).strftime("%Y-%m-%d")
                # Fetch additional data if needed (check if our date is covered)
                has_coverage = False
                for param_data in rdata.values():
                    if date_str in param_data or (date - timedelta(days=1)).strftime("%Y-%m-%d") in param_data:
                        has_coverage = True
                        break
                if not has_coverage and rdata:
                    # Re-fetch with broader range
                    end = (date + timedelta(days=1)).strftime("%Y-%m-%d")
                    new_data = fetch_reservoir_data(reservoir, needed_start, end)
                    # Merge into cache
                    for param, vals in new_data.items():
                        if param in rdata:
                            rdata[param].update(vals)
                        else:
                            rdata[param] = vals

            # Compute features
            features = compute_reservoir_features(rdata, date)
            row.update(features)

            if not np.isnan(features.get("pool_elevation_ft", np.nan)):
                events_with_data += 1

        results.append(row)
        processed_keys.add((loc, date_str))
        events_processed += 1

        # Progress and checkpointing
        if events_processed % CHECKPOINT_INTERVAL == 0:
            pct = events_processed / total_events * 100
            print(f"  {events_processed}/{total_events} ({pct:.1f}%), "
                  f"{events_with_data} with reservoir data")
            _save_checkpoint(results)

    # ---------------------------------------------------------------------------
    # Save final output
    # ---------------------------------------------------------------------------
    print(f"\n--- Saving results ---")
    if results:
        out_df = pd.DataFrame(results)
        out_df.to_csv(OUTPUT_CSV, index=False)
        print(f"Saved {len(out_df)} rows to {OUTPUT_CSV}")

        # Coverage report
        print(f"\nCoverage report:")
        for col in ["pool_elevation_ft", "pool_change_1d_ft", "pool_change_7d_ft",
                     "inflow_cfs", "outflow_cfs", "tailwater_elevation_ft",
                     "pool_stability_7d", "inflow_outflow_ratio"]:
            if col in out_df.columns:
                n = out_df[col].notna().sum()
                pct = n / len(out_df) * 100
                print(f"  {col}: {n}/{len(out_df)} ({pct:.1f}%)")

        # Clean up checkpoint
        if CHECKPOINT_CSV.exists():
            CHECKPOINT_CSV.unlink()
            print("Checkpoint file removed.")
    else:
        print("No results to save.")

    print("\nDone!")


def _save_match_cache(match_cache):
    """Save reservoir match cache to disk."""
    try:
        with open(MATCH_CACHE, "w") as f:
            json.dump(match_cache, f, indent=2, default=str)
    except IOError as e:
        print(f"Warning: Could not save match cache: {e}")


def _save_checkpoint(results):
    """Save intermediate results to checkpoint file."""
    try:
        pd.DataFrame(results).to_csv(CHECKPOINT_CSV, index=False)
    except IOError as e:
        print(f"Warning: Could not save checkpoint: {e}")


if __name__ == "__main__":
    main()
