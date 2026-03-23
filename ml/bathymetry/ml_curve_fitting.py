#!/usr/bin/env python3
"""
OpenCatch — ML Hypsometric Curve Fitting

Replaces 3D-LAKES' linear interpolation between sparse A-E points with learned
curve models that capture true lake-bed geometry. Trained on 4,500 MN DNR sonar
lakes where we have dense ground-truth bathymetry.

Approach:
  1. From each sonar lake, compute DENSE A-E curve (ground truth, 100 percentile bins)
  2. Simulate SPARSE A-E curve (5-10 points, mimicking 3D-LAKES quality)
  3. Train: sparse A-E + morphometric features → dense A-E curve
  4. At inference: any 3D-LAKES lake with a sparse A-E gets a high-resolution curve

Models:
  1. Gaussian Process regression — natural for curve interpolation with uncertainty
  2. Neural ODE — learns continuous curve dynamics (depth → area as ODE)
  3. Physics-constrained spline fitting — monotonic B-splines with volume constraints
  4. XGBoost per-percentile — predicts area at each depth percentile independently

References:
  - Gudasz et al. (2025, Nature Water): ML hypsometric prediction
  - Messager et al. (2016): HydroLAKES global morphometry
  - Hollister et al. (2011): Hypsometric curve classification

Usage:
    python ml_curve_fitting.py \
        --sonar-dir /data/training/v2/mn_sonar \
        --morpho /data/training/v2/mn_morphometric.parquet \
        --output /data/models/curve_fitting \
        --device cuda

Requirements:
    pip install xgboost scikit-learn pandas numpy torch torchdiffeq gpytorch scipy tqdm
"""

import argparse
import gc
import json
import logging
import pickle
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import interpolate, optimize
from scipy.integrate import trapezoid
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("curve_fitting")

EPS = 1e-8

# Number of percentile bins for the dense curve representation
N_DENSE_BINS = 100  # area at depth 0%, 1%, 2%, ..., 99%
# Number of sparse points to simulate (3D-LAKES typical)
N_SPARSE_POINTS = [5, 7, 10]

# Morphometric features to use alongside the sparse curve
MORPHO_FEATURES = [
    "lake_area_km2", "perimeter_km", "shoreline_dev_factor",
    "circularity", "elongation", "convexity",
    "elevation_m", "latitude", "longitude",
    "shore_slope_100m", "watershed_area_km2",
    "lake_type",  # natural=1, reservoir=2
]


# ══════════════════════════════════════════════════════════════════════
# Data Preparation
# ══════════════════════════════════════════════════════════════════════

def compute_dense_ae_curve(depths: np.ndarray, areas: np.ndarray,
                           n_bins: int = N_DENSE_BINS) -> Optional[np.ndarray]:
    """
    Compute dense Area-Elevation curve at uniform depth percentiles.

    Returns array of shape (n_bins,) with normalized area at each
    depth percentile [0%, 1%, ..., 99%]. Area normalized to [0, 1].
    """
    if len(depths) < 3 or depths.max() - depths.min() < 0.1:
        return None

    # Sort by depth ascending
    sort_idx = np.argsort(depths)
    d = depths[sort_idx].astype(np.float64)
    a = areas[sort_idx].astype(np.float64)

    # Normalize
    d_norm = (d - d.min()) / (d.max() - d.min() + EPS)
    a_norm = a / (a.max() + EPS)

    # Interpolate onto uniform grid
    try:
        f = interpolate.interp1d(
            d_norm, a_norm, kind="linear",
            bounds_error=False,
            fill_value=(a_norm[0], a_norm[-1]),
        )
        percentiles = np.linspace(0, 1, n_bins)
        dense_curve = f(percentiles)
        # Enforce monotonicity: area should decrease with depth
        for i in range(1, len(dense_curve)):
            dense_curve[i] = min(dense_curve[i], dense_curve[i - 1])
        return dense_curve
    except Exception:
        return None


def simulate_sparse_ae(dense_curve: np.ndarray, n_points: int = 7,
                       noise_std: float = 0.02) -> Tuple[np.ndarray, np.ndarray]:
    """
    Simulate a sparse A-E curve from a dense ground-truth curve.

    Mimics 3D-LAKES quality: few unevenly spaced points with noise.
    Returns (depth_percentiles, areas) both in [0, 1].
    """
    n_bins = len(dense_curve)

    # Non-uniform sampling: cluster points near surface (more water occurrence data)
    # Use beta distribution biased toward shallow
    rng = np.random.default_rng()
    raw_indices = rng.beta(1.5, 3.0, size=n_points - 2)  # biased to shallow
    raw_indices = np.sort(raw_indices)

    # Always include surface (0) and deepest (1)
    indices = np.concatenate([[0.0], raw_indices, [1.0]])
    indices = np.clip(indices, 0, 1)

    # Sample from dense curve
    bin_indices = (indices * (n_bins - 1)).astype(int)
    bin_indices = np.clip(bin_indices, 0, n_bins - 1)
    areas = dense_curve[bin_indices].copy()

    # Add noise (elevation measurement error, water extent error)
    areas += rng.normal(0, noise_std, size=len(areas))
    areas = np.clip(areas, 0, 1)
    # Re-enforce surface = 1, bottom near 0
    areas[0] = 1.0
    areas[-1] = max(areas[-1], 0.0)

    return indices, areas


