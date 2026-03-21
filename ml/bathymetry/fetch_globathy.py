#!/usr/bin/env python3
"""
OpenCatch — GLOBathy + 3D-LAKES Bathymetry Downloader

Downloads pre-computed synthetic bathymetry data for immediate lake coverage:

1. GLOBathy (Khazaei et al., 2022): Estimated bathymetry for 1.4M lakes globally
   - Source: Google Earth Engine / Zenodo
   - Resolution: 30m raster per lake
   - Method: Area-depth curve fitting from HydroLAKES morphometry

2. 3D-LAKES (2025): ICESat-2 derived bathymetry for 510K lakes
   - Source: Zenodo (doi:10.5281/zenodo.13107867)
   - Method: ICESat-2 ATLAS photon-counting lidar + Landsat water occurrence
   - Quality: RMSE 1.37m validated against in-situ measurements

3. MN DNR Lake Bathymetry: Full surveyed DEMs for ~4,500 Minnesota lakes
   - Best training data for U-Net model
   - 5-ft contour intervals, downloadable as shapefiles/GeoTIFF

Usage:
    # Download GLOBathy for North America
    python fetch_globathy.py --source globathy --region north_america --output /data/bathymetry

    # Download 3D-LAKES data
    python fetch_globathy.py --source 3d-lakes --output /data/bathymetry

    # Download MN DNR training data
    python fetch_globathy.py --source mn-dnr --output /data/bathymetry/training

Requirements:
    pip install requests geopandas shapely tqdm
    Optional: pip install earthengine-api (for GLOBathy via GEE)

Estimated sizes:
    - GLOBathy North America: ~5-10 GB
    - 3D-LAKES global: ~2-3 GB
    - MN DNR (4,500 lakes): ~500 MB
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

# 3D-LAKES on Zenodo
LAKES_3D_ZENODO_DOI = '10.5281/zenodo.13107867'
LAKES_3D_API = 'https://zenodo.org/api/records/13107867'

# GLOBathy on Zenodo (alternative to GEE)
GLOBATHY_ZENODO_DOI = '10.5281/zenodo.4586448'
GLOBATHY_API = 'https://zenodo.org/api/records/4586448'

# MN DNR Lake Bathymetry
MN_DNR_BATHY_URL = 'https://resources.gisdata.mn.gov/pub/gdrs/data/pub/us_mn_state_dnr/water_lake_bathymetry/shp_water_lake_bathymetry.zip'
MN_DNR_CONTOURS_URL = 'https://resources.gisdata.mn.gov/pub/gdrs/data/pub/us_mn_state_dnr/water_lake_bathymetric_contours/shp_water_lake_bathymetric_contours.zip'

# HydroLAKES (lake polygons with morphometric attributes)
HYDROLAKES_URL = 'https://data.hydrosheds.org/file/HydroLAKES/HydroLAKES_polys_v10_shp.zip'

# North America bounding box for filtering
NA_BOUNDS = {
    'min_lat': 24.0,
    'max_lat': 72.0,
    'min_lon': -170.0,
    'max_lon': -52.0,
}


def download_file(url: str, dest: Path, chunk_size: int = 8192,
                  desc: Optional[str] = None, timeout: int = 30) -> bool:
    """Download a file with progress bar and resume support."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Check for partial download
    existing_size = dest.stat().st_size if dest.exists() else 0
    headers = {}
    if existing_size > 0:
        headers['Range'] = f'bytes={existing_size}-'
        log.info(f"Resuming download from {existing_size} bytes")

    try:
        resp = requests.get(url, headers=headers, stream=True, timeout=timeout)

        if resp.status_code == 416:
            log.info(f"File already fully downloaded: {dest}")
            return True

        resp.raise_for_status()

        total = int(resp.headers.get('content-length', 0))
        if existing_size > 0 and resp.status_code == 206:
            total += existing_size
            mode = 'ab'
        else:
            mode = 'wb'
            existing_size = 0

        with open(dest, mode) as f, tqdm(
            total=total,
            initial=existing_size,
            unit='B',
            unit_scale=True,
            desc=desc or dest.name,
        ) as pbar:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                f.write(chunk)
                pbar.update(len(chunk))

        return True

    except Exception as e:
        log.error(f"Download failed: {e}")
        return False


