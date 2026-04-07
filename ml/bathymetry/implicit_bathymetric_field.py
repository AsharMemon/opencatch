#!/usr/bin/env python3
"""
OpenCatch — Implicit Bathymetric Field: Coordinate MLP with Physics Priors

A novel per-lake bathymetry approach where the neural network IS the
bathymetric map.  Instead of predicting depth from spectral/morphometric
features, we fit a continuous implicit surface  f(x, y) -> depth  for each
lake individually.  At inference the network is evaluated on a dense grid
to produce an arbitrarily-high-resolution depth raster — no spectral
features are needed.

Why this works:
  Traditional satellite-derived bathymetry (SDB) needs clear water and
  calibration points.  For turbid or data-sparse lakes we often have only
  heterogeneous, partial observations:
    * Shore boundary (depth=0) — hundreds of points from the lake polygon
    * ICESat-2 / sonar anchors — sparse but accurate point depths
    * Spectral depth proxy — noisy per-pixel predictions from a global
      XGBoost model
    * Area-Elevation (A-E) curve volume — a scalar integral constraint
    * Morphometric priors (max depth, shape)

  A coordinate MLP with SIREN or Fourier-feature encoding can interpolate
  these signals into a smooth, physically plausible surface that satisfies
  all constraints simultaneously.

Architecture options:
  --arch siren    SIREN (Sitzmann et al. 2020) — sinusoidal activations
                  for smooth, bandlimited surfaces
  --arch fourier  Fourier Feature MLP (Tancik et al. 2020) — random
                  Fourier features + ReLU/SiLU residual blocks

Both share the same physics-informed loss from physics_informed_bathy.py.

References:
  - Sitzmann et al. (2020): Implicit Neural Representations with Periodic
    Activation Functions (SIREN)
  - Tancik et al. (2020): Fourier Features Let Networks Learn High
    Frequency Functions in Low Dimensional Domains
  - Raissi et al. (2019): Physics-Informed Neural Networks

Usage:
    python implicit_bathymetric_field.py \\
        --lake-id 10001300 \\
        --shore-points /data/shores/10001300.geojson \\
        --anchors /data/icesat2/10001300_depths.parquet \\
        --spectral-proxy /data/predictions/10001300_spectral.parquet \\
        --ae-volume 1.2e6 \\
        --max-depth 15.0 \\
        --arch siren \\
        --output /data/implicit/10001300 \\
        --device cuda

Requirements:
    pip install torch numpy pandas geopandas shapely scipy tqdm
"""

import argparse
import json
import logging
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("implicit_bathy")

EPS = 1e-8


# ══════════════════════════════════════════════════════════════════════
# SIREN Architecture (Sitzmann et al. 2020)
# ══════════════════════════════════════════════════════════════════════


def _siren_init_(weight, is_first: bool, omega_0: float):
    """SIREN weight initialization.

    First layer:  U(-1/n, 1/n)
    Hidden layers: U(-sqrt(6/n)/omega_0, sqrt(6/n)/omega_0)
    """
    fan_in = weight.shape[1]
    import torch

    with torch.no_grad():
        if is_first:
            bound = 1.0 / fan_in
        else:
            bound = math.sqrt(6.0 / fan_in) / omega_0
        weight.uniform_(-bound, bound)