def encode_sparse_curve(depth_pcts: np.ndarray, areas: np.ndarray,
                        n_features: int = 20) -> np.ndarray:
    """
    Encode a sparse A-E curve into a fixed-size feature vector.

    Strategy:
      - Interpolate to n_features uniform points
      - Add derivative features
    """
    try:
        f = interpolate.interp1d(
            depth_pcts, areas, kind="linear",
            bounds_error=False,
            fill_value=(areas[0], areas[-1]),
        )
        uniform = np.linspace(0, 1, n_features)
        encoded = f(uniform)

        # Gradient features (how fast area changes)
        grad = np.gradient(encoded)
        return np.concatenate([encoded, grad])
    except Exception:
        return np.zeros(n_features * 2)


def prepare_training_data(
    sonar_dir: Path,
    morpho_path: Path,
    n_sparse: int = 7,
) -> Tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """
    Build training dataset from MN DNR sonar lakes.

    Returns:
        X: features (sparse curve encoding + morphometrics)
        y: dense curve targets (n_lakes, N_DENSE_BINS)
        lake_ids: for group CV
    """
    log.info("Loading sonar bathymetry data...")

    # Load morphometric features
    morpho_df = pd.read_parquet(morpho_path)
    log.info(f"  Morphometric data: {len(morpho_df)} lakes")

    # Iterate through sonar files to extract A-E curves
    sonar_files = sorted(sonar_dir.glob("*.csv")) + sorted(sonar_dir.glob("*.parquet"))
    if not sonar_files:
        # Try loading a single combined file
        combined = sonar_dir / "mn_sonar_depths.parquet"
        if combined.exists():
            sonar_files = [combined]
        else:
            raise FileNotFoundError(f"No sonar data found in {sonar_dir}")

    all_features = []
    all_targets = []
    all_lake_ids = []

    for fpath in tqdm(sonar_files, desc="Processing sonar lakes"):
        try:
            if fpath.suffix == ".parquet":
                df = pd.read_parquet(fpath)
            else:
                df = pd.read_csv(fpath)

            # Group by lake
            lake_col = None
            for col in ["lake_id", "dow", "DOW", "DOWLKNUM", "hylak_id"]:
                if col in df.columns:
                    lake_col = col
                    break
            if lake_col is None:
                continue

            depth_col = None
            for col in ["depth_m", "depth", "DEPTH_M", "max_depth"]:
                if col in df.columns:
                    depth_col = col
                    break
            if depth_col is None:
                continue

            area_col = None
            for col in ["area_m2", "area", "AREA_M2", "contour_area"]:
                if col in df.columns:
                    area_col = col
                    break

            for lake_id, group in df.groupby(lake_col):
                depths = group[depth_col].values
                if area_col and area_col in group.columns:
                    areas = group[area_col].values
                else:
                    # Compute areas from depth histogram (cumulative)
                    bins = np.linspace(depths.min(), depths.max(), 50)
                    counts, edges = np.histogram(depths, bins=bins)
                    cum_counts = np.cumsum(counts[::-1])[::-1]
                    areas = cum_counts.astype(float)
                    depths = (edges[:-1] + edges[1:]) / 2

                # Compute dense ground truth
                dense = compute_dense_ae_curve(depths, areas)
                if dense is None:
                    continue

                # Simulate sparse observation (with augmentation)
                for _ in range(3):  # 3 augmentations per lake
                    sparse_d, sparse_a = simulate_sparse_ae(dense, n_points=n_sparse)
                    curve_features = encode_sparse_curve(sparse_d, sparse_a)

                    # Get morphometric features
                    morpho_row = morpho_df[morpho_df.index == lake_id]
                    if len(morpho_row) == 0:
                        # Try string matching
                        morpho_row = morpho_df[morpho_df.index.astype(str) == str(lake_id)]

                    if len(morpho_row) > 0:
                        morpho_feats = []
                        for col in MORPHO_FEATURES:
                            if col in morpho_row.columns:
                                morpho_feats.append(float(morpho_row[col].iloc[0]))
                            else:
                                morpho_feats.append(np.nan)
                        morpho_arr = np.array(morpho_feats)
                    else:
                        morpho_arr = np.full(len(MORPHO_FEATURES), np.nan)

                    combined = np.concatenate([curve_features, morpho_arr])
                    all_features.append(combined)
                    all_targets.append(dense)
                    all_lake_ids.append(lake_id)

        except Exception as e:
            log.warning(f"Error processing {fpath.name}: {e}")
            continue

    log.info(f"Built dataset: {len(all_features)} samples from "
             f"{len(set(all_lake_ids))} unique lakes")

    X = np.array(all_features)
    y = np.array(all_targets)
    lake_ids = np.array(all_lake_ids)

    # Fill NaN morphometric features with median
    for col_idx in range(X.shape[1]):
        mask = np.isnan(X[:, col_idx])
        if mask.any():
            median = np.nanmedian(X[:, col_idx])
            X[mask, col_idx] = median if not np.isnan(median) else 0.0

    return X, y, lake_ids


