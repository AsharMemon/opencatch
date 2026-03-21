#!/usr/bin/env python3
"""
Fetch USGS gauge data for tournament events.
Improves coverage from ~10.8% to hopefully 50%+.

Strategy:
1. For each unique lat/lon, discover nearby USGS sites with discharge data
2. For each event, fetch daily values from the best (nearest) gauge
3. Compute derived features (deltas, z-scores, flow regime)
4. Save progress every 50 events
"""

import pandas as pd
import numpy as np
import requests
import json
import time
import os
import sys
import math
from datetime import datetime, timedelta
from collections import defaultdict

# Paths
RAW_DIR = "/workspace/castline/raw"
GAUGES_CSV = f"{RAW_DIR}/tournament_usgs_gauges.csv"
SITE_CACHE_FILE = f"{RAW_DIR}/usgs_site_discovery_cache.json"
PROGRESS_FILE = f"{RAW_DIR}/usgs_fetch_progress.json"

USGS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
USGS_DV_URL = "https://waterservices.usgs.gov/nwis/dv/"

RATE_LIMIT = 0.5
PARAM_DISCHARGE = "00060"
PARAM_GAGE_HEIGHT = "00065"
PARAM_WATER_TEMP = "00010"

session = requests.Session()
session.headers.update({
    "User-Agent": "CastlineResearch/1.0 (fishing conditions research)",
    "Accept": "application/json",
})


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def load_site_cache():
    if os.path.exists(SITE_CACHE_FILE):
        with open(SITE_CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_site_cache(cache):
    with open(SITE_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=1)


def discover_sites(lat, lon, cache, radius_deg=0.5):
    """Find USGS sites near a lat/lon with discharge data."""
    cache_key = f"{lat:.3f},{lon:.3f}"
    if cache_key in cache:
        return cache[cache_key]

    bbox = f"{lon-radius_deg:.4f},{lat-radius_deg:.4f},{lon+radius_deg:.4f},{lat+radius_deg:.4f}"
    params = {
        "format": "rdb",
        "bBox": bbox,
        "siteType": "ST,LK,SP,ES",
        "hasDataTypeCd": "dv",
        "parameterCd": PARAM_DISCHARGE,
        "siteStatus": "all",
    }

    try:
        time.sleep(RATE_LIMIT)
        resp = session.get(USGS_SITE_URL, params=params, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Site discovery error for {cache_key}: {e}", flush=True)
        if radius_deg < 1.0:
            return discover_sites(lat, lon, cache, radius_deg=min(radius_deg + 0.25, 1.0))
        cache[cache_key] = []
        return []

    sites = []
    for line in resp.text.split("\n"):
        if line.startswith("#") or line.startswith("agency") or line.startswith("5s"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            try:
                site_no = parts[1].strip()
                site_name = parts[2].strip() if len(parts) > 2 else ""
                site_type = parts[3].strip() if len(parts) > 3 else ""
                site_lat = float(parts[4].strip()) if parts[4].strip() else None
                site_lon = float(parts[5].strip()) if parts[5].strip() else None
                if site_lat and site_lon:
                    dist = haversine_km(lat, lon, site_lat, site_lon)
                    if dist <= 100:
                        sites.append({
                            "site_no": site_no,
                            "name": site_name,
                            "type": site_type,
                            "lat": site_lat,
                            "lon": site_lon,
                            "distance_km": round(dist, 2),
                        })
            except (ValueError, IndexError):
                continue

    sites.sort(key=lambda s: s["distance_km"])
    sites = sites[:10]
    cache[cache_key] = sites

    if sites:
        print(f"  Found {len(sites)} sites near {cache_key}, nearest: {sites[0]['site_no']} ({sites[0]['distance_km']}km)", flush=True)
    else:
        print(f"  No sites found near {cache_key}", flush=True)
        if radius_deg < 1.0:
            return discover_sites(lat, lon, cache, radius_deg=min(radius_deg + 0.25, 1.0))

    return sites


def fetch_daily_values(site_no, date_str, days_context=7):
    """Fetch daily values for a site around a date."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start = (dt - timedelta(days=days_context)).strftime("%Y-%m-%d")
    end = (dt + timedelta(days=1)).strftime("%Y-%m-%d")

    params = {
        "format": "json",
        "sites": site_no,
        "startDT": start,
        "endDT": end,
        "parameterCd": f"{PARAM_DISCHARGE},{PARAM_GAGE_HEIGHT},{PARAM_WATER_TEMP}",
        "siteStatus": "all",
    }

    try:
        time.sleep(RATE_LIMIT)
        resp = session.get(USGS_DV_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    DV fetch error for {site_no}: {e}", flush=True)
        return None

    result = {}
    ts_data = data.get("value", {}).get("timeSeries", [])

    for ts in ts_data:
        var_code = ts.get("variable", {}).get("variableCode", [{}])[0].get("value", "")
        values = ts.get("values", [{}])[0].get("value", [])

        date_vals = {}
        for v in values:
            vdate = v.get("dateTime", "")[:10]
            val = v.get("value")
            if val and val not in ("-999999", "-999999.0", "-999999.00"):
                try:
                    date_vals[vdate] = float(val)
                except ValueError:
                    pass

        if var_code == PARAM_DISCHARGE:
            result["discharge_series"] = date_vals
            result["discharge_cfs"] = date_vals.get(date_str)
        elif var_code == PARAM_GAGE_HEIGHT:
            result["gage_height_series"] = date_vals
            result["gage_height_ft"] = date_vals.get(date_str)
        elif var_code == PARAM_WATER_TEMP:
            result["water_temp_usgs"] = date_vals.get(date_str)

    return result if result else None


def compute_derived(result, date_str):
    """Compute delta and regime features from the time series."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    out = {
        "discharge_cfs": result.get("discharge_cfs"),
        "gage_height_ft": result.get("gage_height_ft"),
        "water_temp_usgs": result.get("water_temp_usgs"),
    }

    discharge_series = result.get("discharge_series", {})
    gage_series = result.get("gage_height_series", {})

    q_today = discharge_series.get(date_str)
    if q_today is not None:
        q_1d = discharge_series.get((dt - timedelta(days=1)).strftime("%Y-%m-%d"))
        q_3d = discharge_series.get((dt - timedelta(days=3)).strftime("%Y-%m-%d"))
        if q_1d is not None:
            out["discharge_delta_1d"] = round(q_today - q_1d, 2)
            if q_1d > 0:
                out["discharge_pct_change_1d"] = round((q_today - q_1d) / q_1d, 4)
        if q_3d is not None:
            out["discharge_delta_3d"] = round(q_today - q_3d, 2)

        vals = [v for v in discharge_series.values() if v is not None]
        if len(vals) >= 3:
            mean_q = np.mean(vals)
            std_q = np.std(vals)
            if std_q > 0:
                out["discharge_zscore"] = round((q_today - mean_q) / std_q, 3)

        if len(vals) >= 3:
            median_q = np.median(vals)
            if q_today > median_q * 2:
                out["flow_regime"] = "high_flow"
            elif q_today < median_q * 0.5:
                out["flow_regime"] = "low_flow"
            elif out.get("discharge_delta_1d") is not None:
                if abs(out["discharge_delta_1d"]) > median_q * 0.3:
                    out["flow_regime"] = "rising" if out["discharge_delta_1d"] > 0 else "falling"
                else:
                    out["flow_regime"] = "stable"

    gh_today = gage_series.get(date_str)
    if gh_today is not None:
        gh_1d = gage_series.get((dt - timedelta(days=1)).strftime("%Y-%m-%d"))
        if gh_1d is not None:
            out["gage_height_delta_1d"] = round(gh_today - gh_1d, 3)

    return out


def main():
    print(f"=== USGS Gauge Fetch - {datetime.now().isoformat()} ===", flush=True)

    df = pd.read_csv(GAUGES_CSV)
    # Ensure gauge_site_no is string (USGS sites have leading zeros)
    df["gauge_site_no"] = df["gauge_site_no"].astype("object")
    df["gauge_site_type"] = df["gauge_site_type"].astype("object")
    df["flow_regime"] = df["flow_regime"].astype("object")
    print(f"Loaded {len(df)} events, {df['discharge_cfs'].notna().sum()} already have discharge", flush=True)

    site_cache = load_site_cache()
    print(f"Site cache has {len(site_cache)} entries", flush=True)

    processed = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            progress = json.load(f)
            processed = set(progress.get("processed", []))
        print(f"Resuming: {len(processed)} events already processed", flush=True)

    needs_data = df[
        (df["discharge_cfs"].isna()) &
        (df["lat"].notna()) &
        (df["lon"].notna()) &
        (~df["event_id"].isin(processed))
    ].copy()
    print(f"Events needing data: {len(needs_data)}", flush=True)

    location_groups = needs_data.groupby(["lat", "lon"]).groups
    print(f"Unique locations to process: {len(location_groups)}", flush=True)

    total_fetched = 0
    total_failed = 0
    batch_count = 0

    for (lat, lon), indices in location_groups.items():
        events = needs_data.loc[indices]
        loc_name = events.iloc[0]["location"]
        print(f"\n--- Location: {loc_name} ({lat:.4f}, {lon:.4f}) - {len(events)} events ---", flush=True)

        sites = discover_sites(lat, lon, site_cache)

        if not sites:
            for eid in events["event_id"].values:
                processed.add(eid)
            total_failed += len(events)
            continue

        for _, row in events.iterrows():
            eid = row["event_id"]
            date_str = row["date"]

            if eid in processed:
                continue

            found = False
            for site in sites[:5]:
                result = fetch_daily_values(site["site_no"], date_str)
                if result and result.get("discharge_cfs") is not None:
                    derived = compute_derived(result, date_str)
                    idx = df[df["event_id"] == eid].index[0]
                    for col, val in derived.items():
                        if val is not None:
                            df.at[idx, col] = val
                    df.at[idx, "gauge_site_no"] = site["site_no"]
                    df.at[idx, "gauge_distance_km"] = site["distance_km"]
                    df.at[idx, "gauge_site_type"] = site["type"]
                    total_fetched += 1
                    found = True
                    print(f"  {eid}: Q={derived.get('discharge_cfs')} cfs from {site['site_no']} ({site['distance_km']}km)", flush=True)
                    break

            if not found:
                total_failed += 1
                print(f"  {eid}: no discharge data found", flush=True)

            processed.add(eid)
            batch_count += 1

            if batch_count % 50 == 0:
                df.to_csv(GAUGES_CSV, index=False)
                save_site_cache(site_cache)
                with open(PROGRESS_FILE, "w") as f:
                    json.dump({"processed": list(processed), "timestamp": datetime.now().isoformat()}, f)
                coverage = df["discharge_cfs"].notna().sum()
                print(f"\n  >>> Checkpoint: {coverage}/{len(df)} have discharge ({coverage/len(df)*100:.1f}%) | Fetched: {total_fetched}, Failed: {total_failed}\n", flush=True)

    # Final save
    df.to_csv(GAUGES_CSV, index=False)
    save_site_cache(site_cache)
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"processed": list(processed), "timestamp": datetime.now().isoformat(), "done": True}, f)

    coverage = df["discharge_cfs"].notna().sum()
    print(f"\n=== DONE ===", flush=True)
    print(f"Total events: {len(df)}", flush=True)
    print(f"With discharge: {coverage} ({coverage/len(df)*100:.1f}%)", flush=True)
    print(f"Newly fetched: {total_fetched}", flush=True)
    print(f"Failed/no data: {total_failed}", flush=True)
    print(f"Site cache entries: {len(site_cache)}", flush=True)


if __name__ == "__main__":
    main()
