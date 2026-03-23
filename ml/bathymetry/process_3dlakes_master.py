#!/usr/bin/env python3
"""
OpenCatch — Master 3D-LAKES Processing Pipeline

Orchestrates the full pipeline on the A4000:
  1. Extract A-E features from all 510K L1 CSVs
  2. Build global training dataset
  3. Train morphometric, A-E, combined, and stacked models
  4. Upload results to B2

Memory management: Processes in batches of 10K, checkpoints every 50K.
The A4000 has 32GB RAM — peak usage is ~12GB during training.

Usage:
    python process_3dlakes_master.py [--skip-extract] [--skip-train] [--skip-upload]

Requirements:
    pip install b2sdk pandas numpy scipy scikit-learn xgboost lightgbm tqdm pyarrow
"""

import argparse
import gc
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("master_pipeline")


# ── Configuration ───────────────────────────────────────────────────

CONFIG = {
    "l1_dir": "/data/3d_lakes_l1",
    "depths_parquet": "/data/3d_lakes_with_depths.parquet",
    "qa_csv": "/data/3d_lakes_qa.csv",
    "training_dir": "/data/training",
    "models_dir": "/data/models",
    "global_dataset": "/data/training/global_510k.parquet",
    "ae_model_dir": "/data/models/ae_geometric",
    "ensemble_model_dir": "/data/models/global_ensemble",
    "b2_bucket": "opencatch-data",
    "b2_key_id": "004b6da11e9f7ad0000000004",
    "b2_app_key": "K004pK63g6FSh2nPyxRw76B7ZmYgcrI",
}


def run_step(name: str, cmd: list, log_file: str = None) -> bool:
    """Run a pipeline step, logging output."""
    log.info(f"\n{'=' * 60}")
    log.info(f"STEP: {name}")
    log.info(f"{'=' * 60}")
    log.info(f"  Command: {' '.join(cmd)}")

    t0 = time.time()

    if log_file:
        with open(log_file, "w") as lf:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            lf.write(proc.stdout)
            # Also print last 50 lines to console
            lines = proc.stdout.strip().split("\n")
            for line in lines[-50:]:
                log.info(f"  | {line}")
    else:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        lines = proc.stdout.strip().split("\n")
        for line in lines[-50:]:
            log.info(f"  | {line}")

    elapsed = time.time() - t0
    success = proc.returncode == 0

    if success:
        log.info(f"  DONE in {elapsed / 60:.1f} min")
    else:
        log.error(f"  FAILED (exit code {proc.returncode}) in {elapsed / 60:.1f} min")
        # Print full output on failure
        for line in (proc.stdout or "").strip().split("\n"):
            log.error(f"  | {line}")

    return success


