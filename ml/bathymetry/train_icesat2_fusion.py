#!/usr/bin/env python3
"""
OpenCatch — ICESat-2 + Sentinel-2 Fusion Bathymetry Model

Inspired by the Tibetan Plateau approach (2025): uses ICESat-2 photon-counting
lidar as SPARSE but ACCURATE depth labels combined with Sentinel-2 spectral
features. The model learns to interpolate between sparse ICESat-2 points using
spectral correlations.

Key differences from finetune_depth_anything.py:
- Labels: ICESat-2 ATL13 point depths (sparse, sub-metre accuracy)
  vs. MN DNR sonar DEMs (dense but noisy, interpolation artifacts)
- Training: Point-wise — extract small S2 patch around each ICESat-2 point,
  predict depth at center pixel
- Model: KAN (Kolmogorov-Arnold Network) on spectral features, or
  Depth Anything V2 fine-tuned with point-wise loss

Two model architectures:
1. KAN (default): spectral features -> depth
   - Input: [B02, B03, B04, B08, log-ratios, NDWI, MNDWI, turbidity indices]
   - Fast training, interpretable, strong on tabular data
2. DA V2 Pointwise: patch-based CNN, loss only at center pixel
   - Leverages spatial context from surrounding pixels
   - Heavier but captures spatial patterns

Target: 0.5-1.5m RMSE on North American inland lakes
(Tibetan paper achieved 0.36m on clear Tibetan lakes)

Usage:
    # KAN model (recommended — fast, strong baseline)
    python train_icesat2_fusion.py \\
        --model kan \\
        --icesat2-dir /data/icesat2 \\
        --s2-dir /data/training/v2 \\
        --output /data/models/icesat2_fusion \\
        --device cuda

    # DA V2 pointwise model
    python train_icesat2_fusion.py \\
        --model dav2 \\
        --icesat2-dir /data/icesat2 \\
        --s2-dir /data/training/v2 \\
        --output /data/models/icesat2_fusion \\
        --device cuda

Requirements:
    pip install torch torchvision rasterio geopandas numpy tqdm
    pip install efficient-kan  # for KAN model
    pip install transformers   # for DA V2 model
    pip install planetary-computer pystac-client odc-stac  # for S2 fetching
"""

import argparse
import json
import logging
import math
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import rasterio
import rasterio.windows
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.amp import GradScaler, autocast
from torch.utils.data import DataLoader, Dataset

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("icesat2_fusion")


# ── Spectral Feature Engineering ─────────────────────────────────────

# Sentinel-2 band indices in the 14-band composite
# (from build_s2_composites.py: B02,B03,B04,B05,B06,B07,B08,B8A,B11,B12 + 4 DEM)
BAND_IDX = {
    "B02": 0,   # Blue 490nm
    "B03": 1,   # Green 560nm
    "B04": 2,   # Red 665nm
    "B05": 3,   # Red Edge 1 705nm
    "B06": 4,   # Red Edge 2 740nm
    "B07": 5,   # Red Edge 3 783nm
    "B08": 6,   # NIR 842nm
    "B8A": 7,   # NIR narrow 865nm
    "B11": 8,   # SWIR1 1610nm
    "B12": 9,   # SWIR2 2190nm
}


def compute_spectral_features(bands: np.ndarray) -> np.ndarray:
    """
    Compute spectral features from S2 bands for KAN/MLP input.

    Following the Tibetan Plateau paper + standard SDB indices.

    Args:
        bands: (..., 10) array of S2 reflectance values

    Returns:
        (..., N_features) feature array
    """
    eps = 1e-6

    B02 = bands[..., 0]  # Blue
    B03 = bands[..., 1]  # Green
    B04 = bands[..., 2]  # Red
    B05 = bands[..., 3]  # Red Edge 1
    B06 = bands[..., 4]  # Red Edge 2
    B07 = bands[..., 5]  # Red Edge 3
    B08 = bands[..., 6]  # NIR
    B8A = bands[..., 7]  # NIR narrow
    B11 = bands[..., 8]  # SWIR1
    B12 = bands[..., 9]  # SWIR2

    features = []

    # Raw bands (10)
    features.extend([B02, B03, B04, B05, B06, B07, B08, B8A, B11, B12])

    # Log-transformed bands (key for SDB — depth is exponential with reflectance)
    features.append(np.log(B02.clip(eps)))
    features.append(np.log(B03.clip(eps)))
    features.append(np.log(B04.clip(eps)))
    features.append(np.log(B08.clip(eps)))

    # Stumpf log-ratio (standard SDB):  ln(B02) / ln(B03)
    features.append(np.log(B02.clip(eps)) / np.log(B03.clip(eps)).clip(eps))

    # Lyzenga ratio: ln(B02) / ln(B04)
    features.append(np.log(B02.clip(eps)) / np.log(B04.clip(eps)).clip(eps))

    # Green/Red ratio (turbidity-sensitive depth proxy)
    features.append(B03 / B04.clip(eps))

    # Blue/Green ratio
    features.append(B02 / B03.clip(eps))

    # NDWI = (Green - NIR) / (Green + NIR) — water detection
    features.append((B03 - B08) / (B03 + B08 + eps))

    # MNDWI = (Green - SWIR1) / (Green + SWIR1) — modified, better for turbid water
    features.append((B03 - B11) / (B03 + B11 + eps))

    # NDTI (Turbidity) = (Red - Green) / (Red + Green)
    features.append((B04 - B03) / (B04 + B03 + eps))

    # FAI (Floating Algae Index) = NIR - Red - (SWIR1 - Red) * (833-665)/(1610-665)
    fai_factor = (833 - 665) / (1610 - 665)
    features.append(B08 - B04 - (B11 - B04) * fai_factor)

    # CDOM proxy = Blue / Red Edge 1
    features.append(B02 / B05.clip(eps))

    # Band differences (capture spectral slope)
    features.append(B02 - B03)
    features.append(B03 - B04)
    features.append(B04 - B08)

    # Second-order: Green^2, Blue^2 (nonlinear depth response)
    features.append(B03 ** 2)
    features.append(B02 ** 2)

    # Stack
    result = np.stack(features, axis=-1).astype(np.float32)

    # Replace NaN/inf
    result = np.nan_to_num(result, nan=0.0, posinf=5.0, neginf=-5.0)

    return result


