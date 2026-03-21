#!/usr/bin/env python3
"""
Enrich area_acres in the v7 validation dataset using two sources:

1. Creel Survey_Data.csv  -- best-available area per waterbody
2. NHD web service (hydro.nationalmap.gov) -- lake polygon lookup by lat/lon

Usage:
    python scripts/enrich_morphometry.py
"""

import json
import math
import ssl
import time
import urllib.request
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
V7_PATH = ROOT / "castline/validation/data/assembled/validation_dataset_v7.csv"
CREEL_SURVEY = ROOT / "castline/validation/data/raw/creel/Survey_Data.csv"
CREEL_LOCS = ROOT / "castline/validation/data/raw/creel_gnn_locations.csv"

# ---------------------------------------------------------------------------
# SSL context (work around certificate issues)
# ---------------------------------------------------------------------------
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------------------
# NHD API
# ---------------------------------------------------------------------------
NHD_IDENTIFY_URL = (
    "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/identify"
)
# Layer 5 = NHDWaterbody polygons
NHD_LAYER = "all:5"

SQ_METERS_PER_ACRE = 4046.8564224


def nhd_area_acres(lat: float, lon: float, timeout: int = 20) -> float | None:
    """Query the NHD MapServer identify endpoint for a waterbody polygon at
    the given lat/lon and return its area in acres, or None on failure."""

    params = urllib.parse.urlencode(
        {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "sr": "4326",
            "layers": NHD_LAYER,
            "tolerance": 5,
            "mapExtent": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
            "imageDisplay": "800,600,96",
            "returnGeometry": "false",
            "f": "json",
        }
    )
    url = f"{NHD_IDENTIFY_URL}?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  NHD request failed ({lat:.4f}, {lon:.4f}): {exc}")
        return None

    results = data.get("results", [])
    if not results:
        return None

    # Pick the first waterbody result that has Shape_Area
    for r in results:
        attrs = r.get("attributes", {})
        shape_area = attrs.get("Shape_Area")  # in sq meters
        areasqkm = attrs.get("AreaSqKm")
        if shape_area is not None and float(shape_area) > 0:
            return float(shape_area) / SQ_METERS_PER_ACRE
        if areasqkm is not None and float(areasqkm) > 0:
            return float(areasqkm) * 247.105  # sq km -> acres
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def best_creel_area(row: pd.Series) -> float | None:
    """Return the best-available area from a creel survey row, preferring
    Survey_Acres > NHD_Acres > Calc_Acres > Reported_Acres."""
    for col in ("Survey_Acres", "NHD_Acres", "Calc_Acres", "Reported_Acres"):
        val = row.get(col)
        if pd.notna(val):
            try:
                v = float(val)
                if v > 0:
                    return v
            except (ValueError, TypeError):
                continue
    return None


def parse_creel_location(loc: str):
    """Parse a creel-style v7 location 'Waterbody Name, State' and return
    (waterbody_name_lower, state_lower) or None."""
    parts = [p.strip() for p in loc.rsplit(",", 1)]
    if len(parts) == 2:
        return parts[0].lower(), parts[1].lower()
    return None


