#!/usr/bin/env python3
"""
OpenCatch — Reservoir Monitoring Pipeline
==========================================
End-to-end reservoir bathymetry and storage nowcasting.

Orchestrates the 6 reservoir modules into a single pipeline:
  1. Load/cache NID dam catalog
  2. Per reservoir: fetch flowlines, SWOT WSE, cross-sections, features, A-E, nowcast
  3. Collect features into a DataFrame
  4. Train or predict with the reservoir depth model
  5. Validate against USBR survey A-E tables
  6. Save results (Parquet predictions, JSON metrics, per-reservoir A-E curves)

Usage:
    # Full pipeline for Tier 1 reservoirs
    python run_reservoir_pipeline.py \\
        --nid-catalog /data/reservoir/nid_reservoir_catalog.parquet \\
        --dem-dir /data/dem \\
        --swot-dir /data/swot \\
        --output /data/reservoir/results \\
        --reservoirs tier1 \\
        --mode train

    # Predict mode with trained model
    python run_reservoir_pipeline.py \\
        --nid-catalog /data/reservoir/nid_reservoir_catalog.parquet \\
        --dem-dir /data/dem \\
        --swot-dir /data/swot \\
        --output /data/reservoir/results \\
        --reservoirs 0300175,0300095 \\
        --mode predict

    # Validate against USBR surveys
    python run_reservoir_pipeline.py \\
        --output /data/reservoir/results \\
        --mode validate

Requirements:
    pip install numpy pandas scipy scikit-learn xgboost rasterio shapely \\
               geopandas pyarrow tqdm requests
"""

import argparse
import json
import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm

# -- Logging ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("reservoir_pipeline")

# -- Tier 1 reservoir NID IDs ------------------------------------------------

TIER1_RESERVOIRS = {
    "AZ10307": "Glen Canyon Dam (Lake Powell)",
    "NV10122": "Hoover Dam (Lake Mead)",
    "UT10121": "Flaming Gorge Dam",
    "CA10186": "Shasta Dam",
}

# Unit conversions
FT_TO_M = 0.3048
ACFT_TO_M3 = 1233.48184

# -- Pipeline helpers ---------------------------------------------------------


def resolve_reservoir_ids(
    spec: str,
    nid_catalog: pd.DataFrame,
) -> List[str]:
    """Resolve --reservoirs argument into a list of NID IDs.

    Accepts:
      - "tier1"           -> the 4 Tier 1 reservoirs
      - "all"             -> every row in catalog
      - comma-separated   -> "0300175,0300095"
    """
    if spec.lower() == "tier1":
        ids = list(TIER1_RESERVOIRS.keys())
        log.info(f"Tier 1 reservoirs: {ids}")
        return ids

    if spec.lower() == "all":
        ids = nid_catalog["nid_id"].tolist()
        log.info(f"All reservoirs in catalog: {len(ids)}")
        return ids

    ids = [s.strip() for s in spec.split(",") if s.strip()]
    log.info(f"Explicit reservoir list: {ids}")
    return ids


def load_nid_catalog(catalog_path: Path, output_dir: Path) -> pd.DataFrame:
    """Load or build the NID reservoir catalog.

    If the Parquet catalog exists, read it.  Otherwise, invoke fetch_nid to
    download and process the raw NID CSV.
    """
    if catalog_path.exists():
        df = pd.read_parquet(catalog_path)
        log.info(f"Loaded NID catalog: {len(df)} reservoirs from {catalog_path}")
        return df

    log.info("NID catalog not found — building from scratch via fetch_nid ...")
    from reservoir.fetch_nid import download_nid_csv, parse_nid, add_derived_features

    csv_path = download_nid_csv(output_dir, force=False)
    df = parse_nid(csv_path)
    df = add_derived_features(df)
    df.to_parquet(catalog_path, index=False, engine="pyarrow")
    log.info(f"Built NID catalog: {len(df)} reservoirs -> {catalog_path}")
    return df