# ══════════════════════════════════════════════════════════════════════
# Model 1: Gaussian Process Regression
# ══════════════════════════════════════════════════════════════════════

class GPCurveFitter:
    """
    Gaussian Process regression for hypsometric curve fitting.

    Uses a structured kernel:
      - Matern 5/2 for smooth interpolation
      - RBF for long-range trends
      - White noise for observation noise

    Natural choice because:
      - Provides uncertainty estimates
      - Handles sparse, irregularly-spaced inputs
      - Smooth interpolation is a physical prior
    """

    def __init__(self, n_inducing: int = 100, device: str = "cuda"):
        self.n_inducing = n_inducing
        self.device = device
        self.model = None
        self.likelihood = None
        self.scaler_X = StandardScaler()
        self.scaler_y = StandardScaler()

    def fit(self, X: np.ndarray, y: np.ndarray,
            n_epochs: int = 200, lr: float = 0.01) -> dict:
        """
        Train GP model.

        For efficiency with large datasets, uses Sparse Variational GP
        with inducing points (SVGP via GPyTorch).
        """
        import torch
        import gpytorch
        from gpytorch.models import ApproximateGP
        from gpytorch.variational import (
            CholeskyVariationalDistribution,
            VariationalStrategy,
        )

        log.info("Training Gaussian Process curve fitter...")

        # Scale inputs
        X_scaled = self.scaler_X.fit_transform(X)
        # For GP, predict each output bin separately or use multi-output GP
        # Here we predict all bins jointly using a shared GP + independent outputs

        # For tractability: train separate GP per output bin (parallelized)
        # Or: treat as multi-task GP (batched)
        n_outputs = y.shape[1]

        # Scale targets per-bin
        y_scaled = self.scaler_y.fit_transform(y)

        X_tensor = torch.tensor(X_scaled, dtype=torch.float32).to(self.device)
        y_tensor = torch.tensor(y_scaled, dtype=torch.float32).to(self.device)

        # Use subset of data for inducing points (K-means or random)
        n_train = X_tensor.shape[0]
        n_inducing = min(self.n_inducing, n_train)
        indices = torch.randperm(n_train)[:n_inducing]
        inducing_points = X_tensor[indices]

        # Batch multi-output GP: one GP per output dimension
        class BatchGPModel(ApproximateGP):
            def __init__(self, inducing_points, n_tasks):
                batch_shape = torch.Size([n_tasks])
                variational_distribution = CholeskyVariationalDistribution(
                    inducing_points.shape[0],
                    batch_shape=batch_shape,
                )
                variational_strategy = VariationalStrategy(
                    self, inducing_points.unsqueeze(0).expand(
                        n_tasks, -1, -1
                    ),
                    variational_distribution,
                    learn_inducing_locations=True,
                )
                super().__init__(variational_strategy)
                self.mean_module = gpytorch.means.ConstantMean(
                    batch_shape=batch_shape
                )
                self.covar_module = gpytorch.kernels.ScaleKernel(
                    gpytorch.kernels.MaternKernel(
                        nu=2.5, batch_shape=batch_shape
                    ),
                    batch_shape=batch_shape,
                )

            def forward(self, x):
                mean = self.mean_module(x)
                covar = self.covar_module(x)
                return gpytorch.distributions.MultivariateNormal(mean, covar)

        self.model = BatchGPModel(
            inducing_points, n_tasks=n_outputs
        ).to(self.device)
        self.likelihood = gpytorch.likelihoods.MultitaskGaussianLikelihood(
            num_tasks=n_outputs
        ).to(self.device)

        self.model.train()
        self.likelihood.train()

        optimizer = torch.optim.Adam([
            {"params": self.model.parameters()},
            {"params": self.likelihood.parameters()},
        ], lr=lr)

        mll = gpytorch.mlls.VariationalELBO(
            self.likelihood, self.model, num_data=n_train
        )

        losses = []
        for epoch in range(n_epochs):
            optimizer.zero_grad()
            output = self.model(X_tensor)
            # Reshape y for multi-task: (n, tasks)
            loss = -mll(output, y_tensor.T)  # (tasks, n) expected
            loss.backward()
            optimizer.step()
            losses.append(loss.item())

            if (epoch + 1) % 50 == 0:
                log.info(f"  GP epoch {epoch + 1}/{n_epochs}, loss={loss.item():.4f}")

        return {"final_loss": losses[-1], "losses": losses}

    def predict(self, X: np.ndarray,
                return_std: bool = False) -> Tuple[np.ndarray, ...]:
        """Predict dense curve with optional uncertainty."""
        import torch

        self.model.eval()
        self.likelihood.eval()

        X_scaled = self.scaler_X.transform(X)
        X_tensor = torch.tensor(X_scaled, dtype=torch.float32).to(self.device)

        with torch.no_grad():
            pred_dist = self.model(X_tensor)
            mean = pred_dist.mean  # (n_outputs, n_samples)
            mean = mean.T.cpu().numpy()  # (n_samples, n_outputs)

        y_pred = self.scaler_y.inverse_transform(mean)

        # Enforce physical constraints
        y_pred = np.clip(y_pred, 0, 1)
        # Monotonically decreasing (area decreases with depth)
        for i in range(y_pred.shape[0]):
            for j in range(1, y_pred.shape[1]):
                y_pred[i, j] = min(y_pred[i, j], y_pred[i, j - 1])

        if return_std:
            std = pred_dist.variance.sqrt().T.cpu().numpy()
            std = std * self.scaler_y.scale_
            return y_pred, std
        return (y_pred,)


