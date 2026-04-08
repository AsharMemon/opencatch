#!/usr/bin/env python3
"""
Build PMTiles from normalized survey GeoJSON outputs.

This uses the manifest produced by `normalize_survey_geojson.py`, combines the
normalized contour features plus any label points into a single GeoJSON
FeatureCollection, renders an intermediate MBTiles archive with tippecanoe, and
then converts that archive into a real PMTiles v3 file.

The resulting files are named `<source>_contours.pmtiles`, which matches the
existing Martin registry and mobile contour source ids.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List

from normalize_survey_geojson import iter_geojson_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("build_survey_pmtiles")


def find_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
        f"/usr/bin/{name}",
    ):
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"Required binary not found: {name}")


def load_manifest(path: Path) -> List[dict]:
    return json.loads(path.read_text())


def combine_geojson(inputs: List[Path], output_path: Path) -> int:
    count = 0
    with output_path.open("w", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","features":[\n')
        first = True
        for path in inputs:
            if not path.exists():
                continue
            for feature in iter_geojson_features(path):
                if not first:
                    out.write(",\n")
                json.dump(feature, out, ensure_ascii=True)
                first = False
                count += 1
        out.write("\n]}\n")
    return count


def build_pmtiles(input_geojson: Path, output_path: Path, min_zoom: int, max_zoom: int, tmpdir: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mbtiles_path = tmpdir / f"{output_path.stem}.mbtiles"
    tippecanoe_bin = find_binary("tippecanoe")
    pmtiles_bin = find_binary("pmtiles")
    cmd = [
        tippecanoe_bin,
        f"--minimum-zoom={min_zoom}",
        f"--maximum-zoom={max_zoom}",
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        "--read-parallel",
        "--no-tile-compression",
        "--force",
        "--layer=contours",
        "--name=OpenCatch Contours",
        "--description=Survey-backed bathymetry contour tiles for OpenCatch",
        "--attribution=OpenCatch",
        "-o",
        str(mbtiles_path),
        str(input_geojson),
    ]
    log.info("Running %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    convert_cmd = [
        pmtiles_bin,
        "convert",
        "--force",
        "--tmpdir",
        str(tmpdir),
        str(mbtiles_path),
        str(output_path),
    ]
    log.info("Running %s", " ".join(convert_cmd))
    subprocess.run(convert_cmd, check=True)


def select_tile_ready(manifest: List[dict], selected: List[str] | None) -> List[dict]:
    ready = [entry for entry in manifest if entry.get("tile_ready")]
    if selected:
        selected_set = set(selected)
        ready = [entry for entry in ready if entry.get("source_id") in selected_set]
    return ready


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Martin-ready PMTiles from normalized survey outputs.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/normalized/manifest.json"),
    )
    parser.add_argument(
        "--tile-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/infra/martin/tiles"),
    )
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated source ids or 'all'",
    )
    parser.add_argument("--min-zoom", type=int, default=5)
    parser.add_argument("--max-zoom", type=int, default=16)
    parser.add_argument("--keep-combined", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    selected = None if args.sources == "all" else [s.strip() for s in args.sources.split(",") if s.strip()]
    targets = select_tile_ready(manifest, selected)
    if not targets:
        raise SystemExit("No tile-ready sources selected.")

    args.tile_dir.mkdir(parents=True, exist_ok=True)

    for entry in targets:
        source_id = entry["source_id"]
        outputs: Dict[str, str] = entry.get("outputs") or {}
        normalized = Path(outputs["normalized"])
        labels = Path(outputs["labels"]) if outputs.get("labels") else None

        with tempfile.TemporaryDirectory(prefix=f"{source_id}_tiles_") as tmp:
            combined = Path(tmp) / f"{source_id}_combined.geojson"
            inputs = [normalized] + ([labels] if labels else [])
            feature_count = combine_geojson([p for p in inputs if p], combined)
            log.info("%s combined feature count: %s", source_id, feature_count)

            out_path = args.tile_dir / f"{source_id}_contours.pmtiles"
            build_pmtiles(combined, out_path, args.min_zoom, args.max_zoom, Path(tmp))
            log.info("%s -> %s (%.1f MB)", source_id, out_path, out_path.stat().st_size / 1e6)

            if args.keep_combined:
                keep_path = args.tile_dir / f"{source_id}_combined.geojson"
                keep_path.write_text(combined.read_text())


if __name__ == "__main__":
    main()
