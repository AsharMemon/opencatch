#!/usr/bin/env python3
"""
OpenCatch — Enhanced BathyFormer Training Script

Trains the enhanced BathyFormer (Vision Transformer) for satellite-derived
bathymetry on ICESat-2 + Sentinel-2 data. Improves on the published BathyFormer
(2025, 0.55-0.73m RMSE) targeting sub-0.5m RMSE.

Supports two training modes:
1. Patch mode (primary): 64x64 S2 image patches centered on ICESat-2 depth points
2. Point mode (fallback): tabular spectral features per point (like KAN)

Key features:
- Physics-informed loss (Beer-Lambert + smoothness + gate supervision)
- Dual-band gated depth heads (shallow <6m, deep >6m)
- ICESat-2 refraction correction (Parrish et al. 2019)
- Multi-scale cross-attention (1x/2x/4x patch scales)
- MC Dropout uncertainty estimation
- Per-lake spatial cross-validation (no data leakage)
- Depth-binned evaluation (0-2m, 2-5m, 5-10m, 10-20m, 20m+)
- Depth-balanced sampling (oversample rare deep points)
- Mixed precision training (AMP)
- Cosine annealing with warm restarts
- File-based logging (W&B-compatible JSON lines)

Usage:
    # Point mode on existing training data (fallback)
    python train_bathyformer.py \\
        --data /data/icesat2_s2_training.parquet \\
        --output /data/models/bathyformer \\
        --mode point \\
        --epochs 200 \\
        --batch-size 512 \\
        --device cuda

    # Patch mode with S2 COG patches
    python train_bathyformer.py \\
        --data /data/icesat2_s2_training.parquet \\
        --patch-dir /data/s2_patches \\
        --output /data/models/bathyformer \\
        --mode patch \\
        --epochs 100 \\
        --batch-size 32 \\
        --img-size 64 \\
        --device cuda

    # Auto mode (uses patches if available, falls back to points)
    python train_bathyformer.py \\
        --data /data/icesat2_s2_training.parquet \\
        --output /data/models/bathyformer \\
        --device cuda

Requirements:
    pip install torch pandas pyarrow numpy tqdm scikit-learn rasterio
"""

import argparse
import json
import logging
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.amp import GradScaler, autocast
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bathyformer")


# ── Feature columns (matching build_icesat2_training_set.py) ────────

RAW_BANDS = [
    "blue", "green", "red",
    "rededge1", "rededge2", "rededge3",
    "nir", "nir08",
    "swir16", "swir22",
]

DERIVED_FEATURES = [
    "log_blue", "log_green", "log_red", "log_nir",
    "log_blue_green", "log_blue_red",
    "green_red_ratio", "blue_green_ratio",
    "ndwi", "mndwi", "ndti", "fai", "cdom_proxy",
    "blue_minus_green", "green_minus_red", "red_minus_nir",
    "green_sq", "blue_sq",
]

ICESAT2_FEATURES = [
    "lat", "lon", "h_mean", "h_sigma",
    "n_fit_photons", "w_surface_window_final", "quality",
]

SPECTRAL_FEATURES = RAW_BANDS + DERIVED_FEATURES

DEPTH_BINS = [
    (0, 2, "0-2m"),
    (2, 5, "2-5m"),
    (5, 10, "5-10m"),
    (10, 20, "10-20m"),
    (20, 999, "20m+"),
]


# ── Datasets ────────────────────────────────────────────────────────

