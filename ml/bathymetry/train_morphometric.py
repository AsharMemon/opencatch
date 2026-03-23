#!/usr/bin/env python3
"""
OpenCatch — GLOBathy-style Morphometric Depth Model

Predicts lake depth from morphometric features ONLY — no spectral data needed.
Inspired by GLOBathy (Khazaei et al., 2022) which showed simple lake morphometry
predicts depth surprisingly well using area-depth relationships.

Available for ALL 510K lakes from HydroLAKES + 3D-LAKES because morphometric
features can be computed from polygons and DEMs without any satellite imagery.

Features:
  - Lake surface area, perimeter, shoreline development factor
  - Shape metrics (circularity, elongation, convexity)
  - Shore slope from surrounding DEM
  - Location (lat, lon, elevation), climate zone proxies
  - Lake type (natural vs reservoir from HydroLAKES)
  - Watershed area, volume development factor

Models:
  1. XGBoost (best for tabular)
  2. Random Forest
  3. Linear regression (baseline)
  4. KAN (Kolmogorov-Arnold Network)

Usage:
    python train_morphometric.py \
        --data /data/hydrolakes_3dlakes_merged.parquet \
        --output /data/models/morphometric \
        --device cuda

Requirements:
    pip install xgboost scikit-learn pandas pyarrow numpy torch efficient-kan
"""

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("morphometric")

EPS = 1e-8


# ── Feature Engineering ──────────────────────────────────────────────

# HydroLAKES column mapping (adjust to actual schema)
HYDROLAKES_COLS = {
    "lake_area":  "Lake_area",      # km²
    "shore_len":  "Shore_len",      # km
    "vol_total":  "Vol_total",      # mcm
    "depth_avg":  "Depth_avg",      # m (GLOBathy estimated)
    "elevation":  "Elevation",      # m.a.s.l.
    "slope_100":  "Slope_100",      # shore slope, 100m buffer
    "dis_avg":    "Dis_avg",        # average discharge m³/s
    "res_time":   "Res_time",       # residence time (days)
    "hylak_id":   "Hylak_id",
    "lake_type":  "Lake_type",      # 1=natural, 2=reservoir, 3=other
    "pour_lat":   "Pour_lat",
    "pour_long":  "Pour_long",
    "wshd_area":  "Wshd_area",      # watershed area km²
}

# Columns that may come from 3D-LAKES or other depth sources
DEPTH_COLS = ["max_depth_m", "depth_m", "Depth_avg", "depth_max"]

# Expected climate data columns (WorldClim/ERA5 derived, optional)
CLIMATE_COLS = ["mean_annual_temp_c", "mean_annual_precip_mm", "frost_days"]