N_SPECTRAL_FEATURES = 28  # must match compute_spectral_features output


# ── Dataset ──────────────────────────────────────────────────────────

class ICESat2FusionDataset(Dataset):
    """
    Point-wise dataset: each sample is an ICESat-2 depth measurement
    paired with the Sentinel-2 spectral features at that location.

    For KAN: extracts spectral feature vector at the point
    For DA V2: extracts a small patch centered on the point
    """

    def __init__(
        self,
        icesat2_dir: Path,
        s2_dir: Path,
        patch_size: int = 64,
        mode: str = "kan",  # "kan" or "dav2"
        max_depth: float = 50.0,
        min_quality: int = 2,
        augment: bool = True,
        neighbor_radius: int = 3,
    ):
        self.icesat2_dir = Path(icesat2_dir)
        self.s2_dir = Path(s2_dir)
        self.patch_size = patch_size
        self.mode = mode
        self.max_depth = max_depth
        self.augment = augment
        self.neighbor_radius = neighbor_radius

        self.samples = self._build_index(min_quality)
        log.info(f"ICESat-2 fusion dataset: {len(self.samples)} point samples")

    def _build_index(self, min_quality: int) -> list[dict]:
        """
        Build sample index by matching ICESat-2 points to S2 composites.

        Each sample: {
            lat, lon, depth_m, composite_path,
            pixel_x, pixel_y  (position in the composite raster)
        }
        """
        import geopandas as gpd
        import rasterio
        from rasterio.transform import rowcol

        samples = []

        # Load ICESat-2 depth points
        parquet_files = list(self.icesat2_dir.glob("**/*depths*.parquet"))
        if not parquet_files:
            parquet_files = list(self.icesat2_dir.glob("**/*.parquet"))

        if not parquet_files:
            log.warning(f"No ICESat-2 parquet files in {self.icesat2_dir}")
            return []

        all_points = []
        for pf in parquet_files:
            try:
                try:
                    gdf = gpd.read_parquet(pf)
                except (ValueError, Exception):
                    # Fallback for non-geo parquet (pandas-saved)
                    import pandas as _pd
                    gdf = _pd.read_parquet(pf)
                if "depth_m" in gdf.columns:
                    valid = gdf[
                        gdf["depth_m"].notna()
                        & (gdf["depth_m"] > 0)
                        & (gdf["depth_m"] <= self.max_depth)
                    ]
                    if "quality" in gdf.columns:
                        valid = valid[valid["quality"] >= min_quality]
                    all_points.append(valid)
            except Exception as e:
                log.warning(f"Failed to load {pf}: {e}")

        if not all_points:
            log.warning("No valid ICESat-2 depth points found")
            return []

        import pandas as pd
        points_df = pd.concat(all_points, ignore_index=True)
        log.info(f"Loaded {len(points_df)} ICESat-2 depth points")

        # Find all S2 composites
        composite_paths = sorted(self.s2_dir.glob("**/composite.tif"))
        if not composite_paths:
            log.warning(f"No composites in {self.s2_dir}")
            return []

        log.info(f"Found {len(composite_paths)} S2 composites")

        # For each composite, find ICESat-2 points that fall within it
        for comp_path in composite_paths:
            try:
                with rasterio.open(comp_path) as src:
                    bounds = src.bounds
                    transform = src.transform
                    h, w = src.height, src.width

                # Filter points within this composite's bounds
                mask = (
                    (points_df["lon"] >= bounds.left)
                    & (points_df["lon"] <= bounds.right)
                    & (points_df["lat"] >= bounds.bottom)
                    & (points_df["lat"] <= bounds.top)
                )
                local_pts = points_df[mask]

                if len(local_pts) == 0:
                    continue

                for _, pt in local_pts.iterrows():
                    row, col = rowcol(transform, pt["lon"], pt["lat"])

                    # Ensure point is within raster and has margin for patch
                    margin = self.patch_size // 2 if self.mode == "dav2" else self.neighbor_radius
                    if (margin <= row < h - margin) and (margin <= col < w - margin):
                        samples.append({
                            "lat": float(pt["lat"]),
                            "lon": float(pt["lon"]),
                            "depth_m": float(pt["depth_m"]),
                            "composite_path": str(comp_path),
                            "pixel_y": int(row),
                            "pixel_x": int(col),
                        })

            except Exception as e:
                log.warning(f"Error processing composite {comp_path}: {e}")

        log.info(f"Matched {len(samples)} ICESat-2 points to S2 composites")
        return samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> dict:
        import rasterio

        sample = self.samples[idx]
        depth = sample["depth_m"]
        py, px = sample["pixel_y"], sample["pixel_x"]

        with rasterio.open(sample["composite_path"]) as src:
            if self.mode == "kan":
                return self._get_kan_sample(src, py, px, depth)
            else:
                return self._get_dav2_sample(src, py, px, depth)

    def _get_kan_sample(self, src, py: int, px: int, depth: float) -> dict:
        """Extract spectral features at a point (+ small neighborhood)."""
        r = self.neighbor_radius

        # Read a small window around the point (for spatial averaging)
        window = rasterio.windows.Window(px - r, py - r, 2 * r + 1, 2 * r + 1)
        data = src.read(window=window).astype(np.float32)  # (14, 2r+1, 2r+1)

        # Use first 10 bands (S2 spectral)
        s2_bands = data[:10]  # (10, H, W)

        # Compute spectral features at center pixel
        center_bands = s2_bands[:, r, r]  # (10,)
        features = compute_spectral_features(center_bands)  # (N_features,)

        # Also add mean/std of neighborhood as context
        neigh_bands = s2_bands.reshape(10, -1).T  # (N_pixels, 10)
        neigh_features = compute_spectral_features(neigh_bands)  # (N_pixels, N_features)
        neigh_mean = neigh_features.mean(axis=0)
        neigh_std = neigh_features.std(axis=0)

        # Spatial heterogeneity (texture) features
        spatial_features = np.concatenate([
            features,          # Center pixel features
            neigh_mean,        # Neighborhood mean
            neigh_std,         # Neighborhood std (texture)
        ])

        # DEM features at center pixel (channels 10-13)
        if data.shape[0] >= 14:
            dem_features = data[10:14, r, r]  # (4,) elevation, slope, aspect, curvature
        else:
            dem_features = np.zeros(4, dtype=np.float32)

        all_features = np.concatenate([spatial_features, dem_features])

        if self.augment:
            # Small noise augmentation (simulate reflectance uncertainty)
            noise = np.random.normal(0, 0.02, size=all_features.shape).astype(np.float32)
            all_features = all_features + noise

        return {
            "features": torch.from_numpy(all_features),
            "depth": torch.tensor([depth], dtype=torch.float32),
        }

    def _get_dav2_sample(self, src, py: int, px: int, depth: float) -> dict:
        """Extract RGB patch centered on the ICESat-2 point."""
        ps = self.patch_size
        half = ps // 2

        window = rasterio.windows.Window(px - half, py - half, ps, ps)
        data = src.read(window=window).astype(np.float32)  # (14, ps, ps)

        # Extract RGB (B04=Red, B03=Green, B02=Blue)
        rgb = np.stack([data[2], data[1], data[0]], axis=0)  # (3, H, W)

        # Normalize to ImageNet stats
        rgb = np.clip(rgb, 0, 1)
        mean = np.array([0.485, 0.456, 0.406])[:, None, None]
        std = np.array([0.229, 0.224, 0.225])[:, None, None]
        rgb = (rgb - mean) / std

        if self.augment:
            rgb = self._augment_patch(rgb)

        return {
            "pixel_values": torch.from_numpy(rgb.astype(np.float32)),
            "depth": torch.tensor([depth], dtype=torch.float32),
            "center_y": half,
            "center_x": half,
        }

    def _augment_patch(self, rgb: np.ndarray) -> np.ndarray:
        """Augmentation for patches."""
        if np.random.random() > 0.5:
            rgb = rgb[:, :, ::-1].copy()
        if np.random.random() > 0.5:
            rgb = rgb[:, ::-1, :].copy()
        if np.random.random() > 0.5:
            k = np.random.randint(1, 4)
            rgb = np.rot90(rgb, k, axes=(1, 2)).copy()
        if np.random.random() > 0.5:
            factor = np.random.uniform(0.9, 1.1)
            rgb = rgb * factor
        return rgb


