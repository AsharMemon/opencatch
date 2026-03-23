#!/usr/bin/env python3
"""
OpenCatch — SDB v2 Training: Physics-Informed Ensemble for Sub-1m Bathymetry

Trains on preprocessed SDB features (from extract_s2_preprocessed.py) with:
1. Weighted training by temporal match score
2. Physics-informed loss (Beer-Lambert constraint)
3. Depth-stratified sampling (equal representation across depth bins)
4. Ensemble: Stumpf linear baseline + KAN + BathyFormer, averaged

Previous: 4.65m RMSE with raw DNs
Target: <1.0m RMSE with preprocessed physics features

Architecture:
  Ensemble:
    1. Stumpf baseline: linear on stumpf_ratio (0-parameter calibration)
    2. SpectralKAN: [256, 128, 64, 32] on all SDB features
    3. BathyFormer (point mode): transformer on all SDB features
  Final = weighted average (learned or uniform)

Usage:
    python train_sdb_v2.py \
        --data /data/sdb_preprocessed.parquet \
        --output /data/models/sdb_v2 \
        --epochs 300 \
        --batch-size 1024 \
        --device cuda

Requirements:
    pip install torch pandas pyarrow numpy tqdm scikit-learn
"""

import argparse
import json
import logging
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
log = logging.getLogger("train_sdb_v2")


# ── Feature columns ─────────────────────────────────────────────────

# Physics-based features from sdb_preprocessing.py
SDB_FEATURES = [
    # Core reflectance (deglinted)
    "blue", "green", "red", "nir",
    # Log-transformed (linearizes Beer-Lambert decay)
    "log_blue", "log_green", "log_red", "log_nir",
    # Stumpf (2003) log-ratio
    "stumpf_ratio",
    # Lyzenga (1978) depth-invariant indices
    "lyzenga_bg", "lyzenga_br", "lyzenga_gr",
    # Water indices
    "ndwi", "mndwi", "ndvi",
    # Band ratios
    "blue_green_ratio", "blue_red_ratio", "green_red_ratio",
    # Water quality
    "turbidity_index", "cdom_proxy", "ndti",
    # Relative band depth
    "rel_blue", "rel_green", "rel_red",
    # Second-order interactions
    "blue_x_green", "blue_x_red", "green_x_red",
    "blue_sq", "green_sq",
    # Band differences
    "blue_minus_green", "green_minus_red", "red_minus_nir",
    # Extended (if available)
    "rededge1", "rededge2", "rededge3", "nir08",
    "swir16", "swir22",
    "cdom_rededge", "fai",
]

DEPTH_BINS = [(0, 2, "0-2m"), (2, 5, "2-5m"), (5, 10, "5-10m"),
              (10, 20, "10-20m"), (20, 50, "20-50m")]


# ── Dataset ──────────────────────────────────────────────────────────

class SDBDataset(Dataset):
    """
    SDB training dataset with temporal match weighting.

    Each sample has:
    - features: preprocessed SDB feature vector
    - depth: target depth in metres
    - weight: temporal match quality score (closer S2-ICESat2 date = higher weight)
    """

    def __init__(
        self,
        df: pd.DataFrame,
        features: list[str],
        augment: bool = False,
        noise_scale: float = 0.01,
    ):
        self.features = features

        # Validate
        missing = [f for f in features if f not in df.columns]
        if missing:
            log.warning(f"Missing features (will use 0): {missing}")
            for m in missing:
                df[m] = 0.0

        self.X = torch.tensor(df[features].values, dtype=torch.float32)
        self.y = torch.tensor(df["depth_m"].values, dtype=torch.float32)

        # Temporal match weights (default 1.0 if not available)
        if "temporal_match_score" in df.columns:
            weights = df["temporal_match_score"].fillna(0.5).values
            self.weights = torch.tensor(weights, dtype=torch.float32)
        else:
            self.weights = torch.ones(len(df), dtype=torch.float32)

        # Normalize features
        self.mean = self.X.mean(dim=0)
        self.std = self.X.std(dim=0).clamp(min=1e-6)
        self.X = (self.X - self.mean) / self.std

        # Handle NaN in normalized features
        self.X = torch.nan_to_num(self.X, nan=0.0)

        self.augment = augment
        self.noise_scale = noise_scale

        log.info(f"  Dataset: {len(self)} samples, {len(features)} features")
        log.info(f"  Depth: mean={self.y.mean():.2f}m, range=[{self.y.min():.1f}, {self.y.max():.1f}]m")
        log.info(f"  Weight: mean={self.weights.mean():.3f}, min={self.weights.min():.3f}")

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        x = self.X[idx]
        y = self.y[idx]
        w = self.weights[idx]

        if self.augment:
            noise = torch.randn_like(x) * self.noise_scale
            x = x + noise

        return x, y, w


