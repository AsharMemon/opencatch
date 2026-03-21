#!/usr/bin/env python3
"""
OpenCatch — Sentinel-2 Imagery Fetcher for Bathymetry Training

Downloads cloud-free Sentinel-2 imagery for lakes with known bathymetry.
Uses Microsoft Planetary Computer (free, no API key required).

For each lake:
1. Query STAC catalog for least-cloudy scene in summer months
2. Download bands B02 (blue), B03 (green), B04 (red), B08 (NIR), B11 (SWIR)
3. Clip to lake polygon + buffer
4. Save as multi-band GeoTIFF aligned with bathymetry DEM

Usage:
    python fetch_sentinel2.py \
        --lakes /data/waterbodies/us/all_us_waterbodies.geojson \
        --output /data/sentinel2 \
        --max-cloud 10 \
        --date-range 2023-06-01/2023-09-30

Requirements:
    pip install pystac-client planetary-computer odc-stac rasterio geopandas shapely numpy tqdm
"""

import argparse
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# Sentinel-2 bands for bathymetry
S2_BANDS = ['B02', 'B03', 'B04', 'B08', 'B11']
S2_BAND_RESOLUTION = {'B02': 10, 'B03': 10, 'B04': 10, 'B08': 10, 'B11': 20}

# Planetary Computer STAC endpoint (free, no key needed)
PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


def get_stac_client():
    """Get a signed STAC client for Planetary Computer."""
    import planetary_computer as pc
    from pystac_client import Client

    return Client.open(PC_STAC_URL, modifier=pc.sign_inplace)


def find_best_scene(
    client,
    bbox: tuple[float, float, float, float],
    date_range: str = "2023-06-01/2023-09-30",
    max_cloud: float = 10.0,
):
    """
    Find the least-cloudy Sentinel-2 scene for a bounding box.

    Args:
        client: STAC client
        bbox: (min_lon, min_lat, max_lon, max_lat)
        date_range: ISO date range string
        max_cloud: Maximum cloud cover percentage

    Returns:
        Best STAC item or None
    """
    search = client.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=date_range,
        query={"eo:cloud_cover": {"lt": max_cloud}},
        sortby=["+properties.eo:cloud_cover"],
        max_items=5,
    )

    items = list(search.items())
    if not items:
        log.warning(f"No scenes found for bbox {bbox} with cloud < {max_cloud}%")
        return None

    best = items[0]
    cloud = best.properties.get('eo:cloud_cover', '?')
    log.info(f"Best scene: {best.id} (cloud: {cloud}%)")
    return best


def download_bands(
    item,
    bbox: tuple[float, float, float, float],
    output_path: Path,
    resolution: int = 10,
):
    """
    Download S2 bands for a bounding box and save as multi-band GeoTIFF.

    Args:
        item: STAC item with signed URLs
        bbox: Clip region
        output_path: Output .tif path
        resolution: Target resolution in metres
    """
    import rasterio
    from rasterio.transform import from_bounds
    from odc.stac import load as stac_load

    try:
        # Load bands using odc-stac (handles CRS, resolution, clipping)
        data = stac_load(
            [item],
            bands=S2_BANDS,
            bbox=bbox,
            resolution=resolution,
            crs="EPSG:4326",
        )

        # Stack bands into (C, H, W) array
        bands = []
        for band_name in S2_BANDS:
            arr = data[band_name].values[0]  # First (only) time step
            # Normalize to 0-1 reflectance
            arr = arr.astype(np.float32) / 10000.0
            bands.append(arr)

        stacked = np.stack(bands, axis=0)  # (5, H, W)
        h, w = stacked.shape[1], stacked.shape[2]

        # Build transform
        transform = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], w, h)

        # Write multi-band GeoTIFF
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            output_path,
            'w',
            driver='GTiff',
            height=h,
            width=w,
            count=len(S2_BANDS),
            dtype='float32',
            crs='EPSG:4326',
            transform=transform,
            compress='deflate',
        ) as dst:
            for i, band_name in enumerate(S2_BANDS):
                dst.write(stacked[i], i + 1)
                dst.set_band_description(i + 1, band_name)

        log.info(f"Saved {output_path} ({w}x{h}, {len(S2_BANDS)} bands)")
        return True

    except Exception as e:
        log.error(f"Failed to download bands: {e}")
        return False


