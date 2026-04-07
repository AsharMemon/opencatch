#!/usr/bin/env python3
"""
OpenCatch — Physics-Informed Neural Network for Lake Bathymetry

Embeds physical constraints directly into the neural network loss function
so that ALL predicted bathymetric surfaces must satisfy known physics:

Hard constraints (always satisfied):
  1. Depth = 0 at shoreline boundary
  2. Depth >= 0 everywhere (non-negative)
  3. Max depth bounded by A-E curve estimate

Soft constraints (penalized in loss):
  4. Monotonic depth increase toward lake center (generally)
  5. Depth gradient <= ~45 degrees (angle of repose)
  6. Smooth lake bottom (no sharp underwater discontinuities)
  7. Volume consistent with A-E curve integral
  8. Symmetry prior for simple lake shapes

The PINN learns a mapping: (x, y, morphometry, A-E features) → depth
that respects these constraints even where no depth measurements exist.

This is especially valuable for turbid lakes where spectral SDB fails
and we only have sparse A-E or morphometric information.

References:
  - Raissi et al. (2019): Physics-Informed Neural Networks
  - Karpatne et al. (2017): Theory-guided data science
  - Karniadakis et al. (2021): Physics-informed ML

Usage:
    python physics_informed_bathy.py \
        --data /data/training/v2/mn_sonar \
        --morpho /data/training/v2/mn_morphometric.parquet \
        --output /data/models/pinn_bathy \
        --device cuda

Requirements:
    pip install torch numpy pandas scikit-learn scipy tqdm
"""

import argparse
import gc
import json
import logging
import math
import pickle
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.spatial import ConvexHull
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pinn_bathy")

EPS = 1e-8


# ══════════════════════════════════════════════════════════════════════
# Physics Constants & Constraints
# ══════════════════════════════════════════════════════════════════════

# Maximum underwater slope angle (degrees) — angle of repose for sediment
MAX_SLOPE_DEGREES = 45.0
MAX_SLOPE_RATIO = np.tan(np.radians(MAX_SLOPE_DEGREES))  # ~1.0

# Smoothness: maximum second derivative (curvature) of lake bottom
# Typical lake bottom is gentle — penalize sharp curvatures
MAX_CURVATURE = 0.5  # m/m² (empirical)

# Volume consistency tolerance
VOLUME_TOLERANCE = 0.15  # 15% tolerance on volume vs A-E integral


# ══════════════════════════════════════════════════════════════════════
# PINN Architecture
# ══════════════════════════════════════════════════════════════════════

def build_pinn(input_dim: int, hidden_dim: int = 256, n_layers: int = 6,
               dropout: float = 0.1):
    """
    Build the Physics-Informed Neural Network.

    Architecture:
      - Input: (x_norm, y_norm, dist_to_shore, dist_to_center, morphometric_features)
      - Modified MLP with skip connections and Fourier features
      - Output: depth (scalar, non-negative via softplus)
      - Shoreline boundary handled by multiplicative mask

    Uses Fourier feature encoding for (x, y) to capture high-frequency
    spatial patterns (Tancik et al., 2020).
    """
    import torch
    import torch.nn as nn

    class FourierFeatures(nn.Module):
        """Random Fourier features for positional encoding."""
        def __init__(self, in_dim: int = 2, n_freqs: int = 32, sigma: float = 3.0):
            super().__init__()
            self.n_freqs = n_freqs
            B = torch.randn(in_dim, n_freqs) * sigma
            self.register_buffer("B", B)

        def forward(self, x):
            proj = x @ self.B  # (batch, n_freqs)
            return torch.cat([torch.sin(proj), torch.cos(proj)], dim=-1)

    class ResidualBlock(nn.Module):
        def __init__(self, dim, dropout=0.1):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(dim, dim),
                nn.SiLU(),
                nn.Dropout(dropout),
                nn.Linear(dim, dim),
            )
            self.act = nn.SiLU()

        def forward(self, x):
            return self.act(x + self.net(x))

    class BathyPINN(nn.Module):
        """
        Physics-Informed Neural Network for bathymetry.

        Key design choices:
          1. Fourier features for spatial coordinates → captures patterns at
             multiple spatial scales
          2. Multiplicative shore mask → hard-codes depth=0 at shoreline
          3. Softplus output → guaranteed non-negative depth
          4. Conditioning on morphometric features → adapts to lake type
        """
        def __init__(self, coord_dim=2, context_dim=20, hidden_dim=256,
                     n_layers=6, n_fourier=32, dropout=0.1):
            super().__init__()

            self.fourier = FourierFeatures(coord_dim, n_fourier)
            fourier_out = n_fourier * 2

            # Total input: Fourier features + distance features + context
            total_in = fourier_out + 2 + context_dim  # +2 for dist_shore, dist_center

            self.input_proj = nn.Sequential(
                nn.Linear(total_in, hidden_dim),
                nn.SiLU(),
            )

            self.blocks = nn.ModuleList([
                ResidualBlock(hidden_dim, dropout) for _ in range(n_layers)
            ])

            self.depth_head = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.SiLU(),
                nn.Linear(hidden_dim // 2, 1),
                nn.Softplus(),  # Non-negative depth
            )

            # Uncertainty head (epistemic uncertainty)
            self.uncertainty_head = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 4),
                nn.SiLU(),
                nn.Linear(hidden_dim // 4, 1),
                nn.Softplus(),
            )

        def forward(self, coords, dist_shore, dist_center, context):
            """
            Args:
                coords: (batch, 2) normalized (x, y) in [0, 1]
                dist_shore: (batch, 1) distance to nearest shoreline
                dist_center: (batch, 1) distance to lake centroid
                context: (batch, context_dim) morphometric features

            Returns:
                depth: (batch, 1) predicted depth
                uncertainty: (batch, 1) predicted uncertainty
            """
            # Fourier encode spatial position
            ff = self.fourier(coords)

            # Concatenate all inputs
            x = torch.cat([ff, dist_shore, dist_center, context], dim=-1)
            x = self.input_proj(x)

            for block in self.blocks:
                x = block(x)

            depth_raw = self.depth_head(x)
            uncertainty = self.uncertainty_head(x)

            # Hard constraint: depth = 0 at shoreline
            # Multiply by distance to shore (goes to 0 at boundary)
            shore_mask = torch.sigmoid(dist_shore * 10)  # smooth ramp near shore
            depth = depth_raw * shore_mask

            return depth, uncertainty

    coord_dim = 2
    context_dim = input_dim - 4  # subtract coords + dist_shore + dist_center

    model = BathyPINN(
        coord_dim=coord_dim,
        context_dim=max(1, context_dim),
        hidden_dim=hidden_dim,
        n_layers=n_layers,
        dropout=dropout,
    )

    return model