def build_siren(coord_dim: int = 2, hidden_dim: int = 256, n_layers: int = 5,
                omega_0: float = 30.0, dropout: float = 0.05):
    """Build a SIREN implicit field: (x, y) -> depth."""
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class SirenLayer(nn.Module):
        """Single SIREN layer: sin(omega_0 * (Wx + b))."""

        def __init__(self, in_features: int, out_features: int,
                     omega_0: float = 30.0, is_first: bool = False):
            super().__init__()
            self.omega_0 = omega_0
            self.is_first = is_first
            self.weight = nn.Parameter(torch.empty(out_features, in_features))
            self.bias = nn.Parameter(torch.empty(out_features))
            _siren_init_(self.weight, is_first, omega_0)
            nn.init.zeros_(self.bias)

        def forward(self, x):
            return torch.sin(self.omega_0 * F.linear(x, self.weight, self.bias))

    class ImplicitBathymetricFieldSIREN(nn.Module):
        """
        SIREN-based implicit bathymetric surface.

        Maps normalised lake coordinates (x, y) in [-1, 1]^2 to a
        non-negative depth value via a stack of sinusoidal layers
        with a softplus output head.
        """

        def __init__(self, coord_dim: int = 2, hidden_dim: int = 256,
                     n_layers: int = 5, omega_0: float = 30.0,
                     dropout: float = 0.05):
            super().__init__()
            self.omega_0 = omega_0
            layers = []

            # First SIREN layer
            layers.append(SirenLayer(coord_dim, hidden_dim,
                                     omega_0=omega_0, is_first=True))
            if dropout > 0:
                layers.append(nn.Dropout(dropout))

            # Hidden SIREN layers
            for _ in range(n_layers - 1):
                layers.append(SirenLayer(hidden_dim, hidden_dim,
                                         omega_0=omega_0, is_first=False))
                if dropout > 0:
                    layers.append(nn.Dropout(dropout))

            self.net = nn.Sequential(*layers)

            # Depth head — linear + softplus for non-negativity
            self.depth_head = nn.Sequential(
                nn.Linear(hidden_dim, 1),
                nn.Softplus(beta=1.0),
            )
            # Init depth head conservatively
            nn.init.xavier_uniform_(self.depth_head[0].weight)
            nn.init.zeros_(self.depth_head[0].bias)

        def forward(self, coords):
            """
            Args:
                coords: (batch, 2) normalised coordinates in [-1, 1]^2
            Returns:
                depth: (batch, 1) non-negative predicted depth
            """
            h = self.net(coords)
            return self.depth_head(h)

    return ImplicitBathymetricFieldSIREN(
        coord_dim=coord_dim, hidden_dim=hidden_dim,
        n_layers=n_layers, omega_0=omega_0, dropout=dropout,
    )


# ══════════════════════════════════════════════════════════════════════
# Fourier Feature MLP Architecture
# ══════════════════════════════════════════════════════════════════════


def build_fourier_mlp(coord_dim: int = 2, hidden_dim: int = 256,
                      n_layers: int = 5, n_fourier: int = 64,
                      sigma: float = 5.0, dropout: float = 0.05):
    """Build a Fourier-feature MLP implicit field: (x, y) -> depth.

    Re-uses the FourierFeatures / ResidualBlock design from
    physics_informed_bathy.py but in a standalone single-lake variant
    that takes only coordinates as input.
    """
    import torch
    import torch.nn as nn

    class FourierFeatures(nn.Module):
        """Random Fourier features for positional encoding."""
        def __init__(self, in_dim: int = 2, n_freqs: int = 64,
                     sigma: float = 5.0):
            super().__init__()
            B = torch.randn(in_dim, n_freqs) * sigma
            self.register_buffer("B", B)

        def forward(self, x):
            proj = x @ self.B
            return torch.cat([torch.sin(proj), torch.cos(proj)], dim=-1)

    class ResidualBlock(nn.Module):
        def __init__(self, dim: int, dropout: float = 0.05):
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

    class ImplicitBathymetricFieldFourier(nn.Module):
        """Fourier-feature MLP implicit bathymetric surface."""

        def __init__(self, coord_dim, hidden_dim, n_layers,
                     n_fourier, sigma, dropout):
            super().__init__()
            self.fourier = FourierFeatures(coord_dim, n_fourier, sigma)
            fourier_out = n_fourier * 2

            self.input_proj = nn.Sequential(
                nn.Linear(fourier_out, hidden_dim),
                nn.SiLU(),
            )

            self.blocks = nn.ModuleList([
                ResidualBlock(hidden_dim, dropout) for _ in range(n_layers)
            ])

            self.depth_head = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.SiLU(),
                nn.Linear(hidden_dim // 2, 1),
                nn.Softplus(beta=1.0),
            )

        def forward(self, coords):
            ff = self.fourier(coords)
            h = self.input_proj(ff)
            for block in self.blocks:
                h = block(h)
            return self.depth_head(h)

    return ImplicitBathymetricFieldFourier(
        coord_dim=coord_dim, hidden_dim=hidden_dim,
        n_layers=n_layers, n_fourier=n_fourier,
        sigma=sigma, dropout=dropout,
    )


# ══════════════════════════════════════════════════════════════════════
# Coordinate Normalisation Helpers
# ══════════════════════════════════════════════════════════════════════