# ── Models ───────────────────────────────────────────────────────────

class StumpfBaseline(nn.Module):
    """
    Linear Stumpf model: depth = a + b * stumpf_ratio

    Calibrated via least-squares on training data. During ensemble training,
    the parameters are frozen (serves as an anchor).
    """

    def __init__(self, stumpf_idx: int, max_depth: float = 50.0):
        super().__init__()
        self.stumpf_idx = stumpf_idx
        self.max_depth = max_depth
        self.linear = nn.Linear(1, 1)

    def forward(self, x):
        stumpf = x[:, self.stumpf_idx:self.stumpf_idx + 1]
        depth = self.linear(stumpf)
        return F.softplus(depth).squeeze(-1).clamp(0, self.max_depth)

    def calibrate(self, X: torch.Tensor, y: torch.Tensor):
        """Closed-form calibration from training data."""
        stumpf = X[:, self.stumpf_idx].numpy()
        depths = y.numpy()

        valid = np.isfinite(stumpf) & np.isfinite(depths)
        stumpf = stumpf[valid]
        depths = depths[valid]

        if len(stumpf) < 10:
            return

        from numpy.polynomial import polynomial as P
        coeffs = P.polyfit(stumpf, depths, 1)  # [intercept, slope]
        with torch.no_grad():
            self.linear.bias.fill_(coeffs[0])
            self.linear.weight.fill_(coeffs[1])

        pred = coeffs[0] + coeffs[1] * stumpf
        rmse = np.sqrt(np.mean((pred - depths) ** 2))
        log.info(f"Stumpf calibrated: depth = {coeffs[0]:.3f} + {coeffs[1]:.3f} * ratio, RMSE={rmse:.3f}m")


class SpectralKANv2(nn.Module):
    """
    Improved SpectralKAN for SDB v2 features.
    Uses SiLU activation, batch norm, and residual connections.
    """

    def __init__(
        self,
        in_features: int,
        hidden_dims: list[int] = None,
        dropout: float = 0.1,
        max_depth: float = 50.0,
    ):
        super().__init__()
        self.max_depth = max_depth

        if hidden_dims is None:
            hidden_dims = [256, 128, 64, 32]

        layers = []
        dims = [in_features] + hidden_dims
        for i in range(len(dims) - 1):
            layers.append(nn.Linear(dims[i], dims[i + 1]))
            layers.append(nn.BatchNorm1d(dims[i + 1]))
            layers.append(nn.SiLU())
            layers.append(nn.Dropout(dropout))

        self.backbone = nn.Sequential(*layers)

        # Depth head with Softplus (ensures non-negative output)
        self.head = nn.Sequential(
            nn.Linear(hidden_dims[-1], 16),
            nn.SiLU(),
            nn.Linear(16, 1),
            nn.Softplus(),
        )

    def forward(self, x):
        h = self.backbone(x)
        depth = self.head(h).squeeze(-1)
        return depth.clamp(0, self.max_depth)