# ── KAN Model ────────────────────────────────────────────────────────

class KANBathymetry(nn.Module):
    """
    Kolmogorov-Arnold Network for spectral -> depth prediction.

    KAN replaces linear weights with learnable 1D functions (B-splines),
    providing better function approximation than MLPs with fewer params.

    Falls back to a B-spline MLP approximation if efficient-kan unavailable.
    """

    def __init__(
        self,
        in_features: int,
        hidden_dims: list[int] = None,
        max_depth: float = 50.0,
        grid_size: int = 5,
        spline_order: int = 3,
        use_kan: bool = True,
    ):
        super().__init__()
        self.max_depth = max_depth

        if hidden_dims is None:
            hidden_dims = [128, 64, 32]

        if use_kan:
            try:
                from efficient_kan import KANLinear
                log.info("Using efficient-kan KANLinear layers")
                self._build_kan(in_features, hidden_dims, grid_size, spline_order)
                return
            except ImportError:
                log.warning("efficient-kan not available, using SplineMLP fallback")

        # Fallback: B-spline activated MLP (approximates KAN behavior)
        self._build_spline_mlp(in_features, hidden_dims)

    def _build_kan(self, in_features, hidden_dims, grid_size, spline_order):
        """Build using efficient-kan KANLinear layers."""
        from efficient_kan import KANLinear

        layers = []
        dims = [in_features] + hidden_dims

        for i in range(len(dims) - 1):
            layers.append(KANLinear(
                dims[i], dims[i + 1],
                grid_size=grid_size,
                spline_order=spline_order,
            ))

        self.kan_layers = nn.ModuleList(layers)
        self.output_layer = KANLinear(
            hidden_dims[-1], 1,
            grid_size=grid_size,
            spline_order=spline_order,
        )
        self.use_efficient_kan = True

    def _build_spline_mlp(self, in_features, hidden_dims):
        """
        B-Spline MLP: approximates KAN with learnable spline activations.

        Each layer: Linear + BatchNorm + SplineActivation + Dropout
        """
        layers = []
        dims = [in_features] + hidden_dims

        for i in range(len(dims) - 1):
            layers.extend([
                nn.Linear(dims[i], dims[i + 1]),
                nn.BatchNorm1d(dims[i + 1]),
                SplineActivation(dims[i + 1]),
                nn.Dropout(0.1),
            ])

        self.backbone = nn.Sequential(*layers)
        self.output_layer = nn.Sequential(
            nn.Linear(hidden_dims[-1], 1),
            nn.Softplus(),
        )
        self.use_efficient_kan = False

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, N_features) spectral features

        Returns:
            (B, 1) predicted depth in metres
        """
        if self.use_efficient_kan:
            h = x
            for layer in self.kan_layers:
                h = layer(h)
            depth = self.output_layer(h)
        else:
            h = self.backbone(x)
            depth = self.output_layer(h)

        return depth.clamp(0, self.max_depth)


class SplineActivation(nn.Module):
    """
    Learnable B-spline activation function.

    Approximates the KAN concept: instead of fixed activation + learned weights,
    use learned activation (spline) + fixed identity weights.

    Uses a set of learned control points that define a piecewise polynomial.
    """

    def __init__(self, n_features: int, n_knots: int = 8, init: str = "silu"):
        super().__init__()
        self.n_features = n_features
        self.n_knots = n_knots

        # Learnable spline coefficients per feature
        self.coeffs = nn.Parameter(torch.zeros(n_features, n_knots))
        self.knot_positions = nn.Parameter(
            torch.linspace(-3, 3, n_knots).unsqueeze(0).expand(n_features, -1),
            requires_grad=False,
        )

        # Initialize close to SiLU
        if init == "silu":
            with torch.no_grad():
                x = self.knot_positions[0]
                silu_vals = x * torch.sigmoid(x)
                self.coeffs.data = silu_vals.unsqueeze(0).expand(n_features, -1).clone()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, n_features)
        Returns:
            (B, n_features)
        """
        # Soft assignment to nearest knots using RBF
        # x: (B, F), knots: (F, K) -> dist: (B, F, K)
        x_expanded = x.unsqueeze(-1)  # (B, F, 1)
        knots = self.knot_positions.unsqueeze(0)  # (1, F, K)

        # Gaussian basis
        sigma = (self.knot_positions[0, 1] - self.knot_positions[0, 0]).item() * 0.5
        weights = torch.exp(-0.5 * ((x_expanded - knots) / max(sigma, 0.1)) ** 2)  # (B, F, K)

        # Weighted sum of spline coefficients
        coeffs = self.coeffs.unsqueeze(0)  # (1, F, K)
        result = (weights * coeffs).sum(dim=-1)  # (B, F)

        # Residual connection (stabilizes training)
        return result + x


