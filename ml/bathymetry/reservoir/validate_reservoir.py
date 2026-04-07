#!/usr/bin/env python3
"""
OpenCatch — Reservoir Validation Against USBR Surveys
======================================================
Compares predicted A-E curves and storage volumes against
published USBR survey ground truth.

Metrics:
  - Area RMSE at matched elevations
  - Volume RMSE and relative volume error (%)
  - Depth-stratified metrics (0-10m, 10-30m, 30-60m, 60m+)
  - Benchmarks against 3D-LAKES (RMSE=1.37m), GRDL (MAE=7.87m), GLOBathy (RMSE=1.37m)
  - Ablation: terrain-only vs A-E-only vs ensemble
  - Per-reservoir summary table

Usage:
    python validate_reservoir.py \
        --predicted /data/models/reservoir_depth/predicted_ae_curves.parquet \
        --usbr /data/usbr_surveys/ \
        --output /data/validation/reservoir \
        --plots
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("validate_reservoir")

EPS = 1e-8

# Depth-stratified bins for reservoir analysis
DEPTH_BINS = {
    "0-10m":  (0, 10),
    "10-30m": (10, 30),
    "30-60m": (30, 60),
    "60m+":   (60, 9999),
}

# Published benchmarks for context
BENCHMARKS = {
    "3D-LAKES":  {"rmse_m": 1.37, "source": "Khazaei et al. 2022"},
    "GRDL":      {"mae_m": 7.87, "source": "Li et al. 2022"},
    "GLOBathy":  {"rmse_m": 1.37, "source": "Khazaei et al. 2022"},
}


# ══════════════════════════════════════════════════════════════════════
# Metrics
# ══════════════════════════════════════════════════════════════════════

def compute_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    label: str = "",
) -> dict:
    """Compute standard regression metrics."""
    valid = np.isfinite(y_true) & np.isfinite(y_pred)
    if valid.sum() < 2:
        return {"label": label, "n": 0, "error": "too_few_valid"}

    t = y_true[valid]
    p = y_pred[valid]

    return {
        "label": label,
        "n": int(valid.sum()),
        "rmse": float(np.sqrt(mean_squared_error(t, p))),
        "mae": float(mean_absolute_error(t, p)),
        "r2": float(r2_score(t, p)),
        "bias": float(np.mean(p - t)),
        "std_error": float(np.std(p - t)),
        "median_abs_error": float(np.median(np.abs(p - t))),
        "p90_error": float(np.percentile(np.abs(p - t), 90)),
    }


def compute_depth_stratified(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    label: str = "",
) -> dict:
    """Compute metrics per depth bin."""
    result = {"label": label}
    for bin_name, (lo, hi) in DEPTH_BINS.items():
        mask = (y_true >= lo) & (y_true < hi) & np.isfinite(y_pred)
        if mask.sum() >= 2:
            result[bin_name] = compute_metrics(y_true[mask], y_pred[mask], bin_name)
        else:
            result[bin_name] = {"n": int(mask.sum()), "error": "too_few"}
    return result


def compute_relative_volume_error(
    vol_true: np.ndarray,
    vol_pred: np.ndarray,
) -> float:
    """Relative volume error (%) at total capacity."""
    valid = np.isfinite(vol_true) & np.isfinite(vol_pred) & (vol_true > EPS)
    if valid.sum() == 0:
        return np.nan
    return float(
        np.mean(np.abs(vol_pred[valid] - vol_true[valid]) / vol_true[valid]) * 100.0
    )


def compute_skill_score(model_rmse: float, baseline_rmse: float) -> float:
    """Skill score: 1 - (model_RMSE / baseline_RMSE)^2."""
    if baseline_rmse <= 0:
        return 0.0
    return 1.0 - (model_rmse / baseline_rmse) ** 2


# ══════════════════════════════════════════════════════════════════════
# Data Loading
# ══════════════════════════════════════════════════════════════════════

def load_predicted_ae(predicted_path: Path) -> pd.DataFrame:
    """Load predicted A-E curves.

    Expected columns: reservoir_id, elevation_m, area_km2, volume_m3,
    max_depth_m, ae_shape_n, source (terrain_only | ae_only | ensemble).
    """
    if predicted_path.suffix == ".parquet":
        df = pd.read_parquet(predicted_path)
    else:
        df = pd.read_csv(predicted_path)
    log.info(f"Loaded predicted A-E: {len(df)} rows, "
             f"{df['reservoir_id'].nunique() if 'reservoir_id' in df.columns else '?'} reservoirs")
    return df


def load_usbr_surveys(usbr_dir: Path) -> Dict[str, pd.DataFrame]:
    """Load USBR survey A-E tables (one file per reservoir).

    Each file expected to have: elevation_m (or elevation_ft),
    area_acres (or area_km2), volume_acre_ft (or volume_m3).
    """
    surveys = {}
    files = sorted(usbr_dir.glob("*.csv")) + sorted(usbr_dir.glob("*.parquet"))

    for fpath in files:
        try:
            res_id = fpath.stem
            if fpath.suffix == ".parquet":
                df = pd.read_parquet(fpath)
            else:
                df = pd.read_csv(fpath)

            # Standardize columns
            if "elevation_ft" in df.columns and "elevation_m" not in df.columns:
                df["elevation_m"] = df["elevation_ft"] * 0.3048
            if "area_acres" in df.columns and "area_km2" not in df.columns:
                df["area_km2"] = df["area_acres"] * 0.00404686
            if "volume_acre_ft" in df.columns and "volume_m3" not in df.columns:
                df["volume_m3"] = df["volume_acre_ft"] * 1233.48

            required = ["elevation_m"]
            if not all(c in df.columns for c in required):
                continue

            surveys[res_id] = df
        except Exception as e:
            log.warning(f"Failed loading USBR survey {fpath.name}: {e}")

    log.info(f"Loaded {len(surveys)} USBR survey tables")
    return surveys


# ══════════════════════════════════════════════════════════════════════
# Per-Reservoir Comparison
# ══════════════════════════════════════════════════════════════════════

def compare_ae_curves(
    pred_df: pd.DataFrame,
    usbr_df: pd.DataFrame,
    reservoir_id: str,
) -> dict:
    """Compare predicted vs USBR survey at matched elevations."""
    result = {"reservoir_id": reservoir_id}

    # Match on elevation
    pred_elev = pred_df["elevation_m"].values
    usbr_elev = usbr_df["elevation_m"].values

    # Interpolate predictions to USBR elevations
    if "area_km2" in pred_df.columns and "area_km2" in usbr_df.columns:
        pred_area = np.interp(usbr_elev, pred_elev, pred_df["area_km2"].values)
        true_area = usbr_df["area_km2"].values
        valid = np.isfinite(pred_area) & np.isfinite(true_area)
        if valid.sum() >= 2:
            result["area_rmse_km2"] = float(np.sqrt(
                mean_squared_error(true_area[valid], pred_area[valid])
            ))
            result["area_mae_km2"] = float(
                mean_absolute_error(true_area[valid], pred_area[valid])
            )
            result["area_r2"] = float(r2_score(true_area[valid], pred_area[valid]))

    if "volume_m3" in pred_df.columns and "volume_m3" in usbr_df.columns:
        pred_vol = np.interp(usbr_elev, pred_elev, pred_df["volume_m3"].values)
        true_vol = usbr_df["volume_m3"].values
        valid = np.isfinite(pred_vol) & np.isfinite(true_vol)
        if valid.sum() >= 2:
            result["volume_rmse_m3"] = float(np.sqrt(
                mean_squared_error(true_vol[valid], pred_vol[valid])
            ))
            # Relative volume error at total capacity
            max_true = true_vol[valid].max()
            max_pred = pred_vol[valid].max()
            if max_true > EPS:
                result["relative_volume_error_pct"] = float(
                    abs(max_pred - max_true) / max_true * 100.0
                )

    # Max depth comparison
    if "max_depth_m" in pred_df.columns:
        pred_max_depth = pred_df["max_depth_m"].iloc[0] if len(pred_df) > 0 else np.nan
        # USBR max depth = max elevation - min elevation
        usbr_max_depth = usbr_elev.max() - usbr_elev.min()
        if np.isfinite(pred_max_depth) and usbr_max_depth > 0:
            result["pred_max_depth_m"] = float(pred_max_depth)
            result["usbr_max_depth_m"] = float(usbr_max_depth)
            result["depth_error_m"] = float(pred_max_depth - usbr_max_depth)
            result["depth_abs_error_m"] = float(abs(pred_max_depth - usbr_max_depth))

    return result


# ══════════════════════════════════════════════════════════════════════
# Ablation Study
# ══════════════════════════════════════════════════════════════════════

def run_ablation(
    pred_df: pd.DataFrame,
    usbr_surveys: Dict[str, pd.DataFrame],
) -> Dict[str, dict]:
    """Compare terrain-only vs A-E-only vs ensemble predictions."""
    ablation_results = {}

    if "source" not in pred_df.columns:
        log.info("No 'source' column in predictions, skipping ablation")
        return ablation_results

    for source_name in ["terrain_only", "ae_only", "ensemble"]:
        subset = pred_df[pred_df["source"] == source_name]
        if len(subset) == 0:
            continue

        all_true_depths = []
        all_pred_depths = []

        for res_id, usbr_df in usbr_surveys.items():
            res_pred = subset[subset["reservoir_id"] == res_id]
            if len(res_pred) == 0:
                continue

            if "max_depth_m" in res_pred.columns:
                pred_d = res_pred["max_depth_m"].iloc[0]
                true_d = usbr_df["elevation_m"].max() - usbr_df["elevation_m"].min()
                if np.isfinite(pred_d) and true_d > 0:
                    all_true_depths.append(true_d)
                    all_pred_depths.append(pred_d)

        if len(all_true_depths) >= 2:
            ablation_results[source_name] = compute_metrics(
                np.array(all_true_depths),
                np.array(all_pred_depths),
                label=source_name,
            )

    return ablation_results


# ══════════════════════════════════════════════════════════════════════
# Plots (optional)
# ══════════════════════════════════════════════════════════════════════

def generate_plots(
    per_reservoir: List[dict],
    depth_strat: dict,
    ablation: dict,
    output_dir: Path,
):
    """Generate validation plots. Gracefully degrades if matplotlib unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        log.info("matplotlib not available, skipping plots")
        return

    try:
        import seaborn as sns
        sns.set_style("whitegrid")
    except ImportError:
        pass

    # -- 1. Predicted vs True max depth scatter --
    true_d = [r["usbr_max_depth_m"] for r in per_reservoir if "usbr_max_depth_m" in r]
    pred_d = [r["pred_max_depth_m"] for r in per_reservoir if "pred_max_depth_m" in r]

    if true_d and pred_d:
        fig, ax = plt.subplots(figsize=(8, 8))
        ax.scatter(true_d, pred_d, alpha=0.7, edgecolors="k", linewidth=0.5, s=60)
        lims = [0, max(max(true_d), max(pred_d)) * 1.1]
        ax.plot(lims, lims, "k--", alpha=0.5, label="1:1 line")
        ax.set_xlabel("USBR Survey Max Depth (m)")
        ax.set_ylabel("Predicted Max Depth (m)")
        ax.set_title("Reservoir Max Depth: Predicted vs Survey")
        ax.set_xlim(lims)
        ax.set_ylim(lims)
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(output_dir / "depth_scatter.png", dpi=150)
        plt.close()

    # -- 2. Depth error histogram --
    errors = [r["depth_error_m"] for r in per_reservoir if "depth_error_m" in r]
    if errors:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.hist(errors, bins=30, edgecolor="k", alpha=0.7, color="steelblue")
        ax.axvline(0, color="red", linestyle="--", alpha=0.7)
        ax.set_xlabel("Depth Error (Predicted - True) (m)")
        ax.set_ylabel("Count")
        ax.set_title("Reservoir Depth Error Distribution")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(output_dir / "depth_error_hist.png", dpi=150)
        plt.close()

    # -- 3. Depth-stratified bar chart --
    bin_names = []
    bin_rmses = []
    for bname, bdata in depth_strat.items():
        if bname == "label":
            continue
        if isinstance(bdata, dict) and "rmse" in bdata:
            bin_names.append(bname)
            bin_rmses.append(bdata["rmse"])

    if bin_names:
        fig, ax = plt.subplots(figsize=(8, 5))
        bars = ax.bar(bin_names, bin_rmses, color="steelblue", edgecolor="k")
        for bar, rmse in zip(bars, bin_rmses):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                    f"{rmse:.2f}", ha="center", va="bottom", fontsize=9)
        ax.set_ylabel("RMSE (m)")
        ax.set_title("Depth-Stratified RMSE")
        ax.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        plt.savefig(output_dir / "depth_stratified_rmse.png", dpi=150)
        plt.close()

    # -- 4. Ablation comparison --
    if ablation:
        abl_names = list(ablation.keys())
        abl_rmses = [ablation[k].get("rmse", 0) for k in abl_names]
        if any(r > 0 for r in abl_rmses):
            fig, ax = plt.subplots(figsize=(8, 5))
            bars = ax.bar(abl_names, abl_rmses, color=["#4c72b0", "#55a868", "#c44e52"],
                          edgecolor="k")
            for bar, rmse in zip(bars, abl_rmses):
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                        f"{rmse:.2f}", ha="center", va="bottom", fontsize=9)
            ax.set_ylabel("RMSE (m)")
            ax.set_title("Ablation: Terrain-only vs A-E-only vs Ensemble")
            ax.grid(axis="y", alpha=0.3)
            plt.tight_layout()
            plt.savefig(output_dir / "ablation_comparison.png", dpi=150)
            plt.close()

    # -- 5. Benchmark comparison --
    our_depths = [r.get("depth_abs_error_m", np.nan) for r in per_reservoir]
    our_depths = [d for d in our_depths if np.isfinite(d)]
    if our_depths:
        our_rmse = float(np.sqrt(np.mean(np.array(our_depths) ** 2)))
        names = ["Ours"] + list(BENCHMARKS.keys())
        rmses = [our_rmse]
        for bname, bdata in BENCHMARKS.items():
            rmses.append(bdata.get("rmse_m", bdata.get("mae_m", 0)))

        fig, ax = plt.subplots(figsize=(8, 5))
        colors = ["#c44e52"] + ["#4c72b0"] * len(BENCHMARKS)
        bars = ax.bar(names, rmses, color=colors, edgecolor="k")
        for bar, rmse in zip(bars, rmses):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                    f"{rmse:.2f}", ha="center", va="bottom", fontsize=9)
        ax.set_ylabel("RMSE / MAE (m)")
        ax.set_title("Benchmark Comparison (Max Depth)")
        ax.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        plt.savefig(output_dir / "benchmark_comparison.png", dpi=150)
        plt.close()

    log.info("Plots saved.")


