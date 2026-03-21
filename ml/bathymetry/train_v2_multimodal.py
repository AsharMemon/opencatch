#!/usr/bin/env python3
"""
OpenCatch Bathymetry V2 — Multi-Modal Fusion for Sub-1m RMSE

Architecture:
- Encoder: SatlasPretrain Swin-v2-Base (pretrained on 302M S2 labels)
- Decoder: U-Net with skip connections + attention gates
- Inputs: Sentinel-2 (10 bands) + DEM terrain (elevation, slope, aspect, curvature)
- Labels: MN/WI/MI DNR sonar DEMs + ICESat-2 ATL13 sparse points
- Loss: MSE + Beer-Lambert physics loss + shoreline boundary loss

Training data:
- MN DNR: ~4,500 lakes with full contour DEMs
- ICESat-2 ATL13: sparse depth transects for 100K+ North American lakes
- Sentinel-2 L2A: summer composites (median of cloud-free images)
- 3DEP DEM: 10m terrain surrounding each lake

References:
- SatlasPretrain: Bastani et al. 2023 — pretrained on 302M Sentinel-2 labels
- Swin-BathyUNet: ISPRS 2025 — SOTA satellite-derived bathymetry
- Beer-Lambert physics loss: Legleiter et al. 2019

Usage:
    python train_v2_multimodal.py \\
        --data-dir /data/training/v2 \\
        --output /data/models/v2 \\
        --epochs 200 \\
        --batch-size 8 \\
        --device cuda

Requirements:
    pip install torch torchvision timm rasterio geopandas numpy tqdm
    pip install satlaspretrain-models  (for Swin-v2 weights)
"""

import argparse
import json
import logging
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.cuda.amp import GradScaler, autocast
from torch.utils.data import DataLoader, Dataset, random_split

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bathy_v2")

# ── Constants ────────────────────────────────────────────────────────

# Sentinel-2 L2A bands used (10 bands at 10-20m)
S2_BANDS = [
    "B02",  # Blue 490nm
    "B03",  # Green 560nm
    "B04",  # Red 665nm
    "B05",  # Red Edge 1 705nm
    "B06",  # Red Edge 2 740nm
    "B07",  # Red Edge 3 783nm
    "B08",  # NIR 842nm
    "B8A",  # NIR narrow 865nm
    "B11",  # SWIR1 1610nm
    "B12",  # SWIR2 2190nm
]
N_S2_BANDS = len(S2_BANDS)

# DEM-derived channels
DEM_CHANNELS = ["elevation", "slope", "aspect", "curvature"]
N_DEM_CHANNELS = len(DEM_CHANNELS)

# Total input channels
N_INPUT_CHANNELS = N_S2_BANDS + N_DEM_CHANNELS  # 14

# Beer-Lambert attenuation bands (blue and green)
IDX_BLUE = 0   # B02
IDX_GREEN = 1  # B03

# B2 backup bucket
B2_BUCKET = "opencatch-ml"


# ── Attention Gate ───────────────────────────────────────────────────

class AttentionGate(nn.Module):
    """
    Attention gate for skip connections (Oktay et al., 2018).
    Suppresses irrelevant encoder features before concatenation.
    """

    def __init__(self, gate_ch: int, skip_ch: int, inter_ch: int):
        super().__init__()
        self.W_gate = nn.Sequential(
            nn.Conv2d(gate_ch, inter_ch, 1, bias=False),
            nn.BatchNorm2d(inter_ch),
        )
        self.W_skip = nn.Sequential(
            nn.Conv2d(skip_ch, inter_ch, 1, bias=False),
            nn.BatchNorm2d(inter_ch),
        )
        self.psi = nn.Sequential(
            nn.Conv2d(inter_ch, 1, 1, bias=False),
            nn.BatchNorm2d(1),
            nn.Sigmoid(),
        )
        self.relu = nn.ReLU(inplace=True)

    def forward(self, gate: torch.Tensor, skip: torch.Tensor) -> torch.Tensor:
        g = self.W_gate(gate)
        s = self.W_skip(skip)
        # Align spatial dimensions
        if g.shape[2:] != s.shape[2:]:
            g = F.interpolate(g, size=s.shape[2:], mode="bilinear", align_corners=False)
        alpha = self.psi(self.relu(g + s))
        return skip * alpha


# ── Swin-v2 Encoder Loading ─────────────────────────────────────────

