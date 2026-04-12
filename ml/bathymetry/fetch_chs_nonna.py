#!/usr/bin/env python3
"""
OpenCatch — CHS NONNA acquisition

Builds a local manifest for official CHS NONNA resources:
  - Open Canada dataset page
  - NONNA WMS / WMTS / WCS capabilities
  - NONNA10 ZIP attachment

Usage:
    python fetch_chs_nonna.py --output /data/bathymetry/chs_nonna --download-nonna10
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("fetch_chs_nonna")

NONNA_DATASET_URL = "https://open.canada.ca/data/en/dataset/d3881c4c-650d-4070-bf9b-1e00aabf0a1d"
NONNA10_ZIP_URL = "https://api-proxy.edh-cde.dfo-mpo.gc.ca/catalogue/records/d3881c4c-650d-4070-bf9b-1e00aabf0a1d/attachments/NONNA10.zip"
NONNA_WMS = "https://nonna-geoserver.data.chs-shc.ca/geoserver/wms?request=GetCapabilities"
NONNA_WMTS = "https://nonna-geoserver.data.chs-shc.ca/geoserver/gwc/service/wmts?request=GetCapabilities"
NONNA_WCS = "https://nonna-geoserver.data.chs-shc.ca/geoserver/wcs?request=GetCapabilities"
USER_AGENT = "OpenCatch/1.0 (contact@opencatch.app)"
REQUEST_TIMEOUT = 120


def fetch_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.content


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=REQUEST_TIMEOUT, stream=True, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fh.write(chunk)
    return dest


def parse_wms_layers(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    ns = {"wms": "http://www.opengis.net/wms"}
    layers = []
    for name in root.findall(".//wms:Layer/wms:Name", ns):
        if name.text:
            layers.append(name.text)
    return sorted(set(layers))


def parse_wmts_layers(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    ns = {
        "wmts": "http://www.opengis.net/wmts/1.0",
        "ows": "http://www.opengis.net/ows/1.1",
    }
    layers = []
    for identifier in root.findall(".//wmts:Layer/ows:Identifier", ns):
        if identifier.text:
            layers.append(identifier.text)
    return sorted(set(layers))


def parse_wcs_coverages(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    ns = {
        "wcs": "http://www.opengis.net/wcs/2.0",
        "wcs111": "http://www.opengis.net/wcs/1.1",
        "wcs100": "http://www.opengis.net/wcs",
        "ows": "http://www.opengis.net/ows/2.0",
        "ows11": "http://www.opengis.net/ows/1.1",
    }
    coverages = []
    for path in [
        ".//wcs:CoverageSummary/ows:Title",
        ".//wcs111:CoverageSummary/ows11:Title",
        ".//wcs100:CoverageOfferingBrief/wcs100:name",
    ]:
        for item in root.findall(path, ns):
            if item.text:
                coverages.append(item.text)
    return sorted(set(coverages))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch CHS NONNA capabilities, manifest, and optional NONNA10 ZIP.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/chs_nonna"),
    )
    parser.add_argument(
        "--download-nonna10",
        action="store_true",
        help="Download the official NONNA10 ZIP attachment from Open Canada.",
    )
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    wms_xml = fetch_bytes(NONNA_WMS)
    wmts_xml = fetch_bytes(NONNA_WMTS)
    wcs_xml = fetch_bytes(NONNA_WCS)

    (output_dir / "nonna_wms_capabilities.xml").write_bytes(wms_xml)
    (output_dir / "nonna_wmts_capabilities.xml").write_bytes(wmts_xml)
    (output_dir / "nonna_wcs_capabilities.xml").write_bytes(wcs_xml)

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "Canadian Hydrographic Service NONNA",
        "dataset_url": NONNA_DATASET_URL,
        "resources": {
            "wms": NONNA_WMS,
            "wmts": NONNA_WMTS,
            "wcs": NONNA_WCS,
            "nonna10_zip": NONNA10_ZIP_URL,
        },
        "wms_layers": parse_wms_layers(wms_xml),
        "wmts_layers": parse_wmts_layers(wmts_xml),
        "wcs_coverages": parse_wcs_coverages(wcs_xml),
    }

    if args.download_nonna10:
        zip_path = output_dir / "downloads" / "NONNA10.zip"
        if not zip_path.exists():
            log.info("Downloading CHS NONNA10 ZIP...")
            download_file(NONNA10_ZIP_URL, zip_path)
        manifest["download"] = {
            "path": str(zip_path),
            "bytes": zip_path.stat().st_size,
        }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"manifest": str(manifest_path)}, indent=2))


if __name__ == "__main__":
    main()
