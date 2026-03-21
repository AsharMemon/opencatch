"""Build v7 MAXIMUM dataset — merge ALL outcome sources + environmental data.

v7 expands the dataset from ~1,275 rows / 258 locations to 3,000+ rows / 800+ locations
by integrating:
1. combined_all_outcomes_v2 (Bassmaster all trails: 1,471 rows, 300 locations)
2. TourneyX club/kayak tournaments (2,868 clean rows, 750 locations)
3. Creel CPUE species composition as features (8,059 surveys, 1,445 waterbodies)
4. Open-Meteo weather for ALL events (universal lat/lon coverage)
5. USGS water quality where available
6. All v6 feature engineering (biology, lake features, moon, interactions)
"""
from __future__ import annotations

import json
import math
import ssl
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

# SSL context for API calls (macOS Python sometimes lacks certs)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

RAW = Path("castline/validation/data/raw")
ASSEMBLED = Path("castline/validation/data/assembled")
KNOWLEDGE = Path("castline/validation/knowledge")

# ── Optional feature modules ──
try:
    from castline.validation.features.fish_biology import compute_fish_biology_features
    _HAS_BIO = True
except ImportError:
    _HAS_BIO = False

try:
    from castline.validation.features.location_quality import compute_location_quality_features
    _HAS_LOC_QUALITY = True
except ImportError:
    _HAS_LOC_QUALITY = False


# ────────────────────────────────────────────────────────────
# Step 1: Merge all outcomes
# ────────────────────────────────────────────────────────────