class PointDataset(Dataset):
    """Point-wise spectral features -> depth dataset."""

    def __init__(
        self,
        df: pd.DataFrame,
        features: list[str],
        apply_refraction: bool = True,
        augment: bool = False,
        mean: Optional[torch.Tensor] = None,
        std: Optional[torch.Tensor] = None,
    ):
        self.features = features

        # Filter to available features
        available = [f for f in features if f in df.columns]
        missing = [f for f in features if f not in df.columns]
        if missing:
            log.warning(f"Missing {len(missing)} features: {missing[:5]}...")
        self.features = available

        X = df[self.features].values.astype(np.float32)
        y = df["depth_m"].values.astype(np.float32)

        # Replace NaN with 0 (will be normalised away)
        nan_mask = np.isnan(X)
        if nan_mask.any():
            log.info(f"  Replacing {nan_mask.sum()} NaN values with 0")
            X = np.nan_to_num(X, nan=0.0)

        # Refraction correction on ICESat-2 labels
        if apply_refraction:
            from bathyformer_model import icesat2_refraction_correction
            y_tensor = torch.from_numpy(y)
            y_corrected = icesat2_refraction_correction(y_tensor)
            y = y_corrected.numpy()
            log.info(f"  Applied refraction correction: mean shift "
                     f"{y_tensor.mean():.2f} -> {y_corrected.mean():.2f}m")

        # Remove negative depths
        valid = y > 0
        X = X[valid]
        y = y[valid]
        log.info(f"  Removed {(~valid).sum()} non-positive depth samples")

        self.X = torch.from_numpy(X)
        self.y = torch.from_numpy(y)
        self.augment = augment

        # Normalisation stats
        if mean is not None and std is not None:
            self.mean = mean
            self.std = std
        else:
            self.mean = self.X.mean(dim=0)
            self.std = self.X.std(dim=0).clamp(min=1e-6)

        self.X = (self.X - self.mean) / self.std

        # Lake IDs for spatial CV
        if "lake_id" in df.columns:
            self.lake_ids = df.loc[valid if isinstance(valid, pd.Series)
                                   else df.index[valid], "lake_id"].values
        else:
            self.lake_ids = None

        log.info(f"  Dataset: {len(self)} samples, {len(self.features)} features")
        log.info(f"  Depth: mean={self.y.mean():.2f}m, std={self.y.std():.2f}m, "
                 f"range=[{self.y.min():.2f}, {self.y.max():.2f}]m")

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        x = self.X[idx]
        y = self.y[idx]

        if self.augment:
            noise = torch.randn_like(x) * 0.01
            x = x + noise

        return x, y

    def get_depth_weights(self) -> torch.Tensor:
        """Compute inverse-frequency weights for depth-balanced sampling."""
        depths = self.y.numpy()
        weights = np.ones(len(depths), dtype=np.float32)

        for lo, hi, _ in DEPTH_BINS:
            mask = (depths >= lo) & (depths < hi)
            count = mask.sum()
            if count > 0:
                # Inverse frequency weighting
                weights[mask] = len(depths) / (len(DEPTH_BINS) * count)

        return torch.from_numpy(weights)


