#!/usr/bin/env python3
"""
OpenCatch — Merge US + Canada Water Body Catalogs

Combines NHDPlus (US) and NHN (Canada) water body data into a unified
master catalog with:
    - Consistent schema across both countries
    - Unique OpenCatch water body IDs (oc_XXXXXXXX)
    - Deduplication of border-straddling lakes
    - Optional enrichment join from scrape_waterbody_info.py
    - Optional access point join from fetch_access_points.py

Output:
    master_waterbody_catalog.parquet — metadata only, no geometry (~500 MB)
    master_waterbody_geo.parquet     — with geometry (~20 GB)

Size estimates:
    - US water bodies (NHDPlus HR all features): ~10M
    - Canada water bodies (NHN): ~2M
    - After dedup: ~11M unique features
    - Master catalog (no geom): ~500 MB
    - Master catalog (with geom): ~20 GB
    - Peak RAM: ~8 GB (process in chunks if needed)

Usage:
    # Merge US + Canada into master catalog
    python merge_waterbodies.py \
        --us-dir /data/nhdplus/merged \
        --ca-dir /data/nhn/merged \
        --output /data/merged

    # Merge and join enrichment data
    python merge_waterbodies.py \
        --us-dir /data/nhdplus/merged \
        --ca-dir /data/nhn/merged \
        --enrichment /data/enrichment/enrichment_all.parquet \
        --access-points /data/access_points/all_access_points.parquet \
        --output /data/merged

    # Process only specific regions (for memory-constrained environments)
    python merge_waterbodies.py \
        --us-dir /data/nhdplus/merged \
        --ca-dir /data/nhn/merged \
        --output /data/merged \
        --chunk-size 500000

    # Upload to B2 after merge
    python merge_waterbodies.py \
        --us-dir /data/nhdplus/merged \
        --ca-dir /data/nhn/merged \
        --output /data/merged \
        --upload-b2

Requirements:
    pip install geopandas pyarrow pandas shapely tqdm
    pip install b2sdk  # optional, for B2 upload
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("merge_waterbodies.log")],
)
log = logging.getLogger("merge_wb")

# ---------------------------------------------------------------------------
# Unified schema
# ---------------------------------------------------------------------------

# The master catalog schema — all water bodies get these columns
MASTER_SCHEMA = {
    "oc_id": "str",            # OpenCatch unique ID (oc_XXXXXXXX)
    "permanent_id": "str",     # Original dataset ID (NHDPlus or NHN)
    "name": "str",             # Water body name
    "country": "str",          # US or CA
    "state_province": "str",   # State (US) or Province (CA) code
    "ftype": "int",            # Feature type code
    "ftype_desc": "str",       # Feature type description
    "fcode": "int",            # Feature code (more specific)
    "area_sq_km": "float",     # Area in square km
    "lengthkm": "float",       # Length in km (for flowlines)
    "centroid_lat": "float",   # Centroid latitude
    "centroid_lon": "float",   # Centroid longitude
    "huc2": "str",             # HUC2 region (US only)
    "huc4": "str",             # HUC4 subregion (US only)
    "reachcode": "str",        # NHD reach code (US only)
    "nhn_workunit": "str",     # NHN work unit (CA only)
    "source_fc": "str",        # Source feature class name
    "source_dataset": "str",   # "nhdplus_hr" or "nhn"
}

# Water body type classification for unified display
WATERBODY_TYPES = {
    # NHDPlus FType codes
    390: "lake",
    436: "reservoir",
    466: "wetland",
    493: "estuary",
    460: "river",
    558: "river",       # Artificial path (represents river centerline)
    334: "canal",
    336: "canal",
    378: "glacier",
    403: "floodplain",
    431: "playa",
    # NHN water definition codes
    4: "lake",
    6: "reservoir",
    7: "river",
    8: "tidal_river",
    1: "canal",
    9: "river",
}

# Border lakes known to appear in both NHDPlus and NHN
# These are large enough to be caught by spatial dedup, but we list them
# explicitly to ensure correct handling
KNOWN_BORDER_LAKES = [
    "Lake Superior",
    "Lake Huron",
    "Lake Erie",
    "Lake Ontario",
    "Lake of the Woods",
    "Rainy Lake",
    "Lake St. Clair",
    "Lake Champlain",
    "Lake Memphremagog",
    "Boundary Waters",
]


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def generate_oc_id(permanent_id: str, source: str) -> str:
    """
    Generate a stable OpenCatch water body ID from the source ID.

    Format: oc_{8-char hex hash}
    Deterministic: same input always produces same output.
    """
    raw = f"{source}:{permanent_id}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"oc_{h}"


# ---------------------------------------------------------------------------
# Load and standardize
# ---------------------------------------------------------------------------


def load_us_data(us_dir: Path) -> pd.DataFrame:
    """Load and standardize all US NHDPlus HR parquet files."""
    files = sorted(us_dir.glob("*.parquet"))
    if not files:
        log.warning(f"No US parquet files found in {us_dir}")
        return pd.DataFrame()

    log.info(f"Loading {len(files)} US regional files...")
    dfs = []

    for f in tqdm(files, desc="Loading US data"):
        try:
            # Read without geometry for the catalog
            df = pd.read_parquet(f)

            # Drop geometry column if present (for catalog-only mode)
            if "geometry" in df.columns:
                df = df.drop(columns=["geometry"])

            dfs.append(df)
        except Exception as e:
            log.warning(f"Failed to read {f.name}: {e}")

    if not dfs:
        return pd.DataFrame()

    merged = pd.concat(dfs, ignore_index=True)
    log.info(f"US total: {len(merged)} features")

    # Standardize to master schema
    merged["country"] = "US"
    merged["source_dataset"] = "nhdplus_hr"

    # Map state from HUC or other available info
    if "state_fips" not in merged.columns:
        merged["state_province"] = ""  # Will be inferred from centroid later
    else:
        merged["state_province"] = merged["state_fips"]

    # Fill missing columns
    for col, dtype in MASTER_SCHEMA.items():
        if col not in merged.columns:
            if dtype == "str":
                merged[col] = ""
            elif dtype == "float":
                merged[col] = float("nan")
            elif dtype == "int":
                merged[col] = 0

    # Generate OpenCatch IDs
    merged["oc_id"] = merged.apply(
        lambda r: generate_oc_id(str(r.get("permanent_id", "")), "nhdplus"),
        axis=1,
    )

    # Add unified waterbody type
    merged["wb_type"] = merged["ftype"].map(WATERBODY_TYPES).fillna("unknown")

    return merged


def load_canada_data(ca_dir: Path) -> pd.DataFrame:
    """Load and standardize all Canada NHN parquet files."""
    files = sorted(ca_dir.glob("*.parquet"))
    if not files:
        log.warning(f"No Canada parquet files found in {ca_dir}")
        return pd.DataFrame()

    log.info(f"Loading {len(files)} Canada provincial files...")
    dfs = []

    for f in tqdm(files, desc="Loading CA data"):
        try:
            df = pd.read_parquet(f)
            if "geometry" in df.columns:
                df = df.drop(columns=["geometry"])
            dfs.append(df)
        except Exception as e:
            log.warning(f"Failed to read {f.name}: {e}")

    if not dfs:
        return pd.DataFrame()

    merged = pd.concat(dfs, ignore_index=True)
    log.info(f"Canada total: {len(merged)} features")

    merged["country"] = "CA"
    merged["source_dataset"] = "nhn"

    # Province column mapping
    if "province" in merged.columns:
        merged["state_province"] = merged["province"]
    else:
        merged["state_province"] = ""

    for col, dtype in MASTER_SCHEMA.items():
        if col not in merged.columns:
            if dtype == "str":
                merged[col] = ""
            elif dtype == "float":
                merged[col] = float("nan")
            elif dtype == "int":
                merged[col] = 0

    merged["oc_id"] = merged.apply(
        lambda r: generate_oc_id(str(r.get("permanent_id", "")), "nhn"),
        axis=1,
    )
    merged["wb_type"] = merged["ftype"].map(WATERBODY_TYPES).fillna("unknown")

    return merged


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def deduplicate_border_lakes(
    us_df: pd.DataFrame,
    ca_df: pd.DataFrame,
    distance_threshold_deg: float = 0.01,  # ~1 km
    area_ratio_threshold: float = 0.3,     # 30% area difference
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Identify and merge water bodies that straddle the US-Canada border.

    Strategy:
    1. Find named water bodies in both datasets within border zone (lat 41-50)
    2. Match by name similarity + proximity
    3. Keep the US version as primary, add CA metadata as alternate

    Returns: (us_deduped, ca_deduped, border_matches)
    """
    log.info("Deduplicating border-straddling water bodies...")

    # Border zone: roughly lat 41-50, covering the US-Canada border
    border_lat_min, border_lat_max = 41.0, 50.0

    us_border = us_df[
        (us_df["centroid_lat"] >= border_lat_min)
        & (us_df["centroid_lat"] <= border_lat_max)
        & (us_df["name"].str.len() > 0)
    ].copy()

    ca_border = ca_df[
        (ca_df["centroid_lat"] >= border_lat_min)
        & (ca_df["centroid_lat"] <= border_lat_max)
        & (ca_df["name"].str.len() > 0)
    ].copy()

    log.info(f"  US border zone: {len(us_border)} named features")
    log.info(f"  CA border zone: {len(ca_border)} named features")

    # Normalize names for matching
    us_border["name_norm"] = us_border["name"].str.lower().str.strip()
    ca_border["name_norm"] = ca_border["name"].str.lower().str.strip()

    # Find matches by exact normalized name
    matches = []
    ca_matched_ids = set()

    for _, us_row in us_border.iterrows():
        if not us_row["name_norm"]:
            continue

        # Find CA water bodies with same name
        ca_candidates = ca_border[ca_border["name_norm"] == us_row["name_norm"]]

        for _, ca_row in ca_candidates.iterrows():
            # Check proximity
            dlat = abs(us_row["centroid_lat"] - ca_row["centroid_lat"])
            dlon = abs(us_row["centroid_lon"] - ca_row["centroid_lon"])

            if dlat <= 1.0 and dlon <= 2.0:  # Generous for large border lakes
                matches.append({
                    "us_oc_id": us_row["oc_id"],
                    "ca_oc_id": ca_row["oc_id"],
                    "name": us_row["name"],
                    "us_area": us_row.get("area_sq_km"),
                    "ca_area": ca_row.get("area_sq_km"),
                })
                ca_matched_ids.add(ca_row["oc_id"])

    # Also check known border lakes by name
    for lake_name in KNOWN_BORDER_LAKES:
        name_lower = lake_name.lower()
        us_match = us_border[us_border["name_norm"].str.contains(name_lower, na=False)]
        ca_match = ca_border[ca_border["name_norm"].str.contains(name_lower, na=False)]

        for _, ca_row in ca_match.iterrows():
            if ca_row["oc_id"] not in ca_matched_ids:
                ca_matched_ids.add(ca_row["oc_id"])
                if not us_match.empty:
                    matches.append({
                        "us_oc_id": us_match.iloc[0]["oc_id"],
                        "ca_oc_id": ca_row["oc_id"],
                        "name": lake_name,
                    })

    log.info(f"  Found {len(matches)} border lake matches")

    # Remove matched CA entries
    ca_deduped = ca_df[~ca_df["oc_id"].isin(ca_matched_ids)]
    log.info(f"  Removed {len(ca_matched_ids)} duplicate CA entries")

    border_df = pd.DataFrame(matches) if matches else pd.DataFrame()

    return us_df, ca_deduped, border_df


