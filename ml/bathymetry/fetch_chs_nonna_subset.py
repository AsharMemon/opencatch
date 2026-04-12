#!/usr/bin/env python3
"""
Fetch a Canadian Hydrographic Service NONNA subset and optionally contour it.

This is the official Canada-side companion to the NOAA/CUDEM coastal path.
It downloads a NONNA 10 or NONNA 100 GeoTIFF via CHS WCS and can emit a
simple contour GeoJSON that feeds the existing vector PMTiles pipeline.

Usage
-----
python fetch_chs_nonna_subset.py \
  --bbox 48.9,-123.9,49.5,-123.0 \
  --output /Users/Ashar/Documents/fish/data/bathymetry/chs_nonna/subsets/salish_sea.tif \
  --contours-output /Users/Ashar/Documents/fish/data/bathymetry/chs_nonna/subsets/salish_sea_contours.geojson
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Iterable

import requests

NONNA_WCS_URL = "https://nonna-geoserver.data.chs-shc.ca/geoserver/wcs"
NONNA10_COVERAGE_ID = "nonna__NONNA 10 Coverage"
NONNA100_COVERAGE_ID = "nonna__NONNA 100 Coverage"
USER_AGENT = "OpenCatch/1.0 (contact@opencatch.app)"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("fetch_chs_nonna_subset")


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    south, west, north, east = [float(part.strip()) for part in raw.split(",")]
    return south, west, north, east


def coverage_id_for_resolution(resolution: int) -> str:
    if resolution == 10:
        return NONNA10_COVERAGE_ID
    if resolution == 100:
        return NONNA100_COVERAGE_ID
    raise ValueError("resolution must be 10 or 100")


def iter_chunks(resp: requests.Response, chunk_size: int = 8 * 1024 * 1024) -> Iterable[bytes]:
    for chunk in resp.iter_content(chunk_size=chunk_size):
        if chunk:
            yield chunk


def fetch_subset(
    bbox: tuple[float, float, float, float],
    output_path: Path,
    *,
    resolution: int = 10,
) -> Path:
    south, west, north, east = bbox
    output_path.parent.mkdir(parents=True, exist_ok=True)
    params = [
        ("service", "WCS"),
        ("version", "2.0.1"),
        ("request", "GetCoverage"),
        ("CoverageId", coverage_id_for_resolution(resolution)),
        ("format", "image/tiff"),
        ("subset", f"Lat({south},{north})"),
        ("subset", f"Long({west},{east})"),
        ("subsettingCrs", "http://www.opengis.net/def/crs/EPSG/0/4326"),
    ]
    log.info("Downloading CHS NONNA %sm subset for bbox=%s", resolution, bbox)
    with requests.get(
        NONNA_WCS_URL,
        params=params,
        timeout=300,
        stream=True,
        headers={"User-Agent": USER_AGENT},
    ) as response:
        response.raise_for_status()
        with open(output_path, "wb") as fh:
            for chunk in iter_chunks(response):
                fh.write(chunk)
    return output_path


def build_contours(
    tiff_path: Path,
    contours_output: Path,
    *,
    contour_intervals_m: list[float] | None = None,
) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import rasterio
    from shapely.geometry import LineString, MultiLineString, mapping

    contour_intervals_m = contour_intervals_m or [1, 2, 3, 5, 10, 15, 20, 30, 50, 100]

    with rasterio.open(tiff_path) as ds:
        data = ds.read(1).astype("float32")
        transform = ds.transform
        nodata = ds.nodata
        bounds = ds.bounds

    if nodata is not None:
        data = np.where(data == nodata, np.nan, data)

    # NONNA is elevation-style. Water is below chart datum, so use positive-down.
    data = np.where(data < 0, -data, np.nan)
    if not np.isfinite(data).any():
        raise RuntimeError("NONNA subset contains no bathymetry values in requested bbox")

    max_depth = float(np.nanmax(data))
    levels = [level for level in contour_intervals_m if level <= max_depth]
    if not levels:
        levels = [1, 2, 5]

    rows, cols = data.shape
    lon_arr = np.linspace(bounds.left, bounds.right, cols)
    lat_arr = np.linspace(bounds.bottom, bounds.top, rows)
    lon_grid, lat_grid = np.meshgrid(lon_arr, lat_arr)

    fig, ax = plt.subplots()
    cs = ax.contour(lon_grid, lat_grid, data, levels=levels)
    plt.close(fig)

    features: list[dict] = []
    for idx, level in enumerate(cs.levels):
        lines = []
        for seg in cs.allsegs[idx]:
            if len(seg) >= 2:
                lines.append(LineString(seg))
        if not lines:
            continue
        geom = MultiLineString(lines) if len(lines) > 1 else lines[0]
        depth_ft = round(float(level) * 3.28084, 1)
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(geom),
                "properties": {
                    "feature_kind": "contour_line",
                    "depth_m": round(float(level), 2),
                    "depth_ft": depth_ft,
                    "label": f"{round(depth_ft)} ft",
                    "source": "nonna",
                    "source_id": "ocean_ca",
                    "contour_quality": "survey",
                    "water_body_type": "ocean",
                    "attribution": "Canadian Hydrographic Service NONNA 10",
                },
            }
        )

    contours_output.parent.mkdir(parents=True, exist_ok=True)
    geojson = {"type": "FeatureCollection", "features": features}
    contours_output.write_text(json.dumps(geojson))
    return {
        "contour_count": len(features),
        "max_depth_m": round(max_depth, 2),
        "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
        "rows": rows,
        "cols": cols,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch CHS NONNA WCS subset and optionally contour it.")
    parser.add_argument("--bbox", required=True, help="south,west,north,east")
    parser.add_argument("--output", type=Path, required=True, help="GeoTIFF output path")
    parser.add_argument("--resolution", type=int, default=10, choices=[10, 100])
    parser.add_argument("--contours-output", type=Path, help="Optional contour GeoJSON output path")
    parser.add_argument("--contour-intervals", type=str, help="Comma-separated contour intervals in metres")
    args = parser.parse_args()

    bbox = parse_bbox(args.bbox)
    tiff_path = fetch_subset(bbox, args.output, resolution=args.resolution)
    summary: dict = {
        "output": str(tiff_path),
        "bbox": bbox,
        "resolution": args.resolution,
    }
    if args.contours_output:
        contour_intervals = (
            [float(x.strip()) for x in args.contour_intervals.split(",") if x.strip()]
            if args.contour_intervals
            else None
        )
        summary["contours"] = build_contours(
            tiff_path,
            args.contours_output,
            contour_intervals_m=contour_intervals,
        )
        summary["contours_output"] = str(args.contours_output)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
