#!/usr/bin/env python3
"""
Monitor extraction progress and auto-continue pipeline when done.

Runs in background:
1. Checks extraction progress every 10 minutes
2. Backs up checkpoint to B2 every 100 lakes
3. When extraction completes, runs: 6-step pipeline, multi-temporal, cross-lake retest, final B2 backup
"""

import os
import sys
import time
import subprocess
import logging
import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/data/monitor.log"),
    ],
)
log = logging.getLogger("monitor")

CHECKPOINT = "/data/sonar_s2_full/checkpoint.parquet"
LAST_BACKUP_LAKES = 0


def check_extraction_status():
    """Check if extraction is still running and how many lakes done."""
    # Check if process is running
    result = subprocess.run(
        ["pgrep", "-f", "train_sonar_spectral"],
        capture_output=True, text=True,
    )
    is_running = result.returncode == 0

    # Check checkpoint
    n_lakes = 0
    n_points = 0
    if os.path.exists(CHECKPOINT):
        try:
            import pandas as pd
            df = pd.read_parquet(CHECKPOINT)
            n_lakes = df["lake_id"].nunique()
            n_points = len(df)
        except Exception:
            pass

    return is_running, n_lakes, n_points


def periodic_backup(n_lakes):
    """Run B2 backup if enough new lakes processed."""
    global LAST_BACKUP_LAKES
    if n_lakes - LAST_BACKUP_LAKES >= 100:
        log.info(f"Running periodic backup at {n_lakes} lakes...")
        subprocess.run([sys.executable, "/root/ml/bathymetry/b2_backup.py"],
                       capture_output=True, timeout=300)
        LAST_BACKUP_LAKES = n_lakes
        log.info("Backup done")


def run_post_extraction():
    """Run all remaining pipeline steps after extraction completes."""
    log.info("=" * 70)
    log.info("EXTRACTION COMPLETE — Running post-extraction pipeline")
    log.info("=" * 70)

    # Step 2: 6-step pipeline
    log.info("\n--- Running 6-step pipeline ---")
    subprocess.run(
        [sys.executable, "/root/ml/bathymetry/run_full_bathymetry.py", "--step", "2"],
        timeout=14400,
    )

    # Step 4: Multi-temporal compositing
    log.info("\n--- Running multi-temporal compositing ---")
    subprocess.run(
        [sys.executable, "/root/ml/bathymetry/run_full_bathymetry.py", "--step", "4"],
        timeout=43200,
    )

    # Step 5: Cross-lake generalization re-test with full data
    log.info("\n--- Running cross-lake test on full data ---")
    subprocess.run(
        [sys.executable, "/root/ml/bathymetry/run_full_bathymetry.py", "--step", "5"],
        timeout=7200,
    )

    # Step 3: Re-train morphometric with full data
    log.info("\n--- Re-training morphometric on full data ---")
    subprocess.run(
        [sys.executable, "/root/ml/bathymetry/run_full_bathymetry.py", "--step", "3"],
        timeout=3600,
    )

    # Final B2 backup
    log.info("\n--- Final B2 backup ---")
    subprocess.run(
        [sys.executable, "/root/ml/bathymetry/b2_backup.py"],
        timeout=600,
    )

    log.info("\n" + "=" * 70)
    log.info("ALL PIPELINE STEPS COMPLETE")
    log.info("=" * 70)


def main():
    log.info("Starting extraction monitor")
    log.info(f"Monitoring checkpoint: {CHECKPOINT}")

    while True:
        is_running, n_lakes, n_points = check_extraction_status()

        log.info(f"Status: {'RUNNING' if is_running else 'STOPPED'}, "
                 f"Lakes: {n_lakes}, Points: {n_points:,}")

        if not is_running and n_lakes > 0:
            log.info("Extraction process stopped!")
            # Run post-extraction pipeline
            run_post_extraction()
            break

        # Periodic backup
        if n_lakes > 0:
            periodic_backup(n_lakes)

        # Wait 10 minutes
        time.sleep(600)

    log.info("Monitor exiting")


if __name__ == "__main__":
    main()