def parse_tournament_location(loc: str):
    """Parse a tournament-style v7 location 'Lake Name, City, ST' and return
    (lake_name_lower, state_abbrev_lower) or None."""
    parts = [p.strip() for p in loc.split(",")]
    if len(parts) >= 3:
        return parts[0].lower(), parts[-1].lower()
    if len(parts) == 2:
        return parts[0].lower(), parts[1].lower()
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Enrich area_acres in v7 dataset")
    print("=" * 60)

    # ---- Load v7 ----
    df = pd.read_csv(V7_PATH)
    total = len(df)
    n_locs = df["location"].nunique()
    before_has = df["area_acres"].notna().sum()
    before_pct = before_has / total * 100
    print(f"\nv7 dataset: {total:,} rows, {n_locs:,} locations")
    print(f"area_acres coverage BEFORE: {before_has:,}/{total:,} ({before_pct:.1f}%)")

    # ---- Build creel area lookup ----
    print("\n--- Step 1: Creel Survey_Data.csv area lookup ---")
    creel = pd.read_csv(CREEL_SURVEY)
    creel["_best_area"] = creel.apply(best_creel_area, axis=1)
    creel = creel.dropna(subset=["_best_area"])
    creel["_wb_lower"] = creel["Waterbody_Name"].str.strip().str.lower()
    creel["_st_lower"] = creel["State"].str.strip().str.lower()

    # Keep the largest area per (waterbody, state) to avoid tiny partial entries
    creel_area = (
        creel.groupby(["_wb_lower", "_st_lower"])["_best_area"]
        .max()
        .reset_index()
        .rename(columns={"_best_area": "creel_area_acres"})
    )
    print(f"  Creel area lookup: {len(creel_area):,} unique (waterbody, state) entries")

    # Also build a set of creel location names from the creel_gnn_locations file
    creel_locs = pd.read_csv(CREEL_LOCS)
    creel_loc_names = set()
    for _, r in creel_locs.iterrows():
        wb = str(r.get("waterbody_name", "")).strip()
        st = str(r.get("state", "")).strip()
        if wb and st:
            creel_loc_names.add((wb.lower(), st.lower()))
    print(f"  Creel GNN locations: {len(creel_loc_names):,}")

    # ---- Match creel areas to v7 rows ----
    # Build join keys on v7 rows
    df["_wb_lower"] = None
    df["_st_lower"] = None

    for idx, row in df.iterrows():
        loc = str(row["location"])
        source = str(row.get("source", ""))

        # Creel rows: "Waterbody Name, State"
        parsed = parse_creel_location(loc)
        if parsed:
            wb, st = parsed
            # Check if this is actually a creel location
            if (wb, st) in creel_loc_names:
                df.at[idx, "_wb_lower"] = wb
                df.at[idx, "_st_lower"] = st
                continue

        # Tournament rows: try to match lake name + state abbrev to creel
        parsed_t = parse_tournament_location(loc)
        if parsed_t:
            df.at[idx, "_wb_lower"] = parsed_t[0]
            df.at[idx, "_st_lower"] = parsed_t[1]

    # Merge creel areas
    df = df.merge(creel_area, on=["_wb_lower", "_st_lower"], how="left")

    # Fill area_acres from creel where missing
    creel_filled = 0
    mask = df["area_acres"].isna() & df["creel_area_acres"].notna()
    creel_filled = mask.sum()
    df.loc[mask, "area_acres"] = df.loc[mask, "creel_area_acres"]
    print(f"  Filled {creel_filled:,} rows from creel survey data")

    df.drop(columns=["creel_area_acres", "_wb_lower", "_st_lower"], inplace=True)

    # ---- Step 2: NHD API for tournament rows still missing area ----
    print("\n--- Step 2: NHD web service for remaining gaps ---")
    tournament_sources = {"bassmaster", "tourneyx", "bassmaster_open", "bassmaster_elite"}
    still_missing = df[
        df["area_acres"].isna()
        & df["source"].isin(tournament_sources)
        & df["lat"].notna()
        & df["lon"].notna()
    ]

    # Deduplicate by location to avoid redundant API calls
    missing_locs = still_missing.drop_duplicates(subset=["location"])[
        ["location", "lat", "lon"]
    ].copy()
    print(f"  Unique tournament locations missing area: {len(missing_locs):,}")

    nhd_lookup = {}
    success_count = 0
    for i, (_, row) in enumerate(missing_locs.iterrows()):
        loc = row["location"]
        lat, lon = row["lat"], row["lon"]
        if pd.isna(lat) or pd.isna(lon):
            continue

        print(f"  [{i + 1}/{len(missing_locs)}] Querying NHD for {loc}...", end=" ")
        area = nhd_area_acres(lat, lon)
        if area is not None:
            nhd_lookup[loc] = area
            success_count += 1
            print(f"{area:.1f} acres")
        else:
            print("no result")

        # Be polite to the API
        if (i + 1) % 10 == 0:
            time.sleep(1)

    print(f"  NHD hits: {success_count}/{len(missing_locs)}")

    # Apply NHD results
    nhd_filled = 0
    for loc, area in nhd_lookup.items():
        mask = (df["location"] == loc) & df["area_acres"].isna()
        nhd_filled += mask.sum()
        df.loc[mask, "area_acres"] = area

    print(f"  Filled {nhd_filled:,} rows from NHD web service")

    # ---- Report ----
    after_has = df["area_acres"].notna().sum()
    after_pct = after_has / total * 100
    print("\n" + "=" * 60)
    print("Coverage report")
    print("=" * 60)
    print(f"  BEFORE: {before_has:,}/{total:,} ({before_pct:.1f}%)")
    print(f"  AFTER:  {after_has:,}/{total:,} ({after_pct:.1f}%)")
    print(f"  Gain:   {after_has - before_has:,} rows "
          f"(+{after_pct - before_pct:.1f}pp)")

    # Per-source breakdown
    print("\nPer-source coverage:")
    for src in sorted(df["source"].unique()):
        sub = df[df["source"] == src]
        n = sub["area_acres"].notna().sum()
        pct = n / len(sub) * 100 if len(sub) else 0
        print(f"  {src:30s}: {n:>5,}/{len(sub):>5,} ({pct:5.1f}%)")

    # ---- Save ----
    df.to_csv(V7_PATH, index=False)
    print(f"\nSaved enriched dataset to {V7_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