def upload_to_b2(local_paths: list, remote_prefix: str = "bathymetry/3dlakes") -> bool:
    """Upload files to Backblaze B2."""
    log.info(f"\n{'=' * 60}")
    log.info("UPLOADING TO B2")
    log.info(f"{'=' * 60}")

    try:
        from b2sdk.v2 import B2Api, InMemoryAccountInfo

        info = InMemoryAccountInfo()
        b2 = B2Api(info)
        b2.authorize_account("production", CONFIG["b2_key_id"], CONFIG["b2_app_key"])

        bucket = b2.get_bucket_by_name(CONFIG["b2_bucket"])

        for local_path in local_paths:
            p = Path(local_path)
            if not p.exists():
                log.warning(f"  Skipping (not found): {local_path}")
                continue

            remote_name = f"{remote_prefix}/{p.name}"
            size_mb = p.stat().st_size / 1024 / 1024

            log.info(f"  Uploading: {p.name} ({size_mb:.1f} MB) → {remote_name}")
            bucket.upload_local_file(
                local_file=str(p),
                file_name=remote_name,
            )
            log.info(f"  Uploaded: {remote_name}")

        return True

    except Exception as e:
        log.error(f"B2 upload failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Master 3D-LAKES processing pipeline",
    )
    parser.add_argument("--skip-extract", action="store_true",
                        help="Skip dataset building, use existing parquet")
    parser.add_argument("--skip-train", action="store_true",
                        help="Skip model training")
    parser.add_argument("--skip-upload", action="store_true",
                        help="Skip B2 upload")
    args = parser.parse_args()

    t0 = time.time()

    # Ensure directories exist
    Path(CONFIG["training_dir"]).mkdir(parents=True, exist_ok=True)
    Path(CONFIG["models_dir"]).mkdir(parents=True, exist_ok=True)

    # ── Step 1: Build global training dataset ──
    if not args.skip_extract:
        if Path(CONFIG["global_dataset"]).exists():
            log.info(f"Global dataset already exists: {CONFIG['global_dataset']}")
            log.info("  Use --skip-extract to skip, or delete to rebuild")

        success = run_step(
            "Build Global Training Dataset (510K lakes)",
            [
                sys.executable, "/workspace/build_global_training.py",
                "--l1-dir", CONFIG["l1_dir"],
                "--depths", CONFIG["depths_parquet"],
                "--qa", CONFIG["qa_csv"],
                "--output", CONFIG["global_dataset"],
                "--batch-size", "10000",
            ],
            log_file="/data/build_global.log",
        )
        if not success:
            log.error("Dataset building failed! Check /data/build_global.log")
            return
    else:
        log.info("Skipping dataset building (--skip-extract)")

    # Verify dataset exists
    if not Path(CONFIG["global_dataset"]).exists():
        log.error(f"Global dataset not found: {CONFIG['global_dataset']}")
        return

    # ── Step 2: Train A-E geometric model ──
    if not args.skip_train:
        success = run_step(
            "Train A-E Geometric Model",
            [
                sys.executable, "/workspace/train_ae_geometric.py",
                "--l1-dir", CONFIG["l1_dir"],
                "--depths", CONFIG["depths_parquet"],
                "--qa", CONFIG["qa_csv"],
                "--output", CONFIG["ae_model_dir"],
                "--skip-extract",  # Use features from build step
            ],
            log_file="/data/ae_train.log",
        )
        if not success:
            log.warning("A-E training had issues — continuing anyway")

        gc.collect()

        # ── Step 3: Train global ensemble ──
        success = run_step(
            "Train Global Ensemble (Morph + A-E + Combined + Stacked)",
            [
                sys.executable, "/workspace/train_global_ensemble.py",
                "--data", CONFIG["global_dataset"],
                "--output", CONFIG["ensemble_model_dir"],
            ],
            log_file="/data/ensemble_train.log",
        )
        if not success:
            log.warning("Ensemble training had issues — continuing anyway")
    else:
        log.info("Skipping training (--skip-train)")

    # ── Step 4: Upload to B2 ──
    if not args.skip_upload:
        upload_files = [
            CONFIG["global_dataset"],
            CONFIG["depths_parquet"],
        ]

        # Add all model files
        for model_dir in [CONFIG["ae_model_dir"], CONFIG["ensemble_model_dir"]]:
            md = Path(model_dir)
            if md.exists():
                upload_files.extend([str(f) for f in md.glob("*")])

        # Add logs
        for log_path in ["/data/build_global.log", "/data/ae_train.log",
                         "/data/ensemble_train.log"]:
            if Path(log_path).exists():
                upload_files.append(log_path)

        upload_to_b2(upload_files)
    else:
        log.info("Skipping B2 upload (--skip-upload)")

    # ── Summary ──
    elapsed = time.time() - t0
    log.info(f"\n{'=' * 60}")
    log.info("PIPELINE COMPLETE")
    log.info(f"{'=' * 60}")
    log.info(f"Total time: {elapsed / 60:.1f} minutes")

    # Print result summaries if available
    for results_file in [
        Path(CONFIG["ae_model_dir"]) / "ae_results.json",
        Path(CONFIG["ensemble_model_dir"]) / "results.json",
    ]:
        if results_file.exists():
            with open(results_file) as fp:
                results = json.load(fp)
            log.info(f"\nResults from {results_file.name}:")
            for name, metrics in results.items():
                if isinstance(metrics, dict) and "rmse" in metrics:
                    log.info(f"  {name:20s}: RMSE={metrics['rmse']:.3f}m  "
                             f"R²={metrics.get('r2_log', metrics.get('r2', 0)):.4f}")


if __name__ == "__main__":
    main()