def find_dem_path(dem_dir: Path, nid_id: str, lat: float, lon: float) -> Optional[str]:
    """Locate a DEM tile covering the reservoir.

    Checks for: reservoir-specific files, SRTM-style naming (N37W109.tif),
    and 3DEP/USGS naming patterns (USGS_13_n37w111_*.tif).
    """
    import glob as _glob

    # Reservoir-specific DEM
    specific = dem_dir / f"{nid_id}.tif"
    if specific.exists():
        return str(specific)

    # SRTM tile name convention
    lat_prefix = "N" if lat >= 0 else "S"
    lon_prefix = "W" if lon < 0 else "E"
    lat_int = int(abs(lat)) + 1  # SRTM tiles are named by NW corner
    lon_int = int(abs(lon)) + 1
    tile_name = f"{lat_prefix}{lat_int:02d}{lon_prefix}{lon_int:03d}.tif"
    tile_path = dem_dir / tile_name
    if tile_path.exists():
        return str(tile_path)

    # 3DEP / USGS naming uses the integer lat/lon of the tile corner
    # Try both floor and ceil variants since naming conventions differ
    lat_floor = int(abs(lat))
    lon_floor = int(abs(lon))

    for try_lat in [lat_int, lat_floor]:
        for try_lon in [lon_floor, lon_int]:
            lat_str = f"n{try_lat}" if lat >= 0 else f"s{try_lat}"
            lon_str = f"w{try_lon:03d}" if lon < 0 else f"e{try_lon:03d}"
            pattern = f"*{lat_str}{lon_str}*.tif"
            matches = sorted(dem_dir.glob(pattern))
            if matches:
                log.info(f"  Found DEM: {matches[-1].name} (pattern: {pattern})")
                return str(matches[-1])

    # Search subdirs as last resort
    for p in dem_dir.rglob(tile_name):
        return str(p)

    return None


