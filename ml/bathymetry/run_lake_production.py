#!/usr/bin/env python3
"""
OpenCatch — Lake Production Pipeline
=====================================
Unified production orchestrator that combines all lake bathymetry models into
a single ensemble prediction per lake.

Stages:
  1. Load lake polygons (NHDPlus / 3D-LAKES) and sonar training data
  2. Stage-1 morphometric max depth prediction for ALL lakes
  3. Hybrid sonar model — point-level spectral + sonar predictions
  4. Terrain depth model — turbidity-independent DEM-based prediction
  5. K-donor transfer learning for unsurveyed lakes
  6. Ridge meta-learner stacking of all base model predictions
  7. Depth compression calibration (isotonic by clarity + depth bin)
  8. Contour generation via generate_contours
  9. B2 backup of all outputs

Usage:
    python run_lake_production.py \\
        --data /data/sonar_s2/sonar_s2_split.parquet \\
        --models /data/models/hybrid_sonar \\
        --dem-dir /data/3dep \\
        --output /data/predictions/lakes \\
        --device cuda
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import math
import pickle
import time
import traceback
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from tqdm import tqdm

# ── Local modules ────────────────────────────────────────────────────
from stage1_max_depth import compute_morphometric_features, predict_all_lakes
from train_hybrid_sonar import (
    compute_metrics,
    compute_depth_bins,
    apply_isotonic_by_clarity,
    apply_isotonic_by_depth_bin,
)
from generate_contours import process_lake, merge_geojson
from b2_backup import backup as b2_backup_fn

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("lake_production")

EPS = 1e-6

DEPTH_BINS = [
    (0, 2, "0-2m"),
    (2, 5, "2-5m"),
    (5, 10, "5-10m"),
    (10, 20, "10-20m"),
    (20, 50, "20-50m"),
]

# Columns produced by each base model — used as Ridge meta-learner inputs
BASE_MODEL_COLS = [
    "pred_stage1_max",
    "pred_hybrid_sonar",
    "pred_terrain",
    "pred_kdonor",
]


# ═══════════════════════════════════════════════════════════════════════
# 1. Data Loading
# ═══════════════════════════════════════════════════════════════════════

def load_lake_data(
    data_path: Path,
    lakes_geojson: Optional[Path] = None,
) -> pd.DataFrame:
    """Load sonar training data and optionally filter to a lake subset."""
    log.info(f"Loading sonar data from {data_path}")
    df = pd.read_parquet(data_path)
    log.info(f"  {len(df):,} points across {df['lake_id'].nunique()} lakes")

    if lakes_geojson is not None:
        import geopandas as gpd
        polys = gpd.read_file(lakes_geojson)
        keep_ids = set(polys["lake_id"].astype(str))
        df = df[df["lake_id"].astype(str).isin(keep_ids)].copy()
        log.info(f"  Filtered to {len(df):,} points in {df['lake_id'].nunique()} lakes")

    return df


# ═══════════════════════════════════════════════════════════════════════
# 2. Stage-1: Morphometric Max Depth
# ═══════════════════════════════════════════════════════════════════════

def run_stage1(
    df: pd.DataFrame,
    models_dir: Path,
) -> pd.DataFrame:
    """Predict morphometric max depth prior for every lake."""
    log.info("Stage 1: Morphometric max depth prediction")

    model_paths = sorted(models_dir.glob("lgb_fold*.txt"))
    if not model_paths:
        model_paths = sorted(models_dir.glob("stage1_*.pkl"))

    if not model_paths:
        log.warning("  No Stage-1 models found — filling pred_stage1_max with NaN")
        df["pred_stage1_max"] = np.nan
        return df

    import lightgbm as lgb

    models = []
    for p in model_paths:
        if p.suffix == ".txt":
            models.append(lgb.Booster(model_file=str(p)))
        else:
            with open(p, "rb") as f:
                models.append(pickle.load(f))

    log.info(f"  Loaded {len(models)} Stage-1 fold models")

    lake_df = df.groupby("lake_id", sort=False).first().reset_index()
    morpho = compute_morphometric_features(lake_df)
    morpho = morpho.fillna(morpho.median())

    preds_log = np.mean([m.predict(morpho) for m in models], axis=0)
    lake_df["pred_stage1_max"] = np.expm1(preds_log)

    lookup = lake_df.set_index("lake_id")["pred_stage1_max"]
    df["pred_stage1_max"] = df["lake_id"].map(lookup).astype(np.float32)

    valid = df["pred_stage1_max"].notna().sum()
    log.info(f"  Stage-1 predictions assigned to {valid:,} / {len(df):,} points")
    return df


# ═══════════════════════════════════════════════════════════════════════
# 3. Hybrid Sonar Model
# ═══════════════════════════════════════════════════════════════════════

def run_hybrid_sonar(
    df: pd.DataFrame,
    models_dir: Path,
) -> pd.DataFrame:
    """Load trained hybrid sonar model and predict point-level depths."""
    log.info("Stage 2: Hybrid sonar model prediction")

    model_path = models_dir / "hybrid_sonar_final.pkl"
    if not model_path.exists():
        model_path = models_dir / "hybrid_sonar_hgb.pkl"

    if not model_path.exists():
        log.warning("  No hybrid sonar model found — filling pred_hybrid_sonar with NaN")
        df["pred_hybrid_sonar"] = np.nan
        return df

    with open(model_path, "rb") as f:
        model = pickle.load(f)

    feature_path = models_dir / "feature_columns.json"
    if feature_path.exists():
        with open(feature_path) as f:
            feature_cols = json.load(f)
    else:
        feature_cols = [c for c in df.columns if c not in {
            "depth_m", "lake_id", "split", "clarity_class",
        } and pd.api.types.is_numeric_dtype(df[c])]

    avail = [c for c in feature_cols if c in df.columns]
    X = df[avail].copy()

    df["pred_hybrid_sonar"] = model.predict(X).astype(np.float32)
    log.info(f"  Hybrid sonar predictions: "
             f"mean={df['pred_hybrid_sonar'].mean():.2f}m, "
             f"std={df['pred_hybrid_sonar'].std():.2f}m")
    return df


# ═══════════════════════════════════════════════════════════════════════
# 4. Terrain Depth Model
# ═══════════════════════════════════════════════════════════════════════

def run_terrain_model(
    df: pd.DataFrame,
    models_dir: Path,
    dem_dir: Path,
    device: str = "cuda",
) -> pd.DataFrame:
    """Predict depth from surrounding terrain DEM (turbidity-independent)."""
    log.info("Stage 3: Terrain depth model prediction")

    model_path = models_dir / "terrain_depth" / "best_model.pt"
    if not model_path.exists():
        log.warning("  No terrain model found — filling pred_terrain with NaN")
        df["pred_terrain"] = np.nan
        return df

    import torch
    from terrain_depth_model import TerrainDepthUNet, predict_lake

    model = TerrainDepthUNet()
    state = torch.load(model_path, map_location=device, weights_only=True)
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    log.info(f"  Loaded terrain model from {model_path}")

    lake_preds: dict[str, np.ndarray] = {}
    lake_ids = df["lake_id"].unique()

    for lid in tqdm(lake_ids, desc="  Terrain depth"):
        dem_path = dem_dir / f"{lid}_dem.tif"
        mask_path = dem_dir / f"{lid}_mask.tif"
        if not dem_path.exists() or not mask_path.exists():
            continue
        try:
            out_path = Path(f"/tmp/terrain_{lid}.tif")
            max_d = float(df.loc[df["lake_id"] == lid, "pred_stage1_max"].iloc[0])
            depth = predict_lake(model, dem_path, mask_path, out_path,
                                 device=device, max_depth=max_d if np.isfinite(max_d) else None)
            lake_preds[lid] = float(np.mean(depth[depth > 0])) if (depth > 0).any() else np.nan
        except Exception as exc:
            log.debug(f"  Terrain failed for {lid}: {exc}")

    df["pred_terrain"] = df["lake_id"].map(lake_preds).astype(np.float32)
    n_ok = df["pred_terrain"].notna().sum()
    log.info(f"  Terrain predictions for {n_ok:,} / {len(df):,} points")
    return df


# ═══════════════════════════════════════════════════════════════════════
# 5. K-Donor Transfer Learning
# ═══════════════════════════════════════════════════════════════════════

def run_kdonor(
    df: pd.DataFrame,
    models_dir: Path,
) -> pd.DataFrame:
    """K-donor transfer predictions for unsurveyed lakes."""
    log.info("Stage 4: K-donor transfer learning")

    kdonor_path = models_dir / "transfer_bathy" / "kdonor_results.parquet"
    if not kdonor_path.exists():
        log.warning("  No K-donor results found — filling pred_kdonor with NaN")
        df["pred_kdonor"] = np.nan
        return df

    kd = pd.read_parquet(kdonor_path)
    if "lake_id" in kd.columns and "predicted_depth_m" in kd.columns:
        lookup = kd.set_index("lake_id")["predicted_depth_m"]
        df["pred_kdonor"] = df["lake_id"].map(lookup).astype(np.float32)
    else:
        log.warning("  K-donor parquet missing expected columns")
        df["pred_kdonor"] = np.nan

    n_ok = df["pred_kdonor"].notna().sum()
    log.info(f"  K-donor predictions for {n_ok:,} / {len(df):,} points")
    return df


# ═══════════════════════════════════════════════════════════════════════
# 6. Ridge Meta-Learner Stacking
# ═══════════════════════════════════════════════════════════════════════

def fit_meta_learner(
    df: pd.DataFrame,
    target_col: str = "depth_m",
) -> Ridge:
    """Fit a Ridge meta-learner on base model predictions (train split only)."""
    log.info("Stage 5: Fitting Ridge meta-learner")

    train = df[df["split"] == "train"].copy() if "split" in df.columns else df.copy()

    avail_cols = [c for c in BASE_MODEL_COLS if c in train.columns and train[c].notna().any()]
    if not avail_cols:
        log.error("  No base model predictions available — cannot stack")
        return None

    mask = train[target_col].notna()
    for c in avail_cols:
        mask &= train[c].notna()
    train = train[mask]

    X = train[avail_cols].values
    y = train[target_col].values

    meta = Ridge(alpha=1.0, positive=True, fit_intercept=True)
    meta.fit(X, y)

    preds = meta.predict(X)
    m = compute_metrics(y, preds)
    log.info(f"  Meta-learner train — R²={m['r2']:.3f}, RMSE={m['rmse']:.2f}m, "
             f"MAE={m['mae']:.2f}m (n={m['n']:,})")
    log.info(f"  Feature weights: {dict(zip(avail_cols, [f'{w:.3f}' for w in meta.coef_]))}")

    meta._feature_cols = avail_cols  # stash for inference
    return meta


def apply_meta_learner(
    df: pd.DataFrame,
    meta: Ridge,
) -> pd.DataFrame:
    """Apply trained meta-learner to produce stacked predictions."""
    cols = meta._feature_cols
    X = df[cols].copy()

    # Fill missing base predictions with column median so Ridge can run
    for c in cols:
        med = X[c].median()
        X[c] = X[c].fillna(med if np.isfinite(med) else 0.0)

    df["pred_stacked"] = meta.predict(X.values).astype(np.float32)
    df["pred_stacked"] = df["pred_stacked"].clip(lower=0.0)
    log.info(f"  Stacked predictions: mean={df['pred_stacked'].mean():.2f}m, "
             f"max={df['pred_stacked'].max():.1f}m")
    return df


# ═══════════════════════════════════════════════════════════════════════
# 7. Depth Compression Calibration
# ═══════════════════════════════════════════════════════════════════════

def calibrate_predictions(
    df: pd.DataFrame,
    models_dir: Path,
) -> pd.DataFrame:
    """Apply isotonic calibration by clarity class and depth bin."""
    log.info("Stage 6: Depth compression calibration")

    pred = df["pred_stacked"].values

    # Clarity-based isotonic
    clarity_path = models_dir / "isotonic_by_clarity.pkl"
    if clarity_path.exists() and "clarity_class" in df.columns:
        with open(clarity_path, "rb") as f:
            clarity_models = pickle.load(f)
        pred = apply_isotonic_by_clarity(df, pred, clarity_models)
        log.info("  Applied isotonic calibration by clarity class")

    # Depth-bin isotonic
    depthbin_path = models_dir / "isotonic_by_depth_bin.pkl"
    if depthbin_path.exists():
        with open(depthbin_path, "rb") as f:
            depthbin_models = pickle.load(f)
        pred = apply_isotonic_by_depth_bin(pred, depthbin_models)
        log.info("  Applied isotonic calibration by depth bin")

    df["pred_final"] = np.clip(pred, 0.0, None).astype(np.float32)

    if "depth_m" in df.columns:
        mask = df["depth_m"].notna() & df["pred_final"].notna()
        m = compute_metrics(df.loc[mask, "depth_m"].values, df.loc[mask, "pred_final"].values)
        log.info(f"  Post-calibration — R²={m['r2']:.3f}, RMSE={m['rmse']:.2f}m, "
                 f"bias={m['bias']:+.2f}m")
    return df


# ═══════════════════════════════════════════════════════════════════════
# 8. Evaluation & Summary
# ═══════════════════════════════════════════════════════════════════════

def compute_summary(
    df: pd.DataFrame,
    output_dir: Path,
) -> dict:
    """Compute and save comprehensive evaluation metrics."""
    log.info("Computing summary metrics")
    results: dict = {}

    if "depth_m" not in df.columns:
        log.warning("  No ground truth depth_m column — skipping evaluation")
        return results

    target = df["depth_m"].values
    final = df["pred_final"].values

    # Overall
    results["overall"] = compute_metrics(target, final)
    log.info(f"  Overall — R²={results['overall']['r2']:.3f}, "
             f"RMSE={results['overall']['rmse']:.2f}m, "
             f"MAE={results['overall']['mae']:.2f}m (n={results['overall']['n']:,})")

    # Depth-binned
    results["depth_bins"] = compute_depth_bins(target, final)
    for label, m in results["depth_bins"].items():
        if "r2" in m:
            log.info(f"  {label:>8s} — R²={m['r2']:.3f}, RMSE={m['rmse']:.2f}m (n={m['n']:,})")

    # Per-clarity class
    if "clarity_class" in df.columns:
        results["by_clarity"] = {}
        for cls in sorted(df["clarity_class"].dropna().unique()):
            mask = df["clarity_class"] == cls
            m = compute_metrics(target[mask], final[mask])
            results["by_clarity"][cls] = m
            if "r2" in m:
                log.info(f"  clarity={cls:>10s} — R²={m['r2']:.3f}, RMSE={m['rmse']:.2f}m "
                         f"(n={m['n']:,})")

    # Per split
    if "split" in df.columns:
        results["by_split"] = {}
        for split in df["split"].unique():
            mask = df["split"] == split
            m = compute_metrics(target[mask], final[mask])
            results["by_split"][split] = m
            log.info(f"  split={split:>6s} — R²={m['r2']:.3f}, RMSE={m['rmse']:.2f}m "
                     f"(n={m['n']:,})")

    # Per base model comparison
    results["base_models"] = {}
    for col in BASE_MODEL_COLS + ["pred_stacked", "pred_final"]:
        if col in df.columns:
            valid = df["depth_m"].notna() & df[col].notna()
            m = compute_metrics(target[valid], df.loc[valid, col].values)
            results["base_models"][col] = m

    # Save JSON
    metrics_path = output_dir / "production_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(results, f, indent=2, default=float)
    log.info(f"  Metrics saved to {metrics_path}")

    return results


# ═══════════════════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenCatch — Lake Production Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data", type=str, required=True,
                        help="Path to sonar_s2_split.parquet")
    parser.add_argument("--models", type=str, default="/data/models/hybrid_sonar",
                        help="Directory with trained model artifacts")
    parser.add_argument("--dem-dir", type=str, default="/data/3dep",
                        help="Directory with per-lake DEM and mask TIFs")
    parser.add_argument("--output", type=str, default="/data/predictions/lakes",
                        help="Output directory for predictions and metrics")
    parser.add_argument("--device", type=str, default="cuda", choices=["cuda", "cpu"],
                        help="Device for neural network inference")
    parser.add_argument("--lakes", type=str, default=None,
                        help="Optional GeoJSON to filter to specific lakes")
    parser.add_argument("--skip-contours", action="store_true",
                        help="Skip contour generation step")
    parser.add_argument("--skip-backup", action="store_true",
                        help="Skip B2 backup step")
    args = parser.parse_args()

    t0 = time.time()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    models_dir = Path(args.models)
    dem_dir = Path(args.dem_dir)

    log.info("=" * 70)
    log.info("OpenCatch — Lake Production Pipeline")
    log.info("=" * 70)

    # ── 1. Load data ─────────────────────────────────────────────────
    lakes_filter = Path(args.lakes) if args.lakes else None
    df = load_lake_data(Path(args.data), lakes_filter)
    n_lakes = df["lake_id"].nunique()
    log.info(f"Pipeline starting for {n_lakes} lakes, {len(df):,} points")

    failed_lakes: list[str] = []

    # ── 2. Stage-1: Max depth ────────────────────────────────────────
    try:
        df = run_stage1(df, models_dir)
    except Exception as exc:
        log.error(f"Stage-1 failed: {exc}\n{traceback.format_exc()}")

    # ── 3. Hybrid sonar ──────────────────────────────────────────────
    try:
        df = run_hybrid_sonar(df, models_dir)
    except Exception as exc:
        log.error(f"Hybrid sonar failed: {exc}\n{traceback.format_exc()}")

    # ── 4. Terrain depth ─────────────────────────────────────────────
    try:
        df = run_terrain_model(df, models_dir, dem_dir, device=args.device)
    except Exception as exc:
        log.error(f"Terrain model failed: {exc}\n{traceback.format_exc()}")

    # ── 5. K-donor transfer ──────────────────────────────────────────
    try:
        df = run_kdonor(df, models_dir)
    except Exception as exc:
        log.error(f"K-donor transfer failed: {exc}\n{traceback.format_exc()}")

    gc.collect()

    # ── 6. Ridge meta-learner ────────────────────────────────────────
    meta = fit_meta_learner(df)
    if meta is not None:
        df = apply_meta_learner(df, meta)
        meta_path = output_dir / "ridge_meta_learner.pkl"
        with open(meta_path, "wb") as f:
            pickle.dump(meta, f)
        log.info(f"  Meta-learner saved to {meta_path}")
    else:
        # Fallback: use best available single model
        for fallback_col in ["pred_hybrid_sonar", "pred_terrain", "pred_stage1_max", "pred_kdonor"]:
            if fallback_col in df.columns and df[fallback_col].notna().any():
                df["pred_stacked"] = df[fallback_col]
                log.info(f"  Fallback: using {fallback_col} as stacked prediction")
                break

    # ── 7. Depth compression calibration ─────────────────────────────
    if "pred_stacked" in df.columns:
        df = calibrate_predictions(df, models_dir)
    else:
        log.warning("No stacked predictions available — skipping calibration")

    # ── 8. Save predictions ──────────────────────────────────────────
    pred_path = output_dir / "lake_predictions.parquet"
    df.to_parquet(pred_path, index=False)
    log.info(f"Predictions saved to {pred_path} ({len(df):,} rows)")

    # ── 9. Summary metrics ───────────────────────────────────────────
    results = compute_summary(df, output_dir)

    # ── 10. Contour generation ───────────────────────────────────────
    if not args.skip_contours:
        log.info("Stage 8: Contour generation")
        contour_dir = output_dir / "contours"
        contour_dir.mkdir(parents=True, exist_ok=True)

        try:
            from generate_contours import load_unet_model
            unet_path = models_dir / "stage2" / "best_model.pt"
            if unet_path.exists():
                unet = load_unet_model(unet_path, device=args.device)
                lake_ids = df["lake_id"].unique()
                n_contoured = 0
                for lid in tqdm(lake_ids, desc="  Contours"):
                    s2_path = Path(f"/data/sentinel2/{lid}_s2.tif")
                    if not s2_path.exists():
                        continue
                    try:
                        max_d = float(df.loc[df["lake_id"] == lid, "pred_final"].iloc[0])
                        result = process_lake(unet, s2_path, str(lid),
                                              contour_dir, max_depth=max_d,
                                              device=args.device)
                        if result is not None:
                            n_contoured += 1
                    except Exception as exc:
                        failed_lakes.append(str(lid))
                        log.debug(f"  Contour failed for {lid}: {exc}")

                log.info(f"  Contours generated for {n_contoured} / {len(lake_ids)} lakes")

                # Merge into single GeoJSON
                merged = merge_geojson(contour_dir)
                log.info(f"  Merged contours: {merged}")
            else:
                log.warning(f"  U-Net model not found at {unet_path} — skipping contours")
        except Exception as exc:
            log.error(f"Contour generation failed: {exc}\n{traceback.format_exc()}")
    else:
        log.info("Skipping contour generation (--skip-contours)")

    # ── 11. B2 backup ────────────────────────────────────────────────
    if not args.skip_backup:
        log.info("Stage 9: B2 backup")
        try:
            b2_backup_fn()
        except Exception as exc:
            log.error(f"B2 backup failed: {exc}")
    else:
        log.info("Skipping B2 backup (--skip-backup)")

    # ── Done ─────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    log.info("=" * 70)
    log.info(f"Pipeline complete in {elapsed / 60:.1f} minutes")
    log.info(f"  Lakes processed: {n_lakes}")
    log.info(f"  Points predicted: {len(df):,}")
    if results.get("overall"):
        log.info(f"  Final R²={results['overall']['r2']:.3f}, "
                 f"RMSE={results['overall']['rmse']:.2f}m")
    if failed_lakes:
        log.warning(f"  Failed lakes ({len(failed_lakes)}): {failed_lakes[:10]}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