def merge_all_outcomes() -> pd.DataFrame:
    """Merge combined_all_outcomes_v2 + cleaned TourneyX into one master outcomes file."""
    print("=" * 70)
    print("STEP 1: Merging all outcome sources")
    print("=" * 70)

    # Load combined_v2 (Bassmaster all trails + some mapped events)
    cv2 = pd.read_csv(RAW / "combined_all_outcomes_v2.csv")
    cv2["source"] = "bassmaster"
    print(f"  combined_v2: {len(cv2)} rows, {cv2.location.nunique()} locations")

    # Load TourneyX
    tx = pd.read_csv(RAW / "tourneyx_outcomes.csv")
    print(f"  TourneyX raw: {len(tx)} rows, {tx.location.nunique()} locations")

    # Clean TourneyX: filter quality
    tx = tx[
        (tx.median_weight_lb >= 0.5) &
        (tx.median_weight_lb <= 30) &
        (tx.num_anglers >= 3) &
        (tx.location.notna())
    ].copy()
    tx["source"] = "tourneyx"
    print(f"  TourneyX cleaned: {len(tx)} rows, {tx.location.nunique()} locations")

    # Remove TourneyX rows that overlap with CV2 locations+dates
    cv2_keys = set(zip(cv2.location, cv2.date.astype(str)))
    tx["_key"] = list(zip(tx.location, tx.date.astype(str)))
    tx_new = tx[~tx["_key"].isin(cv2_keys)].drop(columns=["_key"])
    print(f"  TourneyX after dedup with CV2: {len(tx_new)} rows, {tx_new.location.nunique()} locations")

    # Align columns
    common_cols = [
        "event_id", "tournament_slug", "event_name", "date", "location",
        "species", "median_weight_lb", "baseline_signal", "usgs_site_id",
        "results_source", "num_anglers", "day_number", "tms_id", "trail", "source",
    ]
    for col in common_cols:
        if col not in cv2.columns:
            cv2[col] = np.nan
        if col not in tx_new.columns:
            tx_new[col] = np.nan

    merged = pd.concat([cv2[common_cols], tx_new[common_cols]], ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    print(f"\n  Tournament MERGED: {len(merged)} rows, {merged.location.nunique()} locations")
    print(f"    Bassmaster: {(merged.source == 'bassmaster').sum()}")
    print(f"    TourneyX:   {(merged.source == 'tourneyx').sum()}")

    # ── Add creel survey data as training rows ──
    creel_gnn_path = RAW / "creel_gnn_locations_expanded.csv"
    creel_raw_path = RAW / "creel_cpue_bass.csv"

    if creel_gnn_path.exists() and creel_raw_path.exists():
        print("\n  Adding creel survey data as training rows...")
        creel_gnn = pd.read_csv(creel_gnn_path)
        creel_raw = pd.read_csv(creel_raw_path)

        # Build per-survey rows from raw creel data
        # Group by waterbody + date to get one row per survey event
        creel_events = creel_raw.groupby(["waterbody_name", "date"]).agg(
            cpue=("cpue_fish_per_hour", "mean"),
            total_catch=("total_catch", "sum"),
            latitude=("latitude", "first"),
            longitude=("longitude", "first"),
            state=("state", "first"),
        ).reset_index()

        # Merge synthetic weight from GNN locations
        creel_events = creel_events.merge(
            creel_gnn[["waterbody_name", "synthetic_weight_lb"]],
            on="waterbody_name", how="left"
        )

        # Use synthetic weight as target (CPUE-calibrated tournament-equivalent weight)
        creel_events = creel_events[creel_events.synthetic_weight_lb.notna()].copy()

        # Build compatible rows
        creel_rows = pd.DataFrame({
            "event_id": "creel_" + creel_events.waterbody_name + "_" + creel_events.date.astype(str),
            "tournament_slug": np.nan,
            "event_name": "Creel Survey: " + creel_events.waterbody_name,
            "date": pd.to_datetime(creel_events.date, errors="coerce").dt.strftime("%Y-%m-%d"),
            "location": creel_events.waterbody_name + ", " + creel_events.state,
            "species": "bass",
            "median_weight_lb": creel_events.synthetic_weight_lb,
            "baseline_signal": np.nan,
            "usgs_site_id": np.nan,
            "results_source": "creel_survey",
            "num_anglers": np.nan,
            "day_number": 1,
            "tms_id": np.nan,
            "trail": "creel",
            "source": "creel",
        })
        # Drop rows with invalid dates
        creel_rows = creel_rows[creel_rows.date.notna()].copy()

        # Remove creel locations that overlap with existing tournament locations
        existing_wbs = set(merged.location.str.split(",").str[0].str.strip().str.lower())
        creel_rows["_wb"] = creel_rows.location.str.split(",").str[0].str.strip().str.lower()
        creel_new = creel_rows[~creel_rows._wb.isin(existing_wbs)].drop(columns=["_wb"])

        print(f"    Creel events: {len(creel_rows)} total, {len(creel_new)} new (non-overlapping)")
        print(f"    Creel locations: {creel_new.location.nunique()} new")

        merged = pd.concat([merged, creel_new[common_cols]], ignore_index=True)

    print(f"\n  FINAL MERGED: {len(merged)} rows, {merged.location.nunique()} locations")
    print(f"    Bassmaster: {(merged.source == 'bassmaster').sum()}")
    print(f"    TourneyX:   {(merged.source == 'tourneyx').sum()}")
    print(f"    Creel:      {(merged.source == 'creel').sum()}")

    return merged


# ────────────────────────────────────────────────────────────
# Step 2: Geocode locations → lat/lon
# ────────────────────────────────────────────────────────────

def geocode_locations(df: pd.DataFrame) -> pd.DataFrame:
    """Add lat/lon to all rows by geocoding location names."""
    print("\n" + "=" * 70)
    print("STEP 2: Geocoding locations")
    print("=" * 70)

    # Load existing morphometry for known lakes
    morph_path = KNOWLEDGE / "lake_morphometry.json"
    morph = {}
    if morph_path.exists():
        with open(morph_path) as f:
            morph = json.load(f)

    # Load existing v6 location data
    v6_path = ASSEMBLED / "validation_dataset_v6.csv"
    v6_locs = {}
    if v6_path.exists():
        v6 = pd.read_csv(v6_path, usecols=["location", "lat", "lon"])
        for _, row in v6.drop_duplicates("location").iterrows():
            if pd.notna(row.get("lat")) and pd.notna(row.get("lon")):
                v6_locs[row["location"]] = (row["lat"], row["lon"])

    # Load creel locations for matching — use as geocoding source too
    creel_locs = {}
    creel_path = RAW / "creel_gnn_locations_expanded.csv"
    if creel_path.exists():
        creel = pd.read_csv(creel_path)
        for _, row in creel.iterrows():
            creel_locs[row["waterbody_name"].lower()] = (row["latitude"], row["longitude"])

    # Build location → (lat, lon) mapping
    loc_coords = {}

    # 0. From existing geocode cache
    cache_path = RAW / "geocode_cache.json"
    if cache_path.exists():
        with open(cache_path) as f:
            cache = json.load(f)
        for loc, data in cache.items():
            if data.get("lat") is not None and data.get("lon") is not None:
                loc_coords[loc] = (data["lat"], data["lon"])
        print(f"  Loaded {len(loc_coords)} from geocode cache")

    # 1. From v6 dataset
    for loc, (lat, lon) in v6_locs.items():
        loc_coords[loc] = (lat, lon)

    # 2. From morphometry
    for lake, data in morph.items():
        if "lat" in data and "lon" in data:
            # Find matching locations
            for loc in df.location.unique():
                if pd.isna(loc):
                    continue
                if lake.lower() in loc.lower():
                    if loc not in loc_coords:
                        loc_coords[loc] = (data["lat"], data["lon"])

    # 2b. From creel GNN locations (direct lat/lon for "Waterbody, State" format)
    if creel_path.exists():
        creel_gnn = pd.read_csv(creel_path)
        for _, row in creel_gnn.iterrows():
            loc_key = f"{row['waterbody_name']}, {row['state']}"
            if loc_key not in loc_coords:
                loc_coords[loc_key] = (row["latitude"], row["longitude"])
            # Also try matching by waterbody name substring
            for loc in df.location.unique():
                if pd.isna(loc):
                    continue
                if row["waterbody_name"].lower() in loc.lower() and loc not in loc_coords:
                    loc_coords[loc] = (row["latitude"], row["longitude"])

    print(f"  Known locations from v6/morphometry/creel: {len(loc_coords)}")

    # 3. Geocode remaining via Nominatim
    unknown = [loc for loc in df.location.unique() if pd.notna(loc) and loc not in loc_coords]
    print(f"  Locations needing geocoding: {len(unknown)}")

    geocoded = 0
    failed = []
    for i, loc in enumerate(unknown):
        if i > 0 and i % 50 == 0:
            print(f"    Geocoded {i}/{len(unknown)}... ({geocoded} success)")

        # Parse "City, State" format
        query = loc.strip()
        try:
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({
                "q": query + " fishing",
                "format": "json",
                "limit": 1,
                "countrycodes": "us,ca",
            })
            req = urllib.request.Request(url, headers={"User-Agent": "CASTLINE/1.0"})
            with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
                results = json.loads(resp.read().decode())
            if results:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                loc_coords[loc] = (lat, lon)
                geocoded += 1
            else:
                # Try without "fishing" suffix
                url2 = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({
                    "q": query,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "us,ca",
                })
                req2 = urllib.request.Request(url2, headers={"User-Agent": "CASTLINE/1.0"})
                with urllib.request.urlopen(req2, timeout=10, context=SSL_CTX) as resp2:
                    results2 = json.loads(resp2.read().decode())
                if results2:
                    lat = float(results2[0]["lat"])
                    lon = float(results2[0]["lon"])
                    loc_coords[loc] = (lat, lon)
                    geocoded += 1
                else:
                    failed.append(loc)
            time.sleep(1.1)  # Nominatim rate limit: 1 req/sec
        except Exception as e:
            failed.append(loc)
            time.sleep(1.1)

    print(f"  Geocoded: {geocoded}/{len(unknown)} new locations")
    if failed:
        print(f"  Failed: {len(failed)} locations (will be dropped)")

    # Apply to dataframe
    df["lat"] = df.location.map(lambda x: loc_coords.get(x, (np.nan, np.nan))[0])
    df["lon"] = df.location.map(lambda x: loc_coords.get(x, (np.nan, np.nan))[1])

    n_with_coords = df.lat.notna().sum()
    print(f"  Rows with coordinates: {n_with_coords}/{len(df)} ({100*n_with_coords/len(df):.1f}%)")

    # Drop rows without coordinates
    before = len(df)
    df = df[df.lat.notna() & df.lon.notna()].reset_index(drop=True)
    print(f"  Dropped {before - len(df)} rows without coordinates")

    # Save geocoding cache
    cache = {loc: {"lat": lat, "lon": lon} for loc, (lat, lon) in loc_coords.items()}
    cache_path = RAW / "geocode_cache.json"
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"  Saved geocode cache: {cache_path}")

    return df