# ── 3D-LAKES Download ────────────────────────────────────────────────

def fetch_3d_lakes(output_dir: Path) -> list[Path]:
    """
    Download 3D-LAKES dataset from Zenodo.

    Contains ICESat-2 derived bathymetry for 510K lakes:
    - Area-elevation (A-E) curves per lake
    - 3D bathymetry rasters
    - Lake morphometric attributes

    Returns list of downloaded file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    log.info("Fetching 3D-LAKES file listing from Zenodo...")
    try:
        resp = requests.get(LAKES_3D_API, timeout=30)
        resp.raise_for_status()
        record = resp.json()
    except Exception as e:
        log.error(f"Failed to fetch Zenodo metadata: {e}")
        # Fallback: try known file URLs
        log.info("Trying direct download URLs...")
        return _fetch_3d_lakes_fallback(output_dir)

    files = record.get('files', [])
    log.info(f"Found {len(files)} files in 3D-LAKES dataset")

    for file_info in files:
        filename = file_info.get('key', '')
        url = file_info.get('links', {}).get('self', '')
        size = file_info.get('size', 0)

        if not url:
            continue

        # Filter for North America relevant files
        # 3D-LAKES is organized by continent/region
        dest = output_dir / '3d_lakes' / filename
        if dest.exists() and dest.stat().st_size == size:
            log.info(f"Already downloaded: {filename}")
            downloaded.append(dest)
            continue

        log.info(f"Downloading {filename} ({size / 1e6:.1f} MB)...")
        if download_file(url, dest, desc=filename):
            downloaded.append(dest)
        else:
            log.warning(f"Failed to download {filename}")

        time.sleep(1)  # Rate limiting

    return downloaded


def _fetch_3d_lakes_fallback(output_dir: Path) -> list[Path]:
    """Fallback download for 3D-LAKES using known URLs."""
    urls = [
        f'https://zenodo.org/records/13107867/files/3D_LAKES_v1.zip',
    ]
    downloaded = []
    for url in urls:
        filename = url.split('/')[-1]
        dest = output_dir / '3d_lakes' / filename
        if download_file(url, dest, desc=filename):
            downloaded.append(dest)
    return downloaded


# ── GLOBathy Download ────────────────────────────────────────────────

def fetch_globathy_zenodo(output_dir: Path) -> list[Path]:
    """
    Download GLOBathy from Zenodo.

    GLOBathy provides estimated bathymetry for 1.4M lakes using
    area-depth curve fitting from HydroLAKES morphometric data.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    log.info("Fetching GLOBathy file listing from Zenodo...")
    try:
        resp = requests.get(GLOBATHY_API, timeout=30)
        resp.raise_for_status()
        record = resp.json()
    except Exception as e:
        log.error(f"Failed to fetch Zenodo metadata: {e}")
        return downloaded

    files = record.get('files', [])
    log.info(f"Found {len(files)} files in GLOBathy dataset")

    for file_info in files:
        filename = file_info.get('key', '')
        url = file_info.get('links', {}).get('self', '')
        size = file_info.get('size', 0)

        if not url:
            continue

        dest = output_dir / 'globathy' / filename
        if dest.exists() and dest.stat().st_size == size:
            log.info(f"Already downloaded: {filename}")
            downloaded.append(dest)
            continue

        log.info(f"Downloading {filename} ({size / 1e6:.1f} MB)...")
        if download_file(url, dest, desc=filename):
            downloaded.append(dest)

        time.sleep(1)

    return downloaded