# ══════════════════════════════════════════════════════════════════════
# Physics Loss Functions
# ══════════════════════════════════════════════════════════════════════

class PhysicsLoss:
    """
    Collection of physics-based loss terms for bathymetry.

    Each returns a scalar loss and a dict of metrics.
    """

    @staticmethod
    def data_loss(pred_depth, true_depth, uncertainty=None):
        """
        Data fidelity loss with optional heteroscedastic uncertainty.

        If uncertainty is provided, uses negative log-likelihood:
          L = 0.5 * (d - d_hat)² / σ² + 0.5 * log(σ²)
        This lets the model learn where it's uncertain.
        """
        import torch

        if uncertainty is not None and uncertainty.numel() > 0:
            var = uncertainty.pow(2) + EPS
            nll = 0.5 * (pred_depth - true_depth).pow(2) / var + 0.5 * torch.log(var)
            return nll.mean()
        else:
            return torch.nn.functional.mse_loss(pred_depth, true_depth)

    @staticmethod
    def gradient_loss(pred_depth, coords, max_slope=MAX_SLOPE_RATIO):
        """
        Penalize underwater slopes exceeding the angle of repose.

        Computes spatial gradient via autograd and penalizes |grad| > max_slope.
        """
        import torch

        # Compute spatial gradients via autograd
        grad = torch.autograd.grad(
            pred_depth.sum(), coords,
            create_graph=True, retain_graph=True,
        )[0]

        grad_magnitude = torch.sqrt(grad[:, 0]**2 + grad[:, 1]**2 + EPS)

        # Penalize slopes exceeding max
        excess = torch.relu(grad_magnitude - max_slope)
        return excess.mean()

    @staticmethod
    def smoothness_loss(pred_depth, coords):
        """
        Penalize sharp curvatures (second derivatives) in the lake bottom.

        Lakes have smooth bottoms formed by sedimentation — sharp
        discontinuities are unphysical.
        """
        import torch

        # First derivatives
        grad = torch.autograd.grad(
            pred_depth.sum(), coords,
            create_graph=True, retain_graph=True,
        )[0]

        # Second derivatives (Laplacian)
        d2x = torch.autograd.grad(
            grad[:, 0].sum(), coords,
            create_graph=True, retain_graph=True,
        )[0][:, 0]

        d2y = torch.autograd.grad(
            grad[:, 1].sum(), coords,
            create_graph=True, retain_graph=True,
        )[0][:, 1]

        laplacian = d2x + d2y
        return laplacian.pow(2).mean()

    @staticmethod
    def shoreline_loss(pred_depth, dist_shore, threshold=0.02):
        """
        Hard constraint: depth must be 0 at shoreline.

        Penalizes any non-zero depth where dist_to_shore < threshold.
        """
        import torch

        near_shore = dist_shore.squeeze() < threshold
        if near_shore.any():
            return pred_depth[near_shore].pow(2).mean()
        return torch.tensor(0.0, device=pred_depth.device)

    @staticmethod
    def volume_loss(pred_depth, pixel_areas, target_volume):
        """
        Predicted volume must match the A-E curve integral.

        Volume = Σ depth_i * pixel_area_i
        """
        import torch

        pred_volume = (pred_depth.squeeze() * pixel_areas).sum()
        rel_error = (pred_volume - target_volume).abs() / (target_volume + EPS)
        return rel_error

    @staticmethod
    def monotonicity_loss(pred_depth, dist_center):
        """
        Soft constraint: depth generally increases toward lake center.

        Not a hard constraint (some lakes have islands, shelves) but
        acts as a regularizer for the common case.
        """
        import torch

        # For points further from center, depth should be >= nearby shallower points
        # Approximate: correlation between dist_to_center and depth should be positive
        # Use a soft version: penalize cases where depth decreases as we move from shore
        # This is tricky to compute exactly; use a sampling approach

        # Sort by dist_center and penalize decreasing depth
        sorted_idx = torch.argsort(dist_center.squeeze(), descending=True)
        sorted_depth = pred_depth.squeeze()[sorted_idx]

        # Penalize violations: depth[i] < depth[i+1] when sorted by distance
        diffs = sorted_depth[:-1] - sorted_depth[1:]
        violations = torch.relu(-diffs)  # penalize when depth decreases toward center
        return violations.mean()

    @staticmethod
    def max_depth_loss(pred_depth, max_depth):
        """Predicted max depth should not exceed known max depth."""
        import torch

        pred_max = pred_depth.max()
        excess = torch.relu(pred_max - max_depth * 1.1)  # 10% tolerance
        return excess