# ── DA V2 Pointwise Model ───────────────────────────────────────────

class DAV2Pointwise(nn.Module):
    """
    Depth Anything V2 adapted for pointwise depth prediction.

    Instead of full-image dense prediction, we:
    1. Feed a small patch (64x64 or 128x128) centered on an ICESat-2 point
    2. Predict dense depth across the patch
    3. Loss only at the center pixel (where we have ICESat-2 ground truth)

    This forces the model to use spatial context (water color gradients,
    shoreline proximity) while training only on accurate sparse labels.
    """

    def __init__(self, max_depth: float = 50.0, model_size: str = "Small"):
        super().__init__()
        self.max_depth = max_depth

        try:
            from transformers import AutoModelForDepthEstimation
            model_id = f"depth-anything/Depth-Anything-V2-{model_size}-hf"
            self.base_model = AutoModelForDepthEstimation.from_pretrained(
                model_id, torch_dtype=torch.float32,
            )
            log.info(f"Loaded DA V2 {model_size} for pointwise training")
        except Exception as e:
            log.error(f"Failed to load DA V2: {e}")
            raise

        # Metric depth adapter
        self.metric_head = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, 1, 1),
            nn.Softplus(),
        )
        self.scale = nn.Parameter(torch.tensor(10.0))
        self.shift = nn.Parameter(torch.tensor(0.0))

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        """Returns (B, 1, H, W) metric depth prediction."""
        outputs = self.base_model(pixel_values=pixel_values)

        if hasattr(outputs, "predicted_depth"):
            rel_depth = outputs.predicted_depth
        else:
            rel_depth = outputs[0]

        if rel_depth.dim() == 3:
            rel_depth = rel_depth.unsqueeze(1)

        metric = self.scale * rel_depth + self.shift
        metric = self.metric_head(metric)
        return metric.clamp(0, self.max_depth)


