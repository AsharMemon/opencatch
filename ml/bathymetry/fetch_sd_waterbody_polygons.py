#!/usr/bin/env python3
"""
Download official South Dakota lake and reservoir polygons.

This gives us a state-native polygon reference for georeferencing South Dakota
lake bathymetry PDFs, which is much better than relying on HydroLAKES or other
coarse cross-region lake catalogs.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


SERVICE_ROOT = "https://arcgis.sd.gov/arcgis/rest/services/SD_All/NaturalResource_Lakes_and_Reservoirs/MapServer"


def build_base_url(layer: int) -> str:
    return f"{SERVICE_ROOT}/{layer}"


def fetch_json(url: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_service_metadata(base_url: str) -> dict:
    return fetch_json(f"{base_url}?f=pjson")


def fetch_feature_count(base_url: str) -> int:
    obj = fetch_json(f"{base_url}/query?where=1%3D1&returnCountOnly=true&f=json")
    return int(obj["count"])


def query_page(base_url: str, offset: int, limit: int, out_fields: str = "*") -> dict:
    params = {
        "where": "1=1",
        "outFields": out_fields,
        "returnGeometry": "true",
        "outSR": "4326",
        "resultOffset": str(offset),
        "resultRecordCount": str(limit),
        "f": "geojson",
    }
    url = f"{base_url}/query?{urllib.parse.urlencode(params)}"
    return fetch_json(url)


def download_all(
    output_geojson: Path,
    output_meta: Path,
    layer: int,
    page_size: int = 1000,
    sleep_s: float = 0.2,
) -> dict:
    base_url = build_base_url(layer)
    meta = fetch_service_metadata(base_url)
    total = fetch_feature_count(base_url)
    features = []

    for offset in range(0, total, page_size):
        page = query_page(base_url, offset, page_size)
        batch = page.get("features", [])
        features.extend(batch)
        print(f"Fetched {len(features):,}/{total:,} features")
        if sleep_s:
            time.sleep(sleep_s)

    fc = {
        "type": "FeatureCollection",
        "features": features,
    }
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    output_geojson.write_text(json.dumps(fc))

    summary = {
        "base_url": base_url,
        "layer": layer,
        "feature_count": total,
        "downloaded_features": len(features),
        "page_size": page_size,
        "service_name": meta.get("name"),
        "geometry_type": meta.get("geometryType"),
        "max_record_count": meta.get("maxRecordCount"),
        "spatial_reference": meta.get("extent", {}).get("spatialReference"),
        "service_metadata": meta,
    }
    output_meta.write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-geojson",
        default="/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_waterbody_polygons.geojson",
    )
    parser.add_argument(
        "--output-meta",
        default="/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_waterbody_polygons.metadata.json",
    )
    parser.add_argument("--layer", type=int, default=1, help="ArcGIS layer id (1=Big Waterbodies, 2=All Waterbodies)")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--sleep-s", type=float, default=0.2)
    args = parser.parse_args()

    summary = download_all(
        output_geojson=Path(args.output_geojson),
        output_meta=Path(args.output_meta),
        layer=args.layer,
        page_size=args.page_size,
        sleep_s=args.sleep_s,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
