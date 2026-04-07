#!/usr/bin/env python3
"""Back up bathymetry pipeline outputs to Backblaze B2."""

import os
import datetime
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("b2_backup")

B2_BUCKET = "opencatch-data"
B2_KEY_ID = "004b6da11e9f7ad0000000004"
B2_APP_KEY = "K004pK63g6FSh2nPyxRw76B7ZmYgcrI"


def backup():
    from b2sdk.v2 import B2Api, InMemoryAccountInfo

    info = InMemoryAccountInfo()
    api = B2Api(info)
    api.authorize_account("production", B2_KEY_ID, B2_APP_KEY)
    log.info("B2 authorized")

    bucket = api.get_bucket_by_name(B2_BUCKET)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    upload_targets = [
        "/data/sonar_s2_full/checkpoint.parquet",
        "/data/sonar_s2_full/sonar_s2_training.parquet",
        "/data/sonar_s2_full/multitemporal_composite.parquet",
        "/data/sonar_s2/preprocessed_v2.parquet",
        "/data/sonar_s2/checkpoint.parquet",
        "/data/pipeline_outputs/cross_lake_results.json",
        "/data/models/cross_lake_xgb.json",
        "/data/models/cross_lake_lgb.txt",
        "/data/models/terrain_morphometric/morphometric_xgb.json",
        "/data/models/terrain_morphometric/morphometric_results.json",
        "/data/full_pipeline.log",
        "/data/morphometric_train.log",
        "/data/cross_lake_181.log",
        "/data/extraction_1951.log",
    ]

    n_uploaded = 0
    for local_path in upload_targets:
        if not os.path.exists(local_path):
            log.info(f"  Skip: {local_path} (not found)")
            continue

        b2_key = f"bathymetry/full_1951/{timestamp}/{Path(local_path).name}"
        try:
            bucket.upload_local_file(
                local_file=local_path,
                file_name=b2_key,
            )
            size_mb = os.path.getsize(local_path) / 1e6
            log.info(f"  Uploaded: {Path(local_path).name} ({size_mb:.1f} MB) -> {b2_key}")
            n_uploaded += 1
        except Exception as e:
            log.warning(f"  Failed: {local_path}: {e}")

    log.info(f"\nB2 backup complete: {n_uploaded} files uploaded")


if __name__ == "__main__":
    backup()
