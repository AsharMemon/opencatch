#!/usr/bin/env python3
"""
OpenCatch — Ensemble Prediction + Conformal Calibration

Combines predictions from 3 models for robust bathymetry estimation:
1. Terrain U-Net (V1) — DEM-only, works for turbid lakes
2. Multi-Modal Swin (V2) — S2 + DEM, best for clear lakes
3. Depth Anything V2 — RGB-only, generalizes well

Ensemble strategy:
- Uncertainty-weighted average (each model predicts mean + variance)
- Conformal calibration for valid 90% prediction intervals
- Automatic fallback: if one model fails, use remaining models

Output per lake:
- depth_mean.tif: ensemble mean depth
- depth_lower.tif: 90% PI lower bound
- depth_upper.tif: 90% PI upper bound
- contours.geojson: filled contour polygons (papercut blue style)
- metadata.json: model weights, uncertainty stats

Usage:
    python ensemble_predict.py \\
        --v1-model /data/models/terrain_depth/best_model.pt \\
        --v2-model /data/models/v2/best_model.pt \\
        --da-model /data/models/depth_anything/best_model.pt \\
        --lakes /data/waterbodies/us/all_us_waterbodies.geojson \\
        --s2-dir /data/training/v2 \\
        --dem-dir /data/3dep \\
        --output /data/predictions \\
        --device cuda

Requirements:
    pip install torch rasterio geopandas numpy tqdm shapely scipy
"""

import argparse
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
import torch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ensemble")

# Papercut blue palette for contour rendering
PAPERCUT_BLUES = [
    "#E8F4FD",  # 0-2 ft
    "#B8DCF0",  # 2-5 ft
    "#7BB8DE",  # 5-10 ft
    "#4A98C9",  # 10-15 ft
    "#2574A9",  # 15-25 ft
    "#1A5276",  # 25-40 ft
    "#0E3D5C",  # 40-60 ft
    "#071E2E",  # 60+ ft
]

DEPTH_BANDS_M = [0, 0.6, 1.5, 3.0, 4.6, 7.6, 12.2, 18.3, 61.0]

B2_BUCKET = "opencatch-ml"


# ── Model Loaders ────────────────────────────────────────────────────

def load_v1_model(
    model_path: Path,
    device: str = "cuda",
) -> Optional[torch.nn.Module]:
    """Load Terrain U-Net (V1) model."""
    try:
        from terrain_depth_model import TerrainDepthUNet

        model = TerrainDepthUNet(in_channels=4, base_filters=64)
        state = torch.load(model_path, map_location=device, weights_only=True)
        model.load_state_dict(state)
        model.to(device)
        model.eval()
        log.info(f"Loaded V1 Terrain U-Net from {model_path}")
        return model
    except Exception as e:
        log.warning(f"Failed to load V1 model: {e}")
        return None


def load_v2_model(
    model_path: Path,
    device: str = "cuda",
) -> Optional[torch.nn.Module]:
    """Load Multi-Modal Swin (V2) model."""
    try:
        from train_v2_multimodal import SwinBathyUNet, N_INPUT_CHANNELS

        model = SwinBathyUNet(
            in_channels=N_INPUT_CHANNELS,
            pretrained=False,
        )
        state = torch.load(model_path, map_location=device, weights_only=True)
        model.load_state_dict(state)
        model.to(device)
        model.eval()
        log.info(f"Loaded V2 Swin-BathyUNet from {model_path}")
        return model
    except Exception as e:
        log.warning(f"Failed to load V2 model: {e}")
        return None


def load_da_model(
    model_path: Path,
    device: str = "cuda",
) -> Optional[torch.nn.Module]:
    """Load Depth Anything V2 fine-tuned model."""
    try:
        from finetune_depth_anything import (
            DepthAnythingBathymetry,
            load_depth_anything_v2,
        )

        base_model, _ = load_depth_anything_v2("Small", pretrained=False)
        model = DepthAnythingBathymetry(base_model, max_depth=50.0)
        state = torch.load(model_path, map_location=device, weights_only=True)
        model.load_state_dict(state)
        model.to(device)
        model.eval()
        log.info(f"Loaded Depth Anything V2 from {model_path}")
        return model
    except Exception as e:
        log.warning(f"Failed to load DA model: {e}")
        return None


# ── Individual Model Prediction ──────────────────────────────────────

