#!/usr/bin/env python3
"""
OpenCatch -- Fetch National Inventory of Dams (NID) Data

Downloads the complete NID catalog from USACE and processes it into a
reservoir-focused Parquet file with derived features for bathymetry modeling.

The NID contains 92,075 dams with ~70 data fields each, including:
  - Dam location, height, storage capacity, surface area
  - Year completed, dam type, purposes, hazard potential
  - Owner type, regulatory agency, inspection dates

We cross-reference NID dams to SWOT Prior Lake Database (PLD) IDs
so each reservoir can be linked to its SWOT time series.

Usage:
    # Download and process full NID catalog
    python fetch_nid.py --output /data/reservoir

    # Filter to specific states
    python fetch_nid.py --output /data/reservoir --states MN WI IA

    # Filter to large reservoirs only (>1000 acre-ft storage)
    python fetch_nid.py --output /data/reservoir --min-storage-acft 1000

Requirements:
    pip install requests pandas numpy geopandas pyarrow tqdm
"""

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_nid")

# -- Configuration -----------------------------------------------------------

# NID API endpoint (USACE public download)
NID_CSV_URL = "https://nid.sec.usace.army.mil/api/nation/csv"

# Alternative: NID GeoJSON API for spatial queries
NID_GEOJSON_URL = "https://nid.sec.usace.army.mil/api/nation/geojson"

# Columns to retain from NID (full dataset has 70+ columns)
NID_COLUMNS = {
    # Identifiers
    "nidId": "nid_id",
    "name": "dam_name",
    "otherNames": "other_names",
    "stateId": "state",
    "county": "county",
    # Location
    "latitude": "latitude",
    "longitude": "longitude",
    # Physical
    "damHeight": "dam_height_ft",
    "damLength": "dam_length_ft",
    "hydraulicHeight": "hydraulic_height_ft",
    "structuralHeight": "structural_height_ft",
    "nidHeight": "nid_height_ft",
    "nidStorage": "nid_storage_acft",
    "maxStorage": "max_storage_acft",
    "normalStorage": "normal_storage_acft",
    "surfaceArea": "surface_area_acres",
    "drainageArea": "drainage_area_sqmi",
    "maxDischarge": "max_discharge_cfs",
    # Elevation
    "crestElevation": "crest_elevation_ft",
    # Classification
    "damType": "dam_type",
    "purposes": "purposes",
    "hazardPotential": "hazard_potential",
    "conditionAssessment": "condition_assessment",
    "conditionAssessmentDate": "condition_date",
    # Ownership
    "primaryOwnerType": "owner_type",
    "regulatoryAgency": "regulatory_agency",
    # Dates
    "yearCompleted": "year_completed",
    "yearModified": "year_modified",
    # River
    "riverName": "river_name",
    # Inspection
    "inspectionDate": "inspection_date",
    "inspectionFrequency": "inspection_frequency",
}

# Dam purpose codes
DAM_PURPOSES = {
    "I": "irrigation",
    "H": "hydroelectric",
    "C": "flood_control",
    "N": "navigation",
    "S": "water_supply",
    "R": "recreation",
    "P": "fire_protection",
    "F": "fish_and_wildlife",
    "D": "debris_control",
    "T": "tailings",
    "G": "grade_stabilization",
    "O": "other",
}

# Dam type codes
DAM_TYPES = {
    "RE": "earth",
    "ER": "rockfill",
    "PG": "gravity",
    "CB": "buttress",
    "VA": "arch",
    "MV": "multi_arch",
    "RC": "roller_compacted",
    "CN": "concrete",
    "MS": "masonry",
    "ST": "stone",
    "TC": "timber_crib",
    "OT": "other",
}

# Unit conversions
FT_TO_M = 0.3048
ACFT_TO_M3 = 1233.48184
ACRES_TO_KM2 = 0.00404686
SQMI_TO_KM2 = 2.58999