# ────────────────────────────────────────────────────────────
# Step 3: Fetch Open-Meteo weather for all events
# ────────────────────────────────────────────────────────────

def fetch_openmeteo_weather(df: pd.DataFrame) -> pd.DataFrame:
    """Fetch weather from Open-Meteo for ALL events (universal coverage)."""
    print("\n" + "=" * 70)
    print("STEP 3: Fetching Open-Meteo weather")
    print("=" * 70)

    # Check for cached weather
    cache_path = RAW / "openmeteo_weather_cache.csv"
    if cache_path.exists():
        cached = pd.read_csv(cache_path)
        cached_keys = set(zip(cached.lat.round(2), cached.lon.round(2), cached.date.astype(str)))
        print(f"  Weather cache: {len(cached)} rows")
    else:
        cached = pd.DataFrame()
        cached_keys = set()

    # Find events needing weather
    df["_lat_r"] = df.lat.round(2)
    df["_lon_r"] = df.lon.round(2)
    df_keys = set(zip(df._lat_r, df._lon_r, df.date.astype(str)))
    needed_keys = df_keys - cached_keys

    if not needed_keys:
        print("  All weather already cached!")
    else:
        print(f"  Need weather for {len(needed_keys)} unique lat/lon/date combos")

        # Group by lat/lon to batch date requests
        from collections import defaultdict
        loc_dates = defaultdict(list)
        for lat, lon, date in needed_keys:
            loc_dates[(lat, lon)].append(date)

        print(f"  Unique locations to query: {len(loc_dates)}")

        new_rows = []
        done = 0
        for (lat, lon), dates in loc_dates.items():
            dates_sorted = sorted(dates)
            # Open-Meteo allows date ranges
            start_date = dates_sorted[0]
            end_date = dates_sorted[-1]

            try:
                params = {
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": start_date,
                    "end_date": end_date,
                    "daily": ",".join([
                        "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
                        "apparent_temperature_max", "precipitation_sum", "rain_sum",
                        "windspeed_10m_max", "windgusts_10m_max", "winddirection_10m_dominant",
                        "shortwave_radiation_sum", "et0_fao_evapotranspiration",
                        "pressure_msl_max", "pressure_msl_min", "pressure_msl_mean",
                    ]),
                    "timezone": "America/New_York",
                }
                url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
                req = urllib.request.Request(url, headers={"User-Agent": "CASTLINE/1.0"})
                with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                    data = json.loads(resp.read().decode())

                if "daily" in data:
                    daily = data["daily"]
                    for i, d in enumerate(daily.get("time", [])):
                        if d in dates:
                            row = {
                                "lat": lat, "lon": lon, "date": d,
                                "om_air_temp_max": daily.get("temperature_2m_max", [None])[i],
                                "om_air_temp_min": daily.get("temperature_2m_min", [None])[i],
                                "om_air_temp_mean": daily.get("temperature_2m_mean", [None])[i],
                                "om_apparent_temp": daily.get("apparent_temperature_max", [None])[i],
                                "om_precip_mm": daily.get("precipitation_sum", [None])[i],
                                "om_rain_mm": daily.get("rain_sum", [None])[i],
                                "om_wind_max_kph": daily.get("windspeed_10m_max", [None])[i],
                                "om_wind_gust_kph": daily.get("windgusts_10m_max", [None])[i],
                                "om_wind_dir_dominant": daily.get("winddirection_10m_dominant", [None])[i],
                                "om_solar_radiation": daily.get("shortwave_radiation_sum", [None])[i],
                                "om_et0": daily.get("et0_fao_evapotranspiration", [None])[i],
                                "om_pressure_max": daily.get("pressure_msl_max", [None])[i],
                                "om_pressure_min": daily.get("pressure_msl_min", [None])[i],
                                "om_pressure_msl": daily.get("pressure_msl_mean", [None])[i],
                            }
                            new_rows.append(row)

                done += 1
                if done % 25 == 0:
                    print(f"    Fetched {done}/{len(loc_dates)} locations ({len(new_rows)} rows)")
                time.sleep(0.3)  # Rate limit

            except Exception as e:
                done += 1
                if done % 100 == 0:
                    print(f"    Progress: {done}/{len(loc_dates)} (error: {e})")

        if new_rows:
            new_df = pd.DataFrame(new_rows)
            cached = pd.concat([cached, new_df], ignore_index=True) if len(cached) > 0 else new_df
            cached.to_csv(cache_path, index=False)
            print(f"  Cached {len(new_rows)} new weather rows (total: {len(cached)})")

    # Merge weather into df
    if len(cached) > 0:
        cached["_lat_r"] = cached.lat.round(2)
        cached["_lon_r"] = cached.lon.round(2)
        cached["date"] = cached.date.astype(str)

        om_cols = [c for c in cached.columns if c.startswith("om_")]
        merge_df = cached[["_lat_r", "_lon_r", "date"] + om_cols].drop_duplicates(
            subset=["_lat_r", "_lon_r", "date"], keep="last"
        )

        df = df.merge(merge_df, on=["_lat_r", "_lon_r", "date"], how="left")
        matched = df[om_cols[0]].notna().sum() if om_cols else 0
        print(f"  Weather matched: {matched}/{len(df)} rows ({100*matched/len(df):.1f}%)")

    df = df.drop(columns=["_lat_r", "_lon_r"], errors="ignore")

    # Derive pressure deltas
    if "om_pressure_max" in df.columns and "om_pressure_min" in df.columns:
        df["om_pressure_delta_24h"] = df["om_pressure_max"] - df["om_pressure_min"]

    # Estimate water temp from air temp
    if "om_air_temp_mean" in df.columns:
        df["om_est_water_temp"] = df["om_air_temp_mean"] * 0.663 + 7.16

    return df