class PatchDataset(Dataset):
    """
    Patch-based dataset: loads S2 image patches centered on ICESat-2 points.

    Expects pre-extracted patches as numpy files or reads from COG on the fly.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        patch_dir: str,
        img_size: int = 64,
        in_channels: int = 10,
        apply_refraction: bool = True,
        augment: bool = False,
    ):
        self.patch_dir = Path(patch_dir)
        self.img_size = img_size
        self.in_channels = in_channels
        self.augment = augment

        y = df["depth_m"].values.astype(np.float32)

        if apply_refraction:
            from bathyformer_model import icesat2_refraction_correction
            y_tensor = torch.from_numpy(y)
            y = icesat2_refraction_correction(y_tensor).numpy()

        valid = y > 0
        self.df = df[valid].reset_index(drop=True)
        self.y = torch.from_numpy(y[valid])

        # Check how many patches exist
        self.patch_paths = []
        found = 0
        for idx, row in self.df.iterrows():
            # Try multiple naming conventions
            candidates = [
                self.patch_dir / f"{idx}.npy",
                self.patch_dir / f"patch_{idx}.npy",
            ]
            if "point_id" in row:
                candidates.append(self.patch_dir / f"{row['point_id']}.npy")
            if "lat" in row and "lon" in row:
                lat_str = f"{row['lat']:.4f}"
                lon_str = f"{row['lon']:.4f}"
                candidates.append(self.patch_dir / f"{lat_str}_{lon_str}.npy")

            path = None
            for c in candidates:
                if c.exists():
                    path = c
                    found += 1
                    break
            self.patch_paths.append(path)

        log.info(f"  Patch dataset: {len(self)} samples, "
                 f"{found}/{len(self)} patches found on disk")
        log.info(f"  Depth: mean={self.y.mean():.2f}m, "
                 f"range=[{self.y.min():.2f}, {self.y.max():.2f}]m")

        if "lake_id" in df.columns:
            self.lake_ids = self.df["lake_id"].values
        else:
            self.lake_ids = None

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        path = self.patch_paths[idx]

        if path is not None and path.exists():
            patch = np.load(path).astype(np.float32)
            # Ensure correct shape (C, H, W)
            if patch.ndim == 3:
                if patch.shape[0] > patch.shape[2]:
                    # (H, W, C) -> (C, H, W)
                    patch = np.transpose(patch, (2, 0, 1))
            elif patch.ndim == 2:
                # Single band -> expand
                patch = np.expand_dims(patch, 0)

            # Resize if needed
            patch_tensor = torch.from_numpy(patch)
            if patch_tensor.shape[1] != self.img_size or patch_tensor.shape[2] != self.img_size:
                patch_tensor = F.interpolate(
                    patch_tensor.unsqueeze(0),
                    size=(self.img_size, self.img_size),
                    mode="bilinear", align_corners=False,
                ).squeeze(0)

            # Pad/truncate channels
            C = patch_tensor.shape[0]
            if C < self.in_channels:
                pad = torch.zeros(self.in_channels - C, self.img_size, self.img_size)
                patch_tensor = torch.cat([patch_tensor, pad], dim=0)
            elif C > self.in_channels:
                patch_tensor = patch_tensor[:self.in_channels]
        else:
            # No patch available — generate synthetic from point features
            patch_tensor = self._synthetic_patch(idx)

        if self.augment:
            patch_tensor = self._augment_patch(patch_tensor)

        return patch_tensor, self.y[idx]

    def _synthetic_patch(self, idx) -> torch.Tensor:
        """Generate a synthetic patch from point features (for graceful fallback)."""
        row = self.df.iloc[idx]
        channels = []
        for band in RAW_BANDS[:self.in_channels]:
            if band in row and not pd.isna(row[band]):
                val = float(row[band])
                # Create uniform patch with slight spatial noise
                ch = torch.full((self.img_size, self.img_size), val)
                ch += torch.randn_like(ch) * val * 0.02
            else:
                ch = torch.zeros(self.img_size, self.img_size)
            channels.append(ch)

        while len(channels) < self.in_channels:
            channels.append(torch.zeros(self.img_size, self.img_size))

        return torch.stack(channels, dim=0)

    def _augment_patch(self, patch: torch.Tensor) -> torch.Tensor:
        """Apply random augmentations to patches."""
        # Random horizontal/vertical flip
        if torch.rand(1) > 0.5:
            patch = torch.flip(patch, [2])
        if torch.rand(1) > 0.5:
            patch = torch.flip(patch, [1])
        # Random 90-degree rotation
        k = torch.randint(0, 4, (1,)).item()
        if k > 0:
            patch = torch.rot90(patch, k, [1, 2])
        # Small brightness jitter (~2%)
        jitter = 1.0 + (torch.rand(1) - 0.5) * 0.04
        patch = patch * jitter
        return patch

    def get_depth_weights(self) -> torch.Tensor:
        depths = self.y.numpy()
        weights = np.ones(len(depths), dtype=np.float32)
        for lo, hi, _ in DEPTH_BINS:
            mask = (depths >= lo) & (depths < hi)
            count = mask.sum()
            if count > 0:
                weights[mask] = len(depths) / (len(DEPTH_BINS) * count)
        return torch.from_numpy(weights)


# ── Spatial Cross-Validation ────────────────────────────────────────

def spatial_split(
    df: pd.DataFrame,
    val_frac: float = 0.15,
    test_frac: float = 0.15,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Split by lake_id for proper spatial cross-validation.
    Ensures no lake appears in both train and val/test sets.
    """
    rng = np.random.RandomState(seed)

    if "lake_id" not in df.columns:
        log.warning("No lake_id column — using random split (potential spatial leakage!)")
        n = len(df)
        idx = rng.permutation(n)
        n_test = int(n * test_frac)
        n_val = int(n * val_frac)
        return (
            df.iloc[idx[n_test + n_val:]],
            df.iloc[idx[n_test:n_test + n_val]],
            df.iloc[idx[:n_test]],
        )

    lakes = df["lake_id"].unique()
    rng.shuffle(lakes)

    # Compute cumulative point counts
    lake_counts = df.groupby("lake_id").size()
    total = len(df)
    n_test = int(total * test_frac)
    n_val = int(total * val_frac)

    test_lakes, val_lakes, train_lakes = [], [], []
    test_n, val_n = 0, 0

    for lake in lakes:
        cnt = lake_counts.get(lake, 0)
        if test_n < n_test:
            test_lakes.append(lake)
            test_n += cnt
        elif val_n < n_val:
            val_lakes.append(lake)
            val_n += cnt
        else:
            train_lakes.append(lake)

    train_df = df[df["lake_id"].isin(train_lakes)]
    val_df = df[df["lake_id"].isin(val_lakes)]
    test_df = df[df["lake_id"].isin(test_lakes)]

    log.info(f"Spatial split: {len(train_lakes)} train lakes ({len(train_df)} pts), "
             f"{len(val_lakes)} val lakes ({len(val_df)} pts), "
             f"{len(test_lakes)} test lakes ({len(test_df)} pts)")

    return train_df, val_df, test_df


