#!/usr/bin/env python3
"""
Downsample the Missouri Clearwater Lake 1 m point cloud into a tileable
depth-point GeoJSON lane.

The source ZIP contains ~19M UTM Zone 15N points with ELEV_F values. We derive
depth as (max water-surface elevation - point elevation), then aggregate points
into a regular projected grid so the result is compact enough to tile and use
in the app.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import shapefile
from pyproj import Transformer

FT_TO_M = 0.3048


@dataclass
class CellStats:
    count: int = 0
    sum_x: float = 0.0
    sum_y: float = 0.0
    sum_depth_ft: float = 0.0
    min_depth_ft: float = math.inf
    max_depth_ft: float = 0.0

    def add(self, x: float, y: float, depth_ft: float) -> None:
        self.count += 1
        self.sum_x += x
        self.sum_y += y
        self.sum_depth_ft += depth_ft
        self.min_depth_ft = min(self.min_depth_ft, depth_ft)
        self.max_depth_ft = max(self.max_depth_ft, depth_ft)


def find_shapefile(extract_dir: Path) -> Path:
    matches = list(extract_dir.rglob("*.shp"))
    if not matches:
        raise FileNotFoundError("No shapefile found in Missouri bathymetry ZIP")
    return matches[0]


def get_field_names(reader: shapefile.Reader) -> list[str]:
    return [field[0] for field in reader.fields[1:]]


def compute_max_elevation(reader: shapefile.Reader, elev_field: str) -> float:
    max_elev = -math.inf
    for record in reader.iterRecords():
        elev = float(record[elev_field])
        if elev > max_elev:
            max_elev = elev
    if not math.isfinite(max_elev):
        raise ValueError("Could not determine maximum elevation from Missouri points")
    return max_elev


def aggregate_cells(
    reader: shapefile.Reader,
    elev_field: str,
    grid_size_m: float,
    max_elev: float,
) -> dict[tuple[int, int], CellStats]:
    cells: dict[tuple[int, int], CellStats] = {}
    for shape_record in reader.iterShapeRecords():
        x, y = shape_record.shape.points[0]
        elev = float(shape_record.record[elev_field])
        depth_ft = max(0.0, max_elev - elev)
        key = (int(x // grid_size_m), int(y // grid_size_m))
        stats = cells.setdefault(key, CellStats())
        stats.add(x, y, depth_ft)
    return cells


def build_features(
    cells: dict[tuple[int, int], CellStats],
    source_name: str,
    grid_size_m: float,
) -> list[dict]:
    transformer = Transformer.from_crs("EPSG:26915", "EPSG:4326", always_xy=True)
    features = []
    for idx, stats in enumerate(cells.values(), start=1):
        if stats.count <= 0:
            continue
        x = stats.sum_x / stats.count
        y = stats.sum_y / stats.count
        lon, lat = transformer.transform(x, y)
        depth_ft = round(stats.sum_depth_ft / stats.count, 1)
        depth_m = round(depth_ft * FT_TO_M, 3)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "OBJECTID": idx,
                    "LAKE_NAME": source_name,
                    "LAKE_ID": "mo-clearwater-lake",
                    "DEPTH_FT": depth_ft,
                    "DEPTH_M": depth_m,
                    "SAMPLE_COUNT": stats.count,
                    "MIN_DEPTH_FT": round(stats.min_depth_ft, 1),
                    "MAX_DEPTH_FT": round(stats.max_depth_ft, 1),
                    "GRID_M": grid_size_m,
                },
            }
        )
    return features


def extract_points(zip_path: Path, output_path: Path, grid_size_m: float, source_name: str) -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(temp_path)

        shapefile_path = find_shapefile(temp_path)
        reader = shapefile.Reader(str(shapefile_path))
        fields = get_field_names(reader)
        elev_field = "ELEV_F" if "ELEV_F" in fields else fields[2]
        max_elev = compute_max_elevation(reader, elev_field)

        reader = shapefile.Reader(str(shapefile_path))
        cells = aggregate_cells(reader, elev_field, grid_size_m, max_elev)
        features = build_features(cells, source_name, grid_size_m)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump({"type": "FeatureCollection", "features": features}, handle)
    return len(features)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--zip-path",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/mo/clearwater_mb_1m_pts.zip"),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/mo/mo_clearwater_depth_points.geojson"),
    )
    parser.add_argument("--grid-size-m", type=float, default=100.0)
    parser.add_argument("--source-name", default="Clearwater Lake")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    count = extract_points(args.zip_path, args.output_path, args.grid_size_m, args.source_name)
    print(f"Wrote {count} Missouri depth-point features to {args.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
