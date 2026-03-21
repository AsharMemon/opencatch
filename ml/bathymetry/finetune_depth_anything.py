#!/usr/bin/env python3
"""
OpenCatch — Depth Anything V2 Fine-Tuning for Satellite Bathymetry

Fine-tunes Depth Anything V2 Small (24.8M params) for metric depth
prediction from satellite imagery. This is a novel application — no
published work has fine-tuned DA V2 on satellite bathymetry data.

Depth Anything V2 was trained on massive synthetic + real-world RGB
depth data (indoor/outdoor scenes). We fine-tune the metric depth
head to output absolute depth in metres from Sentinel-2 RGB composites.

Architecture:
- Backbone: DINOv2-S (frozen or low LR fine-tuned)
- Head: DPT metric depth head (fine-tuned)
- Input: RGB satellite composite (3 channels from S2 B04, B03, B02)
- Output: Metric depth in metres

Training strategy:
- Freeze DINOv2 backbone initially, train head for 20 epochs
- Unfreeze backbone with 10x lower LR for another 80 epochs
- Apply satellite-specific augmentation (rotation, brightness, haze)

Usage:
    python finetune_depth_anything.py \\
        --data-dir /data/training/v2 \\
        --output /data/models/depth_anything \\
        --epochs 100 \\
        --batch-size 16 \\
        --device cuda

Requirements:
    pip install torch torchvision transformers rasterio numpy tqdm
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.amp import GradScaler, autocast
from torch.utils.data import DataLoader, Dataset, random_split

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("depth_anything_ft")


# ── Model Loading ────────────────────────────────────────────────────

def load_depth_anything_v2(
    model_size: str = "Small",
    pretrained: bool = True,
) -> nn.Module:
    """
    Load Depth Anything V2 model from HuggingFace.

    Models available:
    - depth-anything/Depth-Anything-V2-Small (24.8M params)
    - depth-anything/Depth-Anything-V2-Base (97.5M params)
    - depth-anything/Depth-Anything-V2-Large (335M params)

    Args:
        model_size: "Small", "Base", or "Large"
        pretrained: Whether to load pretrained weights

    Returns:
        Depth Anything V2 model
    """
    try:
        from transformers import AutoModelForDepthEstimation, AutoImageProcessor

        model_id = f"depth-anything/Depth-Anything-V2-{model_size}-hf"
        log.info(f"Loading {model_id} from HuggingFace...")

        model = AutoModelForDepthEstimation.from_pretrained(
            model_id,
            torch_dtype=torch.float32,
        )
        processor = AutoImageProcessor.from_pretrained(model_id)

        log.info(f"Loaded Depth Anything V2 {model_size}")
        n_params = sum(p.numel() for p in model.parameters())
        log.info(f"Parameters: {n_params:,}")

        return model, processor

    except Exception as e:
        log.error(f"Failed to load Depth Anything V2: {e}")
        log.info("Trying torch.hub fallback...")

        # Fallback: try loading from torch.hub
        try:
            model = torch.hub.load(
                "LiheYoung/Depth-Anything",
                f"depth_anything_v2_vits",
                pretrained=pretrained,
            )
            return model, None
        except Exception as e2:
            log.error(f"Torch hub fallback also failed: {e2}")
            raise RuntimeError(
                "Could not load Depth Anything V2. Install: "
                "pip install transformers"
            )


class DepthAnythingBathymetry(nn.Module):
    """
    Wrapper around Depth Anything V2 for metric bathymetry prediction.

    Modifications:
    - Replace relative depth head with metric depth head
    - Add softplus activation for non-negative output
    - Support variable input resolution (satellite tiles vary in size)
    """

    def __init__(
        self,
        base_model: nn.Module,
        max_depth: float = 50.0,
    ):
        super().__init__()
        self.base_model = base_model
        self.max_depth = max_depth

        # Replace the depth head with a metric depth head
        # The base model outputs relative depth; we need metric (metres)
        self.metric_head = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 16, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, 1, 1),
            nn.Softplus(),  # Ensures non-negative depth
        )

        # Scale factor learned during training
        self.scale = nn.Parameter(torch.tensor(10.0))
        self.shift = nn.Parameter(torch.tensor(0.0))

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.

        Args:
            pixel_values: (B, 3, H, W) RGB satellite composite

        Returns:
            (B, 1, H, W) predicted metric depth in metres
        """
        # Get relative depth from base model
        with torch.set_grad_enabled(self.training):
            outputs = self.base_model(pixel_values=pixel_values)

            # Handle different output formats
            if hasattr(outputs, "predicted_depth"):
                relative_depth = outputs.predicted_depth  # (B, H, W)
            elif isinstance(outputs, dict) and "predicted_depth" in outputs:
                relative_depth = outputs["predicted_depth"]
            elif isinstance(outputs, torch.Tensor):
                relative_depth = outputs
            else:
                relative_depth = outputs[0] if isinstance(outputs, (list, tuple)) else outputs

        # Ensure 4D tensor
        if relative_depth.dim() == 3:
            relative_depth = relative_depth.unsqueeze(1)  # (B, 1, H, W)

        # Apply metric depth conversion
        # Scale relative depth to metric range
        metric_depth = self.scale * relative_depth + self.shift

        # Refine with learned metric head
        metric_depth = self.metric_head(metric_depth)

        # Clamp to reasonable range
        metric_depth = metric_depth.clamp(0, self.max_depth)

        return metric_depth


