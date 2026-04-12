#!/usr/bin/env python3
"""
Build generic overlay PMTiles from GeoJSON feature collections.

This is like `build_survey_pmtiles.py`, but it is intended for navigation /
marine overlays rather than bathymetry contours. The output layer name is
configurable so Martin / MapLibre can render overlays without overloading the
`contours` layer name.

Usage:
    python build_overlay_pmtiles.py \
      --input /path/to/noaa_marine_navigation.geojson \
      --output /path/to/noaa_marine_navigation.pmtiles \
      --layer marine_navigation
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from pmtiles_cli import ensure_pmtiles_cli


def find_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (
        f"/Users/Ashar/Library/Python/3.9/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
        f"/usr/bin/{name}",
    ):
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"Required binary not found: {name}")


def to_geojsonl(input_path: Path, output_path: Path) -> int:
    data = json.loads(input_path.read_text())
    features = data.get("features", []) if isinstance(data, dict) else []
    count = 0
    with output_path.open("w", encoding="utf-8") as fh:
        for feature in features:
            json.dump(feature, fh, ensure_ascii=True)
            fh.write("\n")
            count += 1
    return count


def build_pmtiles(input_geojsonl: Path, output_path: Path, layer: str, name: str, description: str, min_zoom: int, max_zoom: int) -> None:
    tippecanoe_bin = find_binary("tippecanoe")
    try:
        pmtiles_bin = find_binary("pmtiles")
        pmtiles_args = [pmtiles_bin, "convert", "--force", "--tmpdir"]
    except FileNotFoundError:
        try:
            pmtiles_bin = find_binary("pmtiles-convert")
            pmtiles_args = [pmtiles_bin, "--overwrite"]
        except FileNotFoundError:
            pmtiles_bin = ensure_pmtiles_cli()
            pmtiles_args = [pmtiles_bin, "convert", "--force", "--tmpdir"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="overlay-pmtiles-") as tmp:
        tmpdir = Path(tmp)
        mbtiles_path = tmpdir / f"{output_path.stem}.mbtiles"
        tippecanoe_cmd = [
            tippecanoe_bin,
            f"--minimum-zoom={min_zoom}",
            f"--maximum-zoom={max_zoom}",
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            "--read-parallel",
            "--no-tile-compression",
            "--force",
            f"--layer={layer}",
            f"--name={name}",
            f"--description={description}",
            "--attribution=OpenCatch",
            "-o",
            str(mbtiles_path),
            str(input_geojsonl),
        ]
        subprocess.run(tippecanoe_cmd, check=True)
        pmtiles_cmd = (
            pmtiles_args + [str(tmpdir), str(mbtiles_path), str(output_path)]
            if "--tmpdir" in pmtiles_args
            else pmtiles_args + [str(mbtiles_path), str(output_path)]
        )
        subprocess.run(pmtiles_cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build generic overlay PMTiles from GeoJSON.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layer", default="overlay")
    parser.add_argument("--name", default="OpenCatch Overlay")
    parser.add_argument("--description", default="OpenCatch overlay tiles")
    parser.add_argument("--min-zoom", type=int, default=4)
    parser.add_argument("--max-zoom", type=int, default=14)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="overlay-geojsonl-") as tmp:
        geojsonl_path = Path(tmp) / f"{args.output.stem}.geojsonl"
        count = to_geojsonl(args.input, geojsonl_path)
        build_pmtiles(
            geojsonl_path,
            args.output,
            args.layer,
            args.name,
            args.description,
            args.min_zoom,
            args.max_zoom,
        )
    print(json.dumps({"output": str(args.output), "feature_count": count}, indent=2))


if __name__ == "__main__":
    main()
