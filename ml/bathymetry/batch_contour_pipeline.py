#!/usr/bin/env python3
"""
OpenCatch — Batch Contour Generation Pipeline (Confidence-Adaptive)

Processes ALL water bodies in a region and generates confidence-adaptive
filled contour polygons for the OpenCatch app.  Integrates with the
ProductionDepthRouter to choose the best depth source for each lake,
river_depth for streams, and ocean_bathymetry for coastal/offshore areas.

Output: GeoJSON FeatureCollections with the PAPERCUT_BLUES filled-polygon
style (darker = deeper).  Contour interval density scales with data quality
— survey-grade lakes get fine 0.5 m contours while coarse estimates only
get a "deep basin" polygon.

Pipeline:
    1. Discover water bodies in bbox (lake catalog + NHDPlus)
    2. For each lake  → generate_unified_contours()
    3. For each river → generate_river_depth_zones()
    4. For ocean bbox  → generate_ocean_contours()
    5. Merge per-type GeoJSON + optional PostGIS upload + PMTiles

Usage:
    python batch_contour_pipeline.py \\
        --region us-midwest \\
        --bbox 43,-97,49,-89 \\
        --output /data/contours/ \\
        --lake-catalog /data/mn_dnr_bathy/lake_stats.parquet \\
        --upload-postgis \\
        --generate-pmtiles

Requirements:
    pip install numpy pandas geopandas rasterio shapely scipy tqdm psycopg2-binary
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import geopandas as gpd
    from shapely.geometry import box, mapping, shape, MultiPolygon, Polygon
    from shapely.ops import unary_union
except ImportError:
    gpd = None

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from scipy.ndimage import gaussian_filter
except ImportError:
    gaussian_filter = None

try:
    import rasterio
    from rasterio.features import shapes as rasterio_shapes
except ImportError:
    rasterio = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("batch_contours")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Papercut blue palette — shallow (light) → deep (dark)
PAPERCUT_BLUES = [
    "#E8F4FD", "#B8DCF0", "#7BB8DE", "#4A98C9",
    "#2574A9", "#1A5276", "#0E3D5C", "#071E2E",
]

M_TO_FT = 3.28084

# Contour intervals per quality tier (metres)
INTERVALS_BY_QUALITY = {
    "survey":   [0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 75, 100],
    "high":     [1, 2, 3, 5, 10, 15, 20],
    "moderate": [5, 10, 15, 20, 30],
    # "coarse" and "estimate" handled specially — no fixed intervals
}

# RMSE thresholds that map to contour quality tiers
RMSE_TIER_THRESHOLDS = {
    "survey": 1.0,    # RMSE < 1 m → survey-grade intervals
    "high": 3.0,      # RMSE < 3 m → Tier-1 ML
    "moderate": 6.0,  # RMSE < 6 m → Tier-2
    # > 6 m → coarse (deep basin only)
}

# Named regions for convenience CLI
REGIONS = {
    "us-midwest":   (43.0, -97.0, 49.0, -89.0),
    "mn":           (43.5, -97.2, 49.4, -89.5),
    "wi":           (42.5, -92.9, 47.1, -86.8),
    "mi":           (41.7, -90.4, 48.3, -82.1),
    "tx":           (25.8, -106.6, 36.5, -93.5),
    "fl":           (24.5, -87.6, 31.0, -80.0),
    "us-northeast": (39.0, -80.0, 47.5, -66.9),
}

# River depth zone definitions (metres)
RIVER_ZONES = [
    {"label": "shallow",  "min_m": 0.0,  "max_m": 0.5,  "color": "#B8DCF0"},
    {"label": "wadeable", "min_m": 0.5,  "max_m": 1.5,  "color": "#4A98C9"},
    {"label": "deep",     "min_m": 1.5,  "max_m": 999.0, "color": "#0E3D5C"},
]


# ---------------------------------------------------------------------------
# Quality classification
# ---------------------------------------------------------------------------

def classify_contour_quality(source: str, rmse_m: float, confidence: float) -> str:
    """
    Map a depth source + RMSE to a contour quality tier.

    Returns one of: survey, high, moderate, coarse, estimate
    """
    # Survey sources always get full resolution
    survey_sources = {
        "mn_dnr_survey", "wi_dnr_survey", "mi_dnr_survey",
        "tx_tpwd_survey", "cudem", "noaa_enc",
    }
    if source in survey_sources:
        return "survey"

    # Morphometric-only → just an outline with max-depth label
    if source in ("morphometric_prior", "k_donor") or confidence < 0.15:
        return "estimate"

    # RMSE-based tiers for ML and modeled sources
    if rmse_m < RMSE_TIER_THRESHOLDS["high"]:
        return "high"
    elif rmse_m < RMSE_TIER_THRESHOLDS["moderate"]:
        return "moderate"
    else:
        return "coarse"


# ---------------------------------------------------------------------------
# Core: generate_unified_contours
# ---------------------------------------------------------------------------

def generate_unified_contours(
    lake_id: str,
    depth_grid: np.ndarray,
    source_info: Dict[str, Any],
    transform=None,
    crs=None,
    lake_name: str = "Unknown",
) -> dict:
    """
    Generate confidence-adaptive filled contour polygons for one water body.

    Parameters
    ----------
    lake_id : str
        Unique water body identifier (e.g. NHD permanent_id).
    depth_grid : np.ndarray
        2-D depth raster in metres (H, W). NaN = no data / land.
    source_info : dict
        Metadata from ProductionDepthRouter, expected keys:
        source, rmse_m, confidence, tier, attribution.
    transform : rasterio Affine, optional
        Geo-transform for the depth raster.
    crs : rasterio CRS, optional
        CRS of the depth raster.
    lake_name : str
        Human-readable lake name.

    Returns
    -------
    dict : GeoJSON FeatureCollection
    """
    source = source_info.get("source", "unknown")
    rmse_m = source_info.get("rmse_m", 10.0)
    confidence = source_info.get("confidence", 0.3)
    tier = source_info.get("tier", "ml")
    attribution = source_info.get("attribution", "")

    quality = classify_contour_quality(source, rmse_m, confidence)

    # Replace NaN with 0 for contour extraction
    depth = np.where(np.isnan(depth_grid), 0.0, depth_grid).astype(np.float32)
    max_depth = float(np.nanmax(depth_grid[~np.isnan(depth_grid)])) if np.any(~np.isnan(depth_grid)) else 0.0

    if max_depth < 0.3:
        log.debug(f"Skipping {lake_name} ({lake_id}): max depth {max_depth:.2f}m too shallow")
        return _empty_fc()

    # Gaussian smoothing — sigma inversely proportional to confidence
    # High confidence → minimal smoothing (sigma=0.5); low confidence → heavy smoothing (sigma=4)
    if gaussian_filter is not None:
        sigma = max(0.5, 4.0 * (1.0 - confidence))
        depth = gaussian_filter(depth, sigma=sigma)
        depth = np.clip(depth, 0.0, max_depth)

    # Select intervals for this quality tier
    if quality in ("survey", "high", "moderate"):
        intervals = [d for d in INTERVALS_BY_QUALITY[quality] if d < max_depth * 0.95]
        if not intervals and max_depth > 0:
            intervals = [max_depth * 0.5]
    elif quality == "coarse":
        # Single "deep basin" polygon at 50% of max depth
        intervals = [max_depth * 0.5]
    else:
        # "estimate" — water outline only, no depth contours
        intervals = []

    features = []

    # Generate filled contour polygons (deeper = darker)
    if intervals and rasterio is not None:
        features.extend(
            _extract_filled_contours(
                depth, intervals, transform,
                lake_id=lake_id,
                lake_name=lake_name,
                source=source,
                rmse_m=rmse_m,
                confidence=confidence,
                quality=quality,
                attribution=attribution,
            )
        )

    # For coarse/estimate: add a max-depth label point at deepest pixel
    if quality in ("coarse", "estimate"):
        max_idx = np.unravel_index(np.argmax(depth), depth.shape)
        if transform is not None:
            lon, lat = rasterio.transform.xy(transform, max_idx[0], max_idx[1])
        else:
            lat, lon = float(max_idx[0]), float(max_idx[1])

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "depth_m": round(max_depth, 1),
                "depth_ft": round(max_depth * M_TO_FT, 1),
                "label": f"Max depth: {round(max_depth, 1)}m / {round(max_depth * M_TO_FT, 0)}ft",
                "lake_id": lake_id,
                "lake": lake_name,
                "source": source,
                "rmse_m": round(rmse_m, 2),
                "confidence": round(confidence, 3),
                "contour_quality": quality,
                "feature_type": "max_depth_label",
                "water_body_type": "lake",
                "attribution": attribution,
            },
        })

    log.info(
        f"  {lake_name} ({lake_id}): quality={quality}, "
        f"{len(features)} features, max_depth={max_depth:.1f}m, "
        f"rmse={rmse_m:.2f}m, confidence={confidence:.2f}"
    )

    return {"type": "FeatureCollection", "features": features}


def _extract_filled_contours(
    depth: np.ndarray,
    intervals: List[float],
    transform,
    *,
    lake_id: str,
    lake_name: str,
    source: str,
    rmse_m: float,
    confidence: float,
    quality: str,
    attribution: str,
) -> List[dict]:
    """Extract filled polygons for each depth interval (shallowest first)."""
    from rasterio.features import shapes as rio_shapes

    features = []
    smooth_tol = max(0.00005, 0.0003 * (1.0 - confidence))

    for band_idx, depth_val in enumerate(intervals):
        mask = (depth >= depth_val).astype(np.uint8)
        if mask.sum() == 0:
            continue

        try:
            polygons = []
            for geom, val in rio_shapes(mask, transform=transform):
                if val != 1:
                    continue
                poly = shape(geom)
                if poly.is_valid and poly.area > 1e-10:
                    if smooth_tol > 0:
                        poly = poly.simplify(smooth_tol, preserve_topology=True)
                    polygons.append(poly)

            if not polygons:
                continue

            merged = unary_union(polygons)
            geoms = merged.geoms if merged.geom_type == "MultiPolygon" else [merged]
            color_idx = min(band_idx, len(PAPERCUT_BLUES) - 1)

            for geom in geoms:
                if geom.area < 1e-10:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": {
                        "depth_m": round(depth_val, 1),
                        "depth_ft": round(depth_val * M_TO_FT, 1),
                        "band_index": band_idx,
                        "color": PAPERCUT_BLUES[color_idx],
                        "lake_id": lake_id,
                        "lake": lake_name,
                        "source": source,
                        "rmse_m": round(rmse_m, 2),
                        "confidence": round(confidence, 3),
                        "contour_quality": quality,
                        "water_body_type": "lake",
                        "attribution": attribution,
                    },
                })
        except Exception as e:
            log.warning(f"Contour extraction failed at {depth_val}m for {lake_name}: {e}")

    return features


# ---------------------------------------------------------------------------
# River depth zones
# ---------------------------------------------------------------------------

def generate_river_depth_zones(
    reach_comid: str,
    nhdplus_gdf: Optional[Any] = None,
    bankfull_df: Optional[Any] = None,
) -> dict:
    """
    Create 3 filled depth-zone polygons for a river reach:
        shallow (<0.5 m), wadeable (0.5-1.5 m), deep (>1.5 m).

    Buffers the NHDPlus flowline centerline by estimated width, then
    partitions into depth zones using hydraulic geometry proportions.

    Parameters
    ----------
    reach_comid : str
        NHDPlus COMID for the reach.
    nhdplus_gdf : GeoDataFrame, optional
        NHDPlus flowlines with geometry. If None, attempts to load from disk.
    bankfull_df : DataFrame, optional
        Bankfull attributes (depth_m, width_m). If None, uses defaults.

    Returns
    -------
    dict : GeoJSON FeatureCollection
    """
    if gpd is None:
        log.warning("geopandas required for river depth zones")
        return _empty_fc()

    comid = str(reach_comid)

    # Try to get the flowline geometry
    flowline_geom = None
    bankfull_width_m = 10.0   # fallback
    bankfull_depth_m = 0.6    # fallback

    if nhdplus_gdf is not None:
        match = nhdplus_gdf[nhdplus_gdf["COMID"].astype(str) == comid]
        if len(match) > 0:
            flowline_geom = match.iloc[0].geometry
    if bankfull_df is not None and pd is not None:
        bf_match = bankfull_df[bankfull_df["COMID"].astype(str) == comid]
        if len(bf_match) > 0:
            row = bf_match.iloc[0]
            bankfull_width_m = row.get("bankfull_width_m", bankfull_width_m)
            bankfull_depth_m = row.get("bankfull_depth_m", bankfull_depth_m)

    if flowline_geom is None:
        log.debug(f"No flowline geometry for COMID {comid}")
        return _empty_fc()

    features = []

    # Approximate degree-per-metre at mid-latitudes (~45°N)
    deg_per_m = 1.0 / 111320.0

    for zone in RIVER_ZONES:
        # Skip zones that exceed bankfull depth
        if zone["min_m"] > bankfull_depth_m:
            continue

        # Proportion of channel width for this depth zone (simplified parabolic)
        # Deepest zone is ~30% of width (thalweg), wadeable ~60%, shallow = full width
        if zone["label"] == "shallow":
            width_frac = 1.0
        elif zone["label"] == "wadeable":
            width_frac = 0.6
        else:
            width_frac = 0.3

        buffer_m = bankfull_width_m * width_frac * 0.5
        buffer_deg = buffer_m * deg_per_m

        try:
            zone_poly = flowline_geom.buffer(buffer_deg)
            if zone_poly.is_empty or zone_poly.area < 1e-12:
                continue

            features.append({
                "type": "Feature",
                "geometry": mapping(zone_poly),
                "properties": {
                    "depth_min_m": zone["min_m"],
                    "depth_max_m": min(zone["max_m"], bankfull_depth_m),
                    "depth_min_ft": round(zone["min_m"] * M_TO_FT, 1),
                    "depth_max_ft": round(min(zone["max_m"], bankfull_depth_m) * M_TO_FT, 1),
                    "zone": zone["label"],
                    "color": zone["color"],
                    "reach_comid": comid,
                    "bankfull_width_m": round(bankfull_width_m, 1),
                    "bankfull_depth_m": round(bankfull_depth_m, 2),
                    "source": "nhdplus_hydraulic_geometry",
                    "water_body_type": "river",
                    "attribution": "NHDPlus V2.1 bankfull geometry (public domain)",
                },
            })
        except Exception as e:
            log.warning(f"River zone '{zone['label']}' failed for COMID {comid}: {e}")

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Batch region processor
# ---------------------------------------------------------------------------

def batch_process_region(
    bbox: Tuple[float, float, float, float],
    output_dir: Path,
    lake_catalog_path: Optional[Path] = None,
    nhdplus_path: Optional[Path] = None,
    include_rivers: bool = True,
    include_ocean: bool = True,
    upload_postgis: bool = False,
    postgis_conn: Optional[str] = None,
    generate_pmtiles: bool = False,
) -> Dict[str, Any]:
    """
    Discover and process all water bodies in a bounding box.

    Parameters
    ----------
    bbox : tuple
        (min_lat, min_lon, max_lat, max_lon)
    output_dir : Path
        Directory for output GeoJSON / PMTiles.
    lake_catalog_path : Path, optional
        Parquet or CSV with lake metadata (lake_id, lat, lon, area_ha, max_depth_m, ...).
    nhdplus_path : Path, optional
        GeoPackage or shapefile of NHDPlus flowlines.
    include_rivers : bool
        Whether to process river reaches.
    include_ocean : bool
        Whether to generate ocean contours for coastal overlap.
    upload_postgis : bool
        Whether to upload results to PostGIS.
    postgis_conn : str, optional
        PostGIS connection string.
    generate_pmtiles : bool
        Whether to run tippecanoe after merging.

    Returns
    -------
    dict : summary statistics
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    min_lat, min_lon, max_lat, max_lon = bbox
    log.info(f"Processing region bbox=({min_lat},{min_lon},{max_lat},{max_lon})")

    stats = {
        "lakes_processed": 0,
        "lakes_skipped": 0,
        "rivers_processed": 0,
        "ocean_processed": False,
        "total_features": 0,
    }

    all_features: List[dict] = []
    lake_features: List[dict] = []
    river_features: List[dict] = []
    ocean_features: List[dict] = []

    # ── Lakes ──────────────────────────────────────────────────────
    lakes_df = _load_lake_catalog(lake_catalog_path, bbox)
    if lakes_df is not None and len(lakes_df) > 0:
        log.info(f"Found {len(lakes_df)} lakes in bbox")
        lake_features = _process_lakes(lakes_df, output_dir, stats)
    else:
        log.warning("No lakes found in bbox — check catalog path and bbox")

    # ── Rivers ─────────────────────────────────────────────────────
    if include_rivers:
        nhdplus_gdf, bankfull_df = _load_nhdplus(nhdplus_path, bbox)
        if nhdplus_gdf is not None and len(nhdplus_gdf) > 0:
            log.info(f"Found {len(nhdplus_gdf)} river reaches in bbox")
            river_features = _process_rivers(nhdplus_gdf, bankfull_df, stats)

    # ── Ocean ──────────────────────────────────────────────────────
    if include_ocean:
        ocean_features = _process_ocean(bbox, output_dir, stats)

    # ── Merge & write ──────────────────────────────────────────────
    all_features = lake_features + river_features + ocean_features
    stats["total_features"] = len(all_features)

    merged_fc = {"type": "FeatureCollection", "features": all_features}
    merged_path = output_dir / "all_contours.geojson"
    _write_geojson(merged_fc, merged_path)

    # Per-type outputs
    if lake_features:
        _write_geojson(
            {"type": "FeatureCollection", "features": lake_features},
            output_dir / "lake_contours.geojson",
        )
    if river_features:
        _write_geojson(
            {"type": "FeatureCollection", "features": river_features},
            output_dir / "river_contours.geojson",
        )
    if ocean_features:
        _write_geojson(
            {"type": "FeatureCollection", "features": ocean_features},
            output_dir / "ocean_contours.geojson",
        )

    # ── PostGIS upload ─────────────────────────────────────────────
    if upload_postgis and postgis_conn:
        upload_to_postgis(merged_path, "bathymetry_contours", postgis_conn)

    # ── PMTiles ────────────────────────────────────────────────────
    if generate_pmtiles:
        _generate_pmtiles(merged_path, output_dir)

    log.info(
        f"Batch complete: {stats['lakes_processed']} lakes, "
        f"{stats['rivers_processed']} rivers, "
        f"ocean={'yes' if stats['ocean_processed'] else 'no'}, "
        f"{stats['total_features']} total features"
    )
    return stats


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _empty_fc() -> dict:
    return {"type": "FeatureCollection", "features": []}