# ── Dataset ──────────────────────────────────────────────────────────

class SatelliteDepthDataset(Dataset):
    """
    Dataset for satellite RGB + depth label pairs.

    Loads 3-channel RGB from the 14-band composite (B04, B03, B02 = Red, Green, Blue)
    and ground truth depth rasters.
    """

    # Band indices in the 14-band composite for RGB
    IDX_RED = 2    # B04
    IDX_GREEN = 1  # B03
    IDX_BLUE = 0   # B02

    def __init__(
        self,
        data_dir: Path,
        patch_size: int = 384,  # DA V2 prefers larger patches
        augment: bool = True,
        crops_per_lake: int = 16,
    ):
        self.data_dir = Path(data_dir)
        self.patch_size = patch_size
        self.augment = augment
        self.crops_per_lake = crops_per_lake
        self.samples = self._scan()

    def _scan(self) -> list[dict]:
        """Find composite + depth pairs."""
        samples = []
        for composite_path in sorted(self.data_dir.glob("**/composite.tif")):
            depth_path = composite_path.parent / "depth.tif"
            if depth_path.exists():
                samples.append({
                    "composite": composite_path,
                    "depth": depth_path,
                })
        log.info(f"Found {len(samples)} training lakes for DA V2")
        return samples

    def __len__(self) -> int:
        return len(self.samples) * self.crops_per_lake

    def __getitem__(self, idx: int) -> dict:
        import rasterio

        sample = self.samples[idx % len(self.samples)]
        ps = self.patch_size

        # Load composite and extract RGB (B04=Red, B03=Green, B02=Blue)
        with rasterio.open(sample["composite"]) as src:
            composite = src.read().astype(np.float32)  # (14, H, W)

        # Extract RGB channels
        rgb = np.stack([
            composite[self.IDX_RED],   # Red (B04)
            composite[self.IDX_GREEN], # Green (B03)
            composite[self.IDX_BLUE],  # Blue (B02)
        ], axis=0)  # (3, H, W)

        # Load depth
        with rasterio.open(sample["depth"]) as src:
            depth = src.read(1).astype(np.float32)  # (H, W)

        # Label mask
        label_mask = np.isfinite(depth) & (depth >= 0)
        depth = np.nan_to_num(depth, nan=0.0)

        # Random crop
        h, w = depth.shape
        if h >= ps and w >= ps:
            for _ in range(10):
                y = np.random.randint(0, h - ps + 1)
                x = np.random.randint(0, w - ps + 1)
                if label_mask[y:y + ps, x:x + ps].sum() > ps * ps * 0.01:
                    break
            rgb = rgb[:, y:y + ps, x:x + ps]
            depth = depth[y:y + ps, x:x + ps]
            label_mask = label_mask[y:y + ps, x:x + ps]
        else:
            pad_h = max(0, ps - h)
            pad_w = max(0, ps - w)
            rgb = np.pad(rgb, ((0, 0), (0, pad_h), (0, pad_w)))
            depth = np.pad(depth, ((0, pad_h), (0, pad_w)))
            label_mask = np.pad(label_mask, ((0, pad_h), (0, pad_w)))

        # Augmentation
        if self.augment:
            rgb, depth, label_mask = self._augment(rgb, depth, label_mask)

        # Normalize RGB to [0, 1] then to ImageNet mean/std
        rgb = np.clip(rgb, 0, 1)
        mean = np.array([0.485, 0.456, 0.406])[:, None, None]
        std = np.array([0.229, 0.224, 0.225])[:, None, None]
        rgb_norm = (rgb - mean) / std

        return {
            "pixel_values": torch.from_numpy(rgb_norm.astype(np.float32)),
            "depth": torch.from_numpy(depth[np.newaxis].astype(np.float32)),
            "label_mask": torch.from_numpy(label_mask[np.newaxis].astype(np.float32)),
        }

    def _augment(
        self,
        rgb: np.ndarray,
        depth: np.ndarray,
        mask: np.ndarray,
    ) -> tuple:
        """Satellite-specific augmentations."""
        # Random flips
        if np.random.random() > 0.5:
            rgb = rgb[:, :, ::-1].copy()
            depth = depth[:, ::-1].copy()
            mask = mask[:, ::-1].copy()
        if np.random.random() > 0.5:
            rgb = rgb[:, ::-1, :].copy()
            depth = depth[::-1, :].copy()
            mask = mask[::-1, :].copy()

        # Random 90-degree rotation
        if np.random.random() > 0.5:
            k = np.random.randint(1, 4)
            rgb = np.rot90(rgb, k, axes=(1, 2)).copy()
            depth = np.rot90(depth, k).copy()
            mask = np.rot90(mask, k).copy()

        # Brightness/contrast
        if np.random.random() > 0.5:
            factor = np.random.uniform(0.8, 1.2)
            rgb = rgb * factor

        # Simulated haze (additive uniform noise)
        if np.random.random() > 0.7:
            haze = np.random.uniform(0, 0.05)
            rgb = rgb + haze

        return rgb, depth, mask


