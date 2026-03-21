#!/usr/bin/env python3
"""
OpenCatch — NHDPlus Water Body Data Pipeline

Downloads the National Hydrography Dataset Plus (NHDPlus) water body features
for the entire US. This provides:
- Every lake, reservoir, pond, and river in the US
- Water body names, areas, and geometries
- HUC (Hydrologic Unit Code) watershed boundaries

Usage:
    python fetch_nhdplus.py --output /data/nhdplus

Requirements:
    pip install requests geopandas shapely tqdm
"""

import os
import sys
import argparse
import logging
from pathlib import Path

import requests
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# NHDPlus HR (High Resolution) download URLs by HUC2 region
# Source: https://www.usgs.gov/national-hydrography/nhdplus-high-resolution
NHDPLUS_BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHDPlusHR/Beta/GDB/"

HUC2_REGIONS = {
    '01': 'New England',
    '02': 'Mid-Atlantic',
    '03': 'South Atlantic-Gulf',
    '04': 'Great Lakes',
    '05': 'Ohio',
    '06': 'Tennessee',
    '07': 'Upper Mississippi',
    '08': 'Lower Mississippi',
    '09': 'Souris-Red-Rainy',
    '10': 'Missouri',
    '11': 'Arkansas-White-Red',
    '12': 'Texas-Gulf',
    '13': 'Rio Grande',
    '14': 'Upper Colorado',
    '15': 'Lower Colorado',
    '16': 'Great Basin',
    '17': 'Pacific Northwest',
    '18': 'California',
    '19': 'Alaska',
    '20': 'Hawaii',
    '21': 'Caribbean',
}

# USGS Water Resources WFS for querying water bodies
USGS_WFS = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer"


def download_file(url: str, dest: Path, chunk_size: int = 8192) -> bool:
    """Download a file with progress bar."""
    try:
        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()
        total = int(r.headers.get('content-length', 0))

        with open(dest, 'wb') as f, tqdm(
            total=total, unit='B', unit_scale=True, desc=dest.name
        ) as bar:
            for chunk in r.iter_content(chunk_size=chunk_size):
                f.write(chunk)
                bar.update(len(chunk))
        return True
    except Exception as e:
        log.error(f"Failed to download {url}: {e}")
        return False


def fetch_waterbodies_by_bbox(
    bbox: tuple[float, float, float, float],
    output_dir: Path,
    min_area_sq_km: float = 0.01,
):
    """
    Fetch water bodies from USGS NHD WFS within a bounding box.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat)
        output_dir: Directory to save GeoJSON output
        min_area_sq_km: Minimum water body area to include
    """
    import geopandas as gpd

    # Query the NHD MapServer
    url = f"{USGS_WFS}/6/query"  # Layer 6 = NHDWaterbody
    params = {
        'where': f'AreaSqKm >= {min_area_sq_km}',
        'geometry': f'{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}',
        'geometryType': 'esriGeometryEnvelope',
        'inSR': '4326',
        'outSR': '4326',
        'outFields': 'GNIS_Name,AreaSqKm,FType,FCode,ReachCode',
        'f': 'geojson',
        'returnGeometry': 'true',
        'resultRecordCount': 10000,
    }

    log.info(f"Fetching water bodies in bbox {bbox}...")
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    data = r.json()

    features = data.get('features', [])
    log.info(f"Found {len(features)} water bodies")

    if features:
        gdf = gpd.GeoDataFrame.from_features(features, crs='EPSG:4326')
        outfile = output_dir / f"waterbodies_{bbox[0]:.1f}_{bbox[1]:.1f}.geojson"
        gdf.to_file(outfile, driver='GeoJSON')
        log.info(f"Saved to {outfile}")
        return gdf

    return None


def fetch_all_us_waterbodies(output_dir: Path, min_area_sq_km: float = 0.01):
    """
    Fetch ALL US water bodies by tiling the country into grid cells.
    This avoids hitting the 10,000 feature limit per request.
    """
    import geopandas as gpd

    output_dir.mkdir(parents=True, exist_ok=True)

    # Tile the US into 2° × 2° grid cells
    all_gdfs = []
    lon_range = range(-125, -66, 2)  # West to East
    lat_range = range(24, 50, 2)     # South to North

    total = len(list(lon_range)) * len(list(lat_range))

    for lon in tqdm(range(-125, -66, 2), desc="Longitude tiles"):
        for lat in range(24, 50, 2):
            bbox = (lon, lat, lon + 2, lat + 2)
            try:
                gdf = fetch_waterbodies_by_bbox(bbox, output_dir, min_area_sq_km)
                if gdf is not None:
                    all_gdfs.append(gdf)
            except Exception as e:
                log.warning(f"Failed for bbox {bbox}: {e}")

    # Merge all into one file
    if all_gdfs:
        merged = gpd.pd.concat(all_gdfs, ignore_index=True)
        # Deduplicate by ReachCode
        merged = merged.drop_duplicates(subset='ReachCode', keep='first')
        outfile = output_dir / 'all_us_waterbodies.geojson'
        merged.to_file(outfile, driver='GeoJSON')
        log.info(f"Total: {len(merged)} unique water bodies saved to {outfile}")


def fetch_canada_nhn(output_dir: Path):
    """
    Download Canadian National Hydro Network water body data.
    Source: https://open.canada.ca/data/en/dataset/a4b190fe-e090-4e6d-881e-b87956c07977
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # NHN GeoPackage download — organized by watershed work units
    # This is a large dataset, so we download the index first
    nhn_index_url = "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/index/nhn_index_workunit.zip"

    log.info("Downloading NHN work unit index...")
    dest = output_dir / "nhn_index.zip"
    download_file(nhn_index_url, dest)
    log.info(f"NHN index downloaded to {dest}")
    log.info("Use the index to identify work units covering your area of interest,")
    log.info("then download individual work unit GeoPackages from:")
    log.info("https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/gpkg/")


def main():
    parser = argparse.ArgumentParser(description='Fetch NHDPlus water body data')
    parser.add_argument('--output', type=str, default='/data/waterbodies',
                       help='Output directory')
    parser.add_argument('--min-area', type=float, default=0.01,
                       help='Minimum water body area in sq km')
    parser.add_argument('--country', choices=['us', 'canada', 'both'], default='us',
                       help='Which country to fetch')
    parser.add_argument('--bbox', type=str, default=None,
                       help='Custom bbox: min_lon,min_lat,max_lon,max_lat')
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.bbox:
        bbox = tuple(float(x) for x in args.bbox.split(','))
        fetch_waterbodies_by_bbox(bbox, output_dir, args.min_area)
    elif args.country in ('us', 'both'):
        fetch_all_us_waterbodies(output_dir / 'us', args.min_area)

    if args.country in ('canada', 'both'):
        fetch_canada_nhn(output_dir / 'canada')


if __name__ == '__main__':
    main()
