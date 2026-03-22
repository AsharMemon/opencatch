#!/usr/bin/env python3
"""
OpenCatch — Sentinel-2 Multi-Temporal Composite Builder

For each training lake, builds a 14-band composite GeoTIFF:
- 10 Sentinel-2 L2A bands (cloud-free median composite)
- 4 DEM-derived channels (elevation, slope, aspect, curvature)

Key processing steps:
1. Query Planetary Computer for all S2 L2A scenes (June-September)
2. Filter to <20% cloud cover per scene
3. Apply per-pixel cloud masking using SCL band
4. Apply Hedley et al. (2005) sun glint correction using SWIR
5. Compute per-pixel median across dates (removes residual noise/glint)
6. Append 3DEP DEM terrain derivatives
7. Save as 14-band GeoTIFF at 10m resolution

Usage:
    python build_s2_composites.py \\
        --lakes /data/waterbodies/us/all_us_waterbodies.geojson \\
        --dem-dir /data/3dep \\
        --output /data/training/v2 \\
        --max-lakes 100

Requirements:
    pip install pystac-client planetary-computer odc-stac
    pip install rasterio geopandas numpy tqdm scipy
"""

import argparse
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("build_s2")

# Sentinel-2 L2A bands for V2 model (10 bands)
# Element84 Earth Search band names (different from Planetary Computer)
S2_BANDS_V2 = [
    "blue",      # B02 490nm — 10m
    "green",     # B03 560nm — 10m
    "red",       # B04 665nm — 10m
    "rededge1",  # B05 705nm — 20m
    "rededge2",  # B06 740nm — 20m
    "rededge3",  # B07 783nm — 20m
    "nir",       # B08 842nm — 10m
    "nir08",     # B8A 865nm — 20m
    "swir16",    # B11 1610nm — 20m
    "swir22",    # B12 2190nm — 20m
]

# Scene Classification Layer values (for cloud masking)
# 0=no_data, 1=saturated, 2=dark, 3=shadow, 4=vegetation,
# 5=bare_soil, 6=water, 7=cloud_low, 8=cloud_medium,
# 9=cloud_high, 10=cirrus, 11=snow
SCL_CLEAR = {4, 5, 6}       # Clear pixels (veg, soil, water)
SCL_CLOUD = {7, 8, 9, 10}   # Cloud pixels to mask

# Planetary Computer STAC
# Element84 Earth Search — free, no auth, S3 COGs, much more reliable
E84_STAC_URL = "https://earth-search.aws.element84.com/v1"
# Legacy Planetary Computer URL (kept for reference)
PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

# 3DEP DEM
DEM_3DEP_COLLECTION = "3dep-seamless"


def get_stac_client():
    """Initialize Element84 Earth Search STAC client (free, no auth)."""
    from pystac_client import Client

    return Client.open(E84_STAC_URL)


# ── Sun Glint Correction ────────────────────────────────────────────

