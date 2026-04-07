#!/usr/bin/env python3
"""
Prepare an isolated V2 dataset root for rebuilt Sentinel-2 composites.

The original `/data/training/v2` tree already contains the georeferenced depth,
mask, shoreline, and DEM rasters we need for bathymetry training. What is
broken is the spectral `composite.tif`. This script creates a clean output tree
that symlinks the non-spectral assets and writes a GeoJSON index of lake
extents, so `build_s2_composites.py` can regenerate only the spectral
composites into the new tree.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.warp import transform_bounds
from shapely.geometry import box

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("prepare_v2")

LINK_FILES = [
    "depth.tif",
    "mask.tif",
    "shoreline.tif",
    "dem.tif",
]


def ensure_symlink(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink():
        if dst.resolve() == src.resolve():
            return
        dst.unlink()
    elif dst.exists():
        raise FileExistsError(f"Refusing to replace non-symlink file: {dst}")
    os.symlink(src, dst)


def build_index(source_dir: Path, output_dir: Path, index_output: Path, max_lakes: int | None) -> None:
    lake_dirs = sorted([p for p in source_dir.iterdir() if p.is_dir()])
    if max_lakes:
        lake_dirs = lake_dirs[:max_lakes]

    records = []
    prepared = 0
    skipped = 0

    for lake_dir in lake_dirs:
        lake_id = lake_dir.name
        depth_path = lake_dir / "depth.tif"
        if not depth_path.exists():
            skipped += 1
            continue

        with rasterio.open(depth_path) as ds:
            bounds_wgs84 = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=21)
            source_crs = str(ds.crs)

        dest_lake = output_dir / lake_id
        dest_lake.mkdir(parents=True, exist_ok=True)

        for name in LINK_FILES:
            src = lake_dir / name
            if src.exists():
                ensure_symlink(src, dest_lake / name)

        records.append(
            {
                "lake_id": lake_id,
                "lake_name": lake_id,
                "source_crs": source_crs,
                "geometry": box(*bounds_wgs84),
            }
        )
        prepared += 1

    if not records:
        raise RuntimeError(f"No valid lake rasters found in {source_dir}")

    index_output.parent.mkdir(parents=True, exist_ok=True)
    if index_output.exists():
        index_output.unlink()
    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(index_output, driver="GeoJSON")

    log.info("Prepared %s lakes (%s skipped)", prepared, skipped)
    log.info("Wrote GeoJSON index: %s", index_output)
    log.info("Prepared dataset root: %s", output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare isolated dataset tree for rebuilt V2 composites")
    parser.add_argument("--source-dir", required=True, help="Existing training/v2 directory")
    parser.add_argument("--output-dir", required=True, help="Destination dataset root with symlinked non-spectral assets")
    parser.add_argument("--index-output", required=True, help="Output GeoJSON file of lake extents in EPSG:4326")
    parser.add_argument("--max-lakes", type=int, default=None, help="Optional limit for smoke tests")
    args = parser.parse_args()

    build_index(
        source_dir=Path(args.source_dir),
        output_dir=Path(args.output_dir),
        index_output=Path(args.index_output),
        max_lakes=args.max_lakes,
    )


if __name__ == "__main__":
    main()