def download_nid_csv(output_dir: Path, force: bool = False) -> Path:
    """Download the full NID CSV from USACE."""
    csv_path = output_dir / "nid_raw.csv"

    if csv_path.exists() and not force:
        log.info(f"NID CSV already exists at {csv_path} ({csv_path.stat().st_size / 1e6:.1f} MB)")
        return csv_path

    log.info("Downloading NID CSV from USACE (this may take a minute)...")
    headers = {
        "User-Agent": "OpenCatch-Bathymetry/1.0 (research; reservoir-monitoring)",
        "Accept": "text/csv",
    }

    try:
        resp = requests.get(NID_CSV_URL, headers=headers, timeout=120, stream=True)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        with open(csv_path, "wb") as f:
            with tqdm(total=total, unit="B", unit_scale=True, desc="Downloading NID") as pbar:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    pbar.update(len(chunk))

        log.info(f"Downloaded {csv_path.stat().st_size / 1e6:.1f} MB to {csv_path}")
        return csv_path

    except requests.exceptions.RequestException as e:
        log.error(f"Failed to download NID CSV: {e}")
        log.info("Falling back to local file if available...")
        if csv_path.exists():
            return csv_path
        raise


def parse_nid(csv_path: Path) -> pd.DataFrame:
    """Parse NID CSV into a clean DataFrame with derived features."""
    log.info(f"Parsing NID CSV from {csv_path}...")

    # NID CSVs have a metadata row at the top ("Data Last Updated:,2026-3-15")
    # Detect and skip it by checking if the first cell looks like a header
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        first_line = f.readline()
    skiprows = 1 if first_line.startswith("Data Last Updated") else 0
    if skiprows:
        log.info(f"  Skipping metadata row: {first_line.strip()}")

    df = pd.read_csv(csv_path, low_memory=False, encoding="utf-8-sig", skiprows=skiprows)
    log.info(f"Raw NID: {len(df)} dams, {len(df.columns)} columns")

    # CSV column name mapping (2026 NID CSV format)
    CSV_COLUMN_MAP = {
        "Dam Name": "dam_name",
        "NID ID": "nid_id",
        "Other Names": "other_names",
        "State": "state",
        "County": "county",
        "Latitude": "latitude",
        "Longitude": "longitude",
        "Dam Height (Ft)": "dam_height_ft",
        "Dam Length (Ft)": "dam_length_ft",
        "Hydraulic Height (Ft)": "hydraulic_height_ft",
        "Structural Height (Ft)": "structural_height_ft",
        "NID Height (Ft)": "nid_height_ft",
        "NID Storage (Acre-Ft)": "nid_storage_acft",
        "Max Storage (Acre-Ft)": "max_storage_acft",
        "Normal Storage (Acre-Ft)": "normal_storage_acft",
        "Surface Area (Acres)": "surface_area_acres",
        "Drainage Area (Sq Miles)": "drainage_area_sqmi",
        "Max Discharge (Cubic Ft/Second)": "max_discharge_cfs",
        "Primary Dam Type": "dam_type",
        "Primary Purpose": "primary_purpose",
        "Purposes": "purposes",
        "Hazard Potential Classification": "hazard_potential",
        "Condition Assessment": "condition_assessment",
        "Condition Assessment Date": "condition_date",
        "Primary Owner Type": "owner_type",
        "Year Completed": "year_completed",
        "Years Modified": "year_modified",
        "River or Stream Name": "river_name",
        "Last Inspection Date": "inspection_date",
    }

    # First try API-style columns (camelCase), then CSV-style (title case)
    rename_map = {}
    for nid_col, our_col in NID_COLUMNS.items():
        if nid_col in df.columns:
            rename_map[nid_col] = our_col
        else:
            for c in df.columns:
                if c.lower() == nid_col.lower():
                    rename_map[c] = our_col
                    break

    # Also try CSV-specific mappings
    for csv_col, our_col in CSV_COLUMN_MAP.items():
        if csv_col in df.columns and our_col not in rename_map.values():
            rename_map[csv_col] = our_col

    df = df.rename(columns=rename_map)
    log.info(f"  Mapped {len(rename_map)} columns: {list(rename_map.values())[:10]}...")

    # Keep only columns we mapped + any extras we found
    all_target_cols = set(NID_COLUMNS.values()) | set(CSV_COLUMN_MAP.values())
    keep_cols = [c for c in all_target_cols if c in df.columns]
    df = df[keep_cols].copy()
    log.info(f"Retained {len(keep_cols)} columns")

    # -- Type conversions and cleaning --

    # Numeric columns (NID sometimes has empty strings or special chars)
    numeric_cols = [
        "latitude", "longitude", "dam_height_ft", "dam_length_ft",
        "hydraulic_height_ft", "structural_height_ft", "nid_height_ft",
        "nid_storage_acft", "max_storage_acft", "normal_storage_acft",
        "surface_area_acres", "drainage_area_sqmi", "max_discharge_cfs",
        "crest_elevation_ft", "year_completed", "year_modified",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Filter: must have location
    n_before = len(df)
    df = df.dropna(subset=["latitude", "longitude"])
    log.info(f"Dropped {n_before - len(df)} dams without coordinates")

    # Filter: must have dam height or storage (otherwise not a real reservoir)
    n_before = len(df)
    has_height = df["dam_height_ft"].notna() if "dam_height_ft" in df.columns else pd.Series(False, index=df.index)
    has_storage = df["max_storage_acft"].notna() if "max_storage_acft" in df.columns else pd.Series(False, index=df.index)
    df = df[has_height | has_storage].copy()
    log.info(f"Dropped {n_before - len(df)} dams without height or storage")

    return df


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add metric conversions, derived ratios, and classification features."""
    log.info("Computing derived features...")

    # -- Metric conversions --
    if "dam_height_ft" in df.columns:
        df["dam_height_m"] = df["dam_height_ft"] * FT_TO_M
    if "max_storage_acft" in df.columns:
        df["max_storage_m3"] = df["max_storage_acft"] * ACFT_TO_M3
    if "normal_storage_acft" in df.columns:
        df["normal_storage_m3"] = df["normal_storage_acft"] * ACFT_TO_M3
    if "surface_area_acres" in df.columns:
        df["surface_area_km2"] = df["surface_area_acres"] * ACRES_TO_KM2
    if "drainage_area_sqmi" in df.columns:
        df["drainage_area_km2"] = df["drainage_area_sqmi"] * SQMI_TO_KM2
    if "crest_elevation_ft" in df.columns:
        df["crest_elevation_m"] = df["crest_elevation_ft"] * FT_TO_M

    # -- Derived ratios --
    if "dam_height_m" in df.columns and "surface_area_km2" in df.columns:
        # Tall narrow dam vs wide shallow dam
        df["height_to_area_ratio"] = df["dam_height_m"] / df["surface_area_km2"].clip(lower=0.001)

    if "normal_storage_m3" in df.columns and "max_storage_m3" in df.columns:
        # Operating range indicator (how much storage varies)
        df["storage_ratio"] = df["normal_storage_m3"] / df["max_storage_m3"].clip(lower=1.0)

    if "dam_height_m" in df.columns and "crest_elevation_m" in df.columns:
        # Estimated base elevation (bottom of dam)
        df["base_elevation_m"] = df["crest_elevation_m"] - df["dam_height_m"]

    if "year_completed" in df.columns:
        df["dam_age_years"] = 2026 - df["year_completed"]
        df["dam_age_years"] = df["dam_age_years"].clip(lower=0, upper=200)

    if "drainage_area_km2" in df.columns and "surface_area_km2" in df.columns:
        df["watershed_lake_ratio"] = df["drainage_area_km2"] / df["surface_area_km2"].clip(lower=0.001)

    # -- Purpose one-hot encoding --
    if "purposes" in df.columns:
        for code, name in DAM_PURPOSES.items():
            df[f"purpose_{name}"] = df["purposes"].str.contains(code, na=False).astype(int)

    # -- Dam type encoding --
    if "dam_type" in df.columns:
        # Extract primary type (first 2 chars)
        df["dam_type_primary"] = df["dam_type"].str[:2].map(DAM_TYPES).fillna("unknown")

    # -- Hazard encoding --
    if "hazard_potential" in df.columns:
        hazard_map = {"H": 3, "S": 2, "L": 1, "U": 0}
        df["hazard_numeric"] = df["hazard_potential"].str[0].map(hazard_map).fillna(0).astype(int)

    # -- Log-transformed features for ML --
    if "surface_area_km2" in df.columns:
        df["log_area_km2"] = np.log1p(df["surface_area_km2"])
    if "max_storage_m3" in df.columns:
        df["log_storage_m3"] = np.log1p(df["max_storage_m3"])
    if "dam_height_m" in df.columns:
        df["log_dam_height_m"] = np.log1p(df["dam_height_m"])

    return df


def crossref_swot_pld(
    df: pd.DataFrame,
    pld_path: Optional[Path] = None,
    max_dist_m: float = 500.0,
) -> pd.DataFrame:
    """Cross-reference NID dams to SWOT Prior Lake Database IDs.

    Matches NID dam locations to PLD reservoir polygons (type=2) by
    spatial proximity. A dam within max_dist_m of a PLD centroid is
    considered a match.

    If no PLD file is available, adds empty pld_lake_id column.
    """
    log.info("Cross-referencing NID dams to SWOT PLD IDs...")

    df["pld_lake_id"] = None

    if pld_path is None or not Path(pld_path).exists():
        log.warning("No PLD file provided -- skipping SWOT crossref. "
                     "Run fetch_swot_data.py --method pld first to generate it.")
        return df

    try:
        import geopandas as gpd
        from scipy.spatial import cKDTree

        # Load PLD
        pld = gpd.read_file(pld_path)
        # Filter to reservoirs (type=2: last digit of lake_id)
        pld["wb_type"] = pld["lake_id"].astype(str).str[-1].astype(int)
        pld_res = pld[pld["wb_type"] == 2].copy()
        log.info(f"PLD has {len(pld_res)} reservoir entries")

        if len(pld_res) == 0:
            log.warning("No reservoirs found in PLD")
            return df

        # Build KD-tree of PLD centroids
        pld_coords = np.column_stack([
            pld_res.geometry.centroid.x.values,
            pld_res.geometry.centroid.y.values,
        ])
        tree = cKDTree(pld_coords)

        # Query NID dam locations
        nid_coords = np.column_stack([
            df["longitude"].values,
            df["latitude"].values,
        ])

        # Approximate meters from degrees at mid-latitude
        deg_to_m = 111_000  # rough approximation
        max_dist_deg = max_dist_m / deg_to_m

        distances, indices = tree.query(nid_coords, distance_upper_bound=max_dist_deg)

        # Assign PLD IDs where match found
        matched = distances < max_dist_deg
        df.loc[matched, "pld_lake_id"] = pld_res.iloc[indices[matched]]["lake_id"].values

        n_matched = matched.sum()
        log.info(f"Matched {n_matched} NID dams to PLD reservoir IDs "
                 f"({n_matched / len(df) * 100:.1f}%)")

    except ImportError:
        log.warning("geopandas or scipy not available -- skipping PLD crossref")
    except Exception as e:
        log.error(f"PLD crossref failed: {e}")

    return df


def filter_reservoirs(
    df: pd.DataFrame,
    states: Optional[list] = None,
    min_storage_acft: float = 0,
    min_height_ft: float = 0,
    hazard_levels: Optional[list] = None,
) -> pd.DataFrame:
    """Apply filters to select target reservoirs."""
    n_start = len(df)

    if states:
        df = df[df["state"].isin(states)]
        log.info(f"State filter ({states}): {n_start} → {len(df)}")

    if min_storage_acft > 0 and "max_storage_acft" in df.columns:
        n = len(df)
        df = df[df["max_storage_acft"].fillna(0) >= min_storage_acft]
        log.info(f"Min storage ({min_storage_acft} ac-ft): {n} → {len(df)}")

    if min_height_ft > 0 and "dam_height_ft" in df.columns:
        n = len(df)
        df = df[df["dam_height_ft"].fillna(0) >= min_height_ft]
        log.info(f"Min height ({min_height_ft} ft): {n} → {len(df)}")

    if hazard_levels and "hazard_potential" in df.columns:
        n = len(df)
        df = df[df["hazard_potential"].str[0].isin(hazard_levels)]
        log.info(f"Hazard filter ({hazard_levels}): {n} → {len(df)}")

    return df


def main():
    parser = argparse.ArgumentParser(
        description="Download and process National Inventory of Dams data"
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output directory for processed data",
    )
    parser.add_argument(
        "--states", nargs="+", default=None,
        help="Filter to specific states (e.g., MN WI IA)",
    )
    parser.add_argument(
        "--min-storage-acft", type=float, default=0,
        help="Minimum storage capacity in acre-feet (default: 0 = all)",
    )
    parser.add_argument(
        "--min-height-ft", type=float, default=0,
        help="Minimum dam height in feet (default: 0 = all)",
    )
    parser.add_argument(
        "--hazard", nargs="+", default=None,
        choices=["H", "S", "L", "U"],
        help="Hazard potential filter (H=high, S=significant, L=low, U=undetermined)",
    )
    parser.add_argument(
        "--pld-path", type=str, default=None,
        help="Path to SWOT PLD GeoPackage for cross-referencing",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-download even if file exists",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Download
    csv_path = download_nid_csv(output_dir, force=args.force)

    # Step 2: Parse
    df = parse_nid(csv_path)

    # Step 3: Derived features
    df = add_derived_features(df)

    # Step 4: Cross-reference SWOT PLD
    df = crossref_swot_pld(df, pld_path=args.pld_path)

    # Step 5: Filter
    df = filter_reservoirs(
        df,
        states=args.states,
        min_storage_acft=args.min_storage_acft,
        min_height_ft=args.min_height_ft,
        hazard_levels=args.hazard,
    )

    # Step 6: Save
    out_path = output_dir / "nid_reservoir_catalog.parquet"
    df.to_parquet(out_path, index=False, engine="pyarrow")
    log.info(f"Saved {len(df)} reservoirs to {out_path}")

    # Summary statistics
    log.info("=== NID Reservoir Catalog Summary ===")
    log.info(f"  Total dams:           {len(df):,}")
    if "dam_height_m" in df.columns:
        log.info(f"  Dam height:           {df['dam_height_m'].describe().to_dict()}")
    if "max_storage_m3" in df.columns:
        log.info(f"  Max storage (M m3):   median={df['max_storage_m3'].median() / 1e6:.1f}, "
                 f"max={df['max_storage_m3'].max() / 1e6:.0f}")
    if "surface_area_km2" in df.columns:
        log.info(f"  Surface area (km2):   median={df['surface_area_km2'].median():.2f}, "
                 f"max={df['surface_area_km2'].max():.0f}")
    if "pld_lake_id" in df.columns:
        n_swot = df["pld_lake_id"].notna().sum()
        log.info(f"  SWOT PLD matched:     {n_swot:,} ({n_swot / len(df) * 100:.1f}%)")
    if "state" in df.columns:
        top_states = df["state"].value_counts().head(10)
        log.info(f"  Top states:           {top_states.to_dict()}")


if __name__ == "__main__":
    main()
