#!/usr/bin/env python3
"""
Weather regime detection feature engineering for CASTLINE.

Fetches hourly ASOS data around each tournament event and classifies
meteorological regimes (pre-frontal, frontal passage, post-frontal, stable,
storm approach). Generates features like hours_since_front and stability_index
that gradient-boosted trees can exploit far better than raw pressure deltas.

Bass feeding behaviour is strongly modulated by frontal passage timing:
- Pre-frontal: aggressive topwater feeding (falling pressure, south wind)
- Frontal passage: lockjaw period (rapid pressure swing, precip, wind shift)
- Post-frontal early (0-12h): suppressed surface, fish push deep
- Post-frontal late (12-36h): stable high pressure, fish resume feeding
- Stable: normal patterns, solunar/structure dependent
- Storm approach: extreme pressure drop, erratic fish behaviour

IEM has NO rate limits, but we use small delays to be polite.
"""

import json
import logging
import math
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Paths (Vast.ai layout)
# ---------------------------------------------------------------------------
BASE_DIR = Path("/workspace/castline")
RAW_DIR = BASE_DIR / "raw"
LOG_DIR = BASE_DIR / "logs"

WEATHER_CSV = RAW_DIR / "tournament_weather_daily.csv"
STATIONS_CSV = RAW_DIR / "iem_asos_stations.csv"
OUTPUT_CSV = RAW_DIR / "tournament_regime_features.csv"
CHECKPOINT_CSV = RAW_DIR / "tournament_regime_features.checkpoint.csv"
HOURLY_CACHE_DIR = RAW_DIR / "hourly_cache"

ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(parents=True, exist_ok=True)
HOURLY_CACHE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "regime_detection.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def angular_diff(a, b):
    """Signed angular difference in degrees, range [-180, 180]."""
    d = (a - b) % 360
    if d > 180:
        d -= 360
    return d


def circular_mean(angles):
    """Circular mean of angles in degrees."""
    rads = np.radians(angles)
    s = np.nanmean(np.sin(rads))
    c = np.nanmean(np.cos(rads))
    return np.degrees(np.arctan2(s, c)) % 360


# ---------------------------------------------------------------------------
# Station lookup
# ---------------------------------------------------------------------------

_stations_df = None


def load_stations():
    global _stations_df
    if _stations_df is not None:
        return _stations_df
    _stations_df = pd.read_csv(STATIONS_CSV)
    log.info("Loaded %d ASOS stations", len(_stations_df))
    return _stations_df


def find_nearest_station(lat, lon, n=3):
    """Return n nearest ASOS station IDs + distances."""
    stations = load_stations()
    dists = stations.apply(
        lambda r: haversine_km(lat, lon, r["lat"], r["lon"]), axis=1
    )
    nearest_idx = dists.nsmallest(n).index
    result = stations.loc[nearest_idx].copy()
    result["distance_km"] = dists.loc[nearest_idx].values
    return result


# ---------------------------------------------------------------------------
# Fetch hourly ASOS data
# ---------------------------------------------------------------------------