def load_swot_timeseries(
    swot_dir: Path,
    nid_id: str,
    pld_lake_id: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """Load cached SWOT water-surface-elevation time series for a reservoir."""
    # Try by NID ID
    for pattern in [
        f"{nid_id}_swot.parquet",
        f"{nid_id}_swot_timeseries.parquet",
    ]:
        p = swot_dir / pattern
        if p.exists():
            return pd.read_parquet(p)

    # Try by PLD lake ID
    if pld_lake_id:
        for pattern in [
            f"pld_{pld_lake_id}.parquet",
            f"{pld_lake_id}_swot.parquet",
        ]:
            p = swot_dir / pattern
            if p.exists():
                return pd.read_parquet(p)

    return None


# -- Per-reservoir processing -------------------------------------------------


def process_single_reservoir(
    nid_row: pd.Series,
    dem_dir: Path,
    swot_dir: Path,
    output_dir: Path,
    flowline_dir: Path,
) -> Dict:
    """Run the full pipeline for a single reservoir.

    Returns a feature dict (or empty dict on failure).
    """
    nid_id = str(nid_row.get("nid_id", "unknown"))
    dam_name = nid_row.get("dam_name", nid_id)
    lat = nid_row["latitude"]
    lon = nid_row["longitude"]

    log.info(f"--- Processing {dam_name} (NID {nid_id}) ---")
    reservoir_out = output_dir / "reservoirs" / nid_id
    reservoir_out.mkdir(parents=True, exist_ok=True)

    features: Dict = {"nid_id": nid_id, "dam_name": dam_name}

    # ---- Step 2a: Fetch NHDPlus flowlines ------------------------------------
    flowline_metrics = None
    thalweg_geom = None
    try:
        from reservoir.fetch_nhdplus_flowlines import (
            fetch_flowlines_nldi,
            process_flowlines_for_reservoir,
        )

        cache_path = flowline_dir / "geojson" / f"{nid_id}_flowlines.json"
        if cache_path.exists():
            import json as _json

            with open(cache_path) as fh:
                geojson = _json.load(fh)
        else:
            # Try the dam location first, then offset downstream
            # (NLDI can't find comids on reservoir surfaces)
            geojson = fetch_flowlines_nldi(lat, lon)
            if not geojson:
                # Try 0.05 degrees (~5km) south (downstream for most US dams)
                for d_lat, d_lon in [(-.05, 0), (.05, 0), (0, -.05), (0, .05), (-.02, -.02)]:
                    geojson = fetch_flowlines_nldi(lat + d_lat, lon + d_lon)
                    if geojson:
                        log.info(f"  Found flowlines at offset ({d_lat:+.2f}, {d_lon:+.2f})")
                        break
            if geojson:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "w") as fh:
                    json.dump(geojson, fh)

        if geojson:
            result = process_flowlines_for_reservoir(
                geojson, dam_lat=lat, dam_lon=lon,
            )
            flowline_metrics = result.get("metrics")
            thalweg_geom = result.get("thalweg_geometry")
            log.info(f"  Flowlines OK — thalweg {flowline_metrics.get('thalweg_length_m', 0):.0f}m")
        else:
            log.warning(f"  No flowlines for {nid_id}")
    except Exception as exc:
        log.warning(f"  Flowline fetch failed: {exc}")

    # ---- Step 2b: Fetch SWOT WSE time series ---------------------------------
    swot_df = None
    try:
        pld_id = nid_row.get("pld_lake_id")
        swot_df = load_swot_timeseries(swot_dir, nid_id, pld_lake_id=pld_id)
        if swot_df is not None:
            log.info(f"  SWOT data: {len(swot_df)} observations")
        else:
            log.info("  No cached SWOT data — fetching via Hydrocron ...")
            from fetch_swot_data import fetch_hydrocron_lake, parse_hydrocron_response

            if pld_id:
                geojson = fetch_hydrocron_lake(str(pld_id))
                if geojson is not None:
                    swot_df = parse_hydrocron_response(geojson)
                    if swot_df is not None and len(swot_df) > 0:
                        swot_df.to_parquet(
                            swot_dir / f"{nid_id}_swot.parquet",
                            index=False, engine="pyarrow",
                        )
                    log.info(f"  Fetched & cached {len(swot_df)} SWOT obs")
    except Exception as exc:
        log.warning(f"  SWOT fetch failed: {exc}")

    # ---- Step 2c: Cross-section extraction -----------------------------------
    extraction_result = None
    try:
        dem_path = find_dem_path(dem_dir, nid_id, lat, lon)
        if dem_path is None:
            log.warning(f"  No DEM tile found for {nid_id} at ({lat:.2f}, {lon:.2f})")
        elif thalweg_geom is not None:
            from reservoir.cross_section_extractor import ReservoirCrossSectionExtractor

            # Build a rough reservoir polygon from surface area (circle approx)
            area_km2 = nid_row.get("surface_area_km2", np.nan)
            reservoir_polygon = _make_approx_polygon(lat, lon, area_km2)

            dam_height_m = nid_row.get("dam_height_m", 30.0)
            wse_m = nid_row.get("crest_elevation_m", np.nan)
            if np.isnan(wse_m):
                wse_m = 1000.0  # fallback
            wse_m *= 0.95  # approximate current pool below crest

            extractor = ReservoirCrossSectionExtractor(
                dem_path=dem_path,
                reservoir_polygon=reservoir_polygon,
                thalweg_line=thalweg_geom,
                dam_location=(lon, lat),
                dam_height_m=dam_height_m if np.isfinite(dam_height_m) else 30.0,
                water_surface_elevation_m=wse_m,
            )
            extraction_result = extractor.extract(reservoir_id=nid_id)
            log.info(
                f"  Cross-sections: {extraction_result.n_valid_sections} valid, "
                f"mean R²={extraction_result.mean_fit_r2:.3f}"
            )

            # Save A-E curve from cross-sections
            ae_df = extractor.result_to_ae_curve(extraction_result)
            ae_path = reservoir_out / f"{nid_id}_ae_xsection.parquet"
            ae_df.to_parquet(ae_path, index=False, engine="pyarrow")
        else:
            log.warning(f"  Skipping cross-sections — no thalweg geometry")
    except Exception as exc:
        log.warning(f"  Cross-section extraction failed: {exc}")

    # ---- Step 2d: Compute reservoir features ---------------------------------
    try:
        from reservoir.reservoir_features import compute_reservoir_features

        feat = compute_reservoir_features(
            nid_row,
            extraction_result=extraction_result,
            swot_df=swot_df,
            flowline_metrics=flowline_metrics,
        )
        features.update(feat)
        log.info(f"  Features: {len(feat)} computed")
    except Exception as exc:
        log.warning(f"  Feature computation failed: {exc}")

    # ---- Step 2e: Build enhanced A-E curve -----------------------------------
    try:
        from build_enhanced_ae import build_enhanced_ae_curve

        ae_dir = reservoir_out
        ae_result = build_enhanced_ae_curve(
            swot_df=swot_df,
            output_dir=ae_dir,
            lake_id=nid_id,
        )
        if ae_result is not None:
            log.info(f"  Enhanced A-E built — {len(ae_result)} points")
    except Exception as exc:
        log.debug(f"  Enhanced A-E build skipped: {exc}")

    # ---- Step 2f: Storage nowcasting -----------------------------------------
    try:
        from reservoir.storage_nowcast import StorageNowcaster

        # Look for A-E curve
        ae_path = reservoir_out / f"{nid_id}_ae_xsection.parquet"
        if ae_path.exists():
            ae_df = pd.read_parquet(ae_path)
            elevs = ae_df["elevation_m"].values
            areas = ae_df["area_m2"].values if "area_m2" in ae_df.columns else ae_df["area_km2"].values * 1e6

            max_storage = nid_row.get("max_storage_m3", np.nan)
            nowcaster = StorageNowcaster(elevs, areas, max_storage)

            current_elev = nid_row.get("crest_elevation_m", np.nan)
            if np.isfinite(current_elev):
                current_elev *= 0.95
                nowcast = nowcaster.nowcast(current_elev)
                features.update({
                    f"nowcast_{k}": v for k, v in nowcast.items()
                })
                log.info(
                    f"  Nowcast: {nowcast.get('percent_full', 0):.1f}% full, "
                    f"{nowcast.get('current_storage_acft', 0):,.0f} ac-ft"
                )

                # Save nowcast JSON
                nowcast_path = reservoir_out / f"{nid_id}_nowcast.json"
                with open(nowcast_path, "w") as fh:
                    json.dump(
                        {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v)
                         for k, v in nowcast.items()},
                        fh, indent=2,
                    )
    except Exception as exc:
        log.warning(f"  Storage nowcast failed: {exc}")

    return features