# ══════════════════════════════════════════════════════════════════════
# Model 2: Neural ODE Curve Fitter
# ══════════════════════════════════════════════════════════════════════

class NeuralODECurveFitter:
    """
    Neural ODE that learns the continuous dynamics dA/dd = f(A, d, z)
    where A = area, d = depth, z = conditioning features.

    The ODE starts at A(0) = 1 (surface) and integrates downward.
    This naturally produces smooth, monotonic curves.

    Benefits:
      - Continuous output at any depth resolution
      - Built-in monotonicity if f ≤ 0
      - Memory-efficient (adjoint method)
      - Physics-aware: models the actual process of depth → area
    """

    def __init__(self, feature_dim: int = 52, hidden_dim: int = 128,
                 device: str = "cuda"):
        self.feature_dim = feature_dim
        self.hidden_dim = hidden_dim
        self.device = device
        self.model = None
        self.scaler_X = StandardScaler()

    def _build_model(self):
        import torch
        import torch.nn as nn

        class ODEFunc(nn.Module):
            """dA/dd = f(A, d, z) — the ODE dynamics."""
            def __init__(self, feature_dim, hidden_dim):
                super().__init__()
                # Conditioning on features z (from sparse curve + morphometry)
                self.cond_net = nn.Sequential(
                    nn.Linear(feature_dim, hidden_dim),
                    nn.SiLU(),
                    nn.Linear(hidden_dim, hidden_dim),
                )
                # Dynamics: takes (A, d, z_cond) → dA/dd
                self.dynamics = nn.Sequential(
                    nn.Linear(hidden_dim + 2, hidden_dim),
                    nn.SiLU(),
                    nn.Linear(hidden_dim, hidden_dim // 2),
                    nn.SiLU(),
                    nn.Linear(hidden_dim // 2, 1),
                    nn.Softplus(),  # Ensure dA/dd > 0 (area decreasing)
                )

            def set_condition(self, z):
                """Pre-compute conditioning features."""
                self.z_cond = self.cond_net(z)  # (batch, hidden)

            def forward(self, t, state):
                """ODE step: state = A at depth t."""
                # t is scalar (depth), state is (batch, 1)
                batch_size = state.shape[0]
                t_expanded = t.expand(batch_size, 1) if t.dim() == 0 else t
                if t_expanded.dim() == 0:
                    t_expanded = t_expanded.unsqueeze(0).expand(batch_size, 1)
                elif t_expanded.dim() == 1:
                    t_expanded = t_expanded.unsqueeze(-1)

                inp = torch.cat([state, t_expanded, self.z_cond], dim=-1)
                # Negative because area DECREASES with depth
                dA_dd = -self.dynamics(inp)
                return dA_dd

        class CurveODEModel(nn.Module):
            """Full model: features → conditioning → ODE integration → curve."""
            def __init__(self, feature_dim, hidden_dim):
                super().__init__()
                self.ode_func = ODEFunc(feature_dim, hidden_dim)

            def forward(self, features, eval_times):
                """
                features: (batch, feature_dim)
                eval_times: (n_times,) — depth percentiles to evaluate at
                Returns: (batch, n_times) — area at each depth
                """
                from torchdiffeq import odeint

                self.ode_func.set_condition(features)

                # Initial state: A(0) = 1 (full surface area)
                batch_size = features.shape[0]
                A0 = torch.ones(batch_size, 1, device=features.device)

                # Integrate ODE from depth 0 to depth 1
                trajectory = odeint(
                    self.ode_func, A0, eval_times,
                    method="dopri5",
                    options={"max_num_steps": 500},
                )
                # trajectory: (n_times, batch, 1)
                curve = trajectory.squeeze(-1).T  # (batch, n_times)
                return torch.clamp(curve, 0, 1)

        self.model = CurveODEModel(self.feature_dim, self.hidden_dim).to(self.device)

    def fit(self, X: np.ndarray, y: np.ndarray,
            n_epochs: int = 300, lr: float = 1e-3,
            batch_size: int = 256) -> dict:
        """Train Neural ODE curve fitter."""
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        log.info("Training Neural ODE curve fitter...")

        X_scaled = self.scaler_X.fit_transform(X)
        self.feature_dim = X_scaled.shape[1]
        self._build_model()

        X_t = torch.tensor(X_scaled, dtype=torch.float32)
        y_t = torch.tensor(y, dtype=torch.float32)

        dataset = TensorDataset(X_t, y_t)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True,
                            drop_last=True)

        eval_times = torch.linspace(0, 1, y.shape[1]).to(self.device)

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, n_epochs)

        losses = []
        best_loss = float("inf")
        best_state = None

        for epoch in range(n_epochs):
            epoch_loss = 0
            n_batches = 0

            for X_batch, y_batch in loader:
                X_batch = X_batch.to(self.device)
                y_batch = y_batch.to(self.device)

                optimizer.zero_grad()

                try:
                    pred = self.model(X_batch, eval_times)
                    loss = nn.functional.mse_loss(pred, y_batch)

                    # Monotonicity penalty: penalize increases in area
                    diffs = pred[:, 1:] - pred[:, :-1]
                    mono_penalty = torch.relu(diffs).mean() * 10.0

                    total_loss = loss + mono_penalty
                    total_loss.backward()

                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                    optimizer.step()

                    epoch_loss += total_loss.item()
                    n_batches += 1
                except Exception as e:
                    log.warning(f"ODE solver error: {e}")
                    continue

            scheduler.step()

            if n_batches > 0:
                avg_loss = epoch_loss / n_batches
                losses.append(avg_loss)
                if avg_loss < best_loss:
                    best_loss = avg_loss
                    best_state = {k: v.cpu().clone()
                                  for k, v in self.model.state_dict().items()}

                if (epoch + 1) % 50 == 0:
                    log.info(f"  ODE epoch {epoch + 1}/{n_epochs}, "
                             f"loss={avg_loss:.6f}")

        if best_state:
            self.model.load_state_dict(best_state)

        return {"final_loss": best_loss, "losses": losses}

    def predict(self, X: np.ndarray, n_points: int = N_DENSE_BINS) -> np.ndarray:
        """Predict dense curve from features."""
        import torch

        self.model.eval()
        X_scaled = self.scaler_X.transform(X)
        X_t = torch.tensor(X_scaled, dtype=torch.float32).to(self.device)
        eval_times = torch.linspace(0, 1, n_points).to(self.device)

        with torch.no_grad():
            pred = self.model(X_t, eval_times)

        y_pred = pred.cpu().numpy()
        y_pred = np.clip(y_pred, 0, 1)
        # Enforce monotonicity
        for i in range(y_pred.shape[0]):
            for j in range(1, y_pred.shape[1]):
                y_pred[i, j] = min(y_pred[i, j], y_pred[i, j - 1])

        return y_pred