class CoordinateNormalizer:
    """Map raw lake coordinates to [-1, 1]^2 and back."""

    def __init__(self, all_xy: np.ndarray, pad_frac: float = 0.05):
        x_min, y_min = all_xy.min(axis=0)
        x_max, y_max = all_xy.max(axis=0)
        pad_x = (x_max - x_min) * pad_frac
        pad_y = (y_max - y_min) * pad_frac
        self.x_min = x_min - pad_x
        self.x_max = x_max + pad_x
        self.y_min = y_min - pad_y
        self.y_max = y_max + pad_y
        self.x_range = self.x_max - self.x_min + EPS
        self.y_range = self.y_max - self.y_min + EPS

    def normalize(self, xy: np.ndarray) -> np.ndarray:
        """(N, 2) raw -> (N, 2) in [-1, 1]."""
        out = np.empty_like(xy, dtype=np.float64)
        out[:, 0] = 2.0 * (xy[:, 0] - self.x_min) / self.x_range - 1.0
        out[:, 1] = 2.0 * (xy[:, 1] - self.y_min) / self.y_range - 1.0
        return out

    def denormalize(self, xy_norm: np.ndarray) -> np.ndarray:
        """(N, 2) in [-1, 1] -> raw."""
        out = np.empty_like(xy_norm)
        out[:, 0] = (xy_norm[:, 0] + 1.0) / 2.0 * self.x_range + self.x_min
        out[:, 1] = (xy_norm[:, 1] + 1.0) / 2.0 * self.y_range + self.y_min
        return out

    def pixel_area_m2(self, n_x: int, n_y: int) -> float:
        """Area of a single grid cell in original coordinate units."""
        dx = self.x_range / n_x
        dy = self.y_range / n_y
        return float(dx * dy)

    def to_dict(self) -> dict:
        return {
            "x_min": float(self.x_min), "x_max": float(self.x_max),
            "y_min": float(self.y_min), "y_max": float(self.y_max),
        }


# ══════════════════════════════════════════════════════════════════════
# Physics-Informed Loss for the Implicit Field
# ══════════════════════════════════════════════════════════════════════