class PointBathyFormer(nn.Module):
    """
    Simplified BathyFormer for point-mode (tabular) features.
    Uses self-attention to capture inter-feature dependencies.
    """

    def __init__(
        self,
        in_features: int,
        d_model: int = 128,
        n_heads: int = 4,
        n_layers: int = 3,
        dropout: float = 0.1,
        max_depth: float = 50.0,
    ):
        super().__init__()
        self.max_depth = max_depth

        # Project each feature to d_model dims (treat features as "tokens")
        self.input_proj = nn.Linear(1, d_model)
        self.pos_emb = nn.Parameter(torch.randn(1, in_features, d_model) * 0.02)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Dual-band gated depth head (shallow vs deep)
        self.shallow_head = nn.Sequential(
            nn.Linear(d_model, 32), nn.SiLU(), nn.Linear(32, 1), nn.Softplus()
        )
        self.deep_head = nn.Sequential(
            nn.Linear(d_model, 32), nn.SiLU(), nn.Linear(32, 1), nn.Softplus()
        )
        self.gate = nn.Sequential(
            nn.Linear(d_model, 32), nn.SiLU(), nn.Linear(32, 1), nn.Sigmoid()
        )

    def forward(self, x):
        B, F = x.shape

        # Each feature becomes a "token"
        tokens = x.unsqueeze(-1)  # (B, F, 1)
        tokens = self.input_proj(tokens)  # (B, F, d_model)
        tokens = tokens + self.pos_emb[:, :F, :]

        # Self-attention across features
        encoded = self.encoder(tokens)  # (B, F, d_model)

        # Pool: mean over feature tokens
        pooled = encoded.mean(dim=1)  # (B, d_model)

        # Gated dual-head
        shallow = self.shallow_head(pooled).squeeze(-1)
        deep = self.deep_head(pooled).squeeze(-1)
        gate = self.gate(pooled).squeeze(-1)  # 0=shallow, 1=deep

        depth = (1 - gate) * shallow + gate * deep
        return depth.clamp(0, self.max_depth)


class SDBEnsemble(nn.Module):
    """
    Ensemble: Stumpf baseline + SpectralKAN + PointBathyFormer

    Combines predictions via learned weights. The Stumpf baseline provides
    a physics anchor, while the neural models capture non-linear patterns.
    """

    def __init__(
        self,
        in_features: int,
        stumpf_idx: int,
        kan_hidden: list[int] = None,
        transformer_d_model: int = 128,
        max_depth: float = 50.0,
    ):
        super().__init__()
        self.max_depth = max_depth

        # Component models
        self.stumpf = StumpfBaseline(stumpf_idx, max_depth)
        self.kan = SpectralKANv2(in_features, kan_hidden, max_depth=max_depth)
        self.transformer = PointBathyFormer(
            in_features, d_model=transformer_d_model, max_depth=max_depth
        )

        # Learned ensemble weights (log-space, softmax to sum to 1)
        self.ensemble_logits = nn.Parameter(torch.tensor([0.5, 1.0, 1.0]))

    def forward(self, x):
        d_stumpf = self.stumpf(x)
        d_kan = self.kan(x)
        d_transformer = self.transformer(x)

        # Softmax ensemble weights
        weights = F.softmax(self.ensemble_logits, dim=0)

        depth = weights[0] * d_stumpf + weights[1] * d_kan + weights[2] * d_transformer
        return depth.clamp(0, self.max_depth)

    def get_ensemble_weights(self) -> dict[str, float]:
        weights = F.softmax(self.ensemble_logits, dim=0).detach().cpu()
        return {
            "stumpf": weights[0].item(),
            "kan": weights[1].item(),
            "transformer": weights[2].item(),
        }

    def predict_components(self, x) -> dict[str, torch.Tensor]:
        """Return individual model predictions (for analysis)."""
        return {
            "stumpf": self.stumpf(x),
            "kan": self.kan(x),
            "transformer": self.transformer(x),
        }


# ── Physics-Informed Loss ────────────────────────────────────────────

