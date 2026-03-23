#!/usr/bin/env python3
"""
OpenCatch — 3D-LAKES Analysis & Validation Pipeline

Task 1: Analyze 510K 3D-LAKES results (depth stats, QA distribution, volume)
Task 2: Validate 3D-LAKES vs MN DNR sonar ground truth
Task 3: Generate bathymetric contour maps from A-E curves
Task 4: Enhancement plan metrics

Runs on the A4000 (Vast.ai).

Usage:
    python analyze_3dlakes.py --task all
    python analyze_3dlakes.py --task 1        # Just analysis
    python analyze_3dlakes.py --task 2        # Just validation
    python analyze_3dlakes.py --task 3        # Just contour generation
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import integrate
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("3dlakes_analysis")


# ── Configuration ───────────────────────────────────────────────────

L1_DIR = "/data/3d_lakes_l1"
ST_CSV = "/data/3d_lakes_st.csv"
QA_CSV = "/data/3d_lakes_qa.csv"
GLOBAL_PARQUET = "/data/training/global_510k.parquet"
DEPTHS_PARQUET = "/data/3d_lakes_with_depths.parquet"
MN_SONAR = "/data/icesat2_depths/mn_sampled_depths.parquet"
OUTPUT_DIR = "/data/3dlakes_analysis"


# ══════════════════════════════════════════════════════════════════════
# TASK 1: Analyze 510K 3D-LAKES Results
# ══════════════════════════════════════════════════════════════════════

def task1_analyze_results():
    """
    Comprehensive analysis of all 510K 3D-LAKES A-E profiles.

    For each lake: extract max_depth, mean_depth, volume (integral of A-E curve).
    Join with QA data. Report distributions and quality statistics.
    """
    log.info("=" * 70)
    log.info("TASK 1: Analyze 510K 3D-LAKES A-E Profiles")
    log.info("=" * 70)

    t0 = time.time()

    # ── Load the pre-built global dataset (has morphometric features) ──
    log.info("Loading global dataset...")
    global_df = pd.read_parquet(GLOBAL_PARQUET)
    log.info(f"  Global dataset: {len(global_df):,} lakes, {global_df.shape[1]} columns")

    # ── Load QA data ──
    log.info("Loading QA data...")
    qa = pd.read_csv(QA_CSV)
    qa = qa.rename(columns={"Hylak_id": "hylak_id"})
    log.info(f"  QA dataset: {len(qa):,} lakes")

    # ── Process a sample of L1 CSVs to compute volume and mean depth ──
    # The global_df already has max_depth and morphometric features from
    # extract_3dlakes_depths.py, but we need volume (integral of A-E curve)
    log.info("\nProcessing L1 A-E CSV files for volume computation...")
    l1_path = Path(L1_DIR)
    csv_files = sorted(l1_path.glob("*_L1.csv"))
    log.info(f"  Found {len(csv_files):,} L1 files")

    volume_records = []
    errors = 0

    for f in tqdm(csv_files, desc="Computing volumes", mininterval=10):
        try:
            hylak_id = int(f.stem.replace("_L1", ""))

            ae = pd.read_csv(f)
            if len(ae) < 2:
                continue

            elev = ae.iloc[:, 0].values  # Elevation (m)
            area = ae.iloc[:, 1].values  # Area (m2)

            # Sort by elevation (bottom to top)
            idx = np.argsort(elev)
            elev = elev[idx]
            area = area[idx]

            max_depth = float(elev[-1] - elev[0])
            if max_depth <= 0:
                continue

            # Depth from surface: depth = max_elev - elev
            depth = elev[-1] - elev

            # Volume = integral of Area(depth) d(depth), from 0 to max_depth
            # Using trapezoidal integration of A-E curve
            # V = integral from bottom_elev to top_elev of Area(h) dh
            volume_m3 = float(np.trapz(area, elev))

            # Mean depth = Volume / Surface area
            surface_area = float(area[-1])  # area at max elevation
            mean_depth = volume_m3 / surface_area if surface_area > 0 else np.nan

            # Volume development = 3 * mean_depth / max_depth
            vol_dev = 3 * mean_depth / max_depth if max_depth > 0 else np.nan

            # Hypsometric integral = integral of relative area vs relative depth
            if max_depth > 0 and surface_area > 0:
                rel_depth = depth / max_depth
                rel_area = area / surface_area
                hyps_integral = float(np.trapz(rel_area, rel_depth))
            else:
                hyps_integral = np.nan

            # Depth percentiles from A-E curve
            # At what depth is 25%, 50%, 75% of area submerged?
            if surface_area > 0:
                area_frac = area / surface_area
                d25_idx = np.searchsorted(area_frac, 0.25)
                d50_idx = np.searchsorted(area_frac, 0.50)
                d75_idx = np.searchsorted(area_frac, 0.75)
                depth_at_25pct = depth[min(d25_idx, len(depth)-1)]
                depth_at_50pct = depth[min(d50_idx, len(depth)-1)]
                depth_at_75pct = depth[min(d75_idx, len(depth)-1)]
            else:
                depth_at_25pct = depth_at_50pct = depth_at_75pct = np.nan

            volume_records.append({
                "hylak_id": hylak_id,
                "max_depth_m": max_depth,
                "mean_depth_m": mean_depth,
                "volume_m3": volume_m3,
                "volume_km3": volume_m3 / 1e9,
                "surface_area_km2": surface_area / 1e6,
                "vol_development": vol_dev,
                "hyps_integral": hyps_integral,
                "n_ae_points": len(ae),
                "depth_at_25pct": float(depth_at_25pct),
                "depth_at_50pct": float(depth_at_50pct),
                "depth_at_75pct": float(depth_at_75pct),
            })

        except Exception as e:
            errors += 1
            if errors <= 5:
                log.warning(f"  Error processing {f.name}: {e}")

    log.info(f"\n  Processed {len(volume_records):,} lakes successfully ({errors} errors)")

    vol_df = pd.DataFrame(volume_records)

    # ── Merge with QA data ──
    merged = vol_df.merge(qa, on="hylak_id", how="left")
    log.info(f"  Merged with QA: {merged['QA_RMSE'].notna().sum():,} lakes have QA data")

    # ── Merge with coordinates from global dataset ──
    coords = global_df[["hylak_id", "lat", "lon"]].drop_duplicates()
    merged = merged.merge(coords, on="hylak_id", how="left")

    # ══════════════════════════════════════════════════════════════════
    # REPORT: Depth Distribution
    # ══════════════════════════════════════════════════════════════════
    log.info("\n" + "=" * 70)
    log.info("DEPTH DISTRIBUTION (510K lakes)")
    log.info("=" * 70)

    depths = merged["max_depth_m"]
    log.info(f"  Total lakes: {len(depths):,}")
    log.info(f"  Min depth:   {depths.min():.3f}m")
    log.info(f"  25th pctile: {depths.quantile(0.25):.2f}m")
    log.info(f"  Median:      {depths.median():.2f}m")
    log.info(f"  Mean:        {depths.mean():.2f}m")
    log.info(f"  75th pctile: {depths.quantile(0.75):.2f}m")
    log.info(f"  95th pctile: {depths.quantile(0.95):.2f}m")
    log.info(f"  Max depth:   {depths.max():.1f}m")

    log.info("\n  Depth distribution:")
    for lo, hi in [(0, 1), (1, 2), (2, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 500), (500, 2000)]:
        n = ((depths >= lo) & (depths < hi)).sum()
        pct = n / len(depths) * 100
        log.info(f"    {lo:>5.0f} - {hi:<5.0f}m: {n:>7,} lakes ({pct:.1f}%)")

    # ══════════════════════════════════════════════════════════════════
    # REPORT: Volume Distribution
    # ══════════════════════════════════════════════════════════════════
    log.info("\n" + "=" * 70)
    log.info("VOLUME DISTRIBUTION")
    log.info("=" * 70)

    vols = merged["volume_km3"]
    log.info(f"  Total volume: {vols.sum():.2f} km³")
    log.info(f"  Median volume: {vols.median():.6f} km³ ({vols.median() * 1e6:.0f} m³)")
    log.info(f"  Mean volume:   {vols.mean():.6f} km³")
    log.info(f"  Max volume:    {vols.max():.2f} km³")

    log.info(f"\n  Mean depth distribution:")
    md = merged["mean_depth_m"]
    log.info(f"    Min:    {md.min():.3f}m")
    log.info(f"    Median: {md.median():.2f}m")
    log.info(f"    Mean:   {md.mean():.2f}m")
    log.info(f"    Max:    {md.max():.1f}m")

    log.info(f"\n  Volume development (Vd = 3 * mean/max):")
    vd = merged["vol_development"]
    log.info(f"    Median: {vd.median():.3f}")
    log.info(f"    Mean:   {vd.mean():.3f}")
    log.info(f"    (Vd=1.0 = cone, Vd=3.0 = cylinder, >1 = concave bowl)")

    # ══════════════════════════════════════════════════════════════════
    # REPORT: QA Quality Flags
    # ══════════════════════════════════════════════════════════════════
    log.info("\n" + "=" * 70)
    log.info("QA QUALITY ASSESSMENT")
    log.info("=" * 70)

    qa_valid = merged[merged["QA_RMSE"].notna()]
    n_qa = len(qa_valid)
    log.info(f"  Lakes with QA data: {n_qa:,} / {len(merged):,}")

    if n_qa > 0:
        # QA_RMSE is categorical: 0, 1, 2 (from the paper)
        # 0 = RMSE < 1m, 1 = RMSE 1-2m, 2 = RMSE > 2m
        log.info("\n  QA_RMSE distribution (0=<1m, 1=1-2m, 2=>2m):")
        for val in sorted(qa_valid["QA_RMSE"].unique()):
            n = (qa_valid["QA_RMSE"] == val).sum()
            pct = n / n_qa * 100
            label = {0: "RMSE < 1m (excellent)", 1: "RMSE 1-2m (good)", 2: "RMSE > 2m (fair)"}.get(val, f"code {val}")
            log.info(f"    QA_RMSE={val}: {n:>7,} lakes ({pct:.1f}%) — {label}")

        log.info("\n  QA_NRMSE distribution (normalized RMSE):")
        for val in sorted(qa_valid["QA_NRMSE"].unique()):
            n = (qa_valid["QA_NRMSE"] == val).sum()
            pct = n / n_qa * 100
            log.info(f"    QA_NRMSE={val}: {n:>7,} lakes ({pct:.1f}%)")

        log.info("\n  QA_Extrapolation distribution:")
        for val in sorted(qa_valid["QA_Extrapolation"].unique()):
            n = (qa_valid["QA_Extrapolation"] == val).sum()
            pct = n / n_qa * 100
            label = {0: "no extrapolation needed", 1: "extrapolation used"}.get(val, f"code {val}")
            log.info(f"    QA_Extrap={val}: {n:>7,} lakes ({pct:.1f}%) — {label}")

        # NRMSE_model (continuous metric)
        nrmse = qa_valid["NRMSE_model"]
        log.info(f"\n  NRMSE_model (continuous):")
        log.info(f"    Min:    {nrmse.min():.4f}")
        log.info(f"    Median: {nrmse.median():.4f}")
        log.info(f"    Mean:   {nrmse.mean():.4f}")
        log.info(f"    90th:   {nrmse.quantile(0.90):.4f}")
        log.info(f"    Max:    {nrmse.max():.4f}")

        # Quality combinations
        excellent = ((qa_valid["QA_RMSE"] == 0) & (qa_valid["QA_NRMSE"] == 0) &
                     (qa_valid["QA_Extrapolation"] == 0)).sum()
        log.info(f"\n  Best quality (all flags 0): {excellent:,} lakes ({excellent/n_qa*100:.1f}%)")

    # ══════════════════════════════════════════════════════════════════
    # REPORT: Geographic Distribution
    # ══════════════════════════════════════════════════════════════════
    log.info("\n" + "=" * 70)
    log.info("GEOGRAPHIC DISTRIBUTION")
    log.info("=" * 70)

    valid_coords = merged[merged["lat"].notna()]

    # Continent-level
    na_mask = (valid_coords["lon"].between(-170, -50)) & (valid_coords["lat"].between(15, 75))
    eu_mask = (valid_coords["lon"].between(-30, 60)) & (valid_coords["lat"].between(35, 72))
    asia_mask = (valid_coords["lon"].between(60, 180)) & (valid_coords["lat"].between(0, 75))
    sa_mask = (valid_coords["lon"].between(-90, -30)) & (valid_coords["lat"].between(-60, 15))
    africa_mask = (valid_coords["lon"].between(-20, 55)) & (valid_coords["lat"].between(-40, 37))
    oceania_mask = (valid_coords["lon"].between(100, 180)) & (valid_coords["lat"].between(-50, 0))

    log.info(f"  North America: {na_mask.sum():>7,}")
    log.info(f"  Europe:        {eu_mask.sum():>7,}")
    log.info(f"  Asia:          {asia_mask.sum():>7,}")
    log.info(f"  South America: {sa_mask.sum():>7,}")
    log.info(f"  Africa:        {africa_mask.sum():>7,}")
    log.info(f"  Oceania:       {oceania_mask.sum():>7,}")

    # US states of interest
    mn_mask = (valid_coords["lon"].between(-97.5, -89.5)) & (valid_coords["lat"].between(43.5, 49.4))
    wi_mask = (valid_coords["lon"].between(-93.0, -86.5)) & (valid_coords["lat"].between(42.5, 47.1))
    mi_mask = (valid_coords["lon"].between(-90.5, -82.0)) & (valid_coords["lat"].between(41.7, 48.3))
    log.info(f"\n  Minnesota area: {mn_mask.sum():>6,}")
    log.info(f"  Wisconsin area: {wi_mask.sum():>6,}")
    log.info(f"  Michigan area:  {mi_mask.sum():>6,}")

    # ── Save results ──
    output_path = Path(OUTPUT_DIR) / "task1_analysis.parquet"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(output_path, index=False)
    log.info(f"\n  Saved full analysis: {output_path}")

    # Save summary JSON
    summary = {
        "total_lakes": len(merged),
        "total_with_qa": n_qa,
        "depth_stats": {
            "min": float(depths.min()),
            "p25": float(depths.quantile(0.25)),
            "median": float(depths.median()),
            "mean": float(depths.mean()),
            "p75": float(depths.quantile(0.75)),
            "p95": float(depths.quantile(0.95)),
            "max": float(depths.max()),
        },
        "volume_stats": {
            "total_km3": float(vols.sum()),
            "median_km3": float(vols.median()),
        },
        "qa_distribution": {},
        "mn_lakes": int(mn_mask.sum()),
    }

    if n_qa > 0:
        for val in sorted(qa_valid["QA_RMSE"].unique()):
            summary["qa_distribution"][f"rmse_{int(val)}"] = int((qa_valid["QA_RMSE"] == val).sum())

    summary_path = Path(OUTPUT_DIR) / "task1_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    log.info(f"  Saved summary: {summary_path}")

    elapsed = time.time() - t0
    log.info(f"\n  Task 1 completed in {elapsed/60:.1f} minutes")

    return merged


# ══════════════════════════════════════════════════════════════════════
# TASK 2: Validate Against MN DNR Sonar Data
# ══════════════════════════════════════════════════════════════════════

def task2_validate_mn_sonar(analysis_df=None):
    """
    Match 3D-LAKES lakes to MN DNR sonar lakes by coordinates.
    Compare max_depth estimates.
    """
    log.info("\n" + "=" * 70)
    log.info("TASK 2: Validate 3D-LAKES vs MN DNR Sonar Ground Truth")
    log.info("=" * 70)

    t0 = time.time()

    # ── Load 3D-LAKES data for MN region ──
    if analysis_df is not None:
        lakes_3d = analysis_df.copy()
    else:
        log.info("Loading global dataset...")
        lakes_3d = pd.read_parquet(GLOBAL_PARQUET)

    # Filter to MN region
    mn_3d = lakes_3d[
        (lakes_3d["lon"].between(-97.5, -89.5)) &
        (lakes_3d["lat"].between(43.5, 49.4))
    ].copy()
    log.info(f"  3D-LAKES in MN region: {len(mn_3d):,}")

    # ── Load MN DNR sonar data ──
    log.info("Loading MN DNR sonar data...")
    mn_sonar = pd.read_parquet(MN_SONAR)
    log.info(f"  MN sonar points: {len(mn_sonar):,}")
    log.info(f"  MN sonar lakes: {mn_sonar['lake_id'].nunique()}")

    # Aggregate per lake: max depth, centroid
    # Note: coords in mn_sonar are UTM projected, need to convert
    # Since we can't easily install pyproj, use the sonar_s2 data which has lat/lon
    # OR compute centroids from projected coords and approximate conversion

    # Actually, let's use the sonar_s2_training data which has proper lat/lon
    log.info("Loading sonar_s2 data (with lat/lon)...")
    sonar_s2 = pd.read_parquet("/data/sonar_s2/sonar_s2_training.parquet")
    log.info(f"  sonar_s2 points: {len(sonar_s2):,}, lakes: {sonar_s2['lake_id'].nunique()}")

    # Get max depth per lake from sonar_s2
    sonar_lake_stats = sonar_s2.groupby("lake_id").agg(
        sonar_max_depth=("depth_m", "max"),
        sonar_mean_depth=("depth_m", "mean"),
        sonar_median_depth=("depth_m", "median"),
        sonar_lat=("lat", "mean"),
        sonar_lon=("lon", "mean"),
        n_sonar_pts=("depth_m", "count"),
    ).reset_index()
    log.info(f"  sonar_s2 lake stats: {len(sonar_lake_stats)} lakes")

    # ALSO get max depths from the full mn_sonar data (more lakes)
    mn_sonar_agg = mn_sonar.groupby("lake_id").agg(
        sonar_max_depth_full=("depth_m", "max"),
        n_sonar_pts_full=("depth_m", "count"),
    ).reset_index()

    # For the full sonar data, we need coordinates. Approximate UTM→WGS84
    # MN is roughly UTM zone 15N:
    # lat ≈ northing / 111320
    # lon ≈ -93 + (easting - 500000) / (111320 * cos(lat_rad))
    # This is rough but OK for nearest-neighbor matching
    mn_sonar_centroids = mn_sonar.groupby("lake_id").agg(
        mean_northing=("lat", "mean"),
        mean_easting=("lon", "mean"),
    ).reset_index()

    # Approximate conversion (UTM 15N)
    mn_sonar_centroids["sonar_lat_approx"] = mn_sonar_centroids["mean_northing"] / 111320.0
    mn_sonar_centroids["sonar_lon_approx"] = -93.0 + (
        mn_sonar_centroids["mean_easting"] - 500000.0
    ) / (111320.0 * np.cos(np.radians(46.0)))  # ~46°N for MN center

    mn_sonar_full = mn_sonar_agg.merge(mn_sonar_centroids[["lake_id", "sonar_lat_approx", "sonar_lon_approx"]], on="lake_id")
    log.info(f"  Full MN sonar: {len(mn_sonar_full)} lakes with approximate coords")

    # ── Method A: Match using sonar_s2 (has exact lat/lon, 181 lakes) ──
    log.info("\n--- Method A: Match 3D-LAKES to sonar_s2 lakes (exact lat/lon) ---")

    from scipy.spatial import cKDTree

    # Build KD-tree of 3D-LAKES MN coordinates
    tree_3d = cKDTree(mn_3d[["lat", "lon"]].values)

    # Query for each sonar lake
    matches_a = []
    for _, row in sonar_lake_stats.iterrows():
        dist, idx = tree_3d.query([row["sonar_lat"], row["sonar_lon"]])
        # Convert dist to approximate km (at ~46°N latitude)
        dist_km = dist * 111.0  # rough deg→km
        if dist_km < 1.0:  # within 1km
            match_row = mn_3d.iloc[idx]
            matches_a.append({
                "lake_id": row["lake_id"],
                "sonar_max_depth": row["sonar_max_depth"],
                "sonar_mean_depth": row["sonar_mean_depth"],
                "sonar_lat": row["sonar_lat"],
                "sonar_lon": row["sonar_lon"],
                "n_sonar_pts": row["n_sonar_pts"],
                "hylak_id": match_row["hylak_id"],
                "threeDL_max_depth": match_row["max_depth_m"] if "max_depth_m" in match_row.index else match_row.get("elev_range_m", np.nan),
                "threeDL_lat": match_row["lat"],
                "threeDL_lon": match_row["lon"],
                "n_ae_points": match_row["n_ae_points"],
                "dist_km": dist_km,
            })

    matches_a_df = pd.DataFrame(matches_a)
    log.info(f"  Matched: {len(matches_a_df)} of {len(sonar_lake_stats)} sonar_s2 lakes")

    if len(matches_a_df) > 0:
        # Compute error metrics
        y_true = matches_a_df["sonar_max_depth"].values
        y_pred = matches_a_df["threeDL_max_depth"].values
        valid = np.isfinite(y_true) & np.isfinite(y_pred) & (y_true > 0)
        y_true = y_true[valid]
        y_pred = y_pred[valid]

        rmse = np.sqrt(np.mean((y_pred - y_true)**2))
        mae = np.mean(np.abs(y_pred - y_true))
        bias = np.mean(y_pred - y_true)
        ss_res = np.sum((y_pred - y_true)**2)
        ss_tot = np.sum((y_true - y_true.mean())**2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
        mape = np.mean(np.abs(y_pred - y_true) / y_true) * 100

        log.info(f"\n  ┌─────────────────────────────────────────┐")
        log.info(f"  │ 3D-LAKES vs Sonar (Method A, N={len(y_true):,}) │")
        log.info(f"  ├─────────────────────────────────────────┤")
        log.info(f"  │ RMSE:    {rmse:8.2f}m                    │")
        log.info(f"  │ MAE:     {mae:8.2f}m                    │")
        log.info(f"  │ Bias:    {bias:8.2f}m                    │")
        log.info(f"  │ R²:      {r2:8.4f}                     │")
        log.info(f"  │ MAPE:    {mape:7.1f}%                    │")
        log.info(f"  └─────────────────────────────────────────┘")

        # Error by depth range
        log.info("\n  Error by sonar depth range:")
        for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 50), (50, 100)]:
            mask = (y_true >= lo) & (y_true < hi)
            if mask.sum() > 0:
                rmse_bin = np.sqrt(np.mean((y_pred[mask] - y_true[mask])**2))
                mae_bin = np.mean(np.abs(y_pred[mask] - y_true[mask]))
                bias_bin = np.mean(y_pred[mask] - y_true[mask])
                log.info(f"    {lo:>3}-{hi:<3}m (N={mask.sum():>3}): RMSE={rmse_bin:.2f}m, MAE={mae_bin:.2f}m, Bias={bias_bin:+.2f}m")

        # Worst misses
        err = np.abs(y_pred - y_true)
        worst_idx = np.argsort(err)[-5:]
        log.info("\n  Worst 5 misses:")
        matched_valid = matches_a_df[valid].reset_index(drop=True)
        for i in reversed(worst_idx):
            r = matched_valid.iloc[i]
            log.info(f"    Lake {r['lake_id']}: sonar={r['sonar_max_depth']:.1f}m, "
                     f"3DL={r['threeDL_max_depth']:.1f}m, "
                     f"err={abs(r['sonar_max_depth']-r['threeDL_max_depth']):.1f}m, "
                     f"n_ae={r['n_ae_points']}")

    # ── Method B: Match using full MN sonar (approximate coords, 1948 lakes) ──
    log.info("\n--- Method B: Match 3D-LAKES to full MN sonar (approx coords, ~1948 lakes) ---")

    matches_b = []
    for _, row in mn_sonar_full.iterrows():
        dist, idx = tree_3d.query([row["sonar_lat_approx"], row["sonar_lon_approx"]])
        dist_km = dist * 111.0
        if dist_km < 2.0:  # slightly wider threshold for approximate coords
            match_row = mn_3d.iloc[idx]
            matches_b.append({
                "lake_id": row["lake_id"],
                "sonar_max_depth": row["sonar_max_depth_full"],
                "n_sonar_pts": row["n_sonar_pts_full"],
                "hylak_id": match_row["hylak_id"],
                "threeDL_max_depth": match_row["max_depth_m"] if "max_depth_m" in match_row.index else match_row.get("elev_range_m", np.nan),
                "n_ae_points": match_row["n_ae_points"],
                "dist_km": dist_km,
            })

    matches_b_df = pd.DataFrame(matches_b)
    log.info(f"  Matched: {len(matches_b_df)} of {len(mn_sonar_full)} full sonar lakes")

    if len(matches_b_df) > 0:
        y_true_b = matches_b_df["sonar_max_depth"].values
        y_pred_b = matches_b_df["threeDL_max_depth"].values
        valid_b = np.isfinite(y_true_b) & np.isfinite(y_pred_b) & (y_true_b > 0) & (y_pred_b > 0)
        y_true_b = y_true_b[valid_b]
        y_pred_b = y_pred_b[valid_b]

        if len(y_true_b) > 0:
            rmse_b = np.sqrt(np.mean((y_pred_b - y_true_b)**2))
            mae_b = np.mean(np.abs(y_pred_b - y_true_b))
            bias_b = np.mean(y_pred_b - y_true_b)
            ss_res_b = np.sum((y_pred_b - y_true_b)**2)
            ss_tot_b = np.sum((y_true_b - y_true_b.mean())**2)
            r2_b = 1.0 - ss_res_b / ss_tot_b if ss_tot_b > 0 else np.nan
            mape_b = np.mean(np.abs(y_pred_b - y_true_b) / y_true_b) * 100

            log.info(f"\n  ┌─────────────────────────────────────────────┐")
            log.info(f"  │ 3D-LAKES vs Full Sonar (Method B, N={len(y_true_b):,}) │")
            log.info(f"  ├─────────────────────────────────────────────┤")
            log.info(f"  │ RMSE:    {rmse_b:8.2f}m                        │")
            log.info(f"  │ MAE:     {mae_b:8.2f}m                        │")
            log.info(f"  │ Bias:    {bias_b:8.2f}m                        │")
            log.info(f"  │ R²:      {r2_b:8.4f}                         │")
            log.info(f"  │ MAPE:    {mape_b:7.1f}%                        │")
            log.info(f"  └─────────────────────────────────────────────┘")

            # Error by depth range
            log.info("\n  Error by sonar depth range:")
            for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 50), (50, 100)]:
                mask = (y_true_b >= lo) & (y_true_b < hi)
                if mask.sum() > 0:
                    rmse_bin = np.sqrt(np.mean((y_pred_b[mask] - y_true_b[mask])**2))
                    bias_bin = np.mean(y_pred_b[mask] - y_true_b[mask])
                    log.info(f"    {lo:>3}-{hi:<3}m (N={mask.sum():>3}): RMSE={rmse_bin:.2f}m, Bias={bias_bin:+.2f}m")

            # How many 3D-LAKES are severely underestimating?
            underest = (y_pred_b < y_true_b * 0.5).sum()
            overest = (y_pred_b > y_true_b * 2.0).sum()
            log.info(f"\n  Severe errors:")
            log.info(f"    Under-estimated by >50%: {underest} ({underest/len(y_true_b)*100:.1f}%)")
            log.info(f"    Over-estimated by >100%: {overest} ({overest/len(y_true_b)*100:.1f}%)")

    # ── Save validation results ──
    output_path = Path(OUTPUT_DIR) / "task2_validation.parquet"
    if len(matches_b_df) > 0:
        matches_b_df.to_parquet(output_path, index=False)
    log.info(f"\n  Saved validation: {output_path}")

    # Save validation summary
    val_summary = {
        "method_a": {
            "n_matched": len(matches_a_df),
            "rmse": float(rmse) if len(matches_a_df) > 0 else None,
            "mae": float(mae) if len(matches_a_df) > 0 else None,
            "r2": float(r2) if len(matches_a_df) > 0 else None,
        },
        "method_b": {
            "n_matched": len(matches_b_df),
            "rmse": float(rmse_b) if len(matches_b_df) > 0 and len(y_true_b) > 0 else None,
            "mae": float(mae_b) if len(matches_b_df) > 0 and len(y_true_b) > 0 else None,
            "r2": float(r2_b) if len(matches_b_df) > 0 and len(y_true_b) > 0 else None,
        },
    }
    summary_path = Path(OUTPUT_DIR) / "task2_summary.json"
    with open(summary_path, "w") as f:
        json.dump(val_summary, f, indent=2)

    elapsed = time.time() - t0
    log.info(f"\n  Task 2 completed in {elapsed/60:.1f} minutes")

    return matches_b_df


# ══════════════════════════════════════════════════════════════════════
# TASK 3: Generate Bathymetric Contour Maps from A-E Curves
# ══════════════════════════════════════════════════════════════════════

def task3_generate_contours(n_demo=20):
    """
    Generate depth contour data from A-E curves.

    The A-E curve gives: at each elevation, the water surface area.
    Depth = max_elevation - elevation.
    Contour rings: each depth level corresponds to an area.
    The fraction of total area at each depth level gives concentric rings.

    For lake mapping, we need:
    - Depth contour lines (isobaths): depth → fraction of lake area enclosed
    - This is the "papercut contour" visualization

    Output: JSON with per-lake contour profiles that can be rendered on a map.
    """
    log.info("\n" + "=" * 70)
    log.info("TASK 3: Generate Bathymetric Contour Profiles from A-E Curves")
    log.info("=" * 70)

    t0 = time.time()

    # Load coordinates
    global_df = pd.read_parquet(GLOBAL_PARQUET)
    coords = global_df[["hylak_id", "lat", "lon"]].drop_duplicates()

    # MN lakes
    mn_coords = coords[
        (coords["lon"].between(-97.5, -89.5)) &
        (coords["lat"].between(43.5, 49.4))
    ]
    log.info(f"  MN lakes in 3D-LAKES: {len(mn_coords):,}")

    # Pick a diverse sample: some deep, some shallow
    mn_depths = global_df[
        (global_df["lon"].between(-97.5, -89.5)) &
        (global_df["lat"].between(43.5, 49.4))
    ].sort_values("max_depth_m", ascending=False)

    # Get top N deepest + some medium + some shallow
    n_each = max(n_demo // 3, 1)
    deep = mn_depths.head(n_each)["hylak_id"].tolist()
    medium_start = len(mn_depths) // 3
    medium = mn_depths.iloc[medium_start:medium_start+n_each]["hylak_id"].tolist()
    shallow = mn_depths.tail(n_each)["hylak_id"].tolist()
    demo_ids = set(deep + medium + shallow)

    log.info(f"  Generating contour profiles for {len(demo_ids)} demo lakes")

    # Process L1 files for demo lakes
    l1_path = Path(L1_DIR)
    contour_profiles = []

    for hylak_id in tqdm(demo_ids, desc="Generating contours"):
        csv_path = l1_path / f"{hylak_id}_L1.csv"
        if not csv_path.exists():
            continue

        try:
            ae = pd.read_csv(csv_path)
            if len(ae) < 3:
                continue

            elev = ae.iloc[:, 0].values
            area = ae.iloc[:, 1].values

            idx = np.argsort(elev)
            elev = elev[idx]
            area = area[idx]

            max_depth = float(elev[-1] - elev[0])
            surface_area = float(area[-1])

            if max_depth <= 0 or surface_area <= 0:
                continue

            # Create depth contours at regular intervals
            n_contours = min(20, len(ae))
            depth_levels = np.linspace(0, max_depth, n_contours)

            # Interpolate area at each depth level
            # depth = max_elev - elev, so elev = max_elev - depth
            contour_elevs = elev[-1] - depth_levels
            contour_areas = np.interp(contour_elevs, elev, area)

            # Fraction of surface area at each depth
            area_fractions = contour_areas / surface_area

            # Get lake coordinates
            lake_row = coords[coords["hylak_id"] == hylak_id]
            lat = float(lake_row["lat"].iloc[0]) if len(lake_row) > 0 else np.nan
            lon = float(lake_row["lon"].iloc[0]) if len(lake_row) > 0 else np.nan

            profile = {
                "hylak_id": int(hylak_id),
                "lat": lat,
                "lon": lon,
                "max_depth_m": max_depth,
                "surface_area_km2": surface_area / 1e6,
                "n_ae_points": len(ae),
                "contours": {
                    "depth_m": depth_levels.tolist(),
                    "area_fraction": area_fractions.tolist(),
                    "area_m2": contour_areas.tolist(),
                },
                # Raw A-E curve for rendering
                "ae_curve": {
                    "elevation_m": elev.tolist(),
                    "area_m2": area.tolist(),
                },
            }
            contour_profiles.append(profile)

        except Exception as e:
            log.warning(f"  Error for {hylak_id}: {e}")

    log.info(f"  Generated {len(contour_profiles)} contour profiles")

    # Save
    output_path = Path(OUTPUT_DIR) / "task3_contour_profiles.json"
    with open(output_path, "w") as f:
        json.dump(contour_profiles, f, indent=2)
    log.info(f"  Saved: {output_path}")

    # ── Now process ALL MN lakes for production use ──
    log.info("\n  Processing ALL MN lakes for production contour data...")
    all_mn_ids = mn_coords["hylak_id"].tolist()

    all_contours = []
    processed = 0
    skipped = 0

    for hylak_id in tqdm(all_mn_ids, desc="All MN contours", mininterval=5):
        csv_path = l1_path / f"{hylak_id}_L1.csv"
        if not csv_path.exists():
            skipped += 1
            continue

        try:
            ae = pd.read_csv(csv_path)
            if len(ae) < 2:
                skipped += 1
                continue

            elev = ae.iloc[:, 0].values
            area = ae.iloc[:, 1].values
            idx = np.argsort(elev)
            elev = elev[idx]
            area = area[idx]

            max_depth = float(elev[-1] - elev[0])
            surface_area = float(area[-1])

            if max_depth <= 0 or surface_area <= 0:
                skipped += 1
                continue

            # Standardized contour levels: every 1m up to 10m, then every 5m
            contour_depths = []
            d = 0
            while d <= max_depth:
                contour_depths.append(d)
                if d < 10:
                    d += 1
                else:
                    d += 5
            contour_depths = np.array(contour_depths)

            contour_elevs = elev[-1] - contour_depths
            contour_areas = np.interp(contour_elevs, elev, area)
            area_fractions = contour_areas / surface_area

            lake_row = coords[coords["hylak_id"] == hylak_id]
            lat = float(lake_row["lat"].iloc[0]) if len(lake_row) > 0 else np.nan
            lon = float(lake_row["lon"].iloc[0]) if len(lake_row) > 0 else np.nan

            all_contours.append({
                "hylak_id": int(hylak_id),
                "lat": lat,
                "lon": lon,
                "max_depth_m": round(max_depth, 2),
                "surface_area_km2": round(surface_area / 1e6, 4),
                "n_contours": len(contour_depths),
                "contour_depths_m": [round(d, 1) for d in contour_depths.tolist()],
                "contour_area_fractions": [round(f, 4) for f in area_fractions.tolist()],
            })
            processed += 1

        except Exception:
            skipped += 1

    log.info(f"  Processed: {processed:,}, Skipped: {skipped:,}")

    # Save compact production format
    prod_path = Path(OUTPUT_DIR) / "mn_contour_profiles.json"
    with open(prod_path, "w") as f:
        json.dump(all_contours, f)
    log.info(f"  Saved MN production contours: {prod_path} ({os.path.getsize(prod_path)/1024:.0f}KB)")

    # Also save as parquet for easier querying
    flat_records = []
    for p in all_contours:
        for i, d in enumerate(p["contour_depths_m"]):
            flat_records.append({
                "hylak_id": p["hylak_id"],
                "lat": p["lat"],
                "lon": p["lon"],
                "max_depth_m": p["max_depth_m"],
                "contour_depth_m": d,
                "area_fraction": p["contour_area_fractions"][i],
            })

    flat_df = pd.DataFrame(flat_records)
    flat_path = Path(OUTPUT_DIR) / "mn_contour_flat.parquet"
    flat_df.to_parquet(flat_path, index=False)
    log.info(f"  Saved flat parquet: {flat_path}")

    elapsed = time.time() - t0
    log.info(f"\n  Task 3 completed in {elapsed/60:.1f} minutes")


# ══════════════════════════════════════════════════════════════════════
# TASK 4: Enhancement Assessment
# ══════════════════════════════════════════════════════════════════════

def task4_enhancement_assessment(analysis_df=None, validation_df=None):
    """
    Assess where 3D-LAKES fails and where we can improve.
    NOT training new models — just analyzing the gaps.
    """
    log.info("\n" + "=" * 70)
    log.info("TASK 4: Enhancement Assessment — Where Can We Improve?")
    log.info("=" * 70)

    # Load data
    if analysis_df is None:
        analysis_path = Path(OUTPUT_DIR) / "task1_analysis.parquet"
        if analysis_path.exists():
            analysis_df = pd.read_parquet(analysis_path)
        else:
            analysis_df = pd.read_parquet(GLOBAL_PARQUET)

    # ── Analyze A-E curve quality ──
    log.info("\n  A-E Curve Quality Analysis:")
    n_points = analysis_df["n_ae_points"]
    log.info(f"    Min AE points:    {n_points.min()}")
    log.info(f"    Median AE points: {n_points.median():.0f}")
    log.info(f"    Mean AE points:   {n_points.mean():.1f}")
    log.info(f"    Max AE points:    {n_points.max()}")

    for threshold in [2, 3, 5, 10, 20, 50]:
        n = (n_points < threshold).sum()
        pct = n / len(n_points) * 100
        log.info(f"    < {threshold} points: {n:>7,} lakes ({pct:.1f}%) — {'POOR' if threshold <= 5 else 'SPARSE'}")

    # ── Identify lakes where 3D-LAKES is weakest ──
    log.info("\n  Where 3D-LAKES is likely weakest:")

    # Low A-E points + high extrapolation
    qa_cols = ["qa_rmse", "qa_nrmse", "qa_extrap"]
    has_qa = all(c in analysis_df.columns for c in qa_cols)

    if has_qa:
        poor_ae = analysis_df[analysis_df["n_ae_points"] < 5]
        log.info(f"    Lakes with <5 AE points: {len(poor_ae):,}")
        if len(poor_ae) > 0:
            poor_qa = poor_ae[poor_ae["qa_rmse"] == 2].shape[0]
            log.info(f"    Of those, QA_RMSE=2 (>2m): {poor_qa:,}")

        extrap = analysis_df[analysis_df["qa_extrap"] == 1]
        log.info(f"    Lakes needing extrapolation: {len(extrap):,}")

    # ── Enhancement opportunities ──
    log.info("\n" + "=" * 70)
    log.info("ENHANCEMENT OPPORTUNITIES")
    log.info("=" * 70)

    log.info("""
    1. WITHIN-LAKE spectral SDB (NOT cross-lake):
       - For lakes with sonar data at some points, use spectral bands to
         interpolate depth WITHIN that specific lake
       - This works because spectral-depth relationship is consistent
         within one waterbody with uniform water properties
       - Does NOT generalize across lakes (as we proved, R² < 0)

    2. ICESat-2 densification:
       - We have 75M ICESat-2 points that could provide additional
         elevation observations around lake shorelines
       - Denser elevation profiles → better A-E curves
       - Especially valuable for lakes with <5 A-E points

    3. Morphometric prior for sparse A-E lakes:
       - For lakes with <5 A-E points, the depth profile is unreliable
       - Use morphometric features (area, perimeter, shape) to predict
         likely depth profile shape
       - Our V15 model already does this: R²=0.723 seen, 0.447 walk-forward

    4. Multi-source fusion:
       - 3D-LAKES A-E profile as primary depth estimate
       - Morphometric model as fallback for sparse lakes
       - ICESat-2 for additional constraints
       - Sonar data where available as ground truth anchor

    5. A-E curve smoothing and gap-filling:
       - Many A-E curves have gaps (discontinuous elevation steps)
       - Monotonic spline fitting could improve volume estimates
       - Physical constraints: area must increase with elevation
    """)

    # ── Quantify improvement potential ──
    if has_qa:
        total = len(analysis_df)
        # Lakes where we could most improve
        improvable = analysis_df[
            (analysis_df["n_ae_points"] < 10) |  # sparse
            (analysis_df["qa_rmse"] == 2) |       # high RMSE
            (analysis_df["qa_extrap"] == 1)        # extrapolated
        ]
        log.info(f"\n  Improvable lakes (sparse or high-error): {len(improvable):,} / {total:,} ({len(improvable)/total*100:.1f}%)")

        # MN-specific
        mn_mask = (analysis_df["lon"].between(-97.5, -89.5)) & (analysis_df["lat"].between(43.5, 49.4))
        mn_improvable = analysis_df[mn_mask & (
            (analysis_df["n_ae_points"] < 10) |
            (analysis_df["qa_rmse"] == 2) |
            (analysis_df["qa_extrap"] == 1)
        )]
        log.info(f"  MN improvable lakes: {len(mn_improvable):,} / {mn_mask.sum():,}")

    # Save enhancement summary
    summary = {
        "total_lakes": len(analysis_df),
        "sparse_ae_lt5": int((n_points < 5).sum()),
        "sparse_ae_lt10": int((n_points < 10).sum()),
        "enhancement_strategies": [
            "within_lake_spectral_sdb",
            "icesat2_densification",
            "morphometric_fallback",
            "multi_source_fusion",
            "ae_curve_smoothing",
        ],
    }
    summary_path = Path(OUTPUT_DIR) / "task4_enhancement.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    log.info(f"\n  Saved: {summary_path}")


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="3D-LAKES Analysis Pipeline")
    parser.add_argument("--task", type=str, default="all",
                        help="Which task: 1, 2, 3, 4, or all")
    args = parser.parse_args()

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    tasks = args.task.lower()
    analysis_df = None
    validation_df = None

    if tasks in ("all", "1"):
        analysis_df = task1_analyze_results()

    if tasks in ("all", "2"):
        validation_df = task2_validate_mn_sonar(analysis_df)

    if tasks in ("all", "3"):
        task3_generate_contours(n_demo=20)

    if tasks in ("all", "4"):
        task4_enhancement_assessment(analysis_df, validation_df)

    log.info("\n" + "=" * 70)
    log.info("ALL TASKS COMPLETE")
    log.info("=" * 70)
    log.info(f"Results saved to: {OUTPUT_DIR}/")

    # List output files
    for f in sorted(Path(OUTPUT_DIR).glob("*")):
        size_kb = f.stat().st_size / 1024
        log.info(f"  {f.name}: {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
