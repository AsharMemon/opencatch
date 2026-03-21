#!/usr/bin/env python3
"""
OpenCatch — Satellite-Derived Bathymetry Prediction

Uses Sentinel-2 multispectral satellite imagery to predict water depth
for inland lakes where no survey data exists.

Architecture:
1. Download Sentinel-2 imagery for target water bodies
2. Extract spectral band ratios (Stumpf log-ratio model as baseline)
3. Train U-Net on lakes WITH known bathymetry → predict unsurveyed lakes
4. Generate depth contour lines for vector tile rendering

References:
- Stumpf et al. (2003): log-ratio model for SDB
- Legleiter et al. (2019): OBRA for optimal band ratios
- Li et al. (2021): Deep learning for inland SDB

Requirements:
    pip install torch torchvision rasterio geopandas shapely
    pip install planetary-computer pystac-client odc-stac
    pip install scikit-learn matplotlib tqdm
"""

import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import numpy as np
import torch
import torch.nn as nn

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


# ── Sentinel-2 Band Configuration ────────────────────────────────────

@dataclass
class S2Bands:
    """Sentinel-2 bands relevant for bathymetry."""
    B02_BLUE: int = 0     # 490 nm — penetrates deepest in clear water
    B03_GREEN: int = 1    # 560 nm — good depth sensitivity
    B04_RED: int = 2      # 665 nm — absorbed quickly (shallow only)
    B08_NIR: int = 3      # 842 nm — water mask (absorbed by water)
    B11_SWIR: int = 4     # 1610 nm — water mask refinement

    @staticmethod
    def band_names():
        return ['B02', 'B03', 'B04', 'B08', 'B11']


# ── Stumpf Log-Ratio Model (Baseline) ───────────────────────────────

def stumpf_log_ratio(blue: np.ndarray, green: np.ndarray) -> np.ndarray:
    """
    Stumpf et al. (2003) log-ratio model for satellite-derived bathymetry.

    depth ∝ m₁ × ln(nRw_blue) / ln(nRw_green) - m₀

    This is a simple physics-based model that works well in clear water.
    The ratio compensates for varying bottom reflectance.
    """
    # Avoid division by zero
    green_safe = np.clip(green, 1e-6, None)
    blue_safe = np.clip(blue, 1e-6, None)

    ratio = np.log(1000 * blue_safe) / np.log(1000 * green_safe)
    return ratio


def compute_band_features(bands: np.ndarray) -> np.ndarray:
    """
    Compute spectral features for bathymetry prediction.

    Input: (H, W, 5) array of S2 bands [B02, B03, B04, B08, B11]
    Output: (H, W, F) array of features
    """
    blue, green, red, nir, swir = [bands[:, :, i] for i in range(5)]

    features = []

    # Raw bands (normalized)
    for b in [blue, green, red]:
        features.append(b)

    # Stumpf log-ratio
    features.append(stumpf_log_ratio(blue, green))

    # Additional ratios
    features.append(np.log1p(blue) - np.log1p(red))   # Blue/Red ratio
    features.append(np.log1p(green) - np.log1p(red))  # Green/Red ratio
    features.append(blue / (green + 1e-6))
    features.append(green / (red + 1e-6))

    # NDWI (Normalized Difference Water Index)
    features.append((green - nir) / (green + nir + 1e-6))

    # Modified NDWI (using SWIR)
    features.append((green - swir) / (green + swir + 1e-6))

    return np.stack(features, axis=-1).astype(np.float32)


# ── U-Net Model for Depth Prediction ────────────────────────────────

class DoubleConv(nn.Module):
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.conv(x)