def _make_approx_polygon(lat: float, lon: float, area_km2: float):
    """Create an approximate circular polygon for the reservoir."""
    from shapely.geometry import Point

    if not np.isfinite(area_km2) or area_km2 <= 0:
        area_km2 = 10.0  # default 10 km²

    radius_km = np.sqrt(area_km2 / np.pi)
    radius_deg = radius_km / 111.0
    return Point(lon, lat).buffer(radius_deg, resolution=32)


# -- Validation against USBR surveys -----------------------------------------


def validate_against_usbr(
    output_dir: Path,
    survey_dir: Optional[Path] = None,
) -> Dict:
    """Compare pipeline A-E curves to USBR published survey tables.

    Returns a dict of per-reservoir validation metrics.
    """
    from reservoir.fetch_usbr_surveys import USBR_SURVEY_CATALOG

    metrics = {}
    for survey in USBR_SURVEY_CATALOG:
        nid_id = survey["nid_id"]
        name = survey["reservoir_name"]

        # Find our predicted A-E
        pred_path = output_dir / "reservoirs" / nid_id / f"{nid_id}_ae_xsection.parquet"
        if not pred_path.exists():
            continue

        # Find USBR truth A-E
        name_safe = name.replace(" ", "_").lower()
        truth_path = None
        if survey_dir:
            for candidate in [
                survey_dir / f"{name_safe}_ae_table.parquet",
                survey_dir / f"{nid_id}_ae_table.parquet",
            ]:
                if candidate.exists():
                    truth_path = candidate
                    break

        if truth_path is None:
            log.info(f"  No USBR truth table for {name} — skipping validation")
            continue

        try:
            pred_df = pd.read_parquet(pred_path)
            truth_df = pd.read_parquet(truth_path)

            # Align on elevation and compare volumes
            truth_elevs = truth_df["elevation_m"].values
            truth_vols = truth_df["capacity_m3"].values if "capacity_m3" in truth_df.columns else None

            if truth_vols is None:
                continue

            # Interpolate our predictions to truth elevations
            pred_elevs = pred_df["elevation_m"].values
            pred_vols = pred_df["volume_m3"].values if "volume_m3" in pred_df.columns else None
            if pred_vols is None:
                continue

            # Only compare within overlapping elevation range
            e_min = max(truth_elevs.min(), pred_elevs.min())
            e_max = min(truth_elevs.max(), pred_elevs.max())
            mask = (truth_elevs >= e_min) & (truth_elevs <= e_max)

            if mask.sum() < 3:
                continue

            pred_interp = np.interp(truth_elevs[mask], pred_elevs, pred_vols)
            truth_interp = truth_vols[mask]

            # Metrics
            residuals = pred_interp - truth_interp
            rmse = np.sqrt(np.mean(residuals ** 2))
            mae = np.mean(np.abs(residuals))
            mape = np.mean(np.abs(residuals) / np.clip(np.abs(truth_interp), 1.0, None)) * 100

            ss_res = np.sum(residuals ** 2)
            ss_tot = np.sum((truth_interp - np.mean(truth_interp)) ** 2)
            r2 = 1.0 - ss_res / max(ss_tot, 1e-10)

            metrics[nid_id] = {
                "reservoir_name": name,
                "n_points": int(mask.sum()),
                "rmse_m3": float(rmse),
                "mae_m3": float(mae),
                "mape_pct": float(mape),
                "r2": float(r2),
                "rmse_acft": float(rmse / ACFT_TO_M3),
                "max_volume_error_pct": float(
                    np.abs(pred_interp[-1] - truth_interp[-1])
                    / max(abs(truth_interp[-1]), 1.0) * 100
                ),
            }
            log.info(
                f"  {name}: R²={r2:.3f}, RMSE={rmse/ACFT_TO_M3:,.0f} ac-ft, "
                f"MAPE={mape:.1f}%"
            )
        except Exception as exc:
            log.warning(f"  Validation failed for {name}: {exc}")

    return metrics


