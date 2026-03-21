"""Fetch NASA POWER weather for 7-day windows around each event.

This enables lag features (3-day and 7-day rolling means/trends) per
Tanaka et al.'s finding that lag features dramatically improve catch prediction.
"""
import pandas as pd
import numpy as np
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

DATASET = "castline/validation/data/assembled/validation_dataset_v10.csv"
OUT_FILE = "castline/validation/data/raw/nasa_power_7day.csv"

PARAMS = "T2M,T2M_MAX,T2M_MIN,T2MDEW,PRECTOTCORR,RH2M,WS2M,PS,ALLSKY_SFC_SW_DWN,CLOUD_AMT"
COL_MAP = {
    "T2M": "np_temp_mean_c",
    "T2M_MAX": "np_temp_max_c",
    "T2M_MIN": "np_temp_min_c",
    "T2MDEW": "np_dewpoint_c",
    "PRECTOTCORR": "np_precip_mm",
    "RH2M": "np_humidity_pct",
    "WS2M": "np_wind_2m_ms",
    "PS": "np_pressure_kpa",
    "ALLSKY_SFC_SW_DWN": "np_solar_mj_m2",
    "CLOUD_AMT": "np_cloud_pct",
}


def fetch_window(lat, lon, event_date, days_before=7):
    """Fetch daily weather for days_before days before event_date."""
    start = (event_date - pd.Timedelta(days=days_before)).strftime("%Y%m%d")
    end = event_date.strftime("%Y%m%d")

    url = (f"https://power.larc.nasa.gov/api/temporal/daily/point?"
           f"parameters={PARAMS}&community=AG&longitude={lon:.4f}"
           f"&latitude={lat:.4f}&start={start}&end={end}&format=JSON")

    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return None
        data = resp.json()
        props = data.get("properties", {}).get("parameter", {})
        if not props:
            return None

        rows = []
        for date_str in sorted(props.get("T2M", {}).keys()):
            row = {"lat": lat, "lon": lon, "date": date_str}
            for api_name, col_name in COL_MAP.items():
                val = props.get(api_name, {}).get(date_str)
                if val is not None and val != -999:
                    row[col_name] = val
                else:
                    row[col_name] = np.nan
            rows.append(row)
        return rows
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("NASA POWER 7-Day Weather Windows")
    print("=" * 60)

    df = pd.read_csv(DATASET, low_memory=False)
    df["date_dt"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date_dt"].notna() & df["lat"].notna() & df["lon"].notna()].copy()
    print(f"Events: {len(df)}")

    # Load existing results
    existing = set()
    if os.path.exists(OUT_FILE):
        old = pd.read_csv(OUT_FILE)
        for _, r in old.iterrows():
            existing.add(f"{r.lat:.4f},{r.lon:.4f},{r.event_date}")
        print(f"Already fetched: {len(existing)} events")

    # Build unique (lat, lon, date) combos
    tasks = []
    for _, row in df.iterrows():
        key = f"{row.lat:.4f},{row.lon:.4f},{row.date}"
        if key not in existing:
            tasks.append((row.lat, row.lon, row.date_dt, row.date))
    print(f"To fetch: {len(tasks)} events")

    if not tasks:
        print("Nothing to fetch!")
        return

    results = []
    completed = 0

    def worker(args):
        lat, lon, date_dt, date_str = args
        rows = fetch_window(lat, lon, date_dt)
        return (date_str, lat, lon, rows)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker, t) for t in tasks]

        for future in as_completed(futures):
            date_str, lat, lon, rows = future.result()
            completed += 1

            if rows:
                for r in rows:
                    r["event_date"] = date_str
                    r["event_lat"] = lat
                    r["event_lon"] = lon
                results.extend(rows)

            if completed % 100 == 0:
                print(f"  {completed}/{len(tasks)} fetched, {len(results)} daily rows")

                # Checkpoint save
                if results:
                    res_df = pd.DataFrame(results)
                    if os.path.exists(OUT_FILE):
                        old = pd.read_csv(OUT_FILE)
                        res_df = pd.concat([old, res_df], ignore_index=True)
                    res_df.to_csv(OUT_FILE, index=False)
                    print(f"  Checkpoint: {len(res_df)} rows saved")

    # Final save
    if results:
        res_df = pd.DataFrame(results)
        if os.path.exists(OUT_FILE):
            old = pd.read_csv(OUT_FILE)
            res_df = pd.concat([old, res_df], ignore_index=True)
        res_df.drop_duplicates(subset=["event_date", "event_lat", "event_lon", "date"],
                                keep="last", inplace=True)
        res_df.to_csv(OUT_FILE, index=False)
        print(f"\nSaved {len(res_df)} daily rows for {completed} events to {OUT_FILE}")
    else:
        print("\nNo data fetched")


if __name__ == "__main__":
    main()
