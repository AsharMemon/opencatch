#!/usr/bin/env python3
"""
Build a scene manifest for ACOLITE-corrected Sentinel-2 rasters.

The Payandeh-style replication script accepts a CSV/parquet manifest with one
row per scene and per-band file paths. This helper scans an ACOLITE output
directory and assembles that manifest automatically when filenames encode
scene IDs and wavelengths/band names.

It is intentionally permissive: it looks for common Sentinel-2 band aliases
and ACOLITE wavelength markers in filenames.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional

import pandas as pd


BAND_PATTERNS = {
    "blue": [
        r"\bblue\b",
        r"\bb02\b",
        r"[_\-]490(?:nm)?\b",
        r"[_\-]492(?:nm)?\b",
        r"rhos_49[02]\b",
    ],
    "green": [
        r"\bgreen\b",
        r"\bb03\b",
        r"[_\-]560(?:nm)?\b",
        r"rhos_560\b",
    ],
    "red": [
        r"\bred\b",
        r"\bb04\b",
        r"[_\-]665(?:nm)?\b",
        r"rhos_665\b",
    ],
    "rededge1": [
        r"\brededge1\b",
        r"\bb05\b",
        r"[_\-]704(?:nm)?\b",
        r"[_\-]705(?:nm)?\b",
        r"rhos_70[45]\b",
    ],
    "rededge2": [
        r"\brededge2\b",
        r"\bb06\b",
        r"[_\-]739(?:nm)?\b",
        r"[_\-]740(?:nm)?\b",
        r"rhos_74[09]\b",
    ],
    "rededge3": [
        r"\brededge3\b",
        r"\bb07\b",
        r"[_\-]779(?:nm)?\b",
        r"[_\-]783(?:nm)?\b",
        r"rhos_78[03]\b",
    ],
    "nir": [
        r"\bnir\b",
        r"\bb08\b",
        r"[_\-]833(?:nm)?\b",
        r"[_\-]842(?:nm)?\b",
        r"rhos_83[32]\b",
        r"rhos_842\b",
    ],
    "swir16": [
        r"\bswir16\b",
        r"\bb11\b",
        r"[_\-]1610(?:nm)?\b",
        r"[_\-]1614(?:nm)?\b",
        r"rhos_161[04]\b",
    ],
}

DATE_PATTERNS = [
    re.compile(r"(20\d{2}[01]\d[0-3]\d)"),
    re.compile(r"(20\d{2}_\d{2}_\d{2})"),
    re.compile(r"(20\d{2}-\d{2}-\d{2})"),
]


def match_band(name: str) -> Optional[str]:
    lower = name.lower()
    for band, patterns in BAND_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, lower):
                return band
    return None


def extract_scene_date(name: str) -> str:
    for pattern in DATE_PATTERNS:
        match = pattern.search(name)
        if match:
            token = match.group(1)
            if "_" in token:
                return token.replace("_", "-")
            if len(token) == 8 and token.isdigit():
                return f"{token[:4]}-{token[4:6]}-{token[6:]}"
            return token
    return ""


def derive_scene_id(name: str, band: str) -> str:
    lower = name.lower()
    patterns = BAND_PATTERNS[band]
    cut = None
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            cut = match.start()
            break
    scene = name[:cut] if cut is not None else name
    scene = re.sub(r"[_\-\.]+$", "", scene)
    return scene


def build_manifest(input_dir: Path, recursive: bool) -> pd.DataFrame:
    rows: dict[str, dict[str, str]] = {}
    iterator = input_dir.rglob("*") if recursive else input_dir.glob("*")
    for path in iterator:
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".tif", ".tiff", ".jp2"}:
            continue

        band = match_band(path.name)
        if band is None:
            continue

        scene_id = derive_scene_id(path.stem, band)
        row = rows.setdefault(
            scene_id,
            {
                "scene_id": scene_id,
                "scene_date": extract_scene_date(path.name),
                "input_scale": 1.0,
                "source": "acolite_manifest_builder",
            },
        )
        row[band] = str(path.resolve())

    manifest = pd.DataFrame(rows.values())
    if manifest.empty:
        raise RuntimeError(f"No ACOLITE-style band files found in {input_dir}")

    required = ["blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir"]
    manifest = manifest.dropna(subset=required)
    manifest = manifest.sort_values(["scene_date", "scene_id"]).reset_index(drop=True)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ACOLITE scene manifest for Payandeh-style replication")
    parser.add_argument("--input", type=Path, required=True, help="Directory containing ACOLITE outputs")
    parser.add_argument("--output", type=Path, required=True, help="CSV or parquet manifest path")
    parser.add_argument("--recursive", action="store_true", help="Scan subdirectories recursively")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_manifest(args.input, recursive=args.recursive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.suffix.lower() == ".parquet":
        manifest.to_parquet(args.output, index=False)
    else:
        manifest.to_csv(args.output, index=False)
    print(f"wrote {len(manifest)} scenes -> {args.output}")


if __name__ == "__main__":
    main()
