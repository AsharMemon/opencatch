#!/usr/bin/env python3
"""
OpenCatch — Multi-State Bathymetry Data Downloader

Downloads and standardizes lake bathymetry data from multiple state agencies
to build a comprehensive training dataset for the V2 model.

Sources:
- Minnesota DNR: ~4,500 lakes (already have via fetch_globathy.py)
- Wisconsin DNR: Lake bathymetry via ArcGIS Open Data
- Michigan EGLE: Lake contours via GIS Open Data
- Massachusetts MassWildlife: Inland water bathymetry
- New York DEC: Lake bathymetry surveys

All data is standardized to:
- GeoTIFF depth raster at 10m resolution
- CRS: EPSG:4326 (WGS84)
- Positive depth values (0 at surface, increasing with depth)
- NaN for areas outside the lake or without data

Usage:
    # Download all states
    python fetch_state_bathymetry.py --output /data/bathymetry/training --source all

    # Single state
    python fetch_state_bathymetry.py --output /data/bathymetry/training --source wisconsin

    # Build unified catalog
    python fetch_state_bathymetry.py --catalog --data-dir /data/bathymetry/training

Requirements:
    pip install requests geopandas rasterio shapely numpy tqdm pyproj
"""

import argparse
import json
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Optional

import numpy as np
import requests
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_state_bathy")


# ── Download Utilities ───────────────────────────────────────────────