class ImplicitFieldLoss:
    """
    Composite loss for fitting an implicit bathymetric surface.

    Re-uses the gradient / smoothness / monotonicity / volume / max-depth
    ideas from :class:`physics_informed_bathy.PhysicsLoss` but adapted
    for the coordinate-only implicit field setting.

    Loss weights (lambdas):
        shore      = 10.0   shore boundary depth=0
        anchor     =  5.0   sonar / ICESat-2 point depths
        spectral   =  1.0   noisy XGBoost proxy (Huber)
        smooth     =  0.5   Laplacian regularisation
        mono       =  0.3   depth increases toward centre
        volume     =  2.0   integrated volume matches A-E
        maxdepth   =  1.0   no prediction exceeds max depth
    """

    DEFAULT_WEIGHTS: Dict[str, float] = {
        "shore": 10.0,
        "anchor": 5.0,
        "spectral": 1.0,
        "smooth": 0.5,
        "mono": 0.3,
        "volume": 2.0,
        "maxdepth": 1.0,
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.w = dict(self.DEFAULT_WEIGHTS)
        if weights:
            self.w.update(weights)

    # ── individual terms ──────────────────────────────────────────

    @staticmethod
    def shore_loss(pred_depth_at_shore):
        """MSE: depth should be 0 at shore points."""
        return pred_depth_at_shore.pow(2).mean()

    @staticmethod
    def anchor_loss(pred_depth_at_anchors, true_depth_at_anchors):
        """MSE against known sonar / ICESat-2 depths."""
        return (pred_depth_at_anchors - true_depth_at_anchors).pow(2).mean()

    @staticmethod
    def spectral_loss(pred_depth_at_proxy, proxy_depth):
        """Huber loss against noisy spectral proxy — robust to outliers."""
        import torch
        return torch.nn.functional.smooth_l1_loss(
            pred_depth_at_proxy, proxy_depth, beta=1.0,
        )

    @staticmethod
    def smoothness_loss(pred_depth, coords):
        """Laplacian (second-derivative) regularisation.

        Penalises sharp curvatures so the lake bottom stays smooth.
        Uses autograd to compute d²z/dx² + d²z/dy².
        """
        import torch

        grad = torch.autograd.grad(
            pred_depth.sum(), coords,
            create_graph=True, retain_graph=True,
        )[0]

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
    def monotonicity_loss(pred_depth, dist_to_center):
        """Soft constraint: depth generally increases toward lake centre.

        Sorts points by distance-to-centroid (descending — centre first)
        and penalises cases where a point closer to centre is shallower
        than its neighbour further out.
        """
        import torch

        sorted_idx = torch.argsort(dist_to_center.squeeze(), descending=True)
        sorted_d = pred_depth.squeeze()[sorted_idx]
        diffs = sorted_d[:-1] - sorted_d[1:]
        return torch.relu(-diffs).mean()

    @staticmethod
    def volume_loss(pred_depth_grid, pixel_area: float, target_volume: float):
        """Integrated volume must match A-E curve integral."""
        import torch

        pred_vol = pred_depth_grid.squeeze().sum() * pixel_area
        target_t = torch.tensor(target_volume, dtype=pred_vol.dtype,
                                device=pred_vol.device)
        rel_err = (pred_vol - target_t).abs() / (target_t + EPS)
        return rel_err

    @staticmethod
    def maxdepth_loss(pred_depth, max_depth: float):
        """No prediction should exceed the known max depth (with 10% tol)."""
        import torch

        ceiling = max_depth * 1.1
        excess = torch.relu(pred_depth - ceiling)
        return excess.mean()

    # ── composite ─────────────────────────────────────────────────

    def __call__(self, components: Dict[str, "torch.Tensor"]) -> (
            Tuple["torch.Tensor", Dict[str, float]]):
        """Weighted sum of all available loss components.

        Args:
            components: dict of {loss_name: tensor} — only present keys
                        are included.
        Returns:
            (total_loss, metrics_dict)
        """
        import torch

        total = torch.tensor(0.0, device=next(iter(components.values())).device)
        metrics: Dict[str, float] = {}
        for name, val in components.items():
            w = self.w.get(name, 1.0)
            total = total + w * val
            metrics[name] = float(val.item())
        metrics["total"] = float(total.item())
        return total, metrics


# ══════════════════════════════════════════════════════════════════════
# Implicit Field Trainer
# ══════════════════════════════════════════════════════════════════════


class ImplicitFieldTrainer:
    """
    Fit an implicit bathymetric surface for a single lake.

    Accepts heterogeneous supervision signals and physics priors.
    """

    def __init__(
        self,
        shore_xy: np.ndarray,
        anchor_xy: Optional[np.ndarray] = None,
        anchor_depth: Optional[np.ndarray] = None,
        spectral_xy: Optional[np.ndarray] = None,
        spectral_depth: Optional[np.ndarray] = None,
        ae_volume: Optional[float] = None,
        max_depth: float = 30.0,
        arch: str = "siren",
        hidden_dim: int = 256,
        n_layers: int = 5,
        omega_0: float = 30.0,
        device: str = "cuda",
        loss_weights: Optional[Dict[str, float]] = None,
    ):
        import torch

        self.device = device
        self.max_depth = max_depth
        self.ae_volume = ae_volume

        # ── Coordinate normalisation ──
        all_xy_parts = [shore_xy]
        if anchor_xy is not None and len(anchor_xy):
            all_xy_parts.append(anchor_xy)
        if spectral_xy is not None and len(spectral_xy):
            all_xy_parts.append(spectral_xy)
        all_xy = np.concatenate(all_xy_parts, axis=0)
        self.normalizer = CoordinateNormalizer(all_xy)

        # ── Build model ──
        if arch == "siren":
            self.model = build_siren(
                coord_dim=2, hidden_dim=hidden_dim,
                n_layers=n_layers, omega_0=omega_0,
            ).to(device)
        elif arch == "fourier":
            self.model = build_fourier_mlp(
                coord_dim=2, hidden_dim=hidden_dim,
                n_layers=n_layers,
            ).to(device)
        else:
            raise ValueError(f"Unknown arch '{arch}', expected siren|fourier")

        self.arch = arch

        # ── Prepare tensors ──
        # Shore points (depth = 0)
        shore_norm = self.normalizer.normalize(shore_xy)
        self.shore_t = torch.tensor(shore_norm, dtype=torch.float32,
                                    device=device)

        # Anchor points
        self.has_anchors = anchor_xy is not None and len(anchor_xy) > 0
        if self.has_anchors:
            anc_norm = self.normalizer.normalize(anchor_xy)
            self.anchor_coords_t = torch.tensor(
                anc_norm, dtype=torch.float32, device=device)
            self.anchor_depth_t = torch.tensor(
                anchor_depth.reshape(-1, 1), dtype=torch.float32,
                device=device)

        # Spectral proxy
        self.has_spectral = spectral_xy is not None and len(spectral_xy) > 0
        if self.has_spectral:
            spec_norm = self.normalizer.normalize(spectral_xy)
            self.spectral_coords_t = torch.tensor(
                spec_norm, dtype=torch.float32, device=device)
            self.spectral_depth_t = torch.tensor(
                spectral_depth.reshape(-1, 1), dtype=torch.float32,
                device=device)

        # Centroid (in normalised space) for monotonicity
        self.centroid_norm = torch.tensor(
            shore_norm.mean(axis=0), dtype=torch.float32, device=device,
        )

        # Lake polygon mask — build cKDTree on shore for in-lake checks
        self.shore_tree = cKDTree(shore_norm)

        # Loss
        self.loss_fn = ImplicitFieldLoss(weights=loss_weights)

    # ── Training ──────────────────────────────────────────────────

    def train(
        self,
        n_epochs: int = 2000,
        lr: float = 1e-4,
        log_every: int = 100,
        patience: int = 500,
    ) -> List[Dict[str, float]]:
        """
        Fit the implicit field.

        Uses Adam with cosine-annealing LR schedule.  Logs composite
        loss every *log_every* epochs and stops early if the total loss
        has not improved for *patience* epochs.

        Returns:
            List of per-epoch metric dicts.
        """
        import torch

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=n_epochs, eta_min=lr * 0.01,
        )

        best_loss = float("inf")
        best_state = None
        stale = 0
        history: List[Dict[str, float]] = []

        log.info("Training implicit bathymetric field  "
                 f"(arch={self.arch}, epochs={n_epochs}, lr={lr})")
        t0 = time.time()

        for epoch in range(n_epochs):
            self.model.train()
            optimizer.zero_grad()

            components: Dict[str, torch.Tensor] = {}

            # 1. Shore loss (depth = 0)
            pred_shore = self.model(self.shore_t)
            components["shore"] = self.loss_fn.shore_loss(pred_shore)

            # 2. Anchor loss
            if self.has_anchors:
                pred_anc = self.model(self.anchor_coords_t)
                components["anchor"] = self.loss_fn.anchor_loss(
                    pred_anc, self.anchor_depth_t)

            # 3. Spectral proxy loss
            if self.has_spectral:
                pred_spec = self.model(self.spectral_coords_t)
                components["spectral"] = self.loss_fn.spectral_loss(
                    pred_spec, self.spectral_depth_t)

            # 4. Smoothness — need autograd through coords
            smooth_coords = self._sample_interior(512)
            smooth_coords.requires_grad_(True)
            pred_smooth = self.model(smooth_coords)
            try:
                components["smooth"] = self.loss_fn.smoothness_loss(
                    pred_smooth, smooth_coords)
            except RuntimeError:
                # autograd can fail on first epoch with some inits
                pass

            # 5. Monotonicity
            dist_center = torch.sqrt(
                ((smooth_coords.detach() - self.centroid_norm) ** 2).sum(dim=-1)
            )
            pred_mono = self.model(smooth_coords.detach())
            components["mono"] = self.loss_fn.monotonicity_loss(
                pred_mono, dist_center)

            # 6. Volume constraint
            if self.ae_volume is not None:
                vol_coords = self._sample_interior(1024)
                pred_vol = self.model(vol_coords)
                n_x = 100
                n_y = 100
                px_area = self.normalizer.pixel_area_m2(n_x, n_y)
                # Scale by sampling fraction (1024 out of ~10000 grid cells)
                scale = (n_x * n_y) / 1024.0
                components["volume"] = self.loss_fn.volume_loss(
                    pred_vol * scale, px_area, self.ae_volume)

            # 7. Max depth constraint
            all_preds = [pred_shore]
            if self.has_anchors:
                all_preds.append(pred_anc)
            if self.has_spectral:
                all_preds.append(pred_spec)
            all_pred_cat = torch.cat(all_preds, dim=0)
            components["maxdepth"] = self.loss_fn.maxdepth_loss(
                all_pred_cat, self.max_depth)

            # Composite loss
            total_loss, metrics = self.loss_fn(components)
            total_loss.backward()

            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()

            metrics["epoch"] = epoch
            metrics["lr"] = scheduler.get_last_lr()[0]
            history.append(metrics)

            loss_val = metrics["total"]
            if loss_val < best_loss:
                best_loss = loss_val
                best_state = {k: v.cpu().clone()
                              for k, v in self.model.state_dict().items()}
                stale = 0
            else:
                stale += 1

            if (epoch + 1) % log_every == 0:
                parts = "  ".join(f"{k}={v:.5f}" for k, v in metrics.items()
                                  if k not in ("epoch", "lr"))
                log.info(f"  Epoch {epoch + 1:>5}/{n_epochs}  {parts}")

            if stale >= patience:
                log.info(f"  Early stopping at epoch {epoch + 1} "
                         f"(no improvement for {patience} epochs)")
                break

        elapsed = time.time() - t0
        log.info(f"  Training complete in {elapsed:.1f}s  "
                 f"(best total loss = {best_loss:.6f})")

        if best_state is not None:
            self.model.load_state_dict(best_state)

        return history

    # ── Prediction ────────────────────────────────────────────────

    def predict_points(self, xy: np.ndarray) -> np.ndarray:
        """Predict depth at arbitrary (x, y) points in original coordinates.

        Args:
            xy: (N, 2) array of (x_metres, y_metres) relative to lake centroid

        Returns:
            (N,) depth array in original units (metres)
        """
        import torch

        coords_norm = self.normalizer.normalize(xy)
        coords_t = torch.tensor(coords_norm, dtype=torch.float32, device=self.device)

        self.model.eval()
        with torch.no_grad():
            pred = self.model(coords_t).squeeze(-1).cpu().numpy()

        return np.clip(pred, 0, self.max_depth)

    def predict_grid(
        self,
        resolution_m: float = 10.0,
        mc_samples: int = 10,
    ) -> Dict[str, np.ndarray]:
        """
        Evaluate the implicit field on a uniform grid over the lake.

        Args:
            resolution_m: grid spacing in original coordinate units
                          (typically metres).
            mc_samples:   number of MC-dropout forward passes for
                          uncertainty estimation (set to 1 to disable).

        Returns:
            dict with keys:
              depth   — (H, W) float32 depth in original units
              uncertainty — (H, W) float32 std-dev across MC passes
              mask    — (H, W) bool, True inside lake
              x       — (W,) raw x coordinates
              y       — (H,) raw y coordinates
        """
        import torch

        n_x = max(2, int(self.normalizer.x_range / resolution_m))
        n_y = max(2, int(self.normalizer.y_range / resolution_m))

        xs_norm = np.linspace(-1.0, 1.0, n_x)
        ys_norm = np.linspace(-1.0, 1.0, n_y)
        gx, gy = np.meshgrid(xs_norm, ys_norm)
        coords_norm = np.column_stack([gx.ravel(), gy.ravel()])

        # In-lake mask: points whose nearest shore distance < radius heuristic
        dists, _ = self.shore_tree.query(coords_norm)
        # Use convex-hull-ish mask: inside if dist < median shore spacing
        median_shore_spacing = np.median(
            np.linalg.norm(np.diff(self.shore_t.cpu().numpy(), axis=0), axis=1))
        in_lake = dists < (median_shore_spacing * 2.0 + 0.1)

        coords_t = torch.tensor(coords_norm, dtype=torch.float32,
                                device=self.device)

        # MC-dropout passes
        preds = []
        for _ in range(mc_samples):
            self.model.train()  # enable dropout
            with torch.no_grad():
                p = self.model(coords_t).squeeze().cpu().numpy()
            preds.append(p)

        self.model.eval()
        preds = np.stack(preds, axis=0)  # (mc, N)
        mean_depth = preds.mean(axis=0)
        std_depth = preds.std(axis=0) if mc_samples > 1 else np.zeros_like(mean_depth)

        # Reshape
        depth_grid = mean_depth.reshape(n_y, n_x)
        unc_grid = std_depth.reshape(n_y, n_x)
        mask = in_lake.reshape(n_y, n_x)

        # Enforce constraints on output
        depth_grid = np.clip(depth_grid, 0.0, self.max_depth * 1.1)
        depth_grid[~mask] = 0.0
        unc_grid[~mask] = 0.0

        # Raw coordinate axes
        xs_raw = np.linspace(self.normalizer.x_min, self.normalizer.x_max, n_x)
        ys_raw = np.linspace(self.normalizer.y_min, self.normalizer.y_max, n_y)

        return {
            "depth": depth_grid.astype(np.float32),
            "uncertainty": unc_grid.astype(np.float32),
            "mask": mask,
            "x": xs_raw,
            "y": ys_raw,
        }

    # ── Helpers ───────────────────────────────────────────────────

    def _sample_interior(self, n: int) -> "torch.Tensor":
        """Sample *n* random points inside the lake (normalised coords).

        Uses rejection sampling against the shore polygon.
        """
        import torch

        pts = []
        while len(pts) < n:
            candidates = np.random.uniform(-1.0, 1.0, size=(n * 2, 2))
            dists, _ = self.shore_tree.query(candidates)
            median_spacing = np.median(
                np.linalg.norm(
                    np.diff(self.shore_t.cpu().numpy(), axis=0), axis=1))
            keep = dists < (median_spacing * 2.0 + 0.1)
            pts.append(candidates[keep])
        pts = np.concatenate(pts, axis=0)[:n]
        return torch.tensor(pts, dtype=torch.float32, device=self.device)

    def save(self, output_dir: Union[str, Path]):
        """Persist model weights, normaliser, and metadata."""
        import torch

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        torch.save(self.model.state_dict(), output_dir / "model.pt")

        meta = {
            "arch": self.arch,
            "max_depth": self.max_depth,
            "ae_volume": self.ae_volume,
            "normalizer": self.normalizer.to_dict(),
            "loss_weights": self.loss_fn.w,
        }
        with open(output_dir / "meta.json", "w") as f:
            json.dump(meta, f, indent=2)

        log.info(f"  Saved implicit field to {output_dir}")