def fetch_hourly_asos(station_id, start_dt, end_dt):
    """
    Fetch hourly ASOS observations.  Returns DataFrame with columns:
    valid (datetime), tmpf, mslp, drct, sknt, p01i.
    Uses file cache keyed by station+date range.
    """
    cache_key = f"{station_id}_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}"
    cache_path = HOURLY_CACHE_DIR / f"{cache_key}.csv"

    if cache_path.exists():
        df = pd.read_csv(cache_path, parse_dates=["valid"])
        return df

    params = {
        "station": station_id,
        "data": ["tmpf", "mslp", "drct", "sknt", "p01i", "alti"],
        "year1": start_dt.year, "month1": start_dt.month, "day1": start_dt.day,
        "year2": end_dt.year, "month2": end_dt.month, "day2": end_dt.day,
        "tz": "UTC",
        "format": "onlycomma",
        "latlon": "yes",
        "missing": "M",
        "trace": "T",
        "report_type": ["3", "4"],
    }

    try:
        resp = requests.get(ASOS_URL, params=params, timeout=45)
        if resp.status_code != 200:
            log.warning("HTTP %d for station %s", resp.status_code, station_id)
            return None

        text = resp.text.strip()
        if len(text) < 30 or text.startswith("<!DOCTYPE"):
            return None

        # Filter comment lines
        lines = [l for l in text.split("\n") if not l.startswith("#")]
        text = "\n".join(lines)

        df = pd.read_csv(StringIO(text), na_values=["M"])
        for col in ["tmpf", "mslp", "drct", "sknt", "p01i", "alti"]:
            if col in df.columns:
                df[col] = df[col].replace("T", 0.001)
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "valid" in df.columns:
            df["valid"] = pd.to_datetime(df["valid"], errors="coerce")
            df = df.dropna(subset=["valid"])
            df = df.sort_values("valid").reset_index(drop=True)

        # Fill mslp from altimeter if needed (alti in inHg -> hPa)
        if "mslp" in df.columns and "alti" in df.columns:
            mask = df["mslp"].isna() & df["alti"].notna()
            df.loc[mask, "mslp"] = df.loc[mask, "alti"] * 33.8639

        # Cache
        if len(df) > 0:
            df.to_csv(cache_path, index=False)

        return df

    except Exception as e:
        log.warning("Fetch failed for %s: %s", station_id, e)
        return None


def get_hourly_for_event(lat, lon, event_date_str, hours_before=48):
    """
    Get hourly weather for an event location/date.
    Tries up to 3 nearest stations. Returns DataFrame or None.
    """
    event_dt = datetime.strptime(event_date_str, "%Y-%m-%d")
    start_dt = event_dt - timedelta(hours=hours_before)
    end_dt = event_dt + timedelta(days=1)  # through end of event day

    nearest = find_nearest_station(lat, lon, n=3)

    for _, st in nearest.iterrows():
        sid = st["station_id"]
        df = fetch_hourly_asos(sid, start_dt, end_dt)
        time.sleep(0.1)  # be polite

        if df is not None and len(df) >= 6:  # need at least 6 observations
            # Filter to our window
            df = df[(df["valid"] >= start_dt) & (df["valid"] <= end_dt)]
            if len(df) >= 6:
                df["station_id"] = sid
                df["station_distance_km"] = st["distance_km"]
                return df

    return None


# ---------------------------------------------------------------------------
# Resample to regular hourly grid
# ---------------------------------------------------------------------------