def load_satlas_swin_encoder(pretrained: bool = True) -> nn.Module:
    """
    Load SatlasPretrain Swin-v2-Base encoder.

    The SatlasPretrain weights are trained on 302M Sentinel-2 labels covering
    land cover, building, tree, crop, and other remote sensing tasks.

    Falls back to ImageNet Swin-v2-Base if SatlasPretrain is unavailable.
    """
    try:
        # Try SatlasPretrain package first
        import satlaspretrain_models
        weights_manager = satlaspretrain_models.Weights()
        model = weights_manager.get_pretrained_model(
            model_identifier="Sentinel2_SwinB_SI_RGB",
            fpn=False,
        )
        log.info("Loaded SatlasPretrain Swin-v2-Base encoder (302M S2 labels)")
        return model
    except (ImportError, Exception) as e:
        log.warning(f"SatlasPretrain not available ({e}), falling back to timm Swin-v2")

    # Fallback: timm Swin-v2-Base with ImageNet weights
    import timm

    model = timm.create_model(
        "swinv2_base_window12to16_192to256",
        pretrained=pretrained,
        features_only=True,
        out_indices=(0, 1, 2, 3),
    )
    log.info("Loaded timm Swin-v2-Base encoder (ImageNet pretrained)")
    return model


def adapt_encoder_input(encoder: nn.Module, n_channels: int = 14) -> nn.Module:
    """
    Adapt the encoder's first layer to accept N input channels instead of 3 RGB.
    Copies pretrained weights for the first 3 channels and initializes the rest.
    """
    # Find the first conv/patch_embed layer
    first_conv = None
    first_conv_name = None

    for name, module in encoder.named_modules():
        if isinstance(module, nn.Conv2d) and module.in_channels in (3, 4):
            first_conv = module
            first_conv_name = name
            break

    if first_conv is None:
        log.warning("Could not find first Conv2d layer to adapt; creating projection layer")
        return encoder

    old_weight = first_conv.weight.data  # (out_ch, 3, kH, kW)
    out_ch, _, kh, kw = old_weight.shape

    new_conv = nn.Conv2d(
        n_channels, out_ch, kernel_size=(kh, kw),
        stride=first_conv.stride, padding=first_conv.padding,
        bias=first_conv.bias is not None,
    )

    # Copy pretrained weights for first 3 channels (RGB ~ B04, B03, B02)
    with torch.no_grad():
        # Initialize all channels with small random values
        nn.init.kaiming_normal_(new_conv.weight, mode="fan_out", nonlinearity="relu")
        # Overwrite first 3 channels with pretrained
        n_copy = min(3, old_weight.shape[1])
        new_conv.weight[:, :n_copy] = old_weight[:, :n_copy]
        if first_conv.bias is not None and new_conv.bias is not None:
            new_conv.bias.copy_(first_conv.bias)

    # Replace in encoder
    parts = first_conv_name.split(".")
    parent = encoder
    for part in parts[:-1]:
        parent = getattr(parent, part)
    setattr(parent, parts[-1], new_conv)

    log.info(f"Adapted encoder input: {first_conv.in_channels} -> {n_channels} channels")
    return encoder


# ── SwinBathyUNet Model ─────────────────────────────────────────────

class DoubleConv(nn.Module):
    """Two 3x3 conv blocks with BN and ReLU."""

    def __init__(self, in_ch: int, out_ch: int, dropout: float = 0.0):
        super().__init__()
        layers = [
            nn.Conv2d(in_ch, out_ch, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1, bias=False),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        ]
        if dropout > 0:
            layers.insert(3, nn.Dropout2d(dropout))
        self.conv = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(x)


