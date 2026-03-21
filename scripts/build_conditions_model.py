#!/usr/bin/env python3
"""
CASTLINE Conditions Model — Day-Level Weather Effects on Fishing

Uses FLW tournament data with exact dates to learn how day-level weather
conditions affect fishing success. The key output is a "conditions effect"
that tells us how much BETTER or WORSE a day is compared to a location's
historical average.

Flow:
  1. Geocode FLW locations via geocode cache + v16 fuzzy match + manual map
  2. Load pre-fetched NOAA weather data (with lag features already computed)
  3. Engineer weather/conditions features
  4. Train CatBoost + XGBoost ensemble with GroupKFold spatial CV
  5. Save model for production inference

Usage:
    python scripts/build_conditions_model.py
"""

import os
import sys
import json
import time
import math
import pickle
import warnings
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score, mean_absolute_error

warnings.filterwarnings("ignore")

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT = Path("/Users/Ashar/Documents/fish")
FLW_PATH = PROJECT / "castline/validation/data/raw/flw_outcomes.csv"
GEOCODE_CACHE = PROJECT / "castline/validation/data/raw/geocode_cache.json"
V16_PATH = PROJECT / "castline/validation/data/assembled/validation_dataset_v16.csv"
NOAA_WEATHER_PATH = PROJECT / "castline/validation/data/raw/flw_weather_noaa.csv"
RESULTS_PATH = PROJECT / "castline/validation/data/conditions_model_results.json"
MODEL_DIR = PROJECT / "castline/validation/data/models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ── Manual geocoding for common FLW lakes ────────────────────────────────────
# These are well-known tournament lakes whose short names don't match
# the geocode cache or v16 dataset.
MANUAL_COORDS = {
    "Kerr": (36.60, -78.35),           # Kerr Lake / Buggs Island, VA/NC
    "Kerr Lake": (36.60, -78.35),
    "Gaston": (36.52, -77.90),         # Lake Gaston, VA/NC
    "Lake Gaston": (36.52, -77.90),
    "Smith Mountain": (37.05, -79.55), # Smith Mountain Lake, VA
    "Quachita": (34.55, -93.20),       # Lake Ouachita, AR
    "Ouachita": (34.55, -93.20),
    "Grand St Marys": (40.54, -84.48), # Grand Lake St. Marys, OH
    "Grand Lake St Mary S": (40.54, -84.48),
    "Grand Lake St Marys": (40.54, -84.48),
    "Millwood": (33.73, -93.95),       # Millwood Lake, AR
    "Lake Millwood": (33.73, -93.95),
    "St Clair": (42.45, -82.75),       # Lake St. Clair, MI
    "Lake St Clair": (42.45, -82.75),
    "Westpoint": (32.89, -85.18),      # West Point Lake, GA/AL
    "West Point": (32.89, -85.18),
    "West Point Lake": (32.89, -85.18),
    "Dumas": (33.88, -91.49),          # Dumas, AR (Arkansas River)
    "Sandusky": (41.45, -82.71),       # Sandusky Bay, OH
    "Pine Bluff": (34.23, -92.00),     # Pine Bluff, AR
    "Ark River Pine Bluff": (34.23, -92.00),
    "Lake Santee Cooper": (33.50, -80.10),
    "Santee Cooper": (33.50, -80.10),
    "Tunica": (34.68, -90.38),
    "Ft Gibson Lake": (35.90, -95.25),
    "Fort Gibson Lake": (35.90, -95.25),
    "Beaver Lake": (36.35, -93.85),
    "Okee Tannie": (26.95, -80.80),    # Okeechobee + Istokpoga
    "Pascagoula River": (30.70, -88.60),
    "Lake Palestine": (32.10, -95.85),
    "Lake Neely Henry": (33.85, -86.05),
    "Weiss Lake": (34.15, -85.80),
    "Ohio River Rocky Point": (38.80, -84.85),
    "Columbus Pool": (32.50, -88.40),
    "Guntersville Lake": (34.37, -86.30),
    "Wateree": (34.35, -80.70),
    "Lake Wateree": (34.35, -80.70),
    "Lake Sinclair": (33.15, -83.25),
    "Lake Truman": (38.25, -93.45),
    "Harry S Truman": (38.25, -93.45),
    "Lake Ferguson": (33.40, -91.05),
    "Rocky Point": (38.80, -84.85),
    "Stockton Lake": (37.60, -93.75),
    "High Rock": (35.60, -80.20),      # High Rock Lake, NC
    "High Rock Lake": (35.60, -80.20),
    "Tanners Creek": (38.95, -84.85),
    "Ohio River": (38.80, -84.85),
    "Clark Hill": (33.65, -82.20),      # Clarks Hill / Thurmond, GA/SC
    "Clarks Hill": (33.65, -82.20),
    "Lake Wheeler": (34.65, -87.05),
    "Lake Oconee": (33.55, -83.25),
    "Old Hickory Lake": (36.30, -86.45),
    "Lake Livingston": (30.75, -95.05),
    "Lake Demopolis": (32.50, -87.85),
    "Greers Ferry": (35.50, -91.80),
    "Greers Ferry Lake": (35.50, -91.80),
    "Chickamauga": (35.10, -85.10),
    "Chickamauga Lake": (35.10, -85.10),
    "Toho": (28.20, -81.35),           # Lake Tohopekaliga, FL
    "Lake Toho": (28.20, -81.35),
    "Kissimmee Chain": (28.20, -81.35),
    "Sam Rayburn": (31.10, -94.10),
    "Sam Rayburn Reservoir": (31.10, -94.10),
    "Pickwick": (34.90, -88.25),
    "Pickwick Lake": (34.90, -88.25),
    "Lake Of The Ozarks": (38.15, -92.65),
    "Lake Ozarks": (38.15, -92.65),
    "Grand Lake": (36.70, -94.85),     # Grand Lake O' the Cherokees, OK
    "Lanier": (34.20, -83.95),         # Lake Lanier, GA
    "Lake Lanier": (34.20, -83.95),
    "Eufaula": (35.30, -95.35),        # Lake Eufaula, OK
    "Lake Eufaula": (35.30, -95.35),
    "Norman": (33.20, -87.40),         # Lake Norman? or Norman, OK area
    "Lake Norman": (35.45, -80.95),
    "Logan Martin": (33.55, -86.30),
    "Logan Martin Lake": (33.55, -86.30),
    "Wilson Lake": (34.75, -87.60),
    "Champlain": (44.50, -73.30),
    "Lake Champlain": (44.50, -73.30),
    "Falcon": (26.55, -99.15),         # Falcon Lake, TX
    "Falcon Lake": (26.55, -99.15),
    "Amistad": (29.45, -101.05),
    "Lake Amistad": (29.45, -101.05),
    "Table Rock": (36.58, -93.35),
    "Table Rock Lake": (36.58, -93.35),
    "Ross Barnett": (32.42, -89.95),
    "Ross Barnett Reservoir": (32.42, -89.95),
    "Lake Murray": (34.05, -81.25),
    "Murray": (34.05, -81.25),
    "Dale Hollow": (36.55, -85.45),
    "Dale Hollow Lake": (36.55, -85.45),
    "Kentucky Lake": (36.60, -88.05),
    "Hartwell": (34.35, -82.85),
    "Lake Hartwell": (34.35, -82.85),
    "Seminole": (30.80, -84.85),
    "Lake Seminole": (30.80, -84.85),
    "Dardanelle": (35.30, -93.15),
    "Lake Dardanelle": (35.30, -93.15),
    "Lewis Smith": (34.10, -87.10),
    "Smith Lake": (34.10, -87.10),
    "Lake Wylie": (35.10, -81.05),
    "Wylie": (35.10, -81.05),
    "Lay Lake": (33.10, -86.50),
    "Lake Texoma": (33.85, -96.60),
    "Texoma": (33.85, -96.60),
    "Bull Shoals": (36.40, -92.55),
    "Bull Shoals Lake": (36.40, -92.55),
    "Watts Bar": (35.65, -84.75),
    "Watts Bar Lake": (35.65, -84.75),
    "Lake Of The Pines": (32.75, -94.65),
    "Cherokee Lake": (36.15, -83.40),
    "Douglas Lake": (35.95, -83.35),
    "Norris Lake": (36.30, -84.05),
    "Center Hill Lake": (36.10, -85.80),
    "Percy Priest": (36.10, -86.55),
    "J Percy Priest": (36.10, -86.55),
    "Lake Cumberland": (36.85, -85.10),
    "Cumberland": (36.85, -85.10),
    "Barkley": (36.80, -88.05),
    "Lake Barkley": (36.80, -88.05),
    "Lake Fork": (32.85, -95.55),
    "Fork": (32.85, -95.55),
    "Cedar Creek": (32.15, -96.10),
    "Cedar Creek Lake": (32.15, -96.10),
    "Cayuga Lake": (42.70, -76.70),
    "Oneida Lake": (43.20, -75.95),
    "St Lawrence River": (44.35, -75.90),
    "1000 Islands": (44.35, -75.90),
    "Potomac": (38.90, -77.05),
    "Potomac River": (38.90, -77.05),
    "James River": (37.50, -79.45),
    "Red River": (33.75, -93.90),
    "Arkansas River": (35.30, -93.15),
    "Detroit River": (42.30, -83.10),
    "Muskegon": (43.25, -86.25),
    "Saginaw": (43.40, -83.95),
    "Saginaw Bay": (43.80, -83.80),
    "Grand River": (43.05, -85.70),
    "Grand": (43.05, -85.70),
    "Wal Mart Open": None,             # Generic event, skip
    "Championship": None,              # Generic event, skip
    "TBD": None,
    "Canceled": None,
    "Lake Erie": (41.50, -82.60),
    "Erie": (41.50, -82.60),
    "Lake Michigan": (43.60, -87.00),
    "Lake Ontario": (43.60, -77.50),
    "Lake Huron": (44.75, -83.40),
    "Oneida": (43.20, -75.95),
    "Cayuga": (42.70, -76.70),
    "Seneca": (42.65, -76.90),
    "Okeechobee": (26.95, -80.80),
    "Lake Okeechobee": (26.95, -80.80),
    "Guntersville": (34.37, -86.30),
    "Lake Guntersville": (34.37, -86.30),
    "Wheeler": (34.65, -87.05),
    "Toledo Bend": (31.35, -93.60),
    "Toledo Bend Reservoir": (31.35, -93.60),
    "Tenkiller": (35.65, -94.95),
    "Lake Tenkiller": (35.65, -94.95),
    "Lake Havasu": (34.50, -114.35),
    "Havasu": (34.50, -114.35),
    "Clear Lake": (39.05, -122.75),
    "Lake Shasta": (40.75, -122.35),
    "Shasta": (40.75, -122.35),
    "Delta": (38.05, -121.75),         # CA Delta
    "California Delta": (38.05, -121.75),
    "Mead": (36.15, -114.75),
    "Lake Mead": (36.15, -114.75),
    "Smith Mountain Lake": (37.05, -79.55),
    "Kentucky Barkley Lakes": (36.70, -88.05),
    "Kentucky Barkley Lake": (36.70, -88.05),
    "Ky Barkley Lakes": (36.70, -88.05),
    "Ky Barkley Lake": (36.70, -88.05),
    "Lake Shelbyville": (39.40, -88.80),
    "Mississippi River La Crosse": (43.80, -91.25),
    "Mississippi River Lacrosse": (43.80, -91.25),
    "Mississippi River Prairie Du Chien": (43.05, -91.15),
    "Lake Hamilton": (34.50, -93.10),   # Hot Springs, AR
    "Hamilton": (34.50, -93.10),
    "Kissimmee River": (27.50, -81.05),
    "Indian Lake": (40.65, -83.70),     # Indian Lake, OH
    "Lake Chickamauga": (35.10, -85.10),
    "Lake Patoka": (38.40, -86.65),     # Patoka Lake, IN
    "Patoka": (38.40, -86.65),
    "Ohio River Maysville": (38.65, -83.75),
    "Ohio River Tanner S Creek": (38.95, -84.85),
    "Ohio River Tanners Creek": (38.95, -84.85),
    "Ohio River Golconda": (37.35, -88.50),
    "Lake Roosevelt": (47.90, -118.40), # Lake Roosevelt, WA
    "Roosevelt": (47.90, -118.40),
    "Lake Cherokee": (36.55, -82.60),   # Cherokee Lake, TN
    "Jordan Lake": (35.70, -79.05),
    "Percy Priest Lake": (36.10, -86.55),
    "Kentucky Lake Paris Landing": (36.30, -88.05),
    "Watts Bar Lake": (35.65, -84.75),
    "Forrest Wood Cup": None,           # Championship event, skip
    "Chevy Trucks Wild Card": None,
    "Jacobs Cup": None,
    "Flw Tour Championship": None,
    "Flw Championship Boat Outdoor Show": None,
    "Chautauqua": (42.15, -79.45),      # Chautauqua Lake, NY
    "Ouachita River": (34.20, -92.65),
    "Ft Gibson": (35.90, -95.25),
    "Arkansas River Dardanelle": (35.30, -93.15),
    "Chevy Open Potomac River": (38.90, -77.05),
    "Fort Loudoun Tellico Lakes": (35.65, -84.25),
    "Fort Loudoun": (35.80, -84.25),
    "Tellico Lake": (35.55, -84.25),
    "Truman": (38.25, -93.45),
    "Truman Lake": (38.25, -93.45),
    "Hudson River": (42.15, -73.90),
    "Patoka Lake": (38.40, -86.65),
    "Shelbyville Lake": (39.40, -88.80),
    "Lake Shelbyville": (39.40, -88.80),
    "Chickahominy River": (37.40, -77.15),
    "Ohio River Carrollton": (38.68, -85.18),
    "Ky Barkley Lake Regional": (36.70, -88.05),
    "Lake Pleasant": (33.85, -112.25), # Lake Pleasant, AZ
    "Mississippi River Red Wing": (44.55, -92.55),
    "Watts Barr": (35.65, -84.75),
    "Wal Mart Open Beaver Lake": (36.35, -93.85),
    "Grand Grand Lake St Mary S": (40.54, -84.48),
    "Chevy Wild Card": None,
    "Tour Championship": None,
    "Burt Mullet": None,
    "Burt Mullett": None,
    "Walleye Tour Championship": None,
    "Fort Madison": (40.63, -91.32),    # Mississippi River at Fort Madison, IA
    "Walmart Bass Fishing League Ohio River": (38.80, -84.85),
    "Stren Championship": None,
    "Stren Series Championship": None,
    "Powell": (37.05, -111.30),
    "Lake Powell": (37.05, -111.30),
    "Lake Travis": (30.40, -97.90),
    "Travis": (30.40, -97.90),
    "Conroe": (30.40, -95.55),
    "Lake Conroe": (30.40, -95.55),
    "Lake Travis": (30.40, -97.90),
    "Ray Roberts": (33.35, -97.05),
    "Ray Roberts Lake": (33.35, -97.05),
    "Lewisville": (33.05, -96.95),
    "Lake Lewisville": (33.05, -96.95),
    "Richland Chambers": (31.95, -96.10),
    "Lake Ouachita": (34.55, -93.20),
    "DeGray": (34.25, -93.10),
    "DeGray Lake": (34.25, -93.10),
    "Norfork": (36.25, -92.25),
    "Norfork Lake": (36.25, -92.25),
    "Winyah Bay": (33.35, -79.25),
    "Santee": (33.50, -80.10),
    "Clarks Hill Lake": (33.65, -82.20),
    "Thurmond": (33.65, -82.20),
    "Lake Thurmond": (33.65, -82.20),
    "Richard B Russell": (34.05, -82.60),
    "Russell": (34.05, -82.60),
    "Keowee": (34.80, -82.90),
    "Lake Keowee": (34.80, -82.90),
    "Jocassee": (35.00, -82.95),
    "Lake Jocassee": (35.00, -82.95),
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. GEOCODE FLW LOCATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def geocode_flw_locations(flw: pd.DataFrame) -> pd.DataFrame:
    """Match FLW location names to lat/lon coordinates."""
    print("\n=== GEOCODING FLW LOCATIONS ===")

    # Clean messy location names (some have embedded newlines, HTML artifacts)
    flw["location"] = flw["location"].str.replace(r"\s*\n\s*", " ", regex=True)
    flw["location"] = flw["location"].str.replace(r"\s{2,}", " ", regex=True)
    flw["location"] = flw["location"].str.strip()
    # Drop events with clearly non-location names (LAKE \n 04 etc.)
    flw = flw[~flw["location"].str.match(r"^LAKE\s*\d*$", na=False)]
    flw = flw[~flw["location"].str.match(r"^River\s", na=False)]  # "River Cowboy Gator" etc.

    # Load geocode cache
    with open(GEOCODE_CACHE) as f:
        gc = json.load(f)

    # Load v16 location map
    v16 = pd.read_csv(V16_PATH, usecols=["location", "lat", "lon"])
    v16_map = v16[v16["lat"].notna()].groupby("location")[["lat", "lon"]].first()

    # Build a lookup combining all sources
    # Priority: manual > geocode_cache exact > v16 fuzzy > geocode_cache fuzzy
    gc_lower = {}
    for k, v in gc.items():
        gc_lower[k.lower().strip()] = (v["lat"], v["lon"])

    v16_lower = {}
    for loc, row in v16_map.iterrows():
        v16_lower[loc.lower().strip()] = (row["lat"], row["lon"])

    unique_locs = flw["location"].dropna().unique()
    loc_coords = {}
    match_source = {}

    for loc in unique_locs:
        ll = loc.lower().strip()

        # 1) Manual mapping
        if loc in MANUAL_COORDS:
            coords = MANUAL_COORDS[loc]
            if coords is None:
                match_source[loc] = "skip"
                continue
            loc_coords[loc] = coords
            match_source[loc] = "manual"
            continue

        # 2) Exact geocode cache match
        if ll in gc_lower:
            loc_coords[loc] = gc_lower[ll]
            match_source[loc] = "geocache_exact"
            continue

        # 3) Fuzzy: "Lake X" or "X Lake" in geocode cache keys
        found = False
        lake_variants = [f"lake {ll}", f"{ll} lake", f"{ll} reservoir"]
        for variant in lake_variants:
            if variant in gc_lower:
                loc_coords[loc] = gc_lower[variant]
                match_source[loc] = "geocache_variant"
                found = True
                break

        if found:
            continue

        # 4) Substring match in geocode cache (loc name at start of key)
        for k, v in gc_lower.items():
            k_base = k.split(",")[0].strip()
            if ll == k_base or f"lake {ll}" == k_base or f"{ll} lake" == k_base:
                loc_coords[loc] = v
                match_source[loc] = "geocache_fuzzy"
                found = True
                break
        if found:
            continue

        # 5) Substring match in v16
        for k, v in v16_lower.items():
            k_base = k.split(",")[0].strip()
            if ll in k_base or f"lake {ll}" in k_base:
                loc_coords[loc] = v
                match_source[loc] = "v16_fuzzy"
                found = True
                break
        if found:
            continue

        match_source[loc] = "unmatched"

    # Summary
    sources = pd.Series(match_source)
    print(f"  Total unique locations: {len(unique_locs)}")
    print(f"  Matched: {len(loc_coords)}")
    print(f"  By source: {sources.value_counts().to_dict()}")

    unmatched = [l for l, s in match_source.items() if s == "unmatched"]
    if unmatched:
        print(f"  Unmatched ({len(unmatched)}): {unmatched[:20]}")

    # Apply to dataframe
    flw["lat"] = flw["location"].map(lambda x: loc_coords.get(x, (None, None))[0] if pd.notna(x) else None)
    flw["lon"] = flw["location"].map(lambda x: loc_coords.get(x, (None, None))[1] if pd.notna(x) else None)

    before = len(flw)
    flw = flw.dropna(subset=["lat", "lon"])
    print(f"  Events with coords: {len(flw)}/{before}")

    return flw


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LOAD PRE-FETCHED NOAA WEATHER DATA
# ═══════════════════════════════════════════════════════════════════════════════


def load_noaa_weather(flw: pd.DataFrame) -> pd.DataFrame:
    """Load pre-fetched NOAA weather and match to FLW events by event_id.

    Falls back to lat/lon proximity (0.1 deg) + exact date match if event_id
    is missing from the NOAA file.
    """
    print("\n=== LOADING NOAA WEATHER DATA ===")

    noaa = pd.read_csv(NOAA_WEATHER_PATH)
    print(f"  Loaded NOAA weather: {len(noaa)} rows")

    # Check how many have actual weather data (non-null temp_mean)
    has_weather = noaa["temp_mean"].notna().sum()
    print(f"  Rows with weather data: {has_weather}/{len(noaa)}")

    if "event_id" in noaa.columns and noaa["event_id"].notna().any():
        # Direct join by event_id
        flw_events = set(flw["event_id"].unique())
        noaa_events = set(noaa["event_id"].dropna().unique())
        matched_ids = flw_events & noaa_events
        print(f"  Matched by event_id: {len(matched_ids)}/{len(flw_events)} FLW events")

        merged = flw.merge(
            noaa.drop(columns=["lat", "lon"], errors="ignore"),
            on="event_id",
            how="left",
            suffixes=("", "_noaa"),
        )
        # If date columns collide, keep the FLW date
        if "date_noaa" in merged.columns:
            merged.drop(columns=["date_noaa"], inplace=True)
    else:
        # Fallback: match by lat/lon proximity + exact date
        print("  No event_id in NOAA file — matching by lat/lon/date proximity")
        noaa["date"] = pd.to_datetime(noaa["date"]).dt.strftime("%Y-%m-%d")
        flw["_date_str"] = pd.to_datetime(flw["date"]).dt.strftime("%Y-%m-%d")

        matched_rows = []
        for _, frow in flw.iterrows():
            candidates = noaa[
                (noaa["date"] == frow["_date_str"])
                & ((noaa["lat"] - frow["lat"]).abs() < 0.1)
                & ((noaa["lon"] - frow["lon"]).abs() < 0.1)
            ]
            if len(candidates) > 0:
                best = candidates.iloc[0]
                matched_rows.append(best)
            else:
                matched_rows.append(pd.Series(dtype=float))

        weather_df = pd.DataFrame(matched_rows).reset_index(drop=True)
        # Drop columns that would collide
        drop_cols = [c for c in ["lat", "lon", "date", "event_id"] if c in weather_df.columns]
        weather_df.drop(columns=drop_cols, inplace=True, errors="ignore")
        merged = pd.concat([flw.reset_index(drop=True), weather_df], axis=1)
        if "_date_str" in merged.columns:
            merged.drop(columns=["_date_str"], inplace=True)

    weather_hit = merged["temp_mean"].notna().sum()
    print(f"  FLW events with NOAA weather: {weather_hit}/{len(merged)}")
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ENGINEER DAY-LEVEL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def compute_moon_phase(date: datetime) -> float:
    """Moon phase as 0-1 (0=new, 0.5=full, 1=new again). Simple synodic approx."""
    # Known new moon: Jan 6, 2000
    ref = datetime(2000, 1, 6)
    days = (date - ref).days
    cycle = 29.530588853  # synodic month
    phase = (days % cycle) / cycle
    return phase


def compute_day_length(lat: float, doy: int) -> float:
    """Approximate day length in hours using the CBM model."""
    # Declination angle
    decl = 23.45 * math.sin(math.radians(360 / 365 * (doy - 81)))
    decl_rad = math.radians(decl)
    lat_rad = math.radians(lat)

    # Hour angle
    cos_ha = -math.tan(lat_rad) * math.tan(decl_rad)
    cos_ha = max(-1, min(1, cos_ha))  # clamp
    ha = math.degrees(math.acos(cos_ha))

    return 2 * ha / 15  # hours


def engineer_features(flw: pd.DataFrame) -> pd.DataFrame:
    """Create day-level conditions features from NOAA weather data.

    The NOAA CSV already has lag features (temp_mean_lag1, pressure_hpa_lag1, etc.)
    and pressure deltas (pressure_delta_1d/2d/3d) and front_phase, so we use those
    directly instead of pivoting by offset.
    """
    print("\n=== ENGINEERING FEATURES ===")

    has_weather = flw["temp_mean"].notna().sum()
    if has_weather == 0:
        print("  WARNING: No weather data available after merge.")
        return flw

    print(f"  Events with NOAA weather: {has_weather}/{len(flw)}")

    # ── Event-day weather features (rename to w_ prefix for model) ──
    flw["w_temp_max"] = flw["temp_max"]
    flw["w_temp_min"] = flw["temp_min"]
    flw["w_temp_mean"] = flw["temp_mean"]
    flw["w_precip_mm"] = flw["precip_mm"]
    flw["w_wind_max_kph"] = flw["wind_max_kph"]
    flw["w_wind_avg_kph"] = flw["wind_avg_kph"]
    flw["w_pressure_hpa"] = flw["pressure_hpa"]
    flw["w_humidity_pct"] = flw["humidity_pct"]
    flw["w_cloud_cover"] = flw["cloud_cover_okta"]

    # ── Pressure change features (CRITICAL) — from NOAA pre-computed ──
    flw["pressure_change_1d"] = flw["pressure_delta_1d"]
    flw["pressure_change_2d"] = flw["pressure_delta_2d"]
    flw["pressure_change_3d"] = flw["pressure_delta_3d"]

    # Frontal indicators from NOAA front_phase column
    flw["prefrontal"] = (flw["front_phase"] == "pre_frontal").astype(float)
    flw["postfrontal"] = (flw["front_phase"] == "post_frontal").astype(float)

    # Pressure volatility over 3 days
    pressure_vals = pd.DataFrame({
        "p0": flw["pressure_hpa"],
        "p1": flw["pressure_hpa_lag1"],
        "p2": flw["pressure_hpa_lag2"],
        "p3": flw["pressure_hpa_lag3"],
    })
    flw["pressure_stability_3d"] = pressure_vals.std(axis=1)

    # ── Temperature features ──
    flw["temp_delta_1d"] = flw["temp_mean"] - flw["temp_mean_lag1"]
    flw["temp_delta_2d"] = flw["temp_mean"] - flw["temp_mean_lag2"]
    flw["temp_range_today"] = flw["temp_max"] - flw["temp_min"]

    # Temperature trend over 3 days (positive = warming)
    flw["temp_trend_3d"] = (flw["temp_mean"] - flw["temp_mean_lag3"]) / 3

    # Bass thermal comfort (optimal air temp ~18-24C / 64-75F)
    flw["bass_thermal_comfort"] = 1.0 - np.minimum(
        np.abs(flw["temp_mean"] - 21) / 10, 1.0
    )

    # Spawn proximity (air temp 15.5-18.3C / 60-65F)
    spawn_center = 16.9
    flw["spawn_proximity"] = np.exp(-0.5 * ((flw["temp_mean"] - spawn_center) / 3) ** 2)

    # ── Wind features ──
    flw["wind_comfort"] = np.clip(1.0 - flw["wind_max_kph"] / 40, 0, 1)

    # ── Rain features ──
    flw["rain_today"] = flw["precip_mm"]
    flw["rain_yesterday"] = flw["precip_mm_lag1"]
    flw["rain_3d_total"] = (
        flw["precip_mm"].fillna(0) +
        flw["precip_mm_lag1"].fillna(0) +
        flw["precip_mm_lag2"].fillna(0)
    )

    # Consecutive dry days before event
    flw["dry_days_before"] = (
        (flw["precip_mm_lag1"].fillna(0) < 1).astype(int) +
        (flw["precip_mm_lag2"].fillna(0) < 1).astype(int) +
        (flw["precip_mm_lag3"].fillna(0) < 1).astype(int)
    )

    # ── Cloud / humidity features (NOAA has these instead of solar/weathercode) ──
    # Rain indicator from precip
    flw["is_rain"] = (flw["precip_mm"].fillna(0) > 1.0).astype(float)
    # Heavy rain as storm proxy
    flw["is_storm"] = (flw["precip_mm"].fillna(0) > 10.0).astype(float)

    # ── Moon / solunar features ──
    def extract_date_from_eid(eid):
        parts = str(eid).split("-")
        try:
            return datetime(int(parts[1]), int(parts[2]), int(parts[3]))
        except (IndexError, ValueError):
            return None

    event_dates_raw = [extract_date_from_eid(eid) for eid in flw["event_id"]]

    # Convert to safe lists to avoid NaT issues
    moon_phases = []
    doy_list = []
    month_list = []
    for d in event_dates_raw:
        if d is not None:
            moon_phases.append(compute_moon_phase(d))
            doy_list.append(d.timetuple().tm_yday)
            month_list.append(d.month)
        else:
            moon_phases.append(np.nan)
            doy_list.append(np.nan)
            month_list.append(np.nan)

    flw["moon_phase"] = moon_phases
    flw["moon_phase_sin"] = np.sin(2 * np.pi * flw["moon_phase"])
    flw["moon_phase_cos"] = np.cos(2 * np.pi * flw["moon_phase"])
    flw["moon_illumination"] = 0.5 * (1 - np.cos(2 * np.pi * flw["moon_phase"]))

    # Day of year and day length
    flw["day_of_year"] = doy_list
    flw["day_length"] = [
        compute_day_length(lat, doy) if pd.notna(doy) else np.nan
        for lat, doy in zip(flw["lat"], flw["day_of_year"])
    ]

    # Season encoding
    flw["season_sin"] = np.sin(2 * np.pi * flw["day_of_year"] / 365)
    flw["season_cos"] = np.cos(2 * np.pi * flw["day_of_year"] / 365)

    # Month
    flw["month"] = month_list

    # Solunar quality (simplified: best near new/full moon)
    flw["solunar_quality"] = np.cos(4 * np.pi * flw["moon_phase"]) * 0.5 + 0.5

    # Growing degree days (cumulative temp above 10C over past 3 days)
    flw["gdd_3d"] = (
        np.maximum(flw["temp_mean"].fillna(0) - 10, 0) +
        np.maximum(flw["temp_mean_lag1"].fillna(0) - 10, 0) +
        np.maximum(flw["temp_mean_lag2"].fillna(0) - 10, 0)
    )

    # Stability index: how stable were conditions over past 3 days
    temp_vals = pd.DataFrame({
        "t0": flw["temp_mean"],
        "t1": flw["temp_mean_lag1"],
        "t2": flw["temp_mean_lag2"],
        "t3": flw["temp_mean_lag3"],
    })
    flw["conditions_stability"] = 1.0 / (1.0 + temp_vals.std(axis=1) + flw["pressure_stability_3d"].fillna(0))

    # ── Location features ──
    flw["is_river"] = flw["location"].str.lower().str.contains(
        r"river|creek|bayou|channel|canal|delta|run", na=False
    ).astype(float)

    # Multi-day flag
    if "day_number" in flw.columns:
        flw["is_multiday"] = (flw["day_number"] > 1).astype(float)
    else:
        flw["is_multiday"] = 0.0

    # Season category
    month_to_season = {12: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 1,
                       6: 2, 7: 2, 8: 2, 9: 3, 10: 3, 11: 3}
    flw["season"] = flw["month"].map(month_to_season)

    print(f"  Total features engineered: {len([c for c in flw.columns if c.startswith('w_') or c in FEATURE_COLS])}")
    print(f"  Events with weather: {flw['w_temp_mean'].notna().sum()}/{len(flw)}")

    return flw


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MODEL TRAINING
# ═══════════════════════════════════════════════════════════════════════════════

# Feature columns for modeling
WEATHER_FEATURES = [
    "w_temp_max", "w_temp_min", "w_temp_mean", "w_precip_mm",
    "w_wind_max_kph", "w_wind_avg_kph", "w_pressure_hpa",
    "w_humidity_pct", "w_cloud_cover",
    "pressure_change_1d", "pressure_change_2d", "pressure_change_3d",
    "prefrontal", "postfrontal", "pressure_stability_3d",
    "temp_delta_1d", "temp_delta_2d", "temp_range_today", "temp_trend_3d",
    "bass_thermal_comfort", "spawn_proximity",
    "wind_comfort",
    "rain_today", "rain_yesterday", "rain_3d_total", "dry_days_before",
    "is_storm", "is_rain",
    "moon_phase_sin", "moon_phase_cos", "moon_illumination",
    "solunar_quality",
    "gdd_3d", "conditions_stability",
]

LOCATION_FEATURES = [
    "lat", "lon", "is_river", "is_multiday",
    "month", "season_sin", "season_cos",
    "day_of_year", "day_length", "season",
    "baseline_signal",
]

FEATURE_COLS = WEATHER_FEATURES + LOCATION_FEATURES


def train_conditions_model(df: pd.DataFrame):
    """Train CatBoost + XGBoost ensemble with spatial GroupKFold CV."""
    print("\n=== TRAINING CONDITIONS MODEL ===")

    # Filter to events with weather data
    df = df.dropna(subset=["w_temp_mean", "median_weight_lb"])
    df = df[df["median_weight_lb"] > 0]

    print(f"  Training samples: {len(df)}")
    print(f"  Unique locations: {df['location'].nunique()}")

    # Target: log(median_weight_lb)
    y = np.log1p(df["median_weight_lb"].values)

    # Features
    X = df[FEATURE_COLS].copy()

    # Fill NaN with median for numeric cols
    for col in X.columns:
        if X[col].dtype in [np.float64, np.float32, np.int64]:
            X[col] = X[col].fillna(X[col].median())

    # Groups for spatial CV (by location)
    groups = df["location"].values

    # ── GroupKFold spatial CV ──
    n_splits = 5
    gkf = GroupKFold(n_splits=n_splits)

    cb_oof = np.full(len(y), np.nan)
    xgb_oof = np.full(len(y), np.nan)
    ens_oof = np.full(len(y), np.nan)

    cb_models = []
    xgb_models = []

    feature_imp_cb = np.zeros(len(FEATURE_COLS))
    feature_imp_xgb = np.zeros(len(FEATURE_COLS))

    try:
        from catboost import CatBoostRegressor
        has_catboost = True
    except ImportError:
        print("  WARNING: CatBoost not installed, using XGBoost only")
        has_catboost = False

    try:
        from xgboost import XGBRegressor
        has_xgboost = True
    except ImportError:
        print("  WARNING: XGBoost not installed")
        has_xgboost = False

    if not has_catboost and not has_xgboost:
        print("  ERROR: Need at least CatBoost or XGBoost")
        return None

    print(f"\n  Running {n_splits}-fold GroupKFold CV...")

    for fold, (train_idx, val_idx) in enumerate(gkf.split(X, y, groups)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        print(f"\n  Fold {fold+1}: train={len(train_idx)}, val={len(val_idx)}, "
              f"val_locations={len(set(groups[val_idx]))}")

        # CatBoost
        if has_catboost:
            cb = CatBoostRegressor(
                iterations=800,
                depth=6,
                learning_rate=0.05,
                l2_leaf_reg=5,
                random_seed=42 + fold,
                verbose=0,
                early_stopping_rounds=50,
            )
            cb.fit(X_train, y_train, eval_set=(X_val, y_val), verbose=0)
            cb_pred = cb.predict(X_val)
            cb_oof[val_idx] = cb_pred
            cb_models.append(cb)
            feature_imp_cb += cb.get_feature_importance() / n_splits

            r2_cb = r2_score(y_val, cb_pred)
            mae_cb = mean_absolute_error(np.expm1(y_val), np.expm1(cb_pred))
            print(f"    CatBoost  R²={r2_cb:.4f}, MAE={mae_cb:.3f} lb")

        # XGBoost
        if has_xgboost:
            xgb = XGBRegressor(
                n_estimators=800,
                max_depth=6,
                learning_rate=0.05,
                reg_lambda=5,
                random_state=42 + fold,
                verbosity=0,
                early_stopping_rounds=50,
            )
            xgb.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
            xgb_pred = xgb.predict(X_val)
            xgb_oof[val_idx] = xgb_pred
            xgb_models.append(xgb)
            feature_imp_xgb += xgb.feature_importances_ / n_splits

            r2_xgb = r2_score(y_val, xgb_pred)
            mae_xgb = mean_absolute_error(np.expm1(y_val), np.expm1(xgb_pred))
            print(f"    XGBoost   R²={r2_xgb:.4f}, MAE={mae_xgb:.3f} lb")

        # Ensemble
        if has_catboost and has_xgboost:
            ens_pred = 0.5 * cb_pred + 0.5 * xgb_pred
            ens_oof[val_idx] = ens_pred
            r2_ens = r2_score(y_val, ens_pred)
            mae_ens = mean_absolute_error(np.expm1(y_val), np.expm1(ens_pred))
            print(f"    Ensemble  R²={r2_ens:.4f}, MAE={mae_ens:.3f} lb")
        elif has_catboost:
            ens_oof[val_idx] = cb_pred
        else:
            ens_oof[val_idx] = xgb_pred

    # ── Overall metrics ──
    valid_mask = ~np.isnan(ens_oof)
    print("\n" + "=" * 60)
    print("  OVERALL SPATIAL CV RESULTS (GroupKFold by location)")
    print("=" * 60)

    if has_catboost:
        mask_cb = ~np.isnan(cb_oof)
        print(f"  CatBoost:  R²={r2_score(y[mask_cb], cb_oof[mask_cb]):.4f}, "
              f"MAE={mean_absolute_error(np.expm1(y[mask_cb]), np.expm1(cb_oof[mask_cb])):.3f} lb")
    if has_xgboost:
        mask_xgb = ~np.isnan(xgb_oof)
        print(f"  XGBoost:   R²={r2_score(y[mask_xgb], xgb_oof[mask_xgb]):.4f}, "
              f"MAE={mean_absolute_error(np.expm1(y[mask_xgb]), np.expm1(xgb_oof[mask_xgb])):.3f} lb")

    print(f"  Ensemble:  R²={r2_score(y[valid_mask], ens_oof[valid_mask]):.4f}, "
          f"MAE={mean_absolute_error(np.expm1(y[valid_mask]), np.expm1(ens_oof[valid_mask])):.3f} lb")

    # ── Feature importance analysis ──
    print("\n" + "=" * 60)
    print("  FEATURE IMPORTANCE (averaged across folds)")
    print("=" * 60)

    if has_catboost:
        imp_df = pd.DataFrame({
            "feature": FEATURE_COLS,
            "importance": feature_imp_cb,
        }).sort_values("importance", ascending=False)
        print("\n  CatBoost top 20:")
        for _, row in imp_df.head(20).iterrows():
            bar = "█" * int(row["importance"] / imp_df["importance"].max() * 30)
            print(f"    {row['feature']:30s} {row['importance']:6.2f} {bar}")

    # Weather vs location feature importance
    print("\n" + "-" * 60)
    if has_catboost:
        weather_imp = imp_df[imp_df["feature"].isin(WEATHER_FEATURES)]["importance"].sum()
        location_imp = imp_df[imp_df["feature"].isin(LOCATION_FEATURES)]["importance"].sum()
        total_imp = weather_imp + location_imp
        print(f"  Weather features:  {weather_imp:6.1f} ({100*weather_imp/total_imp:.1f}%)")
        print(f"  Location features: {location_imp:6.1f} ({100*location_imp/total_imp:.1f}%)")

    # ── Weather-only model (to isolate conditions effect) ──
    print("\n" + "=" * 60)
    print("  WEATHER-ONLY MODEL (conditions effect isolation)")
    print("=" * 60)

    weather_oof = np.full(len(y), np.nan)
    X_weather = df[WEATHER_FEATURES].copy()
    for col in X_weather.columns:
        if X_weather[col].dtype in [np.float64, np.float32, np.int64]:
            X_weather[col] = X_weather[col].fillna(X_weather[col].median())

    for fold, (train_idx, val_idx) in enumerate(gkf.split(X_weather, y, groups)):
        X_train, X_val = X_weather.iloc[train_idx], X_weather.iloc[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        if has_catboost:
            cb_w = CatBoostRegressor(
                iterations=500, depth=5, learning_rate=0.05,
                l2_leaf_reg=5, random_seed=42 + fold, verbose=0,
                early_stopping_rounds=50,
            )
            cb_w.fit(X_train, y_train, eval_set=(X_val, y_val), verbose=0)
            weather_oof[val_idx] = cb_w.predict(X_val)

    weather_valid = ~np.isnan(weather_oof)
    if weather_valid.any():
        r2_w = r2_score(y[weather_valid], weather_oof[weather_valid])
        mae_w = mean_absolute_error(np.expm1(y[weather_valid]), np.expm1(weather_oof[weather_valid]))
        print(f"  Weather-only R²={r2_w:.4f}, MAE={mae_w:.3f} lb")
        print(f"  (This shows how much variance weather alone explains,")
        print(f"   independent of which lake an event is at)")

    # ── Save models ──
    print("\n=== SAVING MODEL ===")
    model_artifact = {
        "feature_cols": FEATURE_COLS,
        "weather_features": WEATHER_FEATURES,
        "location_features": LOCATION_FEATURES,
        "cb_models": cb_models if has_catboost else [],
        "xgb_models": xgb_models if has_xgboost else [],
        "n_folds": n_splits,
        "training_samples": len(df),
        "training_date": datetime.now().isoformat(),
    }

    model_path = MODEL_DIR / "conditions_model.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model_artifact, f)
    print(f"  Model saved to {model_path}")

    # Also save a JSON summary (no model weights)
    summary = {
        "feature_cols": FEATURE_COLS,
        "weather_features": WEATHER_FEATURES,
        "location_features": LOCATION_FEATURES,
        "n_folds": n_splits,
        "training_samples": len(df),
        "training_date": datetime.now().isoformat(),
        "metrics": {
            "ensemble_r2": float(r2_score(y[valid_mask], ens_oof[valid_mask])),
            "ensemble_mae_lb": float(mean_absolute_error(
                np.expm1(y[valid_mask]), np.expm1(ens_oof[valid_mask])
            )),
        },
    }
    if weather_valid.any():
        summary["metrics"]["weather_only_r2"] = float(r2_w)
        summary["metrics"]["weather_only_mae_lb"] = float(mae_w)

    summary_path = MODEL_DIR / "conditions_model_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Summary saved to {summary_path}")

    # Also save to the designated results path
    # Include feature importance ranking
    if has_catboost:
        imp_ranked = sorted(zip(FEATURE_COLS, feature_imp_cb.tolist()),
                            key=lambda x: x[1], reverse=True)
        summary["feature_importance"] = [
            {"feature": feat, "importance": round(imp, 3)}
            for feat, imp in imp_ranked
        ]
        weather_imp_items = [(f, i) for f, i in imp_ranked if f in WEATHER_FEATURES]
        summary["top_weather_features"] = [
            {"feature": feat, "importance": round(imp, 3)}
            for feat, imp in weather_imp_items[:15]
        ]

    with open(RESULTS_PATH, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Results saved to {RESULTS_PATH}")

    return model_artifact


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CONDITIONS LAYER INFERENCE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_conditions_effect(model_artifact: dict, df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the conditions effect: how much BETTER or WORSE a day is
    compared to the location's historical average.

    Returns a dataframe with:
      - predicted_weight_lb: full model prediction
      - location_avg_weight_lb: historical average for that location
      - conditions_effect: predicted / location_avg (>1 = better, <1 = worse)
      - conditions_percentile: where this day ranks in history for this location
    """
    print("\n=== COMPUTING CONDITIONS EFFECT ===")

    # Location historical averages
    loc_avg = df.groupby("location")["median_weight_lb"].mean()

    # Full model predictions (average across fold models)
    X = df[FEATURE_COLS].copy()
    for col in X.columns:
        if X[col].dtype in [np.float64, np.float32, np.int64]:
            X[col] = X[col].fillna(X[col].median())

    preds = []
    for models in [model_artifact.get("cb_models", []), model_artifact.get("xgb_models", [])]:
        for m in models:
            preds.append(m.predict(X))

    if preds:
        avg_pred = np.mean(preds, axis=0)
        df["predicted_weight_lb"] = np.expm1(avg_pred)
    else:
        df["predicted_weight_lb"] = np.nan

    df["location_avg_weight_lb"] = df["location"].map(loc_avg)
    df["conditions_effect"] = df["predicted_weight_lb"] / df["location_avg_weight_lb"]

    # Percentile within location
    df["conditions_percentile"] = df.groupby("location")["conditions_effect"].rank(pct=True)

    # Summary
    print(f"  Mean conditions effect: {df['conditions_effect'].mean():.3f}")
    print(f"  Std conditions effect:  {df['conditions_effect'].std():.3f}")
    print(f"  Best conditions day:    {df['conditions_effect'].max():.3f}")
    print(f"  Worst conditions day:   {df['conditions_effect'].min():.3f}")

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  CASTLINE CONDITIONS MODEL — Day-Level Weather Effects")
    print("  Using FLW tournament data with exact dates")
    print("=" * 70)

    # Load FLW data
    flw = pd.read_csv(FLW_PATH)
    print(f"\nLoaded {len(flw)} FLW events ({flw['location'].nunique()} locations)")
    print(f"Date range: {flw['date'].min()} to {flw['date'].max()}")

    # Step 1: Geocode
    flw = geocode_flw_locations(flw)

    # Step 2: Load pre-fetched NOAA weather
    flw = load_noaa_weather(flw)

    # Step 3: Engineer features
    flw = engineer_features(flw)

    # Step 4: Train model
    model = train_conditions_model(flw)

    # Step 5: Compute conditions effect
    if model:
        flw = compute_conditions_effect(model, flw)

        # Save enriched dataset
        out_path = PROJECT / "castline/validation/data/raw/flw_conditions_enriched.csv"
        flw.to_csv(out_path, index=False)
        print(f"\nEnriched dataset saved to {out_path}")

    print("\n" + "=" * 70)
    print("  DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