def resample_hourly(df):
    """
    Resample sub-hourly ASOS obs to regular hourly grid.
    Returns DataFrame indexed by hour with columns: mslp, tmpf, drct, sknt, p01i.
    """
    if df is None or len(df) == 0:
        return None

    df = df.set_index("valid")

    hourly = pd.DataFrame()
    hourly["mslp"] = df["mslp"].resample("1h").mean()
    hourly["tmpf"] = df["tmpf"].resample("1h").mean()
    hourly["sknt"] = df["sknt"].resample("1h").mean()
    hourly["p01i"] = df["p01i"].resample("1h").sum()  # precip is cumulative

    # Wind direction: circular mean per hour (aligned to same index as hourly)
    if "drct" in df.columns:
        drct_series = df["drct"].resample("1h").apply(
            lambda x: circular_mean(x.dropna().values) if x.dropna().shape[0] > 0 else np.nan
        )
        hourly["drct"] = drct_series

    # Forward/backward fill small gaps (up to 2 hours)
    hourly = hourly.ffill(limit=2).bfill(limit=2)

    return hourly


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def detect_frontal_passages(hourly_df):
    """
    Detect frontal passages in hourly data.
    Returns list of (timestamp, front_type) tuples.

    A frontal passage is identified by:
    1. Pressure drop then rapid rise (>=3 mb in 6h swing)
    2. Wind direction shift >= 45 degrees in 3 hours
    3. Optional: precipitation spike
    """
    if hourly_df is None or len(hourly_df) < 6:
        return []

    fronts = []
    mslp = hourly_df["mslp"]
    drct = hourly_df.get("drct", pd.Series(dtype=float))
    p01i = hourly_df.get("p01i", pd.Series(dtype=float))

    # Rolling pressure changes
    p_delta_3h = mslp.diff(3)   # 3-hour change
    p_delta_6h = mslp.diff(6)   # 6-hour change

    for i in range(6, len(hourly_df)):
        ts = hourly_df.index[i]

        # Look for pressure trough: falling then rising
        # Check if pressure was dropping in previous 6h and now rising
        p_before = p_delta_6h.iloc[i-3] if i >= 9 else np.nan  # 6h trend 3h ago
        p_now = p_delta_3h.iloc[i] if i < len(p_delta_3h) else np.nan

        # Frontal passage signature: was falling, now rising
        if pd.notna(p_before) and pd.notna(p_now):
            was_falling = p_before < -1.5  # was falling >1.5mb/6h
            now_rising = p_now > 1.0       # now rising >1mb/3h

            # Wind shift check
            wind_shifted = False
            if len(drct) > i and i >= 3:
                old_wind = drct.iloc[i-3]
                new_wind = drct.iloc[i]
                if pd.notna(old_wind) and pd.notna(new_wind):
                    shift = abs(angular_diff(new_wind, old_wind))
                    wind_shifted = shift >= 45

            # Precip check
            had_precip = False
            if len(p01i) > i:
                recent_precip = p01i.iloc[max(0,i-3):i+1].sum()
                had_precip = recent_precip > 0.01  # any measurable precip

            # Score the front detection
            score = 0
            if was_falling and now_rising:
                score += 2
            elif was_falling or now_rising:
                score += 1
            if wind_shifted:
                score += 1
            if had_precip:
                score += 0.5

            if score >= 2:
                fronts.append((ts, "frontal_passage", score))

    # Deduplicate: keep strongest front within 6-hour windows
    if len(fronts) <= 1:
        return fronts

    deduped = [fronts[0]]
    for ts, ftype, score in fronts[1:]:
        if (ts - deduped[-1][0]).total_seconds() > 6 * 3600:
            deduped.append((ts, ftype, score))
        elif score > deduped[-1][2]:
            deduped[-1] = (ts, ftype, score)

    return deduped