def hedley_glint_correction(
    bands: np.ndarray,
    nir_idx: int = 6,  # B08
    swir_idx: int = 8,  # B11
    water_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Hedley et al. (2005) sun glint correction.

    Removes sun glint from water pixels using the linear relationship
    between NIR/SWIR and visible bands over deep water.

    The SWIR band is completely absorbed by water, so any SWIR signal
    over water is entirely due to sun glint (specular reflection).

    Args:
        bands: (C, H, W) reflectance array
        nir_idx: Index of NIR band (B08)
        swir_idx: Index of SWIR band (B11)
        water_mask: Optional (H, W) binary water mask

    Returns:
        Glint-corrected (C, H, W) array
    """
    C, H, W = bands.shape
    corrected = bands.copy()

    # Use NIR to identify water pixels if no mask provided
    if water_mask is None:
        water_mask = bands[nir_idx] < 0.15

    # Get SWIR values over water (our glint proxy)
    swir_water = bands[swir_idx][water_mask]
    if len(swir_water) < 100:
        return corrected  # Not enough water pixels

    # Minimum SWIR over deep water (baseline, no glint)
    min_swir = np.percentile(swir_water, 5)

    # For each visible/red-edge band, compute regression slope with SWIR
    for i in range(min(8, C)):  # B02-B8A (visible + red edge)
        if i == nir_idx or i == swir_idx:
            continue

        band_water = bands[i][water_mask]
        swir_vals = bands[swir_idx][water_mask]

        # Linear regression: band = slope * SWIR + intercept
        if len(band_water) < 100:
            continue

        # Use robust regression (remove outliers)
        valid = np.isfinite(band_water) & np.isfinite(swir_vals)
        if valid.sum() < 50:
            continue

        bw = band_water[valid]
        sw = swir_vals[valid]

        # Compute slope using least squares
        cov = np.cov(sw, bw)
        if cov[0, 0] > 1e-10:
            slope = cov[0, 1] / cov[0, 0]
        else:
            continue

        # Apply correction: corrected = original - slope * (SWIR - min_SWIR)
        glint_contribution = slope * (bands[swir_idx] - min_swir)
        corrected[i] = bands[i] - glint_contribution * water_mask.astype(np.float32)

        # Clamp to non-negative
        corrected[i] = np.clip(corrected[i], 0, None)

    return corrected


# ── DEM Processing ───────────────────────────────────────────────────

def compute_terrain_derivatives(
    dem: np.ndarray,
    pixel_size_m: float = 10.0,
) -> np.ndarray:
    """
    Compute terrain derivatives from DEM.

    Returns (4, H, W) array:
      [0] Elevation (normalized to local mean)
      [1] Slope (degrees, normalized)
      [2] Aspect (radians / pi, range [-1, 1])
      [3] Profile curvature (normalized)
    """
    H, W = dem.shape

    # Handle NaN
    dem_clean = np.nan_to_num(dem, nan=np.nanmean(dem) if np.any(np.isfinite(dem)) else 0)

    # Normalize elevation to local mean
    local_mean = dem_clean.mean()
    elevation = (dem_clean - local_mean) / max(abs(local_mean), 1)
    elevation = np.clip(elevation, -2, 2)

    # Slope and aspect from gradients
    dy, dx = np.gradient(dem_clean, pixel_size_m)
    slope_rad = np.arctan(np.sqrt(dx ** 2 + dy ** 2))
    slope_norm = np.degrees(slope_rad) / 45.0  # Normalize

    aspect = np.arctan2(-dx, dy)  # Radians [-pi, pi]
    aspect_norm = aspect / np.pi  # Range [-1, 1]

    # Profile curvature (second derivative along gradient direction)
    dyy, _ = np.gradient(dy, pixel_size_m)
    _, dxx = np.gradient(dx, pixel_size_m)
    curvature = -(dxx + dyy) / 2.0
    curv_std = max(np.nanstd(curvature), 1e-6)
    curvature_norm = np.clip(curvature / (3 * curv_std), -1, 1)

    return np.stack([
        elevation.astype(np.float32),
        slope_norm.astype(np.float32),
        aspect_norm.astype(np.float32),
        curvature_norm.astype(np.float32),
    ], axis=0)


def fetch_dem_for_bbox(
    client,
    bbox: tuple[float, float, float, float],
    resolution: int = 10,
) -> Optional[np.ndarray]:
    """
    Fetch 3DEP DEM from Planetary Computer for a bounding box.

    Args:
        client: STAC client
        bbox: (min_lon, min_lat, max_lon, max_lat)
        resolution: Target resolution in metres

    Returns:
        (H, W) elevation array or None
    """
    from odc.stac import load as stac_load

    try:
        search = client.search(
            collections=[DEM_3DEP_COLLECTION],
            bbox=bbox,
            max_items=5,
        )

        items = list(search.items())
        if not items:
            log.warning(f"No 3DEP data found for bbox {bbox}")
            return None

        data = stac_load(
            items,
            bands=["data"],
            bbox=bbox,
            resolution=resolution,
        )

        dem = data["data"].values[0].astype(np.float32)
        return dem

    except Exception as e:
        log.warning(f"DEM fetch failed: {e}")
        return None


# ── Composite Builder ────────────────────────────────────────────────

def build_composite_for_lake(
    client,
    lake_name: str,
    bbox: tuple[float, float, float, float],
    output_dir: Path,
    date_range: str = "2023-06-01/2023-09-30",
    max_cloud: float = 20.0,
    resolution: int = 10,
    min_scenes: int = 3,
) -> Optional[Path]:
    """
    Build a 14-band composite for a single lake.

    Args:
        client: STAC client
        lake_name: Lake identifier for output filename
        bbox: (min_lon, min_lat, max_lon, max_lat) with buffer
        output_dir: Output directory
        date_range: ISO date range for scene search
        max_cloud: Maximum scene-level cloud cover %
        resolution: Target resolution in metres
        min_scenes: Minimum scenes for a valid median composite

    Returns:
        Path to saved composite GeoTIFF, or None on failure
    """
    from odc.stac import load as stac_load
    import rasterio
    from rasterio.transform import from_bounds

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(lake_name))
    output_path = output_dir / safe_name / "composite.tif"

    if output_path.exists():
        log.info(f"  Already exists: {safe_name}")
        return output_path

    # 1. Query S2 scenes
    try:
        search = client.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": max_cloud}},
            sortby=["+properties.eo:cloud_cover"],
            max_items=30,
        )
        items = list(search.items())
    except Exception as e:
        log.warning(f"  S2 search failed for {lake_name}: {e}")
        return None

    if len(items) < min_scenes:
        log.warning(f"  {lake_name}: only {len(items)} scenes (need {min_scenes})")
        return None

    log.info(f"  {lake_name}: {len(items)} S2 scenes, building composite...")

    # 2. Load all scenes
    try:
        # Load 10 spectral bands + SCL for cloud masking
        all_bands = S2_BANDS_V2 + ["scl"]
        data = stac_load(
            items,
            bands=all_bands,
            bbox=bbox,
            resolution=resolution,
        )
    except Exception as e:
        log.warning(f"  Failed to load S2 data for {lake_name}: {e}")
        return None

    # 3. Per-pixel cloud masking + median composite
    n_times = data[S2_BANDS_V2[0]].shape[0]
    h = data[S2_BANDS_V2[0]].shape[1]
    w = data[S2_BANDS_V2[0]].shape[2]

    # Stack all bands: (T, C, H, W)
    stack = np.zeros((n_times, len(S2_BANDS_V2), h, w), dtype=np.float32)
    for i, band in enumerate(S2_BANDS_V2):
        band_data = data[band].values.astype(np.float32)
        # Normalize to reflectance [0, 1]
        if band_data.max() > 10:
            band_data = band_data / 10000.0
        stack[:, i] = band_data

    # Cloud mask from SCL
    scl = data["scl"].values  # (T, H, W)
    cloud_mask = np.zeros_like(scl, dtype=bool)
    for scl_val in SCL_CLOUD:
        cloud_mask |= (scl == scl_val)

    # Debug: log pre-masking stats
    pre_valid = np.isfinite(stack[:, 0]).mean()
    cloud_pct = cloud_mask.mean()
    log.info(f"  {lake_name}: pre-mask valid={pre_valid:.0%}, cloud={cloud_pct:.0%}")

    # Mask cloudy pixels
    stack[cloud_mask[:, np.newaxis].repeat(len(S2_BANDS_V2), axis=1)] = np.nan

    post_valid = np.isfinite(stack[:, 0]).mean()
    log.info(f"  {lake_name}: post-mask valid={post_valid:.0%}")

    # 4. Sun glint correction per scene
    for t in range(n_times):
        scene = stack[t]
        if np.isfinite(scene).sum() > 0:
            stack[t] = hedley_glint_correction(scene)

    # 5. Median composite across time (ignoring NaN)
    with np.errstate(all="ignore"):
        composite = np.nanmedian(stack, axis=0)  # (C, H, W)

    # Check for sufficient valid pixels (in the center region, not edges)
    ch, cw = composite.shape[1] // 4, composite.shape[2] // 4
    center = composite[0, ch:-ch, cw:-cw] if ch > 0 and cw > 0 else composite[0]
    valid_frac = np.isfinite(center).mean()
    if valid_frac < 0.1:  # Lowered from 0.3 — even 10% is usable
        log.warning(f"  {lake_name}: only {valid_frac:.0%} valid pixels after masking")
        return None
    log.info(f"  {lake_name}: {valid_frac:.0%} valid pixels (center region)")

    # Fill remaining NaN with 0
    composite = np.nan_to_num(composite, nan=0.0)

    # 6. Fetch and append DEM terrain derivatives
    dem = fetch_dem_for_bbox(client, bbox, resolution=resolution)
    if dem is not None:
        # Ensure DEM matches composite spatial dims
        if dem.shape != (h, w):
            from scipy.ndimage import zoom
            zoom_h = h / dem.shape[0]
            zoom_w = w / dem.shape[1]
            dem = zoom(dem, (zoom_h, zoom_w), order=1)

        terrain = compute_terrain_derivatives(dem, pixel_size_m=resolution)
    else:
        # Placeholder terrain (zeros) if DEM unavailable
        log.warning(f"  {lake_name}: no DEM available, using zero terrain channels")
        terrain = np.zeros((4, h, w), dtype=np.float32)

    # 7. Stack: 10 S2 bands + 4 DEM = 14 channels
    full_composite = np.concatenate([composite, terrain], axis=0)
    assert full_composite.shape[0] == 14, f"Expected 14 channels, got {full_composite.shape[0]}"

    # 8. Save as GeoTIFF
    output_path.parent.mkdir(parents=True, exist_ok=True)
    transform = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], w, h)

    band_names = S2_BANDS_V2 + ["elevation", "slope", "aspect", "curvature"]

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=14,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        compress="deflate",
    ) as dst:
        for i in range(14):
            dst.write(full_composite[i], i + 1)
            dst.set_band_description(i + 1, band_names[i])

    log.info(f"  Saved: {output_path} ({w}x{h}, 14 bands, {valid_frac:.0%} valid)")
    return output_path


# ── Batch Processing ─────────────────────────────────────────────────

def build_composites_batch(
    lakes_path: Path,
    output_dir: Path,
    dem_dir: Optional[Path] = None,
    date_range: str = "2023-06-01/2023-09-30",
    max_cloud: float = 20.0,
    max_lakes: Optional[int] = None,
    buffer_deg: float = 0.005,
    resolution: int = 10,
) -> int:
    """
    Build composites for all lakes in a GeoJSON file.

    Args:
        lakes_path: GeoJSON with lake polygons
        output_dir: Output directory
        dem_dir: Optional directory with pre-downloaded DEM tiles
        date_range: Date range for S2 imagery
        max_cloud: Maximum cloud cover %
        max_lakes: Limit number of lakes
        buffer_deg: Buffer around lake in degrees (~500m)
        resolution: Target resolution in metres

    Returns:
        Number of successfully processed lakes
    """
    import geopandas as gpd
    from tqdm import tqdm

    log.info(f"Loading lakes from {lakes_path}...")
    gdf = gpd.read_file(lakes_path)
    # Ensure WGS84 for STAC bbox queries
    if gdf.crs and not gdf.crs.is_geographic:
        log.info(f"Reprojecting from {gdf.crs} to EPSG:4326")
        gdf = gdf.to_crs("EPSG:4326")

    # Filter to named lakes
    name_col = None
    for col in ["GNIS_Name", "name", "NAME", "lake_name"]:
        if col in gdf.columns:
            name_col = col
            break

    if name_col:
        gdf = gdf[gdf[name_col].notna() & (gdf[name_col] != "")]

    # Filter by area
    if "AreaSqKm" in gdf.columns:
        gdf = gdf[gdf["AreaSqKm"] >= 0.1]  # At least 10 hectares

    if max_lakes:
        gdf = gdf.head(max_lakes)

    log.info(f"Processing {len(gdf)} lakes...")

    # Initialize STAC client
    client = get_stac_client()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    failed = 0

    for idx, row in tqdm(gdf.iterrows(), total=len(gdf), desc="Building composites"):
        lake_name = row.get(name_col, f"lake_{idx}") if name_col else f"lake_{idx}"

        bounds = row.geometry.bounds  # (minx, miny, maxx, maxy)
        # If bounds look like UTM (values > 180), transform to WGS84
        if abs(bounds[0]) > 180 or abs(bounds[1]) > 90:
            from rasterio.warp import transform_bounds as _tb
            try:
                bounds = _tb("EPSG:32615", "EPSG:4326", *bounds)  # UTM 15N (MN)
            except Exception:
                try:
                    bounds = _tb("EPSG:32614", "EPSG:4326", *bounds)  # UTM 14N
                except Exception:
                    log.warning(f"  Skipping {lake_name}: cannot transform CRS")
                    failed += 1
                    continue
        bbox = (
            bounds[0] - buffer_deg,
            bounds[1] - buffer_deg,
            bounds[2] + buffer_deg,
            bounds[3] + buffer_deg,
        )

        try:
            result = build_composite_for_lake(
                client=client,
                lake_name=lake_name,
                bbox=bbox,
                output_dir=output_dir,
                date_range=date_range,
                max_cloud=max_cloud,
                resolution=resolution,
            )
            if result:
                success += 1
            else:
                failed += 1
        except Exception as e:
            log.error(f"Failed for {lake_name}: {e}")
            failed += 1

        # Rate limiting for Planetary Computer
        time.sleep(0.5)

    log.info(f"\nDone: {success} successful, {failed} failed out of {len(gdf)} lakes")
    return success


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build Sentinel-2 + DEM composites for bathymetry training",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--lakes", type=str, required=True,
                        help="GeoJSON file of lake polygons")
    parser.add_argument("--output", type=str, default="/data/training/v2",
                        help="Output directory for composites")
    parser.add_argument("--dem-dir", type=str, default=None,
                        help="Directory with pre-downloaded 3DEP DEM tiles")
    parser.add_argument("--date-range", type=str, default="2023-06-01/2023-09-30",
                        help="Date range for S2 imagery (ISO format)")
    parser.add_argument("--max-cloud", type=float, default=20.0,
                        help="Maximum scene-level cloud cover %%")
    parser.add_argument("--max-lakes", type=int, default=None,
                        help="Limit number of lakes to process")
    parser.add_argument("--buffer", type=float, default=0.005,
                        help="Buffer around lake polygon in degrees (~500m)")
    parser.add_argument("--resolution", type=int, default=10,
                        help="Target resolution in metres")

    args = parser.parse_args()

    build_composites_batch(
        lakes_path=Path(args.lakes),
        output_dir=Path(args.output),
        dem_dir=Path(args.dem_dir) if args.dem_dir else None,
        date_range=args.date_range,
        max_cloud=args.max_cloud,
        max_lakes=args.max_lakes,
        buffer_deg=args.buffer,
        resolution=args.resolution,
    )


if __name__ == "__main__":
    main()