# ────────────────────────────────────────────────────────────
# Step 4: Merge existing USGS data where available
# ────────────────────────────────────────────────────────────

def merge_usgs_data(df: pd.DataFrame) -> pd.DataFrame:
    """Merge USGS daily values for events that have site mappings."""
    print("\n" + "=" * 70)
    print("STEP 4: Merging USGS water quality data")
    print("=" * 70)

    usgs_path = RAW / "usgs_history_v2.csv"
    if not usgs_path.exists():
        print("  No USGS data file — skipping")
        return df

    usgs = pd.read_csv(usgs_path)
    print(f"  USGS data: {len(usgs)} rows, {usgs.site_id.nunique()} sites")

    # Normalize site IDs — handle "USGS-03574500" and "2267000" formats
    def _norm(val):
        if pd.isna(val):
            return np.nan
        s = str(val).strip()
        # Remove "USGS-" prefix if present
        if s.upper().startswith("USGS-"):
            s = s[5:]
        try:
            return str(int(float(s))).zfill(8)
        except (ValueError, TypeError):
            return s

    df["_usgs_key"] = df.usgs_site_id.apply(_norm)
    usgs["_usgs_key"] = usgs.site_id.apply(_norm)
    usgs["date"] = usgs.observation_date.astype(str) if "observation_date" in usgs.columns else usgs.date.astype(str)

    # Select USGS columns to merge
    usgs_feature_cols = [c for c in usgs.columns if c not in (
        "event_id", "site_id", "observation_date", "date", "_usgs_key", "source_mode"
    )]

    # Drop any that already exist in df
    usgs_feature_cols = [c for c in usgs_feature_cols if c not in df.columns]

    if usgs_feature_cols:
        usgs_subset = usgs[["_usgs_key", "date"] + usgs_feature_cols].drop_duplicates(
            subset=["_usgs_key", "date"], keep="last"
        )
        df = df.merge(usgs_subset, left_on=["_usgs_key", "date"], right_on=["_usgs_key", "date"], how="left")
        matched = df[usgs_feature_cols[0]].notna().sum()
        print(f"  USGS matched: {matched}/{len(df)} rows ({100*matched/len(df):.1f}%)")
        print(f"  Added {len(usgs_feature_cols)} USGS columns")
    else:
        print("  All USGS columns already present")

    df = df.drop(columns=["_usgs_key"], errors="ignore")

    # Also merge IV features
    iv_path = RAW / "usgs_iv_features_v2.csv"
    if iv_path.exists():
        iv = pd.read_csv(iv_path)
        print(f"  IV features: {len(iv)} rows")
        iv["_usgs_key"] = iv.usgs_site_id.apply(_norm)
        iv["date"] = iv.date.astype(str)
        df["_usgs_key"] = df.usgs_site_id.apply(_norm)

        iv_cols = [c for c in iv.columns if c not in ("usgs_site_id", "date", "_usgs_key", "iv_readings") and c not in df.columns]
        if iv_cols:
            iv_sub = iv[["_usgs_key", "date"] + iv_cols].drop_duplicates(subset=["_usgs_key", "date"])
            df = df.merge(iv_sub, on=["_usgs_key", "date"], how="left")
            matched = df[iv_cols[0]].notna().sum()
            print(f"  IV matched: {matched}/{len(df)} rows")

        df = df.drop(columns=["_usgs_key"], errors="ignore")

    return df


# ────────────────────────────────────────────────────────────
# Step 5: Add creel species composition features
# ────────────────────────────────────────────────────────────