def predict_v1(
    model: torch.nn.Module,
    dem_path: Path,
    mask_path: Path,
    device: str = "cuda",
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Predict depth using V1 Terrain U-Net. Returns (mean, variance)."""
    try:
        import rasterio
        from terrain_depth_model import compute_terrain_features

        with rasterio.open(dem_path) as src:
            dem = src.read(1).astype(np.float32)
            pixel_size = abs(src.transform.a)

        with rasterio.open(mask_path) as src:
            mask = src.read(1).astype(np.float32)

        lake_elev = np.median(dem[mask > 0.5]) if (mask > 0.5).any() else dem.mean()
        features = compute_terrain_features(dem, mask, lake_elev, pixel_size)

        with torch.no_grad():
            x = torch.from_numpy(features[np.newaxis]).to(device)
            pred = model(x).cpu().numpy()[0, 0]

        pred = pred * mask
        # Estimate variance as proportional to predicted depth (heuristic)
        variance = (pred * 0.3) ** 2 + 0.5  # ~30% relative uncertainty + baseline

        return pred, variance

    except Exception as e:
        log.warning(f"V1 prediction failed: {e}")
        return None, None


def predict_v2(
    model: torch.nn.Module,
    composite_path: Path,
    device: str = "cuda",
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Predict depth using V2 Swin-BathyUNet. Returns (mean, variance)."""
    try:
        import rasterio

        with rasterio.open(composite_path) as src:
            composite = src.read().astype(np.float32)  # (14, H, W)

        with torch.no_grad():
            x = torch.from_numpy(composite[np.newaxis]).to(device)
            depth, log_var = model(x)

            depth = depth.cpu().numpy()[0, 0]
            variance = torch.exp(log_var).cpu().numpy()[0, 0]

        return depth, variance

    except Exception as e:
        log.warning(f"V2 prediction failed: {e}")
        return None, None


def predict_da(
    model: torch.nn.Module,
    composite_path: Path,
    device: str = "cuda",
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Predict depth using Depth Anything V2. Returns (mean, variance)."""
    try:
        import rasterio

        with rasterio.open(composite_path) as src:
            composite = src.read().astype(np.float32)  # (14, H, W)

        # Extract RGB (B04, B03, B02 = channels 2, 1, 0)
        rgb = np.stack([composite[2], composite[1], composite[0]], axis=0)
        rgb = np.clip(rgb, 0, 1)

        # Normalize
        mean = np.array([0.485, 0.456, 0.406])[:, None, None]
        std = np.array([0.229, 0.224, 0.225])[:, None, None]
        rgb_norm = (rgb - mean) / std

        with torch.no_grad():
            x = torch.from_numpy(rgb_norm[np.newaxis].astype(np.float32)).to(device)
            pred = model(x)

            if pred.shape[2:] != (composite.shape[1], composite.shape[2]):
                pred = torch.nn.functional.interpolate(
                    pred,
                    size=(composite.shape[1], composite.shape[2]),
                    mode="bilinear",
                    align_corners=False,
                )

            depth = pred.cpu().numpy()[0, 0]

        # DA doesn't predict uncertainty; use higher baseline variance
        variance = (depth * 0.4) ** 2 + 1.0

        return depth, variance

    except Exception as e:
        log.warning(f"DA prediction failed: {e}")
        return None, None


# ── Ensemble Combination ─────────────────────────────────────────────

def uncertainty_weighted_ensemble(
    predictions: list[tuple[np.ndarray, np.ndarray]],
    model_names: list[str],
) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    Combine multiple model predictions using uncertainty weighting.

    For each pixel, the ensemble mean is:
        mean = sum(w_i * pred_i) / sum(w_i)
    where w_i = 1 / variance_i (inverse variance weighting).

    The ensemble variance is:
        var = 1 / sum(1 / var_i)  (optimal combination)

    Args:
        predictions: List of (mean, variance) arrays
        model_names: Names for logging

    Returns:
        (ensemble_mean, ensemble_variance, weights_dict)
    """
    # Filter out None predictions
    valid = [(m, v, name) for (m, v), name in zip(predictions, model_names)
             if m is not None and v is not None]

    if not valid:
        raise ValueError("No valid model predictions available")

    if len(valid) == 1:
        m, v, name = valid[0]
        log.info(f"Single model prediction: {name}")
        return m, v, {name: 1.0}

    # Align spatial dimensions (use the largest)
    target_shape = max(m.shape for m, _, _ in valid)
    from scipy.ndimage import zoom as scipy_zoom

    aligned = []
    for m, v, name in valid:
        if m.shape != target_shape:
            zh = target_shape[0] / m.shape[0]
            zw = target_shape[1] / m.shape[1]
            m = scipy_zoom(m, (zh, zw), order=1)
            v = scipy_zoom(v, (zh, zw), order=1)
        aligned.append((m, v, name))

    # Inverse variance weighting
    total_weight = np.zeros(target_shape, dtype=np.float64)
    weighted_sum = np.zeros(target_shape, dtype=np.float64)
    model_weights = {}

    for m, v, name in aligned:
        # Clamp variance to avoid division by zero
        v_safe = np.clip(v, 0.01, None)
        w = 1.0 / v_safe

        weighted_sum += w * m
        total_weight += w

        # Average weight for this model (for logging)
        model_weights[name] = float(np.mean(w))

    # Normalize weights for logging
    total_avg = sum(model_weights.values())
    if total_avg > 0:
        model_weights = {k: v / total_avg for k, v in model_weights.items()}

    # Ensemble predictions
    total_weight = np.clip(total_weight, 1e-6, None)
    ensemble_mean = (weighted_sum / total_weight).astype(np.float32)
    ensemble_var = (1.0 / total_weight).astype(np.float32)

    # Clamp to non-negative
    ensemble_mean = np.clip(ensemble_mean, 0, None)

    log.info(f"Ensemble weights: {model_weights}")

    return ensemble_mean, ensemble_var, model_weights


# ── Conformal Prediction ─────────────────────────────────────────────

def calibrate_conformal(
    val_predictions: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    alpha: float = 0.1,
) -> float:
    """
    Calibrate conformal prediction intervals on validation data.

    Uses split conformal prediction to find the quantile scaling
    factor that achieves (1-alpha) coverage.

    Args:
        val_predictions: List of (mean, variance, true_depth) per lake
        alpha: Desired miscoverage rate (0.1 = 90% coverage)

    Returns:
        Conformal quantile scaling factor
    """
    all_scores = []

    for mean, var, truth in val_predictions:
        mask = (truth > 0) & np.isfinite(truth) & np.isfinite(mean)
        if mask.sum() == 0:
            continue

        residuals = np.abs(mean[mask] - truth[mask])
        std = np.sqrt(var[mask]).clip(min=0.01)
        scores = residuals / std
        all_scores.extend(scores.tolist())

    if not all_scores:
        log.warning("No validation data for conformal calibration, using default q=2.0")
        return 2.0

    scores = np.array(all_scores)
    # Conformal quantile
    n = len(scores)
    q_level = np.ceil((1 - alpha) * (n + 1)) / n
    q_level = min(q_level, 1.0)
    q_hat = np.quantile(scores, q_level)

    coverage = (scores <= q_hat).mean()
    log.info(
        f"Conformal calibration: q_hat={q_hat:.3f}, "
        f"coverage={coverage:.1%} (target={1 - alpha:.1%}), "
        f"n_scores={n}"
    )

    return float(q_hat)


def apply_conformal_intervals(
    mean: np.ndarray,
    var: np.ndarray,
    q_hat: float,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Apply conformal prediction intervals.

    PI = mean +/- q_hat * sqrt(variance)

    Args:
        mean: Ensemble mean depth
        var: Ensemble variance
        q_hat: Conformal quantile factor

    Returns:
        (lower, upper) prediction interval bounds
    """
    std = np.sqrt(var).clip(min=0.01)
    lower = np.clip(mean - q_hat * std, 0, None)
    upper = mean + q_hat * std

    return lower.astype(np.float32), upper.astype(np.float32)


# ── Contour Generation ──────────────────────────────────────────────

def generate_filled_contours(
    depth: np.ndarray,
    transform,
    crs,
    lake_name: str = "",
    lake_id: str = "",
) -> dict:
    """Generate papercut-style filled contour polygons."""
    from rasterio.features import shapes
    import shapely.geometry as sg
    from shapely.ops import unary_union

    features = []
    max_depth = float(depth[depth > 0].max()) if (depth > 0).any() else 0

    if max_depth <= 0:
        return {"type": "FeatureCollection", "features": []}

    bands = [d for d in DEPTH_BANDS_M if d < max_depth * 1.1]
    if len(bands) < 2:
        bands = [0, max_depth * 0.33, max_depth * 0.66, max_depth]

    for i in range(len(bands) - 1):
        min_depth = bands[i]
        color_idx = min(i, len(PAPERCUT_BLUES) - 1)

        mask = (depth >= min_depth).astype("uint8")
        if mask.sum() == 0:
            continue

        try:
            polygons = []
            for geom, val in shapes(mask, transform=transform):
                if val == 1:
                    poly = sg.shape(geom)
                    if poly.is_valid and poly.area > 1e-10:
                        poly = poly.simplify(0.0002, preserve_topology=True)
                        polygons.append(poly)

            if not polygons:
                continue

            merged = unary_union(polygons)
            geoms = merged.geoms if merged.geom_type == "MultiPolygon" else [merged]

            for geom in geoms:
                if geom.area < 1e-10:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": sg.mapping(geom),
                    "properties": {
                        "depth_min_m": round(min_depth, 1),
                        "depth_min_ft": round(min_depth * 3.28084, 1),
                        "band_index": i,
                        "color": PAPERCUT_BLUES[color_idx],
                        "lake_name": lake_name,
                        "lake_id": lake_id,
                        "source": "opencatch-v2-ensemble",
                    },
                })
        except Exception as e:
            log.warning(f"Contour generation failed at {min_depth}m: {e}")

    return {"type": "FeatureCollection", "features": features}


# ── Point-Level Ensemble (for production orchestrator) ───────────────

def point_level_ensemble(
    base_predictions: dict[str, np.ndarray],
    y_true: Optional[np.ndarray] = None,
    meta_model: Optional[object] = None,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    Combine point-level predictions from multiple models using Ridge stacking.

    This is the point-level analog of uncertainty_weighted_ensemble.
    Used by run_lake_production.py for cross-lake prediction.

    Args:
        base_predictions: Dict mapping model name -> predicted depth array
        y_true: If provided, fit the meta-learner (training mode)
        meta_model: If provided, use this pre-fitted model (inference mode)

    Returns:
        (ensemble_pred, ensemble_std, model_weights_or_meta_model)
    """
    from sklearn.linear_model import Ridge

    names = sorted(base_predictions.keys())
    # Stack predictions, replacing NaN with 0
    X_stack = np.column_stack([
        np.nan_to_num(base_predictions[name], nan=0.0) for name in names
    ])

    if y_true is not None and meta_model is None:
        # Training mode: fit Ridge meta-learner
        valid = np.isfinite(y_true)
        meta = Ridge(alpha=1.0, positive=True, fit_intercept=True)
        meta.fit(X_stack[valid], y_true[valid])
        pred = np.clip(meta.predict(X_stack), 0, 60)
        residuals = y_true[valid] - pred[valid]
        std = np.full(len(pred), float(np.std(residuals)))
        weights = {name: float(w) for name, w in zip(names, meta.coef_)}
        weights["intercept"] = float(meta.intercept_)
        log.info(f"Point-level ensemble weights: {weights}")
        return pred, std, {"meta_model": meta, "weights": weights, "feature_names": names}

    elif meta_model is not None:
        # Inference mode: use pre-fitted meta-learner
        pred = np.clip(meta_model.predict(X_stack), 0, 60)
        std = np.full(len(pred), 2.0)  # Placeholder uncertainty
        return pred, std, {}

    else:
        # Simple average fallback
        pred = np.nanmean(X_stack, axis=1)
        std = np.nanstd(X_stack, axis=1)
        return np.clip(pred, 0, 60), std, {name: 1.0 / len(names) for name in names}


# ── Main Prediction Pipeline ─────────────────────────────────────────

def predict_lake(
    lake_dir: Path,
    models: dict,
    device: str,
    q_hat: float = 2.0,
    output_dir: Optional[Path] = None,
    lake_name: str = "",
) -> Optional[dict]:
    """
    Run ensemble prediction for a single lake.

    Args:
        lake_dir: Directory containing composite.tif, depth.tif, mask.tif, dem.tif
        models: Dict of loaded models {"v1": model, "v2": model, "da": model}
        device: Compute device
        q_hat: Conformal quantile for prediction intervals
        output_dir: Optional output directory (defaults to lake_dir)
        lake_name: Lake identifier

    Returns:
        Metadata dict or None on failure
    """
    import rasterio
    from rasterio.transform import from_bounds

    if output_dir is None:
        output_dir = lake_dir

    output_dir.mkdir(parents=True, exist_ok=True)

    predictions = []
    model_names = []

    # V1: Terrain U-Net
    if "v1" in models and models["v1"] is not None:
        dem_path = lake_dir / "dem.tif"
        mask_path = lake_dir / "mask.tif"
        if dem_path.exists() and mask_path.exists():
            mean, var = predict_v1(models["v1"], dem_path, mask_path, device)
            if mean is not None:
                predictions.append((mean, var))
                model_names.append("v1_terrain")

    # V2: Swin-BathyUNet
    if "v2" in models and models["v2"] is not None:
        composite_path = lake_dir / "composite.tif"
        if composite_path.exists():
            mean, var = predict_v2(models["v2"], composite_path, device)
            if mean is not None:
                predictions.append((mean, var))
                model_names.append("v2_swin")

    # DA: Depth Anything V2
    if "da" in models and models["da"] is not None:
        composite_path = lake_dir / "composite.tif"
        if composite_path.exists():
            mean, var = predict_da(models["da"], composite_path, device)
            if mean is not None:
                predictions.append((mean, var))
                model_names.append("da_v2")

    if not predictions:
        log.warning(f"No predictions available for {lake_name}")
        return None

    # Ensemble
    try:
        ens_mean, ens_var, weights = uncertainty_weighted_ensemble(
            predictions, model_names,
        )
    except Exception as e:
        log.error(f"Ensemble failed for {lake_name}: {e}")
        return None

    # Conformal intervals
    lower, upper = apply_conformal_intervals(ens_mean, ens_var, q_hat)

    # Get geo metadata from composite or DEM
    ref_path = lake_dir / "composite.tif"
    if not ref_path.exists():
        ref_path = lake_dir / "dem.tif"
    if not ref_path.exists():
        log.warning(f"No reference raster found for {lake_name}")
        return None

    with rasterio.open(ref_path) as src:
        transform = src.transform
        crs = src.crs
        profile = src.profile.copy()

    h, w = ens_mean.shape
    profile.update(count=1, dtype="float32", compress="deflate", nodata=np.nan)

    # Ensure output dimensions match
    if (profile["height"], profile["width"]) != (h, w):
        transform = from_bounds(
            transform.c, transform.f + transform.e * profile["height"],
            transform.c + transform.a * profile["width"], transform.f,
            w, h,
        )
        profile.update(height=h, width=w, transform=transform)

    # Save rasters
    for data, suffix in [(ens_mean, "depth_mean"), (lower, "depth_lower"), (upper, "depth_upper")]:
        out_path = output_dir / f"{suffix}.tif"
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(data, 1)

    # Generate contours
    contours = generate_filled_contours(
        ens_mean, transform, crs,
        lake_name=lake_name,
    )
    if contours["features"]:
        contour_path = output_dir / "contours.geojson"
        with open(contour_path, "w") as f:
            json.dump(contours, f)

    # Metadata
    metadata = {
        "lake_name": lake_name,
        "models_used": model_names,
        "model_weights": weights,
        "n_models": len(predictions),
        "max_depth_m": float(ens_mean.max()),
        "mean_depth_m": float(ens_mean[ens_mean > 0].mean()) if (ens_mean > 0).any() else 0,
        "mean_uncertainty_m": float(np.sqrt(ens_var[ens_mean > 0]).mean()) if (ens_mean > 0).any() else 0,
        "conformal_q_hat": q_hat,
        "n_contour_features": len(contours["features"]),
        "raster_shape": list(ens_mean.shape),
    }

    with open(output_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    log.info(
        f"  {lake_name}: max={metadata['max_depth_m']:.1f}m, "
        f"mean_unc={metadata['mean_uncertainty_m']:.2f}m, "
        f"{metadata['n_contour_features']} contours"
    )

    return metadata


# ── Batch Processing ─────────────────────────────────────────────────

def predict_batch(args: argparse.Namespace) -> None:
    """Run ensemble prediction for all lakes."""
    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
        log.warning("CUDA not available, using CPU")

    # Load models
    models = {}
    if args.v1_model and Path(args.v1_model).exists():
        models["v1"] = load_v1_model(Path(args.v1_model), device)
    if args.v2_model and Path(args.v2_model).exists():
        models["v2"] = load_v2_model(Path(args.v2_model), device)
    if args.da_model and Path(args.da_model).exists():
        models["da"] = load_da_model(Path(args.da_model), device)

    n_loaded = sum(1 for v in models.values() if v is not None)
    log.info(f"Loaded {n_loaded} models for ensemble")

    if n_loaded == 0:
        log.error("No models loaded! Provide at least one model path.")
        return

    # Conformal calibration
    q_hat = args.q_hat
    log.info(f"Using conformal quantile q_hat={q_hat:.3f}")

    # Find lake directories
    data_dir = Path(args.data_dir)
    lake_dirs = sorted([d for d in data_dir.iterdir() if d.is_dir()])
    if args.max_lakes:
        lake_dirs = lake_dirs[: args.max_lakes]

    log.info(f"Processing {len(lake_dirs)} lakes...")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    from tqdm import tqdm
    results = []

    for lake_dir in tqdm(lake_dirs, desc="Ensemble predictions"):
        lake_name = lake_dir.name
        lake_output = output_dir / lake_name

        metadata = predict_lake(
            lake_dir=lake_dir,
            models=models,
            device=device,
            q_hat=q_hat,
            output_dir=lake_output,
            lake_name=lake_name,
        )

        if metadata:
            results.append(metadata)

    # Summary
    log.info(f"\nProcessed {len(results)}/{len(lake_dirs)} lakes successfully")

    # Save summary
    summary_path = output_dir / "ensemble_summary.json"
    summary = {
        "n_lakes": len(results),
        "models_loaded": [k for k, v in models.items() if v is not None],
        "conformal_q_hat": q_hat,
        "lakes": results,
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    log.info(f"Summary saved to {summary_path}")

    # Merge all contours
    _merge_contours(output_dir)

    # Sync to B2
    if args.b2_sync:
        _sync_to_b2(output_dir)


def _merge_contours(output_dir: Path) -> Optional[Path]:
    """Merge all per-lake contour GeoJSON files."""
    all_features = []

    for geojson_path in sorted(output_dir.rglob("contours.geojson")):
        try:
            with open(geojson_path) as f:
                data = json.load(f)
            all_features.extend(data.get("features", []))
        except Exception:
            continue

    if not all_features:
        return None

    merged = {"type": "FeatureCollection", "features": all_features}
    merged_path = output_dir / "all_contours.geojson"
    with open(merged_path, "w") as f:
        json.dump(merged, f)

    log.info(f"Merged {len(all_features)} contour features -> {merged_path}")

    # Generate PMTiles if tippecanoe is available
    try:
        pmtiles_path = output_dir / "contours.pmtiles"
        cmd = [
            "tippecanoe",
            "-o", str(pmtiles_path),
            "--force",
            "--name", "OpenCatch Bathymetry V2",
            "--description", "Ensemble satellite-derived depth contours",
            "--minimum-zoom", "8",
            "--maximum-zoom", "14",
            "--coalesce-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            "--layer", "contours",
            str(merged_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0:
            size_mb = pmtiles_path.stat().st_size / 1024 / 1024
            log.info(f"PMTiles created: {pmtiles_path} ({size_mb:.1f} MB)")
        else:
            log.warning(f"tippecanoe failed: {result.stderr[:200]}")
    except FileNotFoundError:
        log.info("tippecanoe not installed, skipping PMTiles generation")
    except Exception as e:
        log.warning(f"PMTiles generation failed: {e}")

    return merged_path


def _sync_to_b2(output_dir: Path) -> None:
    """Sync predictions to B2."""
    try:
        cmd = ["b2", "sync", str(output_dir), f"b2://{B2_BUCKET}/predictions/v2/"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            log.info("Synced predictions to B2")
        else:
            log.warning(f"B2 sync failed: {result.stderr[:200]}")
    except Exception as e:
        log.warning(f"B2 sync error: {e}")


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Ensemble bathymetry prediction with conformal intervals",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Models
    parser.add_argument("--v1-model", type=str, default=None,
                        help="Path to V1 Terrain U-Net model")
    parser.add_argument("--v2-model", type=str, default=None,
                        help="Path to V2 Swin-BathyUNet model")
    parser.add_argument("--da-model", type=str, default=None,
                        help="Path to Depth Anything V2 model")

    # Data
    parser.add_argument("--data-dir", type=str, required=True,
                        help="Directory containing per-lake data subdirectories")
    parser.add_argument("--output", type=str, default="/data/predictions/v2",
                        help="Output directory")
    parser.add_argument("--max-lakes", type=int, default=None,
                        help="Limit number of lakes")

    # Conformal
    parser.add_argument("--q-hat", type=float, default=2.0,
                        help="Conformal quantile factor (calibrate on val set first)")

    # Other
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--b2-sync", action="store_true", default=False,
                        help="Sync results to Backblaze B2")

    args = parser.parse_args()
    predict_batch(args)


if __name__ == "__main__":
    main()
