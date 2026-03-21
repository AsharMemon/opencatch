"""
Fetch weather for all CASTLINE datasets using NOAA GHCN-Daily via Meteostat.

Replaces rate-limited Open-Meteo with unlimited local lookups.

Outputs:
  - castline/validation/data/raw/flw_weather_noaa.csv
  - castline/validation/data/raw/creelcat_weather_noaa.csv

Usage:
    python scripts/fetch_all_weather_noaa.py [--flw-only] [--creel-only] [--test]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

# Project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.noaa_weather_lookup import NOAAWeatherLookup, bulk_fetch_for_events

RAW = ROOT / "castline" / "validation" / "data" / "raw"

# Input files
FLW_OUTCOMES = RAW / "flw_outcomes.csv"
CREEL_SURVEY = RAW / "creel" / "Survey_Data.csv"
GEOCODE_CACHE = RAW / "geocode_cache.json"

# Output files
FLW_WEATHER_OUT = RAW / "flw_weather_noaa.csv"
CREEL_WEATHER_OUT = RAW / "creelcat_weather_noaa.csv"


def _load_geocode_cache() -> dict[str, dict[str, float]]:
    """Load location -> {lat, lon} geocode cache."""
    if GEOCODE_CACHE.exists():
        with open(GEOCODE_CACHE) as f:
            return json.load(f)
    return {}


def fetch_flw_weather():
    """Fetch weather for all FLW tournament events."""
    print("=" * 60)
    print("FLW Tournament Weather (NOAA GHCN-Daily)")
    print("=" * 60)

    if not FLW_OUTCOMES.exists():
        print(f"  ERROR: {FLW_OUTCOMES} not found")
        return

    flw = pd.read_csv(FLW_OUTCOMES, dtype={"event_id": str})
    print(f"  Loaded {len(flw)} FLW events")

    # FLW doesn't have lat/lon — use geocode cache
    geocode = _load_geocode_cache()
    print(f"  Geocode cache: {len(geocode)} entries")

    # Map locations to lat/lon
    lats, lons = [], []
    for _, row in flw.iterrows():
        loc = str(row.get("location", "")).strip()
        match = geocode.get(loc)
        if match:
            lats.append(match["lat"])
            lons.append(match["lon"])
        else:
            # Try with state suffix variations
            found = False
            for key, val in geocode.items():
                if loc.lower() in key.lower() or key.lower() in loc.lower():
                    lats.append(val["lat"])
                    lons.append(val["lon"])
                    found = True
                    break
            if not found:
                lats.append(float("nan"))
                lons.append(float("nan"))

    flw["lat"] = lats
    flw["lon"] = lons

    # Filter to events with coordinates
    has_coords = flw.dropna(subset=["lat", "lon"])
    missing = len(flw) - len(has_coords)
    print(f"  Events with coordinates: {len(has_coords)} ({missing} missing)")

    if has_coords.empty:
        print("  No events with coordinates. Skipping.")
        return

    # Fetch with 3-day context for frontal detection
    print(f"\n  Fetching weather for {len(has_coords)} events (with 3-day lookback)...")
    t0 = time.time()

    weather = bulk_fetch_for_events(has_coords, days_before=3, progress=True)

    elapsed = time.time() - t0
    print(f"\n  Completed in {elapsed:.0f}s ({elapsed/len(has_coords):.2f}s per event)")

    # Merge event_id back if not present
    if "event_id" not in weather.columns:
        weather["event_id"] = has_coords["event_id"].values

    # Save
    weather.to_csv(FLW_WEATHER_OUT, index=False)
    print(f"  Saved to {FLW_WEATHER_OUT}")

    # Summary stats
    _print_coverage_stats(weather)


def fetch_creel_weather():
    """Fetch weather for CreelCat survey events.

    CreelCat surveys span months — we fetch MONTHLY AVERAGES for the
    survey period, matching the Open-Meteo approach.
    """
    print("\n" + "=" * 60)
    print("CreelCat Survey Weather (NOAA GHCN-Daily)")
    print("=" * 60)

    if not CREEL_SURVEY.exists():
        print(f"  ERROR: {CREEL_SURVEY} not found")
        return

    creel = pd.read_csv(CREEL_SURVEY, low_memory=False)
    print(f"  Loaded {len(creel)} CreelCat surveys")

    # Filter to surveys with coordinates
    creel["Lat"] = pd.to_numeric(creel.get("Lat"), errors="coerce")
    creel["Lon"] = pd.to_numeric(creel.get("Lon"), errors="coerce")
    has_coords = creel.dropna(subset=["Lat", "Lon"]).copy()
    print(f"  Surveys with coordinates: {len(has_coords)}")

    # Parse dates
    has_coords["start_dt"] = pd.to_datetime(has_coords["Start_Date"], errors="coerce")
    has_coords["end_dt"] = pd.to_datetime(has_coords["End_Date"], errors="coerce")

    # For surveys without exact dates, construct from Year + Start_Month/End_Month
    mask_no_start = has_coords["start_dt"].isna()
    if mask_no_start.any():
        year = has_coords.loc[mask_no_start, "Year"].astype(int)
        start_month = pd.to_numeric(has_coords.loc[mask_no_start, "Start_Month"], errors="coerce").fillna(1).astype(int)
        end_month = pd.to_numeric(has_coords.loc[mask_no_start, "End_Month"], errors="coerce").fillna(12).astype(int)
        has_coords.loc[mask_no_start, "start_dt"] = pd.to_datetime(
            year.astype(str) + "-" + start_month.astype(str) + "-01", errors="coerce"
        )
        has_coords.loc[mask_no_start, "end_dt"] = pd.to_datetime(
            year.astype(str) + "-" + end_month.astype(str) + "-28", errors="coerce"
        )

    has_coords = has_coords.dropna(subset=["start_dt", "end_dt"])
    print(f"  Surveys with valid date ranges: {len(has_coords)}")

    if has_coords.empty:
        print("  No valid surveys. Skipping.")
        return

    # Fetch monthly averages for each survey
    lookup = NOAAWeatherLookup()
    records: list[dict] = []
    total = len(has_coords)

    print(f"\n  Fetching monthly averages for {total} surveys...")
    t0 = time.time()

    for i, (_, row) in enumerate(has_coords.iterrows()):
        if i % 200 == 0 or i == total - 1:
            print(f"  [{i+1}/{total}] Processing survey {row.get('Survey_ID', 'unknown')}")

        lat = float(row["Lat"])
        lon = float(row["Lon"])
        start = pd.Timestamp(row["start_dt"])
        end = pd.Timestamp(row["end_dt"])

        # Clamp range to max 366 days to avoid huge fetches
        if (end - start).days > 366:
            end = start + pd.Timedelta(days=366)

        df = lookup.get_range(lat, lon, start, end)
        if df.empty:
            record = {
                "Survey_ID": row.get("Survey_ID", ""),
                "lat": lat,
                "lon": lon,
                "start_date": str(start.date()),
                "end_date": str(end.date()),
                "temp_mean": float("nan"),
                "temp_max_mean": float("nan"),
                "temp_min_mean": float("nan"),
                "precip_total_mm": float("nan"),
                "precip_days": float("nan"),
                "wind_avg_kph": float("nan"),
                "pressure_hpa_mean": float("nan"),
                "station_id": None,
                "n_days_data": 0,
            }
        else:
            record = {
                "Survey_ID": row.get("Survey_ID", ""),
                "lat": lat,
                "lon": lon,
                "start_date": str(start.date()),
                "end_date": str(end.date()),
                "temp_mean": _nanmean(df, "temp_mean"),
                "temp_max_mean": _nanmean(df, "temp_max"),
                "temp_min_mean": _nanmean(df, "temp_min"),
                "precip_total_mm": _nansum(df, "precip_mm"),
                "precip_days": int((df["precip_mm"].dropna() > 0.1).sum()) if "precip_mm" in df.columns else 0,
                "wind_avg_kph": _nanmean(df, "wind_avg_kph"),
                "pressure_hpa_mean": _nanmean(df, "pressure_hpa"),
                "station_id": df["station_id"].iloc[0] if "station_id" in df.columns else None,
                "n_days_data": len(df),
            }

        records.append(record)

    elapsed = time.time() - t0
    print(f"\n  Completed in {elapsed:.0f}s ({elapsed/total:.2f}s per survey)")

    result = pd.DataFrame(records)
    result.to_csv(CREEL_WEATHER_OUT, index=False)
    print(f"  Saved to {CREEL_WEATHER_OUT}")

    # Coverage stats
    has_data = result["n_days_data"] > 0
    print(f"\n  Coverage: {has_data.sum()}/{len(result)} surveys have weather data ({has_data.mean()*100:.1f}%)")
    if "pressure_hpa_mean" in result.columns:
        has_pressure = result["pressure_hpa_mean"].notna().sum()
        print(f"  Pressure coverage: {has_pressure}/{len(result)} ({has_pressure/len(result)*100:.1f}%)")


def _nanmean(df: pd.DataFrame, col: str) -> float:
    if col not in df.columns:
        return float("nan")
    vals = df[col].dropna()
    return round(float(vals.mean()), 2) if len(vals) > 0 else float("nan")


def _nansum(df: pd.DataFrame, col: str) -> float:
    if col not in df.columns:
        return float("nan")
    vals = df[col].dropna()
    return round(float(vals.sum()), 2) if len(vals) > 0 else float("nan")


def _print_coverage_stats(df: pd.DataFrame):
    """Print coverage stats for a weather DataFrame."""
    cols_of_interest = ["temp_mean", "temp_max", "temp_min", "precip_mm",
                        "pressure_hpa", "wind_avg_kph", "wind_dir"]
    print("\n  Column coverage:")
    for col in cols_of_interest:
        if col in df.columns:
            n_valid = df[col].notna().sum()
            pct = n_valid / len(df) * 100
            print(f"    {col}: {n_valid}/{len(df)} ({pct:.1f}%)")


def run_test():
    """Quick test with 10 sample events to verify the pipeline works."""
    print("=" * 60)
    print("Quick Test: 10 sample events")
    print("=" * 60)

    test_events = pd.DataFrame([
        {"event_id": "test-01", "lat": 34.37, "lon": -86.29, "date": "2023-06-15"},
        {"event_id": "test-02", "lat": 31.06, "lon": -94.10, "date": "2023-04-10"},
        {"event_id": "test-03", "lat": 26.95, "lon": -80.80, "date": "2023-02-20"},
        {"event_id": "test-04", "lat": 36.60, "lon": -93.31, "date": "2023-05-05"},
        {"event_id": "test-05", "lat": 43.20, "lon": -75.93, "date": "2023-07-01"},
        {"event_id": "test-06", "lat": 41.68, "lon": -82.84, "date": "2023-08-15"},
        {"event_id": "test-07", "lat": 31.17, "lon": -93.57, "date": "2023-03-25"},
        {"event_id": "test-08", "lat": 35.16, "lon": -85.14, "date": "2023-09-10"},
        {"event_id": "test-09", "lat": 32.82, "lon": -95.55, "date": "2023-11-01"},
        {"event_id": "test-10", "lat": 28.30, "lon": -81.38, "date": "2023-01-15"},
    ])

    print(f"\n  Fetching weather for {len(test_events)} test events with 3-day context...")
    t0 = time.time()
    result = bulk_fetch_for_events(test_events, days_before=3, progress=True)
    elapsed = time.time() - t0

    print(f"\n  Completed in {elapsed:.1f}s")
    print(f"\n  Results ({len(result)} rows):")

    display_cols = ["event_id", "date", "temp_mean", "temp_max", "precip_mm",
                    "pressure_hpa", "wind_avg_kph", "station_id", "station_distance_km"]
    available = [c for c in display_cols if c in result.columns]
    print(result[available].to_string(index=False))

    # Check pressure delta features
    pressure_cols = [c for c in result.columns if "pressure_delta" in c or "front_phase" in c]
    if pressure_cols:
        print(f"\n  Pressure/frontal features:")
        print(result[["event_id"] + pressure_cols].to_string(index=False))

    _print_coverage_stats(result)

    test_out = RAW / "noaa_weather_test.csv"
    result.to_csv(test_out, index=False)
    print(f"\n  Test results saved to {test_out}")


def main():
    parser = argparse.ArgumentParser(description="Fetch NOAA GHCN-Daily weather for CASTLINE datasets")
    parser.add_argument("--flw-only", action="store_true", help="Only fetch FLW tournament weather")
    parser.add_argument("--creel-only", action="store_true", help="Only fetch CreelCat survey weather")
    parser.add_argument("--test", action="store_true", help="Run quick 10-event test only")
    args = parser.parse_args()

    if args.test:
        run_test()
        return

    if args.flw_only:
        fetch_flw_weather()
    elif args.creel_only:
        fetch_creel_weather()
    else:
        fetch_flw_weather()
        fetch_creel_weather()

    print("\n=== All done! ===")


if __name__ == "__main__":
    main()