def classify_regime(hourly_df, target_ts, fronts):
    """
    Classify the weather regime at a specific timestamp.

    Returns dict with regime classification and features.
    """
    result = {
        "regime_at_event": "stable",
        "hours_since_front": np.nan,
        "hours_since_precip": np.nan,
        "pressure_trend_6h": np.nan,
        "pressure_trend_12h": np.nan,
        "temp_trend_6h": np.nan,
        "wind_shift_magnitude": np.nan,
        "stability_index": np.nan,
    }

    if hourly_df is None or len(hourly_df) < 6:
        return result

    mslp = hourly_df["mslp"]
    tmpf = hourly_df.get("tmpf", pd.Series(dtype=float))
    drct = hourly_df.get("drct", pd.Series(dtype=float))
    sknt = hourly_df.get("sknt", pd.Series(dtype=float))
    p01i = hourly_df.get("p01i", pd.Series(dtype=float))

    # Find nearest timestamp in data
    idx = hourly_df.index.get_indexer([target_ts], method="nearest")[0]
    if idx < 0 or idx >= len(hourly_df):
        return result

    # --- Pressure trends ---
    if idx >= 6 and pd.notna(mslp.iloc[idx]) and pd.notna(mslp.iloc[idx - 6]):
        result["pressure_trend_6h"] = round(mslp.iloc[idx] - mslp.iloc[idx - 6], 2)

    if idx >= 12 and pd.notna(mslp.iloc[idx]) and pd.notna(mslp.iloc[idx - 12]):
        result["pressure_trend_12h"] = round(mslp.iloc[idx] - mslp.iloc[idx - 12], 2)

    # --- Temperature trend ---
    if idx >= 6 and pd.notna(tmpf.iloc[idx]) and pd.notna(tmpf.iloc[idx - 6]):
        result["temp_trend_6h"] = round(tmpf.iloc[idx] - tmpf.iloc[idx - 6], 2)

    # --- Wind shift magnitude (max direction change in 12h window) ---
    wind_start = max(0, idx - 12)
    wind_slice = drct.iloc[wind_start:idx + 1].dropna()
    if len(wind_slice) >= 2:
        max_shift = 0
        vals = wind_slice.values
        for j in range(1, len(vals)):
            shift = abs(angular_diff(vals[j], vals[j - 1]))
            max_shift = max(max_shift, shift)
        result["wind_shift_magnitude"] = round(max_shift, 1)

    # --- Hours since last precipitation ---
    if len(p01i) > 0:
        precip_before = p01i.iloc[:idx + 1]
        precip_times = precip_before[precip_before > 0.005].index
        if len(precip_times) > 0:
            last_precip = precip_times[-1]
            hours_since = (hourly_df.index[idx] - last_precip).total_seconds() / 3600
            result["hours_since_precip"] = round(hours_since, 1)
        else:
            result["hours_since_precip"] = 48.0  # no precip in window

    # --- Hours since front and regime classification ---
    if fronts:
        # Find most recent front before or at target
        recent_front = None
        for fts, ftype, fscore in fronts:
            if fts <= hourly_df.index[idx]:
                recent_front = (fts, ftype, fscore)

        if recent_front is not None:
            hours_since = (hourly_df.index[idx] - recent_front[0]).total_seconds() / 3600
            result["hours_since_front"] = round(hours_since, 1)

            if hours_since <= 3:
                result["regime_at_event"] = "frontal_passage"
            elif hours_since <= 12:
                result["regime_at_event"] = "post_frontal_early"
            elif hours_since <= 36:
                result["regime_at_event"] = "post_frontal_late"
        else:
            result["hours_since_front"] = 48.0  # no front detected in window

    # Now check pre-frontal and storm conditions based on current trends
    p6 = result["pressure_trend_6h"]
    if pd.notna(p6):
        if p6 < -4:
            result["regime_at_event"] = "storm_approach"
        elif p6 < -2 and result["regime_at_event"] == "stable":
            # Falling pressure with south/SW wind = pre-frontal
            if idx < len(drct) and pd.notna(drct.iloc[idx]):
                wind_dir = drct.iloc[idx]
                if 135 <= wind_dir <= 270:  # S to W
                    result["regime_at_event"] = "pre_frontal"
                else:
                    result["regime_at_event"] = "pre_frontal"  # still pre-frontal even without south wind
            else:
                result["regime_at_event"] = "pre_frontal"

    # --- Stability index (0 = very unstable, 1 = very stable) ---
    # Composite of: pressure variability, wind variability, precip
    stability_components = []

    # Pressure stability: std of 12h pressure
    p_window = mslp.iloc[max(0, idx - 12):idx + 1].dropna()
    if len(p_window) >= 3:
        p_std = p_window.std()
        # Map: 0mb std -> 1.0, 5mb std -> 0.0
        stability_components.append(max(0, 1 - p_std / 5))

    # Wind stability: std of wind speed
    w_window = sknt.iloc[max(0, idx - 12):idx + 1].dropna()
    if len(w_window) >= 3:
        w_std = w_window.std()
        stability_components.append(max(0, 1 - w_std / 15))

    # Precip: any recent precip reduces stability
    precip_window = p01i.iloc[max(0, idx - 12):idx + 1]
    if len(precip_window) > 0:
        total_precip = precip_window.sum()
        stability_components.append(max(0, 1 - total_precip / 0.5))

    # Pressure trend magnitude reduces stability
    if pd.notna(p6):
        stability_components.append(max(0, 1 - abs(p6) / 6))

    if stability_components:
        result["stability_index"] = round(np.mean(stability_components), 3)

    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def load_checkpoint():
    """Load checkpoint if exists, return set of completed event_ids."""
    if CHECKPOINT_CSV.exists():
        df = pd.read_csv(CHECKPOINT_CSV)
        log.info("Loaded checkpoint with %d completed events", len(df))
        return df
    return None