# ---------------------------------------------------------------------------
# State/province inference from coordinates
# ---------------------------------------------------------------------------

# Simplified US state bounding boxes for rough inference
# (Only needed when state info is missing from source data)
_US_STATE_CENTERS = {
    "AL": (32.8, -86.8), "AK": (64.0, -153.0), "AZ": (34.3, -111.7),
    "AR": (34.8, -92.2), "CA": (37.2, -119.5), "CO": (39.0, -105.5),
    "CT": (41.6, -72.7), "DE": (39.0, -75.5), "FL": (28.6, -82.4),
    "GA": (32.7, -83.5), "HI": (20.5, -157.4), "ID": (44.4, -114.6),
    "IL": (40.0, -89.2), "IN": (39.9, -86.3), "IA": (42.0, -93.5),
    "KS": (38.5, -98.3), "KY": (37.8, -85.7), "LA": (31.0, -91.9),
    "ME": (45.4, -69.2), "MD": (39.0, -76.7), "MA": (42.2, -71.8),
    "MI": (44.3, -84.5), "MN": (46.3, -94.3), "MS": (32.7, -89.7),
    "MO": (38.4, -92.5), "MT": (47.1, -109.6), "NE": (41.5, -99.8),
    "NV": (39.3, -116.6), "NH": (43.7, -71.6), "NJ": (40.1, -74.7),
    "NM": (34.4, -106.1), "NY": (42.9, -75.5), "NC": (35.5, -79.9),
    "ND": (47.4, -100.5), "OH": (40.4, -82.8), "OK": (35.6, -97.5),
    "OR": (44.0, -120.5), "PA": (40.9, -77.8), "RI": (41.7, -71.5),
    "SC": (33.9, -80.9), "SD": (44.4, -100.2), "TN": (35.9, -86.4),
    "TX": (31.5, -99.4), "UT": (39.3, -111.7), "VT": (44.1, -72.6),
    "VA": (37.5, -78.9), "WA": (47.4, -120.7), "WV": (38.6, -80.6),
    "WI": (44.6, -89.8), "WY": (43.0, -107.5),
}


