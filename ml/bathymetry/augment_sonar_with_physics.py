#!/usr/bin/env python3
"""
Add advanced physics-based water quality features to an existing sonar split parquet.

This is designed as a fast post-processing step for already-extracted sonar/S2
training data. It avoids another expensive STAC extraction cycle by deriving
physics features directly from the saved reflectance bands.

Usage:
    python augment_sonar_with_physics.py \
        --input /data/.../sonar_s2_split.parquet \
        --output /data/.../sonar_s2_split_phys.parquet
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from physics_features import PhysicsFeatureExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("augment_sonar_physics")


def main() -> None:
    parser = argparse.ArgumentParser(description="Augment sonar split parquet with physics features")
    parser.add_argument("--input", required=True, help="Input parquet with reflectance columns")
    parser.add_argument("--output", required=True, help="Output parquet path")
    parser.add_argument("--prefix", default="phys_", help="Prefix for derived feature columns")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(input_path)
    required = {"blue", "green", "red"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing required reflectance columns: {missing}")

    physics_cols = [col for col in ["blue", "green", "red", "nir", "rededge1"] if col in df.columns]
    physics_input = df[physics_cols].copy()

    extractor = PhysicsFeatureExtractor()
    augmented = extractor.compute_features_df(physics_input, prefix=args.prefix)
    new_cols = [col for col in augmented.columns if col.startswith(args.prefix)]

    for col in new_cols:
        df[col] = augmented[col].values

    df.to_parquet(output_path, index=False)
    log.info("Loaded %s rows from %s", f"{len(df):,}", input_path)
    log.info("Added %s physics columns", len(new_cols))
    log.info("Saved augmented parquet to %s", output_path)


if __name__ == "__main__":
    main()