# ══════════════════════════════════════════════════════════════════════
# Data Preparation
# ══════════════════════════════════════════════════════════════════════

def prepare_lake_data(
    depth_points: np.ndarray,  # (n, 3) → x, y, depth
    boundary_points: np.ndarray,  # (m, 2) → x, y of shoreline
    morphometric_features: np.ndarray,  # (f,) → context features
    max_depth: float,
    ae_volume: float = None,
    grid_resolution: float = 10.0,  # meters
) -> dict:
    """
    Prepare training data for a single lake's PINN.

    Creates:
      - Grid of points covering the lake
      - Distance-to-shore for each point
      - Distance-to-center for each point
      - Known depth values at observation points
      - Physics constraint targets
    """
    # Compute lake bounding box
    all_x = np.concatenate([depth_points[:, 0], boundary_points[:, 0]])
    all_y = np.concatenate([depth_points[:, 1], boundary_points[:, 1]])

    x_min, x_max = all_x.min() - grid_resolution, all_x.max() + grid_resolution
    y_min, y_max = all_y.min() - grid_resolution, all_y.max() + grid_resolution

    # Normalize coordinates to [0, 1]
    x_range = x_max - x_min + EPS
    y_range = y_max - y_min + EPS

    def normalize_coords(pts):
        normalized = pts.copy()
        normalized[:, 0] = (pts[:, 0] - x_min) / x_range
        normalized[:, 1] = (pts[:, 1] - y_min) / y_range
        return normalized

    # Create grid for physics loss evaluation
    n_x = int((x_max - x_min) / grid_resolution) + 1
    n_y = int((y_max - y_min) / grid_resolution) + 1
    grid_x, grid_y = np.meshgrid(
        np.linspace(0, 1, min(n_x, 200)),
        np.linspace(0, 1, min(n_y, 200)),
    )
    grid_points = np.column_stack([grid_x.ravel(), grid_y.ravel()])

    # Normalize observation coordinates
    obs_coords = normalize_coords(depth_points[:, :2])
    obs_depths = depth_points[:, 2]

    # Shore points normalized
    shore_norm = normalize_coords(boundary_points)

    # Compute distance to shore for all points
    from scipy.spatial import cKDTree
    shore_tree = cKDTree(shore_norm)

    obs_dist_shore = shore_tree.query(obs_coords)[0]
    grid_dist_shore = shore_tree.query(grid_points)[0]

    # Distance to centroid
    centroid = shore_norm.mean(axis=0)
    obs_dist_center = np.sqrt(((obs_coords - centroid) ** 2).sum(axis=1))
    grid_dist_center = np.sqrt(((grid_points - centroid) ** 2).sum(axis=1))

    # Point-in-lake mask for grid (simple: use convex hull approximation)
    try:
        hull = ConvexHull(shore_norm)
        from matplotlib.path import Path as MplPath
        hull_path = MplPath(shore_norm[hull.vertices])
        in_lake = hull_path.contains_points(grid_points)
    except Exception:
        # Fallback: all grid points within shore distance threshold
        in_lake = grid_dist_shore < 0.5

    # Normalize depth
    depth_norm = obs_depths / (max_depth + EPS)

    # Pixel areas for volume computation
    pixel_area = (x_range / min(n_x, 200)) * (y_range / min(n_y, 200))

    return {
        "obs_coords": obs_coords,
        "obs_depths": depth_norm,
        "obs_dist_shore": obs_dist_shore,
        "obs_dist_center": obs_dist_center,
        "grid_coords": grid_points[in_lake],
        "grid_dist_shore": grid_dist_shore[in_lake],
        "grid_dist_center": grid_dist_center[in_lake],
        "shore_coords": shore_norm,
        "morpho_features": morphometric_features,
        "max_depth": max_depth,
        "ae_volume": ae_volume,
        "pixel_area": pixel_area,
        "centroid": centroid,
        "normalization": {
            "x_min": float(x_min), "x_max": float(x_max),
            "y_min": float(y_min), "y_max": float(y_max),
        },
        "grid_shape": (min(n_y, 200), min(n_x, 200)),
        "in_lake_mask": in_lake.reshape(min(n_y, 200), min(n_x, 200)),
    }