def compute_morphometric_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all morphometric features for lake depth prediction.

    Follows GLOBathy methodology but adds shape complexity, climate proxies,
    and interaction features. Works with HydroLAKES attribute columns.

    Returns a DataFrame of features with the same index as input.
    """
    feats = pd.DataFrame(index=df.index)

    # ── 1. Basic morphometry ──
    area_km2 = _get_col(df, ["Lake_area", "lake_area_km2", "area_km2", "lake_area_ha"],
                         scale_ha=True)
    perim_km = _get_col(df, ["Shore_len", "shore_len_km", "lake_perim_km", "lake_perim_m"],
                         scale_m=True)

    feats["area_km2"] = area_km2
    feats["log_area"] = np.log1p(area_km2)
    feats["sqrt_area"] = np.sqrt(area_km2.clip(lower=0))
    feats["perim_km"] = perim_km
    feats["log_perim"] = np.log1p(perim_km)

    # ── 2. Shoreline development factor ──
    # SDI = P / (2 * sqrt(pi * A))  — ratio of perimeter to circle of same area
    # SDI = 1.0 for perfect circle, >1 for irregular shoreline
    area_km2_safe = area_km2.clip(lower=EPS)
    feats["sdi"] = perim_km / (2 * np.sqrt(np.pi * area_km2_safe))

    # ── 3. Shape metrics ──
    # Circularity (4*pi*A / P²) — 1.0 for circle, <1 for irregular
    feats["circularity"] = (4 * np.pi * area_km2_safe) / (perim_km ** 2 + EPS)

    # Elongation ratio approximation: assume ellipse, SDI -> elongation
    # For an ellipse: SDI ≈ pi * (a+b) / (2 * sqrt(pi * a * b))
    # Simplified: elongation ~ (SDI)² as higher SDI = more elongated
    feats["elongation"] = feats["sdi"] ** 2

    # Convexity proxy (from SDI): closer to 1 = more convex
    feats["convexity"] = 1.0 / feats["sdi"].clip(lower=1.0)

    # Fractal dimension approximation: D = 2 * ln(P/4) / ln(A)
    # Measures shoreline complexity
    with np.errstate(divide="ignore", invalid="ignore"):
        feats["fractal_dim"] = 2 * np.log(perim_km.clip(lower=EPS) / 4) / np.log(
            area_km2_safe
        )
    feats["fractal_dim"] = feats["fractal_dim"].replace([np.inf, -np.inf], np.nan)

    # ── 4. Shore slope ──
    slope = _get_col(df, ["Slope_100", "slope_100", "shore_slope_deg"])
    feats["shore_slope"] = slope
    feats["log_shore_slope"] = np.log1p(slope.clip(lower=0))

    # ── 5. Location and elevation ──
    lat = _get_col(df, ["Pour_lat", "lake_lat", "lat", "latitude", "centroid_lat"])
    lon = _get_col(df, ["Pour_long", "lake_lon", "lon", "longitude", "centroid_lon"])
    elev = _get_col(df, ["Elevation", "lake_elevation_m", "elevation_m", "elev"])

    feats["lat"] = lat
    feats["lon"] = lon
    feats["abs_lat"] = np.abs(lat)
    feats["elevation_m"] = elev
    feats["log_elevation"] = np.log1p(elev.clip(lower=0))

    # ── 6. Climate proxies ──
    # If actual climate data available, use it; otherwise approximate from lat/elev
    if "mean_annual_temp_c" in df.columns:
        feats["mean_temp_c"] = df["mean_annual_temp_c"]
    else:
        # Rough lapse-rate approximation: T ≈ 30 - 0.5*|lat| - 0.0065*elev
        feats["mean_temp_c"] = 30 - 0.5 * np.abs(lat) - 0.0065 * elev.fillna(0)

    if "mean_annual_precip_mm" in df.columns:
        feats["mean_precip_mm"] = df["mean_annual_precip_mm"]

    if "frost_days" in df.columns:
        feats["frost_days"] = df["frost_days"]

    # ── 7. Lake type ──
    lake_type = _get_col(df, ["Lake_type", "lake_type", "type"])
    feats["is_reservoir"] = (lake_type == 2).astype(np.float32)
    feats["is_natural"] = (lake_type == 1).astype(np.float32)

    # ── 8. Watershed ──
    wshd_area = _get_col(df, ["Wshd_area", "wshd_area_km2", "ws_area_ha", "ws_area_km2"],
                          scale_ha_to_km2=True)
    feats["wshd_area_km2"] = wshd_area
    feats["log_wshd_area"] = np.log1p(wshd_area.clip(lower=0))
    feats["wshd_lake_ratio"] = wshd_area / area_km2_safe

    # ── 9. Volume development factor ──
    # Vd = V / (A * Dmax / 3) — compares actual volume to cone
    # For lakes without known Vd, we estimate from GLOBathy vol + area
    vol_mcm = _get_col(df, ["Vol_total", "vol_total_mcm", "volume_mcm"])
    feats["vol_mcm"] = vol_mcm
    feats["log_vol"] = np.log1p(vol_mcm.clip(lower=0))

    # ── 10. Hydrological features ──
    dis_avg = _get_col(df, ["Dis_avg", "discharge_m3s"])
    feats["discharge_m3s"] = dis_avg
    feats["log_discharge"] = np.log1p(dis_avg.clip(lower=0))

    res_time = _get_col(df, ["Res_time", "residence_time_days"])
    feats["residence_time_days"] = res_time
    feats["log_res_time"] = np.log1p(res_time.clip(lower=0))

    # ── 11. Interaction features ──
    feats["area_x_slope"] = feats["log_area"] * feats["shore_slope"].fillna(0)
    feats["area_x_elev"] = feats["log_area"] * feats["elevation_m"].fillna(0)
    feats["area_x_lat"] = feats["log_area"] * feats["abs_lat"].fillna(0)
    feats["sdi_x_slope"] = feats["sdi"] * feats["shore_slope"].fillna(0)
    feats["wshd_x_slope"] = feats["log_wshd_area"].fillna(0) * feats["shore_slope"].fillna(0)
    feats["reservoir_x_area"] = feats["is_reservoir"] * feats["log_area"]

    # ── 12. Polynomial features for area (GLOBathy core relationship) ──
    feats["area_sq"] = feats["log_area"] ** 2
    feats["area_cube"] = feats["log_area"] ** 3

    return feats


def _get_col(
    df: pd.DataFrame,
    candidates: list[str],
    scale_ha: bool = False,
    scale_m: bool = False,
    scale_ha_to_km2: bool = False,
) -> pd.Series:
    """Try multiple column name candidates, apply unit conversions."""
    for col in candidates:
        if col in df.columns:
            vals = df[col].copy()
            if scale_ha and "ha" in col.lower():
                vals = vals / 100.0  # ha -> km²
            if scale_m and "perim_m" in col.lower():
                vals = vals / 1000.0  # m -> km
            if scale_ha_to_km2 and "ha" in col.lower():
                vals = vals / 100.0
            return vals
    return pd.Series(np.nan, index=df.index, name=candidates[0])


# ── Data Loading ─────────────────────────────────────────────────────

def load_training_data(data_path: Path) -> tuple[pd.DataFrame, str]:
    """
    Load training data from parquet or CSV.

    Expects a file with HydroLAKES morphometric columns + a depth column.
    Returns (dataframe, depth_column_name).
    """
    if data_path.suffix == ".parquet":
        df = pd.read_parquet(data_path)
    else:
        df = pd.read_csv(data_path)

    log.info(f"Loaded {len(df):,} rows, {len(df.columns)} columns from {data_path.name}")
    log.info(f"Columns: {list(df.columns)}")

    # Find depth column
    depth_col = None
    for col in DEPTH_COLS:
        if col in df.columns:
            depth_col = col
            break

    if depth_col is None:
        raise ValueError(
            f"No depth column found. Expected one of {DEPTH_COLS}. "
            f"Available: {list(df.columns)}"
        )

    # Filter to lakes with valid depth
    n_before = len(df)
    df = df.dropna(subset=[depth_col])
    df = df[df[depth_col] > 0]
    log.info(f"Lakes with valid depth ({depth_col}): {len(df):,} / {n_before:,}")

    return df, depth_col


def spatial_cv_split(
    df: pd.DataFrame,
    n_splits: int = 5,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """
    Spatial cross-validation split by lake_id or geographic grid.

    Ensures no lake appears in both train and validation to prevent leakage.
    Falls back to geographic grid if no lake_id column exists.
    """
    from sklearn.model_selection import GroupKFold

    # Find group column
    group_col = None
    for col in ["lake_id", "Hylak_id", "hylak_id", "permanent_id", "hu4_zoneid"]:
        if col in df.columns:
            group_col = col
            break

    if group_col is not None:
        log.info(f"Spatial CV by {group_col} ({df[group_col].nunique():,} groups)")
        groups = df[group_col]
    else:
        # Create geographic grid cells (0.5 degree)
        log.info("No lake_id found — using 0.5-degree geographic grid for spatial CV")
        lat = _get_col(df, ["Pour_lat", "lake_lat", "lat", "latitude"]).fillna(0)
        lon = _get_col(df, ["Pour_long", "lake_lon", "lon", "longitude"]).fillna(0)
        groups = (np.round(lat * 2) * 10000 + np.round(lon * 2)).astype(int)

    gkf = GroupKFold(n_splits=n_splits)
    return list(gkf.split(df, groups=groups))


# ── Models ───────────────────────────────────────────────────────────

def train_xgboost(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
) -> "xgb.Booster":
    """Train XGBoost regressor with early stopping."""
    import xgboost as xgb

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=X_train.columns.tolist())
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=X_train.columns.tolist())

    params = {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "max_depth": 8,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 10,
        "reg_alpha": 0.1,
        "reg_lambda": 1.0,
        "tree_method": "hist",
        "seed": 42,
    }

    model = xgb.train(
        params,
        dtrain,
        num_boost_round=2000,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=50,
        verbose_eval=100,
    )

    return model


def train_random_forest(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
):
    """Train Random Forest regressor."""
    from sklearn.ensemble import RandomForestRegressor

    rf = RandomForestRegressor(
        n_estimators=500,
        max_depth=20,
        min_samples_leaf=10,
        max_features="sqrt",
        n_jobs=-1,
        random_state=42,
    )
    rf.fit(X_train, y_train)
    return rf


def train_linear(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
):
    """Train Ridge regression baseline."""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("ridge", Ridge(alpha=1.0)),
    ])
    pipe.fit(X_train, y_train)
    return pipe


def train_kan(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
    epochs: int = 200,
    batch_size: int = 512,
    device: str = "cuda",
):
    """Train KAN (Kolmogorov-Arnold Network) for morphometric depth."""
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    # Normalise
    X_mean = X_train.mean()
    X_std = X_train.std().clip(lower=1e-6)
    X_tr = torch.tensor(((X_train - X_mean) / X_std).values, dtype=torch.float32)
    y_tr = torch.tensor(y_train, dtype=torch.float32)
    X_va = torch.tensor(((X_val - X_mean) / X_std).values, dtype=torch.float32)
    y_va = torch.tensor(y_val, dtype=torch.float32)

    train_ds = TensorDataset(X_tr, y_tr)
    train_dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True)

    n_feat = X_tr.shape[1]

    # Try efficient-kan, fall back to MLP
    try:
        from efficient_kan import KANLinear
        log.info("Using efficient-kan for morphometric KAN")

        class MorphKAN(nn.Module):
            def __init__(self):
                super().__init__()
                self.layers = nn.ModuleList([
                    KANLinear(n_feat, 128, grid_size=5, spline_order=3),
                    KANLinear(128, 64, grid_size=5, spline_order=3),
                    KANLinear(64, 32, grid_size=5, spline_order=3),
                    KANLinear(32, 1, grid_size=5, spline_order=3),
                ])

            def forward(self, x):
                for layer in self.layers:
                    x = layer(x)
                return torch.softplus(x).squeeze(-1)

    except ImportError:
        log.warning("efficient-kan not available, using MLP fallback for KAN")

        class MorphKAN(nn.Module):
            def __init__(self):
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(n_feat, 128), nn.BatchNorm1d(128), nn.SiLU(), nn.Dropout(0.1),
                    nn.Linear(128, 64), nn.BatchNorm1d(64), nn.SiLU(), nn.Dropout(0.1),
                    nn.Linear(64, 32), nn.BatchNorm1d(32), nn.SiLU(),
                    nn.Linear(32, 1), nn.Softplus(),
                )

            def forward(self, x):
                return self.net(x).squeeze(-1)

    dev = torch.device(device if torch.cuda.is_available() else "cpu")
    model = MorphKAN().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)

    best_val_loss = float("inf")
    best_state = None
    patience = 30
    patience_counter = 0

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0
        for xb, yb in train_dl:
            xb, yb = xb.to(dev), yb.to(dev)
            pred = model(xb)
            loss = nn.functional.huber_loss(pred, yb, delta=2.0)
            opt.zero_grad()
            loss.backward()
            opt.step()
            epoch_loss += loss.item() * len(xb)

        scheduler.step()

        # Validation
        model.eval()
        with torch.no_grad():
            val_pred = model(X_va.to(dev)).cpu().numpy()
            val_mse = np.mean((val_pred - y_val) ** 2)

        if val_mse < best_val_loss:
            best_val_loss = val_mse
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                log.info(f"  KAN early stop at epoch {epoch+1}")
                break

        if (epoch + 1) % 50 == 0:
            log.info(f"  KAN epoch {epoch+1}/{epochs}, val RMSE={np.sqrt(val_mse):.3f}m")

    if best_state is not None:
        model.load_state_dict(best_state)

    # Return wrapper for sklearn-like predict()
    class KANWrapper:
        def __init__(self, model, X_mean, X_std, device):
            self.model = model.eval().to(device)
            self.X_mean = X_mean
            self.X_std = X_std
            self.device = device

        def predict(self, X):
            import torch
            X_norm = (X - self.X_mean) / self.X_std
            X_t = torch.tensor(X_norm.values, dtype=torch.float32).to(self.device)
            with torch.no_grad():
                return self.model(X_t).cpu().numpy()

    return KANWrapper(model, X_mean, X_std, dev)


def predict_model(model, X: pd.DataFrame) -> np.ndarray:
    """Unified predict interface for all model types."""
    import xgboost as xgb

    if isinstance(model, xgb.Booster):
        dmat = xgb.DMatrix(X, feature_names=X.columns.tolist())
        return model.predict(dmat)
    elif hasattr(model, "predict"):
        return model.predict(X)
    else:
        raise ValueError(f"Unknown model type: {type(model)}")


# ── Evaluation ───────────────────────────────────────────────────────

def evaluate(y_true: np.ndarray, y_pred: np.ndarray, label: str = "") -> dict:
    """Compute regression metrics on original depth scale."""
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

    # Back to original scale from log
    y_true_m = np.expm1(y_true)
    y_pred_m = np.expm1(np.clip(y_pred, 0, 10))  # clip log-depth for safety

    rmse = np.sqrt(mean_squared_error(y_true_m, y_pred_m))
    mae = mean_absolute_error(y_true_m, y_pred_m)
    r2 = r2_score(y_true_m, y_pred_m)
    median_ae = np.median(np.abs(y_true_m - y_pred_m))
    mape = np.mean(np.abs(y_true_m - y_pred_m) / y_true_m.clip(min=0.1)) * 100

    metrics = {
        "rmse_m": float(rmse),
        "mae_m": float(mae),
        "median_ae_m": float(median_ae),
        "r2": float(r2),
        "mape_pct": float(mape),
    }

    if label:
        log.info(
            f"  {label}: RMSE={rmse:.2f}m, MAE={mae:.2f}m, "
            f"MedianAE={median_ae:.2f}m, R2={r2:.4f}, MAPE={mape:.1f}%"
        )

    return metrics


def evaluate_depth_bins(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Per-depth-bin evaluation to check performance across depth ranges."""
    y_true_m = np.expm1(y_true)
    y_pred_m = np.expm1(np.clip(y_pred, 0, 10))

    bins = [(0, 3), (3, 10), (10, 20), (20, 50), (50, 200)]
    results = {}

    for lo, hi in bins:
        mask = (y_true_m >= lo) & (y_true_m < hi)
        n = mask.sum()
        if n < 10:
            continue
        rmse = np.sqrt(np.mean((y_true_m[mask] - y_pred_m[mask]) ** 2))
        bias = np.mean(y_pred_m[mask] - y_true_m[mask])
        results[f"{lo}-{hi}m"] = {"n": int(n), "rmse": float(rmse), "bias": float(bias)}
        log.info(f"  Depth {lo}-{hi}m (n={n:,}): RMSE={rmse:.2f}m, Bias={bias:+.2f}m")

    return results


