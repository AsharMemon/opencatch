#!/usr/bin/env python3
"""
OpenCatch — Canadian National Hydro Network (NHN) Water Body Pipeline

Downloads ALL Canadian water body data from the National Hydro Network (NHN)
maintained by Natural Resources Canada.

Data source: GeoGratis FTP — GeoPackage format organized by NHN Work Unit
    https://open.canada.ca/data/en/dataset/a4b190fe-e090-4e6d-881e-b87956c07977
    FTP: https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/

NHN is organized into ~990 "work units" (drainage areas). Each work unit GPKG
contains layers including:
    - NHN_HN_WATERBODY_2  — lakes, ponds, reservoirs
    - NHN_HN_NLFLOW_1     — named linear flow (rivers/streams)
    - NHN_HN_WORKUNIT_LIMIT_2 — work unit boundary

Output: GeoParquet per province with unified schema matching NHDPlus output:
    permanent_id, name, ftype, area_sq_km, centroid_lat, centroid_lon,
    province, nhn_workunit, geometry

Size estimates:
    - NHN work unit index: ~5 MB
    - All NHN GPKGs: ~60 GB total
    - Processed GeoParquet (waterbodies): ~5 GB
    - Peak RAM per work unit: ~500 MB

Designed for Vast.ai / remote server execution.

Usage:
    # Download + process all Canadian water bodies
    python fetch_nhn_canada.py --output /data/nhn --mode all

    # Download only specific provinces (2-letter codes)
    python fetch_nhn_canada.py --output /data/nhn --provinces ON,QC,BC

    # Resume interrupted download
    python fetch_nhn_canada.py --output /data/nhn --mode all --resume

    # Process already-downloaded GPKGs
    python fetch_nhn_canada.py --output /data/nhn --mode process-only

Requirements:
    pip install requests geopandas pyarrow shapely tqdm fiona
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional
from html.parser import HTMLParser

import requests
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("fetch_nhn_canada.log")],
)
log = logging.getLogger("fetch_nhn_canada")

# ---------------------------------------------------------------------------
# NHN configuration
# ---------------------------------------------------------------------------

# NHN GeoPackage FTP base URL
NHN_FTP_BASE = "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/"
NHN_GPKG_BASE = f"{NHN_FTP_BASE}gpkg/"

# NHN work unit index (shapefile) — maps work unit IDs to provinces
NHN_INDEX_URL = f"{NHN_FTP_BASE}index/nhn_index_workunit.zip"

# Canadian provinces and territories (2-letter codes)
PROVINCES = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}

# NHN feature layers to extract from each GPKG
NHN_LAYERS = {
    "NHN_HN_WATERBODY_2": {
        "description": "Lakes, ponds, reservoirs, islands",
        "geometry_type": "Polygon",
    },
    "NHN_HN_NLFLOW_1": {
        "description": "Named linear flow (rivers/streams)",
        "geometry_type": "LineString",
    },
}

# NHN water body definition codes
NHN_WATER_DEFINITION = {
    1: "Canal",
    2: "Conduit",
    3: "Ditch",
    4: "Lake",
    5: "Liquid Waste",
    6: "Reservoir",
    7: "River",
    8: "Tidal River",
    9: "Watercourse",
}

CHECKPOINT_FILE = "nhn_checkpoint.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def download_file(
    url: str, dest: Path, chunk_size: int = 1024 * 1024, max_retries: int = 3
) -> bool:
    """Download a file with progress bar and retry + resume support."""
    for attempt in range(max_retries):
        try:
            headers = {}
            mode = "wb"
            existing_size = 0
            if dest.exists():
                existing_size = dest.stat().st_size
                headers["Range"] = f"bytes={existing_size}-"
                mode = "ab"

            r = requests.get(url, stream=True, timeout=120, headers=headers)

            if r.status_code == 416:
                log.info(f"  {dest.name} already complete")
                return True

            if r.status_code == 206:
                total = existing_size + int(r.headers.get("content-length", 0))
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


def load_checkpoint(output_dir: Path) -> dict:
    cp = output_dir / CHECKPOINT_FILE
    if cp.exists():
        with open(cp) as f:
            return json.load(f)
    return {"downloaded": [], "processed": [], "failed": []}


def save_checkpoint(output_dir: Path, checkpoint: dict):
    with open(output_dir / CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f, indent=2)


# ---------------------------------------------------------------------------
# Work unit index
# ---------------------------------------------------------------------------


class _FTPLinkParser(HTMLParser):
    """Parse FTP directory listing HTML to extract file links."""

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v and not v.startswith("?") and not v.startswith("/"):
                    self.links.append(v)


def discover_work_units(
    output_dir: Path,
    province_filter: Optional[list[str]] = None,
) -> dict[str, dict]:
    """
    Download the NHN work unit index and build a mapping of:
        {work_unit_id: {province, url, name}}

    Falls back to scraping the FTP directory listing if the index
    shapefile is unavailable.
    """
    index_dir = output_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)

    work_units = {}

    # Try downloading and parsing the work unit index shapefile
    index_zip = index_dir / "nhn_index_workunit.zip"
    if not index_zip.exists():
        log.info("Downloading NHN work unit index...")
        download_file(NHN_INDEX_URL, index_zip)

    if index_zip.exists():
        try:
            import geopandas as gpd

            gdf = gpd.read_file(f"zip://{index_zip}")
            log.info(f"NHN index: {len(gdf)} work units")

            for _, row in gdf.iterrows():
                wuid = str(row.get("DATASETNAM", row.get("datasetnam", "")))
                if not wuid:
                    continue

                # Province is typically in the work unit metadata
                prov = str(row.get("PROVINCE", row.get("province", "")))
                if not prov:
                    # Infer province from geometry centroid (rough)
                    prov = _infer_province(row.geometry.centroid.y, row.geometry.centroid.x)

                if province_filter and prov not in province_filter:
                    continue

                # NHN GPKG URL pattern
                url = f"{NHN_GPKG_BASE}{wuid}/{wuid}_en.zip"

                work_units[wuid] = {
                    "province": prov,
                    "url": url,
                    "name": str(row.get("NAME", row.get("name", wuid))),
                }

        except Exception as e:
            log.warning(f"Failed to parse NHN index shapefile: {e}")

    # Fallback: scrape FTP directory
    if not work_units:
        log.info("Falling back to FTP directory listing...")
        work_units = _scrape_ftp_workunits(province_filter)

    log.info(f"Found {len(work_units)} NHN work units to process")
    return work_units


def _infer_province(lat: float, lon: float) -> str:
    """Rough province inference from lat/lon. Returns 2-letter code."""
    # Very approximate bounding boxes
    if lon > -60 and lat < 47:
        return "NS" if lon > -62 else "NB"
    if lon > -65 and lat > 46 and lat < 49:
        return "QC" if lon < -63 else "NB"
    if lon > -80 and lon < -60 and lat > 45 and lat < 55:
        return "QC"
    if lon > -90 and lon < -74 and lat > 41 and lat < 57:
        return "ON"
    if lon > -102 and lon < -88 and lat > 49 and lat < 60:
        return "MB"
    if lon > -110 and lon < -102:
        return "SK"
    if lon > -120 and lon < -110:
        return "AB"
    if lon > -140 and lon < -114 and lat > 53:
        return "BC"
    if lon < -120 and lat < 60:
        return "BC"
    if lat > 60 and lon > -120:
        return "NT"
    if lat > 60 and lon < -120:
        return "YT"
    if lat > 60:
        return "NU"
    if lon > -65 and lat < 50:
        return "PE"
    if lon > -60 and lat > 46 and lat < 53:
        return "NL"
    return "XX"


def _scrape_ftp_workunits(
    province_filter: Optional[list[str]] = None,
) -> dict[str, dict]:
    """Scrape the NHN FTP directory for available GPKG work units."""
    work_units = {}

    try:
        r = requests.get(NHN_GPKG_BASE, timeout=60)
        r.raise_for_status()

        parser = _FTPLinkParser()
        parser.feed(r.text)

        for link in parser.links:
            wuid = link.strip("/")
            if not wuid or wuid.startswith("."):
                continue

            url = f"{NHN_GPKG_BASE}{wuid}/{wuid}_en.zip"
            work_units[wuid] = {
                "province": "XX",  # Unknown until processed
                "url": url,
                "name": wuid,
            }

    except Exception as e:
        log.error(f"FTP scraping failed: {e}")

    return work_units


# ---------------------------------------------------------------------------
# GPKG processing
# ---------------------------------------------------------------------------


def process_nhn_workunit(
    zip_path: Path,
    output_dir: Path,
    wuid: str,
    province: str,
) -> Optional[Path]:
    """
    Extract water features from an NHN work unit GPKG.
    Returns path to output parquet, or None on failure.
    """
    import fiona
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    extract_dir = output_dir / "tmp" / wuid
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Unzip
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # Find GPKG file
        gpkgs = list(extract_dir.rglob("*.gpkg"))
        if not gpkgs:
            # Sometimes it's a GDB
            gpkgs = list(extract_dir.rglob("*.gdb"))
        if not gpkgs:
            log.warning(f"  No GPKG/GDB found in {zip_path.name}")
            return None

        gpkg_path = gpkgs[0]
        available_layers = fiona.listlayers(str(gpkg_path))

        all_dfs = []

        for layer_name, info in NHN_LAYERS.items():
            # NHN layer names can vary; try fuzzy match
            matching = [l for l in available_layers if layer_name.lower() in l.lower()]
            if not matching:
                # Try partial match
                key = layer_name.split("_")[-2]  # e.g., "WATERBODY" or "NLFLOW"
                matching = [l for l in available_layers if key.lower() in l.lower()]

            if not matching:
                continue

            actual_layer = matching[0]
            try:
                gdf = gpd.read_file(str(gpkg_path), layer=actual_layer)
            except Exception as e:
                log.debug(f"  Failed to read {actual_layer}: {e}")
                continue

            if gdf.empty:
                continue

            # Ensure CRS is EPSG:4326
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)

            # Standardize columns
            col_lower = {c: c.lower() for c in gdf.columns}
            gdf = gdf.rename(columns=col_lower)

            # Map NHN columns to our schema
            mapped = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)

            # Permanent ID
            for cand in ["nid", "permanentid", "uuid", "fid"]:
                if cand in gdf.columns:
                    mapped["permanent_id"] = gdf[cand].astype(str)
                    break
            if "permanent_id" not in mapped.columns:
                mapped["permanent_id"] = [f"NHN_{wuid}_{i}" for i in range(len(gdf))]

            # Name
            for cand in ["nameid", "name_en", "name", "geoname", "toponymid"]:
                if cand in gdf.columns:
                    mapped["name"] = gdf[cand].fillna("")
                    break
            if "name" not in mapped.columns:
                mapped["name"] = ""

            # Feature type
            for cand in ["definition", "waterdefini", "waterDefinition"]:
                lc = cand.lower()
                if lc in gdf.columns:
                    mapped["ftype"] = gdf[lc]
                    mapped["ftype_desc"] = gdf[lc].map(NHN_WATER_DEFINITION).fillna("")
                    break
            if "ftype" not in mapped.columns:
                mapped["ftype"] = 0
                mapped["ftype_desc"] = info["description"]

            # Area (compute for polygons)
            if gdf.geometry.iloc[0].geom_type in ("Polygon", "MultiPolygon"):
                gdf_ea = gdf.to_crs(epsg=6933)
                mapped["area_sq_km"] = gdf_ea.geometry.area / 1e6
            else:
                mapped["area_sq_km"] = np.nan

            # Length for lines
            if gdf.geometry.iloc[0].geom_type in ("LineString", "MultiLineString"):
                gdf_ea = gdf.to_crs(epsg=6933)
                mapped["lengthkm"] = gdf_ea.geometry.length / 1000
            else:
                mapped["lengthkm"] = np.nan

            # Centroids
            centroids = mapped.geometry.centroid
            mapped["centroid_lat"] = centroids.y
            mapped["centroid_lon"] = centroids.x

            # Metadata
            mapped["province"] = province
            mapped["nhn_workunit"] = wuid
            mapped["source_fc"] = layer_name
            mapped["huc2"] = ""  # US only
            mapped["huc4"] = ""
            mapped["fcode"] = 0
            mapped["reachcode"] = ""

            all_dfs.append(mapped)

        if not all_dfs:
            return None

        result = pd.concat(all_dfs, ignore_index=True)
        result = gpd.GeoDataFrame(result, geometry="geometry", crs="EPSG:4326")

        # Save
        parquet_dir = output_dir / "parquet"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        out_path = parquet_dir / f"nhn_{wuid}.parquet"
        result.to_parquet(out_path, index=False)

        log.info(f"  {wuid}: {len(result)} features → {out_path.name}")
        return out_path

    except zipfile.BadZipFile:
        log.error(f"  Corrupt zip: {zip_path.name}")
        return None
    except Exception as e:
        log.error(f"  Processing failed for {wuid}: {e}")
        return None
    finally:
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def run_download(
    output_dir: Path,
    province_filter: Optional[list[str]] = None,
    resume: bool = True,
):
    """Download NHN GPKG zips for all work units."""
    zip_dir = output_dir / "zips"
    zip_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = load_checkpoint(output_dir) if resume else {"downloaded": [], "processed": [], "failed": []}
    work_units = discover_work_units(output_dir, province_filter)

    log.info(f"Downloading {len(work_units)} NHN work units...")

    for wuid, info in tqdm(sorted(work_units.items()), desc="NHN work units"):
        if wuid in checkpoint["downloaded"]:
            continue

        dest = zip_dir / f"{wuid}_en.zip"
        url = info["url"]

        success = download_file(url, dest)
        if success:
            checkpoint["downloaded"].append(wuid)
        else:
            checkpoint.setdefault("failed", []).append(wuid)

        save_checkpoint(output_dir, checkpoint)
        time.sleep(0.5)  # Be nice to NRCan servers

    log.info(
        f"Download complete: {len(checkpoint['downloaded'])} succeeded, "
        f"{len(checkpoint.get('failed', []))} failed"
    )


def run_process(
    output_dir: Path,
    province_filter: Optional[list[str]] = None,
    resume: bool = True,
):
    """Process downloaded NHN GPKGs into GeoParquet."""
    zip_dir = output_dir / "zips"
    checkpoint = load_checkpoint(output_dir) if resume else {"downloaded": [], "processed": [], "failed": []}

    work_units = discover_work_units(output_dir, province_filter)

    zips = sorted(zip_dir.glob("*.zip"))
    log.info(f"Processing {len(zips)} NHN work unit zips...")

    for zip_path in tqdm(zips, desc="Processing"):
        wuid = zip_path.stem.replace("_en", "")

        if wuid in checkpoint.get("processed", []):
            continue

        province = work_units.get(wuid, {}).get("province", "XX")

        result = process_nhn_workunit(zip_path, output_dir, wuid, province)
        if result:
            checkpoint.setdefault("processed", []).append(wuid)
        else:
            checkpoint.setdefault("process_failed", []).append(wuid)

        save_checkpoint(output_dir, checkpoint)

    log.info(f"Processing complete: {len(checkpoint.get('processed', []))} work units")


def run_merge(output_dir: Path):
    """Merge per-workunit parquets into per-province files and a master catalog."""
    import geopandas as gpd
    import pandas as pd

    parquet_dir = output_dir / "parquet"
    if not parquet_dir.exists():
        log.error(f"No parquet directory at {parquet_dir}")
        return

    files = sorted(parquet_dir.glob("nhn_*.parquet"))
    log.info(f"Merging {len(files)} NHN parquet files...")

    # Group by province
    by_province = {}
    for f in files:
        try:
            gdf = gpd.read_parquet(f)
            prov = gdf["province"].iloc[0] if "province" in gdf.columns else "XX"
            by_province.setdefault(prov, []).append(f)
        except Exception as e:
            log.warning(f"Cannot read {f.name}: {e}")

    merged_dir = output_dir / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for prov, prov_files in sorted(by_province.items()):
        log.info(f"  Merging {prov} ({PROVINCES.get(prov, '?')}) — {len(prov_files)} work units")
        dfs = [gpd.read_parquet(f) for f in prov_files]
        merged = pd.concat(dfs, ignore_index=True)

        # Deduplicate by permanent_id
        if "permanent_id" in merged.columns:
            before = len(merged)
            merged = merged.drop_duplicates(subset="permanent_id", keep="first")
            if len(merged) < before:
                log.info(f"    Deduped: {before} → {len(merged)}")

        gdf = gpd.GeoDataFrame(merged, geometry="geometry", crs="EPSG:4326")
        out = merged_dir / f"nhn_{prov}.parquet"
        gdf.to_parquet(out, index=False)
        total += len(gdf)
        log.info(f"    {len(gdf)} features → {out.name}")

    # Master catalog (no geometry)
    log.info("Building Canada master catalog...")
    cat_parts = []
    for f in sorted(merged_dir.glob("nhn_*.parquet")):
        cols = [
            "permanent_id", "name", "ftype", "ftype_desc", "area_sq_km",
            "lengthkm", "centroid_lat", "centroid_lon", "province", "nhn_workunit",
            "source_fc",
        ]
        try:
            gdf = gpd.read_parquet(f)
            available = [c for c in cols if c in gdf.columns]
            cat_parts.append(gdf[available])
        except Exception:
            pass

    if cat_parts:
        catalog = pd.concat(cat_parts, ignore_index=True)
        cat_path = output_dir / "canada_waterbody_catalog.parquet"
        catalog.to_parquet(cat_path, index=False)
        log.info(f"Canada catalog: {len(catalog)} features → {cat_path}")

    log.info(f"Total Canadian water features: {total}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch — Canadian NHN water body pipeline"
    )
    parser.add_argument(
        "--output", type=str, default="/data/nhn",
        help="Output directory",
    )
    parser.add_argument(
        "--mode", choices=["all", "download", "process-only", "merge"],
        default="all", help="Pipeline mode",
    )
    parser.add_argument(
        "--provinces", type=str, default=None,
        help="Comma-separated province codes (e.g. ON,QC,BC)",
    )
    parser.add_argument(
        "--resume", action="store_true", default=True,
        help="Resume from checkpoint (default: True)",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="Start fresh",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    prov_filter = [p.strip().upper() for p in args.provinces.split(",")] if args.provinces else None
    resume = not args.no_resume

    if args.mode in ("all", "download"):
        run_download(output_dir, prov_filter, resume)

    if args.mode in ("all", "process-only"):
        run_process(output_dir, prov_filter, resume)

    if args.mode in ("all", "merge"):
        run_merge(output_dir)


if __name__ == "__main__":
    main()