# ── Losses ───────────────────────────────────────────────────────────

class BathymetryLoss(nn.Module):
    """
    Combined loss for bathymetry prediction.

    Components:
    1. MSE: standard pixel-wise loss
    2. Huber: robust to outliers (turbid water, mismatched points)
    3. Log-depth: emphasizes relative accuracy across depth ranges
    4. Physics-informed Beer-Lambert penalty (optional)
    """

    def __init__(
        self,
        huber_delta: float = 2.0,
        log_weight: float = 0.3,
        physics_weight: float = 0.0,  # set >0 if S2 reflectance available
    ):
        super().__init__()
        self.huber = nn.HuberLoss(delta=huber_delta, reduction="mean")
        self.log_weight = log_weight
        self.physics_weight = physics_weight

    def forward(
        self,
        pred: torch.Tensor,
        target: torch.Tensor,
        reflectance: Optional[torch.Tensor] = None,
    ) -> tuple[torch.Tensor, dict]:
        """
        Args:
            pred: (B, 1) predicted depth
            target: (B, 1) ICESat-2 depth
            reflectance: (B, N_bands) optional, for physics loss

        Returns:
            total loss, dict of component losses
        """
        # Huber loss (robust MSE)
        loss_huber = self.huber(pred, target)

        # Log-depth loss (emphasizes relative accuracy)
        eps = 0.1
        loss_log = F.mse_loss(
            torch.log(pred + eps),
            torch.log(target + eps),
        )

        total = loss_huber + self.log_weight * loss_log

        metrics = {
            "huber": loss_huber.item(),
            "log_depth": loss_log.item(),
        }

        # Physics-informed loss: Beer-Lambert law
        # depth ~ -ln(reflectance) / K_d  =>  reflectance ~ exp(-K_d * depth)
        # Penalize if predicted depth violates this relationship
        if self.physics_weight > 0 and reflectance is not None:
            # Use blue band (index 0) — deepest penetration
            R_blue = reflectance[:, 0:1].clamp(1e-4)
            # Expected: ln(R) should be linearly related to depth
            ln_R = torch.log(R_blue)
            # Soft penalty: correlation between -ln(R) and depth should be positive
            neg_ln_R = -ln_R
            # Standardize
            z_pred = (pred - pred.mean()) / (pred.std() + 1e-6)
            z_lnR = (neg_ln_R - neg_ln_R.mean()) / (neg_ln_R.std() + 1e-6)
            # Negative correlation penalty
            corr = (z_pred * z_lnR).mean()
            physics_loss = F.relu(-corr)  # Penalize if anti-correlated
            total = total + self.physics_weight * physics_loss
            metrics["physics"] = physics_loss.item()

        metrics["total"] = total.item()
        return total, metrics


# ── Training Loop ────────────────────────────────────────────────────