def fetch_for_lakes(
    lakes_path: Path,
    output_dir: Path,
    date_range: str = "2023-06-01/2023-09-30",
    max_cloud: float = 10.0,
    buffer_deg: float = 0.005,
    max_lakes: Optional[int] = None,
):
    """
    Download Sentinel-2 imagery for all lakes in a GeoJSON file.

    Args:
        lakes_path: Path to waterbodies GeoJSON
        output_dir: Output directory for S2 tiles
        date_range: Date range for imagery search
        max_cloud: Maximum cloud cover %
        buffer_deg: Buffer around lake polygon in degrees (~500m)
        max_lakes: Limit number of lakes (for testing)
    """
    import geopandas as gpd
    from tqdm import tqdm

    output_dir.mkdir(parents=True, exist_ok=True)

    gdf = gpd.read_file(lakes_path)
    log.info(f"Loaded {len(gdf)} water bodies from {lakes_path}")

    # Filter to named lakes with reasonable size
    if 'GNIS_Name' in gdf.columns:
        named = gdf[gdf['GNIS_Name'].notna() & (gdf['GNIS_Name'] != '')]
        log.info(f"{len(named)} named water bodies")
    else:
        named = gdf

    if 'AreaSqKm' in gdf.columns:
        named = named[named['AreaSqKm'] >= 0.1]  # At least 10 hectares
        log.info(f"{len(named)} lakes >= 0.1 sq km")

    if max_lakes:
        named = named.head(max_lakes)

    client = get_stac_client()
    success_count = 0
    skip_count = 0

    for idx, row in tqdm(named.iterrows(), total=len(named), desc="Fetching S2"):
        lake_name = row.get('GNIS_Name', f'lake_{idx}')
        safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in str(lake_name))

        output_path = output_dir / f"{safe_name}_s2.tif"
        if output_path.exists():
            skip_count += 1
            continue

        # Get bounding box of lake geometry + buffer
        geom = row.geometry
        bounds = geom.bounds  # (minx, miny, maxx, maxy)
        bbox = (
            bounds[0] - buffer_deg,
            bounds[1] - buffer_deg,
            bounds[2] + buffer_deg,
            bounds[3] + buffer_deg,
        )

        # Find best scene
        item = find_best_scene(client, bbox, date_range, max_cloud)
        if item is None:
            continue

        # Download bands
        if download_bands(item, bbox, output_path):
            success_count += 1

    log.info(f"\nDone: {success_count} downloaded, {skip_count} skipped (already exist)")


def main():
    parser = argparse.ArgumentParser(description='Fetch Sentinel-2 imagery for bathymetry training')
    parser.add_argument('--lakes', type=str, required=True,
                       help='GeoJSON file of lake polygons')
    parser.add_argument('--output', type=str, default='/data/sentinel2',
                       help='Output directory for S2 tiles')
    parser.add_argument('--date-range', type=str, default='2023-06-01/2023-09-30',
                       help='Date range for imagery (ISO format)')
    parser.add_argument('--max-cloud', type=float, default=10.0,
                       help='Maximum cloud cover percentage')
    parser.add_argument('--max-lakes', type=int, default=None,
                       help='Limit number of lakes (for testing)')
    parser.add_argument('--buffer', type=float, default=0.005,
                       help='Buffer around lake in degrees')
    args = parser.parse_args()

    fetch_for_lakes(
        Path(args.lakes),
        Path(args.output),
        date_range=args.date_range,
        max_cloud=args.max_cloud,
        buffer_deg=args.buffer,
        max_lakes=args.max_lakes,
    )


if __name__ == '__main__':
    main()