class BathymetryUNet(nn.Module):
    """
    U-Net for pixel-wise depth prediction from satellite imagery.

    Input: (B, C, H, W) — C spectral features
    Output: (B, 1, H, W) — predicted depth in metres
    """

    def __init__(self, in_channels: int = 11, base_filters: int = 32):
        super().__init__()

        f = base_filters

        # Encoder
        self.enc1 = DoubleConv(in_channels, f)
        self.enc2 = DoubleConv(f, f * 2)
        self.enc3 = DoubleConv(f * 2, f * 4)
        self.enc4 = DoubleConv(f * 4, f * 8)

        # Bottleneck
        self.bottleneck = DoubleConv(f * 8, f * 16)

        # Decoder
        self.up4 = nn.ConvTranspose2d(f * 16, f * 8, 2, stride=2)
        self.dec4 = DoubleConv(f * 16, f * 8)
        self.up3 = nn.ConvTranspose2d(f * 8, f * 4, 2, stride=2)
        self.dec3 = DoubleConv(f * 8, f * 4)
        self.up2 = nn.ConvTranspose2d(f * 4, f * 2, 2, stride=2)
        self.dec2 = DoubleConv(f * 4, f * 2)
        self.up1 = nn.ConvTranspose2d(f * 2, f, 2, stride=2)
        self.dec1 = DoubleConv(f * 2, f)

        # Output head — predict depth (positive values)
        self.head = nn.Sequential(
            nn.Conv2d(f, 1, 1),
            nn.Softplus(),  # Ensures positive depth predictions
        )

        self.pool = nn.MaxPool2d(2)

    def forward(self, x):
        # Encoder
        e1 = self.enc1(x)
        e2 = self.enc2(self.pool(e1))
        e3 = self.enc3(self.pool(e2))
        e4 = self.enc4(self.pool(e3))

        # Bottleneck
        b = self.bottleneck(self.pool(e4))

        # Decoder with skip connections
        d4 = self.dec4(torch.cat([self.up4(b), e4], dim=1))
        d3 = self.dec3(torch.cat([self.up3(d4), e3], dim=1))
        d2 = self.dec2(torch.cat([self.up2(d3), e2], dim=1))
        d1 = self.dec1(torch.cat([self.up1(d2), e1], dim=1))

        return self.head(d1)


# ── Training Pipeline ────────────────────────────────────────────────

class BathymetryDataset(torch.utils.data.Dataset):
    """
    Dataset pairing Sentinel-2 imagery with known bathymetry.

    For each lake with survey data:
    - Load S2 bands → compute spectral features
    - Load bathymetric DEM → target depth raster
    - Generate 256×256 random crops for training
    """

    def __init__(self, data_dir: Path, patch_size: int = 256, augment: bool = True):
        self.data_dir = data_dir
        self.patch_size = patch_size
        self.augment = augment
        self.samples = self._scan_samples()

    def _scan_samples(self):
        """Find all matched S2 + bathymetry pairs."""
        samples = []
        for s2_path in self.data_dir.glob('*_s2.tif'):
            bathy_path = s2_path.parent / s2_path.name.replace('_s2.tif', '_depth.tif')
            mask_path = s2_path.parent / s2_path.name.replace('_s2.tif', '_mask.tif')
            if bathy_path.exists():
                samples.append({
                    's2': s2_path,
                    'depth': bathy_path,
                    'mask': mask_path if mask_path.exists() else None,
                })
        return samples

    def __len__(self):
        return len(self.samples) * 16  # 16 random crops per lake

    def __getitem__(self, idx):
        sample = self.samples[idx % len(self.samples)]

        import rasterio

        with rasterio.open(sample['s2']) as src:
            bands = src.read()  # (C, H, W)
        with rasterio.open(sample['depth']) as src:
            depth = src.read(1)  # (H, W)

        # Random crop
        h, w = depth.shape
        ps = self.patch_size
        if h > ps and w > ps:
            y = np.random.randint(0, h - ps)
            x = np.random.randint(0, w - ps)
            bands = bands[:, y:y+ps, x:x+ps]
            depth = depth[y:y+ps, x:x+ps]

        # Compute spectral features
        bands_hwc = np.transpose(bands, (1, 2, 0))  # (H, W, C)
        features = compute_band_features(bands_hwc)
        features = np.transpose(features, (2, 0, 1))  # (F, H, W)

        # Water mask: NIR should be low for water
        water_mask = (bands[3] < 0.1).astype(np.float32)

        # Apply water mask to depth (non-water = 0)
        depth = depth * water_mask

        if self.augment:
            # Random horizontal/vertical flip
            if np.random.random() > 0.5:
                features = features[:, :, ::-1].copy()
                depth = depth[:, ::-1].copy()
            if np.random.random() > 0.5:
                features = features[:, ::-1, :].copy()
                depth = depth[::-1, :].copy()

        return (
            torch.from_numpy(features),
            torch.from_numpy(depth[np.newaxis]),  # (1, H, W)
            torch.from_numpy(water_mask[np.newaxis]),
        )


