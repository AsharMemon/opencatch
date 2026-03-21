#!/usr/bin/env python3
"""
Expand regime feature coverage for tournament events.

Fetches hourly ASOS data from Iowa Environmental Mesonet for events
missing regime features, computes regime classifications and trends,
and appends to the existing regime features CSV.

Checkpoints after every batch of 50 events.
"""

import pandas as pd
import numpy as np
import requests
import time
import json
import os
import sys
import math
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────
RAW = Path("/workspace/castline/raw")
REGIME_PATH = RAW / "tournament_regime_features.csv"
WEATHER_PATH = RAW / "tournament_weather_daily.csv"
STATION_CACHE = RAW / "iem_asos_stations.csv"
CHECKPOINT = RAW / "regime_expansion_checkpoint.csv"
LOG_FILE = RAW / "regime_expansion.log"

BATCH_SIZE = 50
RATE_LIMIT = 0.5  # seconds between API calls
MAX_STATION_DIST_KM = 120  # skip if nearest station is too far
REQUEST_TIMEOUT = 20  # seconds per API call

# ── logging ────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── station discovery ──────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = la2 - la1
    dlon = lo2 - lo1
    a = math.sin(dlat/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def fetch_asos_station_list():
    """Fetch ASOS station list from IEM for all US networks."""
    if STATION_CACHE.exists():
        log(f"Loading cached station list from {STATION_CACHE}")
        return pd.read_csv(STATION_CACHE)

    log("Fetching ASOS station list from IEM...")
    all_stations = []
    # US state ASOS networks
    networks = [f"{s}_ASOS" for s in [
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
        "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
        "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
        "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
        "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY",
    ]]

    for net in networks:
        url = f"https://mesonet.agron.iastate.edu/geojson/network/{net}.geojson"
        try:
            r = requests.get(url, timeout=30)
            if r.status_code != 200:
                continue
            data = r.json()
            for feat in data.get("features", []):
                props = feat.get("properties", {})
                coords = feat.get("geometry", {}).get("coordinates", [None, None])
                all_stations.append({
                    "station_id": props.get("sid", ""),
                    "station_name": props.get("sname", ""),
                    "lat": coords[1],
                    "lon": coords[0],
                    "network": net,
                })
            time.sleep(0.3)
        except Exception as e:
            log(f"  Warning: failed to fetch {net}: {e}")
            continue

    df = pd.DataFrame(all_stations).dropna(subset=["lat", "lon"])
    df.to_csv(STATION_CACHE, index=False)
    log(f"Cached {len(df)} ASOS stations")
    return df


def find_nearest_stations(lat, lon, stations_df, n=3):
    """Find N nearest ASOS stations to a given lat/lon. Returns list of (station_id, dist_km)."""
    dists = stations_df.apply(
        lambda r: haversine(lat, lon, r["lat"], r["lon"]), axis=1
    )
    top_idx = dists.nsmallest(n).index
    return [(stations_df.loc[i, "station_id"], dists[i]) for i in top_idx]


# ── HTTP session for connection pooling ────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "castline-regime-fetcher/1.0"})

# ── hourly data fetch ─────────────────────────────────────────────
def fetch_hourly_asos(station_id, start_date, end_date):
    """Fetch hourly ASOS data from IEM for a station and date range."""
    url = (
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
        f"?station={station_id}"
        "&data=all&tz=UTC&format=comma&latlon=yes"
        f"&year1={start_date.year}&month1={start_date.month}&day1={start_date.day}"
        f"&year2={end_date.year}&month2={end_date.month}&day2={end_date.day}"
    )
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                log(f"  HTTP {r.status_code} for {station_id}, attempt {attempt+1}")
                time.sleep(2)
                continue
            text = r.text
            # IEM puts a comment header line starting with #
            lines = [l for l in text.strip().split("\n") if not l.startswith("#")]
            if len(lines) < 2:
                return None
            csv_text = "\n".join(lines)
            df = pd.read_csv(StringIO(csv_text))
            if "valid" not in df.columns:
                return None
            df["valid"] = pd.to_datetime(df["valid"], errors="coerce")
            df = df.dropna(subset=["valid"]).sort_values("valid").reset_index(drop=True)
            return df
        except requests.exceptions.Timeout:
            log(f"  Timeout for {station_id}, attempt {attempt+1}")
            time.sleep(2)
            continue
        except Exception as e:
            log(f"  Error fetching {station_id}: {e}")
            return None
    return None


# ── regime computation ─────────────────────────────────────────────
def compute_regime_features(hourly_df, event_date):
    """Compute regime features from hourly ASOS data."""
    if hourly_df is None or len(hourly_df) < 6:
        return None

    # Parse event date as end of day
    event_dt = pd.Timestamp(event_date)
    event_end = event_dt + timedelta(hours=23, minutes=59)

    # Filter to data up to and including event date
    mask = hourly_df["valid"] <= event_end
    df = hourly_df[mask].copy()
    if len(df) < 6:
        return None

    # ── Extract pressure, temp, wind columns ──
    # IEM ASOS columns: alti (altimeter in inHg), tmpf (temp F), drct (wind dir), sknt (wind speed kt), p01i (precip in)
    # Convert altimeter (inHg) to sea-level pressure (hPa): 1 inHg = 33.8639 hPa
    # Note: IEM uses "M" for missing; check actual numeric content, not just column existence
    df["pressure_hpa"] = np.nan
    if "mslp" in df.columns:
        mslp_vals = pd.to_numeric(df["mslp"], errors="coerce")
        if mslp_vals.notna().sum() >= 6:
            df["pressure_hpa"] = mslp_vals
    if df["pressure_hpa"].notna().sum() < 6 and "alti" in df.columns:
        alti_vals = pd.to_numeric(df["alti"], errors="coerce") * 33.8639
        if alti_vals.notna().sum() >= 6:
            df["pressure_hpa"] = alti_vals
    if df["pressure_hpa"].notna().sum() < 6:
        return None

    # Temperature: convert F to C
    if "tmpf" in df.columns:
        df["temp_c"] = (pd.to_numeric(df["tmpf"], errors="coerce") - 32) * 5 / 9
    else:
        df["temp_c"] = np.nan

    # Wind direction
    if "drct" in df.columns:
        df["wind_dir"] = pd.to_numeric(df["drct"], errors="coerce")
    else:
        df["wind_dir"] = np.nan

    # Precipitation
    if "p01i" in df.columns:
        df["precip_in"] = pd.to_numeric(df["p01i"], errors="coerce").fillna(0)
    else:
        df["precip_in"] = 0.0

    # Drop rows with no pressure
    df = df.dropna(subset=["pressure_hpa"])
    if len(df) < 6:
        return None

    # ── Get the last N hours of data ──
    last_time = df["valid"].max()

    def get_window(hours):
        cutoff = last_time - timedelta(hours=hours)
        return df[df["valid"] >= cutoff]

    w6 = get_window(6)
    w12 = get_window(12)
    w24 = get_window(24)
    w72 = df  # full window

    # ── Pressure trends ──
    pressure_trend_6h = np.nan
    pressure_trend_12h = np.nan
    if len(w6) >= 2:
        pressure_trend_6h = round(w6["pressure_hpa"].iloc[-1] - w6["pressure_hpa"].iloc[0], 2)
    if len(w12) >= 2:
        pressure_trend_12h = round(w12["pressure_hpa"].iloc[-1] - w12["pressure_hpa"].iloc[0], 2)

    # ── Temperature trend ──
    temp_trend_6h = np.nan
    if len(w6) >= 2 and w6["temp_c"].notna().sum() >= 2:
        valid_temps = w6.dropna(subset=["temp_c"])
        if len(valid_temps) >= 2:
            temp_trend_6h = round(valid_temps["temp_c"].iloc[-1] - valid_temps["temp_c"].iloc[0], 1)

    # ── Wind shift magnitude ──
    wind_shift_magnitude = np.nan
    if w24["wind_dir"].notna().sum() >= 4:
        wind_dirs = w24["wind_dir"].dropna().values
        max_shift = 0
        for i in range(len(wind_dirs) - 1):
            diff = abs(wind_dirs[i+1] - wind_dirs[i])
            if diff > 180:
                diff = 360 - diff
            max_shift = max(max_shift, diff)
        wind_shift_magnitude = round(max_shift, 0)

    # ── Stability index (0-1, 1=very stable) ──
    stability_index = np.nan
    if len(w24) >= 4:
        p_std = w24["pressure_hpa"].std()
        # Typical pressure std: 0-10 hPa over 24h
        # Map: 0 std -> 1.0 stability, 10+ std -> 0.0
        stability_index = round(max(0, 1.0 - p_std / 10.0), 3)

    # ── Hours since precipitation ──
    hours_since_precip = np.nan
    precip_rows = w72[w72["precip_in"] > 0.005]
    if len(precip_rows) > 0:
        last_precip = precip_rows["valid"].max()
        hours_since_precip = round((last_time - last_precip).total_seconds() / 3600, 0)
    elif len(w72) > 0:
        # No precip in entire window
        hours_since_precip = round((last_time - w72["valid"].min()).total_seconds() / 3600, 0)

    # ── Front detection and hours since front ──
    hours_since_front = np.nan
    # A front is detected by: rapid pressure change (>3 hPa/3h) + temp shift (>3°C/3h) + wind shift (>45°)
    if len(w72) >= 6:
        times = w72["valid"].values
        pressures = w72["pressure_hpa"].values
        temps = w72["temp_c"].values
        winds = w72["wind_dir"].values

        front_times = []
        for i in range(3, len(w72)):
            # Look back ~3 hours (3 obs if hourly)
            dp = abs(pressures[i] - pressures[max(0, i-3)]) if not (np.isnan(pressures[i]) or np.isnan(pressures[max(0,i-3)])) else 0
            dt_val = abs(temps[i] - temps[max(0, i-3)]) if not (np.isnan(temps[i]) or np.isnan(temps[max(0,i-3)])) else 0
            dw = 0
            if not (np.isnan(winds[i]) or np.isnan(winds[max(0, i-3)])):
                dw = abs(winds[i] - winds[max(0, i-3)])
                if dw > 180:
                    dw = 360 - dw

            # Relaxed front detection: pressure change >2.5 hPa AND (temp shift >2°C OR wind shift >40°)
            if dp > 2.5 and (dt_val > 2.0 or dw > 40):
                front_times.append(pd.Timestamp(times[i]))

        if front_times:
            last_front = max(front_times)
            hours_since_front = round((last_time - last_front).total_seconds() / 3600, 0)

    # ── Regime classification ──
    regime = classify_regime(pressure_trend_6h, pressure_trend_12h, temp_trend_6h,
                             hours_since_front, stability_index)

    return {
        "pressure_trend_6h": pressure_trend_6h,
        "pressure_trend_12h": pressure_trend_12h,
        "temp_trend_6h": temp_trend_6h,
        "wind_shift_magnitude": wind_shift_magnitude,
        "stability_index": stability_index,
        "hours_since_front": hours_since_front,
        "hours_since_precip": hours_since_precip,
        "regime_at_event": regime,
    }


def classify_regime(p6, p12, t6, hsf, stab):
    """Classify weather regime from computed features."""
    if pd.isna(p6) or pd.isna(p12):
        return ""

    # Frontal passage: front within last 6 hours
    if not pd.isna(hsf) and hsf <= 6:
        return "frontal_passage"

    # Post-frontal early: 6-18 hours after front
    if not pd.isna(hsf) and 6 < hsf <= 18:
        return "post_frontal_early"

    # Post-frontal late: 18-36 hours after front
    if not pd.isna(hsf) and 18 < hsf <= 36:
        return "post_frontal_late"

    # Rising: pressure rising >2 hPa in 12h
    if p12 > 2.0:
        if not pd.isna(stab) and stab > 0.7:
            return "stable_high"
        return "rising"

    # Falling: pressure falling >2 hPa in 12h
    if p12 < -2.0:
        return "falling"

    # Stable: low variability
    if not pd.isna(stab) and stab > 0.8:
        if p12 >= 0:
            return "stable_high"
        else:
            return "stable_low"

    # Default
    if p12 >= 0:
        return "rising"
    return "falling"


# ── main pipeline ──────────────────────────────────────────────────
def main():
    log("=" * 60)
    log("Starting regime feature expansion")

    # Load data
    weather_df = pd.read_csv(WEATHER_PATH)
    log(f"Master weather: {len(weather_df)} events")

    existing_df = pd.read_csv(REGIME_PATH)
    existing_ids = set(existing_df["event_id"].unique())
    log(f"Existing regime data: {len(existing_ids)} events")

    # Load checkpoint if exists
    checkpoint_ids = set()
    if CHECKPOINT.exists():
        cp = pd.read_csv(CHECKPOINT)
        checkpoint_ids = set(cp["event_id"].unique())
        log(f"Checkpoint has {len(checkpoint_ids)} events")
        existing_ids = existing_ids | checkpoint_ids

    # Find events needing regime data
    missing = weather_df[~weather_df["event_id"].isin(existing_ids)].copy()
    log(f"Events needing regime data: {len(missing)}")

    if len(missing) == 0:
        log("Nothing to do!")
        return

    # Fetch station list for fallback lookups
    stations_df = fetch_asos_station_list()
    log(f"Available ASOS stations: {len(stations_df)}")

    # Build station candidates: use weather file's station_id first, then nearest from cache
    log("Building station candidate lists...")
    loc_key = missing.groupby(["lat", "lon"]).first()[["station_id", "station_distance_km"]].reset_index()
    station_map = {}
    for _, row in loc_key.iterrows():
        key = (round(row["lat"], 6), round(row["lon"], 6))
        # Primary: station from weather file (already known to work for daily data)
        primary = (row["station_id"], row["station_distance_km"])
        # Fallbacks: nearest from ASOS cache, excluding the primary
        fallbacks = find_nearest_stations(row["lat"], row["lon"], stations_df, n=4)
        fallbacks = [(sid, d) for sid, d in fallbacks if sid != row["station_id"]][:3]
        station_map[key] = [primary] + fallbacks
    log(f"Mapped {len(station_map)} unique locations (primary + 3 fallbacks)")

    # Process in batches
    results = []
    total = len(missing)
    success = 0
    skipped = 0
    failed = 0
    api_cache = {}  # cache: (station_id, date_key) -> hourly_df

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = missing.iloc[batch_start:batch_end]
        log(f"Processing batch {batch_start//BATCH_SIZE + 1}: events {batch_start+1}-{batch_end} of {total}")

        batch_results = []
        for evt_idx, (_, event) in enumerate(batch.iterrows()):
            eid = event["event_id"]
            lat, lon = event["lat"], event["lon"]
            date_str = event["date"]

            key = (round(lat, 6), round(lon, 6))
            candidates = station_map.get(key, [])

            if not candidates or candidates[0][1] > MAX_STATION_DIST_KM:
                skipped += 1
                continue

            event_date = pd.Timestamp(date_str)
            start_date = event_date - timedelta(days=3)
            end_date = event_date + timedelta(days=1)

            # Try each candidate station until we get data
            hourly_df = None
            station_id = None
            station_dist = None
            for cand_id, cand_dist in candidates:
                if cand_dist > MAX_STATION_DIST_KM:
                    break
                cache_key = (cand_id, start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d"))
                cached = cache_key in api_cache
                if cached:
                    hdf = api_cache[cache_key]
                else:
                    hdf = fetch_hourly_asos(cand_id, start_date, end_date)
                    api_cache[cache_key] = hdf
                    time.sleep(RATE_LIMIT)

                n_obs = len(hdf) if hdf is not None else 0
                if n_obs >= 6:
                    hourly_df = hdf
                    station_id = cand_id
                    station_dist = cand_dist
                    log(f"  [{batch_start + evt_idx + 1}/{total}] {eid[:50]} -> {cand_id} ({cand_dist:.0f}km) {'CACHE' if cached else f'{n_obs} obs'}")
                    break
                else:
                    log(f"  [{batch_start + evt_idx + 1}/{total}] {eid[:50]} -> {cand_id} ({cand_dist:.0f}km) {n_obs} obs, trying next...")

            if hourly_df is None or station_id is None:
                failed += 1
                continue

            features = compute_regime_features(hourly_df, event_date)
            if features is None:
                failed += 1
                continue

            row = {
                "event_id": eid,
                "date": date_str,
                "regime_at_event": features["regime_at_event"],
                "hours_since_front": features["hours_since_front"],
                "hours_since_precip": features["hours_since_precip"],
                "pressure_trend_6h": features["pressure_trend_6h"],
                "pressure_trend_12h": features["pressure_trend_12h"],
                "temp_trend_6h": features["temp_trend_6h"],
                "wind_shift_magnitude": features["wind_shift_magnitude"],
                "stability_index": features["stability_index"],
                "regime_station_id": station_id,
                "regime_station_dist_km": round(station_dist, 1),
            }
            batch_results.append(row)
            success += 1

        # Save checkpoint after each batch
        if batch_results:
            results.extend(batch_results)
            cp_df = pd.DataFrame(results)
            cp_df.to_csv(CHECKPOINT, index=False)
            log(f"  Batch done. Success: {success}, Skipped: {skipped}, Failed: {failed}. Checkpointed {len(results)} new rows.")

    # ── Merge and save ──
    log("Merging results with existing data...")
    if results:
        new_df = pd.DataFrame(results)
        # Load checkpoint data (may include rows from previous runs)
        if CHECKPOINT.exists():
            cp_all = pd.read_csv(CHECKPOINT)
            new_df = cp_all

        # Combine with existing
        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["event_id"], keep="last")
        combined.to_csv(REGIME_PATH, index=False)
        log(f"Final regime file: {len(combined)} events (was {len(existing_df)})")

        coverage = len(combined) / len(weather_df) * 100
        log(f"Coverage: {coverage:.1f}% of {len(weather_df)} events")
    else:
        log("No new results to merge.")

    log("Done!")


if __name__ == "__main__":
    main()
