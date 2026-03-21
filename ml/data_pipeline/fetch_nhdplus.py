#!/usr/bin/env python3
"""
OpenCatch — NHDPlus HR Water Body Data Pipeline

Downloads NHDPlus High Resolution water body boundaries for ALL of the US.
Extracts NHDWaterbody (lakes/ponds/reservoirs), NHDArea (wide rivers/estuaries),
and NHDFlowline (rivers/streams) feature classes.

Data source: USGS National Map — NHDPlus HR FileGDB by HUC4 subregion
    https://www.usgs.gov/national-hydrography/access-national-hydrography-products

Output: GeoParquet files per HUC2 region with unified schema:
    permanent_id, name, ftype, fcode, area_sq_km, centroid_lat, centroid_lon,
    state_fips, huc2, huc4, geometry

Size estimates:
    - Raw NHDPlus HR GDB downloads: ~120 GB total across all HUC4s
    - Processed GeoParquet (waterbodies only): ~8 GB
    - Processed GeoParquet (flowlines simplified): ~15 GB
    - Peak RAM per HUC4: ~2 GB

Designed for Vast.ai / remote server execution.

Usage:
    # Download + process all US water bodies
    python fetch_nhdplus.py --output /data/nhdplus --mode all

    # Download only specific HUC2 regions
    python fetch_nhdplus.py --output /data/nhdplus --huc2 04,07,09

    # Resume interrupted download
    python fetch_nhdplus.py --output /data/nhdplus --mode all --resume

    # Process already-downloaded GDBs
    python fetch_nhdplus.py --output /data/nhdplus --mode process-only

Requirements:
    pip install requests geopandas pyarrow shapely tqdm fiona
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

import requests
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("fetch_nhdplus.log")],
)
log = logging.getLogger("fetch_nhdplus")

# ---------------------------------------------------------------------------
# NHDPlus HR download configuration
# ---------------------------------------------------------------------------

# NHDPlus HR GDBs are distributed by HUC4 subregion on the USGS S3 bucket.
# Each zip is ~0.5–3 GB and contains a FileGDB with all feature classes.
NHDPLUS_S3_BASE = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHDPlusHR/Beta/GDB/"
)

# HUC2 regions — we enumerate HUC4s within each
HUC2_REGIONS = {
    "01": "New England",
    "02": "Mid-Atlantic",
    "03": "South Atlantic-Gulf",
    "04": "Great Lakes",
    "05": "Ohio",
    "06": "Tennessee",
    "07": "Upper Mississippi",
    "08": "Lower Mississippi",
    "09": "Souris-Red-Rainy",
    "10": "Missouri",
    "11": "Arkansas-White-Red",
    "12": "Texas-Gulf",
    "13": "Rio Grande",
    "14": "Upper Colorado",
    "15": "Lower Colorado",
    "16": "Great Basin",
    "17": "Pacific Northwest",
    "18": "California",
    "19": "Alaska",
    "20": "Hawaii",
    "21": "Caribbean",
}

# USGS National Map API for listing available HUC4 downloads
NATIONAL_MAP_API = "https://tnmaccess.nationalmap.gov/api/v1/products"

# Fallback: USGS WFS for bbox-based queries when GDB download is unavailable
USGS_WFS = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer"

# Feature classes to extract from each GDB
FEATURE_CLASSES = {
    "NHDWaterbody": {
        "description": "Lakes, ponds, reservoirs, swamps",
        "geometry_type": "Polygon",
    },
    "NHDArea": {
        "description": "Wide river reaches, estuaries, inundation areas",
        "geometry_type": "Polygon",
    },
    "NHDFlowline": {
        "description": "Rivers, streams, canals, pipelines",
        "geometry_type": "LineString",
    },
}

# Columns to keep in output
OUTPUT_COLUMNS = [
    "permanent_id",
    "name",
    "ftype",
    "ftype_desc",
    "fcode",
    "area_sq_km",
    "lengthkm",
    "centroid_lat",
    "centroid_lon",
    "huc2",
    "huc4",
    "reachcode",
    "source_fc",
    "geometry",
]

# FType code descriptions (common ones)
FTYPE_DESCRIPTIONS = {
    390: "Lake/Pond",
    436: "Reservoir",
    466: "Swamp/Marsh",
    493: "Estuary",
    460: "Stream/River",
    558: "Artificial Path",
    334: "Canal/Ditch",
    336: "Canal/Ditch",
    420: "Foreshore",
    364: "Foreshore",
    378: "Ice Mass",
    403: "Inundation Area",
    431: "Playa",
    537: "Area of Complex Channels",
}

# Checkpoint file name
CHECKPOINT_FILE = "nhdplus_checkpoint.json"


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def download_file(
    url: str, dest: Path, chunk_size: int = 1024 * 1024, max_retries: int = 3
) -> bool:
    """Download a file with progress bar and retry logic."""
    for attempt in range(max_retries):
        try:
            # Support resume via Range header
            headers = {}
            mode = "wb"
            existing_size = 0
            if dest.exists():
                existing_size = dest.stat().st_size
                headers["Range"] = f"bytes={existing_size}-"
                mode = "ab"

            r = requests.get(url, stream=True, timeout=120, headers=headers)

            if r.status_code == 416:
                # Range not satisfiable — file already complete
                log.info(f"  {dest.name} already fully downloaded")
                return True

            if r.status_code == 206:
                total = existing_size + int(r.headers.get("content-length", 0))
                log.info(f"  Resuming from {existing_size / 1e6:.0f} MB")
            else:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                mode = "wb"
                existing_size = 0

            with open(dest, mode) as f, tqdm(
                total=total,
                initial=existing_size,
                unit="B",
                unit_scale=True,
                desc=dest.name,
            ) as bar:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    f.write(chunk)
                    bar.update(len(chunk))

            return True

        except Exception as e:
            log.warning(f"  Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))

    log.error(f"Failed to download {url} after {max_retries} attempts")
    return False


# ---------------------------------------------------------------------------
# HUC4 discovery
# ---------------------------------------------------------------------------


def discover_huc4_urls(huc2_filter: Optional[list[str]] = None) -> dict[str, str]:
    """
    Query the USGS National Map API to find NHDPlus HR GDB downloads for
    each HUC4 subregion.

    Returns: {huc4_code: download_url}
    """
    log.info("Discovering available NHDPlus HR downloads from National Map API...")
    huc4_urls = {}

    datasets_param = "National Hydrography Dataset Plus High Resolution (NHDPlus HR)"
    offset = 0
    page_size = 50

    while True:
        params = {
            "datasets": datasets_param,
            "prodFormats": "FileGDB",
            "offset": offset,
            "max": page_size,
            "outputFormat": "JSON",
        }

        try:
            r = requests.get(NATIONAL_MAP_API, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning(f"National Map API query failed at offset {offset}: {e}")
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            title = item.get("title", "")
            url = item.get("downloadURL", "")
            # Extract HUC4 from title — format like "NHDPLUS_H_0101_HU4_GDB.zip"
            for part in title.replace("_", " ").split():
                if len(part) == 4 and part.isdigit():
                    huc4 = part
                    huc2 = huc4[:2]
                    if huc2_filter and huc2 not in huc2_filter:
                        continue
                    if url:
                        huc4_urls[huc4] = url
                    break

        offset += page_size
        if offset >= data.get("total", 0):
            break

        time.sleep(0.5)

    log.info(f"Found {len(huc4_urls)} HUC4 downloads")

    # Fallback: construct URLs directly if API returned nothing
    if not huc4_urls:
        log.info("API returned no results, constructing HUC4 URLs from known pattern...")
        huc4_urls = _construct_huc4_urls(huc2_filter)

    return huc4_urls


def _construct_huc4_urls(huc2_filter: Optional[list[str]] = None) -> dict[str, str]:
    """
    Construct NHDPlus HR download URLs from the known S3 naming pattern.
    Pattern: NHDPLUS_H_{HUC4}_HU4_GDB.zip
    """
    # Known HUC4 subregions per HUC2 (approximate — some may not exist)
    # We try all 2-digit suffixes 01–22 for each HUC2
    huc4_urls = {}
    huc2s = huc2_filter if huc2_filter else list(HUC2_REGIONS.keys())

    for huc2 in huc2s:
        for suffix in range(1, 23):
            huc4 = f"{huc2}{suffix:02d}"
            url = f"{NHDPLUS_S3_BASE}NHDPLUS_H_{huc4}_HU4_GDB.zip"
            huc4_urls[huc4] = url

    return huc4_urls


# ---------------------------------------------------------------------------
# Checkpoint management
# ---------------------------------------------------------------------------


def load_checkpoint(output_dir: Path) -> dict:
    """Load checkpoint state from disk."""
    cp_file = output_dir / CHECKPOINT_FILE
    if cp_file.exists():
        with open(cp_file) as f:
            return json.load(f)
    return {"downloaded": [], "processed": [], "failed": []}


def save_checkpoint(output_dir: Path, checkpoint: dict):
    """Save checkpoint state to disk."""
    cp_file = output_dir / CHECKPOINT_FILE
    with open(cp_file, "w") as f:
        json.dump(checkpoint, f, indent=2)


# ---------------------------------------------------------------------------
# GDB processing
# ---------------------------------------------------------------------------


def extract_features_from_gdb(
    gdb_path: Path,
    huc4: str,
    feature_classes: list[str],
    min_area_sq_km: float = 0.0,
) -> "geopandas.GeoDataFrame":
    """
    Extract water body features from an NHDPlus HR FileGDB.

    Returns a GeoDataFrame with unified schema.
    """
    import fiona
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import mapping

    all_dfs = []

    for fc_name in feature_classes:
        try:
            layers = fiona.listlayers(str(gdb_path))
            if fc_name not in layers:
                log.debug(f"  {fc_name} not found in {gdb_path.name}")
                continue

            gdf = gpd.read_file(str(gdb_path), layer=fc_name)
            log.info(f"  {fc_name}: {len(gdf)} features")

            if gdf.empty:
                continue

            # Ensure CRS is EPSG:4326
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)

            # Standardize columns
            col_map = {}
            for col in gdf.columns:
                lc = col.lower()
                if lc in ("permanent_identifier", "permanent_id", "nhdplusid"):
                    col_map[col] = "permanent_id"
                elif lc in ("gnis_name", "gnis_nm"):
                    col_map[col] = "name"
                elif lc == "ftype":
                    col_map[col] = "ftype"
                elif lc == "fcode":
                    col_map[col] = "fcode"
                elif lc in ("areasqkm", "area_sq_km"):
                    col_map[col] = "area_sq_km"
                elif lc in ("lengthkm", "length_km"):
                    col_map[col] = "lengthkm"
                elif lc == "reachcode":
                    col_map[col] = "reachcode"

            gdf = gdf.rename(columns=col_map)

            # Compute area for polygons if not present
            if "area_sq_km" not in gdf.columns:
                if gdf.geometry.iloc[0].geom_type in ("Polygon", "MultiPolygon"):
                    # Project to equal-area for accurate area calc
                    gdf_ea = gdf.to_crs(epsg=6933)  # Cylindrical equal-area
                    gdf["area_sq_km"] = gdf_ea.geometry.area / 1e6
                else:
                    gdf["area_sq_km"] = np.nan

            # Filter by minimum area
            if min_area_sq_km > 0 and "area_sq_km" in gdf.columns:
                mask = gdf["area_sq_km"].isna() | (gdf["area_sq_km"] >= min_area_sq_km)
                gdf = gdf[mask]

            # Compute centroids
            centroids = gdf.geometry.centroid
            gdf["centroid_lat"] = centroids.y
            gdf["centroid_lon"] = centroids.x

            # Add metadata
            gdf["huc4"] = huc4
            gdf["huc2"] = huc4[:2]
            gdf["source_fc"] = fc_name

            # Add ftype description
            if "ftype" in gdf.columns:
                gdf["ftype_desc"] = gdf["ftype"].map(FTYPE_DESCRIPTIONS).fillna("")
            else:
                gdf["ftype_desc"] = ""

            # Fill missing columns
            for col in ["permanent_id", "name", "lengthkm", "reachcode"]:
                if col not in gdf.columns:
                    gdf[col] = np.nan if col in ("lengthkm",) else ""

            # Select output columns
            keep = [c for c in OUTPUT_COLUMNS if c in gdf.columns]
            gdf = gdf[keep]

            all_dfs.append(gdf)

        except Exception as e:
            log.warning(f"  Failed to read {fc_name} from {gdb_path.name}: {e}")

    if not all_dfs:
        return None

    import pandas as pd

    result = pd.concat(all_dfs, ignore_index=True)
    return gpd.GeoDataFrame(result, geometry="geometry", crs="EPSG:4326")


def process_huc4_gdb(
    zip_path: Path,
    output_dir: Path,
    huc4: str,
    feature_classes: list[str],
    min_area_sq_km: float = 0.0,
    keep_gdb: bool = False,
) -> Optional[Path]:
    """
    Unzip, extract features, save as GeoParquet, clean up.

    Returns path to output parquet file, or None on failure.
    """
    extract_dir = output_dir / "tmp" / huc4
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Unzip
        log.info(f"  Extracting {zip_path.name}...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # Find the .gdb directory inside
        gdb_dirs = list(extract_dir.rglob("*.gdb"))
        if not gdb_dirs:
            log.error(f"  No .gdb found in {zip_path.name}")
            return None

        gdb_path = gdb_dirs[0]
        log.info(f"  Processing {gdb_path.name}...")

        gdf = extract_features_from_gdb(gdb_path, huc4, feature_classes, min_area_sq_km)

        if gdf is None or gdf.empty:
            log.warning(f"  No features extracted from {huc4}")
            return None

        # Save as GeoParquet
        parquet_dir = output_dir / "parquet"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        out_path = parquet_dir / f"nhdplus_hr_{huc4}.parquet"
        gdf.to_parquet(out_path, index=False)
        log.info(f"  Saved {len(gdf)} features → {out_path.name} ({out_path.stat().st_size / 1e6:.1f} MB)")

        return out_path

    except zipfile.BadZipFile:
        log.error(f"  Corrupt zip file: {zip_path.name}")
        return None
    except Exception as e:
        log.error(f"  Processing failed for {huc4}: {e}")
        return None
    finally:
        # Clean up extracted GDB (large)
        if not keep_gdb and extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# WFS fallback for regions without GDB downloads
# ---------------------------------------------------------------------------


def fetch_waterbodies_wfs(
    bbox: tuple[float, float, float, float],
    output_dir: Path,
    min_area_sq_km: float = 0.01,
    max_records: int = 10000,
) -> Optional[Path]:
    """
    Fetch water bodies from USGS NHD WFS within a bounding box.
    Fallback method when GDB downloads are unavailable.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat)
        output_dir: Directory to save output
        min_area_sq_km: Minimum water body area to include
    """
    import geopandas as gpd

    url = f"{USGS_WFS}/6/query"  # Layer 6 = NHDWaterbody
    params = {
        "where": f"AreaSqKm >= {min_area_sq_km}",
        "geometry": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "Permanent_Identifier,GNIS_Name,AreaSqKm,FType,FCode,ReachCode",
        "f": "geojson",
        "returnGeometry": "true",
        "resultRecordCount": max_records,
    }

    log.info(f"  WFS query for bbox {bbox}...")
    try:
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning(f"  WFS query failed: {e}")
        return None

    features = data.get("features", [])
    if not features:
        return None

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    log.info(f"  WFS returned {len(gdf)} features")

    # Standardize
    col_map = {
        "Permanent_Identifier": "permanent_id",
        "GNIS_Name": "name",
        "AreaSqKm": "area_sq_km",
        "FType": "ftype",
        "FCode": "fcode",
        "ReachCode": "reachcode",
    }
    gdf = gdf.rename(columns={k: v for k, v in col_map.items() if k in gdf.columns})

    centroids = gdf.geometry.centroid
    gdf["centroid_lat"] = centroids.y
    gdf["centroid_lon"] = centroids.x
    gdf["source_fc"] = "NHDWaterbody_WFS"

    bbox_str = f"{bbox[0]:.0f}_{bbox[1]:.0f}"
    out_path = output_dir / f"wfs_{bbox_str}.parquet"
    gdf.to_parquet(out_path, index=False)

    return out_path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_download(
    output_dir: Path,
    huc2_filter: Optional[list[str]] = None,
    resume: bool = True,
    delete_zips: bool = False,
):
    """Download NHDPlus HR GDB zips for requested HUC4 subregions."""
    zip_dir = output_dir / "zips"
    zip_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = load_checkpoint(output_dir) if resume else {"downloaded": [], "processed": [], "failed": []}

    huc4_urls = discover_huc4_urls(huc2_filter)
    log.info(f"Total HUC4 regions to download: {len(huc4_urls)}")

    for huc4, url in sorted(huc4_urls.items()):
        if huc4 in checkpoint["downloaded"]:
            log.info(f"[{huc4}] Already downloaded, skipping")
            continue

        filename = url.split("/")[-1] if "/" in url else f"NHDPLUS_H_{huc4}_HU4_GDB.zip"
        dest = zip_dir / filename

        log.info(f"[{huc4}] Downloading {filename}...")
        success = download_file(url, dest)

        if success:
            checkpoint["downloaded"].append(huc4)
            save_checkpoint(output_dir, checkpoint)
        else:
            checkpoint["failed"].append(huc4)
            save_checkpoint(output_dir, checkpoint)

        # Be nice to USGS servers
        time.sleep(1)

    log.info(
        f"Download complete: {len(checkpoint['downloaded'])} succeeded, "
        f"{len(checkpoint['failed'])} failed"
    )


def run_process(
    output_dir: Path,
    huc2_filter: Optional[list[str]] = None,
    feature_classes: Optional[list[str]] = None,
    min_area_sq_km: float = 0.0,
    resume: bool = True,
    keep_gdb: bool = False,
):
    """Process downloaded GDB zips into GeoParquet."""
    zip_dir = output_dir / "zips"
    checkpoint = load_checkpoint(output_dir) if resume else {"downloaded": [], "processed": [], "failed": []}

    if feature_classes is None:
        feature_classes = list(FEATURE_CLASSES.keys())

    # Find all zips
    if not zip_dir.exists():
        log.error(f"No zip directory found at {zip_dir}")
        return

    zips = sorted(zip_dir.glob("*.zip"))
    log.info(f"Found {len(zips)} zip files to process")

    for zip_path in zips:
        # Extract HUC4 from filename
        parts = zip_path.stem.split("_")
        huc4 = None
        for p in parts:
            if len(p) == 4 and p.isdigit():
                huc4 = p
                break

        if not huc4:
            log.warning(f"Cannot determine HUC4 from {zip_path.name}, skipping")
            continue

        if huc2_filter:
            huc2 = huc4[:2]
            if huc2 not in huc2_filter:
                continue

        if huc4 in checkpoint.get("processed", []):
            log.info(f"[{huc4}] Already processed, skipping")
            continue

        log.info(f"[{huc4}] Processing {zip_path.name}...")
        result = process_huc4_gdb(
            zip_path, output_dir, huc4, feature_classes, min_area_sq_km, keep_gdb
        )

        if result:
            checkpoint.setdefault("processed", []).append(huc4)
        else:
            checkpoint.setdefault("process_failed", []).append(huc4)

        save_checkpoint(output_dir, checkpoint)

    log.info(f"Processing complete: {len(checkpoint.get('processed', []))} HUC4s processed")


def run_merge(output_dir: Path):
    """Merge all per-HUC4 parquet files into per-HUC2 and a master catalog."""
    import geopandas as gpd
    import pandas as pd

    parquet_dir = output_dir / "parquet"
    if not parquet_dir.exists():
        log.error(f"No parquet directory at {parquet_dir}")
        return

    files = sorted(parquet_dir.glob("nhdplus_hr_*.parquet"))
    log.info(f"Merging {len(files)} parquet files...")

    # Group by HUC2
    by_huc2 = {}
    for f in files:
        huc4 = f.stem.split("_")[-1]
        huc2 = huc4[:2]
        by_huc2.setdefault(huc2, []).append(f)

    merged_dir = output_dir / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)

    total_features = 0
    for huc2, huc4_files in sorted(by_huc2.items()):
        log.info(f"  Merging HUC2 {huc2} ({HUC2_REGIONS.get(huc2, '?')}) — {len(huc4_files)} files")
        dfs = [gpd.read_parquet(f) for f in huc4_files]
        merged = pd.concat(dfs, ignore_index=True)

        # Deduplicate by permanent_id
        if "permanent_id" in merged.columns:
            before = len(merged)
            merged = merged.drop_duplicates(subset="permanent_id", keep="first")
            if len(merged) < before:
                log.info(f"    Deduped: {before} → {len(merged)}")

        out = merged_dir / f"nhdplus_hr_huc2_{huc2}.parquet"
        gdf = gpd.GeoDataFrame(merged, geometry="geometry", crs="EPSG:4326")
        gdf.to_parquet(out, index=False)
        total_features += len(gdf)
        log.info(f"    {len(gdf)} features → {out.name}")

    # Write a lightweight master catalog (no geometry, just metadata)
    log.info("Building master catalog (no geometry)...")
    catalog_parts = []
    for f in sorted(merged_dir.glob("*.parquet")):
        gdf = gpd.read_parquet(f, columns=[c for c in OUTPUT_COLUMNS if c != "geometry"])
        catalog_parts.append(gdf)

    if catalog_parts:
        catalog = pd.concat(catalog_parts, ignore_index=True)
        catalog_path = output_dir / "us_waterbody_catalog.parquet"
        catalog.to_parquet(catalog_path, index=False)
        log.info(f"Master catalog: {len(catalog)} features → {catalog_path}")

    log.info(f"Total US water features: {total_features}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch — NHDPlus HR water body pipeline"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="/data/nhdplus",
        help="Output directory (default: /data/nhdplus)",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "download", "process-only", "merge", "wfs-fallback"],
        default="all",
        help="Pipeline mode",
    )
    parser.add_argument(
        "--huc2",
        type=str,
        default=None,
        help="Comma-separated HUC2 codes to filter (e.g. 04,07,09)",
    )
    parser.add_argument(
        "--min-area",
        type=float,
        default=0.0,
        help="Minimum water body area in sq km (0 = keep all)",
    )
    parser.add_argument(
        "--features",
        type=str,
        default="NHDWaterbody,NHDArea,NHDFlowline",
        help="Comma-separated feature classes to extract",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Resume from checkpoint (default: True)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh, ignore checkpoint",
    )
    parser.add_argument(
        "--keep-gdb",
        action="store_true",
        help="Keep extracted GDB files (uses lots of disk)",
    )
    parser.add_argument(
        "--delete-zips",
        action="store_true",
        help="Delete zip files after processing",
    )
    parser.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="Bounding box for WFS fallback: min_lon,min_lat,max_lon,max_lat",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    huc2_filter = [h.strip() for h in args.huc2.split(",")] if args.huc2 else None
    feature_classes = [f.strip() for f in args.features.split(",")]
    resume = not args.no_resume

    if args.mode == "wfs-fallback":
        if not args.bbox:
            log.error("--bbox required for wfs-fallback mode")
            sys.exit(1)
        bbox = tuple(float(x) for x in args.bbox.split(","))
        fetch_waterbodies_wfs(bbox, output_dir / "wfs", args.min_area)
        return

    if args.mode in ("all", "download"):
        run_download(output_dir, huc2_filter, resume, args.delete_zips)

    if args.mode in ("all", "process-only"):
        run_process(
            output_dir, huc2_filter, feature_classes, args.min_area, resume, args.keep_gdb
        )

    if args.mode in ("all", "merge"):
        run_merge(output_dir)


if __name__ == "__main__":
    main()