def fetch_globathy_gee(output_dir: Path, region: str = 'north_america') -> Path:
    """
    Download GLOBathy from Google Earth Engine.

    This is the preferred method as it allows filtering by region
    and downloading only North American lakes.

    Requires: pip install earthengine-api
    Must authenticate first: earthengine authenticate

    GEE Asset: projects/sat-io/open-datasets/GLOBathy/GLOBathy_bathymetry
    """
    try:
        import ee
    except ImportError:
        log.error("earthengine-api not installed. Use: pip install earthengine-api")
        log.info("Falling back to Zenodo download")
        return fetch_globathy_zenodo(output_dir)

    log.info("Initializing Earth Engine...")
    try:
        ee.Initialize()
    except Exception:
        log.error("Earth Engine not authenticated. Run: earthengine authenticate")
        return fetch_globathy_zenodo(output_dir)

    # GLOBathy is an ImageCollection in GEE
    globathy = ee.ImageCollection('projects/sat-io/open-datasets/GLOBathy/GLOBathy_bathymetry')

    # Filter to North America
    na_bbox = ee.Geometry.BBox(
        NA_BOUNDS['min_lon'], NA_BOUNDS['min_lat'],
        NA_BOUNDS['max_lon'], NA_BOUNDS['max_lat']
    )

    filtered = globathy.filterBounds(na_bbox)
    count = filtered.size().getInfo()
    log.info(f"Found {count} GLOBathy images in North America")

    # Export as a single mosaic
    mosaic = filtered.mosaic().clip(na_bbox)

    # This would need a GEE export task — for now, log instructions
    log.info("""
    To export GLOBathy for North America from GEE:

    1. Go to https://code.earthengine.google.com
    2. Run this script:

    var globathy = ee.ImageCollection('projects/sat-io/open-datasets/GLOBathy/GLOBathy_bathymetry');
    var na = ee.Geometry.BBox(-170, 24, -52, 72);
    var mosaic = globathy.filterBounds(na).mosaic().clip(na);

    Export.image.toDrive({
      image: mosaic,
      description: 'GLOBathy_NorthAmerica',
      scale: 30,
      region: na,
      maxPixels: 1e13,
      fileFormat: 'GeoTIFF'
    });

    3. Download the exported file from Google Drive
    """)

    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / 'globathy'


# ── MN DNR Bathymetry (Training Data) ────────────────────────────────