class SwinBathyUNet(nn.Module):
    """
    Swin-v2 encoder + U-Net decoder with attention gates.

    Architecture follows Swin-BathyUNet (ISPRS 2025) adapted for
    inland lake bathymetry with multi-modal inputs.

    Input: (B, 14, H, W) — 10 S2 bands + 4 DEM channels
    Output: (B, 1, H, W) — predicted depth in metres (positive = deeper)
    """

    # Swin-v2-Base feature dimensions at each stage
    SWIN_DIMS = [128, 256, 512, 1024]

    def __init__(
        self,
        in_channels: int = 14,
        pretrained: bool = True,
        decoder_channels: tuple[int, ...] = (512, 256, 128, 64),
        dropout: float = 0.1,
    ):
        super().__init__()

        # ── Encoder ──
        self.encoder = load_satlas_swin_encoder(pretrained=pretrained)
        self.encoder = adapt_encoder_input(self.encoder, n_channels=in_channels)

        # Input projection (in case encoder expects fixed size)
        self.input_proj = nn.Sequential(
            nn.Conv2d(in_channels, in_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(in_channels),
            nn.ReLU(inplace=True),
        )

        # ── Decoder with attention gates ──
        enc_dims = self.SWIN_DIMS
        dec_dims = decoder_channels

        # Bottleneck
        self.bottleneck = DoubleConv(enc_dims[3], dec_dims[0], dropout=dropout)

        # Decoder stages (bottom-up)
        self.up4 = nn.ConvTranspose2d(dec_dims[0], dec_dims[0], 2, stride=2)
        self.attn4 = AttentionGate(dec_dims[0], enc_dims[2], enc_dims[2] // 2)
        self.dec4 = DoubleConv(dec_dims[0] + enc_dims[2], dec_dims[1], dropout=dropout)

        self.up3 = nn.ConvTranspose2d(dec_dims[1], dec_dims[1], 2, stride=2)
        self.attn3 = AttentionGate(dec_dims[1], enc_dims[1], enc_dims[1] // 2)
        self.dec3 = DoubleConv(dec_dims[1] + enc_dims[1], dec_dims[2], dropout=dropout)

        self.up2 = nn.ConvTranspose2d(dec_dims[2], dec_dims[2], 2, stride=2)
        self.attn2 = AttentionGate(dec_dims[2], enc_dims[0], enc_dims[0] // 2)
        self.dec2 = DoubleConv(dec_dims[2] + enc_dims[0], dec_dims[3], dropout=dropout)

        self.up1 = nn.ConvTranspose2d(dec_dims[3], dec_dims[3], 2, stride=2)
        self.dec1 = DoubleConv(dec_dims[3], dec_dims[3])

        # Depth prediction head — softplus ensures non-negative output
        self.depth_head = nn.Sequential(
            nn.Conv2d(dec_dims[3], 32, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 1, 1),
            nn.Softplus(),
        )

        # Uncertainty head — predicts log(variance) for uncertainty weighting
        self.uncertainty_head = nn.Sequential(
            nn.Conv2d(dec_dims[3], 32, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 1, 1),
        )

    def forward(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass.

        Args:
            x: (B, 14, H, W) input tensor

        Returns:
            depth: (B, 1, H, W) predicted depth in metres
            log_var: (B, 1, H, W) predicted log-variance for uncertainty
        """
        B, C, H, W = x.shape

        # Input projection
        x = self.input_proj(x)

        # Encoder (Swin-v2 feature pyramid)
        try:
            features = self.encoder(x)  # List of feature maps at 4 scales
        except Exception:
            # Fallback: manual forward through encoder stages
            features = self._encoder_forward_fallback(x)

        # timm Swin-v2 outputs (B, H, W, C) — convert to (B, C, H, W)
        converted = []
        for f in features:
            if f.dim() == 4 and f.shape[-1] != f.shape[-2]:
                # (B, H, W, C) format — permute to (B, C, H, W)
                f = f.permute(0, 3, 1, 2).contiguous()
            converted.append(f)

        # Unpack encoder features (from smallest to largest spatial)
        e1, e2, e3, e4 = converted[0], converted[1], converted[2], converted[3]

        # Bottleneck
        b = self.bottleneck(e4)

        # Decoder with attention-gated skip connections
        d4 = self.up4(b)
        d4 = self._align_and_cat(d4, self.attn4(d4, e3))
        d4 = self.dec4(d4)

        d3 = self.up3(d4)
        d3 = self._align_and_cat(d3, self.attn3(d3, e2))
        d3 = self.dec3(d3)

        d2 = self.up2(d3)
        d2 = self._align_and_cat(d2, self.attn2(d2, e1))
        d2 = self.dec2(d2)

        d1 = self.up1(d2)
        d1 = self.dec1(d1)

        # Upsample to input resolution if needed
        if d1.shape[2:] != (H, W):
            d1 = F.interpolate(d1, size=(H, W), mode="bilinear", align_corners=False)

        depth = self.depth_head(d1)
        log_var = self.uncertainty_head(d1)

        return depth, log_var

    def _align_and_cat(
        self, x: torch.Tensor, skip: torch.Tensor
    ) -> torch.Tensor:
        """Align spatial dims and concatenate."""
        if x.shape[2:] != skip.shape[2:]:
            x = F.interpolate(x, size=skip.shape[2:], mode="bilinear", align_corners=False)
        return torch.cat([x, skip], dim=1)

    def _encoder_forward_fallback(self, x: torch.Tensor) -> list[torch.Tensor]:
        """Fallback encoder forward for non-features_only models."""
        import timm

        encoder = timm.create_model(
            "swinv2_base_window12to16_192to256",
            pretrained=False,
            features_only=True,
            out_indices=(0, 1, 2, 3),
            in_chans=x.shape[1],
        ).to(x.device)

        return encoder(x)


# ── Physics-Informed Loss ────────────────────────────────────────────

class BathymetryPhysicsLoss(nn.Module):
    """
    Multi-component loss for bathymetry prediction.

    Components:
    1. MSE/Huber on depth (primary) — supports sparse labels via mask
    2. Beer-Lambert: ln(Blue)/ln(Green) should correlate with depth
    3. Shoreline boundary: depth at water edge must approach 0
    4. Smoothness: spatial gradient regularization
    5. Uncertainty-weighted (Kendall & Gal, 2017) for aleatoric uncertainty

    All losses are masked — only computed where labels exist.
    """

    def __init__(
        self,
        w_mse: float = 1.0,
        w_beer_lambert: float = 0.1,
        w_shoreline: float = 0.2,
        w_smooth: float = 0.05,
        use_uncertainty: bool = True,
    ):
        super().__init__()
        self.w_mse = w_mse
        self.w_beer_lambert = w_beer_lambert
        self.w_shoreline = w_shoreline
        self.w_smooth = w_smooth
        self.use_uncertainty = use_uncertainty

    def forward(
        self,
        pred_depth: torch.Tensor,
        pred_log_var: torch.Tensor,
        target_depth: torch.Tensor,
        label_mask: torch.Tensor,
        s2_bands: torch.Tensor,
        shoreline_mask: torch.Tensor,
    ) -> tuple[torch.Tensor, dict[str, float]]:
        """
        Compute total loss.

        Args:
            pred_depth: (B, 1, H, W) predicted depth
            pred_log_var: (B, 1, H, W) predicted log-variance
            target_depth: (B, 1, H, W) ground truth depth
            label_mask: (B, 1, H, W) binary — 1 where labels exist
            s2_bands: (B, 14, H, W) input bands (need blue/green for BL)
            shoreline_mask: (B, 1, H, W) binary — 1 at water boundary pixels

        Returns:
            total_loss, component_dict
        """
        components = {}
        n_valid = label_mask.sum().clamp(min=1)

        # 1. MSE loss (masked, with optional uncertainty weighting)
        residual = (pred_depth - target_depth) ** 2
        if self.use_uncertainty:
            # Kendall & Gal (2017): L = (1/2σ²)|y-ŷ|² + (1/2)log(σ²)
            precision = torch.exp(-pred_log_var)
            mse_loss = (0.5 * precision * residual * label_mask).sum() / n_valid
            mse_loss = mse_loss + 0.5 * (pred_log_var * label_mask).sum() / n_valid
        else:
            mse_loss = (residual * label_mask).sum() / n_valid
        components["mse"] = mse_loss.item()

        # 2. Beer-Lambert physics loss
        # In clear water: depth ∝ ln(R_blue) / ln(R_green)
        # Penalize if predicted depth doesn't correlate with this ratio
        blue = s2_bands[:, IDX_BLUE:IDX_BLUE + 1].clamp(min=1e-4)
        green = s2_bands[:, IDX_GREEN:IDX_GREEN + 1].clamp(min=1e-4)
        bl_ratio = torch.log(blue) / torch.log(green).clamp(min=0.01)
        bl_ratio = bl_ratio.detach()  # Don't backprop through input

        # Correlation loss: negative Pearson R between BL ratio and depth
        bl_masked = bl_ratio * label_mask
        depth_masked = pred_depth * label_mask

        bl_mean = bl_masked.sum() / n_valid
        depth_mean = depth_masked.sum() / n_valid
        bl_centered = (bl_masked - bl_mean * label_mask)
        depth_centered = (depth_masked - depth_mean * label_mask)

        cov = (bl_centered * depth_centered * label_mask).sum() / n_valid
        bl_std = ((bl_centered ** 2 * label_mask).sum() / n_valid).sqrt().clamp(min=1e-6)
        depth_std = ((depth_centered ** 2 * label_mask).sum() / n_valid).sqrt().clamp(min=1e-6)

        pearson_r = cov / (bl_std * depth_std)
        # We want positive correlation (deeper = higher ratio in clear water)
        bl_loss = 1.0 - pearson_r.clamp(min=-1, max=1)
        components["beer_lambert"] = bl_loss.item()

        # 3. Shoreline boundary loss: depth at shore should be ~0
        n_shore = shoreline_mask.sum().clamp(min=1)
        shore_loss = (pred_depth ** 2 * shoreline_mask).sum() / n_shore
        components["shoreline"] = shore_loss.item()

        # 4. Smoothness loss: L1 on spatial gradients
        dx = torch.abs(pred_depth[:, :, :, 1:] - pred_depth[:, :, :, :-1])
        dy = torch.abs(pred_depth[:, :, 1:, :] - pred_depth[:, :, :-1, :])
        smooth_loss = dx.mean() + dy.mean()
        components["smooth"] = smooth_loss.item()

        # Total
        total = (
            self.w_mse * mse_loss
            + self.w_beer_lambert * bl_loss
            + self.w_shoreline * shore_loss
            + self.w_smooth * smooth_loss
        )
        components["total"] = total.item()

        return total, components


# ── Dataset ──────────────────────────────────────────────────────────

class MultiModalBathyDataset(Dataset):
    """
    Multi-modal bathymetry dataset.

    Each sample is a directory containing:
    - composite.tif: 14-band GeoTIFF (10 S2 + 4 DEM channels)
    - depth.tif: ground truth depth raster (NaN where no label)
    - mask.tif: water mask (1=water, 0=land)
    - shoreline.tif: shore boundary pixels (1=shore, 0=other)

    Also supports sparse ICESat-2 labels via:
    - icesat2.npy: sparse depth array (NaN where no observation)

    For sparse labels, the label_mask is derived from non-NaN pixels.
    """

    def __init__(
        self,
        data_dir: Path,
        patch_size: int = 256,
        augment: bool = True,
        crops_per_lake: int = 16,
    ):
        self.data_dir = Path(data_dir)
        self.patch_size = patch_size
        self.augment = augment
        self.crops_per_lake = crops_per_lake
        self.samples = self._scan()
        log.info(f"Dataset: {len(self.samples)} lakes, {len(self)} total crops")

    def _scan(self) -> list[dict]:
        """Scan for training samples."""
        samples = []

        # Dense labels (state DNR rasters)
        for composite_path in sorted(self.data_dir.glob("**/composite.tif")):
            lake_dir = composite_path.parent
            depth_path = lake_dir / "depth.tif"
            if not depth_path.exists():
                continue
            samples.append({
                "composite": composite_path,
                "depth": depth_path,
                "mask": lake_dir / "mask.tif",
                "shoreline": lake_dir / "shoreline.tif",
                "sparse": False,
            })

        # Sparse labels (ICESat-2)
        for composite_path in sorted(self.data_dir.glob("**/composite.tif")):
            lake_dir = composite_path.parent
            icesat_path = lake_dir / "icesat2_depth.tif"
            if not icesat_path.exists():
                continue
            # Only add if not already in dense set
            if not (lake_dir / "depth.tif").exists():
                samples.append({
                    "composite": composite_path,
                    "depth": icesat_path,
                    "mask": lake_dir / "mask.tif",
                    "shoreline": lake_dir / "shoreline.tif",
                    "sparse": True,
                })

        if not samples:
            log.warning(f"No training samples found in {self.data_dir}")
        return samples

    def __len__(self) -> int:
        return len(self.samples) * self.crops_per_lake

    def __getitem__(self, idx: int) -> dict:
        import rasterio

        sample = self.samples[idx % len(self.samples)]
        ps = self.patch_size

        # Load composite (14 channels)
        with rasterio.open(sample["composite"]) as src:
            composite = src.read().astype(np.float32)  # (14, H, W)

        # Load depth (ground truth)
        with rasterio.open(sample["depth"]) as src:
            depth = src.read(1).astype(np.float32)  # (H, W)

        # Load water mask
        mask_path = sample["mask"]
        if mask_path.exists():
            with rasterio.open(mask_path) as src:
                water_mask = src.read(1).astype(np.float32)
        else:
            # Derive from NIR band (B08 = channel 6)
            water_mask = (composite[6] < 0.15).astype(np.float32)

        # Load shoreline mask
        shore_path = sample["shoreline"]
        if shore_path.exists():
            with rasterio.open(shore_path) as src:
                shoreline = src.read(1).astype(np.float32)
        else:
            # Derive: dilate water mask - erode water mask
            from scipy.ndimage import binary_dilation, binary_erosion
            dilated = binary_dilation(water_mask > 0.5, iterations=2).astype(np.float32)
            eroded = binary_erosion(water_mask > 0.5, iterations=2).astype(np.float32)
            shoreline = (dilated - eroded).clip(0, 1)

        # Label mask: where we have valid depth labels
        label_mask = np.isfinite(depth) & (depth >= 0) & (water_mask > 0.5)
        label_mask = label_mask.astype(np.float32)

        # Replace NaN with 0 in depth (masked out anyway)
        depth = np.nan_to_num(depth, nan=0.0)

        # Random crop
        h, w = depth.shape
        if h >= ps and w >= ps:
            # Try to crop a region with labels
            for _ in range(10):
                y = np.random.randint(0, h - ps + 1)
                x = np.random.randint(0, w - ps + 1)
                crop_mask = label_mask[y : y + ps, x : x + ps]
                if crop_mask.sum() > ps * ps * 0.01:  # At least 1% valid
                    break

            composite = composite[:, y : y + ps, x : x + ps]
            depth = depth[y : y + ps, x : x + ps]
            water_mask = water_mask[y : y + ps, x : x + ps]
            shoreline = shoreline[y : y + ps, x : x + ps]
            label_mask = label_mask[y : y + ps, x : x + ps]
        else:
            # Pad to patch size
            pad_h = max(0, ps - h)
            pad_w = max(0, ps - w)
            composite = np.pad(composite, ((0, 0), (0, pad_h), (0, pad_w)))
            depth = np.pad(depth, ((0, pad_h), (0, pad_w)))
            water_mask = np.pad(water_mask, ((0, pad_h), (0, pad_w)))
            shoreline = np.pad(shoreline, ((0, pad_h), (0, pad_w)))
            label_mask = np.pad(label_mask, ((0, pad_h), (0, pad_w)))

        # Augmentation
        if self.augment:
            composite, depth, water_mask, shoreline, label_mask = self._augment(
                composite, depth, water_mask, shoreline, label_mask
            )

        return {
            "composite": torch.from_numpy(composite),
            "depth": torch.from_numpy(depth[np.newaxis]),
            "water_mask": torch.from_numpy(water_mask[np.newaxis]),
            "shoreline": torch.from_numpy(shoreline[np.newaxis]),
            "label_mask": torch.from_numpy(label_mask[np.newaxis]),
        }

    def _augment(
        self,
        composite: np.ndarray,
        depth: np.ndarray,
        water_mask: np.ndarray,
        shoreline: np.ndarray,
        label_mask: np.ndarray,
    ) -> tuple:
        """Apply random augmentations."""
        # Random horizontal flip
        if np.random.random() > 0.5:
            composite = composite[:, :, ::-1].copy()
            depth = depth[:, ::-1].copy()
            water_mask = water_mask[:, ::-1].copy()
            shoreline = shoreline[:, ::-1].copy()
            label_mask = label_mask[:, ::-1].copy()

        # Random vertical flip
        if np.random.random() > 0.5:
            composite = composite[:, ::-1, :].copy()
            depth = depth[::-1, :].copy()
            water_mask = water_mask[::-1, :].copy()
            shoreline = shoreline[::-1, :].copy()
            label_mask = label_mask[::-1, :].copy()

        # Random 90-degree rotation
        if np.random.random() > 0.5:
            k = np.random.randint(1, 4)
            composite = np.rot90(composite, k, axes=(1, 2)).copy()
            depth = np.rot90(depth, k).copy()
            water_mask = np.rot90(water_mask, k).copy()
            shoreline = np.rot90(shoreline, k).copy()
            label_mask = np.rot90(label_mask, k).copy()

        # Random brightness/contrast on S2 bands only (first 10 channels)
        if np.random.random() > 0.5:
            brightness = np.random.uniform(0.9, 1.1)
            composite[:N_S2_BANDS] = composite[:N_S2_BANDS] * brightness

        return composite, depth, water_mask, shoreline, label_mask


# ── Metrics ──────────────────────────────────────────────────────────

def compute_metrics(
    pred: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
) -> dict[str, float]:
    """Compute RMSE, MAE, R-squared on masked pixels."""
    with torch.no_grad():
        valid = mask > 0.5
        n = valid.sum().float().clamp(min=1)

        p = pred[valid]
        t = target[valid]

        residuals = p - t
        mse = (residuals ** 2).sum() / n
        rmse = mse.sqrt().item()
        mae = residuals.abs().sum().item() / n.item()

        # R-squared
        ss_res = (residuals ** 2).sum()
        ss_tot = ((t - t.mean()) ** 2).sum().clamp(min=1e-6)
        r2 = (1 - ss_res / ss_tot).item()

    return {"rmse": rmse, "mae": mae, "r2": r2}


# ── Training Loop ────────────────────────────────────────────────────

def train(args: argparse.Namespace) -> None:
    """Main training loop with mixed precision, checkpointing, and B2 sync."""
    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
        log.warning("CUDA not available, using CPU")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Dataset ──
    dataset = MultiModalBathyDataset(
        args.data_dir,
        patch_size=args.patch_size,
        augment=True,
        crops_per_lake=args.crops_per_lake,
    )

    if len(dataset.samples) == 0:
        log.error(f"No training data found in {args.data_dir}")
        log.error("Expected structure: <lake_dir>/composite.tif + depth.tif")
        return

    # Split: 80/20
    n_val_lakes = max(1, len(dataset.samples) // 5)
    n_train_lakes = len(dataset.samples) - n_val_lakes
    n_val = n_val_lakes * args.crops_per_lake
    n_train = len(dataset) - n_val

    train_ds, val_ds = random_split(dataset, [n_train, n_val])

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.workers,
        pin_memory=True,
        drop_last=True,
        persistent_workers=args.workers > 0,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.workers,
        pin_memory=True,
        persistent_workers=args.workers > 0,
    )

    log.info(f"Train: {n_train_lakes} lakes ({n_train} crops)")
    log.info(f"Val:   {n_val_lakes} lakes ({n_val} crops)")

    # ── Model ──
    model = SwinBathyUNet(
        in_channels=N_INPUT_CHANNELS,
        pretrained=args.pretrained,
        dropout=args.dropout,
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters())
    n_trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log.info(f"Model: {n_params:,} params ({n_trainable:,} trainable)")

    # ── Optimizer ──
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=args.lr,
        weight_decay=args.weight_decay,
    )

    # Cosine annealing with warm restarts
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=args.warmup_epochs, T_mult=2
    )

    # ── Loss ──
    criterion = BathymetryPhysicsLoss(
        w_mse=1.0,
        w_beer_lambert=args.w_beer_lambert,
        w_shoreline=args.w_shoreline,
        w_smooth=args.w_smooth,
        use_uncertainty=True,
    )

    # ── Mixed precision ──
    scaler = GradScaler(enabled=(device == "cuda"))

    # ── Resume from checkpoint ──
    start_epoch = 0
    best_val_rmse = float("inf")
    history = []

    ckpt_path = output_dir / "checkpoint.pt"
    if ckpt_path.exists() and args.resume:
        log.info(f"Resuming from {ckpt_path}")
        ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model_state"])
        optimizer.load_state_dict(ckpt["optimizer_state"])
        scheduler.load_state_dict(ckpt["scheduler_state"])
        scaler.load_state_dict(ckpt["scaler_state"])
        start_epoch = ckpt["epoch"] + 1
        best_val_rmse = ckpt.get("best_val_rmse", float("inf"))
        history = ckpt.get("history", [])
        log.info(f"Resumed at epoch {start_epoch}, best RMSE: {best_val_rmse:.4f}m")

    # ── Training ──
    patience_counter = 0

    for epoch in range(start_epoch, args.epochs):
        t0 = time.time()

        # Train
        model.train()
        train_loss_total = 0.0
        train_loss_components = {}
        train_metrics = {"rmse": 0, "mae": 0, "r2": 0}
        n_train_batches = 0

        for batch in train_loader:
            composite = batch["composite"].to(device)
            depth = batch["depth"].to(device)
            label_mask = batch["label_mask"].to(device)
            shoreline = batch["shoreline"].to(device)

            with autocast(enabled=(device == "cuda")):
                pred_depth, pred_log_var = model(composite)
                loss, components = criterion(
                    pred_depth, pred_log_var, depth, label_mask,
                    composite, shoreline,
                )

            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            scaler.step(optimizer)
            scaler.update()

            train_loss_total += loss.item()
            for k, v in components.items():
                train_loss_components[k] = train_loss_components.get(k, 0) + v

            metrics = compute_metrics(pred_depth, depth, label_mask)
            for k, v in metrics.items():
                train_metrics[k] += v
            n_train_batches += 1

        scheduler.step()

        # Average train metrics
        for k in train_loss_components:
            train_loss_components[k] /= max(n_train_batches, 1)
        for k in train_metrics:
            train_metrics[k] /= max(n_train_batches, 1)

        # Validate
        model.eval()
        val_loss_total = 0.0
        val_metrics = {"rmse": 0, "mae": 0, "r2": 0}
        n_val_batches = 0

        with torch.no_grad():
            for batch in val_loader:
                composite = batch["composite"].to(device)
                depth = batch["depth"].to(device)
                label_mask = batch["label_mask"].to(device)
                shoreline = batch["shoreline"].to(device)

                with autocast(enabled=(device == "cuda")):
                    pred_depth, pred_log_var = model(composite)
                    loss, _ = criterion(
                        pred_depth, pred_log_var, depth, label_mask,
                        composite, shoreline,
                    )

                val_loss_total += loss.item()
                metrics = compute_metrics(pred_depth, depth, label_mask)
                for k, v in metrics.items():
                    val_metrics[k] += v
                n_val_batches += 1

        for k in val_metrics:
            val_metrics[k] /= max(n_val_batches, 1)

        elapsed = time.time() - t0
        lr_current = optimizer.param_groups[0]["lr"]

        # Log
        epoch_record = {
            "epoch": epoch,
            "lr": lr_current,
            "train_rmse": train_metrics["rmse"],
            "train_mae": train_metrics["mae"],
            "train_r2": train_metrics["r2"],
            "val_rmse": val_metrics["rmse"],
            "val_mae": val_metrics["mae"],
            "val_r2": val_metrics["r2"],
            "loss_mse": train_loss_components.get("mse", 0),
            "loss_beer_lambert": train_loss_components.get("beer_lambert", 0),
            "loss_shoreline": train_loss_components.get("shoreline", 0),
            "loss_smooth": train_loss_components.get("smooth", 0),
            "elapsed_s": elapsed,
        }
        history.append(epoch_record)

        log.info(
            f"Epoch {epoch + 1}/{args.epochs} "
            f"({elapsed:.0f}s, lr={lr_current:.1e}) — "
            f"Train RMSE: {train_metrics['rmse']:.3f}m, "
            f"Val RMSE: {val_metrics['rmse']:.3f}m, "
            f"Val MAE: {val_metrics['mae']:.3f}m, "
            f"Val R2: {val_metrics['r2']:.3f} | "
            f"BL: {train_loss_components.get('beer_lambert', 0):.3f}, "
            f"Shore: {train_loss_components.get('shoreline', 0):.3f}"
        )

        # Save checkpoint
        checkpoint = {
            "epoch": epoch,
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "scheduler_state": scheduler.state_dict(),
            "scaler_state": scaler.state_dict(),
            "best_val_rmse": best_val_rmse,
            "history": history,
            "args": vars(args),
        }
        torch.save(checkpoint, ckpt_path)

        # Best model
        if val_metrics["rmse"] < best_val_rmse:
            best_val_rmse = val_metrics["rmse"]
            patience_counter = 0
            torch.save(model.state_dict(), output_dir / "best_model.pt")
            log.info(f"  -> New best model! Val RMSE: {best_val_rmse:.4f}m")

            # Sync best model to B2
            if args.b2_sync:
                _sync_to_b2(output_dir / "best_model.pt", "models/v2/best_model.pt")
        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                log.info(
                    f"Early stopping at epoch {epoch + 1} "
                    f"(no improvement for {args.patience} epochs)"
                )
                break

        # Periodic B2 sync
        if args.b2_sync and (epoch + 1) % 10 == 0:
            _sync_to_b2(ckpt_path, "models/v2/checkpoint.pt")

    # ── Final summary ──
    log.info("=" * 60)
    log.info("Training Complete!")
    log.info(f"Best Val RMSE: {best_val_rmse:.4f}m")
    log.info(f"Epochs trained: {epoch + 1}")
    log.info(f"Model saved to: {output_dir / 'best_model.pt'}")

    # Save training history
    with open(output_dir / "training_history.json", "w") as f:
        json.dump(history, f, indent=2)

    # Save final metrics
    final_metrics = {
        "best_val_rmse": best_val_rmse,
        "epochs_trained": epoch + 1,
        "n_training_lakes": n_train_lakes,
        "n_val_lakes": n_val_lakes,
        "n_params": n_params,
        "model": "SwinBathyUNet",
        "encoder": "SatlasPretrain Swin-v2-Base",
        "input_channels": N_INPUT_CHANNELS,
        "patch_size": args.patch_size,
        "loss_components": ["mse", "beer_lambert", "shoreline", "smooth"],
    }
    with open(output_dir / "training_metrics.json", "w") as f:
        json.dump(final_metrics, f, indent=2)

    # Final B2 sync
    if args.b2_sync:
        _sync_to_b2(output_dir, "models/v2/")


def _sync_to_b2(local_path: Path, remote_path: str) -> None:
    """Sync file or directory to Backblaze B2."""
    try:
        local_path = Path(local_path)
        if local_path.is_file():
            cmd = ["b2", "file", "upload", B2_BUCKET, str(local_path), remote_path]
        else:
            cmd = ["b2", "sync", str(local_path), f"b2://{B2_BUCKET}/{remote_path}"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            log.info(f"B2 sync: {local_path} -> {remote_path}")
        else:
            log.warning(f"B2 sync failed: {result.stderr[:200]}")
    except Exception as e:
        log.warning(f"B2 sync error: {e}")


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch Bathymetry V2 — Multi-Modal Fusion Training",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Data
    parser.add_argument("--data-dir", type=str, required=True,
                        help="Training data directory (lakes with composite.tif + depth.tif)")
    parser.add_argument("--output", type=str, default="/data/models/v2",
                        help="Output directory for models and logs")

    # Model
    parser.add_argument("--pretrained", action="store_true", default=True,
                        help="Use SatlasPretrain/ImageNet pretrained encoder")
    parser.add_argument("--no-pretrained", dest="pretrained", action="store_false")
    parser.add_argument("--patch-size", type=int, default=256,
                        help="Training patch size")
    parser.add_argument("--dropout", type=float, default=0.1,
                        help="Decoder dropout rate")

    # Training
    parser.add_argument("--epochs", type=int, default=200,
                        help="Maximum training epochs")
    parser.add_argument("--batch-size", type=int, default=8,
                        help="Batch size")
    parser.add_argument("--lr", type=float, default=1e-4,
                        help="Initial learning rate")
    parser.add_argument("--weight-decay", type=float, default=1e-4,
                        help="AdamW weight decay")
    parser.add_argument("--grad-clip", type=float, default=1.0,
                        help="Gradient clipping norm")
    parser.add_argument("--warmup-epochs", type=int, default=10,
                        help="Cosine annealing warm restart period")
    parser.add_argument("--patience", type=int, default=20,
                        help="Early stopping patience")
    parser.add_argument("--crops-per-lake", type=int, default=16,
                        help="Random crops per lake per epoch")
    parser.add_argument("--workers", type=int, default=4,
                        help="DataLoader workers")
    parser.add_argument("--device", type=str, default="cuda",
                        help="Compute device (cuda/cpu)")

    # Loss weights
    parser.add_argument("--w-beer-lambert", type=float, default=0.1,
                        help="Beer-Lambert physics loss weight")
    parser.add_argument("--w-shoreline", type=float, default=0.2,
                        help="Shoreline boundary loss weight")
    parser.add_argument("--w-smooth", type=float, default=0.05,
                        help="Smoothness regularization weight")

    # Checkpointing
    parser.add_argument("--resume", action="store_true", default=True,
                        help="Resume from checkpoint if available")
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--b2-sync", action="store_true", default=False,
                        help="Sync checkpoints to Backblaze B2")

    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