def download_file(
    url: str,
    dest: Path,
    desc: Optional[str] = None,
    timeout: int = 60,
    max_retries: int = 3,
) -> bool:
    """Download file with progress, resume support, and retries."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        try:
            existing_size = dest.stat().st_size if dest.exists() else 0
            headers = {}
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"

            resp = requests.get(url, headers=headers, stream=True, timeout=timeout)

            if resp.status_code == 416:
                log.info(f"Already fully downloaded: {dest.name}")
                return True

            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            mode = "ab" if (existing_size > 0 and resp.status_code == 206) else "wb"
            if mode == "wb":
                existing_size = 0

            with open(dest, mode) as f, tqdm(
                total=(total + existing_size) if total else None,
                initial=existing_size,
                unit="B",
                unit_scale=True,
                desc=desc or dest.name,
            ) as pbar:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    pbar.update(len(chunk))

            return True

        except Exception as e:
            log.warning(f"Download attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    log.error(f"Failed to download {url} after {max_retries} attempts")
    return False


def extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Extract a zip file."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest_dir)
        log.info(f"Extracted {zip_path.name} -> {dest_dir}")
        return dest_dir
    except Exception as e:
        log.error(f"Extraction failed for {zip_path}: {e}")
        return dest_dir


def query_arcgis_feature_service(
    base_url: str,
    where: str = "1=1",
    out_fields: str = "*",
    out_format: str = "geojson",
    max_records: int = 5000,
) -> Optional[dict]:
    """Query an ArcGIS REST Feature Service and return GeoJSON."""
    params = {
        "where": where,
        "outFields": out_fields,
        "f": out_format,
        "returnGeometry": "true",
        "resultRecordCount": max_records,
    }

    try:
        resp = requests.get(f"{base_url}/query", params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"ArcGIS query failed: {e}")
        return None


# ── Rasterization ────────────────────────────────────────────────────

def contours_to_depth_raster(
    contours_gdf,
    output_path: Path,
    resolution_m: float = 10.0,
    depth_column: str = "DEPTH",
    lake_polygon=None,
) -> bool:
    """
    Convert depth contour lines/polygons to a gridded depth raster.

    Uses linear interpolation between contour lines to create a
    continuous depth surface.

    Args:
        contours_gdf: GeoDataFrame with contour geometries and depth values
        output_path: Output GeoTIFF path
        resolution_m: Target resolution in metres
        depth_column: Column name containing depth values
        lake_polygon: Optional lake boundary polygon

    Returns:
        True if successful
    """
    import rasterio
    from rasterio.transform import from_bounds
    from scipy.interpolate import griddata

    if len(contours_gdf) == 0:
        return False

    # Determine bounds
    bounds = contours_gdf.total_bounds  # (minx, miny, maxx, maxy)

    # Convert resolution from metres to degrees (approximate)
    res_deg = resolution_m / 111000.0

    # Create grid
    x_min, y_min, x_max, y_max = bounds
    cols = max(1, int((x_max - x_min) / res_deg))
    rows = max(1, int((y_max - y_min) / res_deg))

    if cols > 5000 or rows > 5000:
        # Too large, increase resolution
        scale = max(cols, rows) / 3000
        res_deg *= scale
        cols = max(1, int((x_max - x_min) / res_deg))
        rows = max(1, int((y_max - y_min) / res_deg))

    xi = np.linspace(x_min, x_max, cols)
    yi = np.linspace(y_max, y_min, rows)  # Note: top to bottom
    xx, yy = np.meshgrid(xi, yi)

    # Extract points from contour geometries
    points = []
    values = []

    for _, row in contours_gdf.iterrows():
        geom = row.geometry
        depth = row[depth_column]

        if not np.isfinite(depth) or depth < 0:
            continue

        # Extract coordinates from geometry
        try:
            if geom.geom_type == "LineString":
                coords = list(geom.coords)
            elif geom.geom_type == "MultiLineString":
                coords = []
                for line in geom.geoms:
                    coords.extend(list(line.coords))
            elif geom.geom_type in ("Polygon", "MultiPolygon"):
                coords = list(geom.exterior.coords) if geom.geom_type == "Polygon" else []
                if geom.geom_type == "MultiPolygon":
                    for poly in geom.geoms:
                        coords.extend(list(poly.exterior.coords))
            elif geom.geom_type == "Point":
                coords = [(geom.x, geom.y)]
            else:
                continue

            for x, y in coords:
                points.append((x, y))
                values.append(depth)
        except Exception:
            continue

    if len(points) < 3:
        log.warning(f"Not enough points ({len(points)}) for interpolation")
        return False

    points = np.array(points)
    values = np.array(values)

    # Add zero-depth points at lake shore if polygon available
    if lake_polygon is not None:
        try:
            shore_coords = list(lake_polygon.exterior.coords)
            for x, y in shore_coords[::5]:  # Sample every 5th point
                points = np.vstack([points, [x, y]])
                values = np.append(values, 0.0)
        except Exception:
            pass

    # Interpolate
    try:
        grid_points = np.column_stack([xx.ravel(), yy.ravel()])
        depth_grid = griddata(points, values, grid_points, method="linear")
        depth_grid = depth_grid.reshape(rows, cols)
    except Exception as e:
        log.error(f"Interpolation failed: {e}")
        return False

    # Fill NaN with nearest neighbor
    mask_nan = np.isnan(depth_grid)
    if mask_nan.any() and not mask_nan.all():
        depth_nearest = griddata(points, values, grid_points, method="nearest")
        depth_nearest = depth_nearest.reshape(rows, cols)
        depth_grid[mask_nan] = depth_nearest[mask_nan]

    # Clamp to non-negative
    depth_grid = np.clip(depth_grid, 0, None)

    # Apply lake mask if available
    if lake_polygon is not None:
        try:
            from rasterio.features import geometry_mask
            transform = from_bounds(x_min, y_min, x_max, y_max, cols, rows)
            mask = geometry_mask(
                [lake_polygon], out_shape=(rows, cols),
                transform=transform, invert=True,
            )
            depth_grid[~mask] = np.nan
        except Exception:
            pass

    # Save as GeoTIFF
    transform = from_bounds(x_min, y_min, x_max, y_max, cols, rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        compress="deflate",
        nodata=np.nan,
    ) as dst:
        dst.write(depth_grid.astype(np.float32), 1)

    log.info(f"Saved depth raster: {output_path} ({cols}x{rows})")
    return True


# ── Minnesota DNR ────────────────────────────────────────────────────

def fetch_minnesota(output_dir: Path) -> int:
    """
    Download Minnesota DNR lake bathymetry data.
    This wraps the existing fetch_globathy.py MN DNR downloader.
    """
    mn_dir = output_dir / "minnesota"
    mn_dir.mkdir(parents=True, exist_ok=True)

    # MN DNR bathymetric contours
    contours_url = (
        "https://resources.gisdata.mn.gov/pub/gdrs/data/pub/us_mn_state_dnr/"
        "water_lake_bathymetric_contours/shp_water_lake_bathymetric_contours.zip"
    )

    zip_path = mn_dir / "bathymetric_contours.zip"
    if not zip_path.exists():
        log.info("Downloading Minnesota DNR bathymetric contours...")
        if not download_file(contours_url, zip_path, desc="MN DNR Contours"):
            return 0
    else:
        log.info("MN DNR contours already downloaded")

    # Extract
    extract_dir = mn_dir / "contours"
    if not extract_dir.exists():
        extract_zip(zip_path, extract_dir)

    # Count shapefile features
    n_lakes = _count_mn_lakes(extract_dir)
    log.info(f"Minnesota: ~{n_lakes} lakes with bathymetry")
    return n_lakes


def _count_mn_lakes(extract_dir: Path) -> int:
    """Count unique lakes in MN DNR contour shapefiles."""
    try:
        import geopandas as gpd
        shp_files = list(extract_dir.rglob("*.shp"))
        if not shp_files:
            return 0
        gdf = gpd.read_file(shp_files[0])
        if "DOW_NUM" in gdf.columns:
            return gdf["DOW_NUM"].nunique()
        return len(gdf) // 10  # Rough estimate
    except Exception:
        return 4500  # Known approximate count


# ── Wisconsin DNR ────────────────────────────────────────────────────

def fetch_wisconsin(output_dir: Path) -> int:
    """
    Download Wisconsin DNR lake bathymetry data.

    Source: WI DNR ArcGIS Open Data Portal
    - Lake bathymetric contours
    - Lake depth data
    """
    wi_dir = output_dir / "wisconsin"
    wi_dir.mkdir(parents=True, exist_ok=True)

    # WI DNR has bathymetry data on their Open Data portal
    # ArcGIS Feature Service for lake bathymetry contours
    bathy_service_url = (
        "https://dnrmaps.wi.gov/arcgis/rest/services/"
        "DW_Map_Dynamic_Inland/MapServer/7"
    )

    # Try ArcGIS Feature Service query
    log.info("Querying Wisconsin DNR bathymetry service...")

    geojson = query_arcgis_feature_service(
        bathy_service_url,
        where="1=1",
        out_fields="*",
        max_records=10000,
    )

    if geojson and "features" in geojson and len(geojson["features"]) > 0:
        geojson_path = wi_dir / "wi_bathymetry_contours.geojson"
        with open(geojson_path, "w") as f:
            json.dump(geojson, f)
        n_features = len(geojson["features"])
        log.info(f"Wisconsin: {n_features} bathymetry contour features")
        return n_features

    # Fallback: WI DNR Open Data GeoJSON download
    log.info("Trying WI DNR Open Data Portal direct download...")

    wi_open_data_urls = [
        # Lake bathymetry from WI Open Data
        "https://data-wi-dnr.opendata.arcgis.com/api/download/v1/items/"
        "fbc57ce7f5e74ae1a57b10ae56e45fc8/geojson?layers=0",
        # Alternate lake contours endpoint
        "https://data-wi-dnr.opendata.arcgis.com/api/download/v1/items/"
        "bathy_contours/geojson",
    ]

    for url in wi_open_data_urls:
        dest = wi_dir / "wi_bathymetry.geojson"
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(dest, "wb") as f:
                    f.write(resp.content)
                log.info(f"Downloaded WI bathymetry from Open Data Portal")
                return _count_geojson_features(dest)
        except Exception as e:
            log.warning(f"WI Open Data download failed: {e}")
            continue

    # Fallback: download lake list and construct download URLs
    log.info("Querying WI DNR for available lake surveys...")
    lake_list_url = (
        "https://dnrmaps.wi.gov/arcgis/rest/services/"
        "DW_Map_Dynamic_Inland/MapServer/0/query"
    )
    params = {
        "where": "BATHY_FLAG = 'Y'",
        "outFields": "WATERBODY_WBIC,WATERBODY_NAME,COUNTY_NAME",
        "f": "json",
        "returnGeometry": "false",
        "resultRecordCount": 5000,
    }
    try:
        resp = requests.get(lake_list_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])
        log.info(f"Wisconsin: {len(features)} lakes with bathymetry surveys")

        # Save lake list
        lake_list_path = wi_dir / "wi_lakes_with_bathy.json"
        with open(lake_list_path, "w") as f:
            json.dump(features, f, indent=2)

        return len(features)
    except Exception as e:
        log.warning(f"WI lake list query failed: {e}")
        return 0


# ── Michigan EGLE ────────────────────────────────────────────────────

def fetch_michigan(output_dir: Path) -> int:
    """
    Download Michigan EGLE lake bathymetry data.

    Source: Michigan Open Data Portal (GIS Michigan)
    - Inland lake bathymetric contours
    """
    mi_dir = output_dir / "michigan"
    mi_dir.mkdir(parents=True, exist_ok=True)

    # Michigan GIS Open Data
    mi_urls = [
        # MI DEQ/EGLE lake bathymetry
        (
            "https://gis-michigan.opendata.arcgis.com/api/download/v1/items/"
            "lake_bathymetry/geojson"
        ),
        # MI GIS services
        (
            "https://services1.arcgis.com/7w1SUsLXj6kBqU84/arcgis/rest/services/"
            "Michigan_Inland_Lake_Bathymetry/FeatureServer/0"
        ),
    ]

    # Try feature service first
    log.info("Querying Michigan EGLE bathymetry...")

    for url in mi_urls:
        if "/FeatureServer/" in url:
            geojson = query_arcgis_feature_service(
                url, where="1=1", max_records=10000,
            )
            if geojson and "features" in geojson:
                dest = mi_dir / "mi_bathymetry.geojson"
                with open(dest, "w") as f:
                    json.dump(geojson, f)
                n = len(geojson["features"])
                log.info(f"Michigan: {n} bathymetry features")
                return n
        else:
            try:
                dest = mi_dir / "mi_bathymetry.geojson"
                if download_file(url, dest, desc="MI Bathymetry"):
                    n = _count_geojson_features(dest)
                    if n > 0:
                        log.info(f"Michigan: {n} features")
                        return n
            except Exception:
                continue

    # Michigan DEQ has individual lake PDF maps; try to find GIS data
    log.info("Michigan: Trying alternative data sources...")

    # MI SOM GIS service
    mi_som_url = (
        "https://services1.arcgis.com/7w1SUsLXj6kBqU84/arcgis/rest/services/"
        "Inland_Lakes/FeatureServer/0"
    )
    geojson = query_arcgis_feature_service(
        mi_som_url, where="1=1", out_fields="LAKE_NAME,MAX_DEPTH,SURFACE_AREA",
        max_records=10000,
    )
    if geojson and "features" in geojson:
        dest = mi_dir / "mi_inland_lakes.geojson"
        with open(dest, "w") as f:
            json.dump(geojson, f)
        n = len(geojson["features"])
        log.info(f"Michigan: {n} inland lake records (may include depth data)")
        return n

    log.warning("Michigan bathymetry data not found via API")
    return 0


# ── Massachusetts ────────────────────────────────────────────────────

def fetch_massachusetts(output_dir: Path) -> int:
    """
    Download Massachusetts MassWildlife inland water bathymetry.

    Source: MassGIS Data Portal
    https://www.mass.gov/info-details/massgis-data-masswildlife-inland-water-bathymetry
    """
    ma_dir = output_dir / "massachusetts"
    ma_dir.mkdir(parents=True, exist_ok=True)

    # MassGIS bathymetry download
    ma_urls = [
        # MassGIS Data Portal — bathymetry shapefiles
        "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/"
        "shapefiles/state/bathymetry.zip",
        # Alternative MassGIS endpoint
        "https://gis.massdot.state.ma.us/arcgis/rest/services/"
        "Inland_Water_Bathymetry/MapServer/0",
    ]

    for url in ma_urls:
        if url.endswith(".zip"):
            dest = ma_dir / "bathymetry.zip"
            if download_file(url, dest, desc="MA Bathymetry"):
                extract_dir = extract_zip(dest, ma_dir / "extracted")
                # Count shapefiles
                import glob
                shp_files = glob.glob(str(extract_dir) + "/**/*.shp", recursive=True)
                if shp_files:
                    n = len(shp_files)
                    log.info(f"Massachusetts: {n} bathymetry shapefiles")
                    return n
        elif "/FeatureServer/" in url or "/MapServer/" in url:
            geojson = query_arcgis_feature_service(
                url, where="1=1", max_records=5000,
            )
            if geojson and "features" in geojson:
                dest = ma_dir / "ma_bathymetry.geojson"
                with open(dest, "w") as f:
                    json.dump(geojson, f)
                n = len(geojson["features"])
                log.info(f"Massachusetts: {n} features")
                return n

    log.warning("Massachusetts bathymetry data download failed")
    return 0


# ── New York DEC ─────────────────────────────────────────────────────

def fetch_new_york(output_dir: Path) -> int:
    """
    Download New York DEC lake bathymetry data.

    Source: NY DEC / NY GIS Clearinghouse
    """
    ny_dir = output_dir / "new_york"
    ny_dir.mkdir(parents=True, exist_ok=True)

    # NY Open Data / DEC
    ny_urls = [
        # NY DEC lake bathymetry feature service
        (
            "https://services6.arcgis.com/DZHaqZm9cxOD4CWM/arcgis/rest/services/"
            "Lake_Bathymetry/FeatureServer/0"
        ),
        # NY GIS Clearinghouse
        (
            "https://gis.ny.gov/gisdata/inventories/details.cfm?"
            "DSID=1133&DSID=1133"
        ),
    ]

    for url in ny_urls:
        if "/FeatureServer/" in url:
            geojson = query_arcgis_feature_service(
                url, where="1=1", max_records=10000,
            )
            if geojson and "features" in geojson:
                dest = ny_dir / "ny_bathymetry.geojson"
                with open(dest, "w") as f:
                    json.dump(geojson, f)
                n = len(geojson["features"])
                log.info(f"New York: {n} bathymetry features")
                return n

    log.warning("New York bathymetry data not found via API")
    return 0


# ── Unified Catalog ──────────────────────────────────────────────────

def build_catalog(data_dir: Path) -> Path:
    """
    Build a unified training catalog from all state bathymetry data.

    Creates a CSV with:
    - lake_id, lake_name, state
    - data_path (GeoTIFF depth raster)
    - source (DNR/EGLE/etc)
    - max_depth_m, area_km2
    - data_quality (dense contours vs sparse points)
    """
    import pandas as pd

    data_dir = Path(data_dir)
    catalog_records = []

    # Scan for depth rasters
    for tif_path in sorted(data_dir.rglob("*_depth.tif")):
        state = tif_path.parent.name
        lake_name = tif_path.stem.replace("_depth", "")

        try:
            import rasterio
            with rasterio.open(tif_path) as src:
                depth = src.read(1)
                valid = depth[np.isfinite(depth) & (depth > 0)]
                max_depth = float(valid.max()) if len(valid) > 0 else 0
                mean_depth = float(valid.mean()) if len(valid) > 0 else 0
                n_pixels = int((valid > 0).sum())
        except Exception:
            max_depth = 0
            mean_depth = 0
            n_pixels = 0

        catalog_records.append({
            "lake_name": lake_name,
            "state": state,
            "data_path": str(tif_path),
            "max_depth_m": round(max_depth, 2),
            "mean_depth_m": round(mean_depth, 2),
            "n_valid_pixels": n_pixels,
            "source": _infer_source(state),
        })

    # Also scan for contour shapefiles/GeoJSON
    for shp_path in sorted(data_dir.rglob("*.shp")):
        state = _infer_state_from_path(shp_path)
        catalog_records.append({
            "lake_name": shp_path.stem,
            "state": state,
            "data_path": str(shp_path),
            "data_type": "contour_shapefile",
            "source": _infer_source(state),
        })

    for geojson_path in sorted(data_dir.rglob("*bathymetry*.geojson")):
        state = _infer_state_from_path(geojson_path)
        catalog_records.append({
            "lake_name": geojson_path.stem,
            "state": state,
            "data_path": str(geojson_path),
            "data_type": "contour_geojson",
            "source": _infer_source(state),
        })

    catalog_path = data_dir / "training_catalog.csv"
    df = pd.DataFrame(catalog_records)
    df.to_csv(catalog_path, index=False)

    log.info(f"Built training catalog: {len(df)} entries -> {catalog_path}")
    log.info(f"States: {df['state'].value_counts().to_dict()}")

    return catalog_path


def _infer_source(state: str) -> str:
    """Map state name to data source."""
    return {
        "minnesota": "MN DNR",
        "wisconsin": "WI DNR",
        "michigan": "MI EGLE",
        "massachusetts": "MA MassWildlife",
        "new_york": "NY DEC",
    }.get(state.lower(), "Unknown")


def _infer_state_from_path(path: Path) -> str:
    """Infer state from file path."""
    path_str = str(path).lower()
    for state in ["minnesota", "wisconsin", "michigan", "massachusetts", "new_york"]:
        if state in path_str:
            return state
    return "unknown"


def _count_geojson_features(path: Path) -> int:
    """Count features in a GeoJSON file."""
    try:
        with open(path) as f:
            data = json.load(f)
        return len(data.get("features", []))
    except Exception:
        return 0


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download multi-state lake bathymetry data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Download all states
    python fetch_state_bathymetry.py --output /data/bathymetry/training --source all

    # Single state
    python fetch_state_bathymetry.py --output /data/bathymetry/training --source wisconsin

    # Build unified catalog from downloaded data
    python fetch_state_bathymetry.py --catalog --data-dir /data/bathymetry/training
        """,
    )

    parser.add_argument("--output", type=str, default="/data/bathymetry/training",
                        help="Output directory for bathymetry data")
    parser.add_argument("--source", type=str, default="all",
                        choices=["all", "minnesota", "wisconsin", "michigan",
                                 "massachusetts", "new_york"],
                        help="Which state to download")
    parser.add_argument("--catalog", action="store_true",
                        help="Build unified training catalog from existing data")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Data directory for --catalog mode")

    args = parser.parse_args()

    if args.catalog:
        data_dir = Path(args.data_dir or args.output)
        build_catalog(data_dir)
        return

    output_dir = Path(args.output)
    totals = {}

    fetchers = {
        "minnesota": fetch_minnesota,
        "wisconsin": fetch_wisconsin,
        "michigan": fetch_michigan,
        "massachusetts": fetch_massachusetts,
        "new_york": fetch_new_york,
    }

    if args.source == "all":
        states = list(fetchers.keys())
    else:
        states = [args.source]

    for state in states:
        log.info("=" * 60)
        log.info(f"Fetching {state.title()} bathymetry data...")
        log.info("=" * 60)
        n = fetchers[state](output_dir)
        totals[state] = n

    # Summary
    log.info("\n" + "=" * 60)
    log.info("Download Summary:")
    for state, count in totals.items():
        log.info(f"  {state.title()}: {count} features/lakes")
    log.info(f"  Total: {sum(totals.values())}")
    log.info("=" * 60)

    # Build catalog
    log.info("\nBuilding unified catalog...")
    build_catalog(output_dir)


if __name__ == "__main__":
    main()