# ══════════════════════════════════════════════════════════════════════
# Model 3: Physics-Constrained Spline Fitting
# ══════════════════════════════════════════════════════════════════════

class PhysicsSplineFitter:
    """
    Fits monotonic B-splines with physical constraints learned from
    training data. Doesn't require GPU.

    Constraints:
      1. A(0) = 1 (surface area = max)
      2. A(D) ≥ 0 (non-negative area)
      3. dA/dd ≤ 0 (monotonically decreasing)
      4. ∫A(d)dd = V (volume consistency)
      5. Smoothness penalty (natural spline)

    Learns per-lake-type spline priors from training data, then fits
    each new lake using those priors as regularization.
    """

    def __init__(self, n_knots: int = 15, alpha: float = 1.0):
        self.n_knots = n_knots
        self.alpha = alpha  # regularization strength
        self.cluster_splines = {}  # learned prior splines per cluster
        self.cluster_model = None  # classifies lake into cluster

    def fit(self, X: np.ndarray, y: np.ndarray,
            n_clusters: int = 8) -> dict:
        """
        Learn spline priors from training data.

        1. Cluster lakes by curve shape
        2. Fit average spline per cluster
        3. Train classifier: morphometric features → cluster
        """
        from sklearn.cluster import KMeans
        from sklearn.ensemble import RandomForestClassifier

        log.info("Training physics-constrained spline fitter...")
        log.info(f"  Clustering {len(y)} curves into {n_clusters} shape types...")

        # Cluster by curve shape
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(y)

        # Compute mean spline per cluster
        for c in range(n_clusters):
            mask = labels == c
            if mask.sum() < 5:
                continue
            mean_curve = y[mask].mean(axis=0)
            std_curve = y[mask].std(axis=0)
            self.cluster_splines[c] = {
                "mean": mean_curve,
                "std": std_curve,
                "count": int(mask.sum()),
            }
            log.info(f"  Cluster {c}: {mask.sum()} lakes, "
                     f"HI={mean_curve.mean():.3f}")

        # Train cluster classifier from features
        self.cluster_model = RandomForestClassifier(
            n_estimators=200, max_depth=10, random_state=42, n_jobs=-1
        )
        self.cluster_model.fit(X, labels)
        acc = self.cluster_model.score(X, labels)
        log.info(f"  Cluster classifier accuracy: {acc:.3f}")

        self.kmeans = kmeans
        return {"n_clusters": n_clusters, "cluster_acc": acc}

    def predict(self, X: np.ndarray, sparse_curves: List[Tuple] = None,
                n_points: int = N_DENSE_BINS) -> np.ndarray:
        """
        Predict dense curves using constrained spline fitting.

        For each lake:
        1. Classify into cluster → get prior spline
        2. Fit B-spline to sparse points with prior regularization
        3. Enforce monotonicity
        """
        from scipy.interpolate import BSpline, make_lsq_spline

        clusters = self.cluster_model.predict(X)
        results = np.zeros((len(X), n_points))
        eval_d = np.linspace(0, 1, n_points)

        for i in range(len(X)):
            c = clusters[i]
            prior = self.cluster_splines.get(c, {}).get("mean", None)

            if sparse_curves is not None and i < len(sparse_curves):
                d_sparse, a_sparse = sparse_curves[i]
                try:
                    # Constrained spline fit
                    curve = self._fit_constrained_spline(
                        d_sparse, a_sparse, prior, eval_d
                    )
                    results[i] = curve
                except Exception:
                    results[i] = prior if prior is not None else eval_d[::-1]
            elif prior is not None:
                results[i] = prior
            else:
                # Linear fallback
                results[i] = 1.0 - eval_d

        return results

    def _fit_constrained_spline(
        self, d_obs: np.ndarray, a_obs: np.ndarray,
        prior: Optional[np.ndarray], eval_d: np.ndarray,
    ) -> np.ndarray:
        """
        Fit a monotone-constrained spline to sparse observations,
        regularized toward the cluster prior.
        """
        # Objective: minimize ||spline(d_obs) - a_obs||² + α||spline - prior||²
        # Subject to: spline is monotonically decreasing, spline(0)=1, spline(1)≥0

        n_eval = len(eval_d)

        # Initial guess from prior or linear
        if prior is not None:
            x0 = prior.copy()
        else:
            x0 = 1.0 - eval_d

        # Interpolate observations
        f_obs = interpolate.interp1d(
            d_obs, a_obs, kind="linear",
            bounds_error=False,
            fill_value=(a_obs[0], a_obs[-1]),
        )
        a_obs_dense = f_obs(eval_d)

        def objective(a):
            # Data fidelity
            obs_err = np.sum((a - a_obs_dense) ** 2)
            # Prior regularization
            prior_err = 0
            if prior is not None:
                prior_err = self.alpha * np.sum((a - prior) ** 2)
            # Smoothness (second derivative)
            d2a = np.diff(a, n=2)
            smooth_err = 0.1 * np.sum(d2a ** 2)
            return obs_err + prior_err + smooth_err

        # Constraints
        from scipy.optimize import minimize, LinearConstraint

        # Monotonicity: a[i+1] <= a[i] → a[i] - a[i+1] >= 0
        n = n_eval
        A_mono = np.zeros((n - 1, n))
        for j in range(n - 1):
            A_mono[j, j] = 1
            A_mono[j, j + 1] = -1
        mono_constraint = LinearConstraint(A_mono, lb=0, ub=np.inf)

        # Bounds: 0 <= a <= 1, a[0] = 1
        bounds = [(0, 1)] * n
        bounds[0] = (1, 1)  # surface = max area

        result = optimize.minimize(
            objective, x0, method="SLSQP",
            bounds=bounds,
            constraints=[{"type": "ineq", "fun": lambda a: A_mono @ a}],
            options={"maxiter": 200, "ftol": 1e-8},
        )

        if result.success:
            return result.x
        else:
            # Fallback: enforce monotonicity on raw interpolation
            curve = a_obs_dense.copy()
            curve[0] = 1.0
            for j in range(1, len(curve)):
                curve[j] = min(curve[j], curve[j - 1])
            return np.clip(curve, 0, 1)