def fetch_mn_dnr(output_dir: Path) -> list[Path]:
    """
    Download Minnesota DNR lake bathymetry data.

    This is the BEST training data available:
    - ~4,500 lakes with full-coverage surveyed DEMs
    - 5-ft contour intervals
    - Downloadable as shapefiles
    - Perfect for training the U-Net model

    Two datasets:
    1. Lake Bathymetry (point-based depth soundings)
    2. Bathymetric Contours (contour line shapefiles)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    # Download contour lines (more useful for training)
    contour_dest = output_dir / 'mn_dnr' / 'bathymetric_contours.zip'
    if not contour_dest.exists():
        log.info("Downloading MN DNR bathymetric contours (~200 MB)...")
        if download_file(MN_DNR_CONTOURS_URL, contour_dest, desc='MN DNR Contours'):
            downloaded.append(contour_dest)
            # Extract
            import zipfile
            extract_dir = contour_dest.parent / 'contours'
            extract_dir.mkdir(exist_ok=True)
            try:
                with zipfile.ZipFile(contour_dest) as zf:
                    zf.extractall(extract_dir)
                log.info(f"Extracted to {extract_dir}")
            except Exception as e:
                log.warning(f"Extraction failed: {e}")
    else:
        log.info("MN DNR contours already downloaded")
        downloaded.append(contour_dest)

    # Download bathymetry points
    bathy_dest = output_dir / 'mn_dnr' / 'lake_bathymetry.zip'
    if not bathy_dest.exists():
        log.info("Downloading MN DNR lake bathymetry (~300 MB)...")
        if download_file(MN_DNR_BATHY_URL, bathy_dest, desc='MN DNR Bathymetry'):
            downloaded.append(bathy_dest)
            import zipfile
            extract_dir = bathy_dest.parent / 'bathymetry'
            extract_dir.mkdir(exist_ok=True)
            try:
                with zipfile.ZipFile(bathy_dest) as zf:
                    zf.extractall(extract_dir)
                log.info(f"Extracted to {extract_dir}")
            except Exception as e:
                log.warning(f"Extraction failed: {e}")
    else:
        log.info("MN DNR bathymetry already downloaded")
        downloaded.append(bathy_dest)

    return downloaded


# ── HydroLAKES (Lake Polygons) ───────────────────────────────────────

def fetch_hydrolakes(output_dir: Path) -> Optional[Path]:
    """
    Download HydroLAKES lake polygon dataset.

    Contains 1.4M lake polygons globally with morphometric attributes:
    - Lake area, perimeter, volume
    - Shoreline development index
    - Elevation, latitude/longitude
    - Residence time
    - Pour point coordinates

    Used as the base geometry for all bathymetry work.
    Size: ~1.5 GB compressed
    """
    dest = output_dir / 'hydrolakes' / 'HydroLAKES_polys_v10.zip'
    if dest.exists():
        log.info("HydroLAKES already downloaded")
        return dest

    log.info("Downloading HydroLAKES polygons (~1.5 GB)...")
    if download_file(HYDROLAKES_URL, dest, desc='HydroLAKES'):
        # Extract
        import zipfile
        extract_dir = dest.parent / 'extracted'
        extract_dir.mkdir(exist_ok=True)
        try:
            with zipfile.ZipFile(dest) as zf:
                zf.extractall(extract_dir)
            log.info(f"Extracted to {extract_dir}")
        except Exception as e:
            log.warning(f"Extraction failed: {e}")
        return dest

    return None


# ── Contour Style Generator ─────────────────────────────────────────

# Papercut blue color palette (light → dark, 8 depth bands)
PAPERCUT_BLUES = [
    '#E8F4FD',  # 0-2 ft: lightest
    '#B8DCF0',  # 2-5 ft
    '#7BB8DE',  # 5-10 ft
    '#4A98C9',  # 10-15 ft
    '#2574A9',  # 15-25 ft
    '#1A5276',  # 25-40 ft
    '#0E3D5C',  # 40-60 ft
    '#071E2E',  # 60+ ft: darkest navy
]

# Depth band boundaries in feet
DEPTH_BANDS_FT = [0, 2, 5, 10, 15, 25, 40, 60, 200]
# Same in metres
DEPTH_BANDS_M = [0, 0.6, 1.5, 3.0, 4.6, 7.6, 12.2, 18.3, 61.0]


def generate_filled_contours(
    depth_raster,
    transform,
    crs,
    lake_id: str,
    lake_name: str = 'Unknown',
    smooth_tolerance: float = 0.0002,
) -> dict:
    """
    Generate FILLED contour polygons (not lines) for the papercut blue style.

    Each polygon represents a depth band. When rendered from shallowest to
    deepest, they create the layered papercut look.

    Returns GeoJSON FeatureCollection with filled polygons.
    """
    from rasterio.features import shapes
    import shapely.geometry as sg
    from shapely.ops import unary_union

    features = []
    max_depth = float(depth_raster[depth_raster > 0].max()) if (depth_raster > 0).any() else 0

    if max_depth <= 0:
        return {'type': 'FeatureCollection', 'features': []}

    # Use depth bands that make sense for this lake's depth
    bands_m = [d for d in DEPTH_BANDS_M if d < max_depth * 1.1]
    if len(bands_m) < 2:
        bands_m = [0, max_depth * 0.33, max_depth * 0.66, max_depth]

    for i in range(len(bands_m) - 1):
        min_depth = bands_m[i]
        max_band = bands_m[i + 1]
        color_idx = min(i, len(PAPERCUT_BLUES) - 1)

        # Create mask for this depth band
        mask = (depth_raster >= min_depth).astype('uint8')

        if mask.sum() == 0:
            continue

        try:
            polygons = []
            for geom, val in shapes(mask, transform=transform):
                if val == 1:
                    poly = sg.shape(geom)
                    if poly.is_valid and poly.area > 1e-10:
                        if smooth_tolerance > 0:
                            poly = poly.simplify(smooth_tolerance, preserve_topology=True)
                        polygons.append(poly)

            if not polygons:
                continue

            merged = unary_union(polygons)

            # Handle both single and multi polygons
            geoms = merged.geoms if merged.geom_type == 'MultiPolygon' else [merged]

            for geom in geoms:
                if geom.area < 1e-10:
                    continue

                features.append({
                    'type': 'Feature',
                    'geometry': sg.mapping(geom),
                    'properties': {
                        'depth_min_m': round(min_depth, 1),
                        'depth_max_m': round(max_band, 1),
                        'depth_min_ft': round(min_depth * 3.28084, 1),
                        'depth_max_ft': round(max_band * 3.28084, 1),
                        'band_index': i,
                        'color': PAPERCUT_BLUES[color_idx],
                        'lake_id': lake_id,
                        'lake_name': lake_name,
                        'source': 'opencatch',
                    },
                })

        except Exception as e:
            log.warning(f"Filled contour generation failed for band {min_depth}-{max_band}m: {e}")

    return {
        'type': 'FeatureCollection',
        'features': features,
    }


# ── MapLibre Style for Papercut Blue Contours ────────────────────────

def generate_maplibre_style_layers() -> list[dict]:
    """
    Generate MapLibre GL style layers for the papercut blue contour look.

    These layers should be added to the MapLibre style JSON under the
    'layers' array, after the water fill layer and before labels.

    The layers render filled polygons from the 'bathymetry_filled' source,
    with each depth band getting progressively darker blue.
    """
    layers = []

    for i, color in enumerate(PAPERCUT_BLUES):
        layers.append({
            'id': f'bathy-fill-{i}',
            'type': 'fill',
            'source': 'bathymetry_filled',
            'source-layer': 'contours',
            'filter': ['==', ['get', 'band_index'], i],
            'paint': {
                'fill-color': color,
                'fill-opacity': 0.85,
                'fill-outline-color': _darken(color, 0.15),
            },
            'minzoom': 10,
            'maxzoom': 18,
        })

    # Add contour line labels at higher zoom
    layers.append({
        'id': 'bathy-labels',
        'type': 'symbol',
        'source': 'bathymetry_filled',
        'source-layer': 'contours',
        'minzoom': 13,
        'layout': {
            'text-field': ['concat', ['to-string', ['get', 'depth_min_ft']], ' ft'],
            'text-size': 10,
            'text-font': ['Open Sans Regular'],
            'symbol-placement': 'point',
        },
        'paint': {
            'text-color': '#FFFFFF',
            'text-halo-color': 'rgba(0, 30, 60, 0.7)',
            'text-halo-width': 1,
        },
    })

    return layers


def _darken(hex_color: str, amount: float = 0.2) -> str:
    """Darken a hex color by a fraction."""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = max(0, int(r * (1 - amount)))
    g = max(0, int(g * (1 - amount)))
    b = max(0, int(b * (1 - amount)))
    return f'#{r:02x}{g:02x}{b:02x}'


# ── Main CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Download bathymetry datasets for OpenCatch',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Download everything (recommended for Vast.ai)
    python fetch_globathy.py --source all --output /data/bathymetry

    # Just MN DNR training data
    python fetch_globathy.py --source mn-dnr --output /data/bathymetry/training

    # Generate MapLibre style JSON
    python fetch_globathy.py --style-only
        """,
    )
    parser.add_argument('--source', choices=['globathy', '3d-lakes', 'mn-dnr', 'hydrolakes', 'all'],
                       default='all', help='Which dataset to download')
    parser.add_argument('--output', type=str, default='/data/bathymetry',
                       help='Output directory')
    parser.add_argument('--style-only', action='store_true',
                       help='Just print MapLibre style layers JSON')
    args = parser.parse_args()

    if args.style_only:
        layers = generate_maplibre_style_layers()
        print(json.dumps(layers, indent=2))
        return

    output_dir = Path(args.output)

    if args.source in ('mn-dnr', 'all'):
        log.info("=" * 60)
        log.info("Downloading MN DNR Lake Bathymetry (training data)")
        log.info("=" * 60)
        fetch_mn_dnr(output_dir)

    if args.source in ('hydrolakes', 'all'):
        log.info("=" * 60)
        log.info("Downloading HydroLAKES (lake polygons)")
        log.info("=" * 60)
        fetch_hydrolakes(output_dir)

    if args.source in ('3d-lakes', 'all'):
        log.info("=" * 60)
        log.info("Downloading 3D-LAKES (ICESat-2 bathymetry)")
        log.info("=" * 60)
        fetch_3d_lakes(output_dir)

    if args.source in ('globathy', 'all'):
        log.info("=" * 60)
        log.info("Downloading GLOBathy (synthetic bathymetry)")
        log.info("=" * 60)
        fetch_globathy_zenodo(output_dir)

    log.info("\nAll downloads complete!")
    log.info(f"Data stored in: {output_dir}")
    log.info("\nNext steps:")
    log.info("  1. Run generate_contours.py to create filled contour polygons")
    log.info("  2. Run tippecanoe to generate PMTiles")
    log.info("  3. Upload PMTiles to B2/S3 for serving")


if __name__ == '__main__':
    main()
