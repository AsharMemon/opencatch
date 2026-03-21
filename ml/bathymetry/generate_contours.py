#!/usr/bin/env python3
"""
OpenCatch — Batch Contour Generation for All Lakes

Takes a trained Stage 2 U-Net model and generates depth contour lines
for every lake, outputting GeoJSON that can be converted to vector tiles
for MapLibre rendering.

Pipeline:
1. Load trained model
2. For each lake: load S2 imagery → predict depth raster → generate contours
3. Output: per-lake GeoJSON + merged national GeoJSON
4. Optionally run tippecanoe to create PMTiles

Usage:
    python generate_contours.py \
        --model /data/models/stage2/best_model.pt \
        --lakes /data/waterbodies/us/all_us_waterbodies.geojson \
        --s2-dir /data/sentinel2 \
        --stage1-dir /data/models/stage1 \
        --output /data/contours \
        --device cuda

Requirements:
    pip install torch rasterio geopandas shapely numpy tqdm lightgbm
"""

import argparse
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# Standard depth contour intervals (metres)
CONTOUR_INTERVALS_M = [0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 75, 100]
# Corresponding feet intervals for display
CONTOUR_INTERVALS_FT = [2, 3, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200]


def load_unet_model(model_path: Path, device: str = 'cuda'):
    """Load trained BathymetryUNet model."""
    import torch
    from predict_depth import BathymetryUNet

    model = BathymetryUNet(in_channels=11)
    state_dict = torch.load(model_path, map_location=device, weights_only=True)
    model.load_state_dict(state_dict)
    model.to(device)
    model.eval()
    log.info(f"Loaded U-Net from {model_path}")
    return model


def load_stage1_models(stage1_dir: Path):
    """Load Stage 1 LightGBM models for max depth estimation."""
    import lightgbm as lgb

    models = []
    for model_path in sorted(stage1_dir.glob('lgb_fold*.txt')):
        model = lgb.Booster(model_file=str(model_path))
        models.append(model)

    if models:
        log.info(f"Loaded {len(models)} Stage 1 LightGBM models from {stage1_dir}")
    return models


def predict_depth_raster(
    model,
    s2_path: Path,
    max_depth: Optional[float] = None,
    device: str = 'cuda',
) -> tuple[np.ndarray, dict]:
    """
    Predict depth raster for a single lake from Sentinel-2 imagery.

    Args:
        model: Trained BathymetryUNet
        s2_path: Path to multi-band S2 GeoTIFF
        max_depth: Optional max depth constraint from Stage 1
        device: Compute device

    Returns:
        (depth_raster, metadata) where depth_raster is (H, W) in metres
    """
    import torch
    import rasterio
    from predict_depth import compute_band_features

    with rasterio.open(s2_path) as src:
        bands = src.read()  # (C, H, W)
        transform = src.transform
        crs = src.crs
        profile = src.profile

    # Compute spectral features
    bands_hwc = np.transpose(bands, (1, 2, 0))  # (H, W, C)
    features = compute_band_features(bands_hwc)  # (H, W, F)
    features_chw = np.transpose(features, (2, 0, 1))  # (F, H, W)

    # Water mask from NIR band
    nir = bands[3] if bands.shape[0] > 3 else bands[-1]
    water_mask = (nir < 0.15).astype(np.float32)

    # Predict depth
    with torch.no_grad():
        x = torch.from_numpy(features_chw[np.newaxis]).to(device)  # (1, F, H, W)

        # Process in tiles if image is large
        _, _, h, w = x.shape
        if h > 1024 or w > 1024:
            depth = _predict_tiled(model, x, tile_size=512, overlap=64, device=device)
        else:
            depth = model(x).cpu().numpy()[0, 0]  # (H, W)

    # Apply water mask
    depth = depth * water_mask

    # Clamp to max depth if available from Stage 1
    if max_depth is not None and max_depth > 0:
        depth = np.clip(depth, 0, max_depth * 1.2)  # Allow 20% overshoot

    metadata = {
        'transform': transform,
        'crs': crs,
        'profile': profile,
        'water_mask': water_mask,
    }

    return depth, metadata


def _predict_tiled(model, x, tile_size=512, overlap=64, device='cuda'):
    """Predict large images in overlapping tiles to avoid OOM."""
    import torch

    _, _, h, w = x.shape
    depth = np.zeros((h, w), dtype=np.float32)
    count = np.zeros((h, w), dtype=np.float32)

    step = tile_size - overlap

    for y in range(0, h, step):
        for xi in range(0, w, step):
            y_end = min(y + tile_size, h)
            x_end = min(xi + tile_size, w)
            y_start = max(0, y_end - tile_size)
            x_start = max(0, x_end - tile_size)

            tile = x[:, :, y_start:y_end, x_start:x_end]
            with torch.no_grad():
                pred = model(tile).cpu().numpy()[0, 0]

            depth[y_start:y_end, x_start:x_end] += pred
            count[y_start:y_end, x_start:x_end] += 1

    depth /= np.maximum(count, 1)
    return depth