# -- Training -----------------------------------------------------------------


def train_reservoir_depth_model(
    features_df: pd.DataFrame,
    output_dir: Path,
    device: str = "cpu",
) -> Dict:
    """Train an XGBoost model to predict reservoir max depth from features.

    Returns training metrics dict.
    """
    try:
        import xgboost as xgb
        from sklearn.model_selection import KFold
        from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
    except ImportError as exc:
        log.error(f"Training requires xgboost + scikit-learn: {exc}")
        return {}

    target_col = "estimated_max_depth_m"
    if target_col not in features_df.columns:
        # Fall back to dam height as proxy
        target_col = "dam_height_m"
        if target_col not in features_df.columns:
            log.error("No target column found for training")
            return {}

    # Drop rows without target
    df = features_df.dropna(subset=[target_col]).copy()
    if len(df) < 10:
        log.warning(f"Only {len(df)} samples with target — skipping training")
        return {"n_samples": len(df), "status": "insufficient_data"}

    # Select numeric feature columns (exclude identifiers and target)
    exclude = {"nid_id", "dam_name", "state", target_col}
    feat_cols = [
        c for c in df.columns
        if c not in exclude and pd.api.types.is_numeric_dtype(df[c])
    ]
    log.info(f"Training with {len(feat_cols)} features, {len(df)} samples")

    X = df[feat_cols].values
    y = df[target_col].values

    # Replace inf/nan
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # 5-fold CV
    kf = KFold(n_splits=min(5, len(df)), shuffle=True, random_state=42)
    oof_preds = np.full(len(y), np.nan)

    fold_metrics = []
    models = []

    for fold_idx, (train_idx, val_idx) in enumerate(kf.split(X)):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]

        tree_method = "hist"
        if device.startswith("cuda") or device.startswith("gpu"):
            tree_method = "gpu_hist"

        model = xgb.XGBRegressor(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            tree_method=tree_method,
            random_state=42 + fold_idx,
        )
        model.fit(
            X_tr, y_tr,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )
        preds = model.predict(X_val)
        oof_preds[val_idx] = preds
        models.append(model)

        r2 = r2_score(y_val, preds)
        rmse = np.sqrt(mean_squared_error(y_val, preds))
        fold_metrics.append({"fold": fold_idx, "r2": r2, "rmse": rmse})
        log.info(f"  Fold {fold_idx}: R²={r2:.3f}, RMSE={rmse:.2f}m")

    # Overall OOF metrics
    valid_mask = np.isfinite(oof_preds)
    oof_r2 = r2_score(y[valid_mask], oof_preds[valid_mask])
    oof_rmse = np.sqrt(mean_squared_error(y[valid_mask], oof_preds[valid_mask]))
    oof_mae = mean_absolute_error(y[valid_mask], oof_preds[valid_mask])

    log.info(f"  OOF: R²={oof_r2:.3f}, RMSE={oof_rmse:.2f}m, MAE={oof_mae:.2f}m")

    # Save best model (lowest val RMSE fold)
    best_fold = min(range(len(fold_metrics)), key=lambda i: fold_metrics[i]["rmse"])
    model_path = output_dir / "reservoir_depth_model.json"
    models[best_fold].save_model(str(model_path))
    log.info(f"  Saved best model (fold {best_fold}) -> {model_path}")

    # Save feature names
    feat_path = output_dir / "reservoir_depth_features.json"
    with open(feat_path, "w") as fh:
        json.dump(feat_cols, fh, indent=2)

    training_metrics = {
        "n_samples": len(df),
        "n_features": len(feat_cols),
        "oof_r2": float(oof_r2),
        "oof_rmse": float(oof_rmse),
        "oof_mae": float(oof_mae),
        "fold_metrics": fold_metrics,
        "target": target_col,
        "feature_columns": feat_cols,
        "model_path": str(model_path),
    }
    return training_metrics


