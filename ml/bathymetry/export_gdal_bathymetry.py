#!/usr/bin/env python3
"""
Export heavier survey sources (FGDB/shapefile bundles) to GeoJSON via GDAL.

This keeps the main normalizer simple: once a source is exported to a clean
EPSG:4326 GeoJSON, `normalize_survey_geojson.py` can treat it like the lighter
sources already on disk.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("export_gdal_bathymetry")

WORKSPACE = Path("/Users/Ashar/Documents/fish")
GDAL_IMAGE = "ghcr.io/osgeo/gdal:alpine-small-latest"


@dataclass(frozen=True)
class ExportConfig:
    source_id: str
    source_path: str
    layer_name: str
    output_name: str


EXPORTS: Dict[str, ExportConfig] = {
    "ma": ExportConfig(
        source_id="ma",
        source_path="/workspace/data/bathymetry/ma/ma_bathy.zip",
        layer_name="DFWBATHY_ARC",
        output_name="ma_contours.geojson",
    ),
    "mt": ExportConfig(
        source_id="mt",
        source_path="/workspace/data/bathymetry/mt/mt_data.zip",
        layer_name="lakesBathymetry",
        output_name="mt_contours.geojson",
    ),
    "on": ExportConfig(
        source_id="on",
        source_path="/workspace/data/bathymetry/on_lines/on_lines_data.zip/Non_Sensitive.gdb",
        layer_name="BATHYMETRY_LINE",
        output_name="on_contours.geojson",
    ),
    "qc": ExportConfig(
        source_id="qc",
        source_path="/workspace/data/bathymetry/qc/qc_data.zip/GBLQ.gdb",
        layer_name="isobathes_l",
        output_name="qc_contours.geojson",
    ),
    "wa": ExportConfig(
        source_id="wa",
        source_path="/workspace/data/bathymetry/wa/wa_data.zip/LakeBathymetry.gdb",
        layer_name="LakeBathymetryLine",
        output_name="wa_contours.geojson",
    ),
}


def docker_source_path(config: ExportConfig) -> str:
    if config.source_path.endswith(".zip"):
        return f"/vsizip/{config.source_path}"
    if ".zip/" in config.source_path:
        zip_path, inner = config.source_path.split(".zip/", 1)
        return f"/vsizip/{zip_path}.zip/{inner}"
    return config.source_path


def docker_output_path(output_path: Path) -> str:
    return f"/workspace/{output_path.relative_to(WORKSPACE)}"


def build_cmd(config: ExportConfig, output_path: Path) -> List[str]:
    source = docker_source_path(config)
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{WORKSPACE}:/workspace",
        GDAL_IMAGE,
        "ogr2ogr",
        "-f",
        "GeoJSON",
        "-t_srs",
        "EPSG:4326",
        "-dim",
        "XY",
        "-lco",
        "RFC7946=YES",
        docker_output_path(output_path),
        source,
        config.layer_name,
    ]


def export_source(config: ExportConfig, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / config.output_name
    if output_path.exists():
        output_path.unlink()

    cmd = build_cmd(config, output_path)
    log.info("Exporting %s -> %s", config.source_id, output_path)
    subprocess.run(cmd, check=True)
    log.info("%s complete (%.1f MB)", config.source_id, output_path.stat().st_size / 1e6)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export heavier bathymetry sources to GeoJSON via GDAL.")
    parser.add_argument("--sources", default="ma,mt,on,qc,wa", help="Comma-separated source ids or 'all'")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=WORKSPACE / "data" / "bathymetry" / "gdal_exports",
    )
    args = parser.parse_args()

    selected = list(EXPORTS) if args.sources == "all" else [s.strip() for s in args.sources.split(",") if s.strip()]
    for source_id in selected:
        config = EXPORTS.get(source_id)
        if config is None:
            log.warning("Unknown export source: %s", source_id)
            continue
        export_source(config, args.output_dir)


if __name__ == "__main__":
    main()
