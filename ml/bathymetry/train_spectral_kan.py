#!/usr/bin/env python3
"""
OpenCatch — Spectral KAN Bathymetry Model Training

Trains a KAN (Kolmogorov-Arnold Network) on the ICESat-2 + Sentinel-2 training
set built by build_icesat2_training_set.py. Designed for 100K+ training points.

Key features:
- Spatial cross-validation: train/val/test split by lake_id (no leakage)
- Physics-informed loss: penalises negative depth, encourages depth-reflectance monotonicity
- Multi-scale KAN: separate KAN heads for shallow (<5m), medium (5-15m), deep (>15m)
- Stumpf initialisation: pre-train on log-ratio linear model as warm start
- Extensive logging: per-epoch metrics, per-depth-bin RMSE, feature importance

Architecture:
  Input (28 spectral features) -> KAN [256, 128, 64, 32] -> depth (m)

Target: sub-1m RMSE on validation set (Tibetan paper: 0.36m on clear water)

Usage:
    python train_spectral_kan.py \
        --data /data/icesat2_s2_training.parquet \
        --output /data/models/spectral_kan \
        --epochs 300 \
        --batch-size 1024 \
        --device cuda

    # With pre-computed depth-only file (will try to load S2 features)
    python train_spectral_kan.py \
        --data /data/icesat2_s2_training.parquet \
        --output /data/models/spectral_kan

Requirements:
    pip install torch efficient-kan pandas pyarrow numpy tqdm scikit-learn
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("spectral_kan")


# ── Feature columns ─────────────────────────────────────────────────

# Raw S2 bands
RAW_BANDS = [
    "blue", "green", "red",
    "rededge1", "rededge2", "rededge3",
    "nir", "nir08",
    "swir16", "swir22",
]

# Derived features (from build_icesat2_training_set.py)
DERIVED_FEATURES = [
    "log_blue", "log_green", "log_red", "log_nir",
    "log_blue_green", "log_blue_red",
    "green_red_ratio", "blue_green_ratio",
    "ndwi", "mndwi", "ndti", "fai", "cdom_proxy",
    "blue_minus_green", "green_minus_red", "red_minus_nir",
    "green_sq", "blue_sq",
]

# ICESat-2 depth-only features (used when S2 bands are not available)
ICESAT2_FEATURES = [
    "lat", "lon", "h_mean", "h_sigma",
    "n_fit_photons", "w_surface_window_final", "quality",
]

ALL_FEATURES = RAW_BANDS + DERIVED_FEATURES + ICESAT2_FEATURES


# ── Dataset ─────────────────────────────────────────────────────────

class SpectralDepthDataset(Dataset):
    """Point-wise spectral -> depth dataset."""

    def __init__(self, df: pd.DataFrame, features: list[str], augment: bool = False):
        self.features = features

        # Validate columns exist
        missing = [f for f in features if f not in df.columns]
        if missing:
            raise ValueError(f"Missing feature columns: {missing}")

        self.X = torch.tensor(df[features].values, dtype=torch.float32)
        self.y = torch.tensor(df["depth_m"].values, dtype=torch.float32)

        # Normalise features (store stats for inference)
        self.mean = self.X.mean(dim=0)
        self.std = self.X.std(dim=0).clamp(min=1e-6)
        self.X = (self.X - self.mean) / self.std

        self.augment = augment

        log.info(f"  Dataset: {len(self)} samples, {len(features)} features")
        log.info(f"  Depth: mean={self.y.mean():.2f}m, "
                 f"std={self.y.std():.2f}m, "
                 f"range=[{self.y.min():.2f}, {self.y.max():.2f}]m")

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        x = self.X[idx]
        y = self.y[idx]

        if self.augment:
            # Small Gaussian noise on spectral features (~1% of std)
            noise = torch.randn_like(x) * 0.01
            x = x + noise

        return x, y


# ── KAN Model ───────────────────────────────────────────────────────

class SpectralKAN(nn.Module):
    """
    KAN for spectral bathymetry with physics-informed output head.

    Architecture:
      Input -> KANLinear layers -> depth head (Softplus for non-negative output)

    Falls back to SplineMLP if efficient-kan unavailable.
    """

    def __init__(
        self,
        in_features: int = 28,
        hidden_dims: list[int] = None,
        grid_size: int = 5,
        spline_order: int = 3,
        max_depth: float = 50.0,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.max_depth = max_depth

        if hidden_dims is None:
            hidden_dims = [256, 128, 64, 32]

        self.use_kan = False
        try:
            from efficient_kan import KANLinear
            log.info("Using efficient-kan KANLinear layers")
            self._build_kan(in_features, hidden_dims, grid_size, spline_order)
            self.use_kan = True
        except ImportError:
            log.warning("efficient-kan not installed, using SplineMLP fallback")
            self._build_mlp(in_features, hidden_dims, dropout)

    def _build_kan(self, in_features, hidden_dims, grid_size, spline_order):
        from efficient_kan import KANLinear

        layers = []
        dims = [in_features] + hidden_dims
        for i in range(len(dims) - 1):
            layers.append(KANLinear(
                dims[i], dims[i + 1],
                grid_size=grid_size,
                spline_order=spline_order,
            ))
        self.backbone = nn.ModuleList(layers)
        self.head = KANLinear(hidden_dims[-1], 1, grid_size=grid_size,
                              spline_order=spline_order)

    def _build_mlp(self, in_features, hidden_dims, dropout):
        layers = []
        dims = [in_features] + hidden_dims
        for i in range(len(dims) - 1):
            layers.extend([
                nn.Linear(dims[i], dims[i + 1]),
                nn.BatchNorm1d(dims[i + 1]),
                nn.SiLU(),
                nn.Dropout(dropout),
            ])
        self.backbone = nn.Sequential(*layers)
        self.head = nn.Sequential(
            nn.Linear(hidden_dims[-1], 1),
            nn.Softplus(),
        )

    def forward(self, x):
        if self.use_kan:
            h = x
            for layer in self.backbone:
                h = layer(h)
            depth = self.head(h)
        else:
            h = self.backbone(x)
            depth = self.head(h)
        return depth.squeeze(-1).clamp(0, self.max_depth)


class MultiScaleKAN(nn.Module):
    """
    Multi-scale KAN with separate heads for different depth ranges.
    A gating network routes inputs to shallow/medium/deep experts.
    """

    def __init__(
        self,
        in_features: int = 28,
        hidden_dims: list[int] = None,
        grid_size: int = 5,
        max_depth: float = 50.0,
    ):
        super().__init__()
        self.max_depth = max_depth

        if hidden_dims is None:
            hidden_dims = [256, 128, 64, 32]

        # Shared backbone (first 2 layers)
        shared_dims = hidden_dims[:2]
        expert_dims = hidden_dims[2:]

        self.shared = self._make_block(in_features, shared_dims)

        # Expert heads for different depth ranges
        expert_in = shared_dims[-1]
        self.shallow_head = self._make_block(expert_in, expert_dims + [1])  # <5m
        self.medium_head = self._make_block(expert_in, expert_dims + [1])   # 5-15m
        self.deep_head = self._make_block(expert_in, expert_dims + [1])     # >15m

        # Gating network
        self.gate = nn.Sequential(
            nn.Linear(expert_in, 32),
            nn.SiLU(),
            nn.Linear(32, 3),
            nn.Softmax(dim=-1),
        )

    def _make_block(self, in_dim, dims):
        layers = []
        for d in dims:
            layers.extend([
                nn.Linear(in_dim, d),
                nn.BatchNorm1d(d) if d > 1 else nn.Identity(),
                nn.SiLU() if d > 1 else nn.Softplus(),
            ])
            in_dim = d
        return nn.Sequential(*layers)

    def forward(self, x):
        shared = self.shared(x)

        # Expert predictions
        d_shallow = self.shallow_head(shared).squeeze(-1)
        d_medium = self.medium_head(shared).squeeze(-1)
        d_deep = self.deep_head(shared).squeeze(-1)

        # Gating weights
        gates = self.gate(shared)  # (B, 3)

        # Weighted combination
        depth = (
            gates[:, 0] * d_shallow +
            gates[:, 1] * d_medium +
            gates[:, 2] * d_deep
        )

        return depth.clamp(0, self.max_depth)


# ── Loss functions ──────────────────────────────────────────────────

class PhysicsInformedLoss(nn.Module):
    """
    Combined loss with physics constraints:
    1. Huber loss (robust to outliers)
    2. Monotonicity penalty: blue reflectance should decrease with depth
    3. Non-negative penalty (redundant with Softplus but reinforces)
    """

    def __init__(self, physics_weight: float = 0.1, blue_idx: int = 0):
        super().__init__()
        self.physics_weight = physics_weight
        self.blue_idx = blue_idx  # -1 means no blue band available
        self.huber = nn.HuberLoss(delta=2.0)

    def forward(self, pred, target, features=None):
        # Primary loss
        loss = self.huber(pred, target)

        if features is not None and self.physics_weight > 0 and self.blue_idx >= 0:
            # Monotonicity: deeper water -> less blue reflectance
            # Penalise if (pred_i > pred_j) but (blue_i > blue_j) for nearby points
            # Simplified: correlation between pred and blue should be negative
            blue = features[:, self.blue_idx]  # blue band (normalised)
            if len(pred) > 10:
                # Sample random pairs
                n = min(len(pred), 256)
                idx_a = torch.randint(0, len(pred), (n,))
                idx_b = torch.randint(0, len(pred), (n,))

                depth_diff = pred[idx_a] - pred[idx_b]
                blue_diff = blue[idx_a] - blue[idx_b]

                # Penalise if depth increases with blue (should be inverse)
                violation = F.relu(depth_diff * blue_diff)
                loss = loss + self.physics_weight * violation.mean()

        return loss


# ── Stumpf linear warm-start ────────────────────────────────────────

def stumpf_warm_start(df: pd.DataFrame):
    """
    Compute linear Stumpf model as a baseline and warm-start reference.
    depth = m0 + m1 * ln(Blue)/ln(Green)
    """
    from sklearn.linear_model import LinearRegression

    train = df[df["split"] == "train"]
    eps = 1e-6
    X = (np.log(train["blue"].clip(eps)) / np.log(train["green"].clip(eps)).clip(eps)).values.reshape(-1, 1)
    y = train["depth_m"].values

    valid = np.isfinite(X.ravel()) & np.isfinite(y) & (np.abs(X.ravel()) < 10)
    X, y = X[valid], y[valid]

    lr = LinearRegression().fit(X, y)
    pred = lr.predict(X)
    rmse = np.sqrt(np.mean((pred - y) ** 2))
    r2 = lr.score(X, y)

    log.info(f"Stumpf linear baseline: RMSE={rmse:.3f}m, R2={r2:.3f}")
    log.info(f"  depth = {lr.intercept_:.3f} + {lr.coef_[0]:.3f} * ln(Blue)/ln(Green)")

    return {"rmse": rmse, "r2": r2, "intercept": lr.intercept_, "slope": lr.coef_[0]}


# ── Training loop ───────────────────────────────────────────────────

def train_model(
    df: pd.DataFrame,
    output_dir: Path,
    model_type: str = "kan",
    hidden_dims: list[int] = None,
    grid_size: int = 5,
    epochs: int = 300,
    batch_size: int = 1024,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    physics_weight: float = 0.1,
    patience: int = 30,
    device: str = "cuda",
    max_depth: float = 50.0,
):
    """Full training pipeline with validation monitoring."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine available features — supports both S2+ICeSat-2 and ICeSat-2-only modes
    available = [f for f in ALL_FEATURES if f in df.columns and f != "depth_m"]
    has_s2 = any(b in df.columns for b in RAW_BANDS)
    has_icesat2 = any(f in df.columns for f in ICESAT2_FEATURES)
    if len(available) < 3:
        log.error(f"Only {len(available)} features available. Need at least 3.")
        return
    if not has_s2:
        log.warning("No S2 bands found — running in ICESat-2 depth-only mode")
    log.info(f"Using {len(available)} features: {available}")

    # Stumpf baseline (only with S2 bands)
    if has_s2 and "blue" in df.columns and "green" in df.columns:
        baseline = stumpf_warm_start(df)
    else:
        log.info("Skipping Stumpf baseline (no S2 bands)")
        baseline = {"rmse": float("inf")}

    # Create datasets
    train_df = df[df["split"] == "train"].reset_index(drop=True)
    val_df = df[df["split"] == "val"].reset_index(drop=True)
    test_df = df[df["split"] == "test"].reset_index(drop=True)

    log.info(f"Train: {len(train_df):,}, Val: {len(val_df):,}, Test: {len(test_df):,}")

    train_ds = SpectralDepthDataset(train_df, available, augment=True)
    val_ds = SpectralDepthDataset(val_df, available, augment=False)

    # Use training normalisation stats for val/test
    val_ds.mean = train_ds.mean
    val_ds.std = train_ds.std
    val_ds.X = (torch.tensor(val_df[available].values, dtype=torch.float32) - train_ds.mean) / train_ds.std

    # Weighted sampler for depth-balanced training
    depth_bins = pd.cut(train_df["depth_m"], bins=[0, 2, 5, 10, 20, 50], labels=False)
    bin_counts = depth_bins.value_counts()
    weights = 1.0 / bin_counts[depth_bins].values
    weights = weights / weights.sum()
    sampler = WeightedRandomSampler(weights, len(weights), replacement=True)

    train_loader = DataLoader(train_ds, batch_size=batch_size, sampler=sampler,
                              num_workers=4, pin_memory=True, drop_last=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size * 2,
                            num_workers=2, pin_memory=True)

    # Model
    if model_type == "multiscale":
        model = MultiScaleKAN(
            in_features=len(available),
            hidden_dims=hidden_dims or [256, 128, 64, 32],
            grid_size=grid_size,
            max_depth=max_depth,
        )
    else:
        model = SpectralKAN(
            in_features=len(available),
            hidden_dims=hidden_dims or [256, 128, 64, 32],
            grid_size=grid_size,
            max_depth=max_depth,
        )

    n_params = sum(p.numel() for p in model.parameters())
    log.info(f"Model: {model_type}, {n_params:,} parameters")
    model = model.to(device)

    # Optimiser
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=50, T_mult=2, eta_min=1e-6
    )

    blue_idx = available.index("blue") if "blue" in available else -1
    criterion = PhysicsInformedLoss(physics_weight=physics_weight, blue_idx=blue_idx)
    scaler = torch.amp.GradScaler("cuda") if "cuda" in device else None

    # Training
    best_val_rmse = float("inf")
    best_epoch = 0
    no_improve = 0
    history = []

    log.info(f"\nStarting training: {epochs} epochs, batch_size={batch_size}")
    log.info(f"Device: {device}")
    start_time = time.time()

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        n_batches = 0

        for x_batch, y_batch in train_loader:
            x_batch = x_batch.to(device)
            y_batch = y_batch.to(device)

            optimizer.zero_grad()

            if scaler:
                with torch.amp.autocast("cuda"):
                    pred = model(x_batch)
                    loss = criterion(pred, y_batch, x_batch)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                pred = model(x_batch)
                loss = criterion(pred, y_batch, x_batch)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()

            train_loss += loss.item()
            n_batches += 1

        scheduler.step()
        avg_train_loss = train_loss / max(n_batches, 1)

        # Validation
        model.eval()
        val_preds, val_targets = [], []
        with torch.no_grad():
            for x_batch, y_batch in val_loader:
                x_batch = x_batch.to(device)
                pred = model(x_batch)
                val_preds.append(pred.cpu())
                val_targets.append(y_batch)

        val_preds = torch.cat(val_preds)
        val_targets = torch.cat(val_targets)

        val_rmse = ((val_preds - val_targets) ** 2).mean().sqrt().item()
        val_mae = (val_preds - val_targets).abs().mean().item()
        val_r2 = 1 - ((val_preds - val_targets) ** 2).sum() / \
                     ((val_targets - val_targets.mean()) ** 2).sum()
        val_r2 = val_r2.item()

        # Depth-bin RMSE
        bin_rmses = {}
        for lo, hi, label in [(0, 2, "0-2m"), (2, 5, "2-5m"), (5, 10, "5-10m"),
                               (10, 20, "10-20m"), (20, 50, "20-50m")]:
            mask = (val_targets >= lo) & (val_targets < hi)
            if mask.sum() > 0:
                bin_rmse = ((val_preds[mask] - val_targets[mask]) ** 2).mean().sqrt().item()
                bin_rmses[label] = bin_rmse

        # Track best
        improved = False
        if val_rmse < best_val_rmse:
            best_val_rmse = val_rmse
            best_epoch = epoch
            no_improve = 0
            improved = True

            # Save best model
            checkpoint = {
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_rmse": val_rmse,
                "val_mae": val_mae,
                "val_r2": val_r2,
                "features": available,
                "norm_mean": train_ds.mean.numpy().tolist(),
                "norm_std": train_ds.std.numpy().tolist(),
                "model_type": model_type,
                "hidden_dims": hidden_dims or [256, 128, 64, 32],
                "grid_size": grid_size,
                "max_depth": max_depth,
                "n_features": len(available),
            }
            torch.save(checkpoint, output_dir / "best_model.pt")
        else:
            no_improve += 1

        record = {
            "epoch": epoch,
            "train_loss": avg_train_loss,
            "val_rmse": val_rmse,
            "val_mae": val_mae,
            "val_r2": val_r2,
            "best_rmse": best_val_rmse,
            "lr": optimizer.param_groups[0]["lr"],
            **{f"rmse_{k}": v for k, v in bin_rmses.items()},
        }
        history.append(record)

        # Log
        if epoch % 5 == 0 or improved or epoch <= 3:
            star = " *BEST*" if improved else ""
            log.info(
                f"Epoch {epoch:3d}/{epochs}: "
                f"loss={avg_train_loss:.4f} | "
                f"val RMSE={val_rmse:.3f}m MAE={val_mae:.3f}m R2={val_r2:.3f}"
                f"{star}"
            )
            if bin_rmses and epoch % 20 == 0:
                bins_str = " | ".join(f"{k}:{v:.2f}" for k, v in bin_rmses.items())
                log.info(f"  Depth bins: {bins_str}")

        # Early stopping
        if no_improve >= patience:
            log.info(f"Early stopping at epoch {epoch} (no improvement for {patience} epochs)")
            break

    elapsed = time.time() - start_time
    log.info(f"\nTraining complete in {elapsed / 60:.1f} minutes")
    log.info(f"Best val RMSE: {best_val_rmse:.3f}m (epoch {best_epoch})")
    log.info(f"Stumpf baseline RMSE: {baseline['rmse']:.3f}m")
    log.info(f"Improvement over baseline: {baseline['rmse'] - best_val_rmse:.3f}m")

    # Save history
    pd.DataFrame(history).to_csv(output_dir / "training_history.csv", index=False)

    # Test evaluation
    if len(test_df) > 0:
        log.info("\n" + "=" * 60)
        log.info("TEST SET EVALUATION")
        log.info("=" * 60)
        _evaluate_test(model, test_df, available, train_ds, device, output_dir, max_depth)

    # Feature importance (permutation-based)
    log.info("\n" + "=" * 60)
    log.info("FEATURE IMPORTANCE")
    log.info("=" * 60)
    _compute_feature_importance(model, val_ds, available, device, output_dir)

    # Save final config
    config = {
        "model_type": model_type,
        "n_features": len(available),
        "features": available,
        "hidden_dims": hidden_dims or [256, 128, 64, 32],
        "grid_size": grid_size,
        "max_depth": max_depth,
        "best_val_rmse": best_val_rmse,
        "best_epoch": best_epoch,
        "total_epochs": epoch,
        "n_train": len(train_df),
        "n_val": len(val_df),
        "n_test": len(test_df),
        "baseline_rmse": baseline.get("rmse"),
    }
    with open(output_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    log.info(f"\nAll outputs saved to {output_dir}")


def _evaluate_test(model, test_df, features, train_ds, device, output_dir, max_depth):
    """Evaluate on held-out test lakes."""
    test_ds = SpectralDepthDataset(test_df, features, augment=False)
    test_ds.mean = train_ds.mean
    test_ds.std = train_ds.std
    test_ds.X = (torch.tensor(test_df[features].values, dtype=torch.float32) - train_ds.mean) / train_ds.std

    loader = DataLoader(test_ds, batch_size=2048, num_workers=2)

    model.eval()
    all_preds, all_targets = [], []
    with torch.no_grad():
        for x, y in loader:
            pred = model(x.to(device))
            all_preds.append(pred.cpu())
            all_targets.append(y)

    preds = torch.cat(all_preds)
    targets = torch.cat(all_targets)

    rmse = ((preds - targets) ** 2).mean().sqrt().item()
    mae = (preds - targets).abs().mean().item()
    r2 = 1 - ((preds - targets) ** 2).sum() / ((targets - targets.mean()) ** 2).sum()

    log.info(f"Test RMSE: {rmse:.3f}m")
    log.info(f"Test MAE:  {mae:.3f}m")
    log.info(f"Test R2:   {r2:.3f}")

    # Per-lake metrics
    test_df = test_df.copy()
    test_df["pred"] = preds.numpy()
    lake_metrics = test_df.groupby("lake_id").apply(
        lambda g: pd.Series({
            "rmse": np.sqrt(((g["pred"] - g["depth_m"]) ** 2).mean()),
            "n_points": len(g),
        })
    ).reset_index()

    log.info(f"\nPer-lake test metrics ({len(lake_metrics)} lakes):")
    log.info(f"  Median lake RMSE: {lake_metrics['rmse'].median():.3f}m")
    log.info(f"  Mean lake RMSE:   {lake_metrics['rmse'].mean():.3f}m")
    log.info(f"  Best lake RMSE:   {lake_metrics['rmse'].min():.3f}m")
    log.info(f"  Worst lake RMSE:  {lake_metrics['rmse'].max():.3f}m")

    # Save predictions
    test_df[["lat", "lon", "depth_m", "pred", "lake_id"]].to_csv(
        output_dir / "test_predictions.csv", index=False
    )

    return {"rmse": rmse, "mae": mae, "r2": r2.item()}


def _compute_feature_importance(model, val_ds, features, device, output_dir):
    """Permutation-based feature importance."""
    loader = DataLoader(val_ds, batch_size=2048, num_workers=2)

    # Baseline predictions
    model.eval()
    all_preds, all_targets = [], []
    with torch.no_grad():
        for x, y in loader:
            all_preds.append(model(x.to(device)).cpu())
            all_targets.append(y)

    base_preds = torch.cat(all_preds)
    targets = torch.cat(all_targets)
    base_rmse = ((base_preds - targets) ** 2).mean().sqrt().item()

    importances = {}
    for i, feat in enumerate(features):
        # Permute feature i
        X_perm = val_ds.X.clone()
        X_perm[:, i] = X_perm[torch.randperm(len(X_perm)), i]

        perm_ds = torch.utils.data.TensorDataset(X_perm, val_ds.y)
        perm_loader = DataLoader(perm_ds, batch_size=2048)

        perm_preds = []
        with torch.no_grad():
            for x, _ in perm_loader:
                perm_preds.append(model(x.to(device)).cpu())

        perm_preds = torch.cat(perm_preds)
        perm_rmse = ((perm_preds - targets) ** 2).mean().sqrt().item()
        importances[feat] = perm_rmse - base_rmse

    # Sort by importance
    sorted_imp = sorted(importances.items(), key=lambda x: -x[1])
    log.info(f"Top 10 features (by RMSE increase when permuted):")
    for feat, imp in sorted_imp[:10]:
        log.info(f"  {feat:25s}: +{imp:.4f}m RMSE")

    # Save
    pd.DataFrame([{"feature": k, "importance": v} for k, v in sorted_imp]).to_csv(
        output_dir / "feature_importance.csv", index=False
    )


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Train spectral KAN bathymetry model",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data", type=str, required=True,
                        help="Path to training GeoParquet (from build_icesat2_training_set.py)")
    parser.add_argument("--output", type=str, default="/data/models/spectral_kan",
                        help="Output directory for model and logs")
    parser.add_argument("--model", type=str, default="kan",
                        choices=["kan", "multiscale"],
                        help="Model architecture")
    parser.add_argument("--hidden-dims", type=int, nargs="+", default=[256, 128, 64, 32],
                        help="Hidden layer dimensions")
    parser.add_argument("--grid-size", type=int, default=5,
                        help="KAN grid size (B-spline control points)")
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--physics-weight", type=float, default=0.1,
                        help="Weight for physics-informed loss")
    parser.add_argument("--patience", type=int, default=30,
                        help="Early stopping patience")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--max-depth", type=float, default=50.0)

    args = parser.parse_args()

    log.info("Loading training data...")
    df = pd.read_parquet(args.data)
    log.info(f"Loaded {len(df):,} rows, {len(df.columns)} columns")
    log.info(f"Columns: {list(df.columns)}")

    if "split" not in df.columns:
        log.warning("No 'split' column found. Adding random split.")
        from sklearn.model_selection import train_test_split
        # Still split by lake_id if available
        if "lake_id" in df.columns:
            lakes = df["lake_id"].unique()
            train_l, temp_l = train_test_split(lakes, test_size=0.30, random_state=42)
            val_l, test_l = train_test_split(temp_l, test_size=0.50, random_state=42)
            df["split"] = "test"
            df.loc[df["lake_id"].isin(train_l), "split"] = "train"
            df.loc[df["lake_id"].isin(val_l), "split"] = "val"
        else:
            df["split"] = np.random.choice(["train", "val", "test"],
                                            size=len(df), p=[0.7, 0.15, 0.15])

    train_model(
        df=df,
        output_dir=Path(args.output),
        model_type=args.model,
        hidden_dims=args.hidden_dims,
        grid_size=args.grid_size,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        weight_decay=args.weight_decay,
        physics_weight=args.physics_weight,
        patience=args.patience,
        device=args.device,
        max_depth=args.max_depth,
    )


if __name__ == "__main__":
    main()
