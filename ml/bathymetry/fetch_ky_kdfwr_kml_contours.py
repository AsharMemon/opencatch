#!/usr/bin/env python3
"""
Fetch matched Kentucky KDFWR contour KMLs and convert them into a run-root that
the existing packaging pipeline can consume.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests


NS = {"kml": "http://www.opengis.net/kml/2.2"}
USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-csv", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--sleep-ms", type=int, default=25)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "lake"


def fetch_text(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def parse_coordinates(text: str) -> list[list[float]]:
    coords: list[list[float]] = []
    for token in text.replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        coords.append([lon, lat])
    return coords


def parse_depth_ft(name: str) -> float | None:
    if not name:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)", name)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def extract_contour_features(kml_text: str, lake_name: str, kml_url: str) -> list[dict[str, Any]]:
    root = ET.fromstring(kml_text)
    features: list[dict[str, Any]] = []
    for placemark in root.findall(".//kml:Placemark", NS):
        name_el = placemark.find("kml:name", NS)
        name = (name_el.text or "").strip() if name_el is not None and name_el.text else ""
        depth_ft = parse_depth_ft(name)
        if depth_ft is None:
            continue
        for line in placemark.findall(".//kml:LineString/kml:coordinates", NS):
            if line.text is None:
                continue
            coords = parse_coordinates(line.text)
            if len(coords) < 2:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "lake_name": lake_name,
                        "depth_ft": depth_ft,
                        "depth_m": round(depth_ft * 0.3048, 3),
                        "feature_kind": "contour_line",
                        "quality_score": 1.0,
                        "source_name": "ky_kdfwr_kml",
                        "source_url": kml_url,
                    },
                }
            )
    return features


def main() -> None:
    args = parse_args()
    rows = read_csv(args.inventory_csv)
    digitized_dir = args.run_root / "digitized"
    raw_dir = args.run_root / "raw_kml"
    digitized_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    quality_rows: list[dict[str, str]] = []
    summary_rows: list[dict[str, Any]] = []

    for idx, row in enumerate(rows, start=1):
        lake_name = (row.get("lake_name") or "").strip()
        kml_url = (row.get("kml_url") or "").strip()
        if not lake_name or not kml_url:
            continue
        slug = slugify(lake_name)
        kml_text = fetch_text(kml_url)
        (raw_dir / f"{slug}.kml").write_text(kml_text, encoding="utf-8")
        features = extract_contour_features(kml_text, lake_name, kml_url)
        out_path = digitized_dir / f"{slug}.geojson"
        out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
        quality_rows.append({"file_name": f"{slug}.geojson", "score": "1.0"})
        summary_rows.append(
            {
                "lake_name": lake_name,
                "kml_url": kml_url,
                "feature_count": len(features),
                "output_geojson": str(out_path),
            }
        )
        if idx % 10 == 0 or idx == len(rows):
            print(f"processed {idx}/{len(rows)} Kentucky KML lakes")
        if args.sleep_ms > 0:
            time.sleep(args.sleep_ms / 1000.0)

    with (digitized_dir / "_quality_report.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file_name", "score"])
        writer.writeheader()
        writer.writerows(quality_rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inventory_csv": str(args.inventory_csv),
        "run_root": str(args.run_root),
        "lake_count": len(summary_rows),
        "feature_count": sum(int(row["feature_count"]) for row in summary_rows),
        "examples": summary_rows[:10],
    }
    (args.run_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