# ══════════════════════════════════════════════════════════════════════
# Main Validation Pipeline
# ══════════════════════════════════════════════════════════════════════

def run_validation(
    predicted_path: Path,
    usbr_dir: Path,
    output_dir: Path,
    do_plots: bool = False,
) -> dict:
    """Full validation pipeline."""
    output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # Load data
    pred_df = load_predicted_ae(predicted_path)
    usbr_surveys = load_usbr_surveys(usbr_dir)

    if not usbr_surveys:
        log.error("No USBR surveys loaded, aborting")
        return {"error": "no_usbr_data"}

    # -- Per-reservoir comparison --
    per_reservoir = []
    matched_ids = []

    for res_id, usbr_df in usbr_surveys.items():
        res_pred = pred_df[pred_df["reservoir_id"] == res_id] if "reservoir_id" in pred_df.columns else pd.DataFrame()
        if len(res_pred) == 0:
            log.debug(f"No predictions for {res_id}, skipping")
            continue

        comparison = compare_ae_curves(res_pred, usbr_df, res_id)
        per_reservoir.append(comparison)
        matched_ids.append(res_id)

    log.info(f"Matched {len(per_reservoir)} reservoirs between predictions and USBR")

    if not per_reservoir:
        log.error("No matched reservoirs found")
        return {"error": "no_matches"}

    # -- Aggregate depth metrics --
    all_true_depths = []
    all_pred_depths = []
    for r in per_reservoir:
        if "usbr_max_depth_m" in r and "pred_max_depth_m" in r:
            all_true_depths.append(r["usbr_max_depth_m"])
            all_pred_depths.append(r["pred_max_depth_m"])

    true_arr = np.array(all_true_depths)
    pred_arr = np.array(all_pred_depths)

    overall_metrics = compute_metrics(true_arr, pred_arr, "overall_max_depth")
    depth_strat = compute_depth_stratified(true_arr, pred_arr, "depth_stratified")

    # Relative volume errors
    vol_errors = [r["relative_volume_error_pct"] for r in per_reservoir
                  if "relative_volume_error_pct" in r]
    vol_summary = {}
    if vol_errors:
        vol_arr = np.array(vol_errors)
        vol_summary = {
            "mean_pct": float(np.mean(vol_arr)),
            "median_pct": float(np.median(vol_arr)),
            "p90_pct": float(np.percentile(vol_arr, 90)),
            "n": len(vol_arr),
        }

    # -- Ablation --
    ablation = run_ablation(pred_df, usbr_surveys)

    # -- Benchmark comparison --
    benchmark_comparison = {}
    if "rmse" in overall_metrics:
        our_rmse = overall_metrics["rmse"]
        for bname, bdata in BENCHMARKS.items():
            ref_rmse = bdata.get("rmse_m", bdata.get("mae_m", np.nan))
            benchmark_comparison[bname] = {
                "ref_value_m": ref_rmse,
                "source": bdata["source"],
                "our_rmse": our_rmse,
                "skill_score": compute_skill_score(our_rmse, ref_rmse) if np.isfinite(ref_rmse) else np.nan,
            }

    # -- Compile results --
    elapsed = time.time() - t0

    results = {
        "n_matched_reservoirs": len(per_reservoir),
        "overall_max_depth": overall_metrics,
        "depth_stratified": depth_strat,
        "relative_volume_error": vol_summary,
        "ablation": ablation,
        "benchmark_comparison": benchmark_comparison,
        "per_reservoir": per_reservoir,
        "elapsed_seconds": round(elapsed, 1),
    }

    # -- Save JSON --
    def _serializable(obj):
        if isinstance(obj, dict):
            return {k: _serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_serializable(v) for v in obj]
        if isinstance(obj, (np.floating, np.float32, np.float64)):
            return float(obj)
        if isinstance(obj, (np.integer, np.int32, np.int64)):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    json_path = output_dir / "reservoir_validation.json"
    with open(json_path, "w") as f:
        json.dump(_serializable(results), f, indent=2)

    # -- Console summary --
    log.info(f"\n{'='*80}")
    log.info("  RESERVOIR VALIDATION RESULTS (vs USBR Surveys)")
    log.info(f"{'='*80}")
    log.info(f"  Matched reservoirs: {len(per_reservoir)}")
    log.info(f"  Overall max depth:  RMSE={overall_metrics.get('rmse', '?'):.3f}m  "
             f"MAE={overall_metrics.get('mae', '?'):.3f}m  "
             f"R²={overall_metrics.get('r2', '?'):.4f}")

    if vol_summary:
        log.info(f"  Relative volume error: mean={vol_summary['mean_pct']:.1f}%  "
                 f"median={vol_summary['median_pct']:.1f}%  "
                 f"p90={vol_summary['p90_pct']:.1f}%")

    # Depth-stratified
    log.info(f"\n  {'Depth Bin':<12s} {'RMSE':>8s} {'MAE':>8s} {'R²':>8s} {'N':>6s}")
    log.info("-" * 50)
    for bin_name in DEPTH_BINS:
        bdata = depth_strat.get(bin_name, {})
        if "error" in bdata:
            log.info(f"  {bin_name:<12s} {'(too few)':>30s}")
        else:
            log.info(f"  {bin_name:<12s} {bdata.get('rmse', np.nan):8.3f} "
                     f"{bdata.get('mae', np.nan):8.3f} "
                     f"{bdata.get('r2', np.nan):8.4f} "
                     f"{bdata.get('n', 0):6d}")

    # Ablation
    if ablation:
        log.info(f"\n  {'Ablation':<15s} {'RMSE':>8s} {'MAE':>8s} {'R²':>8s}")
        log.info("-" * 50)
        for abl_name, abl_metrics in ablation.items():
            log.info(f"  {abl_name:<15s} {abl_metrics.get('rmse', np.nan):8.3f} "
                     f"{abl_metrics.get('mae', np.nan):8.3f} "
                     f"{abl_metrics.get('r2', np.nan):8.4f}")

    # Benchmark comparison
    log.info(f"\n  {'Benchmark':<15s} {'Ref RMSE':>10s} {'Our RMSE':>10s} {'Skill':>8s}")
    log.info("-" * 50)
    for bname, bcomp in benchmark_comparison.items():
        log.info(f"  {bname:<15s} {bcomp['ref_value_m']:10.3f} "
                 f"{bcomp['our_rmse']:10.3f} "
                 f"{bcomp.get('skill_score', np.nan):8.3f}")

    # Per-reservoir table
    log.info(f"\n  {'Reservoir':<25s} {'True Depth':>12s} {'Pred Depth':>12s} "
             f"{'Error':>8s} {'Vol Err%':>10s}")
    log.info("-" * 80)
    for r in sorted(per_reservoir, key=lambda x: abs(x.get("depth_error_m", 0)), reverse=True):
        res_id = r["reservoir_id"]
        true_d = r.get("usbr_max_depth_m", np.nan)
        pred_d = r.get("pred_max_depth_m", np.nan)
        err = r.get("depth_error_m", np.nan)
        vol_err = r.get("relative_volume_error_pct", np.nan)
        log.info(f"  {res_id:<25s} {true_d:12.2f} {pred_d:12.2f} "
                 f"{err:8.2f} {vol_err:10.1f}")

    log.info(f"\n{'='*80}")
    log.info(f"  Results saved to: {json_path}")
    log.info(f"  Elapsed: {elapsed:.1f}s")
    log.info(f"{'='*80}")

    # -- Plots --
    if do_plots:
        generate_plots(per_reservoir, depth_strat, ablation, output_dir)

    return results


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Validate Reservoir Predictions Against USBR Surveys"
    )
    parser.add_argument(
        "--predicted", type=str, required=True,
        help="Path to predicted A-E curves (parquet or CSV)",
    )
    parser.add_argument(
        "--usbr", type=str, required=True,
        help="Directory with USBR survey A-E tables (one per reservoir)",
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output directory for validation results",
    )
    parser.add_argument(
        "--plots", action="store_true",
        help="Generate comparison plots (requires matplotlib)",
    )
    args = parser.parse_args()

    run_validation(
        predicted_path=Path(args.predicted),
        usbr_dir=Path(args.usbr),
        output_dir=Path(args.output),
        do_plots=args.plots,
    )


if __name__ == "__main__":
    main()