def train_kan(args: argparse.Namespace) -> None:
    """Train KAN model on ICESat-2 spectral features."""
    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
        log.warning("CUDA not available, using CPU")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Dataset
    dataset = ICESat2FusionDataset(
        icesat2_dir=Path(args.icesat2_dir),
        s2_dir=Path(args.s2_dir),
        mode="kan",
        max_depth=args.max_depth,
        augment=True,
        neighbor_radius=args.neighbor_radius,
    )

    if len(dataset) == 0:
        log.error("No training samples found. Ensure ICESat-2 points overlap S2 composites.")
        return

    # Determine input feature size from a sample
    sample = dataset[0]
    n_features = sample["features"].shape[0]
    log.info(f"Input feature dimension: {n_features}")

    # Train/val split (80/20)
    n_val = max(1, len(dataset) // 5)
    n_train = len(dataset) - n_val
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        num_workers=args.workers, pin_memory=True, drop_last=True,
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True,
    )

    log.info(f"Train: {n_train} samples, Val: {n_val} samples")

    # Model
    model = KANBathymetry(
        in_features=n_features,
        hidden_dims=args.hidden_dims,
        max_depth=args.max_depth,
        grid_size=args.grid_size,
        spline_order=args.spline_order,
        use_kan=not args.no_kan,
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters())
    log.info(f"KAN model parameters: {n_params:,}")

    # Optimizer
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=20, T_mult=2,
    )
    criterion = BathymetryLoss(
        huber_delta=2.0,
        log_weight=0.3,
        physics_weight=args.physics_weight,
    )

    scaler = GradScaler("cuda", enabled=(device == "cuda"))
    best_val_rmse = float("inf")
    history = []

    # Resume
    ckpt_path = output_dir / "checkpoint_kan.pt"
    start_epoch = 0
    if ckpt_path.exists() and args.resume:
        ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model_state"])
        start_epoch = ckpt["epoch"] + 1
        best_val_rmse = ckpt.get("best_val_rmse", float("inf"))
        history = ckpt.get("history", [])
        log.info(f"Resumed from epoch {start_epoch}, best RMSE: {best_val_rmse:.4f}m")

    for epoch in range(start_epoch, args.epochs):
        t0 = time.time()

        # Train
        model.train()
        train_losses = []
        train_preds, train_targets = [], []

        for batch in train_loader:
            features = batch["features"].to(device)
            depth = batch["depth"].to(device)

            # Guard against NaN in input features
            features = torch.nan_to_num(features, nan=0.0, posinf=5.0, neginf=-5.0)
            if torch.isnan(depth).any():
                continue

            with autocast(device_type="cuda", enabled=(device == "cuda")):
                pred = model(features)
                loss, loss_dict = criterion(pred, depth)

            if torch.isnan(loss) or torch.isinf(loss):
                continue

            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()

            train_losses.append(loss_dict)
            train_preds.append(pred.detach())
            train_targets.append(depth.detach())

        scheduler.step()

        # Compute train metrics
        train_preds = torch.cat(train_preds)
        train_targets = torch.cat(train_targets)
        train_rmse = ((train_preds - train_targets) ** 2).mean().sqrt().item()
        train_mae = (train_preds - train_targets).abs().mean().item()

        # Validate
        model.eval()
        val_preds, val_targets = [], []

        with torch.no_grad():
            for batch in val_loader:
                features = batch["features"].to(device)
                depth = batch["depth"].to(device)

                with autocast(device_type="cuda", enabled=(device == "cuda")):
                    pred = model(features)

                val_preds.append(pred)
                val_targets.append(depth)

        val_preds = torch.cat(val_preds)
        val_targets = torch.cat(val_targets)
        val_rmse = ((val_preds - val_targets) ** 2).mean().sqrt().item()
        val_mae = (val_preds - val_targets).abs().mean().item()
        val_r2 = 1 - ((val_preds - val_targets) ** 2).sum() / ((val_targets - val_targets.mean()) ** 2).sum()
        val_r2 = val_r2.item()

        # Depth-binned RMSE
        depth_bins = _compute_depth_bin_rmse(val_preds, val_targets)

        elapsed = time.time() - t0
        lr_current = optimizer.param_groups[0]["lr"]

        record = {
            "epoch": epoch,
            "lr": lr_current,
            "train_rmse": train_rmse,
            "train_mae": train_mae,
            "val_rmse": val_rmse,
            "val_mae": val_mae,
            "val_r2": val_r2,
            "depth_bins": depth_bins,
            "elapsed_s": elapsed,
        }
        history.append(record)

        log.info(
            f"Epoch {epoch + 1}/{args.epochs} ({elapsed:.0f}s, lr={lr_current:.1e}) — "
            f"Train RMSE: {train_rmse:.3f}m, Val RMSE: {val_rmse:.3f}m, "
            f"Val MAE: {val_mae:.3f}m, Val R²: {val_r2:.3f}"
        )
        if depth_bins:
            bins_str = ", ".join(f"{k}: {v:.2f}m" for k, v in depth_bins.items())
            log.info(f"  Depth-bin RMSE: {bins_str}")

        # Checkpoint
        torch.save({
            "epoch": epoch,
            "model_state": model.state_dict(),
            "best_val_rmse": best_val_rmse,
            "history": history,
            "n_features": n_features,
            "hidden_dims": args.hidden_dims,
        }, ckpt_path)

        if val_rmse < best_val_rmse:
            best_val_rmse = val_rmse
            torch.save({
                "model_state": model.state_dict(),
                "n_features": n_features,
                "hidden_dims": args.hidden_dims,
                "max_depth": args.max_depth,
                "val_rmse": val_rmse,
                "val_mae": val_mae,
                "val_r2": val_r2,
            }, output_dir / "best_kan_model.pt")
            log.info(f"  -> New best! Val RMSE: {best_val_rmse:.4f}m")

    # Final summary
    log.info("=" * 60)
    log.info(f"Training complete! Best Val RMSE: {best_val_rmse:.4f}m")
    log.info("=" * 60)

    with open(output_dir / "kan_history.json", "w") as f:
        json.dump(history, f, indent=2)

    metrics = {
        "model": "KAN",
        "best_val_rmse": best_val_rmse,
        "n_samples": len(dataset),
        "n_features": n_features,
        "hidden_dims": args.hidden_dims,
        "epochs": args.epochs,
        "approach": "ICESat-2 + Sentinel-2 fusion (Tibetan Plateau inspired)",
    }
    with open(output_dir / "kan_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)


def train_dav2_pointwise(args: argparse.Namespace) -> None:
    """Train DA V2 with pointwise loss at ICESat-2 locations."""
    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
        log.warning("CUDA not available, using CPU")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Dataset
    dataset = ICESat2FusionDataset(
        icesat2_dir=Path(args.icesat2_dir),
        s2_dir=Path(args.s2_dir),
        patch_size=args.patch_size,
        mode="dav2",
        max_depth=args.max_depth,
        augment=True,
    )

    if len(dataset) == 0:
        log.error("No training samples found.")
        return

    n_val = max(1, len(dataset) // 5)
    n_train = len(dataset) - n_val
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        num_workers=args.workers, pin_memory=True, drop_last=True,
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True,
    )

    log.info(f"Train: {n_train}, Val: {n_val}")

    # Model: two-stage (frozen then full fine-tune)
    model = DAV2Pointwise(
        max_depth=args.max_depth,
        model_size=args.model_size,
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters())
    log.info(f"DA V2 Pointwise parameters: {n_params:,}")

    # Stage 1: Freeze backbone
    for param in model.base_model.parameters():
        param.requires_grad = False

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log.info(f"Stage 1 trainable params (head only): {trainable:,}")

    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=args.lr, weight_decay=args.weight_decay,
    )
    criterion = BathymetryLoss(huber_delta=2.0, log_weight=0.3)
    scaler = GradScaler("cuda", enabled=(device == "cuda"))

    best_val_rmse = float("inf")
    history = []
    total_epochs = args.frozen_epochs + args.finetune_epochs

    for epoch in range(total_epochs):
        t0 = time.time()

        # Unfreeze backbone at transition
        if epoch == args.frozen_epochs:
            log.info("Stage 2: Unfreezing backbone with lower LR")
            for param in model.base_model.parameters():
                param.requires_grad = True

            optimizer = torch.optim.AdamW([
                {"params": model.base_model.parameters(), "lr": args.lr * 0.1},
                {"params": list(model.metric_head.parameters()) + [model.scale, model.shift],
                 "lr": args.lr},
            ], weight_decay=args.weight_decay)

        # Train
        model.train()
        train_rmse_sum = 0
        n_batches = 0

        for batch in train_loader:
            pixel_values = batch["pixel_values"].to(device)
            depth_target = batch["depth"].to(device)  # (B, 1)
            cy = batch["center_y"]  # (B,)
            cx = batch["center_x"]  # (B,)

            with autocast(device_type="cuda", enabled=(device == "cuda")):
                pred_map = model(pixel_values)  # (B, 1, H, W)

                # Resize if needed
                ps = pixel_values.shape[2]
                if pred_map.shape[2] != ps:
                    pred_map = F.interpolate(
                        pred_map, size=(ps, ps),
                        mode="bilinear", align_corners=False,
                    )

                # Extract center pixel prediction
                # pred_map: (B, 1, H, W), cy/cx: (B,)
                B = pred_map.shape[0]
                center_pred = pred_map[
                    torch.arange(B), 0, cy.long(), cx.long()
                ].unsqueeze(1)  # (B, 1)

                loss, _ = criterion(center_pred, depth_target)

            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()

            train_rmse_sum += ((center_pred.detach() - depth_target) ** 2).mean().sqrt().item()
            n_batches += 1

        # Validate
        model.eval()
        val_preds, val_targets = [], []

        with torch.no_grad():
            for batch in val_loader:
                pixel_values = batch["pixel_values"].to(device)
                depth_target = batch["depth"].to(device)
                cy = batch["center_y"]
                cx = batch["center_x"]

                pred_map = model(pixel_values)
                ps = pixel_values.shape[2]
                if pred_map.shape[2] != ps:
                    pred_map = F.interpolate(
                        pred_map, size=(ps, ps),
                        mode="bilinear", align_corners=False,
                    )

                B = pred_map.shape[0]
                center_pred = pred_map[
                    torch.arange(B), 0, cy.long(), cx.long()
                ].unsqueeze(1)

                val_preds.append(center_pred)
                val_targets.append(depth_target)

        val_preds = torch.cat(val_preds)
        val_targets = torch.cat(val_targets)
        val_rmse = ((val_preds - val_targets) ** 2).mean().sqrt().item()
        val_mae = (val_preds - val_targets).abs().mean().item()

        elapsed = time.time() - t0
        stage = "Frozen" if epoch < args.frozen_epochs else "Full"
        train_rmse = train_rmse_sum / max(n_batches, 1)

        log.info(
            f"[{stage}] Epoch {epoch + 1}/{total_epochs} ({elapsed:.0f}s) — "
            f"Train RMSE: {train_rmse:.3f}m, Val RMSE: {val_rmse:.3f}m, "
            f"Val MAE: {val_mae:.3f}m"
        )

        history.append({
            "epoch": epoch,
            "stage": stage,
            "train_rmse": train_rmse,
            "val_rmse": val_rmse,
            "val_mae": val_mae,
            "elapsed_s": elapsed,
        })

        if val_rmse < best_val_rmse:
            best_val_rmse = val_rmse
            torch.save(model.state_dict(), output_dir / "best_dav2_pointwise.pt")
            log.info(f"  -> New best! Val RMSE: {best_val_rmse:.4f}m")

        # Checkpoint
        torch.save({
            "epoch": epoch,
            "model_state": model.state_dict(),
            "best_val_rmse": best_val_rmse,
            "history": history,
        }, output_dir / "checkpoint_dav2.pt")

    log.info(f"Training complete! Best Val RMSE: {best_val_rmse:.4f}m")

    with open(output_dir / "dav2_history.json", "w") as f:
        json.dump(history, f, indent=2)


