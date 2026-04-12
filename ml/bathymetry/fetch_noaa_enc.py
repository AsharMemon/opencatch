#!/usr/bin/env python3
"""
OpenCatch — NOAA ENC / marine transportation acquisition

Fetches official NOAA ENC Direct-to-GIS resources and writes a local manifest
that the coastal/ocean pipeline can use for acquisition and attribution.

This script focuses on the official public NOAA sources that matter most for
OpenCatch:
  - ENC Direct scale-band services
  - Coastal Maintained Channels shapefile
  - Shipping Lanes and Regulations shapefile
  - U.S. Maritime Limits & Boundaries shapefile

Usage:
    python fetch_noaa_enc.py --output /data/bathymetry/noaa_enc --download-themes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("fetch_noaa_enc")

REQUEST_TIMEOUT = 120
USER_AGENT = "OpenCatch/1.0 (contact@opencatch.app)"

SCALE_BANDS: Dict[str, Dict[str, str]] = {
    "overview": {
        "map_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_overview/MapServer",
        "gp_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_gp_overview/GPServer",
        "wms": "https://encdirect.noaa.gov/arcgis/services/encdirect/enc_overview/MapServer/WMSServer?request=GetCapabilities&service=WMS",
    },
    "general": {
        "map_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_general/MapServer",
        "gp_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_gp_general/GPServer",
        "wms": "https://encdirect.noaa.gov/arcgis/services/encdirect/enc_general/MapServer/WMSServer?request=GetCapabilities&service=WMS",
    },
    "coastal": {
        "map_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_coastal/MapServer",
        "gp_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_gp_coastal/GPServer",
        "wms": "https://encdirect.noaa.gov/arcgis/services/encdirect/enc_coastal/MapServer/WMSServer?request=GetCapabilities&service=WMS",
    },
    "approach": {
        "map_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_approach/MapServer",
        "gp_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_gp_approach/GPServer",
        "wms": "https://encdirect.noaa.gov/arcgis/services/encdirect/enc_approach/MapServer/WMSServer?request=GetCapabilities&service=WMS",
    },
    "harbor": {
        "map_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_harbor/MapServer",
        "gp_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_gp_harbor/GPServer",
        "wms": "https://encdirect.noaa.gov/arcgis/services/encdirect/enc_harbor/MapServer/WMSServer?request=GetCapabilities&service=WMS",
    },
    "berthing": {
        "map_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_berthing/MapServer",
        "gp_service": "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/enc_gp_berthing/GPServer",
        "wms": "https://encdirect.noaa.gov/arcgis/services/encdirect/enc_berthing/MapServer/WMSServer?request=GetCapabilities&service=WMS",
    },
}

THEME_LAYERS: Dict[str, Dict[str, str]] = {
    "coastal_maintained_channels": {
        "title": "Coastal Maintained Channels",
        "rest": "https://gis.charttools.noaa.gov/arcgis/rest/services/NavigationChartData/MarineTransportation/MapServer/1",
        "wms": "https://gis.charttools.noaa.gov/arcgis/services/NavigationChartData/MarineTransportation/MapServer/WMSServer?request=GetCapabilities&service=WMS",
        "shapefile": "https://encdirect.noaa.gov/theme_layers/data/coastal_maintained_channels/maintainedchannels.zip",
        "metadata": "https://inport.nmfs.noaa.gov/inport/item/39972",
    },
    "shipping_lanes_and_regulations": {
        "title": "Shipping Lanes and Regulations",
        "rest": "https://gis.charttools.noaa.gov/arcgis/rest/services/NavigationChartData/MarineTransportation/MapServer/0",
        "wms": "https://encdirect.noaa.gov/arcgis/services/NavigationChartData/MarineTransportation/MapServer/WMSServer?request=GetCapabilities&service=WMS",
        "shapefile": "https://encdirect.noaa.gov/theme_layers/data/shipping_lanes/shippinglanes.zip",
        "metadata": "https://inport.nmfs.noaa.gov/inport/item/39986",
    },
    "us_maritime_limits_and_boundaries": {
        "title": "U.S. Maritime Limits & Boundaries",
        "rest": "https://maritimeboundaries.noaa.gov/arcgis/rest/services/MaritimeBoundaries/US_Maritime_Limits_Boundaries/MapServer",
        "wms": "https://maritimeboundaries.noaa.gov/arcgis/services/MaritimeBoundaries/US_Maritime_Limits_Boundaries/MapServer/WMSServer?request=GetCapabilities&service=WMS",
        "shapefile": "https://maritimeboundaries.noaa.gov/downloads/USMaritimeLimitsAndBoundariesSHP.zip",
        "metadata": "https://inport.nmfs.noaa.gov/inport/item/39963",
    },
}


def fetch_text(url: str) -> str:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def fetch_json(url: str) -> dict:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        stream=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fh.write(chunk)
    return dest


def collect_scale_band_manifest() -> List[dict]:
    manifest = []
    for name, urls in SCALE_BANDS.items():
        entry = {"name": name, **urls}
        try:
            meta = fetch_json(f"{urls['map_service']}?f=pjson")
            entry["service_description"] = meta.get("serviceDescription")
            entry["copyright"] = meta.get("copyrightText")
            entry["full_extent"] = meta.get("fullExtent")
            entry["spatial_reference"] = meta.get("spatialReference")
        except Exception as exc:  # pragma: no cover - network-dependent
            entry["error"] = str(exc)
        manifest.append(entry)
    return manifest


def collect_theme_manifest() -> List[dict]:
    manifest = []
    for key, urls in THEME_LAYERS.items():
        entry = {"id": key, **urls}
        try:
            meta = fetch_json(f"{urls['rest']}?f=pjson")
            entry["geometry_type"] = meta.get("geometryType")
            entry["fields"] = [field.get("name") for field in meta.get("fields", [])]
            entry["extent"] = meta.get("extent")
            entry["max_record_count"] = meta.get("maxRecordCount")
        except Exception as exc:  # pragma: no cover - network-dependent
            entry["error"] = str(exc)
        manifest.append(entry)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch NOAA ENC official acquisition metadata and theme layers.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/noaa_enc"),
    )
    parser.add_argument(
        "--download-themes",
        action="store_true",
        help="Download official NOAA theme-layer shapefiles locally.",
    )
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    scale_bands = collect_scale_band_manifest()
    themes = collect_theme_manifest()

    downloads = []
    if args.download_themes:
        download_dir = output_dir / "downloads"
        for key, urls in THEME_LAYERS.items():
            dest = download_dir / f"{key}.zip"
            if dest.exists() and dest.stat().st_size > 0:
                log.info("Skipping existing NOAA theme download: %s", dest.name)
            else:
                log.info("Downloading NOAA theme layer: %s", key)
                download_file(urls["shapefile"], dest)
                time.sleep(0.2)
            downloads.append(
                {
                    "id": key,
                    "path": str(dest),
                    "bytes": dest.stat().st_size if dest.exists() else 0,
                }
            )

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "NOAA ENC Direct to GIS / NOAA GIS Data & Services",
        "scale_bands": scale_bands,
        "theme_layers": themes,
        "downloads": downloads,
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"manifest": str(manifest_path), "theme_count": len(themes)}, indent=2))


if __name__ == "__main__":
    main()
