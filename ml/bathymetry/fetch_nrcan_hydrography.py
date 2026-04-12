#!/usr/bin/env python3
"""
Fetch and validate official NRCan / GeoBase hydrography metadata for
Canadian river-network completeness.

This captures the canonical National Hydro Network (NHN) distribution and
service references so the coverage stack can treat Canadian hydrography as an
official source even before full national tiling is complete.

Usage:
  python fetch_nrcan_hydrography.py \
    --output /Users/Ashar/Documents/fish/data/bathymetry/nrcan_hydrography
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import requests


USER_AGENT = "OpenCatch/1.0 (contact@opencatch.app)"
TIMEOUT_S = 30

DOWNLOAD_DIRECTORY_URL = (
    "https://natural-resources.canada.ca/science-data/science-research/geomatics/"
    "download-directory-documentation"
)
NHN_FTP_BASE = "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/"
WEB_SERVICES_URL = "https://natural-resources.canada.ca/node/17216?=undefined&wbdisable=true"


def fetch_text(url: str) -> str:
    resp = requests.get(url, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def fetch_head(url: str) -> dict:
    resp = requests.head(url, timeout=TIMEOUT_S, allow_redirects=True, headers={"User-Agent": USER_AGENT})
    return {
        "url": url,
        "status_code": resp.status_code,
        "final_url": str(resp.url),
        "content_type": resp.headers.get("content-type"),
    }


def list_directory(url: str) -> list[str]:
    text = fetch_text(url)
    return re.findall(r'href="([^"]+)"', text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch official NRCan hydrography metadata.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    download_directory_html = fetch_text(DOWNLOAD_DIRECTORY_URL)
    (output_dir / "download_directory.html").write_text(download_directory_html)

    head_checks = {
        "nhn_ftp_base": fetch_head(NHN_FTP_BASE),
        "web_services_page": fetch_head(WEB_SERVICES_URL),
    }
    (output_dir / "head_checks.json").write_text(json.dumps(head_checks, indent=2))

    gdb_root = f"{NHN_FTP_BASE}gdb_en/"
    gdb_root_links = list_directory(gdb_root)
    region_dirs = sorted({href.rstrip("/") for href in gdb_root_links if re.fullmatch(r"[0-9]{2}/", href)})
    sample_packages: dict[str, list[str]] = {}
    for region in region_dirs[:3]:
        region_url = f"{gdb_root}{region}/"
        region_links = list_directory(region_url)
        sample_packages[region] = [href for href in region_links if href.endswith(".zip")][:5]

    directory_manifest = {
        "gdb_root": gdb_root,
        "region_directories": region_dirs,
        "sample_packages": sample_packages,
    }
    (output_dir / "directory_manifest.json").write_text(json.dumps(directory_manifest, indent=2))

    manifest = {
        "sources": {
            "nhn": {
                "download_directory_url": DOWNLOAD_DIRECTORY_URL,
                "ftp_base": NHN_FTP_BASE,
                "classification": "official national hydrography geometry",
                "notes": (
                    "NHN is the official GeoBase inland hydrography source for Canada. "
                    "Use it for river-network completeness where CHS/NONNA does not provide bathymetric coverage."
                ),
                "gdb_region_directory_count": len(region_dirs),
                "gdb_root_url": gdb_root,
            },
            "nrcan_web_services": {
                "page_url": WEB_SERVICES_URL,
                "classification": "official service catalog",
            },
        }
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"output": str(output_dir), "manifest": str(output_dir / "manifest.json")}, indent=2))


if __name__ == "__main__":
    main()