# ── Metrics ─────────────────────────────────────────────────────────

def compute_metrics(
    preds: np.ndarray,
    targets: np.ndarray,
) -> dict[str, float]:
    """Compute depth-specific regression metrics."""
    residuals = preds - targets
    metrics = {
        "rmse": float(np.sqrt(np.mean(residuals ** 2))),
        "mae": float(np.mean(np.abs(residuals))),
        "bias": float(np.mean(residuals)),
        "r2": float(1 - np.sum(residuals ** 2) / np.sum((targets - targets.mean()) ** 2 + 1e-8)),
        "median_ae": float(np.median(np.abs(residuals))),
        "p90_ae": float(np.percentile(np.abs(residuals), 90)),
        "n": len(preds),
    }

    # Per depth bin metrics
    for lo, hi, name in DEPTH_BINS:
        mask = (targets >= lo) & (targets < hi)
        if mask.sum() > 5:
            bin_res = residuals[mask]
            metrics[f"rmse_{name}"] = float(np.sqrt(np.mean(bin_res ** 2)))
            metrics[f"mae_{name}"] = float(np.mean(np.abs(bin_res)))
            metrics[f"n_{name}"] = int(mask.sum())

    return metrics


# ── Logger ──────────────────────────────────────────────────────────

class JSONLogger:
    """W&B-style JSON lines logger to file."""

    def __init__(self, log_dir: Path):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / "metrics.jsonl"
        self.config_path = log_dir / "config.json"
        self._fh = open(self.log_path, "w")

    def log_config(self, config: dict):
        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=2, default=str)

    def log(self, metrics: dict, step: int):
        entry = {"step": step, "timestamp": datetime.now().isoformat()}
        entry.update(metrics)
        self._fh.write(json.dumps(entry) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()


# ── Training Loop ──────────────────────────────────────────────────

def train_one_epoch(
    model: nn.Module,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    loss_fn,
    scaler: GradScaler,
    device: torch.device,
    mode: str,
    epoch: int,
    max_grad_norm: float = 1.0,
) -> dict[str, float]:
    """Train for one epoch with mixed precision."""
    model.train()
    total_losses = {}
    n_batches = 0

    for batch_idx, (x, y) in enumerate(loader):
        x = x.to(device, non_blocking=True)
        y = y.to(device, non_blocking=True)

        optimizer.zero_grad(set_to_none=True)

        with autocast(device_type=device.type, enabled=(device.type == "cuda")):
            output = model(x, mode=mode)
            losses = loss_fn(
                output, y,
                spectral=x if mode == "patch" else x,
                uncertainty=output.get("uncertainty"),
            )

        scaler.scale(losses["total"]).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm)
        scaler.step(optimizer)
        scaler.update()

        # Accumulate losses
        for k, v in losses.items():
            total_losses[k] = total_losses.get(k, 0.0) + v.item()
        n_batches += 1

    return {k: v / max(n_batches, 1) for k, v in total_losses.items()}