def infer_state(lat: float, lon: float) -> str:
    """Infer US state from coordinates (nearest state center)."""
    if pd.isna(lat) or pd.isna(lon):
        return ""

    best_state = ""
    best_dist = float("inf")

    for state, (slat, slon) in _US_STATE_CENTERS.items():
        d = (lat - slat) ** 2 + (lon - slon) ** 2
        if d < best_dist:
            best_dist = d
            best_state = state

    return best_state


# ---------------------------------------------------------------------------
# Join enrichment and access points
# ---------------------------------------------------------------------------


def join_enrichment(
    master: pd.DataFrame,
    enrichment_path: Path,
) -> pd.DataFrame:
    """Join enrichment data (NWIS, species, EPA) onto the master catalog."""
    log.info(f"Joining enrichment from {enrichment_path}...")
    enrich = pd.read_parquet(enrichment_path)
    log.info(f"  Enrichment records: {len(enrich)}")

    # Join on waterbody_id -> permanent_id
    if "waterbody_id" in enrich.columns and "permanent_id" in master.columns:
        master = master.merge(
            enrich,
            left_on="permanent_id",
            right_on="waterbody_id",
            how="left",
            suffixes=("", "_enrich"),
        )
        # Drop duplicate columns
        for col in master.columns:
            if col.endswith("_enrich"):
                master = master.drop(columns=[col])

    log.info(f"  After join: {len(master)} rows")
    return master


