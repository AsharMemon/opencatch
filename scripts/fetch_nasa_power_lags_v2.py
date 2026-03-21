"""Fetch NASA POWER weather for 7-day windows around each event.

NASA POWER rate limit: ~30 requests/minute for unauthenticated.
Strategy: 1 request every 2.5 seconds, sequential (no threading).
With retries and exponential backoff.

This enables lag features per Tanaka et al. — 1-7 day temperature
trends and 3-day moving averages dramatically improve catch prediction.
"""
import pandas as pd
import numpy as np
import requests
import time
import os
import sys

DATASET = "castline/validation/data/assembled/validation_dataset_v11.csv"
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

REQUEST_DELAY = 2.5  # seconds between requests
MAX_RETRIES = 3
CHECKPOINT_EVERY = 50


def fetch_window(lat, lon, event_date, days_before=7):
    """Fetch daily weather for days_before days before event_date."""
    start = (event_date - pd.Timedelta(days=days_before)).strftime("%Y%m%d")
    end = event_date.strftime("%Y%m%d")

    url = (f"https://power.larc.nasa.gov/api/temporal/daily/point?"
           f"parameters={PARAMS}&community=AG&longitude={lon:.4f}"
           f"&latitude={lat:.4f}&start={start}&end={end}&format=JSON")

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 429:
                # Rate limited — back off
                wait = 10 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
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
        except requests.exceptions.Timeout:
            time.sleep(5 * (attempt + 1))
            continue
        except Exception:
            return None
    return None


def checkpoint_save(results, out_file):
    """Save results, merging with existing file."""
    res_df = pd.DataFrame(results)
    if os.path.exists(out_file):
        old = pd.read_csv(out_file)
        res_df = pd.concat([old, res_df], ignore_index=True)
    res_df.drop_duplicates(
        subset=["event_date", "event_lat", "event_lon", "date"],
        keep="last", inplace=True
    )
    res_df.to_csv(out_file, index=False)
    return len(res_df)


def main():
    print("=" * 60, flush=True)
    print("NASA POWER 7-Day Weather Windows (v2 — sequential)", flush=True)
    print("=" * 60, flush=True)

    df = pd.read_csv(DATASET, low_memory=False)
    df["date_dt"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date_dt"].notna() & df["lat"].notna() & df["lon"].notna()].copy()
    print(f"Events: {len(df)}", flush=True)

    # Load existing results to skip
    existing_keys = set()
    if os.path.exists(OUT_FILE):
        old = pd.read_csv(OUT_FILE)
        # Group by event to find which events already have data
        for event_date in old["event_date"].unique():
            sub = old[old["event_date"] == event_date]
            if len(sub) > 0:
                lat = sub.iloc[0]["event_lat"]
                lon = sub.iloc[0]["event_lon"]
                existing_keys.add(f"{lat:.4f},{lon:.4f},{event_date}")
        print(f"Already fetched: {len(existing_keys)} event-windows", flush=True)

    # Build unique (lat, lon, date) tasks, deduped by location+date
    seen = set()
    tasks = []
    for _, row in df.iterrows():
        key = f"{row.lat:.4f},{row.lon:.4f},{row.date}"
        if key not in existing_keys and key not in seen:
            tasks.append((row.lat, row.lon, row.date_dt, row.date))
            seen.add(key)

    print(f"To fetch: {len(tasks)} unique event-windows", flush=True)
    print(f"Est. time: {len(tasks) * REQUEST_DELAY / 60:.0f} minutes", flush=True)

    if not tasks:
        print("Nothing to fetch!", flush=True)
        return

    results = []
    fetched = 0
    failed = 0
    t0 = time.time()

    for i, (lat, lon, date_dt, date_str) in enumerate(tasks):
        rows = fetch_window(lat, lon, date_dt)

        if rows:
            for r in rows:
                r["event_date"] = date_str
                r["event_lat"] = lat
                r["event_lon"] = lon
            results.extend(rows)
            fetched += 1
        else:
            failed += 1

        # Progress report
        if (i + 1) % 25 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed * 60  # per minute
            remaining = (len(tasks) - i - 1) / rate if rate > 0 else 0
            print(f"  {i+1}/{len(tasks)} done ({fetched} ok, {failed} fail) "
                  f"[{elapsed/60:.1f}min, ~{remaining:.0f}min left]", flush=True)

        # Checkpoint save
        if (i + 1) % CHECKPOINT_EVERY == 0 and results:
            total_rows = checkpoint_save(results, OUT_FILE)
            print(f"  Checkpoint: {total_rows} total rows saved", flush=True)
            results = []  # Clear buffer after save

        time.sleep(REQUEST_DELAY)

    # Final save
    if results:
        total_rows = checkpoint_save(results, OUT_FILE)
        print(f"\nFinal: {total_rows} total daily rows saved to {OUT_FILE}", flush=True)

    elapsed = time.time() - t0
    print(f"\nCompleted: {fetched}/{len(tasks)} events ({fetched/len(tasks)*100:.1f}%) "
          f"in {elapsed/60:.1f} minutes", flush=True)
    print(f"Failed: {failed}", flush=True)


if __name__ == "__main__":
    main()
