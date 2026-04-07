#!/usr/bin/env python3
"""
OpenCatch -- Confidence-Gated Depth Contour Renderer
=====================================================
Renders aesthetic depth contour charts whose detail level is governed by
the confidence of the underlying depth source.

Quality tiers:
    1. Survey-backed        -> full detail (1ft / 0.5m), solid lines, all labels
    2. Glacial specialist   -> detailed   (2ft / 1m),   solid lines
    3. Mountain specialist  -> moderate   (5ft / 2m),   dashed deep bands
    4. Unified fallback     -> coarse     (10ft / 3m),  dashed, "estimated" tag
    5. No-data              -> outline only, max-depth label if LAGOS known

For each lake the renderer:
    * Determines source & confidence from the production depth router
    * Selects contour intervals matching the tier
    * Applies the PAPERCUT_BLUES colour palette
    * Generates filled polygons, contour lines, and depth labels
    * Emits GeoJSON with provenance metadata ready for PMTiles

Usage:
    python confidence_contour_renderer.py \\
        --lake-catalog /data/waterbodies/lake_catalog.parquet \\
        --depth-dir /data/production_depths \\
        --output /data/contours/confidence \\
        --states MN WI MI \\
        --generate-style

Requirements:
    pip install numpy pandas geopandas rasterio shapely scipy tqdm
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import geopandas as gpd
    from shapely.geometry import mapping, shape, MultiPolygon, Polygon, Point
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
    from rasterio.transform import from_bounds
except ImportError:
    rasterio = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("confidence_contours")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

M_TO_FT = 3.28084
FT_TO_M = 1.0 / M_TO_FT

# Papercut blue palette -- shallow (light) -> deep (dark)
PAPERCUT_BLUES = [
    "#E8F4FD", "#D0E8F5", "#B8DCF0", "#9BCAE5",
    "#7BB8DE", "#5DA6D4", "#4A98C9", "#3786BD",
    "#2574A9", "#1E6391", "#1A5276", "#14425F",
    "#0E3D5C", "#0A2E45", "#071E2E",
]

# Attribution strings by source
ATTRIBUTIONS = {
    "mn_dnr_survey":    "MN DNR Lake Survey Program",
    "wi_dnr_survey":    "WI DNR Lake Survey",
    "mi_deq_survey":    "MI DEQ Lake Bathymetry",
    "state_survey":     "State DNR Survey",
    "glacial_model":    "OpenCatch Glacial Specialist Model",
    "mountain_model":   "OpenCatch Mountain Specialist Model",
    "unified_fallback": "OpenCatch Unified Depth Estimate",
    "lagos_max_depth":  "LAGOS-NE Maximum Depth",
}

# States supported for batch processing
PMTILES_STATES = [
    "MN", "WI", "MI", "NY", "ME", "NH", "VT", "MA", "CT", "RI",
    "TX", "FL", "CO", "MT", "WY", "ID", "WA", "OR", "CA",
]


# ---------------------------------------------------------------------------
# Quality tier definitions
# ---------------------------------------------------------------------------

@dataclass
class ContourTier:
    """Rendering parameters for a confidence tier."""
    name: str
    contour_quality: str           # "survey", "high", "medium", "low", "estimate"
    intervals_ft: List[float]
    intervals_m: List[float]
    line_style: str                # "solid" or "dashed"
    label_visible: bool            # show depth numbers
    show_estimated_tag: bool       # append "(est.)" to labels
    min_confidence: float          # lower bound
    max_confidence: float          # upper bound (exclusive)
    source_pattern: str            # regex-ish key for source matching


TIERS: List[ContourTier] = [
    ContourTier(
        name="survey",
        contour_quality="survey",
        intervals_ft=[1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200],
        intervals_m=[0.5, 1, 1.5, 2, 3, 5, 7, 10, 15, 20, 25, 30, 50, 75],
        line_style="solid",
        label_visible=True,
        show_estimated_tag=False,
        min_confidence=0.90,
        max_confidence=1.01,
        source_pattern="survey",
    ),
    ContourTier(
        name="glacial_specialist",
        contour_quality="high",
        intervals_ft=[2, 5, 10, 15, 20, 30, 40, 50, 75, 100],
        intervals_m=[1, 2, 3, 5, 7, 10, 15, 20, 30, 50],
        line_style="solid",
        label_visible=True,
        show_estimated_tag=False,
        min_confidence=0.70,
        max_confidence=0.90,
        source_pattern="glacial",
    ),
    ContourTier(
        name="mountain_specialist",
        contour_quality="medium",
        intervals_ft=[5, 10, 20, 30, 50, 75, 100],
        intervals_m=[2, 5, 10, 15, 20, 30, 50],
        line_style="dashed",
        label_visible=True,
        show_estimated_tag=False,
        min_confidence=0.50,
        max_confidence=0.70,
        source_pattern="mountain",
    ),
    ContourTier(
        name="unified_fallback",
        contour_quality="low",
        intervals_ft=[10, 20, 30, 50, 100],
        intervals_m=[3, 5, 10, 15, 30],
        line_style="dashed",
        label_visible=True,
        show_estimated_tag=True,
        min_confidence=0.20,
        max_confidence=0.50,
        source_pattern="unified|fallback",
    ),
    ContourTier(
        name="no_data",
        contour_quality="estimate",
        intervals_ft=[],
        intervals_m=[],
        line_style="dashed",
        label_visible=False,
        show_estimated_tag=True,
        min_confidence=0.0,
        max_confidence=0.20,
        source_pattern="lagos|none",
    ),
]


@dataclass
class LakeRenderResult:
    """Stats for a single rendered lake."""
    lake_id: str
    lake_name: str
    tier_name: str
    source: str
    confidence: float
    num_contours: int
    num_labels: int
    max_depth_m: float
    render_time_s: float = 0.0


@dataclass
class BatchStats:
    """Aggregate statistics for a batch run."""
    total_lakes: int = 0
    rendered: int = 0
    skipped: int = 0
    by_tier: Dict[str, int] = field(default_factory=dict)
    total_features: int = 0
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------

def select_tier(source: str, confidence: float) -> ContourTier:
    """Choose the rendering tier based on depth source and confidence score."""
    src_lower = source.lower() if source else ""

    # Survey sources always get top tier regardless of confidence
    if "survey" in src_lower or "dnr" in src_lower or "deq" in src_lower:
        return TIERS[0]

    # Source-specific overrides
    if "glacial" in src_lower and confidence >= 0.65:
        return TIERS[1]
    if "mountain" in src_lower and confidence >= 0.45:
        return TIERS[2]

    # Fall through to confidence-based selection
    for tier in TIERS:
        if tier.min_confidence <= confidence < tier.max_confidence:
            return tier
    return TIERS[-1]


def depth_to_color(depth_m: float, max_depth_m: float) -> str:
    """Map a depth value to a PAPERCUT_BLUES colour."""
    if max_depth_m <= 0:
        return PAPERCUT_BLUES[0]
    frac = min(depth_m / max_depth_m, 1.0)
    idx = int(frac * (len(PAPERCUT_BLUES) - 1))
    idx = max(0, min(idx, len(PAPERCUT_BLUES) - 1))
    return PAPERCUT_BLUES[idx]


def get_attribution(source: str) -> str:
    """Return the attribution string for a data source."""
    src_lower = (source or "").lower()
    for key, value in ATTRIBUTIONS.items():
        if key in src_lower:
            return value
    return f"OpenCatch ({source})"


# ---------------------------------------------------------------------------
# Contour generation from depth grids (ML predictions)
# ---------------------------------------------------------------------------

def generate_contours_from_grid(
    depth_grid: np.ndarray,
    transform: Any,
    tier: ContourTier,
    lake_boundary: Optional[Any] = None,
    smooth_sigma: float = 1.5,
    units: str = "metric",
) -> List[Dict[str, Any]]:
    """
    Generate filled contour polygons from a depth raster grid.

    Parameters
    ----------
    depth_grid : 2D array of depth values in metres (NaN = no data).
    transform  : rasterio Affine transform for georeferencing.
    tier       : ContourTier controlling interval selection.
    lake_boundary : Optional Shapely geometry to clip contours.
    smooth_sigma  : Gaussian smoothing sigma (pixels). 0 = no smoothing.
    units      : "metric" or "imperial" for interval selection.

    Returns
    -------
    List of GeoJSON-like feature dicts (geometry + properties).
    """
    if rasterio is None:
        log.error("rasterio required for grid-based contour generation")
        return []

    intervals = tier.intervals_m if units == "metric" else [
        v * FT_TO_M for v in tier.intervals_ft
    ]
    if not intervals:
        return []

    # Smooth for aesthetic contours
    grid = depth_grid.copy()
    valid_mask = ~np.isnan(grid)
    if smooth_sigma > 0 and gaussian_filter is not None:
        filled = np.where(valid_mask, grid, 0.0)
        smoothed = gaussian_filter(filled, sigma=smooth_sigma)
        grid = np.where(valid_mask, smoothed, np.nan)

    max_depth = float(np.nanmax(grid)) if np.any(valid_mask) else 0.0
    features: List[Dict[str, Any]] = []

    # Build depth bands between consecutive intervals
    band_edges = [0.0] + [iv for iv in intervals if iv < max_depth] + [max_depth + 1.0]
    for i in range(len(band_edges) - 1):
        shallow = band_edges[i]
        deep = band_edges[i + 1]

        # Binary mask for this depth band
        band_mask = (grid >= shallow) & (grid < deep) & valid_mask
        if not np.any(band_mask):
            continue

        mask_u8 = band_mask.astype(np.uint8)
        try:
            polygons = list(rasterio_shapes(mask_u8, mask=mask_u8, transform=transform))
        except Exception as exc:
            log.warning("rasterio shapes failed for band %.1f-%.1f: %s", shallow, deep, exc)
            continue

        for geom_dict, _value in polygons:
            geom = shape(geom_dict)
            if lake_boundary is not None:
                geom = geom.intersection(lake_boundary)
            if geom.is_empty:
                continue

            mid_depth = (shallow + deep) / 2.0
            features.append({
                "type": "Feature",
                "geometry": mapping(geom),
                "properties": {
                    "depth_m": round(mid_depth, 2),
                    "depth_ft": round(mid_depth * M_TO_FT, 1),
                    "band_shallow_m": round(shallow, 2),
                    "band_deep_m": round(deep, 2),
                    "fill_color": depth_to_color(mid_depth, max_depth),
                    "line_style": tier.line_style,
                    "contour_quality": tier.contour_quality,
                },
            })

    log.debug("Generated %d contour polygons from grid (max_depth=%.1fm)", len(features), max_depth)
    return features


# ---------------------------------------------------------------------------
# Contour generation from survey vector data (direct rendering)
# ---------------------------------------------------------------------------

def generate_contours_from_survey(
    survey_gdf: Any,
    tier: ContourTier,
    max_depth_m: float,
) -> List[Dict[str, Any]]:
    """
    Re-render pre-existing survey contour lines as styled GeoJSON features.

    Parameters
    ----------
    survey_gdf : GeoDataFrame with depth column and line/polygon geometries.
    tier       : ContourTier (always survey tier for this path).
    max_depth_m : Maximum depth for colour mapping.

    Returns
    -------
    List of GeoJSON feature dicts.
    """
    features: List[Dict[str, Any]] = []
    depth_col = None
    for candidate in ("depth_m", "DEPTH_M", "depth_ft", "DEPTH_FT", "CONTOUR", "contour"):
        if candidate in survey_gdf.columns:
            depth_col = candidate
            break

    if depth_col is None:
        log.warning("No depth column found in survey GeoDataFrame")
        return features

    is_ft = "ft" in depth_col.lower()

    for _, row in survey_gdf.iterrows():
        raw = row[depth_col]
        if pd is not None and pd.isna(raw):
            continue
        depth_val = float(raw)
        depth_m = depth_val * FT_TO_M if is_ft else depth_val
        depth_ft = depth_val if is_ft else depth_val * M_TO_FT

        features.append({
            "type": "Feature",
            "geometry": mapping(row.geometry),
            "properties": {
                "depth_m": round(depth_m, 2),
                "depth_ft": round(depth_ft, 1),
                "fill_color": depth_to_color(depth_m, max_depth_m),
                "line_style": "solid",
                "contour_quality": "survey",
            },
        })

    log.debug("Rendered %d survey contour features", len(features))
    return features


# ---------------------------------------------------------------------------
# Depth label placement
# ---------------------------------------------------------------------------

def generate_depth_labels(
    contour_features: List[Dict[str, Any]],
    tier: ContourTier,
    lake_name: str = "",
    min_label_spacing_m: float = 200.0,
) -> List[Dict[str, Any]]:
    """
    Create point features for depth number labels at band centroids.

    Avoids overlap by enforcing a minimum spacing between labels.
    For low-confidence tiers, appends "(est.)" to the label text.
    """
    if not tier.label_visible:
        return []

    labels: List[Dict[str, Any]] = []
    placed_points: List[Tuple[float, float]] = []

    # Sort by depth so shallow labels come first
    sorted_features = sorted(contour_features, key=lambda f: f["properties"].get("depth_m", 0))

    for feat in sorted_features:
        props = feat["properties"]
        depth_m = props.get("depth_m", 0)
        depth_ft = props.get("depth_ft", 0)

        try:
            geom = shape(feat["geometry"])
            centroid = geom.centroid
        except Exception:
            continue

        cx, cy = centroid.x, centroid.y

        # Overlap check -- skip if too close to an existing label
        too_close = False
        for px, py in placed_points:
            dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
            # Approximate metres from degrees at mid-latitude
            dist_m = dist * 111_000 * 0.7
            if dist_m < min_label_spacing_m:
                too_close = True
                break
        if too_close:
            continue

        placed_points.append((cx, cy))

        label_text = f"{int(depth_ft)}'"
        if tier.show_estimated_tag:
            label_text += " (est.)"

        labels.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [cx, cy]},
            "properties": {
                "label_text": label_text,
                "depth_m": round(depth_m, 2),
                "depth_ft": round(depth_ft, 1),
                "label_visible": True,
                "contour_quality": tier.contour_quality,
            },
        })

    log.debug("Placed %d depth labels for %s", len(labels), lake_name or "lake")
    return labels


# ---------------------------------------------------------------------------
# No-data outline renderer
# ---------------------------------------------------------------------------

def generate_outline_only(
    lake_geom: Any,
    lake_id: str,
    lake_name: str,
    max_depth_m: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Render a lake outline with optional max-depth label (LAGOS fallback).

    Returns (contour_features, label_features).
    """
    contours = [{
        "type": "Feature",
        "geometry": mapping(lake_geom),
        "properties": {
            "depth_m": 0,
            "depth_ft": 0,
            "fill_color": PAPERCUT_BLUES[0],
            "line_style": "dashed",
            "contour_quality": "estimate",
            "is_outline": True,
        },
    }]

    labels = []
    if max_depth_m is not None and max_depth_m > 0:
        centroid = lake_geom.centroid
        labels.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [centroid.x, centroid.y]},
            "properties": {
                "label_text": f"Max ~{int(max_depth_m * M_TO_FT)}' (est.)",
                "depth_m": round(max_depth_m, 2),
                "depth_ft": round(max_depth_m * M_TO_FT, 1),
                "label_visible": True,
                "contour_quality": "estimate",
            },
        })

    return contours, labels