def add_creel_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add creel survey CPUE and species composition features via spatial proximity."""
    print("\n" + "=" * 70)
    print("STEP 5: Adding creel CPUE & species features")
    print("=" * 70)

    creel_path = RAW / "creel_cpue_bass.csv"
    if not creel_path.exists():
        print("  No creel data — skipping")
        return df

    creel = pd.read_csv(creel_path)
    creel = creel[creel.latitude.notna() & creel.longitude.notna()].copy()
    print(f"  Creel surveys: {len(creel)} rows, {creel.waterbody_name.nunique()} waterbodies")

    # Build waterbody-level summaries
    wb_stats = creel.groupby("waterbody_name").agg(
        wb_lat=("latitude", "mean"),
        wb_lon=("longitude", "mean"),
        total_cpue=("cpue_fish_per_hour", "mean"),
        n_surveys=("survey_id", "nunique"),
    ).reset_index()

    # Species-specific CPUE
    for species in ["largemouth_bass", "smallmouth_bass", "spotted_bass"]:
        sp_data = creel[creel.species == species].groupby("waterbody_name").agg(
            cpue=("cpue_fish_per_hour", "mean"),
            n=("survey_id", "nunique"),
        ).reset_index()
        sp_data = sp_data.rename(columns={"cpue": f"{species}_cpue", "n": f"{species}_surveys"})
        wb_stats = wb_stats.merge(sp_data[["waterbody_name", f"{species}_cpue", f"{species}_surveys"]],
                                   on="waterbody_name", how="left")

    # Compute species ratios
    wb_stats["total_species_surveys"] = (
        wb_stats.get("largemouth_bass_surveys", pd.Series(0)).fillna(0) +
        wb_stats.get("smallmouth_bass_surveys", pd.Series(0)).fillna(0) +
        wb_stats.get("spotted_bass_surveys", pd.Series(0)).fillna(0)
    )
    for sp in ["largemouth_bass", "smallmouth_bass", "spotted_bass"]:
        col = f"{sp}_surveys"
        if col in wb_stats.columns:
            wb_stats[f"{sp}_ratio"] = wb_stats[col].fillna(0) / wb_stats["total_species_surveys"].clip(lower=1)

    print(f"  Waterbody summaries: {len(wb_stats)}")

    # Build KD-tree for spatial matching
    wb_valid = wb_stats[wb_stats.wb_lat.notna()].copy()
    wb_coords = np.radians(wb_valid[["wb_lat", "wb_lon"]].values)
    # Convert to cartesian for proper distance
    wb_cart = np.column_stack([
        np.cos(wb_coords[:, 0]) * np.cos(wb_coords[:, 1]),
        np.cos(wb_coords[:, 0]) * np.sin(wb_coords[:, 1]),
        np.sin(wb_coords[:, 0]),
    ])
    tree = cKDTree(wb_cart)

    # Query for each unique location in df
    loc_df = df[["location", "lat", "lon"]].drop_duplicates("location")
    loc_coords = np.radians(loc_df[["lat", "lon"]].values)
    loc_cart = np.column_stack([
        np.cos(loc_coords[:, 0]) * np.cos(loc_coords[:, 1]),
        np.cos(loc_coords[:, 0]) * np.sin(loc_coords[:, 1]),
        np.sin(loc_coords[:, 0]),
    ])

    K = 10  # nearest waterbodies
    distances, indices = tree.query(loc_cart, k=K)
    R_EARTH = 6371.0  # km

    creel_features = {}
    feature_names = [
        "creel_cpue_mean", "creel_n_surveys",
        "creel_lmb_cpue", "creel_smb_cpue", "creel_spotted_cpue",
        "creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio",
        "creel_nearest_dist_km",
    ]

    for i, (_, row) in enumerate(loc_df.iterrows()):
        dists_km = distances[i] * R_EARTH
        idxs = indices[i]

        # Weight by inverse distance (within 100km)
        mask = dists_km < 100
        if not mask.any():
            # Use closest regardless
            mask[0] = True

        valid_dists = dists_km[mask]
        valid_idxs = idxs[mask]
        weights = 1.0 / (valid_dists + 1.0)
        weights /= weights.sum()

        feats = {
            "creel_cpue_mean": np.average(wb_valid.iloc[valid_idxs].total_cpue.values, weights=weights),
            "creel_n_surveys": wb_valid.iloc[valid_idxs].n_surveys.sum(),
            "creel_nearest_dist_km": dists_km[0],
        }
        for sp, short in [("largemouth_bass", "lmb"), ("smallmouth_bass", "smb"), ("spotted_bass", "spotted")]:
            cpue_col = f"{sp}_cpue"
            ratio_col = f"{sp}_ratio"
            vals = wb_valid.iloc[valid_idxs][cpue_col].fillna(0).values
            feats[f"creel_{short}_cpue"] = np.average(vals, weights=weights)
            ratio_vals = wb_valid.iloc[valid_idxs][ratio_col].fillna(0).values
            feats[f"creel_{short}_ratio"] = np.average(ratio_vals, weights=weights)

        creel_features[row["location"]] = feats

    # Map to df
    for feat in feature_names:
        df[feat] = df.location.map(lambda loc: creel_features.get(loc, {}).get(feat, np.nan))

    coverage = df["creel_cpue_mean"].notna().mean()
    print(f"  Creel feature coverage: {100*coverage:.1f}%")
    print(f"  Added {len(feature_names)} creel features")

    # Species composition indicator features
    df["is_smallmouth_water"] = (df["creel_smb_ratio"] > 0.3).astype(float)
    df["is_largemouth_water"] = (df["creel_lmb_ratio"] > 0.5).astype(float)
    df["species_diversity"] = 1.0 - (
        df["creel_lmb_ratio"].fillna(0)**2 +
        df["creel_smb_ratio"].fillna(0)**2 +
        df["creel_spotted_ratio"].fillna(0)**2
    )

    return df


# ────────────────────────────────────────────────────────────
# Step 6: Add morphometry
# ────────────────────────────────────────────────────────────

def add_morphometry(df: pd.DataFrame) -> pd.DataFrame:
    """Add lake morphometry (area, depth, shore dev) from knowledge base."""
    print("\n" + "=" * 70)
    print("STEP 6: Adding morphometry features")
    print("=" * 70)

    morph_path = KNOWLEDGE / "lake_morphometry.json"
    if not morph_path.exists():
        print("  No morphometry file — skipping")
        return df

    with open(morph_path) as f:
        morph = json.load(f)

    # Match locations to morphometry entries
    matched = 0
    for col in ["area_acres", "max_depth_ft", "shore_dev"]:
        if col not in df.columns:
            df[col] = np.nan

    for lake_name, data in morph.items():
        mask = df.location.str.contains(lake_name, case=False, na=False)
        if mask.any():
            for col in ["area_acres", "max_depth_ft", "shore_dev"]:
                if col in data:
                    df.loc[mask & df[col].isna(), col] = data[col]
            matched += mask.sum()

    coverage = df["area_acres"].notna().mean()
    print(f"  Morphometry matched: {matched} rows ({100*coverage:.1f}% coverage)")

    # Derived morphometry features
    df["wtype_river"] = df.location.str.contains("River|Creek|Canal|Bayou", case=False, na=False).astype(float)
    df["wtype_reservoir"] = df.location.str.contains("Reservoir|Dam|Pool|Impoundment", case=False, na=False).astype(float)
    df["wtype_natural_lake"] = ((df.wtype_river == 0) & (df.wtype_reservoir == 0)).astype(float)
    df["is_lake"] = (df.wtype_river == 0).astype(float)

    if "area_acres" in df.columns and "max_depth_ft" in df.columns:
        df["reservoir_score"] = (
            np.log1p(df["area_acres"].fillna(0)) *
            np.log1p(df["max_depth_ft"].fillna(0))
        ) / 20.0

    # Latitude-based features
    df["latitude_growth_potential"] = 1.0 / (1.0 + np.exp(-0.3 * (df.lat - 33)))
    df["northern_trophy_potential"] = np.where(df.lat > 42, 1.0, np.where(df.lat > 38, 0.5, 0.0))
    df["shad_habitat_score"] = np.clip(1.0 - (df.lat - 30) / 20, 0, 1)
    df["smallmouth_habitat_score"] = np.clip((df.lat - 35) / 15, 0, 1)

    return df


# ────────────────────────────────────────────────────────────
# Step 7: Temporal + astronomical features
# ────────────────────────────────────────────────────────────

def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add temporal, moon phase, and seasonal features."""
    print("\n" + "=" * 70)
    print("STEP 7: Adding temporal features")
    print("=" * 70)

    dt = pd.to_datetime(df.date, errors="coerce")
    df["year"] = dt.dt.year
    df["month"] = dt.dt.month
    df["day_of_year"] = dt.dt.dayofyear

    # Season encoding (sin/cos)
    df["season_sin"] = np.sin(2 * np.pi * df.day_of_year / 365.25)
    df["season_cos"] = np.cos(2 * np.pi * df.day_of_year / 365.25)

    # Day length (hours) from latitude and day of year
    def _day_length(lat, doy):
        lat_rad = np.radians(lat)
        decl = np.radians(23.44 * np.sin(np.radians(360 / 365 * (doy + 284))))
        cos_ha = -np.tan(lat_rad) * np.tan(decl)
        cos_ha = np.clip(cos_ha, -1, 1)
        return 2 * np.degrees(np.arccos(cos_ha)) / 15
    df["day_length_hours"] = _day_length(df.lat.values, df.day_of_year.values)

    # Spawn phase (latitude-adjusted)
    spawn_peak = 90 + (df.lat - 30) * 2.5  # later spawn at higher latitudes
    df["spawn_phase_score"] = np.exp(-0.5 * ((df.day_of_year - spawn_peak) / 30) ** 2)

    # Moon phase
    SYNODIC = 29.530588853
    REF = pd.Timestamp("2000-01-06 18:14", tz="UTC")
    days_since = (dt.dt.tz_localize("UTC") - REF).dt.total_seconds() / 86400.0
    phase = (days_since % SYNODIC) / SYNODIC
    df["moon_phase"] = phase
    df["moon_phase_sin"] = np.sin(2 * np.pi * phase)
    df["moon_phase_cos"] = np.cos(2 * np.pi * phase)
    df["moon_illumination_pct"] = (1.0 - np.cos(2 * np.pi * phase)) / 2.0 * 100.0

    # Solunar score (simplified)
    df["solunar_score"] = 0.5 + 0.3 * np.cos(2 * np.pi * phase) + 0.2 * np.cos(4 * np.pi * phase)

    print(f"  Added temporal features: year, month, day_of_year, season_sin/cos, day_length, spawn_phase, moon, solunar")

    return df