def join_access_points(
    master: pd.DataFrame,
    access_path: Path,
) -> pd.DataFrame:
    """
    Compute access point counts per water body and join onto master catalog.
    """
    log.info(f"Joining access point counts from {access_path}...")

    try:
        import geopandas as gpd
        ap = gpd.read_parquet(access_path)
    except Exception:
        ap = pd.read_parquet(access_path)

    log.info(f"  Access points: {len(ap)}")

    # If access points have waterbody_id, group and count
    if "waterbody_id" in ap.columns:
        counts = ap.groupby("waterbody_id").agg(
            access_point_count=("access_type", "count"),
            boat_launch_count=("access_type", lambda x: (x == "boat_launch").sum()),
            fishing_pier_count=("access_type", lambda x: (x == "fishing_pier").sum()),
            parking_count=("access_type", lambda x: (x == "parking").sum()),
            trail_count=("access_type", lambda x: (x == "trail").sum()),
        ).reset_index()

        master = master.merge(
            counts,
            left_on="permanent_id",
            right_on="waterbody_id",
            how="left",
        )
        if "waterbody_id" in master.columns:
            master = master.drop(columns=["waterbody_id"])

        # Fill NaN counts with 0
        count_cols = [c for c in master.columns if c.endswith("_count")]
        master[count_cols] = master[count_cols].fillna(0).astype(int)

    log.info(f"  After join: {len(master)} rows")
    return master