def depth_to_contours(
    depth_raster: np.ndarray,
    transform,
    crs,
    intervals_m: list[float] = None,
    smooth_tolerance: float = 0.0002,
    lake_name: str = 'Unknown',
    lake_id: str = '',
    filled: bool = True,
) -> dict:
    """
    Convert depth raster to GeoJSON FeatureCollection.

    Two modes:
    - filled=True (default): Filled polygons for papercut blue style.
      Each polygon represents "all area deeper than X metres."
      Render shallowest first, deepest last for the layered look.
    - filled=False: Contour boundary lines (original behavior).

    Uses rasterio.features.shapes for robust contour extraction.
    """
    from rasterio.features import shapes
    import shapely.geometry as sg
    from shapely.ops import unary_union

    # Papercut blue palette (light shallow → dark deep)
    PAPERCUT_BLUES = [
        '#E8F4FD', '#B8DCF0', '#7BB8DE', '#4A98C9',
        '#2574A9', '#1A5276', '#0E3D5C', '#071E2E',
    ]

    if intervals_m is None:
        # Auto-select intervals based on max depth
        max_depth = np.nanmax(depth_raster[depth_raster > 0]) if np.any(depth_raster > 0) else 0
        intervals_m = [d for d in CONTOUR_INTERVALS_M if d < max_depth * 0.95]
        if not intervals_m and max_depth > 0:
            intervals_m = [max_depth * 0.25, max_depth * 0.5, max_depth * 0.75]

    features = []

    for band_idx, depth_val in enumerate(intervals_m):
        mask = (depth_raster >= depth_val).astype(np.uint8)

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

            if filled:
                # Output filled polygons for papercut style
                geoms = merged.geoms if merged.geom_type == 'MultiPolygon' else [merged]
                color_idx = min(band_idx, len(PAPERCUT_BLUES) - 1)

                for geom in geoms:
                    if geom.area < 1e-10:
                        continue
                    features.append({
                        'type': 'Feature',
                        'geometry': sg.mapping(geom),
                        'properties': {
                            'depth_m': round(depth_val, 1),
                            'depth_ft': round(depth_val * 3.28084, 1),
                            'band_index': band_idx,
                            'color': PAPERCUT_BLUES[color_idx],
                            'lake': lake_name,
                            'lake_id': lake_id,
                            'source': 'opencatch-ml',
                        },
                    })
            else:
                # Output contour boundary lines (original behavior)
                if merged.geom_type == 'MultiPolygon':
                    boundaries = [p.exterior for p in merged.geoms]
                elif merged.geom_type == 'Polygon':
                    boundaries = [merged.exterior]
                else:
                    continue

                for boundary in boundaries:
                    if boundary.length < 1e-6:
                        continue
                    features.append({
                        'type': 'Feature',
                        'geometry': sg.mapping(boundary),
                        'properties': {
                            'depth_m': round(depth_val, 1),
                            'depth_ft': round(depth_val * 3.28084, 1),
                            'lake': lake_name,
                            'lake_id': lake_id,
                            'source': 'opencatch-ml',
                        },
                    })

        except Exception as e:
            log.warning(f"Contour generation failed at {depth_val}m: {e}")

    return {
        'type': 'FeatureCollection',
        'features': features,
    }


def process_lake(
    model,
    s2_path: Path,
    lake_name: str,
    output_dir: Path,
    max_depth: Optional[float] = None,
    device: str = 'cuda',
) -> Optional[dict]:
    """Process a single lake: predict depth → generate contours → save."""

    try:
        # Predict depth
        depth, meta = predict_depth_raster(model, s2_path, max_depth, device)

        if depth.max() < 0.3:
            log.warning(f"Skipping {lake_name}: max predicted depth {depth.max():.2f}m (too shallow)")
            return None

        # Save depth raster
        import rasterio
        depth_path = output_dir / 'rasters' / f"{lake_name}_depth.tif"
        depth_path.parent.mkdir(parents=True, exist_ok=True)

        profile = meta['profile'].copy()
        profile.update(count=1, dtype='float32', compress='deflate')
        with rasterio.open(depth_path, 'w', **profile) as dst:
            dst.write(depth, 1)

        # Generate contours
        geojson = depth_to_contours(
            depth, meta['transform'], meta['crs'],
            lake_name=lake_name,
        )

        if geojson['features']:
            geojson_path = output_dir / 'geojson' / f"{lake_name}_contours.geojson"
            geojson_path.parent.mkdir(parents=True, exist_ok=True)
            with open(geojson_path, 'w') as f:
                json.dump(geojson, f)

            log.info(f"  {lake_name}: {len(geojson['features'])} contour lines, "
                     f"max depth {depth.max():.1f}m")
            return geojson

    except Exception as e:
        log.error(f"Failed to process {lake_name}: {e}")

    return None


