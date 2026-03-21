#!/usr/bin/env python3
"""
Fetch MODIS Aqua chlorophyll-a data from NOAA CoastWatch ERDDAP.

Uses the erdMH1chlamday dataset (monthly, 4km resolution).
Many inland freshwater lakes will have no coverage — that's expected
since MODIS ocean color is designed for open water and 4km pixels
are too coarse for most small lakes. Results are saved with NaN
for locations with no data.

Usage:
    python3 scripts/fetch_modis_chlorophyll.py
"""

import os
import sys
import time
import math
import requests
import pandas as pd
import numpy as np
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_CSV = BASE_DIR / "castline/validation/data/assembled/validation_dataset_v15.csv"
OUTPUT_CSV = BASE_DIR / "castline/validation/data/raw/modis_chlorophyll.csv"
CHECKPOINT_CSV = OUTPUT_CSV.with_suffix(".checkpoint.csv")

ERDDAP_BASE = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/erdMH1chlamday"

# Time range for climatology
TIME_START = "2018-01-16"
TIME_END = "2023-12-16"

# Spatial buffer around each point (degrees). ~5 km at mid-latitudes.
SPATIAL_BUFFER = 0.05

# Request behaviour
DELAY_SECONDS = 2.0
MAX_RETRIES = 3
RETRY_BACKOFF = 5.0  # seconds, multiplied by attempt number
REQUEST_TIMEOUT = 30  # seconds

CHECKPOINT_EVERY = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_chlorophyll(lat: float, lon: float, session: requests.Session) -> dict:
    """
    Query ERDDAP for monthly MODIS chlorophyll-a near (lat, lon).

    Returns a dict with chl_mean, chl_max, chl_min, chl_std, n_months.
    All values are NaN if no data is available.
    """
    empty = {
        "chl_mean": np.nan,
        "chl_max": np.nan,
        "chl_min": np.nan,
        "chl_std": np.nan,
        "n_months": 0,
    }

    lat_lo = lat - SPATIAL_BUFFER
    lat_hi = lat + SPATIAL_BUFFER
    lon_lo = lon - SPATIAL_BUFFER
    lon_hi = lon + SPATIAL_BUFFER

    url = (
        f"{ERDDAP_BASE}.csv"
        f"?chlorophyll"
        f"[({TIME_START}):1:({TIME_END})]"
        f"[({lat_lo:.4f}):1:({lat_hi:.4f})]"
        f"[({lon_lo:.4f}):1:({lon_hi:.4f})]"
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)

            # 404 / empty grid → no data for this location
            if resp.status_code == 404:
                return empty
            # ERDDAP returns 400 for out-of-range queries
            if resp.status_code == 400:
                return empty

            resp.raise_for_status()

            # ERDDAP CSV has two header rows: column names, then units
            lines = resp.text.strip().split("\n")
            if len(lines) <= 2:
                return empty

            # Parse values — skip the units row (index 1)
            header = lines[0].split(",")
            chl_idx = None
            for i, col in enumerate(header):
                if "chlorophyll" in col.lower():
                    chl_idx = i
                    break
            if chl_idx is None:
                return empty

            values = []
            for line in lines[2:]:
                parts = line.split(",")
                try:
                    val = float(parts[chl_idx])
                    if not math.isnan(val) and val >= 0:
                        values.append(val)
                except (ValueError, IndexError):
                    continue

            if not values:
                return empty

            arr = np.array(values)
            return {
                "chl_mean": float(np.nanmean(arr)),
                "chl_max": float(np.nanmax(arr)),
                "chl_min": float(np.nanmin(arr)),
                "chl_std": float(np.nanstd(arr)),
                "n_months": len(arr),
            }

        except requests.exceptions.RequestException as exc:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * attempt
                print(f"    Retry {attempt}/{MAX_RETRIES} after error: {exc}. "
                      f"Waiting {wait:.0f}s...")
                time.sleep(wait)
            else:
                print(f"    Failed after {MAX_RETRIES} attempts: {exc}")
                return empty

    return empty  # unreachable, but defensive


def load_checkpoint() -> pd.DataFrame | None:
    """Load previously saved checkpoint if it exists."""
    if CHECKPOINT_CSV.exists():
        df = pd.read_csv(CHECKPOINT_CSV)
        print(f"Loaded checkpoint with {len(df)} locations already fetched.")
        return df
    return None


def save_checkpoint(records: list[dict]) -> None:
    """Save current progress to checkpoint file."""
    df = pd.DataFrame(records)
    df.to_csv(CHECKPOINT_CSV, index=False)


def save_final(records: list[dict]) -> None:
    """Save final output and remove checkpoint."""
    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved {len(df)} locations to {OUTPUT_CSV}")
    if CHECKPOINT_CSV.exists():
        CHECKPOINT_CSV.unlink()
        print("Removed checkpoint file.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Load input dataset
    print(f"Reading {INPUT_CSV} ...")
    src = pd.read_csv(INPUT_CSV, usecols=["lat", "lon"])
    locations = src.drop_duplicates(subset=["lat", "lon"]).reset_index(drop=True)
    print(f"Found {len(locations)} unique (lat, lon) pairs.")

    # Check for checkpoint
    done_set: set[tuple[float, float]] = set()
    records: list[dict] = []

    checkpoint_df = load_checkpoint()
    if checkpoint_df is not None:
        records = checkpoint_df.to_dict("records")
        done_set = {(r["lat"], r["lon"]) for r in records}

    # Filter to remaining locations
    remaining = [
        (row.lat, row.lon)
        for _, row in locations.iterrows()
        if (row.lat, row.lon) not in done_set
    ]
    print(f"{len(remaining)} locations remaining to fetch.\n")

    if not remaining:
        print("All locations already fetched. Saving final output.")
        save_final(records)
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "CASTLINE-Research/1.0 (fishing conditions model; polite bot)"
    })

    for i, (lat, lon) in enumerate(remaining, start=1):
        print(f"[{i}/{len(remaining)}] lat={lat:.4f}, lon={lon:.4f} ... ", end="", flush=True)

        result = fetch_chlorophyll(lat, lon, session)
        result["lat"] = lat
        result["lon"] = lon
        records.append(result)

        if result["n_months"] > 0:
            print(f"OK  chl_mean={result['chl_mean']:.3f}  n_months={result['n_months']}")
        else:
            print("no data")

        # Checkpoint
        if i % CHECKPOINT_EVERY == 0:
            save_checkpoint(records)
            print(f"  -- checkpoint saved ({len(records)} total) --")

        # Be polite
        if i < len(remaining):
            time.sleep(DELAY_SECONDS)

    # Final save
    save_final(records)

    # Summary
    df = pd.DataFrame(records)
    n_with_data = (df["n_months"] > 0).sum()
    print(f"\nSummary: {n_with_data}/{len(df)} locations had MODIS chlorophyll data "
          f"({100 * n_with_data / len(df):.1f}% coverage).")
    if n_with_data > 0:
        subset = df[df["n_months"] > 0]
        print(f"  chl_mean range: {subset['chl_mean'].min():.3f} – {subset['chl_mean'].max():.3f}")
        print(f"  median n_months: {subset['n_months'].median():.0f}")


if __name__ == "__main__":
    main()