# ── Main Training Pipeline ───────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GLOBathy-style morphometric depth model")
    parser.add_argument("--data", type=str, required=True, help="Training data (parquet/csv)")
    parser.add_argument("--output", type=str, default="/data/models/morphometric")
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=200, help="KAN training epochs")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument(
        "--models", nargs="+",
        default=["xgboost", "rf", "linear", "kan"],
        choices=["xgboost", "rf", "linear", "kan"],
        help="Which models to train",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    df, depth_col = load_training_data(Path(args.data))

    # Compute features
    log.info("Computing morphometric features...")
    X = compute_morphometric_features(df)

    # Log-transform depth target (positive-skewed distribution)
    y = np.log1p(df[depth_col].values)

    # Handle NaN: fill with median (morphometric features rarely missing in HydroLAKES)
    n_missing = X.isnull().sum()
    if n_missing.any():
        log.info(f"Missing values per feature:\n{n_missing[n_missing > 0]}")
    X = X.fillna(X.median())

    feature_names = X.columns.tolist()
    log.info(f"Features ({len(feature_names)}): {feature_names}")
    log.info(f"Target: log1p({depth_col}), range [{y.min():.2f}, {y.max():.2f}]")

    # Spatial CV
    cv_splits = spatial_cv_split(df, n_splits=args.n_folds)

    # Train each model type
    all_results = {}
    model_trainers = {
        "xgboost": train_xgboost,
        "rf": train_random_forest,
        "linear": train_linear,
        "kan": lambda Xtr, ytr, Xva, yva: train_kan(
            Xtr, ytr, Xva, yva, epochs=args.epochs, device=args.device,
        ),
    }

    for model_name in args.models:
        log.info(f"\n{'='*60}")
        log.info(f"Training {model_name.upper()}")
        log.info(f"{'='*60}")

        trainer = model_trainers[model_name]

        oof_preds = np.full(len(y), np.nan)
        fold_metrics = []
        t0 = time.time()

        for fold_i, (train_idx, val_idx) in enumerate(cv_splits):
            log.info(f"\n--- Fold {fold_i + 1}/{args.n_folds} ---")
            X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            log.info(f"  Train: {len(train_idx):,}, Val: {len(val_idx):,}")

            model = trainer(X_train, y_train, X_val, y_val)

            # Predict
            val_pred = predict_model(model, X_val)
            oof_preds[val_idx] = val_pred

            # Evaluate fold
            fold_m = evaluate(y_val, val_pred, label=f"Fold {fold_i + 1}")
            fold_metrics.append(fold_m)

        elapsed = time.time() - t0
        log.info(f"\n{model_name.upper()} OOF Results (all folds):")
        valid_mask = ~np.isnan(oof_preds)
        oof_m = evaluate(y[valid_mask], oof_preds[valid_mask], label="OOF")
        oof_m["train_time_s"] = float(elapsed)

        # Per-depth-bin analysis
        log.info(f"\nPer-depth-bin metrics:")
        bin_results = evaluate_depth_bins(y[valid_mask], oof_preds[valid_mask])
        oof_m["depth_bins"] = bin_results

        # Average fold metrics
        avg_fold = {
            k: float(np.mean([f[k] for f in fold_metrics]))
            for k in fold_metrics[0] if isinstance(fold_metrics[0][k], float)
        }
        oof_m["avg_fold_metrics"] = avg_fold

        all_results[model_name] = oof_m

        # Save OOF predictions
        np.save(output_dir / f"oof_preds_{model_name}.npy", oof_preds)

    # ── Summary ──
    log.info(f"\n{'='*60}")
    log.info("SUMMARY — Morphometric Models")
    log.info(f"{'='*60}")
    for name, m in sorted(all_results.items(), key=lambda x: -x[1]["r2"]):
        log.info(
            f"  {name:10s}: R2={m['r2']:.4f}, RMSE={m['rmse_m']:.2f}m, "
            f"MAE={m['mae_m']:.2f}m ({m['train_time_s']:.0f}s)"
        )

    # Train final models on all data for production
    log.info("\nTraining final models on ALL data...")
    final_models = {}
    for model_name in args.models:
        trainer = model_trainers[model_name]
        # Use 90/10 split for final model early stopping
        n = len(X)
        idx = np.random.permutation(n)
        split = int(0.9 * n)
        X_tr, X_va = X.iloc[idx[:split]], X.iloc[idx[split:]]
        y_tr, y_va = y[idx[:split]], y[idx[split:]]
        final_models[model_name] = trainer(X_tr, y_tr, X_va, y_va)

    # Save final models
    import joblib

    for name, model in final_models.items():
        model_path = output_dir / f"final_{name}.joblib"
        joblib.dump(model, model_path)
        log.info(f"Saved {model_path}")

    # Save metrics and feature list
    results_out = {
        "models": all_results,
        "features": feature_names,
        "n_lakes": len(df),
        "depth_col": depth_col,
        "n_folds": args.n_folds,
    }
    with open(output_dir / "morphometric_results.json", "w") as f:
        json.dump(results_out, f, indent=2)

    log.info(f"\nAll results saved to {output_dir}")
    log.info("Done.")


if __name__ == "__main__":
    main()