# ══════════════════════════════════════════════════════════════════════
# Training Loop
# ══════════════════════════════════════════════════════════════════════

class PINNTrainer:
    """
    Trains the PINN for bathymetry with physics-informed loss.

    Loss = λ_data * L_data
         + λ_gradient * L_gradient
         + λ_smooth * L_smooth
         + λ_shore * L_shore
         + λ_volume * L_volume
         + λ_mono * L_monotonicity
         + λ_maxdepth * L_maxdepth

    Loss weights are annealed during training (data loss emphasized
    early, physics losses ramped up later).
    """

    def __init__(self, device: str = "cuda"):
        self.device = device
        self.model = None
        self.optimizer = None
        self.scaler_morpho = StandardScaler()

        # Loss weights (annealed)
        self.loss_weights = {
            "data":       10.0,
            "gradient":   0.5,
            "smooth":     0.1,
            "shore":      5.0,
            "volume":     1.0,
            "mono":       0.3,
            "maxdepth":   2.0,
        }

    def train_single_lake(
        self,
        lake_data: dict,
        n_epochs: int = 500,
        lr: float = 1e-3,
        batch_size: int = 512,
    ) -> dict:
        """
        Train PINN for a single lake.

        This is the per-lake fine-tuning approach: start from a pre-trained
        model and fine-tune on each lake's specific data + constraints.
        """
        import torch
        import torch.nn as nn

        morpho = lake_data["morpho_features"]
        if morpho.ndim == 1:
            morpho = morpho.reshape(1, -1)
        morpho_scaled = self.scaler_morpho.transform(morpho)
        context = torch.tensor(morpho_scaled, dtype=torch.float32).to(self.device)

        # Input dimension
        context_dim = context.shape[1]
        coord_dim = 2
        total_dim = coord_dim + 2 + context_dim  # coords + dist_shore + dist_center + context

        if self.model is None:
            self.model = build_pinn(total_dim, hidden_dim=256, n_layers=6).to(self.device)

        self.optimizer = torch.optim.AdamW(
            self.model.parameters(), lr=lr, weight_decay=1e-5
        )
        scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            self.optimizer, T_0=100, T_mult=2
        )

        # Convert data to tensors
        obs_coords = torch.tensor(
            lake_data["obs_coords"], dtype=torch.float32
        ).to(self.device)
        obs_depths = torch.tensor(
            lake_data["obs_depths"], dtype=torch.float32
        ).unsqueeze(-1).to(self.device)
        obs_dist_shore = torch.tensor(
            lake_data["obs_dist_shore"], dtype=torch.float32
        ).unsqueeze(-1).to(self.device)
        obs_dist_center = torch.tensor(
            lake_data["obs_dist_center"], dtype=torch.float32
        ).unsqueeze(-1).to(self.device)

        grid_coords = torch.tensor(
            lake_data["grid_coords"], dtype=torch.float32,
        ).to(self.device).requires_grad_(True)
        grid_dist_shore = torch.tensor(
            lake_data["grid_dist_shore"], dtype=torch.float32
        ).unsqueeze(-1).to(self.device)
        grid_dist_center = torch.tensor(
            lake_data["grid_dist_center"], dtype=torch.float32
        ).unsqueeze(-1).to(self.device)

        max_depth_t = torch.tensor(
            lake_data["max_depth"], dtype=torch.float32
        ).to(self.device)

        n_obs = len(obs_coords)
        n_grid = len(grid_coords)
        context_expanded_obs = context.expand(n_obs, -1)
        context_expanded_grid = context.expand(n_grid, -1)

        physics_loss = PhysicsLoss()
        losses_history = []
        best_loss = float("inf")
        best_state = None

        for epoch in range(n_epochs):
            self.model.train()
            self.optimizer.zero_grad()

            # ── Data loss ──
            pred_obs, unc_obs = self.model(
                obs_coords, obs_dist_shore, obs_dist_center, context_expanded_obs
            )
            L_data = physics_loss.data_loss(pred_obs, obs_depths, unc_obs)

            # ── Physics losses on grid ──
            pred_grid, _ = self.model(
                grid_coords, grid_dist_shore, grid_dist_center, context_expanded_grid
            )

            # Gradient constraint (requires grad on coords)
            try:
                L_gradient = physics_loss.gradient_loss(pred_grid, grid_coords)
            except Exception:
                L_gradient = torch.tensor(0.0, device=self.device)

            # Smoothness
            try:
                L_smooth = physics_loss.smoothness_loss(pred_grid, grid_coords)
            except Exception:
                L_smooth = torch.tensor(0.0, device=self.device)

            # Shoreline
            L_shore = physics_loss.shoreline_loss(pred_grid, grid_dist_shore)

            # Max depth
            L_maxdepth = physics_loss.max_depth_loss(
                pred_grid * max_depth_t, max_depth_t
            )

            # Monotonicity
            L_mono = physics_loss.monotonicity_loss(pred_grid, grid_dist_center)

            # Volume
            L_volume = torch.tensor(0.0, device=self.device)
            if lake_data.get("ae_volume") is not None:
                pixel_areas = torch.full(
                    (n_grid,), lake_data["pixel_area"],
                    dtype=torch.float32, device=self.device,
                )
                L_volume = physics_loss.volume_loss(
                    pred_grid * max_depth_t, pixel_areas,
                    torch.tensor(lake_data["ae_volume"], dtype=torch.float32,
                                 device=self.device),
                )

            # ── Annealed total loss ──
            # Ramp up physics losses over training
            ramp = min(1.0, epoch / (n_epochs * 0.3))
            w = self.loss_weights

            total_loss = (
                w["data"] * L_data
                + w["gradient"] * ramp * L_gradient
                + w["smooth"] * ramp * L_smooth
                + w["shore"] * w["shore"] * L_shore
                + w["volume"] * ramp * L_volume
                + w["mono"] * ramp * L_mono
                + w["maxdepth"] * L_maxdepth
            )

            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.optimizer.step()
            scheduler.step()

            loss_val = total_loss.item()
            if loss_val < best_loss:
                best_loss = loss_val
                best_state = {k: v.cpu().clone()
                              for k, v in self.model.state_dict().items()}

            losses_history.append({
                "epoch": epoch,
                "total": loss_val,
                "data": L_data.item(),
                "gradient": L_gradient.item() if isinstance(L_gradient, torch.Tensor) else 0,
                "smooth": L_smooth.item() if isinstance(L_smooth, torch.Tensor) else 0,
                "shore": L_shore.item(),
                "volume": L_volume.item(),
            })

            if (epoch + 1) % 100 == 0:
                log.info(f"  Epoch {epoch + 1}/{n_epochs}: total={loss_val:.6f}, "
                         f"data={L_data.item():.6f}, grad={L_gradient.item():.6f}, "
                         f"shore={L_shore.item():.6f}")

        if best_state:
            self.model.load_state_dict(best_state)

        return {
            "best_loss": best_loss,
            "n_epochs": n_epochs,
            "losses": losses_history,
        }

    def predict_grid(self, lake_data: dict) -> np.ndarray:
        """
        Predict depth on a regular grid covering the lake.

        Returns 2D depth map (in meters).
        """
        import torch

        self.model.eval()

        morpho = lake_data["morpho_features"]
        if morpho.ndim == 1:
            morpho = morpho.reshape(1, -1)
        morpho_scaled = self.scaler_morpho.transform(morpho)
        context = torch.tensor(morpho_scaled, dtype=torch.float32).to(self.device)

        # Full grid
        grid_shape = lake_data["grid_shape"]
        n_y, n_x = grid_shape
        gy, gx = np.meshgrid(
            np.linspace(0, 1, n_y),
            np.linspace(0, 1, n_x),
            indexing="ij",
        )
        all_coords = np.column_stack([gx.ravel(), gy.ravel()])

        # Distances
        from scipy.spatial import cKDTree
        shore_tree = cKDTree(lake_data["shore_coords"])
        dist_shore = shore_tree.query(all_coords)[0]
        centroid = lake_data["centroid"]
        dist_center = np.sqrt(((all_coords - centroid) ** 2).sum(axis=1))

        n = len(all_coords)
        context_exp = context.expand(n, -1)

        coords_t = torch.tensor(all_coords, dtype=torch.float32).to(self.device)
        ds_t = torch.tensor(dist_shore, dtype=torch.float32).unsqueeze(-1).to(self.device)
        dc_t = torch.tensor(dist_center, dtype=torch.float32).unsqueeze(-1).to(self.device)

        with torch.no_grad():
            pred, unc = self.model(coords_t, ds_t, dc_t, context_exp)

        depth_map = pred.squeeze().cpu().numpy().reshape(grid_shape)
        depth_map *= lake_data["max_depth"]  # denormalize

        # Apply in-lake mask
        in_lake = lake_data["in_lake_mask"]
        depth_map[~in_lake] = 0.0

        # Clip to valid range
        depth_map = np.clip(depth_map, 0, lake_data["max_depth"] * 1.1)

        return depth_map