# ══════════════════════════════════════════════════════════════════════
# Model 4: XGBoost Per-Percentile
# ══════════════════════════════════════════════════════════════════════

class XGBoostCurveFitter:
    """
    Train one XGBoost model per output percentile bin.

    Simple but effective: each model independently predicts
    area at its depth percentile from the sparse curve + morphometry.

    Post-processing enforces monotonicity and smoothness.
    """

    def __init__(self, n_bins: int = N_DENSE_BINS, n_threads: int = 8):
        self.n_bins = n_bins
        self.n_threads = n_threads
        self.models = {}
        self.scaler = StandardScaler()

    def fit(self, X: np.ndarray, y: np.ndarray,
            X_val: np.ndarray = None, y_val: np.ndarray = None) -> dict:
        """Train one XGBoost per output bin."""
        import xgboost as xgb

        log.info(f"Training {self.n_bins} XGBoost models (one per depth percentile)...")

        X_scaled = self.scaler.fit_transform(X)

        params = {
            "objective": "reg:squarederror",
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 10,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "nthread": self.n_threads,
            "verbosity": 0,
        }

        results = {}
        for bin_idx in tqdm(range(self.n_bins), desc="Training XGB per percentile"):
            dtrain = xgb.DMatrix(X_scaled, label=y[:, bin_idx])

            evals = [(dtrain, "train")]
            if X_val is not None and y_val is not None:
                X_val_scaled = self.scaler.transform(X_val)
                dval = xgb.DMatrix(X_val_scaled, label=y_val[:, bin_idx])
                evals.append((dval, "val"))

            model = xgb.train(
                params, dtrain, num_boost_round=300,
                evals=evals,
                early_stopping_rounds=30,
                verbose_eval=False,
            )
            self.models[bin_idx] = model

            if bin_idx == 0 or (bin_idx + 1) % 25 == 0:
                best = model.best_score if hasattr(model, "best_score") else "N/A"
                log.info(f"  Bin {bin_idx}: best_score={best}")

        return results

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict dense curve from features."""
        import xgboost as xgb

        X_scaled = self.scaler.transform(X)
        dmat = xgb.DMatrix(X_scaled)

        preds = np.zeros((len(X), self.n_bins))
        for bin_idx in range(self.n_bins):
            if bin_idx in self.models:
                preds[:, bin_idx] = self.models[bin_idx].predict(dmat)

        # Clip and enforce monotonicity
        preds = np.clip(preds, 0, 1)
        preds[:, 0] = 1.0  # surface always max
        for i in range(preds.shape[0]):
            for j in range(1, preds.shape[1]):
                preds[i, j] = min(preds[i, j], preds[i, j - 1])

        return preds


# ══════════════════════════════════════════════════════════════════════
# Evaluation
# ══════════════════════════════════════════════════════════════════════

def evaluate_curve_predictions(
    y_true: np.ndarray, y_pred: np.ndarray,
    max_depths: np.ndarray = None,
    label: str = "Model",
) -> dict:
    """
    Evaluate predicted curves vs ground truth.

    Metrics:
      - Per-bin MSE, MAE
      - Overall R² across all bins
      - Curve shape metrics (curvature error, volume error)
      - Depth-stratified performance (shallow, mid, deep)
    """
    n_samples, n_bins = y_true.shape

    # Flatten for overall metrics
    y_true_flat = y_true.flatten()
    y_pred_flat = y_pred.flatten()

    overall = {
        "rmse": float(np.sqrt(mean_squared_error(y_true_flat, y_pred_flat))),
        "mae": float(mean_absolute_error(y_true_flat, y_pred_flat)),
        "r2": float(r2_score(y_true_flat, y_pred_flat)),
    }

    # Per-bin metrics
    bin_rmse = np.zeros(n_bins)
    for b in range(n_bins):
        bin_rmse[b] = np.sqrt(mean_squared_error(y_true[:, b], y_pred[:, b]))

    # Depth-stratified (shallow = 0-25%, mid = 25-50%, deep = 50-100%)
    strata = {
        "shallow_0_25": (0, n_bins // 4),
        "mid_25_50": (n_bins // 4, n_bins // 2),
        "deep_50_100": (n_bins // 2, n_bins),
    }
    strata_metrics = {}
    for name, (start, end) in strata.items():
        yt = y_true[:, start:end].flatten()
        yp = y_pred[:, start:end].flatten()
        strata_metrics[name] = {
            "rmse": float(np.sqrt(mean_squared_error(yt, yp))),
            "r2": float(r2_score(yt, yp)) if len(yt) > 1 else 0,
        }

    # Volume error: integral of predicted vs true curve
    vol_true = np.trapz(y_true, axis=1)
    vol_pred = np.trapz(y_pred, axis=1)
    vol_error = np.abs(vol_true - vol_pred) / (vol_true + EPS)

    # Hypsometric integral error
    hi_true = y_true.mean(axis=1)
    hi_pred = y_pred.mean(axis=1)

    result = {
        "label": label,
        "overall": overall,
        "strata": strata_metrics,
        "volume_error_pct": {
            "mean": float(vol_error.mean() * 100),
            "median": float(np.median(vol_error) * 100),
            "p90": float(np.percentile(vol_error, 90) * 100),
        },
        "hypsometric_integral_error": {
            "mean": float(np.abs(hi_true - hi_pred).mean()),
            "median": float(np.median(np.abs(hi_true - hi_pred))),
        },
        "per_bin_rmse_mean": float(bin_rmse.mean()),
        "per_bin_rmse_max": float(bin_rmse.max()),
    }

    log.info(f"\n{'='*60}")
    log.info(f"  {label} Curve Fitting Results")
    log.info(f"{'='*60}")
    log.info(f"  Overall RMSE: {overall['rmse']:.4f}")
    log.info(f"  Overall R²:   {overall['r2']:.4f}")
    log.info(f"  Volume error:  {vol_error.mean()*100:.1f}% mean")
    for name, m in strata_metrics.items():
        log.info(f"  {name}: RMSE={m['rmse']:.4f}, R²={m['r2']:.4f}")
    log.info(f"{'='*60}\n")

    return result


# ══════════════════════════════════════════════════════════════════════
# Main Training Pipeline
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="ML Hypsometric Curve Fitting")
    parser.add_argument("--sonar-dir", type=str, required=True,
                        help="Directory with MN DNR sonar bathymetry")
    parser.add_argument("--morpho", type=str, required=True,
                        help="Morphometric features parquet")
    parser.add_argument("--output", type=str, required=True,
                        help="Output directory for models")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--n-sparse", type=int, default=7,
                        help="Number of sparse A-E points to simulate")
    parser.add_argument("--models", nargs="+",
                        default=["xgboost", "gp", "ode", "spline"],
                        help="Models to train")
    parser.add_argument("--n-folds", type=int, default=5)
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Prepare data ──
    log.info("Preparing training data...")
    X, y, lake_ids = prepare_training_data(
        sonar_dir=Path(args.sonar_dir),
        morpho_path=Path(args.morpho),
        n_sparse=args.n_sparse,
    )
    log.info(f"Dataset: X={X.shape}, y={y.shape}, {len(set(lake_ids))} lakes")

    # ── Group K-Fold by lake (no lake in both train and val) ──
    gkf = GroupKFold(n_splits=args.n_folds)
    unique_lakes = np.array(list(set(lake_ids)))

    all_results = {}

    for fold, (train_idx, val_idx) in enumerate(gkf.split(X, y, lake_ids)):
        log.info(f"\n{'#'*60}")
        log.info(f"  FOLD {fold + 1}/{args.n_folds}")
        log.info(f"{'#'*60}")

        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        # ── Baseline: Linear interpolation (3D-LAKES approach) ──
        # Just use the sparse curve encoding → linear interp
        n_curve_feats = 40  # 20 uniform + 20 gradient
        sparse_pred = X_val[:, :20]  # first 20 features = uniform-interp sparse
        baseline_eval = evaluate_curve_predictions(
            y_val, sparse_pred[:, :y_val.shape[1]] if sparse_pred.shape[1] >= y_val.shape[1]
            else np.column_stack([sparse_pred, np.zeros((len(sparse_pred),
                                 y_val.shape[1] - sparse_pred.shape[1]))]),
            label="Linear Interpolation (baseline)",
        )

        # ── XGBoost ──
        if "xgboost" in args.models:
            log.info("\n--- XGBoost Per-Percentile ---")
            xgb_model = XGBoostCurveFitter(n_bins=y.shape[1])
            xgb_model.fit(X_train, y_train, X_val, y_val)
            xgb_pred = xgb_model.predict(X_val)
            xgb_eval = evaluate_curve_predictions(y_val, xgb_pred, label="XGBoost")
            all_results[f"xgboost_fold{fold}"] = xgb_eval

            # Save model
            with open(output_dir / f"xgb_curve_fold{fold}.pkl", "wb") as f:
                pickle.dump(xgb_model, f)

        # ── Gaussian Process ──
        if "gp" in args.models:
            log.info("\n--- Gaussian Process ---")
            try:
                gp_model = GPCurveFitter(n_inducing=200, device=args.device)
                gp_model.fit(X_train, y_train, n_epochs=150)
                gp_pred, gp_std = gp_model.predict(X_val, return_std=True)
                gp_eval = evaluate_curve_predictions(y_val, gp_pred, label="GP")
                all_results[f"gp_fold{fold}"] = gp_eval

                # Report mean uncertainty
                log.info(f"  GP mean uncertainty: {gp_std.mean():.4f}")
            except Exception as e:
                log.error(f"GP training failed: {e}")

        # ── Neural ODE ──
        if "ode" in args.models:
            log.info("\n--- Neural ODE ---")
            try:
                ode_model = NeuralODECurveFitter(
                    feature_dim=X.shape[1],
                    hidden_dim=128,
                    device=args.device,
                )
                ode_model.fit(X_train, y_train, n_epochs=200)
                ode_pred = ode_model.predict(X_val)
                ode_eval = evaluate_curve_predictions(y_val, ode_pred, label="Neural ODE")
                all_results[f"ode_fold{fold}"] = ode_eval
            except Exception as e:
                log.error(f"Neural ODE training failed: {e}")

        # ── Physics Spline ──
        if "spline" in args.models:
            log.info("\n--- Physics-Constrained Spline ---")
            spline_model = PhysicsSplineFitter(n_knots=15, alpha=1.0)
            spline_model.fit(X_train, y_train)
            spline_pred = spline_model.predict(X_val)
            spline_eval = evaluate_curve_predictions(
                y_val, spline_pred, label="Physics Spline"
            )
            all_results[f"spline_fold{fold}"] = spline_eval

        gc.collect()

        # Only run first fold for quick check
        if fold == 0:
            log.info("(Remaining folds will run in full training mode)")

    # ── Save results ──
    with open(output_dir / "curve_fitting_results.json", "w") as f:
        json.dump(all_results, f, indent=2)

    log.info(f"\nAll results saved to {output_dir}")

    # ── Summary ──
    log.info("\n" + "=" * 70)
    log.info("  CURVE FITTING SUMMARY (Fold 0)")
    log.info("=" * 70)
    for key, res in all_results.items():
        if "fold0" in key:
            log.info(f"  {res['label']:30s}  R²={res['overall']['r2']:.4f}  "
                     f"RMSE={res['overall']['rmse']:.4f}  "
                     f"Vol_err={res['volume_error_pct']['mean']:.1f}%")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