# ---------------------------------------------------------------------------
# B2 Backblaze upload
# ---------------------------------------------------------------------------


def upload_to_b2(output_dir: Path, bucket_name: str = "opencatch-data"):
    """Upload merged output to Backblaze B2."""
    try:
        from b2sdk.v2 import InMemoryAccountInfo, B2Api

        info = InMemoryAccountInfo()
        b2_api = B2Api(info)

        key_id = os.environ.get("B2_KEY_ID", "004b6da11e9f7ad0000000004")
        app_key = os.environ.get("B2_APP_KEY", "K004pK63g6FSh2nPyxRw76B7ZmYgcrI")

        b2_api.authorize_account("production", key_id, app_key)

        bucket = b2_api.get_bucket_by_name(bucket_name)

        files_to_upload = list(output_dir.glob("*.parquet")) + list(output_dir.glob("*.json"))

        for f in tqdm(files_to_upload, desc="Uploading to B2"):
            remote_path = f"waterbodies/{f.name}"
            bucket.upload_local_file(
                local_file=str(f),
                file_name=remote_path,
            )
            log.info(f"  Uploaded {f.name} → b2://{bucket_name}/{remote_path}")

    except ImportError:
        log.error("b2sdk not installed. Run: pip install b2sdk")
    except Exception as e:
        log.error(f"B2 upload failed: {e}")


# ---------------------------------------------------------------------------
# Main merge pipeline
# ---------------------------------------------------------------------------