# ────────────────────────────────────────────────────────────
# Step 8: Core weather features (fill gaps)
# ────────────────────────────────────────────────────────────

def fill_weather_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Fill core weather columns from Open-Meteo data."""
    print("\n" + "=" * 70)
    print("STEP 8: Filling weather gaps")
    print("=" * 70)

    fill_map = {
        "air_temp_c": "om_air_temp_mean",
        "pressure_mb": "om_pressure_msl",
        "wind_speed_kph": "om_wind_max_kph",
    }

    for target, source in fill_map.items():
        if target not in df.columns:
            df[target] = np.nan
        if source in df.columns:
            before = df[target].notna().sum()
            df[target] = df[target].fillna(df[source])
            after = df[target].notna().sum()
            print(f"  {target}: {before} → {after} ({after-before} filled)")

    # Water temp from air temp if missing
    if "water_temp_c" not in df.columns:
        df["water_temp_c"] = np.nan
    if "om_est_water_temp" in df.columns:
        before = df.water_temp_c.notna().sum()
        df["water_temp_c"] = df["water_temp_c"].fillna(df["om_est_water_temp"])
        after = df.water_temp_c.notna().sum()
        print(f"  water_temp_c: {before} → {after} ({after-before} filled from estimate)")

    # Wind direction from Open-Meteo
    if "om_wind_dir_dominant" in df.columns:
        if "wind_dir_sin" not in df.columns:
            df["wind_dir_sin"] = np.nan
        if "wind_dir_cos" not in df.columns:
            df["wind_dir_cos"] = np.nan
        mask = df.wind_dir_sin.isna() & df.om_wind_dir_dominant.notna()
        if mask.any():
            rad = np.radians(df.loc[mask, "om_wind_dir_dominant"])
            df.loc[mask, "wind_dir_sin"] = np.sin(rad)
            df.loc[mask, "wind_dir_cos"] = np.cos(rad)

    return df


# ────────────────────────────────────────────────────────────
# Step 9: Fish biology features
# ────────────────────────────────────────────────────────────

def add_biology_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add fish biology features if module is available."""
    print("\n" + "=" * 70)
    print("STEP 9: Adding fish biology features")
    print("=" * 70)

    if not _HAS_BIO:
        print("  Fish biology module not available — skipping")
        return df

    bio_rows = []
    for _, row in df.iterrows():
        doy = int(row.get("day_of_year", 180))
        feats = compute_fish_biology_features(
            water_temp_c=float(row.get("water_temp_c", np.nan)),
            air_temp_c=float(row.get("air_temp_c", np.nan)),
            pressure_mb=float(row.get("pressure_mb", np.nan)),
            pressure_delta_6h=float(row.get("pressure_delta_6h", np.nan) if pd.notna(row.get("pressure_delta_6h")) else np.nan),
            pressure_delta_24h=float(row.get("om_pressure_delta_24h", np.nan) if pd.notna(row.get("om_pressure_delta_24h")) else np.nan),
            wind_speed_kph=float(row.get("wind_speed_kph", np.nan)),
            cloud_cover_pct=float(row.get("cloud_cover_pct", np.nan) if pd.notna(row.get("cloud_cover_pct")) else np.nan),
            day_of_year=doy,
            latitude=float(row.get("lat", np.nan)),
            humidity_pct=float(row.get("om_humidity", np.nan) if pd.notna(row.get("om_humidity")) else np.nan),
            precip_mm=float(row.get("om_precip_mm", 0.0) if pd.notna(row.get("om_precip_mm")) else 0.0),
            dissolved_oxygen_mgL=float(row.get("dissolved_oxygen_mgL", np.nan) if pd.notna(row.get("dissolved_oxygen_mgL")) else np.nan),
            moon_phase=float(row.get("moon_phase", np.nan)),
        )
        bio_rows.append(feats)

    bio_df = pd.DataFrame(bio_rows)
    new_cols = [c for c in bio_df.columns if c not in df.columns]
    for col in new_cols:
        df[col] = bio_df[col].values
    print(f"  Added {len(new_cols)} biology features")

    return df