# ── Training ─────────────────────────────────────────────────────────

def train(args: argparse.Namespace) -> None:
    """Two-stage fine-tuning: frozen backbone -> full fine-tune."""
    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
        log.warning("CUDA not available, using CPU")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load model
    base_model, processor = load_depth_anything_v2(
        model_size=args.model_size,
        pretrained=True,
    )

    model = DepthAnythingBathymetry(
        base_model=base_model,
        max_depth=args.max_depth,
    ).to(device)

    # Dataset
    dataset = SatelliteDepthDataset(
        args.data_dir,
        patch_size=args.patch_size,
        augment=True,
        crops_per_lake=args.crops_per_lake,
    )

    if len(dataset.samples) == 0:
        log.error(f"No training data found in {args.data_dir}")
        return

    n_val = max(1, len(dataset.samples) // 5)
    n_val_crops = n_val * args.crops_per_lake
    n_train_crops = len(dataset) - n_val_crops
    train_ds, val_ds = random_split(dataset, [n_train_crops, n_val_crops])

    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        num_workers=args.workers, pin_memory=True, drop_last=True,
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True,
    )

    log.info(f"Train: {n_train_crops} crops, Val: {n_val_crops} crops")

    # ── Stage 1: Frozen backbone ──
    log.info("=" * 50)
    log.info("Stage 1: Training metric head (backbone frozen)")
    log.info("=" * 50)

    # Freeze backbone
    for param in model.base_model.parameters():
        param.requires_grad = False

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log.info(f"Trainable parameters (head only): {trainable:,}")

    optimizer_head = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=args.lr_head,
        weight_decay=1e-4,
    )
    scheduler_head = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer_head, T_max=args.frozen_epochs,
    )
    scaler = GradScaler("cuda", enabled=(device == "cuda"))

    best_val_rmse = float("inf")
    history = []

    # Resume support
    ckpt_path = output_dir / "checkpoint.pt"
    start_epoch = 0
    if ckpt_path.exists() and args.resume:
        ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model_state"])
        start_epoch = ckpt["epoch"] + 1
        best_val_rmse = ckpt.get("best_val_rmse", float("inf"))
        history = ckpt.get("history", [])
        log.info(f"Resumed from epoch {start_epoch}")

    total_epochs = args.frozen_epochs + args.finetune_epochs

    for epoch in range(start_epoch, total_epochs):
        t0 = time.time()

        # Switch to Stage 2 at frozen_epochs boundary
        if epoch == args.frozen_epochs:
            log.info("=" * 50)
            log.info("Stage 2: Full fine-tuning (backbone unfrozen)")
            log.info("=" * 50)

            for param in model.base_model.parameters():
                param.requires_grad = True

            trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
            log.info(f"Trainable parameters (full): {trainable:,}")

            # New optimizer with lower backbone LR
            param_groups = [
                {"params": model.base_model.parameters(), "lr": args.lr_backbone},
                {"params": list(model.metric_head.parameters()) + [model.scale, model.shift],
                 "lr": args.lr_head},
            ]
            optimizer_head = torch.optim.AdamW(param_groups, weight_decay=1e-4)
            scheduler_head = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer_head, T_max=args.finetune_epochs,
            )
            scaler = GradScaler("cuda", enabled=(device == "cuda"))

        # Train
        model.train()
        train_loss = 0.0
        train_rmse = 0.0
        n_batches = 0

        for batch in train_loader:
            pixel_values = batch["pixel_values"].to(device)
            depth = batch["depth"].to(device)
            label_mask = batch["label_mask"].to(device)

            with autocast(device_type="cuda", enabled=(device == "cuda")):
                pred = model(pixel_values)

                # Resize pred to match depth if needed
                if pred.shape[2:] != depth.shape[2:]:
                    pred = F.interpolate(
                        pred, size=depth.shape[2:],
                        mode="bilinear", align_corners=False,
                    )

                # Masked MSE
                n_valid = label_mask.sum().clamp(min=1)
                mse = ((pred - depth) ** 2 * label_mask).sum() / n_valid

                # Scale-invariant gradient loss (improves edge quality)
                dx_pred = pred[:, :, :, 1:] - pred[:, :, :, :-1]
                dx_gt = depth[:, :, :, 1:] - depth[:, :, :, :-1]
                dy_pred = pred[:, :, 1:, :] - pred[:, :, :-1, :]
                dy_gt = depth[:, :, 1:, :] - depth[:, :, :-1, :]
                grad_loss = (
                    (dx_pred - dx_gt).abs().mean()
                    + (dy_pred - dy_gt).abs().mean()
                )

                loss = mse + 0.1 * grad_loss

            optimizer_head.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer_head)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer_head)
            scaler.update()

            train_loss += loss.item()
            train_rmse += mse.sqrt().item()
            n_batches += 1

        scheduler_head.step()

        # Validate
        model.eval()
        val_loss = 0.0
        val_rmse_sum = 0.0
        val_mae_sum = 0.0
        n_val_batches = 0

        with torch.no_grad():
            for batch in val_loader:
                pixel_values = batch["pixel_values"].to(device)
                depth = batch["depth"].to(device)
                label_mask = batch["label_mask"].to(device)

                with autocast(device_type="cuda", enabled=(device == "cuda")):
                    pred = model(pixel_values)
                    if pred.shape[2:] != depth.shape[2:]:
                        pred = F.interpolate(
                            pred, size=depth.shape[2:],
                            mode="bilinear", align_corners=False,
                        )

                n_valid = label_mask.sum().clamp(min=1)
                mse = ((pred - depth) ** 2 * label_mask).sum() / n_valid
                mae = ((pred - depth).abs() * label_mask).sum() / n_valid

                val_loss += mse.item()
                val_rmse_sum += mse.sqrt().item()
                val_mae_sum += mae.item()
                n_val_batches += 1

        avg_train_rmse = train_rmse / max(n_batches, 1)
        avg_val_rmse = val_rmse_sum / max(n_val_batches, 1)
        avg_val_mae = val_mae_sum / max(n_val_batches, 1)
        elapsed = time.time() - t0

        stage = "Stage1-Frozen" if epoch < args.frozen_epochs else "Stage2-Full"
        lr_current = optimizer_head.param_groups[-1]["lr"]

        record = {
            "epoch": epoch,
            "stage": stage,
            "lr": lr_current,
            "train_rmse": avg_train_rmse,
            "val_rmse": avg_val_rmse,
            "val_mae": avg_val_mae,
            "elapsed_s": elapsed,
        }
        history.append(record)

        log.info(
            f"[{stage}] Epoch {epoch + 1}/{total_epochs} "
            f"({elapsed:.0f}s, lr={lr_current:.1e}) — "
            f"Train RMSE: {avg_train_rmse:.3f}m, "
            f"Val RMSE: {avg_val_rmse:.3f}m, "
            f"Val MAE: {avg_val_mae:.3f}m"
        )

        # Checkpoint
        torch.save({
            "epoch": epoch,
            "model_state": model.state_dict(),
            "best_val_rmse": best_val_rmse,
            "history": history,
        }, ckpt_path)

        if avg_val_rmse < best_val_rmse:
            best_val_rmse = avg_val_rmse
            torch.save(model.state_dict(), output_dir / "best_model.pt")
            log.info(f"  -> New best! Val RMSE: {best_val_rmse:.4f}m")

    # Save final results
    log.info("=" * 50)
    log.info(f"Training complete! Best Val RMSE: {best_val_rmse:.4f}m")

    with open(output_dir / "training_history.json", "w") as f:
        json.dump(history, f, indent=2)

    metrics = {
        "best_val_rmse": best_val_rmse,
        "total_epochs": total_epochs,
        "frozen_epochs": args.frozen_epochs,
        "finetune_epochs": args.finetune_epochs,
        "model_size": args.model_size,
        "n_lakes": len(dataset.samples),
        "approach": "Depth Anything V2 fine-tuned on satellite bathymetry (novel)",
    }
    with open(output_dir / "training_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fine-tune Depth Anything V2 for satellite bathymetry",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--data-dir", type=str, required=True,
                        help="Training data directory (lakes with composite.tif + depth.tif)")
    parser.add_argument("--output", type=str, default="/data/models/depth_anything",
                        help="Output directory")

    # Model
    parser.add_argument("--model-size", type=str, default="Small",
                        choices=["Small", "Base", "Large"],
                        help="Depth Anything V2 model size")
    parser.add_argument("--max-depth", type=float, default=50.0,
                        help="Maximum prediction depth in metres")

    # Training
    parser.add_argument("--frozen-epochs", type=int, default=20,
                        help="Epochs with frozen backbone")
    parser.add_argument("--finetune-epochs", type=int, default=80,
                        help="Epochs with full fine-tuning")
    parser.add_argument("--batch-size", type=int, default=16,
                        help="Batch size")
    parser.add_argument("--lr-head", type=float, default=1e-3,
                        help="Learning rate for metric head")
    parser.add_argument("--lr-backbone", type=float, default=1e-5,
                        help="Learning rate for backbone (10x lower)")
    parser.add_argument("--patch-size", type=int, default=384,
                        help="Training patch size")
    parser.add_argument("--crops-per-lake", type=int, default=16,
                        help="Random crops per lake")
    parser.add_argument("--workers", type=int, default=4,
                        help="DataLoader workers")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")

    args = parser.parse_args()
    args.epochs = args.frozen_epochs + args.finetune_epochs
    train(args)


if __name__ == "__main__":
    main()