@torch.no_grad()
def evaluate(
    model: nn.Module,
    loader: DataLoader,
    loss_fn,
    device: torch.device,
    mode: str,
) -> tuple[dict[str, float], dict[str, float]]:
    """Evaluate model on a dataset. Returns (losses, metrics)."""
    model.eval()
    all_preds = []
    all_targets = []
    total_losses = {}
    n_batches = 0

    for x, y in loader:
        x = x.to(device, non_blocking=True)
        y = y.to(device, non_blocking=True)

        with autocast(device_type=device.type, enabled=(device.type == "cuda")):
            output = model(x, mode=mode)
            losses = loss_fn(
                output, y,
                spectral=x,
                uncertainty=output.get("uncertainty"),
            )

        all_preds.append(output["depth"].cpu().numpy().flatten())
        all_targets.append(y.cpu().numpy().flatten())

        for k, v in losses.items():
            total_losses[k] = total_losses.get(k, 0.0) + v.item()
        n_batches += 1

    avg_losses = {k: v / max(n_batches, 1) for k, v in total_losses.items()}
    preds = np.concatenate(all_preds)
    targets = np.concatenate(all_targets)
    metrics = compute_metrics(preds, targets)

    return avg_losses, metrics


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train Enhanced BathyFormer")

    # Data
    parser.add_argument("--data", type=str, required=True,
                        help="Path to training parquet file")
    parser.add_argument("--patch-dir", type=str, default=None,
                        help="Directory with pre-extracted S2 patches (enables patch mode)")
    parser.add_argument("--output", type=str, default="/data/models/bathyformer",
                        help="Output directory for model and logs")
    parser.add_argument("--mode", type=str, default="auto",
                        choices=["auto", "patch", "point"],
                        help="Training mode: patch (image), point (tabular), or auto")

    # Model architecture
    parser.add_argument("--model-size", type=str, default="base",
                        choices=["tiny", "small", "base"],
                        help="Model size variant")
    parser.add_argument("--img-size", type=int, default=64,
                        help="Patch image size (patch mode only)")
    parser.add_argument("--in-channels", type=int, default=10,
                        help="Number of spectral channels (10=S2, 16=S2+Landsat)")
    parser.add_argument("--max-depth", type=float, default=30.0,
                        help="Maximum predicted depth (m)")
    parser.add_argument("--no-dual-head", action="store_true",
                        help="Disable dual-band gated heads")
    parser.add_argument("--no-uncertainty", action="store_true",
                        help="Disable uncertainty estimation")

    # Training
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=512,
                        help="Batch size (512 for point, 32 for patch)")
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-epochs", type=int, default=10)
    parser.add_argument("--min-lr", type=float, default=1e-6)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument("--depth-balanced", action="store_true", default=True,
                        help="Use depth-balanced sampling")
    parser.add_argument("--no-depth-balanced", action="store_true",
                        help="Disable depth-balanced sampling")

    # Physics loss weights
    parser.add_argument("--alpha-beer-lambert", type=float, default=0.1)
    parser.add_argument("--beta-smooth", type=float, default=0.05)
    parser.add_argument("--delta-gate", type=float, default=0.05)

    # Refraction
    parser.add_argument("--no-refraction", action="store_true",
                        help="Skip ICESat-2 refraction correction")

    # Hardware
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)

    # Validation
    parser.add_argument("--val-frac", type=float, default=0.15)
    parser.add_argument("--test-frac", type=float, default=0.15)
    parser.add_argument("--patience", type=int, default=30,
                        help="Early stopping patience (epochs)")

    args = parser.parse_args()

    # ── Setup ──────────────────────────────────────────────────
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(args.seed)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = JSONLogger(output_dir / "logs")

    log.info(f"Device: {device}")
    log.info(f"Output: {output_dir}")

    # ── Load data ─────────────────────────────────────────────
    log.info(f"Loading data from {args.data}")
    df = pd.read_parquet(args.data)
    log.info(f"Loaded {len(df)} samples, columns: {list(df.columns)}")

    # Determine mode
    mode = args.mode
    if mode == "auto":
        if args.patch_dir and Path(args.patch_dir).exists():
            mode = "patch"
            log.info("Auto-detected patch mode (patch directory exists)")
        else:
            mode = "point"
            log.info("Auto-detected point mode (no patch directory)")

    # Adjust batch size for mode
    if mode == "patch" and args.batch_size > 64:
        log.info(f"Reducing batch size from {args.batch_size} to 32 for patch mode")
        args.batch_size = 32

    # ── Spatial split ─────────────────────────────────────────
    train_df, val_df, test_df = spatial_split(
        df, val_frac=args.val_frac, test_frac=args.test_frac, seed=args.seed,
    )

    # ── Create datasets ──────────────────────────────────────
    apply_refraction = not args.no_refraction
    depth_balanced = args.depth_balanced and not args.no_depth_balanced

    if mode == "point":
        # Determine available features
        all_candidates = SPECTRAL_FEATURES + ICESAT2_FEATURES
        features = [f for f in all_candidates if f in train_df.columns]
        log.info(f"Using {len(features)} features for point mode")

        train_ds = PointDataset(
            train_df, features, apply_refraction=apply_refraction, augment=True,
        )
        val_ds = PointDataset(
            val_df, features, apply_refraction=apply_refraction, augment=False,
            mean=train_ds.mean, std=train_ds.std,
        )
        test_ds = PointDataset(
            test_df, features, apply_refraction=apply_refraction, augment=False,
            mean=train_ds.mean, std=train_ds.std,
        )
        in_features = len(train_ds.features)

    else:  # patch mode
        train_ds = PatchDataset(
            train_df, args.patch_dir, args.img_size, args.in_channels,
            apply_refraction=apply_refraction, augment=True,
        )
        val_ds = PatchDataset(
            val_df, args.patch_dir, args.img_size, args.in_channels,
            apply_refraction=apply_refraction, augment=False,
        )
        test_ds = PatchDataset(
            test_df, args.patch_dir, args.img_size, args.in_channels,
            apply_refraction=apply_refraction, augment=False,
        )
        in_features = 28  # fallback for point embed

    # ── Samplers ──────────────────────────────────────────────
    if depth_balanced:
        weights = train_ds.get_depth_weights()
        sampler = WeightedRandomSampler(weights, len(train_ds), replacement=True)
        train_loader = DataLoader(
            train_ds, batch_size=args.batch_size, sampler=sampler,
            num_workers=args.num_workers, pin_memory=True, drop_last=True,
        )
    else:
        train_loader = DataLoader(
            train_ds, batch_size=args.batch_size, shuffle=True,
            num_workers=args.num_workers, pin_memory=True, drop_last=True,
        )

    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size * 2, shuffle=False,
        num_workers=args.num_workers, pin_memory=True,
    )
    test_loader = DataLoader(
        test_ds, batch_size=args.batch_size * 2, shuffle=False,
        num_workers=args.num_workers, pin_memory=True,
    )

    # ── Build model ──────────────────────────────────────────
    from bathyformer_model import (
        BathyFormer, PhysicsInformedLoss,
        bathyformer_tiny, bathyformer_small, bathyformer_base,
    )

    model_builders = {
        "tiny": bathyformer_tiny,
        "small": bathyformer_small,
        "base": bathyformer_base,
    }

    model_kwargs = dict(
        in_channels=args.in_channels,
        in_features=in_features,
        img_size=args.img_size,
        max_depth=args.max_depth,
        dual_head=not args.no_dual_head,
        predict_uncertainty=not args.no_uncertainty,
        dropout=args.dropout,
    )

    model = model_builders[args.model_size](**model_kwargs).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    log.info(f"Model: BathyFormer-{args.model_size} ({n_params:,} parameters)")

    # ── Loss function ─────────────────────────────────────────
    loss_fn = PhysicsInformedLoss(
        alpha_beer_lambert=args.alpha_beer_lambert,
        beta_smooth=args.beta_smooth if mode == "patch" else 0.0,
        delta_gate=args.delta_gate,
        use_huber=True,
        huber_delta=1.0,
    )

    # ── Optimizer + scheduler ────────────────────────────────
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=args.lr,
        weight_decay=args.weight_decay,
        betas=(0.9, 0.999),
    )

    # Cosine annealing with warm restarts
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=50, T_mult=2, eta_min=args.min_lr,
    )

    # Warmup: linearly increase LR for first N epochs
    warmup_scheduler = torch.optim.lr_scheduler.LinearLR(
        optimizer, start_factor=0.01, end_factor=1.0, total_iters=args.warmup_epochs,
    )
    combined_scheduler = torch.optim.lr_scheduler.SequentialLR(
        optimizer,
        schedulers=[warmup_scheduler, scheduler],
        milestones=[args.warmup_epochs],
    )

    scaler = GradScaler(enabled=(device.type == "cuda"))

    # ── Log config ───────────────────────────────────────────
    config = {
        "model_size": args.model_size,
        "mode": mode,
        "n_params": n_params,
        "n_train": len(train_ds),
        "n_val": len(val_ds),
        "n_test": len(test_ds),
        "n_features": in_features if mode == "point" else args.in_channels,
        "img_size": args.img_size if mode == "patch" else None,
        "batch_size": args.batch_size,
        "lr": args.lr,
        "weight_decay": args.weight_decay,
        "epochs": args.epochs,
        "dropout": args.dropout,
        "max_depth": args.max_depth,
        "dual_head": not args.no_dual_head,
        "uncertainty": not args.no_uncertainty,
        "refraction_correction": apply_refraction,
        "depth_balanced": depth_balanced,
        "alpha_beer_lambert": args.alpha_beer_lambert,
        "beta_smooth": args.beta_smooth,
        "delta_gate": args.delta_gate,
        "device": str(device),
        "seed": args.seed,
    }
    logger.log_config(config)
    log.info(f"Config: {json.dumps(config, indent=2)}")

    # ── Training loop ─────────────────────────────────────────
    best_val_rmse = float("inf")
    best_epoch = 0
    patience_counter = 0
    start_time = time.time()

    log.info(f"\n{'='*60}")
    log.info(f"Starting training: {args.epochs} epochs, mode={mode}")
    log.info(f"{'='*60}\n")

    for epoch in range(1, args.epochs + 1):
        epoch_start = time.time()

        # Train
        train_losses = train_one_epoch(
            model, train_loader, optimizer, loss_fn, scaler,
            device, mode, epoch, args.max_grad_norm,
        )

        # Validate
        val_losses, val_metrics = evaluate(model, val_loader, loss_fn, device, mode)

        # Step scheduler
        combined_scheduler.step()
        current_lr = optimizer.param_groups[0]["lr"]

        epoch_time = time.time() - epoch_start

        # ── Logging ──────────────────────────────────────────
        log_entry = {
            "epoch": epoch,
            "lr": current_lr,
            "epoch_time": epoch_time,
        }
        for k, v in train_losses.items():
            log_entry[f"train/{k}"] = v
        for k, v in val_losses.items():
            log_entry[f"val/loss_{k}"] = v
        for k, v in val_metrics.items():
            log_entry[f"val/{k}"] = v

        logger.log(log_entry, step=epoch)

        # Console output
        val_rmse = val_metrics["rmse"]
        val_mae = val_metrics["mae"]
        val_r2 = val_metrics["r2"]

        is_best = val_rmse < best_val_rmse
        marker = " *BEST*" if is_best else ""

        if epoch % 5 == 0 or epoch <= 3 or is_best:
            log.info(
                f"Epoch {epoch:3d}/{args.epochs} | "
                f"LR {current_lr:.2e} | "
                f"Train loss {train_losses['total']:.4f} | "
                f"Val RMSE {val_rmse:.3f}m MAE {val_mae:.3f}m R2 {val_r2:.3f} | "
                f"{epoch_time:.1f}s{marker}"
            )

            # Print per-bin RMSE periodically
            if epoch % 20 == 0 or is_best:
                bin_strs = []
                for _, _, name in DEPTH_BINS:
                    key = f"rmse_{name}"
                    if key in val_metrics:
                        bin_strs.append(f"{name}: {val_metrics[key]:.3f}m")
                if bin_strs:
                    log.info(f"  Depth bins: {' | '.join(bin_strs)}")

        # ── Best model + early stopping ──────────────────────
        if is_best:
            best_val_rmse = val_rmse
            best_epoch = epoch
            patience_counter = 0

            # Save best model
            ckpt = {
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_rmse": val_rmse,
                "val_metrics": val_metrics,
                "config": config,
            }
            if mode == "point":
                ckpt["feature_mean"] = train_ds.mean
                ckpt["feature_std"] = train_ds.std
                ckpt["features"] = train_ds.features

            torch.save(ckpt, output_dir / "best_model.pt")

        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                log.info(f"Early stopping at epoch {epoch} "
                         f"(best: epoch {best_epoch}, RMSE {best_val_rmse:.3f}m)")
                break

        # Save periodic checkpoint
        if epoch % 50 == 0:
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
            }, output_dir / f"checkpoint_epoch{epoch}.pt")

    total_time = time.time() - start_time
    log.info(f"\nTraining complete in {total_time / 60:.1f} minutes")
    log.info(f"Best val RMSE: {best_val_rmse:.3f}m at epoch {best_epoch}")

    # ── Final evaluation on test set ──────────────────────────
    log.info(f"\n{'='*60}")
    log.info("Final evaluation on held-out test lakes")
    log.info(f"{'='*60}")

    # Load best model
    ckpt = torch.load(output_dir / "best_model.pt", weights_only=False)
    model.load_state_dict(ckpt["model_state_dict"])

    test_losses, test_metrics = evaluate(model, test_loader, loss_fn, device, mode)

    log.info(f"\nTest Results (unseen lakes):")
    log.info(f"  RMSE:      {test_metrics['rmse']:.3f}m")
    log.info(f"  MAE:       {test_metrics['mae']:.3f}m")
    log.info(f"  Bias:      {test_metrics['bias']:.3f}m")
    log.info(f"  R2:        {test_metrics['r2']:.3f}")
    log.info(f"  Median AE: {test_metrics['median_ae']:.3f}m")
    log.info(f"  P90 AE:    {test_metrics['p90_ae']:.3f}m")
    log.info(f"  N:         {test_metrics['n']}")

    log.info("\nPer-depth-bin RMSE:")
    for _, _, name in DEPTH_BINS:
        key = f"rmse_{name}"
        n_key = f"n_{name}"
        if key in test_metrics:
            log.info(f"  {name:>6s}: RMSE {test_metrics[key]:.3f}m "
                     f"(n={test_metrics.get(n_key, '?')})")

    # ── MC Dropout uncertainty on test set ────────────────────
    if not args.no_uncertainty:
        log.info("\nMC Dropout uncertainty estimation (20 passes)...")
        model.train()  # Enable dropout
        mc_preds = []
        mc_targets = []

        with torch.no_grad():
            for x, y in test_loader:
                x = x.to(device)
                mc_batch = []
                for _ in range(20):
                    out = model(x, mode=mode)
                    mc_batch.append(out["depth"].cpu().numpy().flatten())
                mc_preds.append(np.stack(mc_batch, axis=0))
                mc_targets.append(y.numpy().flatten())

        model.eval()
        mc_preds = np.concatenate(mc_preds, axis=1)  # (20, N)
        mc_targets = np.concatenate(mc_targets)

        epistemic_std = mc_preds.std(axis=0)
        mean_pred = mc_preds.mean(axis=0)

        log.info(f"  Mean epistemic uncertainty: {epistemic_std.mean():.3f}m")
        log.info(f"  Median epistemic uncertainty: {np.median(epistemic_std):.3f}m")

        # Calibration: does uncertainty correlate with error?
        abs_error = np.abs(mean_pred - mc_targets)
        corr = np.corrcoef(epistemic_std, abs_error)[0, 1]
        log.info(f"  Uncertainty-error correlation: {corr:.3f}")

    # ── Save final results ───────────────────────────────────
    results = {
        "best_epoch": best_epoch,
        "best_val_rmse": best_val_rmse,
        "test_metrics": test_metrics,
        "training_time_minutes": total_time / 60,
        "config": config,
    }
    with open(output_dir / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    logger.close()

    log.info(f"\nResults saved to {output_dir}")
    log.info(f"Best model: {output_dir / 'best_model.pt'}")
    log.info(f"Metrics log: {output_dir / 'logs' / 'metrics.jsonl'}")

    # ── Summary ──────────────────────────────────────────────
    target_met = test_metrics["rmse"] < 0.5
    log.info(f"\n{'='*60}")
    log.info(f"TARGET: sub-0.5m RMSE — {'ACHIEVED' if target_met else 'NOT YET'} "
             f"(got {test_metrics['rmse']:.3f}m)")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