# ────────────────────────────────────────────────────────────
# Step 10: Interaction features
# ────────────────────────────────────────────────────────────

def add_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add cross-feature interaction terms."""
    print("\n" + "=" * 70)
    print("STEP 10: Adding interaction features")
    print("=" * 70)

    n = 0

    # Species × environment interactions
    if "creel_smb_ratio" in df.columns and "water_temp_c" in df.columns:
        # Smallmouth prefer cooler water
        df["smb_temp_comfort"] = df["creel_smb_ratio"] * np.clip(1 - (df["water_temp_c"] - 18) / 10, 0, 1)
        n += 1

    if "creel_lmb_ratio" in df.columns and "water_temp_c" in df.columns:
        # Largemouth prefer warmer water
        df["lmb_temp_comfort"] = df["creel_lmb_ratio"] * np.clip(1 - abs(df["water_temp_c"] - 22) / 12, 0, 1)
        n += 1

    # Spawn × species
    if "spawn_phase_score" in df.columns and "creel_smb_ratio" in df.columns:
        df["species_spawn_interaction"] = df["spawn_phase_score"] * (
            df["creel_lmb_ratio"].fillna(0) * 0.7 + df["creel_smb_ratio"].fillna(0) * 1.3
        )
        n += 1

    # Water temp × season
    if "water_temp_c" in df.columns and "spawn_phase_score" in df.columns:
        df["water_temp_spawn"] = df["water_temp_c"] * df["spawn_phase_score"]
        n += 1

    # Pressure × moon
    if "om_pressure_msl" in df.columns and "moon_illumination_pct" in df.columns:
        df["pressure_moon"] = df["om_pressure_msl"] * df["moon_illumination_pct"] / 100
        n += 1

    # Wind × area (bigger lakes = more wind effect)
    if "wind_speed_kph" in df.columns and "area_acres" in df.columns:
        df["wind_fetch"] = df["wind_speed_kph"] * np.log1p(df["area_acres"].fillna(0))
        n += 1

    print(f"  Added {n} interaction features")
    return df


# ────────────────────────────────────────────────────────────
# Step 11: Clean
# ────────────────────────────────────────────────────────────

def clean_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """Apply cleaning rules."""
    print("\n" + "=" * 70)
    print("STEP 11: Cleaning dataset")
    print("=" * 70)

    n_start = len(df)

    # Sensor bounds
    bounds = {
        "water_temp_c": (-2, 45),
        "air_temp_c": (-50, 60),
        "pressure_mb": (850, 1100),
        "wind_speed_kph": (0, 250),
        "median_weight_lb": (0.5, 35),
        "discharge_cfs": (0, 2_000_000),
        "gage_height_ft": (-20, 120),
    }
    clipped = 0
    for col, (lo, hi) in bounds.items():
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        bad = s.notna() & ((s < lo) | (s > hi))
        if bad.any():
            df.loc[bad, col] = np.nan
            clipped += bad.sum()

    if clipped:
        print(f"  Clipped {clipped} out-of-bounds values")

    # Remove rows without valid target
    df = df[df.median_weight_lb.notna()].reset_index(drop=True)
    print(f"  Rows: {n_start} → {len(df)} (dropped {n_start - len(df)} invalid)")

    # Deduplicate
    n_before = len(df)
    df = df.drop_duplicates(subset=["location", "date"], keep="first").reset_index(drop=True)
    if len(df) < n_before:
        print(f"  Deduped: removed {n_before - len(df)} duplicates")

    return df


# ────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────

def build_v7(skip_geocoding: bool = False, skip_weather: bool = False) -> pd.DataFrame:
    """Build the v7 maximum dataset."""
    print("=" * 70)
    print("BUILDING v7 MAXIMUM DATASET")
    print("=" * 70)

    # 1. Merge all outcomes
    df = merge_all_outcomes()

    # 2. Geocode
    if skip_geocoding:
        # Load from cache
        cache_path = RAW / "geocode_cache.json"
        if cache_path.exists():
            with open(cache_path) as f:
                cache = json.load(f)
            df["lat"] = df.location.map(lambda x: cache.get(x, {}).get("lat", np.nan))
            df["lon"] = df.location.map(lambda x: cache.get(x, {}).get("lon", np.nan))
            n = df.lat.notna().sum()
            print(f"\n  Loaded geocode cache: {n}/{len(df)} rows with coords")
            df = df[df.lat.notna()].reset_index(drop=True)
        else:
            df = geocode_locations(df)
    else:
        df = geocode_locations(df)

    # 3. Open-Meteo weather
    if skip_weather:
        # Load from cache
        cache_path = RAW / "openmeteo_weather_cache.csv"
        if cache_path.exists():
            cached = pd.read_csv(cache_path)
            cached["_lat_r"] = cached.lat.round(2)
            cached["_lon_r"] = cached.lon.round(2)
            cached["date"] = cached.date.astype(str)
            df["_lat_r"] = df.lat.round(2)
            df["_lon_r"] = df.lon.round(2)
            om_cols = [c for c in cached.columns if c.startswith("om_")]
            merge_df = cached[["_lat_r", "_lon_r", "date"] + om_cols].drop_duplicates(
                subset=["_lat_r", "_lon_r", "date"], keep="last"
            )
            df = df.merge(merge_df, on=["_lat_r", "_lon_r", "date"], how="left")
            df = df.drop(columns=["_lat_r", "_lon_r"], errors="ignore")
            if "om_pressure_max" in df.columns and "om_pressure_min" in df.columns:
                df["om_pressure_delta_24h"] = df["om_pressure_max"] - df["om_pressure_min"]
            if "om_air_temp_mean" in df.columns:
                df["om_est_water_temp"] = df["om_air_temp_mean"] * 0.663 + 7.16
            n = df[om_cols[0]].notna().sum() if om_cols else 0
            print(f"\n  Loaded weather cache: {n}/{len(df)} rows with weather")
    else:
        df = fetch_openmeteo_weather(df)

    # 4. USGS data
    df = merge_usgs_data(df)

    # 5. Creel features
    df = add_creel_features(df)

    # 6. Morphometry
    df = add_morphometry(df)

    # 7. Temporal features
    df = add_temporal_features(df)

    # 8. Fill weather gaps
    df = fill_weather_gaps(df)

    # 9. Biology features
    df = add_biology_features(df)

    # 10. Interaction features
    df = add_interaction_features(df)

    # 11. Clean
    df = clean_dataset(df)

    # Final report
    print("\n" + "=" * 70)
    print("v7 DATASET SUMMARY")
    print("=" * 70)
    print(f"  Rows: {len(df)}")
    print(f"  Columns: {len(df.columns)}")
    print(f"  Locations: {df.location.nunique()}")
    print(f"  Date range: {df.date.min()} to {df.date.max()}")
    print(f"  Sources: {df.source.value_counts().to_dict()}")

    key_cols = [
        "median_weight_lb", "water_temp_c", "air_temp_c", "pressure_mb",
        "wind_speed_kph", "lat", "lon", "area_acres", "creel_cpue_mean",
        "creel_smb_ratio", "creel_lmb_ratio",
    ]
    print("\n  Feature coverage:")
    for col in key_cols:
        if col in df.columns:
            pct = 100 * df[col].notna().mean()
            print(f"    {col:<25s}: {pct:5.1f}%")

    # Save
    output_path = ASSEMBLED / "validation_dataset_v7.csv"
    df.to_csv(output_path, index=False)
    print(f"\n  Saved to {output_path}")

    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-geocoding", action="store_true")
    parser.add_argument("--skip-weather", action="store_true")
    args = parser.parse_args()
    build_v7(skip_geocoding=args.skip_geocoding, skip_weather=args.skip_weather)