# -- Prediction ---------------------------------------------------------------


def predict_reservoir_depths(
    features_df: pd.DataFrame,
    output_dir: Path,
) -> pd.DataFrame:
    """Load trained model and predict max depth for each reservoir."""
    try:
        import xgboost as xgb
    except ImportError:
        log.error("xgboost required for prediction")
        return features_df

    model_path = output_dir / "reservoir_depth_model.json"
    feat_path = output_dir / "reservoir_depth_features.json"

    if not model_path.exists():
        log.error(f"No trained model found at {model_path} — run with --mode train first")
        return features_df

    model = xgb.XGBRegressor()
    model.load_model(str(model_path))

    with open(feat_path) as fh:
        feat_cols = json.load(fh)

    # Ensure all feature columns exist
    for col in feat_cols:
        if col not in features_df.columns:
            features_df[col] = 0.0

    X = features_df[feat_cols].values
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    preds = model.predict(X)
    features_df["predicted_max_depth_m"] = preds
    log.info(f"Predicted depths for {len(features_df)} reservoirs: "
             f"mean={np.mean(preds):.1f}m, max={np.max(preds):.1f}m")

    return features_df


# -- B2 upload ----------------------------------------------------------------


def upload_to_b2(output_dir: Path) -> bool:
    """Upload results to Backblaze B2 if credentials are available."""
    try:
        import subprocess

        result = subprocess.run(
            ["b2", "sync", "--replaceNewer",
             str(output_dir), "b2://opencatch-reservoir/results/"],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0:
            log.info("Uploaded results to B2")
            return True
        else:
            log.warning(f"B2 upload failed: {result.stderr}")
            return False
    except FileNotFoundError:
        log.info("b2 CLI not found — skipping upload")
        return False
    except Exception as exc:
        log.warning(f"B2 upload error: {exc}")
        return False


# -- Main pipeline ------------------------------------------------------------


def run_pipeline(args: argparse.Namespace) -> None:
    """Execute the end-to-end reservoir pipeline."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    dem_dir = Path(args.dem_dir) if args.dem_dir else output_dir / "dem"
    swot_dir = Path(args.swot_dir) if args.swot_dir else output_dir / "swot"
    flowline_dir = output_dir / "flowlines"

    dem_dir.mkdir(parents=True, exist_ok=True)
    swot_dir.mkdir(parents=True, exist_ok=True)
    flowline_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log.info(f"=== Reservoir Pipeline — {timestamp} ===")
    log.info(f"  Mode:     {args.mode}")
    log.info(f"  Output:   {output_dir}")
    log.info(f"  DEM dir:  {dem_dir}")
    log.info(f"  SWOT dir: {swot_dir}")

    # ---- Step 1: Load NID catalog -------------------------------------------
    catalog_path = Path(args.nid_catalog) if args.nid_catalog else output_dir / "nid_reservoir_catalog.parquet"
    nid_catalog = load_nid_catalog(catalog_path, output_dir)

    # Resolve target reservoirs
    reservoir_ids = resolve_reservoir_ids(args.reservoirs, nid_catalog)
    target_df = nid_catalog[nid_catalog["nid_id"].isin(reservoir_ids)].copy()

    if len(target_df) == 0:
        log.warning(f"No matching reservoirs in catalog for: {reservoir_ids}")
        # For tier1 with known names, proceed anyway with minimal info
        if args.reservoirs.lower() == "tier1":
            tier1_coords = {
                "AZ10307": (36.9379, -111.4842),  # Glen Canyon / Lake Powell
                "NV10122": (36.0163, -114.7374),   # Hoover / Lake Mead
                "UT10121": (40.9149, -109.4219),   # Flaming Gorge
                "CA10186": (40.7186, -122.4192),   # Shasta
            }
            rows = []
            for nid_id, name in TIER1_RESERVOIRS.items():
                lat, lon = tier1_coords.get(nid_id, (37.0, -111.0))
                rows.append({"nid_id": nid_id, "dam_name": name, "latitude": lat, "longitude": lon})
            target_df = pd.DataFrame(rows)
            log.info(f"Using built-in Tier 1 metadata ({len(target_df)} reservoirs)")
        else:
            log.error("Aborting — no reservoirs to process")
            return

    log.info(f"Processing {len(target_df)} reservoirs")

    # ---- Step 2: Per-reservoir processing ------------------------------------
    all_features = []
    errors = {}
    t0 = time.time()

    for idx, (_, nid_row) in enumerate(tqdm(
        target_df.iterrows(),
        total=len(target_df),
        desc="Reservoirs",
    )):
        nid_id = str(nid_row.get("nid_id", f"res_{idx}"))
        try:
            feat = process_single_reservoir(
                nid_row, dem_dir, swot_dir, output_dir, flowline_dir,
            )
            if feat:
                all_features.append(feat)
        except Exception as exc:
            log.error(f"FAILED {nid_id}: {exc}")
            errors[nid_id] = traceback.format_exc()

    elapsed = time.time() - t0
    log.info(f"Processed {len(all_features)}/{len(target_df)} reservoirs "
             f"in {elapsed:.1f}s ({len(errors)} errors)")

    # ---- Step 3: Collect features into DataFrame ----------------------------
    if not all_features:
        log.error("No features computed — nothing to save")
        return

    features_df = pd.DataFrame(all_features)
    features_path = output_dir / "reservoir_features.parquet"
    features_df.to_parquet(features_path, index=False, engine="pyarrow")
    log.info(f"Saved features: {features_df.shape} -> {features_path}")

    # ---- Step 3b: Merge USBR survey targets if available --------------------
    usbr_dir = output_dir / "usbr_surveys"
    usbr_combined = usbr_dir / "all_tier1_ae.parquet"
    if usbr_combined.exists():
        try:
            usbr_df = pd.read_parquet(usbr_combined)
            # Get max depth per reservoir from USBR A-E tables
            usbr_targets = (
                usbr_df.groupby("nid_id")["max_depth_m"]
                .first()
                .reset_index()
                .rename(columns={"max_depth_m": "usbr_max_depth_m"})
            )
            features_df = features_df.merge(usbr_targets, on="nid_id", how="left")
            # Use USBR max depth as the primary target (better than dam_height)
            if "usbr_max_depth_m" in features_df.columns:
                features_df["estimated_max_depth_m"] = features_df["usbr_max_depth_m"]
                n_targets = features_df["estimated_max_depth_m"].notna().sum()
                log.info(f"Merged USBR targets: {n_targets} reservoirs with max_depth ground truth")
        except Exception as e:
            log.warning(f"Could not load USBR targets: {e}")

    # ---- Step 4/5: Train or predict -----------------------------------------
    training_metrics = {}
    if args.mode == "train":
        log.info("=== Training reservoir depth model ===")
        training_metrics = train_reservoir_depth_model(
            features_df, output_dir, device=args.device,
        )
        # Also generate predictions on the training set
        if training_metrics.get("model_path"):
            features_df = predict_reservoir_depths(features_df, output_dir)

    elif args.mode == "predict":
        log.info("=== Predicting reservoir depths ===")
        features_df = predict_reservoir_depths(features_df, output_dir)

    # ---- Step 6: Validate against USBR surveys ------------------------------
    validation_metrics = {}
    if args.mode in ("train", "validate"):
        log.info("=== Validating against USBR surveys ===")
        survey_dir = output_dir / "surveys"
        validation_metrics = validate_against_usbr(output_dir, survey_dir)

    # ---- Step 7: Save results -----------------------------------------------
    log.info("=== Saving final results ===")

    # Predictions Parquet
    pred_path = output_dir / "reservoir_predictions.parquet"
    features_df.to_parquet(pred_path, index=False, engine="pyarrow")
    log.info(f"  Predictions: {pred_path}")

    # Metrics JSON
    metrics = {
        "timestamp": timestamp,
        "mode": args.mode,
        "n_reservoirs_processed": len(all_features),
        "n_reservoirs_requested": len(target_df),
        "n_errors": len(errors),
        "elapsed_seconds": elapsed,
        "training_metrics": training_metrics,
        "validation_metrics": validation_metrics,
        "errors": {k: str(v)[:500] for k, v in errors.items()},
    }
    metrics_path = output_dir / "reservoir_metrics.json"
    with open(metrics_path, "w") as fh:
        json.dump(metrics, fh, indent=2, default=str)
    log.info(f"  Metrics: {metrics_path}")

    # ---- Step 8: Upload to B2 -----------------------------------------------
    if not args.no_upload:
        upload_to_b2(output_dir)

    # ---- Summary ------------------------------------------------------------
    log.info("=" * 60)
    log.info("PIPELINE COMPLETE")
    log.info(f"  Reservoirs processed: {len(all_features)}/{len(target_df)}")
    log.info(f"  Errors:              {len(errors)}")
    log.info(f"  Elapsed:             {elapsed:.1f}s")
    if training_metrics.get("oof_r2"):
        log.info(f"  Training OOF R²:     {training_metrics['oof_r2']:.3f}")
        log.info(f"  Training OOF RMSE:   {training_metrics['oof_rmse']:.2f}m")
    if validation_metrics:
        avg_r2 = np.mean([m["r2"] for m in validation_metrics.values()])
        avg_mape = np.mean([m["mape_pct"] for m in validation_metrics.values()])
        log.info(f"  Validation avg R²:   {avg_r2:.3f}")
        log.info(f"  Validation avg MAPE: {avg_mape:.1f}%")
    log.info(f"  Output directory:    {output_dir}")
    log.info("=" * 60)


# -- CLI ----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch — Reservoir Monitoring Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Train on Tier 1 reservoirs
  python run_reservoir_pipeline.py \\
      --nid-catalog /data/reservoir/nid_reservoir_catalog.parquet \\
      --dem-dir /data/dem --swot-dir /data/swot \\
      --output /data/reservoir/results \\
      --reservoirs tier1 --mode train

  # Predict for specific reservoirs
  python run_reservoir_pipeline.py \\
      --output /data/reservoir/results \\
      --reservoirs 0300175,0300095 --mode predict

  # Validate only (assumes previous run produced A-E curves)
  python run_reservoir_pipeline.py \\
      --output /data/reservoir/results --mode validate
""",
    )
    parser.add_argument(
        "--nid-catalog", type=str, default=None,
        help="Path to NID reservoir catalog Parquet (built if missing)",
    )
    parser.add_argument(
        "--dem-dir", type=str, default=None,
        help="Directory containing DEM tiles (SRTM/3DEP GeoTIFFs)",
    )
    parser.add_argument(
        "--swot-dir", type=str, default=None,
        help="Directory for SWOT time series cache",
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output directory for all results",
    )
    parser.add_argument(
        "--mode", type=str, default="train",
        choices=["train", "predict", "validate"],
        help="Pipeline mode (default: train)",
    )
    parser.add_argument(
        "--reservoirs", type=str, default="tier1",
        help="Comma-separated NID IDs, or 'tier1' / 'all' (default: tier1)",
    )
    parser.add_argument(
        "--device", type=str, default="cpu",
        help="Compute device: cpu, cuda, gpu (default: cpu)",
    )
    parser.add_argument(
        "--no-upload", action="store_true",
        help="Skip B2 upload step",
    )
    args = parser.parse_args()

    try:
        run_pipeline(args)
    except KeyboardInterrupt:
        log.warning("Pipeline interrupted by user")
        sys.exit(1)
    except Exception as exc:
        log.error(f"Pipeline failed: {exc}")
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