class BeerLambertLoss(nn.Module):
    """
    Physics-informed loss combining:
    1. Weighted Huber loss (robust, weighted by temporal match score)
    2. Beer-Lambert consistency: deeper water should have lower blue reflectance
    3. Non-negativity enforcement
    4. Depth-gradient smoothness
    """

    def __init__(
        self,
        huber_delta: float = 2.0,
        beer_lambert_weight: float = 0.1,
        smoothness_weight: float = 0.01,
        blue_idx: int = 0,
    ):
        super().__init__()
        self.huber = nn.HuberLoss(delta=huber_delta, reduction="none")
        self.beer_lambert_weight = beer_lambert_weight
        self.smoothness_weight = smoothness_weight
        self.blue_idx = blue_idx

    def forward(
        self,
        pred: torch.Tensor,
        target: torch.Tensor,
        features: torch.Tensor,
        weights: torch.Tensor,
    ) -> torch.Tensor:
        # 1. Weighted Huber loss
        pixel_loss = self.huber(pred, target)
        weighted_loss = (pixel_loss * weights).mean()

        # 2. Beer-Lambert constraint: deeper -> less blue reflectance
        # Sample random pairs and check consistency
        if self.beer_lambert_weight > 0 and self.blue_idx >= 0 and len(pred) > 20:
            n_pairs = min(len(pred), 256)
            idx_a = torch.randint(0, len(pred), (n_pairs,), device=pred.device)
            idx_b = torch.randint(0, len(pred), (n_pairs,), device=pred.device)

            depth_diff = pred[idx_a] - pred[idx_b]  # Positive = a deeper
            blue_diff = features[idx_a, self.blue_idx] - features[idx_b, self.blue_idx]

            # Penalise: depth increases AND blue increases (should be inverse)
            violation = F.relu(depth_diff * blue_diff)
            beer_lambert_loss = violation.mean()

            weighted_loss = weighted_loss + self.beer_lambert_weight * beer_lambert_loss

        # 3. Smoothness: penalise large differences between nearby predictions
        # (sorted by index, which is roughly spatial in tile-batched data)
        if self.smoothness_weight > 0 and len(pred) > 2:
            diff = (pred[1:] - pred[:-1]).abs()
            # Only penalise very large jumps (>10m between adjacent points)
            smoothness_penalty = F.relu(diff - 10.0).mean()
            weighted_loss = weighted_loss + self.smoothness_weight * smoothness_penalty

        return weighted_loss


# ── Depth-Stratified Sampler ─────────────────────────────────────────

def create_depth_sampler(depths: np.ndarray, bins: list[tuple] = None) -> WeightedRandomSampler:
    """
    Create a weighted sampler that balances representation across depth bins.

    Without this, shallow points (0-5m) dominate training because ICESat-2
    detects more shallow-water bathymetry than deep.
    """
    if bins is None:
        bins = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 50)]

    bin_labels = np.zeros(len(depths), dtype=int)
    for i, (lo, hi) in enumerate(bins):
        mask = (depths >= lo) & (depths < hi)
        bin_labels[mask] = i

    bin_counts = np.bincount(bin_labels, minlength=len(bins))
    bin_counts = np.maximum(bin_counts, 1)  # Avoid division by zero

    # Weight = inverse frequency
    bin_weights = 1.0 / bin_counts
    sample_weights = bin_weights[bin_labels]
    sample_weights = sample_weights / sample_weights.sum()

    return WeightedRandomSampler(
        weights=sample_weights,
        num_samples=len(sample_weights),
        replacement=True,
    )


# ── Training Loop ────────────────────────────────────────────────────