def train_model(
    data_dir: Path,
    output_dir: Path,
    epochs: int = 50,
    batch_size: int = 8,
    lr: float = 1e-3,
    device: str = 'cuda',
):
    """Train the bathymetry U-Net model."""
    output_dir.mkdir(parents=True, exist_ok=True)

    dataset = BathymetryDataset(data_dir)
    n_val = max(1, len(dataset) // 5)
    n_train = len(dataset) - n_val
    train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

    train_loader = torch.utils.data.DataLoader(
        train_ds, batch_size=batch_size, shuffle=True, num_workers=4, pin_memory=True,
    )
    val_loader = torch.utils.data.DataLoader(
        val_ds, batch_size=batch_size, shuffle=False, num_workers=4, pin_memory=True,
    )

    model = BathymetryUNet(in_channels=11).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_loss = float('inf')

    for epoch in range(epochs):
        # Train
        model.train()
        train_loss = 0
        for features, depth, mask in train_loader:
            features = features.to(device)
            depth = depth.to(device)
            mask = mask.to(device)

            pred = model(features)

            # Masked MSE loss — only penalize within water areas
            loss = ((pred - depth) ** 2 * mask).sum() / (mask.sum() + 1)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            train_loss += loss.item()

        scheduler.step()

        # Validate
        model.eval()
        val_loss = 0
        with torch.no_grad():
            for features, depth, mask in val_loader:
                features = features.to(device)
                depth = depth.to(device)
                mask = mask.to(device)
                pred = model(features)
                loss = ((pred - depth) ** 2 * mask).sum() / (mask.sum() + 1)
                val_loss += loss.item()

        avg_train = train_loss / len(train_loader)
        avg_val = val_loss / len(val_loader) if len(val_loader) > 0 else 0

        log.info(f"Epoch {epoch+1}/{epochs} — Train RMSE: {avg_train**.5:.3f}m, Val RMSE: {avg_val**.5:.3f}m")

        if avg_val < best_val_loss:
            best_val_loss = avg_val
            torch.save(model.state_dict(), output_dir / 'best_model.pt')
            log.info(f"  → New best model saved (RMSE: {avg_val**.5:.3f}m)")

    log.info("Training complete!")
    return model


# ── Contour Generation ───────────────────────────────────────────────

def depth_to_contours(
    depth_raster: np.ndarray,
    transform,  # rasterio Affine
    intervals: list[float] = [1, 2, 3, 5, 7, 10, 15, 20, 30, 50],
    smooth: bool = True,
) -> dict:
    """
    Convert a predicted depth raster to contour lines as GeoJSON.

    Args:
        depth_raster: (H, W) array of predicted depths in metres
        transform: Rasterio affine transform for georeferencing
        intervals: Depth values at which to draw contours
        smooth: Whether to smooth contour lines

    Returns:
        GeoJSON FeatureCollection of contour lines
    """
    from rasterio.features import shapes
    import shapely.geometry as sg

    features = []

    for depth_val in intervals:
        # Binary mask: areas deeper than this contour
        mask = (depth_raster >= depth_val).astype(np.uint8)

        # Extract polygons from mask
        for geom, val in shapes(mask, transform=transform):
            if val == 1:
                # Convert polygon to line (boundary)
                poly = sg.shape(geom)
                line = poly.exterior

                if smooth and line.length > 0:
                    line = line.simplify(0.0001)  # Simplify slightly

                features.append({
                    'type': 'Feature',
                    'geometry': sg.mapping(line),
                    'properties': {
                        'depth_m': depth_val,
                        'depth_ft': round(depth_val * 3.281, 1),
                    },
                })

    return {
        'type': 'FeatureCollection',
        'features': features,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Train bathymetry prediction model')
    parser.add_argument('--data-dir', type=str, required=True, help='Directory with S2 + depth pairs')
    parser.add_argument('--output-dir', type=str, default='/models/bathymetry', help='Model output dir')
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--device', type=str, default='cuda')
    args = parser.parse_args()

    train_model(
        Path(args.data_dir),
        Path(args.output_dir),
        epochs=args.epochs,
        batch_size=args.batch_size,
        device=args.device,
    )