def save_checkpoint(results_df):
    """Save intermediate results."""
    results_df.to_csv(CHECKPOINT_CSV, index=False)


def run():
    log.info("=" * 60)
    log.info("CASTLINE Weather Regime Detection")
    log.info("=" * 60)

    # Load tournament weather data
    weather_csv = WEATHER_CSV
    if not weather_csv.exists():
        log.error("Tournament weather not found at %s", weather_csv)
        # Try alternative paths
        alt_paths = [
            RAW_DIR / "combined_weather_history.csv",
            RAW_DIR / "flw_weather_noaa.csv",
        ]
        for alt in alt_paths:
            if alt.exists():
                log.info("Using alternative: %s", alt)
                weather_csv = alt
                break
        else:
            log.error("No weather data found. Exiting.")
            sys.exit(1)

    weather = pd.read_csv(weather_csv)
    log.info("Loaded %d tournament weather rows from %s", len(weather), weather_csv)

    # Ensure required columns
    required = ["event_id", "date", "lat", "lon"]
    missing = [c for c in required if c not in weather.columns]
    if missing:
        log.error("Missing required columns: %s", missing)
        log.info("Available columns: %s", list(weather.columns))
        sys.exit(1)

    # Deduplicate by event_id + date
    weather = weather.drop_duplicates(subset=["event_id", "date"])
    log.info("Unique event-days: %d", len(weather))

    # Load checkpoint
    checkpoint_df = load_checkpoint()
    completed_keys = set()
    all_results = []

    if checkpoint_df is not None:
        completed_keys = set(
            checkpoint_df["event_id"].astype(str) + "_" + checkpoint_df["date"].astype(str)
        )
        all_results = checkpoint_df.to_dict("records")
        log.info("Resuming from checkpoint: %d already done", len(completed_keys))

    # Process each event
    total = len(weather)
    new_count = 0
    fail_count = 0

    for i, (_, row) in enumerate(weather.iterrows()):
        event_id = str(row["event_id"])
        date_str = str(row["date"])
        key = f"{event_id}_{date_str}"

        if key in completed_keys:
            continue

        lat = row.get("lat")
        lon = row.get("lon")

        if pd.isna(lat) or pd.isna(lon):
            log.debug("Skipping %s: no lat/lon", event_id)
            result = {
                "event_id": event_id,
                "date": date_str,
                "regime_at_event": np.nan,
                "hours_since_front": np.nan,
                "hours_since_precip": np.nan,
                "pressure_trend_6h": np.nan,
                "pressure_trend_12h": np.nan,
                "temp_trend_6h": np.nan,
                "wind_shift_magnitude": np.nan,
                "stability_index": np.nan,
                "regime_station_id": np.nan,
                "regime_station_dist_km": np.nan,
            }
            all_results.append(result)
            fail_count += 1
            continue

        # Fetch hourly ASOS data (48h before through event day)
        hourly_raw = get_hourly_for_event(lat, lon, date_str, hours_before=48)

        if hourly_raw is None or len(hourly_raw) < 6:
            log.debug("No hourly data for %s on %s", event_id, date_str)
            result = {
                "event_id": event_id,
                "date": date_str,
                "regime_at_event": np.nan,
                "hours_since_front": np.nan,
                "hours_since_precip": np.nan,
                "pressure_trend_6h": np.nan,
                "pressure_trend_12h": np.nan,
                "temp_trend_6h": np.nan,
                "wind_shift_magnitude": np.nan,
                "stability_index": np.nan,
                "regime_station_id": np.nan,
                "regime_station_dist_km": np.nan,
            }
            all_results.append(result)
            fail_count += 1
            continue

        station_id = hourly_raw["station_id"].iloc[0]
        station_dist = hourly_raw["station_distance_km"].iloc[0]

        # Resample to hourly
        hourly = resample_hourly(hourly_raw)

        if hourly is None or len(hourly) < 6:
            fail_count += 1
            result = {
                "event_id": event_id,
                "date": date_str,
                "regime_at_event": np.nan,
                "hours_since_front": np.nan,
                "hours_since_precip": np.nan,
                "pressure_trend_6h": np.nan,
                "pressure_trend_12h": np.nan,
                "temp_trend_6h": np.nan,
                "wind_shift_magnitude": np.nan,
                "stability_index": np.nan,
                "regime_station_id": station_id,
                "regime_station_dist_km": round(station_dist, 1),
            }
            all_results.append(result)
            continue

        # Detect frontal passages in the full window
        fronts = detect_frontal_passages(hourly)

        # Classify regime at event time (morning of event day ~8am local ≈ 13:00 UTC)
        event_dt = datetime.strptime(date_str, "%Y-%m-%d")
        target_ts = pd.Timestamp(event_dt.replace(hour=13, minute=0))

        regime = classify_regime(hourly, target_ts, fronts)

        result = {
            "event_id": event_id,
            "date": date_str,
            **regime,
            "regime_station_id": station_id,
            "regime_station_dist_km": round(station_dist, 1),
        }
        all_results.append(result)
        new_count += 1

        # Checkpoint every 50 events
        if new_count % 50 == 0:
            df_out = pd.DataFrame(all_results)
            save_checkpoint(df_out)
            log.info(
                "Progress: %d/%d (new: %d, failed: %d, checkpoint saved)",
                i + 1, total, new_count, fail_count,
            )

        if (i + 1) % 25 == 0:
            log.info(
                "Progress: %d/%d (new: %d, failed: %d)",
                i + 1, total, new_count, fail_count,
            )

    # Final output
    df_out = pd.DataFrame(all_results)

    # Summary stats
    log.info("=" * 60)
    log.info("RESULTS")
    log.info("=" * 60)
    log.info("Total events processed: %d", len(df_out))
    log.info("Regime coverage: %d/%d (%.1f%%)",
             df_out["regime_at_event"].notna().sum(), len(df_out),
             100 * df_out["regime_at_event"].notna().sum() / max(1, len(df_out)))

    if "regime_at_event" in df_out.columns:
        regime_dist = df_out["regime_at_event"].value_counts()
        log.info("Regime distribution:")
        for regime, count in regime_dist.items():
            log.info("  %-20s %4d (%.1f%%)", regime, count, 100 * count / len(df_out))

    feat_cols = [
        "hours_since_front", "hours_since_precip", "pressure_trend_6h",
        "pressure_trend_12h", "temp_trend_6h", "wind_shift_magnitude",
        "stability_index",
    ]
    log.info("Feature coverage:")
    for col in feat_cols:
        if col in df_out.columns:
            cov = df_out[col].notna().sum()
            log.info("  %-25s %4d/%d (%.1f%%)", col, cov, len(df_out),
                     100 * cov / max(1, len(df_out)))

    # Save
    df_out.to_csv(OUTPUT_CSV, index=False)
    log.info("Saved regime features to %s", OUTPUT_CSV)

    # Clean up checkpoint
    if CHECKPOINT_CSV.exists():
        os.remove(CHECKPOINT_CSV)
        log.info("Removed checkpoint file")

    return df_out


if __name__ == "__main__":
    run()
