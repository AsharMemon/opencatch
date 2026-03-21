"""Fetch chlorophyll-a and Secchi depth data from the Water Quality Portal.

These are lake productivity indicators that can serve as location-level
features for fishing catch prediction. Higher chlorophyll-a = more
productive lake = generally better fishing for bass.

Data source: https://www.waterqualitydata.us/
"""
import pandas as pd
import numpy as np
import requests
import time
import os

DATASET = "castline/validation/data/assembled/validation_dataset_v11.csv"
OUT_FILE = "castline/validation/data/raw/wqp_lake_productivity.csv"

# WQP characteristics to fetch
CHARACTERISTICS = [
    "Chlorophyll a",
    "Chlorophyll a, corrected for pheophytin",
    "Depth, Secchi disk depth",
    "Total Phosphorus, mixed forms",
]


def fetch_wqp_near_location(lat, lon, char_name, radius_miles=15):
    """Fetch water quality data near a location from WQP."""
    url = "https://www.waterqualitydata.us/data/Result/search"
    params = {
        "lat": f"{lat:.4f}",
        "long": f"{lon:.4f}",
        "within": str(radius_miles),
        "characteristicName": char_name,
        "mimeType": "csv",
        "sorted": "no",
        "dataProfile": "narrowResult",
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return None
        if len(resp.text) < 100:
            return None

        # Parse CSV response
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text), low_memory=False)
        if len(df) == 0:
            return None

        return df
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("WQP Lake Productivity Data Fetch")
    print("=" * 60)

    df = pd.read_csv(DATASET, low_memory=False)
    df = df[df["lat"].notna() & df["lon"].notna()].copy()

    # Get unique locations
    locations = df.groupby("location").agg(
        lat=("lat", "first"),
        lon=("lon", "first"),
        n_events=("median_weight_lb", "count"),
    ).reset_index()

    # Sort by number of events (most important locations first)
    locations = locations.sort_values("n_events", ascending=False).reset_index(drop=True)
    print(f"Locations: {len(locations)}")

    # Load existing results
    existing_locs = set()
    if os.path.exists(OUT_FILE):
        old = pd.read_csv(OUT_FILE)
        existing_locs = set(old.location.unique())
        print(f"Already fetched: {len(existing_locs)} locations")

    results = []
    fetched = 0
    total = len(locations)

    for i, row in locations.iterrows():
        if row.location in existing_locs:
            continue

        loc_results = []
        for char in CHARACTERISTICS:
            wqp_data = fetch_wqp_near_location(row.lat, row.lon, char)
            if wqp_data is not None and len(wqp_data) > 0:
                # Extract key columns
                for _, r in wqp_data.iterrows():
                    try:
                        val = float(r.get("ResultMeasureValue", np.nan))
                        if np.isnan(val) or val < 0:
                            continue
                        loc_results.append({
                            "location": row.location,
                            "lat": row.lat,
                            "lon": row.lon,
                            "characteristic": char,
                            "value": val,
                            "unit": r.get("ResultMeasure/MeasureUnitCode", ""),
                            "date": r.get("ActivityStartDate", ""),
                            "org": r.get("OrganizationFormalName", ""),
                        })
                    except (ValueError, TypeError):
                        continue

            time.sleep(0.3)  # rate limit

        if loc_results:
            results.extend(loc_results)
            fetched += 1

        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{total} searched, {fetched} with WQP data, {len(results)} measurements")

            # Checkpoint save
            if results:
                res_df = pd.DataFrame(results)
                if os.path.exists(OUT_FILE):
                    old = pd.read_csv(OUT_FILE)
                    res_df = pd.concat([old, res_df], ignore_index=True)
                res_df.to_csv(OUT_FILE, index=False)
                print(f"  Checkpoint: {len(res_df)} rows saved")

        time.sleep(0.1)

    # Final save
    if results:
        res_df = pd.DataFrame(results)
        if os.path.exists(OUT_FILE):
            old = pd.read_csv(OUT_FILE)
            res_df = pd.concat([old, res_df], ignore_index=True)
        res_df.to_csv(OUT_FILE, index=False)
        print(f"\nSaved {len(res_df)} measurements to {OUT_FILE}")

        # Summary stats
        summary = res_df.groupby("characteristic").agg(
            n_locs=("location", "nunique"),
            n_measurements=("value", "count"),
            mean_value=("value", "mean"),
            median_value=("value", "median"),
        )
        print("\nSummary:")
        print(summary.to_string())
    else:
        print("\nNo data fetched")


if __name__ == "__main__":
    main()