def merge_geojson(output_dir: Path) -> Path:
    """Merge all per-lake GeoJSON into a single national file."""
    geojson_dir = output_dir / 'geojson'
    all_features = []

    for f in sorted(geojson_dir.glob('*_contours.geojson')):
        with open(f) as fp:
            data = json.load(fp)
            all_features.extend(data.get('features', []))

    merged = {
        'type': 'FeatureCollection',
        'features': all_features,
    }

    merged_path = output_dir / 'all_contours.geojson'
    with open(merged_path, 'w') as f:
        json.dump(merged, f)

    log.info(f"Merged {len(all_features)} contour features → {merged_path}")
    return merged_path


def generate_pmtiles(geojson_path: Path, output_dir: Path) -> Optional[Path]:
    """
    Convert GeoJSON contours to PMTiles using tippecanoe.
    PMTiles can be served directly by MapLibre GL without a tile server.
    """
    pmtiles_path = output_dir / 'contours.pmtiles'

    cmd = [
        'tippecanoe',
        '-o', str(pmtiles_path),
        '--force',
        '--name', 'OpenCatch Bathymetry',
        '--description', 'Satellite-derived depth contours',
        '--attribution', 'OpenCatch ML Pipeline',
        '--minimum-zoom', '8',
        '--maximum-zoom', '14',
        '--coalesce-densest-as-needed',  # Preserve nested polygon structure
        '--extend-zooms-if-still-dropping',
        '--layer', 'contours',
        str(geojson_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0:
            size_mb = pmtiles_path.stat().st_size / 1024 / 1024
            log.info(f"PMTiles created: {pmtiles_path} ({size_mb:.1f} MB)")
            return pmtiles_path
        else:
            log.error(f"tippecanoe failed: {result.stderr}")
    except FileNotFoundError:
        log.warning("tippecanoe not installed — skipping PMTiles generation")
        log.info("Install: brew install tippecanoe (macOS) or build from https://github.com/felt/tippecanoe")
    except Exception as e:
        log.error(f"PMTiles generation failed: {e}")

    return None


def main():
    parser = argparse.ArgumentParser(description='Generate depth contours for all lakes')
    parser.add_argument('--model', type=str, required=True,
                       help='Path to trained Stage 2 U-Net model (.pt)')
    parser.add_argument('--lakes', type=str, required=True,
                       help='GeoJSON of lake polygons')
    parser.add_argument('--s2-dir', type=str, required=True,
                       help='Directory containing S2 imagery tiles')
    parser.add_argument('--stage1-dir', type=str, default=None,
                       help='Stage 1 model dir (for max depth constraints)')
    parser.add_argument('--output', type=str, default='/data/contours',
                       help='Output directory')
    parser.add_argument('--device', type=str, default='cuda',
                       help='Compute device (cuda/cpu)')
    parser.add_argument('--pmtiles', action='store_true',
                       help='Generate PMTiles using tippecanoe')
    args = parser.parse_args()

    import torch
    device = args.device if torch.cuda.is_available() else 'cpu'
    log.info(f"Using device: {device}")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load models
    model = load_unet_model(Path(args.model), device)

    stage1_models = None
    stage1_predictions = {}
    if args.stage1_dir:
        stage1_models = load_stage1_models(Path(args.stage1_dir))
        # Load precomputed max depth predictions if available
        pred_path = Path(args.stage1_dir) / 'all_predictions.csv'
        if pred_path.exists():
            import pandas as pd
            preds = pd.read_csv(pred_path)
            if 'GNIS_Name' in preds.columns and 'predicted_max_depth_m' in preds.columns:
                stage1_predictions = dict(zip(
                    preds['GNIS_Name'].str.lower(),
                    preds['predicted_max_depth_m']
                ))
                log.info(f"Loaded {len(stage1_predictions)} Stage 1 max depth predictions")

    # Find S2 tiles
    s2_dir = Path(args.s2_dir)
    s2_files = sorted(s2_dir.glob('*_s2.tif'))
    log.info(f"Found {len(s2_files)} S2 imagery tiles")

    if not s2_files:
        log.error(f"No S2 tiles found in {s2_dir}")
        return

    # Process each lake
    success_count = 0
    from tqdm import tqdm

    for s2_path in tqdm(s2_files, desc="Generating contours"):
        lake_name = s2_path.stem.replace('_s2', '')

        # Get max depth constraint from Stage 1
        max_depth = stage1_predictions.get(lake_name.lower())

        result = process_lake(model, s2_path, lake_name, output_dir, max_depth, device)
        if result:
            success_count += 1

    log.info(f"\nProcessed {success_count}/{len(s2_files)} lakes successfully")

    # Merge all contours
    merged_path = merge_geojson(output_dir)

    # Generate PMTiles if requested
    if args.pmtiles:
        generate_pmtiles(merged_path, output_dir)

    log.info("Done!")


if __name__ == '__main__':
    main()