# ── Utilities ────────────────────────────────────────────────────────

def _compute_depth_bin_rmse(
    preds: torch.Tensor,
    targets: torch.Tensor,
) -> dict[str, float]:
    """Compute RMSE in depth bins (0-2m, 2-5m, 5-10m, 10-20m, 20+m)."""
    bins = [
        ("0-2m", 0, 2),
        ("2-5m", 2, 5),
        ("5-10m", 5, 10),
        ("10-20m", 10, 20),
        ("20m+", 20, 100),
    ]
    result = {}
    for name, lo, hi in bins:
        mask = (targets >= lo) & (targets < hi)
        if mask.sum() > 0:
            rmse = ((preds[mask] - targets[mask]) ** 2).mean().sqrt().item()
            result[name] = rmse
    return result


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ICESat-2 + Sentinel-2 Fusion Bathymetry Training",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Data
    parser.add_argument("--icesat2-dir", type=str, required=True,
                        help="Directory with ICESat-2 depth parquet files")
    parser.add_argument("--s2-dir", type=str, required=True,
                        help="Directory with S2 composite GeoTIFFs")
    parser.add_argument("--output", type=str, default="/data/models/icesat2_fusion",
                        help="Output directory")

    # Model selection
    parser.add_argument("--model", type=str, default="kan",
                        choices=["kan", "dav2"],
                        help="Model architecture: KAN or DAV2 pointwise")

    # KAN-specific
    parser.add_argument("--hidden-dims", type=int, nargs="+", default=[128, 64, 32],
                        help="KAN hidden layer dimensions")
    parser.add_argument("--grid-size", type=int, default=5,
                        help="KAN B-spline grid size")
    parser.add_argument("--spline-order", type=int, default=3,
                        help="KAN B-spline order")
    parser.add_argument("--no-kan", action="store_true",
                        help="Use SplineMLP fallback instead of efficient-kan")
    parser.add_argument("--neighbor-radius", type=int, default=3,
                        help="Pixel radius for neighborhood features")
    parser.add_argument("--physics-weight", type=float, default=0.1,
                        help="Weight for Beer-Lambert physics loss")

    # DAV2-specific
    parser.add_argument("--model-size", type=str, default="Small",
                        choices=["Small", "Base", "Large"],
                        help="DA V2 model size")
    parser.add_argument("--patch-size", type=int, default=64,
                        help="Patch size for DAV2 pointwise training")
    parser.add_argument("--frozen-epochs", type=int, default=10,
                        help="DAV2 epochs with frozen backbone")
    parser.add_argument("--finetune-epochs", type=int, default=40,
                        help="DAV2 epochs with full fine-tuning")

    # Training
    parser.add_argument("--epochs", type=int, default=200,
                        help="Training epochs (KAN only)")
    parser.add_argument("--batch-size", type=int, default=256,
                        help="Batch size (256 for KAN, 16 for DAV2)")
    parser.add_argument("--lr", type=float, default=1e-3,
                        help="Learning rate")
    parser.add_argument("--weight-decay", type=float, default=1e-4,
                        help="Weight decay")
    parser.add_argument("--max-depth", type=float, default=50.0,
                        help="Maximum depth in metres")
    parser.add_argument("--workers", type=int, default=4,
                        help="DataLoader workers")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")

    args = parser.parse_args()

    log.info("=" * 60)
    log.info("OpenCatch — ICESat-2 + Sentinel-2 Fusion Bathymetry")
    log.info(f"Model: {args.model.upper()}")
    log.info(f"ICESat-2 data: {args.icesat2_dir}")
    log.info(f"S2 composites: {args.s2_dir}")
    log.info(f"Output: {args.output}")
    log.info("=" * 60)

    if args.model == "kan":
        train_kan(args)
    else:
        # Smaller batch size for DAV2
        if args.batch_size > 32:
            args.batch_size = 16
            log.info(f"Reduced batch size to {args.batch_size} for DAV2")
        train_dav2_pointwise(args)


if __name__ == "__main__":
    main()