def _write_geojson(fc: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(fc, f)
    size_mb = path.stat().st_size / (1024 * 1024)
    log.info(f"Wrote {len(fc['features'])} features → {path} ({size_mb:.1f} MB)")


def _load_lake_catalog(
    catalog_path: Optional[Path],
    bbox: Tuple[float, float, float, float],
) -> Optional[Any]:
    """Load lake catalog, filter to bbox."""
    if catalog_path is None or pd is None:
        return None

    catalog_path = Path(catalog_path)
    if not catalog_path.exists():
        log.warning(f"Lake catalog not found: {catalog_path}")
        return None

    if catalog_path.suffix == ".parquet":
        df = pd.read_parquet(catalog_path)
    else:
        df = pd.read_csv(catalog_path)

    min_lat, min_lon, max_lat, max_lon = bbox

    # Look for lat/lon columns (various naming conventions)
    lat_col = next((c for c in df.columns if c.lower() in ("lat", "latitude", "center_lat")), None)
    lon_col = next((c for c in df.columns if c.lower() in ("lon", "longitude", "center_lon", "lng")), None)

    if lat_col and lon_col:
        mask = (
            (df[lat_col] >= min_lat) & (df[lat_col] <= max_lat) &
            (df[lon_col] >= min_lon) & (df[lon_col] <= max_lon)
        )
        df = df[mask].copy()

    log.info(f"Loaded {len(df)} lakes from {catalog_path}")
    return df


def _load_nhdplus(
    nhdplus_path: Optional[Path],
    bbox: Tuple[float, float, float, float],
) -> Tuple[Optional[Any], Optional[Any]]:
    """Load NHDPlus flowlines clipped to bbox. Returns (gdf, bankfull_df)."""
    if nhdplus_path is None or gpd is None:
        return None, None

    nhdplus_path = Path(nhdplus_path)
    if not nhdplus_path.exists():
        log.warning(f"NHDPlus path not found: {nhdplus_path}")
        return None, None

    try:
        min_lat, min_lon, max_lat, max_lon = bbox
        bbox_geom = box(min_lon, min_lat, max_lon, max_lat)
        gdf = gpd.read_file(nhdplus_path, bbox=bbox_geom)
        log.info(f"Loaded {len(gdf)} NHDPlus features from {nhdplus_path}")

        # Try to load bankfull attributes from companion file
        bankfull_path = nhdplus_path.parent / "bankfull_attributes.csv"
        bankfull_df = None
        if bankfull_path.exists() and pd is not None:
            bankfull_df = pd.read_csv(bankfull_path)
            log.info(f"Loaded {len(bankfull_df)} bankfull records")

        return gdf, bankfull_df
    except Exception as e:
        log.error(f"Failed to load NHDPlus: {e}")
        return None, None


def _process_lakes(
    lakes_df,
    output_dir: Path,
    stats: Dict[str, Any],
) -> List[dict]:
    """Run generate_unified_contours for each lake via ProductionDepthRouter."""
    from tqdm import tqdm

    features: List[dict] = []

    # Lazy import to avoid circular deps
    try:
        from production_depth_router import ProductionDepthRouter
        router = ProductionDepthRouter()
    except ImportError:
        log.warning("ProductionDepthRouter not available — using fallback lake processing")
        router = None

    id_col = next(
        (c for c in lakes_df.columns if c.lower() in ("lake_id", "id", "permanent_id", "dow")),
        lakes_df.columns[0],
    )
    name_col = next(
        (c for c in lakes_df.columns if c.lower() in ("lake_name", "name", "gnis_name")),
        None,
    )

    for idx, row in tqdm(lakes_df.iterrows(), total=len(lakes_df), desc="Lakes"):
        lake_id = str(row[id_col])
        lake_name = str(row[name_col]) if name_col else lake_id

        try:
            if router is not None:
                lake_map = router.get_lake_map(lake_id)
                if lake_map is None:
                    stats["lakes_skipped"] += 1
                    continue

                source_info = {
                    "source": lake_map.metadata.get("primary_source", "unknown"),
                    "rmse_m": float(np.nanmean(lake_map.rmse)),
                    "confidence": float(np.nanmean(lake_map.confidence)),
                    "tier": lake_map.metadata.get("tier", "ml"),
                    "attribution": lake_map.metadata.get("attribution", ""),
                }
                transform = lake_map.metadata.get("transform")
                crs = lake_map.metadata.get("crs")

                fc = generate_unified_contours(
                    lake_id, lake_map.depth, source_info,
                    transform=transform, crs=crs, lake_name=lake_name,
                )
            else:
                # Fallback: use max_depth column if available for a placeholder
                stats["lakes_skipped"] += 1
                continue

            if fc["features"]:
                features.extend(fc["features"])

                # Per-lake GeoJSON
                lake_path = output_dir / "lakes" / f"{lake_id}_contours.geojson"
                _write_geojson(fc, lake_path)

                stats["lakes_processed"] += 1

        except Exception as e:
            log.error(f"Failed to process lake {lake_name} ({lake_id}): {e}")
            stats["lakes_skipped"] += 1

    return features


def _process_rivers(
    nhdplus_gdf,
    bankfull_df,
    stats: Dict[str, Any],
) -> List[dict]:
    """Generate river depth zones for all reaches."""
    from tqdm import tqdm

    features: List[dict] = []
    comid_col = next(
        (c for c in nhdplus_gdf.columns if c.upper() == "COMID"),
        nhdplus_gdf.columns[0],
    )

    for idx, row in tqdm(nhdplus_gdf.iterrows(), total=len(nhdplus_gdf), desc="Rivers"):
        comid = str(row[comid_col])
        fc = generate_river_depth_zones(comid, nhdplus_gdf, bankfull_df)
        if fc["features"]:
            features.extend(fc["features"])
            stats["rivers_processed"] += 1

    return features


def _process_ocean(
    bbox: Tuple[float, float, float, float],
    output_dir: Path,
    stats: Dict[str, Any],
) -> List[dict]:
    """Fetch CUDEM/GEBCO and generate ocean contours for bbox."""
    try:
        from ocean_bathymetry import OceanBathymetryLayer
        ocean = OceanBathymetryLayer()

        min_lat, min_lon, max_lat, max_lon = bbox
        result = ocean.get_depth_grid(min_lat, min_lon, max_lat, max_lon)
        if result is None:
            return []

        depth_grid, meta = result
        if depth_grid.max() < 0.5:
            return []

        # Use the ocean module's own contour generation
        fc = ocean.generate_contours(
            depth_grid, meta.get("transform"), meta.get("crs"),
        )
        stats["ocean_processed"] = True
        return fc.get("features", [])

    except ImportError:
        log.info("ocean_bathymetry module not available — skipping ocean contours")
    except Exception as e:
        log.warning(f"Ocean contour generation failed: {e}")

    return []


# ---------------------------------------------------------------------------
# PostGIS upload
# ---------------------------------------------------------------------------

def upload_to_postgis(
    geojson_path: Path,
    table_name: str,
    conn_string: str,
) -> None:
    """
    Insert GeoJSON features into PostGIS bathymetry_contours table.

    Schema: id (serial), lake_id, depth_ft, geom (geometry), source,
            rmse_m, confidence, contour_quality, water_body_type, attribution
    """
    geojson_path = Path(geojson_path)
    if not geojson_path.exists():
        log.error(f"GeoJSON not found: {geojson_path}")
        return

    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        log.error("psycopg2 not installed — cannot upload to PostGIS")
        return

    with open(geojson_path) as f:
        fc = json.load(f)

    features = fc.get("features", [])
    if not features:
        log.warning("No features to upload")
        return

    log.info(f"Uploading {len(features)} features to PostGIS table '{table_name}'")

    conn = psycopg2.connect(conn_string)
    try:
        cur = conn.cursor()

        # Create table if not exists
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id SERIAL PRIMARY KEY,
                lake_id TEXT,
                depth_ft REAL,
                depth_m REAL,
                geom GEOMETRY,
                source TEXT,
                rmse_m REAL,
                confidence REAL,
                contour_quality TEXT,
                water_body_type TEXT,
                attribution TEXT,
                band_index INTEGER,
                color TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_{table_name}_lake_id
                ON {table_name} (lake_id);
            CREATE INDEX IF NOT EXISTS idx_{table_name}_geom
                ON {table_name} USING GIST (geom);
        """)

        # Batch insert
        rows = []
        for feat in features:
            props = feat.get("properties", {})
            geom_json = json.dumps(feat.get("geometry", {}))
            rows.append((
                props.get("lake_id", props.get("reach_comid", "")),
                props.get("depth_ft"),
                props.get("depth_m"),
                geom_json,
                props.get("source", ""),
                props.get("rmse_m"),
                props.get("confidence"),
                props.get("contour_quality", ""),
                props.get("water_body_type", ""),
                props.get("attribution", ""),
                props.get("band_index"),
                props.get("color", ""),
            ))

        insert_sql = f"""
            INSERT INTO {table_name}
                (lake_id, depth_ft, depth_m, geom, source, rmse_m, confidence,
                 contour_quality, water_body_type, attribution, band_index, color)
            VALUES %s
        """
        template = (
            "(%(lake_id)s, %(depth_ft)s, %(depth_m)s, "
            "ST_SetSRID(ST_GeomFromGeoJSON(%(geom)s), 4326), "
            "%(source)s, %(rmse_m)s, %(confidence)s, "
            "%(contour_quality)s, %(water_body_type)s, %(attribution)s, "
            "%(band_index)s, %(color)s)"
        )

        # Use executemany for simplicity with ST_GeomFromGeoJSON
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            for row in batch:
                cur.execute(
                    f"""INSERT INTO {table_name}
                        (lake_id, depth_ft, depth_m, geom, source, rmse_m,
                         confidence, contour_quality, water_body_type,
                         attribution, band_index, color)
                    VALUES (
                        %s, %s, %s,
                        ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )""",
                    row,
                )
            conn.commit()
            log.info(f"  Inserted batch {i // batch_size + 1} ({len(batch)} rows)")

        conn.commit()
        log.info(f"PostGIS upload complete: {len(rows)} rows → {table_name}")

    except Exception as e:
        conn.rollback()
        log.error(f"PostGIS upload failed: {e}")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PMTiles generation
# ---------------------------------------------------------------------------

def _generate_pmtiles(geojson_path: Path, output_dir: Path) -> Optional[Path]:
    """Convert merged GeoJSON to PMTiles via tippecanoe."""
    pmtiles_path = output_dir / "bathymetry.pmtiles"

    cmd = [
        "tippecanoe",
        "-o", str(pmtiles_path),
        "--force",
        "--name", "OpenCatch Bathymetry",
        "--description", "Confidence-adaptive depth contours",
        "--attribution", "OpenCatch ML Pipeline",
        "--minimum-zoom", "8",
        "--maximum-zoom", "14",
        "--coalesce-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        "--layer", "contours",
        str(geojson_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if result.returncode == 0:
            size_mb = pmtiles_path.stat().st_size / (1024 * 1024)
            log.info(f"PMTiles created: {pmtiles_path} ({size_mb:.1f} MB)")
            return pmtiles_path
        else:
            log.error(f"tippecanoe failed: {result.stderr}")
    except FileNotFoundError:
        log.warning(
            "tippecanoe not installed — skipping PMTiles generation. "
            "Install: brew install tippecanoe (macOS) or build from "
            "https://github.com/felt/tippecanoe"
        )
    except Exception as e:
        log.error(f"PMTiles generation failed: {e}")

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch batch contour generation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--region", type=str, default=None,
        help=f"Named region shortcut: {', '.join(REGIONS.keys())}",
    )
    parser.add_argument(
        "--bbox", type=str, default=None,
        help="Bounding box: min_lat,min_lon,max_lat,max_lon",
    )
    parser.add_argument(
        "--output", type=str, default="/data/contours",
        help="Output directory for GeoJSON / PMTiles",
    )
    parser.add_argument(
        "--lake-catalog", type=str, default=None,
        help="Path to lake catalog (parquet or CSV)",
    )
    parser.add_argument(
        "--nhdplus", type=str, default=None,
        help="Path to NHDPlus flowlines (GeoPackage or shapefile)",
    )
    parser.add_argument(
        "--no-rivers", action="store_true",
        help="Skip river depth zone generation",
    )
    parser.add_argument(
        "--no-ocean", action="store_true",
        help="Skip ocean contour generation",
    )
    parser.add_argument(
        "--upload-postgis", action="store_true",
        help="Upload results to PostGIS",
    )
    parser.add_argument(
        "--postgis-conn", type=str,
        default=os.environ.get("POSTGIS_CONN", ""),
        help="PostGIS connection string (or set POSTGIS_CONN env var)",
    )
    parser.add_argument(
        "--generate-pmtiles", action="store_true",
        help="Generate PMTiles via tippecanoe after merging",
    )
    args = parser.parse_args()

    # Resolve bbox
    if args.region and args.region in REGIONS:
        bbox = REGIONS[args.region]
        log.info(f"Using region '{args.region}' → bbox={bbox}")
    elif args.bbox:
        bbox = tuple(float(x.strip()) for x in args.bbox.split(","))
        if len(bbox) != 4:
            parser.error("--bbox must have exactly 4 values: min_lat,min_lon,max_lat,max_lon")
    else:
        parser.error("Provide either --region or --bbox")
        return

    t0 = time.time()
    stats = batch_process_region(
        bbox=bbox,
        output_dir=Path(args.output),
        lake_catalog_path=Path(args.lake_catalog) if args.lake_catalog else None,
        nhdplus_path=Path(args.nhdplus) if args.nhdplus else None,
        include_rivers=not args.no_rivers,
        include_ocean=not args.no_ocean,
        upload_postgis=args.upload_postgis,
        postgis_conn=args.postgis_conn,
        generate_pmtiles=args.generate_pmtiles,
    )

    elapsed = time.time() - t0
    log.info(f"Pipeline finished in {elapsed:.1f}s")
    log.info(f"Summary: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()
