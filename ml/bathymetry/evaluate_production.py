#!/usr/bin/env python3
"""
OpenCatch — Production Evaluation with Regime Routing

Evaluates hybrid sonar model predictions using a regime-routing architecture:
  - clear_anchored: spectral SDB works well, candidates for within-lake upgrade
  - moderate_spectral: partial spectral signal, blended approach
  - turbid_morphometric: morphometric/terrain backbone dominates

Reports headline metrics, per-lake distributions, coverage-error curves,
depth-stratified RMSE/bias, regime-specific breakdowns, and per-lake summaries.

Usage:
    python evaluate_production.py \
        --predictions /data/lake_v4_*/hybrid_sonar/test_predictions.parquet \
        --output /data/production_eval
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from train_hybrid_sonar import compute_metrics, compute_depth_bins

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("prod_eval")

EPS = 1e-6

DEPTH_BINS = [
    (0, 2, "0-2m"),
    (2, 5, "2-5m"),
    (5, 10, "5-10m"),
    (10, 20, "10-20m"),
    (20, 50, "20-50m"),
]

REGIMES = ["clear_anchored", "moderate_spectral", "turbid_morphometric"]


# ── Regime classification ────────────────────────────────────────────

def classify_regime(df: pd.DataFrame) -> pd.Series:
    """Assign each row to a regime based on clarity_class and depth."""
    regime = pd.Series("moderate_spectral", index=df.index, dtype="object")

    # Turbid OR deep lakes -> morphometric backbone
    is_turbid = df["clarity_class"].str.lower() == "turbid" if "clarity_class" in df.columns else pd.Series(False, index=df.index)
    is_deep = df["lake_max_depth_m"] > 20.0 if "lake_max_depth_m" in df.columns else pd.Series(False, index=df.index)
    regime[is_turbid | is_deep] = "turbid_morphometric"

    # Clear lakes with spectral signal
    is_clear = df["clarity_class"].str.lower() == "clear" if "clarity_class" in df.columns else pd.Series(False, index=df.index)
    regime[is_clear & ~is_deep] = "clear_anchored"

    return regime


# ── Confidence scoring ───────────────────────────────────────────────

def compute_confidence(lake_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-lake production confidence score combining:
      1. Inverse residual spread (lower std -> higher confidence)
      2. Clarity class bonus (clear > moderate > turbid)
      3. Point density (more points -> more reliable)
      4. Depth range coverage fraction
    Returns DataFrame with lake_id, confidence, and sub-components.
    """
    records = []
    for lake_id, grp in lake_df.groupby("lake_id"):
        residuals = np.abs(grp["depth_m"].values - grp["pred_final"].values)
        residual_std = float(np.std(residuals)) if len(residuals) > 1 else 999.0

        # 1. Residual stability: sigmoid-mapped inverse std
        resid_score = 1.0 / (1.0 + residual_std)

        # 2. Clarity bonus
        clarity = grp["clarity_class"].iloc[0] if "clarity_class" in grp.columns else "moderate"
        clarity_map = {"clear": 1.0, "moderate": 0.6, "turbid": 0.3}
        clarity_score = clarity_map.get(str(clarity).lower(), 0.5)

        # 3. Point density: log-scaled count (saturates ~500 pts)
        n_pts = len(grp)
        density_score = min(1.0, math.log1p(n_pts) / math.log1p(500))

        # 4. Depth range coverage: fraction of 0-max covered
        depth_range = grp["depth_m"].max() - grp["depth_m"].min()
        max_depth = grp["depth_m"].max()
        coverage_score = float(depth_range / (max_depth + EPS)) if max_depth > 0 else 0.0

        # Spectral quality indicator (if available)
        spectral_score = 1.0
        if "temporal_match_score" in grp.columns:
            tms = grp["temporal_match_score"].mean()
            spectral_score = float(tms) if np.isfinite(tms) else 0.5

        # Weighted combination
        confidence = (
            0.35 * resid_score
            + 0.20 * clarity_score
            + 0.15 * density_score
            + 0.15 * coverage_score
            + 0.15 * spectral_score
        )

        records.append({
            "lake_id": lake_id,
            "confidence": round(float(confidence), 4),
            "resid_std": round(residual_std, 4),
            "resid_score": round(resid_score, 4),
            "clarity_score": round(clarity_score, 4),
            "density_score": round(density_score, 4),
            "coverage_score": round(coverage_score, 4),
            "spectral_score": round(spectral_score, 4),
            "n_points": n_pts,
        })

    return pd.DataFrame(records)


# ── Coverage-error curve ─────────────────────────────────────────────

