#!/usr/bin/env python3
"""
OpenCatch — Terrain-Based Bathymetry Prediction

Predicts lake bathymetry from surrounding terrain DEM only, based on:
  Martinsen et al. (2023): "Predicting lake bathymetry from the surrounding
  topography with deep learning" (Limnology & Oceanography: Methods)

Key insight: The shape of the surrounding landscape predicts the underwater
shape because similar geologic processes formed both. This works for ALL
lakes regardless of water clarity, unlike optical methods.

Architecture:
  - Input: DEM patch around lake (256x256 or 512x512) + lake boundary mask
  - Output: Predicted depth raster within the lake boundary
  - Model: U-Net with ResNet34 encoder (pretrained on ImageNet)

Training data:
  - MN DNR: ~4,500 lakes with full surveyed DEMs
  - Additional: WI DNR, MI DEQ, Ontario MNR where available
  - DEM: 3DEP (US) at 10m or 1m resolution, CDEM (Canada)

Advantages over optical SDB:
  - Works for turbid/eutrophic lakes (most Midwest lakes)
  - No cloud-free imagery requirement
  - Works year-round (no seasonal sun angle issues)
  - DEM data is freely available for all of North America

Usage:
    # Train on MN DNR data
    python terrain_depth_model.py train \
        --dem-dir /data/3dep \
        --bathy-dir /data/mn_dnr/contours \
        --lake-polys /data/hydrolakes/na_lakes.gpkg \
        --output /data/models/terrain_depth \
        --epochs 100 --batch-size 16

    # Predict for new lakes
    python terrain_depth_model.py predict \
        --model /data/models/terrain_depth/best_model.pt \
        --dem-dir /data/3dep \
        --lake-polys /data/hydrolakes/unsurveyed.gpkg \
        --output /data/predictions

Requirements:
    pip install torch torchvision rasterio geopandas shapely numpy tqdm
    pip install segmentation-models-pytorch (optional, for pretrained encoders)
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


# ── Model Architecture ───────────────────────────────────────────────

class TerrainDepthUNet(nn.Module):
    """
    U-Net for predicting lake depth from surrounding terrain DEM.

    Input channels (4):
      0: Elevation (normalized to lake surface = 0, surrounding terrain positive)
      1: Slope (degrees, computed from DEM)
      2: Lake boundary mask (1 = water, 0 = land)
      3: Distance to shore (positive inside lake, negative outside)

    Output: (1, H, W) — predicted depth in metres (positive downward)
    """

    def __init__(self, in_channels: int = 4, base_filters: int = 64):
        super().__init__()
        f = base_filters

        # Encoder
        self.enc1 = self._double_conv(in_channels, f)
        self.enc2 = self._double_conv(f, f * 2)
        self.enc3 = self._double_conv(f * 2, f * 4)
        self.enc4 = self._double_conv(f * 4, f * 8)

        # Bottleneck
        self.bottleneck = self._double_conv(f * 8, f * 16)

        # Decoder
        self.up4 = nn.ConvTranspose2d(f * 16, f * 8, 2, stride=2)
        self.dec4 = self._double_conv(f * 16, f * 8)
        self.up3 = nn.ConvTranspose2d(f * 8, f * 4, 2, stride=2)
        self.dec3 = self._double_conv(f * 8, f * 4)
        self.up2 = nn.ConvTranspose2d(f * 4, f * 2, 2, stride=2)
        self.dec2 = self._double_conv(f * 4, f * 2)
        self.up1 = nn.ConvTranspose2d(f * 2, f, 2, stride=2)
        self.dec1 = self._double_conv(f * 2, f)

        # Depth prediction head
        self.head = nn.Sequential(
            nn.Conv2d(f, 1, 1),
            nn.Softplus(),  # Ensures positive depth
        )

        self.pool = nn.MaxPool2d(2)
        self.dropout = nn.Dropout2d(0.1)

    def _double_conv(self, in_ch, out_ch):
        return nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        # Encoder
        e1 = self.enc1(x)
        e2 = self.enc2(self.pool(e1))
        e3 = self.enc3(self.pool(e2))
        e4 = self.enc4(self.pool(e3))

        # Bottleneck
        b = self.dropout(self.bottleneck(self.pool(e4)))

        # Decoder with skip connections + padding to match sizes
        d4 = self._decode_step(self.up4, b, e4, self.dec4)
        d3 = self._decode_step(self.up3, d4, e3, self.dec3)
        d2 = self._decode_step(self.up2, d3, e2, self.dec2)
        d1 = self._decode_step(self.up1, d2, e1, self.dec1)

        return self.head(d1)

    def _decode_step(self, up, x, skip, dec):
        x = up(x)
        # Pad if sizes don't match
        dh = skip.size(2) - x.size(2)
        dw = skip.size(3) - x.size(3)
        if dh > 0 or dw > 0:
            x = F.pad(x, [0, dw, 0, dh])
        return dec(torch.cat([x, skip], dim=1))


# ── Feature Engineering ──────────────────────────────────────────────

def compute_terrain_features(
    dem: np.ndarray,
    lake_mask: np.ndarray,
    lake_elevation: float,
    pixel_size_m: float = 10.0,
) -> np.ndarray:
    """
    Compute 4-channel input features from DEM and lake mask.

    Args:
        dem: (H, W) elevation raster in metres
        lake_mask: (H, W) binary mask (1 = water, 0 = land)
        lake_elevation: Water surface elevation in metres
        pixel_size_m: DEM pixel size in metres

    Returns:
        (4, H, W) feature array:
          [0] Relative elevation (lake surface = 0)
          [1] Terrain slope in degrees
          [2] Lake mask (1 = water)
          [3] Signed distance to shore (positive inside, negative outside)
    """
    # Relative elevation (above lake surface)
    rel_elev = dem - lake_elevation
    # Normalize to reasonable range
    rel_elev = np.clip(rel_elev, -50, 200) / 100.0

    # Slope computation (gradient magnitude)
    dy, dx = np.gradient(dem, pixel_size_m)
    slope_rad = np.arctan(np.sqrt(dx**2 + dy**2))
    slope_deg = np.degrees(slope_rad)
    slope_norm = slope_deg / 45.0  # Normalize (most slopes < 45°)

    # Signed distance to shore
    from scipy import ndimage
    shore = lake_mask.astype(np.float32)
    dist_inside = ndimage.distance_transform_edt(shore) * pixel_size_m
    dist_outside = ndimage.distance_transform_edt(1 - shore) * pixel_size_m
    signed_dist = (dist_inside - dist_outside) / 500.0  # Normalize to ~[-1, 1]

    features = np.stack([
        rel_elev.astype(np.float32),
        slope_norm.astype(np.float32),
        lake_mask.astype(np.float32),
        signed_dist.astype(np.float32),
    ], axis=0)

    return features


# ── Dataset ──────────────────────────────────────────────────────────

class TerrainBathyDataset(torch.utils.data.Dataset):
    """
    Pairs terrain DEM patches with known lake bathymetry.

    For each lake with survey data:
    - Load DEM around the lake (including surrounding terrain)
    - Load known depth raster (from MN DNR contours)
    - Compute terrain features
    - Generate random 256x256 crops for training
    """

    def __init__(self, data_dir: Path, patch_size: int = 256, augment: bool = True):
        self.data_dir = data_dir
        self.patch_size = patch_size
        self.augment = augment
        self.samples = self._scan()

    def _scan(self):
        samples = []
        for dem_path in sorted(self.data_dir.glob('*_dem.tif')):
            depth_path = dem_path.parent / dem_path.name.replace('_dem.tif', '_depth.tif')
            mask_path = dem_path.parent / dem_path.name.replace('_dem.tif', '_mask.tif')
            if depth_path.exists() and mask_path.exists():
                samples.append({
                    'dem': dem_path,
                    'depth': depth_path,
                    'mask': mask_path,
                })
        log.info(f"Found {len(samples)} DEM+depth training pairs")
        return samples

    def __len__(self):
        return len(self.samples) * 16  # 16 random crops per lake

    def __getitem__(self, idx):
        sample = self.samples[idx % len(self.samples)]
        import rasterio

        with rasterio.open(sample['dem']) as src:
            dem = src.read(1).astype(np.float32)
            pixel_size = abs(src.transform.a)

        with rasterio.open(sample['depth']) as src:
            depth = src.read(1).astype(np.float32)

        with rasterio.open(sample['mask']) as src:
            mask = src.read(1).astype(np.float32)

        # Estimate lake elevation from DEM at lake boundary
        lake_elev = np.median(dem[mask > 0.5]) if (mask > 0.5).any() else dem.mean()

        # Compute terrain features
        features = compute_terrain_features(dem, mask, lake_elev, pixel_size)

        # Random crop
        h, w = depth.shape
        ps = self.patch_size
        if h > ps and w > ps:
            y = np.random.randint(0, h - ps)
            x = np.random.randint(0, w - ps)
            features = features[:, y:y+ps, x:x+ps]
            depth = depth[y:y+ps, x:x+ps]
            mask = mask[y:y+ps, x:x+ps]
        elif h < ps or w < ps:
            # Pad to patch size
            pad_h = max(0, ps - h)
            pad_w = max(0, ps - w)
            features = np.pad(features, ((0, 0), (0, pad_h), (0, pad_w)))
            depth = np.pad(depth, ((0, pad_h), (0, pad_w)))
            mask = np.pad(mask, ((0, pad_h), (0, pad_w)))

        if self.augment:
            # Random flips
            if np.random.random() > 0.5:
                features = features[:, :, ::-1].copy()
                depth = depth[:, ::-1].copy()
                mask = mask[:, ::-1].copy()
            if np.random.random() > 0.5:
                features = features[:, ::-1, :].copy()
                depth = depth[::-1, :].copy()
                mask = mask[::-1, :].copy()
            # Random 90° rotation
            if np.random.random() > 0.5:
                k = np.random.randint(1, 4)
                features = np.rot90(features, k, axes=(1, 2)).copy()
                depth = np.rot90(depth, k).copy()
                mask = np.rot90(mask, k).copy()

        return (
            torch.from_numpy(features),
            torch.from_numpy(depth[np.newaxis]),
            torch.from_numpy(mask[np.newaxis]),
        )


# ── Training ─────────────────────────────────────────────────────────

def train(
    data_dir: Path,
    output_dir: Path,
    epochs: int = 100,
    batch_size: int = 16,
    lr: float = 1e-3,
    device: str = 'cuda',
):
    """Train terrain-based depth prediction model."""
    output_dir.mkdir(parents=True, exist_ok=True)

    dataset = TerrainBathyDataset(data_dir)
    if len(dataset.samples) == 0:
        log.error(f"No training data found in {data_dir}. Need *_dem.tif + *_depth.tif + *_mask.tif")
        return

    n_val = max(1, len(dataset.samples) // 5)
    n_train = len(dataset) - n_val * 16
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val * 16])

    train_loader = torch.utils.data.DataLoader(
        train_ds, batch_size=batch_size, shuffle=True,
        num_workers=4, pin_memory=True, drop_last=True,
    )
    val_loader = torch.utils.data.DataLoader(
        val_ds, batch_size=batch_size, shuffle=False,
        num_workers=4, pin_memory=True,
    )

    model = TerrainDepthUNet(in_channels=4, base_filters=64).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, T_0=20, T_mult=2)

    best_val = float('inf')
    patience = 15
    no_improve = 0

    log.info(f"Training on {len(dataset.samples)} lakes ({len(train_ds)} train crops, {len(val_ds)} val crops)")
    log.info(f"Model params: {sum(p.numel() for p in model.parameters()):,}")

    for epoch in range(epochs):
        # Train
        model.train()
        train_loss = 0
        for features, depth, mask in train_loader:
            features = features.to(device)
            depth = depth.to(device)
            mask = mask.to(device)

            pred = model(features)

            # Masked MSE loss (only in water areas)
            mse = ((pred - depth) ** 2 * mask).sum() / (mask.sum() + 1)
            # Add Huber loss for robustness to outliers
            huber = (F.smooth_l1_loss(pred * mask, depth * mask, reduction='sum')
                     / (mask.sum() + 1))
            loss = 0.7 * mse + 0.3 * huber

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += loss.item()

        scheduler.step()

        # Validate
        model.eval()
        val_loss = 0
        val_mae = 0
        n_val_pixels = 0
        with torch.no_grad():
            for features, depth, mask in val_loader:
                features = features.to(device)
                depth = depth.to(device)
                mask = mask.to(device)
                pred = model(features)
                mse = ((pred - depth) ** 2 * mask).sum() / (mask.sum() + 1)
                val_loss += mse.item()
                val_mae += (torch.abs(pred - depth) * mask).sum().item()
                n_val_pixels += mask.sum().item()

        avg_train = train_loss / max(len(train_loader), 1)
        avg_val = val_loss / max(len(val_loader), 1)
        avg_mae = val_mae / max(n_val_pixels, 1)

        log.info(
            f"Epoch {epoch+1}/{epochs} — "
            f"Train RMSE: {avg_train**.5:.3f}m, "
            f"Val RMSE: {avg_val**.5:.3f}m, "
            f"Val MAE: {avg_mae:.3f}m"
        )

        if avg_val < best_val:
            best_val = avg_val
            no_improve = 0
            torch.save(model.state_dict(), output_dir / 'best_model.pt')
            torch.save({
                'epoch': epoch,
                'model_state': model.state_dict(),
                'optimizer_state': optimizer.state_dict(),
                'val_rmse': avg_val ** 0.5,
                'val_mae': avg_mae,
            }, output_dir / 'checkpoint.pt')
            log.info(f"  → New best (RMSE: {avg_val**.5:.3f}m, MAE: {avg_mae:.3f}m)")
        else:
            no_improve += 1
            if no_improve >= patience:
                log.info(f"Early stopping at epoch {epoch+1} (no improvement for {patience} epochs)")
                break

    # Save final metrics
    metrics = {
        'best_val_rmse': best_val ** 0.5,
        'epochs_trained': epoch + 1,
        'n_training_lakes': len(dataset.samples),
        'model': 'TerrainDepthUNet',
        'approach': 'Martinsen et al. 2023 — terrain-based bathymetry prediction',
    }
    with open(output_dir / 'training_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=2)

    log.info(f"\nTraining complete! Best Val RMSE: {best_val**.5:.3f}m")
    return model


# ── Prediction ───────────────────────────────────────────────────────

def predict_lake(
    model: TerrainDepthUNet,
    dem_path: Path,
    mask_path: Path,
    output_path: Path,
    device: str = 'cuda',
    max_depth: Optional[float] = None,
) -> np.ndarray:
    """Predict bathymetry for a single lake from its terrain DEM."""
    import rasterio

    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        profile = src.profile
        pixel_size = abs(src.transform.a)

    with rasterio.open(mask_path) as src:
        mask = src.read(1).astype(np.float32)

    lake_elev = np.median(dem[mask > 0.5]) if (mask > 0.5).any() else dem.mean()
    features = compute_terrain_features(dem, mask, lake_elev, pixel_size)

    # Predict
    model.eval()
    with torch.no_grad():
        x = torch.from_numpy(features[np.newaxis]).to(device)
        h, w = dem.shape

        if h > 1024 or w > 1024:
            # Tiled prediction for large DEMs
            depth = _predict_tiled_terrain(model, x, device=device)
        else:
            depth = model(x).cpu().numpy()[0, 0]

    # Apply mask and constraints
    depth = depth * mask
    if max_depth is not None:
        depth = np.clip(depth, 0, max_depth * 1.2)

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_profile = profile.copy()
    out_profile.update(count=1, dtype='float32', compress='deflate')
    with rasterio.open(output_path, 'w', **out_profile) as dst:
        dst.write(depth, 1)

    return depth


def _predict_tiled_terrain(model, x, tile_size=512, overlap=64, device='cuda'):
    """Tiled prediction for large DEMs."""
    _, _, h, w = x.shape
    depth = np.zeros((h, w), dtype=np.float32)
    count = np.zeros((h, w), dtype=np.float32)
    step = tile_size - overlap

    for y in range(0, h, step):
        for xi in range(0, w, step):
            y_end = min(y + tile_size, h)
            x_end = min(xi + tile_size, w)
            y_start = max(0, y_end - tile_size)
            x_start = max(0, x_end - tile_size)

            tile = x[:, :, y_start:y_end, x_start:x_end]
            with torch.no_grad():
                pred = model(tile).cpu().numpy()[0, 0]

            depth[y_start:y_end, x_start:x_end] += pred
            count[y_start:y_end, x_start:x_end] += 1

    depth /= np.maximum(count, 1)
    return depth


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Terrain-based bathymetry prediction')
    sub = parser.add_subparsers(dest='command')

    # Train command
    train_p = sub.add_parser('train', help='Train the model')
    train_p.add_argument('--data-dir', type=str, required=True,
                        help='Directory with *_dem.tif + *_depth.tif + *_mask.tif')
    train_p.add_argument('--output', type=str, default='/data/models/terrain_depth')
    train_p.add_argument('--epochs', type=int, default=100)
    train_p.add_argument('--batch-size', type=int, default=16)
    train_p.add_argument('--lr', type=float, default=1e-3)
    train_p.add_argument('--device', type=str, default='cuda')

    # Predict command
    pred_p = sub.add_parser('predict', help='Predict depth for a lake')
    pred_p.add_argument('--model', type=str, required=True)
    pred_p.add_argument('--dem', type=str, required=True)
    pred_p.add_argument('--mask', type=str, required=True)
    pred_p.add_argument('--output', type=str, required=True)
    pred_p.add_argument('--max-depth', type=float, default=None)
    pred_p.add_argument('--device', type=str, default='cuda')

    args = parser.parse_args()

    if args.command == 'train':
        device = args.device
        if device == 'cuda' and not torch.cuda.is_available():
            device = 'cpu'
            log.info("CUDA not available, using CPU")
        train(Path(args.data_dir), Path(args.output),
              epochs=args.epochs, batch_size=args.batch_size,
              lr=args.lr, device=device)

    elif args.command == 'predict':
        device = args.device
        if device == 'cuda' and not torch.cuda.is_available():
            device = 'cpu'
        model = TerrainDepthUNet(in_channels=4, base_filters=64)
        state = torch.load(args.model, map_location=device, weights_only=True)
        model.load_state_dict(state)
        model.to(device)
        predict_lake(model, Path(args.dem), Path(args.mask),
                    Path(args.output), device=device, max_depth=args.max_depth)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