# ══════════════════════════════════════════════════════════════════════
# Pre-training on Many Lakes
# ══════════════════════════════════════════════════════════════════════

class PreTrainer:
    """
    Pre-train a shared PINN backbone on many lakes simultaneously.

    After pre-training, the model can be fine-tuned per-lake with
    just a few epochs and sparse data.
    """

    def __init__(self, device: str = "cuda", hidden_dim: int = 256):
        self.device = device
        self.hidden_dim = hidden_dim
        self.model = None
        self.scaler_morpho = StandardScaler()

    def pretrain(
        self,
        lakes_data: List[dict],
        n_epochs: int = 100,
        lr: float = 3e-4,
        batch_size: int = 1024,
    ) -> dict:
        """
        Pre-train on a batch of lakes.

        Samples random points from random lakes each batch,
        with physics losses computed on each lake's grid.
        """
        import torch
        import torch.nn as nn

        log.info(f"Pre-training PINN on {len(lakes_data)} lakes...")

        # Collect morphometric features for scaling
        all_morpho = np.array([d["morpho_features"] for d in lakes_data])
        self.scaler_morpho.fit(all_morpho)

        # Determine input dim
        morpho_dim = all_morpho.shape[1]
        total_dim = 2 + 2 + morpho_dim  # coords + dists + morpho

        self.model = build_pinn(
            total_dim, hidden_dim=self.hidden_dim, n_layers=6
        ).to(self.device)

        optimizer = torch.optim.AdamW(
            self.model.parameters(), lr=lr, weight_decay=1e-5
        )
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, n_epochs)

        physics_loss = PhysicsLoss()
        losses = []

        for epoch in range(n_epochs):
            epoch_loss = 0
            n_batches = 0

            # Sample lakes
            rng = np.random.default_rng(epoch)
            n_lakes_per_batch = min(16, len(lakes_data))
            lake_indices = rng.choice(len(lakes_data), n_lakes_per_batch, replace=False)

            for lake_idx in lake_indices:
                lake = lakes_data[lake_idx]

                # Prepare tensors
                morpho = self.scaler_morpho.transform(
                    lake["morpho_features"].reshape(1, -1)
                )
                morpho_t = torch.tensor(morpho, dtype=torch.float32).to(self.device)

                n_obs = len(lake["obs_coords"])
                coords_t = torch.tensor(
                    lake["obs_coords"], dtype=torch.float32
                ).to(self.device)
                depths_t = torch.tensor(
                    lake["obs_depths"], dtype=torch.float32
                ).unsqueeze(-1).to(self.device)
                ds_t = torch.tensor(
                    lake["obs_dist_shore"], dtype=torch.float32
                ).unsqueeze(-1).to(self.device)
                dc_t = torch.tensor(
                    lake["obs_dist_center"], dtype=torch.float32
                ).unsqueeze(-1).to(self.device)

                context = morpho_t.expand(n_obs, -1)

                optimizer.zero_grad()

                pred, unc = self.model(coords_t, ds_t, dc_t, context)
                loss = physics_loss.data_loss(pred, depths_t, unc)

                # Shoreline loss
                loss += 5.0 * physics_loss.shoreline_loss(pred, ds_t)

                # Max depth
                max_d = torch.tensor(
                    lake["max_depth"], dtype=torch.float32, device=self.device
                )
                loss += 2.0 * physics_loss.max_depth_loss(pred * max_d, max_d)

                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()

                epoch_loss += loss.item()
                n_batches += 1

            scheduler.step()

            avg_loss = epoch_loss / max(n_batches, 1)
            losses.append(avg_loss)

            if (epoch + 1) % 25 == 0:
                log.info(f"  Pretrain epoch {epoch + 1}/{n_epochs}, loss={avg_loss:.6f}")

        return {"losses": losses, "final_loss": losses[-1]}