def coverage_error_curve(
    lake_metrics: pd.DataFrame, thresholds: list[float] | None = None
) -> list[dict]:
    """
    Compute RMSE at different coverage levels by retaining the most-confident
    lakes first and dropping the least-confident.
    """
    if thresholds is None:
        thresholds = [1.0, 0.8, 0.6, 0.4, 0.2]

    sorted_lakes = lake_metrics.sort_values("confidence", ascending=False).reset_index(drop=True)
    n_total = len(sorted_lakes)
    results = []

    for frac in thresholds:
        n_keep = max(1, int(n_total * frac))
        subset = sorted_lakes.iloc[:n_keep]
        pooled_rmse = float(np.sqrt((subset["rmse"] ** 2 * subset["n_points"]).sum()
                                     / subset["n_points"].sum()))
        mean_r2 = float(subset["r2"].mean())
        results.append({
            "coverage_pct": int(frac * 100),
            "n_lakes": n_keep,
            "pooled_rmse": round(pooled_rmse, 3),
            "mean_lake_r2": round(mean_r2, 4),
        })

    return results


# ── Per-lake metrics ─────────────────────────────────────────────────

def per_lake_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Compute R2, RMSE, MAE, bias per lake."""
    records = []
    for lake_id, grp in df.groupby("lake_id"):
        m = compute_metrics(grp["depth_m"].values, grp["pred_final"].values)
        m["lake_id"] = lake_id
        m["regime"] = grp["regime"].iloc[0] if "regime" in grp.columns else "unknown"
        if "clarity_class" in grp.columns:
            m["clarity_class"] = grp["clarity_class"].iloc[0]
        if "lake_max_depth_m" in grp.columns:
            m["lake_max_depth_m"] = float(grp["lake_max_depth_m"].iloc[0])
        m["n_points"] = len(grp)
        records.append(m)
    return pd.DataFrame(records)


# ── Pretty printing ──────────────────────────────────────────────────

def print_headline(metrics: dict) -> None:
    log.info("=" * 60)
    log.info("HEADLINE METRICS (pooled pointwise)")
    log.info("  N points : %d", metrics["n"])
    log.info("  R2       : %.4f", metrics["r2"])
    log.info("  RMSE     : %.3f m", metrics["rmse"])
    log.info("  MAE      : %.3f m", metrics["mae"])
    log.info("  Bias     : %.3f m", metrics["bias"])
    log.info("  Median AE: %.3f m", metrics["median_ae"])
    log.info("  P90 AE   : %.3f m", metrics["p90_ae"])
    log.info("=" * 60)


def print_distribution(lake_df: pd.DataFrame) -> None:
    log.info("-" * 60)
    log.info("PER-LAKE DISTRIBUTION")
    for col in ["r2", "rmse", "mae"]:
        vals = lake_df[col].dropna()
        if len(vals) == 0:
            continue
        log.info(
            "  %-6s  mean=%.3f  median=%.3f  p10=%.3f  p25=%.3f  p75=%.3f  p90=%.3f",
            col.upper(),
            vals.mean(), vals.median(),
            np.percentile(vals, 10), np.percentile(vals, 25),
            np.percentile(vals, 75), np.percentile(vals, 90),
        )
    log.info("-" * 60)


def print_depth_bins(bins: dict) -> None:
    log.info("-" * 60)
    log.info("DEPTH-STRATIFIED METRICS")
    for label, m in bins.items():
        if "rmse" in m:
            log.info("  %-8s  n=%5d  RMSE=%.3f  MAE=%.3f  bias=%+.3f",
                     label, m["n"], m["rmse"], m["mae"], m["bias"])
        else:
            log.info("  %-8s  n=%5d  (too few points)", label, m.get("n", 0))
    log.info("-" * 60)


def print_regime(lake_df: pd.DataFrame) -> None:
    log.info("-" * 60)
    log.info("REGIME-SPECIFIC METRICS")
    for regime in REGIMES:
        subset = lake_df[lake_df["regime"] == regime]
        if len(subset) == 0:
            log.info("  %-25s  (no lakes)", regime)
            continue
        log.info(
            "  %-25s  lakes=%d  mean_R2=%.3f  med_R2=%.3f  mean_RMSE=%.3f",
            regime, len(subset),
            subset["r2"].mean(), subset["r2"].median(), subset["rmse"].mean(),
        )
    log.info("-" * 60)


def print_coverage_curve(curve: list[dict]) -> None:
    log.info("-" * 60)
    log.info("COVERAGE-ERROR CURVE (most confident first)")
    for row in curve:
        log.info(
            "  Top %3d%%  lakes=%4d  pooled_RMSE=%.3f m  mean_R2=%.4f",
            row["coverage_pct"], row["n_lakes"], row["pooled_rmse"], row["mean_lake_r2"],
        )
    log.info("-" * 60)


def print_best_worst(lake_df: pd.DataFrame, n: int = 20) -> None:
    valid = lake_df.dropna(subset=["r2"]).copy()
    if len(valid) == 0:
        return

    log.info("-" * 60)
    log.info("TOP %d BEST LAKES (by R2)", n)
    best = valid.nlargest(n, "r2")
    for _, row in best.iterrows():
        log.info(
            "  %-20s  R2=%.3f  RMSE=%.2f  n=%d  regime=%s",
            str(row["lake_id"])[:20], row["r2"], row["rmse"],
            row["n_points"], row.get("regime", "?"),
        )

    log.info("BOTTOM %d WORST LAKES (by R2)", n)
    worst = valid.nsmallest(n, "r2")
    for _, row in worst.iterrows():
        log.info(
            "  %-20s  R2=%.3f  RMSE=%.2f  n=%d  regime=%s",
            str(row["lake_id"])[:20], row["r2"], row["rmse"],
            row["n_points"], row.get("regime", "?"),
        )
    log.info("-" * 60)


# ── Main pipeline ────────────────────────────────────────────────────

def load_predictions(pattern: str) -> pd.DataFrame:
    """Load test predictions from one or more parquet files (supports globs)."""
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise FileNotFoundError(f"No files matching: {pattern}")
    log.info("Loading predictions from %d file(s)", len(paths))
    frames = [pd.read_parquet(p) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    log.info("Loaded %d points across %d lakes", len(df), df["lake_id"].nunique())
    return df


def run_evaluation(df: pd.DataFrame, output_dir: Path) -> dict:
    """Full evaluation pipeline. Returns combined metrics dict."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Validate required columns
    required = {"lake_id", "depth_m", "pred_final"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    # ── Regime assignment ────────────────────────────────────────
    df["regime"] = classify_regime(df)
    regime_counts = df.groupby("regime")["lake_id"].nunique()
    log.info("Regime assignment: %s", dict(regime_counts))

    # ── Headline 1: Pooled pointwise ─────────────────────────────
    headline = compute_metrics(df["depth_m"].values, df["pred_final"].values)
    print_headline(headline)

    # ── Depth-stratified ─────────────────────────────────────────
    depth_bins = compute_depth_bins(df["depth_m"].values, df["pred_final"].values)
    print_depth_bins(depth_bins)

    # ── Per-lake metrics ─────────────────────────────────────────
    lake_df = per_lake_metrics(df)
    print_distribution(lake_df)

    # ── Confidence scoring ───────────────────────────────────────
    conf_df = compute_confidence(df)
    lake_df = lake_df.merge(conf_df[["lake_id", "confidence", "resid_std",
                                       "resid_score", "clarity_score",
                                       "density_score", "coverage_score",
                                       "spectral_score"]],
                              on="lake_id", how="left")

    # ── Coverage-error curve ─────────────────────────────────────
    coverage_curve = coverage_error_curve(lake_df)
    print_coverage_curve(coverage_curve)

    # ── Regime-specific ──────────────────────────────────────────
    print_regime(lake_df)

    regime_metrics = {}
    for regime in REGIMES:
        subset = df[df["regime"] == regime]
        if len(subset) >= 5:
            regime_metrics[regime] = compute_metrics(
                subset["depth_m"].values, subset["pred_final"].values
            )
        else:
            regime_metrics[regime] = {"n": len(subset)}

    # ── Best / worst lakes ───────────────────────────────────────
    print_best_worst(lake_df, n=20)

    # ── Assemble full report ─────────────────────────────────────
    report = {
        "headline_pooled": headline,
        "per_lake_distribution": {
            col: {
                "mean": round(float(lake_df[col].mean()), 4),
                "median": round(float(lake_df[col].median()), 4),
                "p10": round(float(np.percentile(lake_df[col].dropna(), 10)), 4),
                "p25": round(float(np.percentile(lake_df[col].dropna(), 25)), 4),
                "p75": round(float(np.percentile(lake_df[col].dropna(), 75)), 4),
                "p90": round(float(np.percentile(lake_df[col].dropna(), 90)), 4),
            }
            for col in ["r2", "rmse", "mae"]
            if col in lake_df.columns and lake_df[col].notna().sum() > 0
        },
        "depth_stratified": depth_bins,
        "regime_metrics": regime_metrics,
        "coverage_error_curve": coverage_curve,
        "n_lakes": int(df["lake_id"].nunique()),
        "n_points": len(df),
        "regime_lake_counts": {k: int(v) for k, v in regime_counts.items()},
    }

    # ── Write outputs ────────────────────────────────────────────
    json_path = output_dir / "production_metrics.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info("Wrote JSON metrics to %s", json_path)

    parquet_path = output_dir / "per_lake_metrics.parquet"
    lake_df.to_parquet(parquet_path, index=False)
    log.info("Wrote per-lake parquet to %s (%d lakes)", parquet_path, len(lake_df))

    return report


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenCatch production evaluation with regime routing",
    )
    parser.add_argument(
        "--predictions", required=True,
        help="Glob pattern for test prediction parquet(s)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Output directory for metrics JSON and per-lake parquet",
    )
    args = parser.parse_args()

    df = load_predictions(args.predictions)
    report = run_evaluation(df, Path(args.output))

    # Final summary line
    h = report["headline_pooled"]
    log.info(
        "DONE — %d lakes, %d points | R2=%.4f  RMSE=%.3f m  MAE=%.3f m",
        report["n_lakes"], report["n_points"], h["r2"], h["rmse"], h["mae"],
    )


if __name__ == "__main__":
    main()