def train(
    df: pd.DataFrame,
    output_dir: Path,
    epochs: int = 300,
    batch_size: int = 1024,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    patience: int = 40,
    device: str = "cuda",
    max_depth: float = 50.0,
    kan_hidden: list[int] = None,
    transformer_d_model: int = 128,
    beer_lambert_weight: float = 0.1,
):
    """Full training pipeline with ensemble."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine available features
    available = [f for f in SDB_FEATURES if f in df.columns]
    if len(available) < 5:
        log.error(f"Only {len(available)} SDB features found. Need at least 5. "
                  f"Did you run extract_s2_preprocessed.py first?")
        return
    log.info(f"Using {len(available)} SDB features")

    # Check for stumpf_ratio (critical for ensemble)
    if "stumpf_ratio" not in available:
        log.warning("stumpf_ratio not found! Ensemble will use KAN+Transformer only.")
        stumpf_idx = -1
    else:
        stumpf_idx = available.index("stumpf_ratio")

    # Splits
    if "split" not in df.columns:
        log.warning("No 'split' column. Creating random split by lake_id.")
        if "lake_id" in df.columns:
            lakes = df["lake_id"].unique()
            np.random.seed(42)
            np.random.shuffle(lakes)
            n_train = int(len(lakes) * 0.70)
            n_val = int(len(lakes) * 0.15)
            train_l = set(lakes[:n_train])
            val_l = set(lakes[n_train:n_train + n_val])
            df["split"] = "test"
            df.loc[df["lake_id"].isin(train_l), "split"] = "train"
            df.loc[df["lake_id"].isin(val_l), "split"] = "val"
        else:
            df["split"] = np.random.choice(["train", "val", "test"],
                                            size=len(df), p=[0.7, 0.15, 0.15])

    train_df = df[df["split"] == "train"].reset_index(drop=True)
    val_df = df[df["split"] == "val"].reset_index(drop=True)
    test_df = df[df["split"] == "test"].reset_index(drop=True)
    log.info(f"Train: {len(train_df):,}  Val: {len(val_df):,}  Test: {len(test_df):,}")

    # Datasets
    train_ds = SDBDataset(train_df, available, augment=True)
    val_ds = SDBDataset(val_df, available, augment=False)

    # Use training normalization for val
    val_ds.mean = train_ds.mean
    val_ds.std = train_ds.std
    val_ds.X = (torch.tensor(val_df[available].values, dtype=torch.float32) - train_ds.mean) / train_ds.std
    val_ds.X = torch.nan_to_num(val_ds.X, nan=0.0)

    # Depth-stratified sampler
    sampler = create_depth_sampler(train_df["depth_m"].values)

    train_loader = DataLoader(
        train_ds, batch_size=batch_size, sampler=sampler,
        num_workers=4, pin_memory=True, drop_last=True,
    )
    val_loader = DataLoader(
        val_ds, batch_size=batch_size * 2,
        num_workers=2, pin_memory=True,
    )

    # Model
    if kan_hidden is None:
        kan_hidden = [256, 128, 64, 32]

    model = SDBEnsemble(
        in_features=len(available),
        stumpf_idx=stumpf_idx,
        kan_hidden=kan_hidden,
        transformer_d_model=transformer_d_model,
        max_depth=max_depth,
    )

    # Calibrate Stumpf baseline on training data
    if stumpf_idx >= 0:
        model.stumpf.calibrate(train_ds.X, train_ds.y)

    n_params = sum(p.numel() for p in model.parameters())
    log.info(f"Ensemble model: {n_params:,} parameters")
    model = model.to(device)

    # Optimizer
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=50, T_mult=2, eta_min=1e-6
    )

    blue_idx = available.index("blue") if "blue" in available else -1
    criterion = BeerLambertLoss(
        beer_lambert_weight=beer_lambert_weight,
        blue_idx=blue_idx,
    )
    scaler = torch.amp.GradScaler("cuda") if "cuda" in device else None

    # Training
    best_val_rmse = float("inf")
    best_epoch = 0
    no_improve = 0
    history = []

    log.info(f"\nStarting training: {epochs} epochs, batch_size={batch_size}, device={device}")
    t0 = time.time()

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        n_batches = 0

        for x_batch, y_batch, w_batch in train_loader:
            x_batch = x_batch.to(device)
            y_batch = y_batch.to(device)
            w_batch = w_batch.to(device)

            optimizer.zero_grad()

            if scaler:
                with torch.amp.autocast("cuda"):
                    pred = model(x_batch)
                    loss = criterion(pred, y_batch, x_batch, w_batch)
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                pred = model(x_batch)
                loss = criterion(pred, y_batch, x_batch, w_batch)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()

            train_loss += loss.item()
            n_batches += 1

        scheduler.step()
        avg_loss = train_loss / max(n_batches, 1)

        # Validation
        model.eval()
        val_preds, val_targets = [], []
        with torch.no_grad():
            for x_batch, y_batch, _ in val_loader:
                x_batch = x_batch.to(device)
                pred = model(x_batch)
                val_preds.append(pred.cpu())
                val_targets.append(y_batch)

        val_preds = torch.cat(val_preds)
        val_targets = torch.cat(val_targets)

        val_rmse = ((val_preds - val_targets) ** 2).mean().sqrt().item()
        val_mae = (val_preds - val_targets).abs().mean().item()
        ss_res = ((val_preds - val_targets) ** 2).sum()
        ss_tot = ((val_targets - val_targets.mean()) ** 2).sum()
        val_r2 = (1 - ss_res / ss_tot).item() if ss_tot > 0 else 0.0

        # Per-depth-bin RMSE
        bin_rmses = {}
        for lo, hi, label in DEPTH_BINS:
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

            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_rmse": val_rmse,
                "val_mae": val_mae,
                "val_r2": val_r2,
                "features": available,
                "norm_mean": train_ds.mean.numpy().tolist(),
                "norm_std": train_ds.std.numpy().tolist(),
                "ensemble_weights": model.get_ensemble_weights(),
                "kan_hidden": kan_hidden,
                "transformer_d_model": transformer_d_model,
                "max_depth": max_depth,
                "n_features": len(available),
            }, output_dir / "best_model.pt")
        else:
            no_improve += 1

        record = {
            "epoch": epoch,
            "train_loss": avg_loss,
            "val_rmse": val_rmse,
            "val_mae": val_mae,
            "val_r2": val_r2,
            "best_rmse": best_val_rmse,
            "lr": optimizer.param_groups[0]["lr"],
            **{f"rmse_{k}": v for k, v in bin_rmses.items()},
        }
        history.append(record)

        # Logging
        if epoch % 5 == 0 or improved or epoch <= 3:
            star = " *BEST*" if improved else ""
            log.info(
                f"Epoch {epoch:3d}/{epochs}: loss={avg_loss:.4f} | "
                f"val RMSE={val_rmse:.3f}m MAE={val_mae:.3f}m R2={val_r2:.3f}"
                f"{star}"
            )
            if bin_rmses and epoch % 20 == 0:
                bins_str = " | ".join(f"{k}:{v:.2f}" for k, v in bin_rmses.items())
                log.info(f"  Depth bins: {bins_str}")
                ew = model.get_ensemble_weights()
                log.info(f"  Ensemble: stumpf={ew['stumpf']:.3f} "
                         f"kan={ew['kan']:.3f} transformer={ew['transformer']:.3f}")

        # Early stopping
        if no_improve >= patience:
            log.info(f"Early stopping at epoch {epoch} (no improvement for {patience} epochs)")
            break

    elapsed = time.time() - t0
    log.info(f"\nTraining complete in {elapsed / 60:.1f} minutes")
    log.info(f"Best val RMSE: {best_val_rmse:.3f}m (epoch {best_epoch})")
    ew = model.get_ensemble_weights()
    log.info(f"Final ensemble weights: stumpf={ew['stumpf']:.3f} "
             f"kan={ew['kan']:.3f} transformer={ew['transformer']:.3f}")

    # Save history
    pd.DataFrame(history).to_csv(output_dir / "training_history.csv", index=False)

    # Test evaluation
    if len(test_df) > 0:
        log.info("\n" + "=" * 60)
        log.info("TEST SET EVALUATION (unseen lakes)")
        log.info("=" * 60)
        _evaluate_test(model, test_df, available, train_ds, device, output_dir, max_depth)

    # Save config
    config = {
        "model": "sdb_v2_ensemble",
        "n_features": len(available),
        "features": available,
        "kan_hidden": kan_hidden,
        "transformer_d_model": transformer_d_model,
        "max_depth": max_depth,
        "best_val_rmse": best_val_rmse,
        "best_epoch": best_epoch,
        "total_epochs": epoch,
        "n_train": len(train_df),
        "n_val": len(val_df),
        "n_test": len(test_df),
        "ensemble_weights": ew,
        "preprocessing": "sdb_preprocessing.SDBPreprocessor",
    }
    with open(output_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    log.info(f"\nAll outputs saved to {output_dir}")


def _evaluate_test(model, test_df, features, train_ds, device, output_dir, max_depth):
    """Evaluate on held-out test lakes with component breakdown."""
    test_ds = SDBDataset(test_df, features, augment=False)
    test_ds.mean = train_ds.mean
    test_ds.std = train_ds.std
    test_ds.X = (torch.tensor(test_df[features].values, dtype=torch.float32) - train_ds.mean) / train_ds.std
    test_ds.X = torch.nan_to_num(test_ds.X, nan=0.0)

    loader = DataLoader(test_ds, batch_size=2048, num_workers=2)

    model.eval()
    all_preds, all_targets = [], []
    comp_preds = {"stumpf": [], "kan": [], "transformer": []}

    with torch.no_grad():
        for x, y, _ in loader:
            x = x.to(device)
            all_preds.append(model(x).cpu())
            all_targets.append(y)

            # Component predictions
            components = model.predict_components(x)
            for k, v in components.items():
                comp_preds[k].append(v.cpu())

    preds = torch.cat(all_preds)
    targets = torch.cat(all_targets)

    rmse = ((preds - targets) ** 2).mean().sqrt().item()
    mae = (preds - targets).abs().mean().item()
    ss_res = ((preds - targets) ** 2).sum()
    ss_tot = ((targets - targets.mean()) ** 2).sum()
    r2 = (1 - ss_res / ss_tot).item() if ss_tot > 0 else 0.0

    log.info(f"Test RMSE: {rmse:.3f}m")
    log.info(f"Test MAE:  {mae:.3f}m")
    log.info(f"Test R2:   {r2:.3f}")

    # Component performance
    log.info("\nComponent breakdown:")
    for name in ["stumpf", "kan", "transformer"]:
        cp = torch.cat(comp_preds[name])
        c_rmse = ((cp - targets) ** 2).mean().sqrt().item()
        log.info(f"  {name:12s}: RMSE={c_rmse:.3f}m")

    # Per-depth-bin
    log.info("\nDepth-bin RMSE:")
    for lo, hi, label in DEPTH_BINS:
        mask = (targets >= lo) & (targets < hi)
        if mask.sum() > 0:
            bin_rmse = ((preds[mask] - targets[mask]) ** 2).mean().sqrt().item()
            n = mask.sum().item()
            log.info(f"  {label}: RMSE={bin_rmse:.3f}m (n={n:,})")

    # Per-lake metrics
    test_df = test_df.copy()
    test_df["pred"] = preds.numpy()

    if "lake_id" in test_df.columns:
        lake_metrics = test_df.groupby("lake_id").apply(
            lambda g: pd.Series({
                "rmse": np.sqrt(((g["pred"] - g["depth_m"]) ** 2).mean()),
                "n_points": len(g),
            })
        ).reset_index()

        log.info(f"\nPer-lake test metrics ({len(lake_metrics)} lakes):")
        log.info(f"  Median RMSE: {lake_metrics['rmse'].median():.3f}m")
        log.info(f"  Mean RMSE:   {lake_metrics['rmse'].mean():.3f}m")
        log.info(f"  Best:        {lake_metrics['rmse'].min():.3f}m")
        log.info(f"  Worst:       {lake_metrics['rmse'].max():.3f}m")

    # Save predictions
    test_df[["lat", "lon", "depth_m", "pred"]].to_csv(
        output_dir / "test_predictions.csv", index=False
    )


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Train SDB v2 ensemble on preprocessed features",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data", type=str, required=True,
                        help="Preprocessed parquet from extract_s2_preprocessed.py")
    parser.add_argument("--output", type=str, default="/data/models/sdb_v2",
                        help="Output directory")
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=40)
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--max-depth", type=float, default=50.0)
    parser.add_argument("--kan-hidden", type=int, nargs="+", default=[256, 128, 64, 32])
    parser.add_argument("--transformer-dim", type=int, default=128)
    parser.add_argument("--beer-lambert-weight", type=float, default=0.1)

    args = parser.parse_args()

    log.info("Loading preprocessed data...")
    df = pd.read_parquet(args.data)
    log.info(f"Loaded {len(df):,} rows, {len(df.columns)} columns")

    # Show feature availability
    sdb_available = [f for f in SDB_FEATURES if f in df.columns]
    log.info(f"SDB features available: {len(sdb_available)}/{len(SDB_FEATURES)}")

    train(
        df=df,
        output_dir=Path(args.output),
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        weight_decay=args.weight_decay,
        patience=args.patience,
        device=args.device,
        max_depth=args.max_depth,
        kan_hidden=args.kan_hidden,
        transformer_d_model=args.transformer_dim,
        beer_lambert_weight=args.beer_lambert_weight,
    )


if __name__ == "__main__":
    main()