# ---------------------------------------------------------------------------
# Single lake renderer
# ---------------------------------------------------------------------------

def render_lake(
    lake_id: str,
    lake_name: str,
    source: str,
    confidence: float,
    depth_grid: Optional[np.ndarray] = None,
    grid_transform: Optional[Any] = None,
    survey_gdf: Optional[Any] = None,
    lake_boundary: Optional[Any] = None,
    max_depth_m: Optional[float] = None,
    rmse_m: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Render depth contours for a single lake with confidence gating.

    Returns a GeoJSON FeatureCollection dict with provenance metadata.
    """
    t0 = time.monotonic()
    tier = select_tier(source, confidence)
    attribution = get_attribution(source)

    contour_features: List[Dict[str, Any]] = []
    label_features: List[Dict[str, Any]] = []

    # --- Route by data availability ---

    if tier.name == "no_data":
        # Outline only
        if lake_boundary is not None:
            contour_features, label_features = generate_outline_only(
                lake_boundary, lake_id, lake_name, max_depth_m
            )
        else:
            log.warning("Lake %s (%s): no boundary geometry, skipping", lake_id, lake_name)
            return _empty_fc(lake_id, lake_name, source, confidence, tier, attribution)

    elif survey_gdf is not None and len(survey_gdf) > 0:
        # Direct rendering of survey contour data
        survey_max = max_depth_m or 30.0
        contour_features = generate_contours_from_survey(survey_gdf, tier, survey_max)
        label_features = generate_depth_labels(contour_features, tier, lake_name)

    elif depth_grid is not None and grid_transform is not None:
        # ML-predicted depth grid -> contours
        contour_features = generate_contours_from_grid(
            depth_grid, grid_transform, tier, lake_boundary
        )
        label_features = generate_depth_labels(contour_features, tier, lake_name)

    else:
        log.warning("Lake %s (%s): no depth data or grid, falling back to outline", lake_id, lake_name)
        if lake_boundary is not None:
            contour_features, label_features = generate_outline_only(
                lake_boundary, lake_id, lake_name, max_depth_m
            )

    # Stamp provenance on every feature
    for feat in contour_features + label_features:
        feat["properties"].update({
            "lake_id": lake_id,
            "lake_name": lake_name,
            "source": source,
            "confidence": round(confidence, 3),
            "contour_quality": tier.contour_quality,
            "line_style": tier.line_style,
            "label_visible": tier.label_visible,
            "attribution": attribution,
        })
        if rmse_m is not None:
            feat["properties"]["rmse_m"] = round(rmse_m, 2)

    elapsed = time.monotonic() - t0
    all_features = contour_features + label_features
    log.info(
        "Lake %s (%s): tier=%s, %d contours + %d labels [%.2fs]",
        lake_id, lake_name, tier.name, len(contour_features), len(label_features), elapsed,
    )

    return {
        "type": "FeatureCollection",
        "features": all_features,
        "properties": {
            "lake_id": lake_id,
            "lake_name": lake_name,
            "source": source,
            "confidence": round(confidence, 3),
            "tier": tier.name,
            "contour_quality": tier.contour_quality,
            "num_contours": len(contour_features),
            "num_labels": len(label_features),
            "render_time_s": round(elapsed, 3),
            "attribution": attribution,
        },
    }


def _empty_fc(
    lake_id: str, lake_name: str, source: str,
    confidence: float, tier: ContourTier, attribution: str,
) -> Dict[str, Any]:
    """Return an empty FeatureCollection with metadata."""
    return {
        "type": "FeatureCollection",
        "features": [],
        "properties": {
            "lake_id": lake_id,
            "lake_name": lake_name,
            "source": source,
            "confidence": round(confidence, 3),
            "tier": tier.name,
            "contour_quality": tier.contour_quality,
            "num_contours": 0,
            "num_labels": 0,
            "render_time_s": 0.0,
            "attribution": attribution,
        },
    }


# ---------------------------------------------------------------------------
# MapLibre style JSON generation
# ---------------------------------------------------------------------------

def generate_maplibre_style(
    pmtiles_url: str = "pmtiles://https://tiles.opencatch.com/contours/{state}.pmtiles",
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Generate a MapLibre GL style JSON with confidence-based styling rules.

    Includes:
    * Fill layer with depth-based colour from feature properties
    * Line layer with solid/dashed styling per confidence tier
    * Label layer with depth numbers, visibility gated by quality tier
    """
    source_id = "depth-contours"

    style: Dict[str, Any] = {
        "version": 8,
        "name": "OpenCatch Depth Contours",
        "sources": {
            source_id: {
                "type": "vector",
                "url": pmtiles_url,
            },
        },
        "layers": [
            # Filled depth band polygons
            {
                "id": "depth-fill",
                "type": "fill",
                "source": source_id,
                "source-layer": "contours",
                "filter": ["has", "fill_color"],
                "paint": {
                    "fill-color": ["get", "fill_color"],
                    "fill-opacity": [
                        "match", ["get", "contour_quality"],
                        "survey", 0.75,
                        "high", 0.65,
                        "medium", 0.55,
                        "low", 0.40,
                        0.30,
                    ],
                },
            },
            # Contour lines -- solid for high confidence, dashed for lower
            {
                "id": "depth-lines-solid",
                "type": "line",
                "source": source_id,
                "source-layer": "contours",
                "filter": ["==", ["get", "line_style"], "solid"],
                "paint": {
                    "line-color": "#1A5276",
                    "line-width": [
                        "interpolate", ["linear"], ["zoom"],
                        8, 0.3,
                        14, 1.2,
                    ],
                    "line-opacity": 0.6,
                },
            },
            {
                "id": "depth-lines-dashed",
                "type": "line",
                "source": source_id,
                "source-layer": "contours",
                "filter": ["==", ["get", "line_style"], "dashed"],
                "paint": {
                    "line-color": "#2574A9",
                    "line-width": [
                        "interpolate", ["linear"], ["zoom"],
                        8, 0.3,
                        14, 1.0,
                    ],
                    "line-opacity": 0.5,
                    "line-dasharray": [4, 3],
                },
            },
            # Depth number labels
            {
                "id": "depth-labels",
                "type": "symbol",
                "source": source_id,
                "source-layer": "labels",
                "filter": ["==", ["get", "label_visible"], True],
                "minzoom": 11,
                "layout": {
                    "text-field": ["get", "label_text"],
                    "text-size": [
                        "interpolate", ["linear"], ["zoom"],
                        11, 10,
                        15, 14,
                    ],
                    "text-font": ["DIN Pro Medium", "Arial Unicode MS Regular"],
                    "text-allow-overlap": False,
                    "text-ignore-placement": False,
                    "text-padding": 8,
                    "symbol-sort-key": ["get", "depth_m"],
                },
                "paint": {
                    "text-color": "#0E3D5C",
                    "text-halo-color": "#FFFFFF",
                    "text-halo-width": 1.5,
                    "text-opacity": [
                        "match", ["get", "contour_quality"],
                        "survey", 1.0,
                        "high", 0.9,
                        "medium", 0.75,
                        "low", 0.6,
                        0.5,
                    ],
                },
            },
        ],
    }

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(style, f, indent=2)
        log.info("Wrote MapLibre style to %s", output_path)

    return style


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def batch_render_state(
    state: str,
    lake_catalog_path: Path,
    depth_dir: Path,
    output_dir: Path,
    max_lakes: Optional[int] = None,
) -> BatchStats:
    """
    Render confidence-gated contours for all lakes in a state.

    Reads lake metadata from a parquet catalog, loads depth grids
    or survey data from depth_dir, and writes per-lake + merged GeoJSON.
    """
    if pd is None or gpd is None:
        log.error("pandas and geopandas required for batch rendering")
        return BatchStats()

    t0 = time.monotonic()
    stats = BatchStats()

    # Load catalog
    catalog = pd.read_parquet(lake_catalog_path)
    state_lakes = catalog[catalog["state"] == state].copy()
    stats.total_lakes = len(state_lakes)
    if max_lakes:
        state_lakes = state_lakes.head(max_lakes)
    log.info("Batch rendering %s: %d lakes (of %d total)", state, len(state_lakes), stats.total_lakes)

    state_output = output_dir / state.lower()
    state_output.mkdir(parents=True, exist_ok=True)

    all_features: List[Dict[str, Any]] = []

    for idx, (_, row) in enumerate(state_lakes.iterrows()):
        lake_id = str(row.get("lake_id", row.get("permanent_id", f"unk_{idx}")))
        lake_name = str(row.get("lake_name", row.get("gnis_name", "")))
        source = str(row.get("depth_source", "unknown"))
        confidence = float(row.get("confidence", 0.0))
        max_depth_m = float(row["max_depth_m"]) if "max_depth_m" in row and not pd.isna(row.get("max_depth_m")) else None
        rmse_m = float(row["rmse_m"]) if "rmse_m" in row and not pd.isna(row.get("rmse_m")) else None

        # Try to load depth grid
        grid_path = depth_dir / state.lower() / f"{lake_id}_depth.tif"
        depth_grid = None
        grid_transform = None
        if grid_path.exists() and rasterio is not None:
            try:
                with rasterio.open(grid_path) as ds:
                    depth_grid = ds.read(1).astype(np.float32)
                    depth_grid[depth_grid == ds.nodata] = np.nan
                    grid_transform = ds.transform
            except Exception as exc:
                log.warning("Failed to read depth grid %s: %s", grid_path, exc)

        # Try to load survey contours
        survey_gdf = None
        survey_path = depth_dir / state.lower() / f"{lake_id}_survey.geojson"
        if survey_path.exists() and gpd is not None:
            try:
                survey_gdf = gpd.read_file(survey_path)
            except Exception as exc:
                log.warning("Failed to read survey %s: %s", survey_path, exc)

        # Load boundary
        lake_boundary = None
        boundary_path = depth_dir / state.lower() / f"{lake_id}_boundary.geojson"
        if boundary_path.exists() and gpd is not None:
            try:
                bnd = gpd.read_file(boundary_path)
                if len(bnd) > 0:
                    lake_boundary = bnd.geometry.iloc[0]
            except Exception:
                pass

        fc = render_lake(
            lake_id=lake_id,
            lake_name=lake_name,
            source=source,
            confidence=confidence,
            depth_grid=depth_grid,
            grid_transform=grid_transform,
            survey_gdf=survey_gdf,
            lake_boundary=lake_boundary,
            max_depth_m=max_depth_m,
            rmse_m=rmse_m,
        )

        n_feats = len(fc.get("features", []))
        if n_feats > 0:
            stats.rendered += 1
            tier_name = fc["properties"]["tier"]
            stats.by_tier[tier_name] = stats.by_tier.get(tier_name, 0) + 1
            stats.total_features += n_feats
            all_features.extend(fc["features"])

            # Write per-lake GeoJSON
            lake_out = state_output / f"{lake_id}.geojson"
            with open(lake_out, "w") as f:
                json.dump(fc, f)
        else:
            stats.skipped += 1

        if (idx + 1) % 500 == 0:
            log.info("  ... processed %d / %d lakes", idx + 1, len(state_lakes))

    # Write merged state GeoJSON
    merged_path = output_dir / f"{state.lower()}_contours.geojson"
    merged_fc = {"type": "FeatureCollection", "features": all_features}
    with open(merged_path, "w") as f:
        json.dump(merged_fc, f)

    stats.elapsed_s = time.monotonic() - t0
    log.info(
        "State %s complete: %d rendered, %d skipped, %d features in %.1fs | tiers: %s",
        state, stats.rendered, stats.skipped, stats.total_features,
        stats.elapsed_s, dict(stats.by_tier),
    )
    return stats


def batch_render_all(
    states: List[str],
    lake_catalog_path: Path,
    depth_dir: Path,
    output_dir: Path,
    generate_style: bool = True,
    max_lakes_per_state: Optional[int] = None,
) -> Dict[str, BatchStats]:
    """Render contours for all specified states."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: Dict[str, BatchStats] = {}
    for state in states:
        log.info("=" * 60)
        log.info("Starting state: %s", state)
        log.info("=" * 60)
        results[state] = batch_render_state(
            state=state,
            lake_catalog_path=Path(lake_catalog_path),
            depth_dir=Path(depth_dir),
            output_dir=output_dir,
            max_lakes=max_lakes_per_state,
        )

    # Summary
    total_rendered = sum(s.rendered for s in results.values())
    total_features = sum(s.total_features for s in results.values())
    log.info("=" * 60)
    log.info("BATCH COMPLETE: %d states, %d lakes rendered, %d total features",
             len(states), total_rendered, total_features)

    if generate_style:
        style_path = output_dir / "maplibre_depth_style.json"
        generate_maplibre_style(output_path=style_path)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch Confidence-Gated Depth Contour Renderer",
    )
    parser.add_argument("--lake-catalog", type=Path, required=True,
                        help="Parquet file with lake metadata (lake_id, state, depth_source, confidence, ...)")
    parser.add_argument("--depth-dir", type=Path, required=True,
                        help="Root directory with per-state depth grids and survey data")
    parser.add_argument("--output", type=Path, required=True,
                        help="Output directory for GeoJSON contours")
    parser.add_argument("--states", nargs="+", default=PMTILES_STATES,
                        help="State codes to process (default: all PMTiles states)")
    parser.add_argument("--max-lakes", type=int, default=None,
                        help="Limit lakes per state (for testing)")
    parser.add_argument("--generate-style", action="store_true",
                        help="Generate MapLibre style JSON")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    batch_render_all(
        states=args.states,
        lake_catalog_path=args.lake_catalog,
        depth_dir=args.depth_dir,
        output_dir=args.output,
        generate_style=args.generate_style,
        max_lakes_per_state=args.max_lakes,
    )


if __name__ == "__main__":
    main()