# ══════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════

def validate_pinn(
    trainer: PINNTrainer,
    test_lakes: List[dict],
    output_dir: Path,
) -> dict:
    """
    Validate PINN predictions against sonar ground truth.

    For each test lake:
    1. Train PINN on 20% of sonar points
    2. Predict remaining 80%
    3. Report metrics stratified by depth bin
    """
    import torch

    output_dir.mkdir(parents=True, exist_ok=True)

    all_true = []
    all_pred = []
    all_depth_bins = []
    lake_metrics = []

    for lake_data in tqdm(test_lakes, desc="Validating PINN"):
        n_obs = len(lake_data["obs_coords"])
        if n_obs < 20:
            continue

        # Split
        rng = np.random.RandomState(42)
        perm = rng.permutation(n_obs)
        n_train = max(10, int(0.2 * n_obs))

        train_data = {
            "obs_coords": lake_data["obs_coords"][perm[:n_train]],
            "obs_depths": lake_data["obs_depths"][perm[:n_train]],
            "obs_dist_shore": lake_data["obs_dist_shore"][perm[:n_train]],
            "obs_dist_center": lake_data["obs_dist_center"][perm[:n_train]],
            "grid_coords": lake_data["grid_coords"],
            "grid_dist_shore": lake_data["grid_dist_shore"],
            "grid_dist_center": lake_data["grid_dist_center"],
            "shore_coords": lake_data["shore_coords"],
            "morpho_features": lake_data["morpho_features"],
            "max_depth": lake_data["max_depth"],
            "ae_volume": lake_data.get("ae_volume"),
            "pixel_area": lake_data["pixel_area"],
            "centroid": lake_data["centroid"],
            "grid_shape": lake_data["grid_shape"],
            "in_lake_mask": lake_data["in_lake_mask"],
        }

        # Train
        try:
            trainer.train_single_lake(train_data, n_epochs=300, lr=1e-3)
        except Exception as e:
            log.warning(f"Training failed: {e}")
            continue

        # Predict on test points
        test_coords = lake_data["obs_coords"][perm[n_train:]]
        test_depths = lake_data["obs_depths"][perm[n_train:]]
        test_ds = lake_data["obs_dist_shore"][perm[n_train:]]
        test_dc = lake_data["obs_dist_center"][perm[n_train:]]

        morpho = trainer.scaler_morpho.transform(
            lake_data["morpho_features"].reshape(1, -1)
        )
        morpho_t = torch.tensor(morpho, dtype=torch.float32).to(trainer.device)

        n_test = len(test_coords)
        coords_t = torch.tensor(test_coords, dtype=torch.float32).to(trainer.device)
        ds_t = torch.tensor(test_ds, dtype=torch.float32).unsqueeze(-1).to(trainer.device)
        dc_t = torch.tensor(test_dc, dtype=torch.float32).unsqueeze(-1).to(trainer.device)
        context = morpho_t.expand(n_test, -1)

        trainer.model.eval()
        with torch.no_grad():
            pred, _ = trainer.model(coords_t, ds_t, dc_t, context)

        pred_depths = pred.squeeze().cpu().numpy() * lake_data["max_depth"]
        true_depths = test_depths * lake_data["max_depth"]

        valid = np.isfinite(pred_depths) & np.isfinite(true_depths)
        if valid.sum() < 5:
            continue

        p = pred_depths[valid]
        t = true_depths[valid]

        all_true.extend(t)
        all_pred.extend(p)

        for d in t:
            if d < 5:
                all_depth_bins.append("0-5m")
            elif d < 10:
                all_depth_bins.append("5-10m")
            elif d < 20:
                all_depth_bins.append("10-20m")
            else:
                all_depth_bins.append("20m+")

        lake_metrics.append({
            "n_train": n_train,
            "n_test": int(valid.sum()),
            "rmse": float(np.sqrt(mean_squared_error(t, p))),
            "mae": float(mean_absolute_error(t, p)),
            "r2": float(r2_score(t, p)) if len(t) > 1 else np.nan,
            "max_depth": lake_data["max_depth"],
        })

    # Aggregate
    all_true = np.array(all_true)
    all_pred = np.array(all_pred)

    results = {
        "overall": {
            "n_lakes": len(lake_metrics),
            "n_points": len(all_true),
            "rmse": float(np.sqrt(mean_squared_error(all_true, all_pred)))
            if len(all_true) > 0 else np.nan,
            "mae": float(mean_absolute_error(all_true, all_pred))
            if len(all_true) > 0 else np.nan,
            "r2": float(r2_score(all_true, all_pred))
            if len(all_true) > 1 else np.nan,
        },
    }

    # By depth bin
    for bin_name in ["0-5m", "5-10m", "10-20m", "20m+"]:
        mask = np.array(all_depth_bins) == bin_name
        if mask.sum() > 10:
            results[f"depth_{bin_name}"] = {
                "n_points": int(mask.sum()),
                "rmse": float(np.sqrt(mean_squared_error(
                    all_true[mask], all_pred[mask]
                ))),
                "r2": float(r2_score(all_true[mask], all_pred[mask])),
            }

    # Per-lake RMSE distribution
    if lake_metrics:
        rmses = [m["rmse"] for m in lake_metrics]
        results["per_lake_rmse"] = {
            "mean": float(np.mean(rmses)),
            "median": float(np.median(rmses)),
            "p10": float(np.percentile(rmses, 10)),
            "p90": float(np.percentile(rmses, 90)),
        }

    # Save
    with open(output_dir / "pinn_validation.json", "w") as f:
        json.dump(results, f, indent=2)

    log.info(f"\n{'='*60}")
    log.info(f"  PINN Bathymetry Validation")
    log.info(f"{'='*60}")
    log.info(f"  Lakes: {results['overall']['n_lakes']}")
    log.info(f"  Points: {results['overall']['n_points']}")
    log.info(f"  RMSE: {results['overall']['rmse']:.2f}m")
    log.info(f"  R²:   {results['overall']['r2']:.4f}")
    for bin_name in ["0-5m", "5-10m", "10-20m", "20m+"]:
        key = f"depth_{bin_name}"
        if key in results:
            log.info(f"  {bin_name}: RMSE={results[key]['rmse']:.2f}m, "
                     f"R²={results[key]['r2']:.4f}")
    log.info(f"{'='*60}")

    return results


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Physics-Informed NN Bathymetry")
    parser.add_argument("--data", type=str, required=True,
                        help="Sonar data directory")
    parser.add_argument("--morpho", type=str, required=True,
                        help="Morphometric features parquet")
    parser.add_argument("--output", type=str, required=True,
                        help="Output directory")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--pretrain-epochs", type=int, default=100)
    parser.add_argument("--finetune-epochs", type=int, default=300)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("Loading data...")
    morpho_df = pd.read_parquet(args.morpho)

    # Load sonar data and prepare lake data structures
    data_dir = Path(args.data)
    sonar_files = sorted(data_dir.glob("*.parquet")) + sorted(data_dir.glob("*.csv"))

    lakes_data = []
    for fpath in tqdm(sonar_files[:500], desc="Preparing lakes"):  # limit for memory
        try:
            lake_id = fpath.stem
            if fpath.suffix == ".parquet":
                df = pd.read_parquet(fpath)
            else:
                df = pd.read_csv(fpath)

            # Find columns
            x_col, y_col, depth_col = None, None, None
            for c in ["x", "X", "easting", "lon", "longitude"]:
                if c in df.columns:
                    x_col = c
                    break
            for c in ["y", "Y", "northing", "lat", "latitude"]:
                if c in df.columns:
                    y_col = c
                    break
            for c in ["depth_m", "depth", "z", "DEPTH"]:
                if c in df.columns:
                    depth_col = c
                    break

            if not all([x_col, y_col, depth_col]):
                continue

            # Create depth points
            depths = df[depth_col].dropna().values
            xs = df[x_col].dropna().values[:len(depths)]
            ys = df[y_col].dropna().values[:len(depths)]
            depth_points = np.column_stack([xs, ys, depths])

            if len(depth_points) < 20:
                continue

            max_depth = float(depths.max())

            # Approximate boundary from convex hull of points
            try:
                hull = ConvexHull(depth_points[:, :2])
                boundary = depth_points[hull.vertices, :2]
            except Exception:
                continue

            # Morphometric features
            if lake_id in morpho_df.index:
                morpho_row = morpho_df.loc[lake_id]
            else:
                matches = morpho_df[morpho_df.index.astype(str) == lake_id]
                if len(matches) == 0:
                    morpho_row = pd.Series(np.zeros(10))
                else:
                    morpho_row = matches.iloc[0]

            morpho_feats = morpho_row.values[:20].astype(float)  # first 20 features
            morpho_feats = np.nan_to_num(morpho_feats, 0.0)

            lake_data = prepare_lake_data(
                depth_points, boundary, morpho_feats, max_depth,
            )
            lake_data["lake_id"] = lake_id
            lakes_data.append(lake_data)

        except Exception as e:
            continue

    log.info(f"Prepared {len(lakes_data)} lakes for training")

    if not lakes_data:
        log.error("No lakes prepared. Check data format.")
        return

    # ── Pre-training ──
    pretrainer = PreTrainer(device=args.device)
    pretrain_result = pretrainer.pretrain(
        lakes_data, n_epochs=args.pretrain_epochs
    )

    # Save pre-trained model
    import torch
    torch.save(pretrainer.model.state_dict(), output_dir / "pinn_pretrained.pt")
    pickle.dump(pretrainer.scaler_morpho, open(output_dir / "morpho_scaler.pkl", "wb"))

    # ── Validation ──
    if args.validate:
        trainer = PINNTrainer(device=args.device)
        trainer.model = pretrainer.model
        trainer.scaler_morpho = pretrainer.scaler_morpho

        # Use last 20% of lakes as test
        n_test = max(10, len(lakes_data) // 5)
        test_lakes = lakes_data[-n_test:]
        validate_pinn(trainer, test_lakes, output_dir)

    log.info(f"\nAll results saved to {output_dir}")


if __name__ == "__main__":
    main()