# ══════════════════════════════════════════════════════════════════════
# Convenience: fit_lake_implicit()
# ══════════════════════════════════════════════════════════════════════


def fit_lake_implicit(
    lake_id: str,
    shore_path: Union[str, Path],
    anchors_path: Optional[Union[str, Path]] = None,
    spectral_path: Optional[Union[str, Path]] = None,
    ae_volume: Optional[float] = None,
    max_depth: float = 30.0,
    arch: str = "siren",
    n_epochs: int = 2000,
    lr: float = 1e-4,
    resolution_m: float = 10.0,
    device: str = "cuda",
    output_dir: Optional[Union[str, Path]] = None,
) -> Dict:
    """
    End-to-end: load data, fit an implicit bathymetric field, predict grid.

    Args:
        lake_id:       unique lake identifier
        shore_path:    path to GeoJSON or CSV with shore boundary coords
        anchors_path:  path to parquet/CSV with columns (x, y, depth_m)
        spectral_path: path to parquet/CSV with columns (x, y, depth_m)
        ae_volume:     total lake volume from A-E curve (m^3)
        max_depth:     estimated maximum depth (m)
        arch:          'siren' or 'fourier'
        n_epochs:      training epochs
        lr:            learning rate
        resolution_m:  output grid resolution (m)
        device:        'cuda' or 'cpu'
        output_dir:    if provided, save model + raster here

    Returns:
        dict with keys: depth, uncertainty, mask, x, y, history, metrics
    """
    log.info(f"Fitting implicit bathymetric field for lake {lake_id}")
    t0 = time.time()

    # ── Load shore points ──
    shore_path = Path(shore_path)
    if shore_path.suffix == ".geojson" or shore_path.suffix == ".json":
        import geopandas as gpd
        gdf = gpd.read_file(shore_path)
        geom = gdf.geometry.iloc[0]
        # Extract boundary coordinates
        if hasattr(geom, "exterior"):
            coords = np.array(geom.exterior.coords)[:, :2]
        else:
            coords = np.array(geom.coords)[:, :2]
        shore_xy = coords
    elif shore_path.suffix == ".parquet":
        df = pd.read_parquet(shore_path)
        shore_xy = df[["x", "y"]].values
    else:
        df = pd.read_csv(shore_path)
        shore_xy = df[["x", "y"]].values

    log.info(f"  Shore points: {len(shore_xy)}")

    # ── Load anchor points ──
    anchor_xy, anchor_depth = None, None
    if anchors_path is not None:
        anchors_path = Path(anchors_path)
        if anchors_path.suffix == ".parquet":
            adf = pd.read_parquet(anchors_path)
        else:
            adf = pd.read_csv(anchors_path)
        adf = adf.dropna(subset=["x", "y", "depth_m"])
        anchor_xy = adf[["x", "y"]].values
        anchor_depth = adf["depth_m"].values
        log.info(f"  Anchor points: {len(anchor_xy)}")

    # ── Load spectral proxy ──
    spectral_xy, spectral_depth = None, None
    if spectral_path is not None:
        spectral_path = Path(spectral_path)
        if spectral_path.suffix == ".parquet":
            sdf = pd.read_parquet(spectral_path)
        else:
            sdf = pd.read_csv(spectral_path)
        sdf = sdf.dropna(subset=["x", "y", "depth_m"])
        spectral_xy = sdf[["x", "y"]].values
        spectral_depth = sdf["depth_m"].values
        log.info(f"  Spectral proxy points: {len(spectral_xy)}")

    # ── Build trainer ──
    trainer = ImplicitFieldTrainer(
        shore_xy=shore_xy,
        anchor_xy=anchor_xy,
        anchor_depth=anchor_depth,
        spectral_xy=spectral_xy,
        spectral_depth=spectral_depth,
        ae_volume=ae_volume,
        max_depth=max_depth,
        arch=arch,
        device=device,
    )

    # ── Train ──
    history = trainer.train(n_epochs=n_epochs, lr=lr)

    # ── Predict ──
    grid = trainer.predict_grid(resolution_m=resolution_m, mc_samples=10)

    # ── Metrics ──
    depth = grid["depth"]
    mask = grid["mask"]
    metrics = {
        "lake_id": lake_id,
        "arch": arch,
        "n_epochs_actual": len(history),
        "final_loss": history[-1]["total"] if history else None,
        "pred_max_depth": float(depth[mask].max()) if mask.any() else 0.0,
        "pred_mean_depth": float(depth[mask].mean()) if mask.any() else 0.0,
        "pred_volume_approx": float(
            depth[mask].sum() * trainer.normalizer.pixel_area_m2(
                depth.shape[1], depth.shape[0])
        ) if mask.any() else 0.0,
        "target_max_depth": max_depth,
        "target_volume": ae_volume,
        "mean_uncertainty": float(grid["uncertainty"][mask].mean())
        if mask.any() else 0.0,
        "elapsed_s": time.time() - t0,
    }

    log.info(f"  Predicted max depth: {metrics['pred_max_depth']:.2f} m")
    log.info(f"  Mean uncertainty:    {metrics['mean_uncertainty']:.3f} m")
    log.info(f"  Elapsed:             {metrics['elapsed_s']:.1f} s")

    # ── Save ──
    if output_dir is not None:
        output_dir = Path(output_dir)
        trainer.save(output_dir)

        np.savez_compressed(
            output_dir / "depth_grid.npz",
            depth=grid["depth"],
            uncertainty=grid["uncertainty"],
            mask=grid["mask"],
            x=grid["x"],
            y=grid["y"],
        )
        with open(output_dir / "metrics.json", "w") as f:
            json.dump(metrics, f, indent=2)
        with open(output_dir / "history.json", "w") as f:
            json.dump(history, f)
        log.info(f"  All outputs saved to {output_dir}")

    return {
        **grid,
        "history": history,
        "metrics": metrics,
    }


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Implicit Bathymetric Field — coordinate MLP with physics priors",
    )
    parser.add_argument("--lake-id", type=str, required=True,
                        help="Unique lake identifier")
    parser.add_argument("--shore-points", type=str, required=True,
                        help="GeoJSON, parquet, or CSV with shore boundary")
    parser.add_argument("--anchors", type=str, default=None,
                        help="Parquet/CSV with sonar/ICESat-2 depths (x, y, depth_m)")
    parser.add_argument("--spectral-proxy", type=str, default=None,
                        help="Parquet/CSV with XGBoost spectral depth proxy")
    parser.add_argument("--ae-volume", type=float, default=None,
                        help="Total lake volume from A-E curve (m^3)")
    parser.add_argument("--max-depth", type=float, required=True,
                        help="Estimated maximum depth (m)")
    parser.add_argument("--arch", type=str, default="siren",
                        choices=["siren", "fourier"],
                        help="Network architecture (default: siren)")
    parser.add_argument("--hidden-dim", type=int, default=256)
    parser.add_argument("--n-layers", type=int, default=5)
    parser.add_argument("--n-epochs", type=int, default=2000)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--resolution", type=float, default=10.0,
                        help="Output grid resolution in metres")
    parser.add_argument("--output", type=str, required=True,
                        help="Output directory")
    parser.add_argument("--device", type=str, default="cuda")
    args = parser.parse_args()

    result = fit_lake_implicit(
        lake_id=args.lake_id,
        shore_path=args.shore_points,
        anchors_path=args.anchors,
        spectral_path=args.spectral_proxy,
        ae_volume=args.ae_volume,
        max_depth=args.max_depth,
        arch=args.arch,
        n_epochs=args.n_epochs,
        lr=args.lr,
        resolution_m=args.resolution,
        device=args.device,
        output_dir=args.output,
    )

    log.info("Done.")


if __name__ == "__main__":
    main()