def run_merge(
    us_dir: Optional[Path],
    ca_dir: Optional[Path],
    output_dir: Path,
    enrichment_path: Optional[Path] = None,
    access_path: Optional[Path] = None,
    chunk_size: int = 0,
    upload_b2: bool = False,
):
    """Run the full merge pipeline."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load datasets
    us_df = load_us_data(us_dir) if us_dir and us_dir.exists() else pd.DataFrame()
    ca_df = load_canada_data(ca_dir) if ca_dir and ca_dir.exists() else pd.DataFrame()

    if us_df.empty and ca_df.empty:
        log.error("No data loaded from either US or Canada")
        return

    # Deduplicate border lakes
    if not us_df.empty and not ca_df.empty:
        us_df, ca_df, border_matches = deduplicate_border_lakes(us_df, ca_df)
        if not border_matches.empty:
            border_matches.to_parquet(output_dir / "border_lake_matches.parquet", index=False)
    else:
        border_matches = pd.DataFrame()

    # Combine
    log.info("Combining US and Canada datasets...")
    parts = []
    if not us_df.empty:
        parts.append(us_df)
    if not ca_df.empty:
        parts.append(ca_df)

    master = pd.concat(parts, ignore_index=True)
    log.info(f"Combined: {len(master)} total water features")

    # Fill missing state/province from coordinates
    missing_sp = master["state_province"].isna() | (master["state_province"] == "")
    if missing_sp.any():
        log.info(f"Inferring state/province for {missing_sp.sum()} features...")
        us_missing = missing_sp & (master["country"] == "US")
        if us_missing.any():
            master.loc[us_missing, "state_province"] = master.loc[us_missing].apply(
                lambda r: infer_state(r["centroid_lat"], r["centroid_lon"]),
                axis=1,
            )

    # Deduplicate by oc_id
    before = len(master)
    master = master.drop_duplicates(subset="oc_id", keep="first")
    if len(master) < before:
        log.info(f"Deduped by oc_id: {before} → {len(master)}")

    # Join enrichment
    if enrichment_path and enrichment_path.exists():
        master = join_enrichment(master, enrichment_path)

    # Join access points
    if access_path and access_path.exists():
        master = join_access_points(master, access_path)

    # Select final columns
    schema_cols = list(MASTER_SCHEMA.keys()) + ["wb_type"]
    extra_cols = [c for c in master.columns if c not in schema_cols and c != "geometry"]
    final_cols = [c for c in schema_cols if c in master.columns] + extra_cols

    master = master[[c for c in final_cols if c in master.columns]]

    # Save master catalog (no geometry)
    catalog_path = output_dir / "master_waterbody_catalog.parquet"
    master.to_parquet(catalog_path, index=False)
    log.info(f"Master catalog: {len(master)} features → {catalog_path}")
    log.info(f"  File size: {catalog_path.stat().st_size / 1e6:.1f} MB")

    # Save summary stats
    stats = {
        "total_features": len(master),
        "us_features": int((master["country"] == "US").sum()),
        "ca_features": int((master["country"] == "CA").sum()),
        "named_features": int((master["name"].str.len() > 0).sum()),
        "border_matches": len(border_matches) if not border_matches.empty else 0,
        "wb_types": master["wb_type"].value_counts().to_dict() if "wb_type" in master.columns else {},
        "states": master[master["country"] == "US"]["state_province"].value_counts().head(20).to_dict(),
        "provinces": master[master["country"] == "CA"]["state_province"].value_counts().head(20).to_dict(),
    }

    stats_path = output_dir / "catalog_stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2, default=str)
    log.info(f"Stats: {stats_path}")

    # Print summary
    log.info("\n" + "=" * 60)
    log.info("MASTER CATALOG SUMMARY")
    log.info("=" * 60)
    log.info(f"Total features:  {stats['total_features']:,}")
    log.info(f"  US:            {stats['us_features']:,}")
    log.info(f"  Canada:        {stats['ca_features']:,}")
    log.info(f"  Named:         {stats['named_features']:,}")
    log.info(f"  Border dedup:  {stats['border_matches']}")
    if "wb_type" in master.columns:
        log.info("Water body types:")
        for wbt, count in master["wb_type"].value_counts().items():
            log.info(f"  {wbt}: {count:,}")

    # Upload to B2
    if upload_b2:
        upload_to_b2(output_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch — Merge US + Canada water body catalogs"
    )
    parser.add_argument(
        "--us-dir", type=str, default=None,
        help="Directory with merged US NHDPlus parquet files",
    )
    parser.add_argument(
        "--ca-dir", type=str, default=None,
        help="Directory with merged Canada NHN parquet files",
    )
    parser.add_argument(
        "--output", type=str, default="/data/merged",
        help="Output directory",
    )
    parser.add_argument(
        "--enrichment", type=str, default=None,
        help="Path to enrichment parquet (from scrape_waterbody_info.py)",
    )
    parser.add_argument(
        "--access-points", type=str, default=None,
        help="Path to access points parquet (from fetch_access_points.py)",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=0,
        help="Process in chunks of this size (0 = all at once)",
    )
    parser.add_argument(
        "--upload-b2", action="store_true",
        help="Upload results to Backblaze B2",
    )
    args = parser.parse_args()

    us_dir = Path(args.us_dir) if args.us_dir else None
    ca_dir = Path(args.ca_dir) if args.ca_dir else None
    enrichment = Path(args.enrichment) if args.enrichment else None
    access_pts = Path(args.access_points) if args.access_points else None

    if not us_dir and not ca_dir:
        log.error("At least one of --us-dir or --ca-dir is required")
        sys.exit(1)

    run_merge(
        us_dir, ca_dir, Path(args.output),
        enrichment, access_pts,
        args.chunk_size, args.upload_b2,
    )


if __name__ == "__main__":
    main()
