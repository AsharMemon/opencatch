#!/usr/bin/env python3
"""
OpenCatch — USACE IENC acquisition

Scrapes the official USACE IENC SHP downloads page, builds a manifest of all
available chart ZIPs, and optionally downloads a subset for local processing.

Usage:
    python fetch_usace_ienc.py --output /data/bathymetry/usace_ienc
    python fetch_usace_ienc.py --output /data/bathymetry/usace_ienc --download-first 3
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import List

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("fetch_usace_ienc")

IENC_SHP_URL = "https://ienccloud.us/ienc_shp.html"
USER_AGENT = "OpenCatch/1.0 (contact@opencatch.app)"
REQUEST_TIMEOUT = 120


def fetch_html(url: str) -> str:
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.text


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=REQUEST_TIMEOUT, stream=True, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fh.write(chunk)
    return dest


def scrape_links(html: str) -> tuple[str | None, List[str]]:
    master = None
    chart_links: List[str] = []
    for href in re.findall(r'href=["\']([^"\']+)', html, re.I):
        normalized = href.replace("\\", "/")
        lower = normalized.lower()
        if lower.endswith("master_service_gdb.zip"):
            master = normalized
        elif lower.endswith("_shape.zip") and "ienc_shp/" in lower:
            chart_links.append(normalized)
    return master, sorted(set(chart_links))


def build_manifest(master_url: str | None, chart_urls: List[str]) -> dict:
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": IENC_SHP_URL,
        "master_service_gdb": master_url,
        "chart_count": len(chart_urls),
        "charts": [
            {
                "id": Path(url).name.replace("_SHAPE.zip", "").replace(".zip", ""),
                "url": url,
            }
            for url in chart_urls
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch official USACE IENC shapefile manifests and optional ZIPs.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/usace_ienc"),
    )
    parser.add_argument(
        "--download-first",
        type=int,
        default=0,
        help="Download the first N chart ZIPs after writing the manifest.",
    )
    parser.add_argument(
        "--download-master",
        action="store_true",
        help="Also download the official USACE IENC master GDB ZIP.",
    )
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    html = fetch_html(IENC_SHP_URL)
    master_url, chart_urls = scrape_links(html)
    manifest = build_manifest(master_url, chart_urls)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    downloaded = []

    if args.download_master and master_url:
        master_dest = output_dir / "downloads" / Path(master_url).name
        if not master_dest.exists():
            log.info("Downloading USACE master GDB: %s", master_dest.name)
            download_file(master_url, master_dest)
        downloaded.append({"id": "master_service_gdb", "path": str(master_dest)})

    if args.download_first > 0:
        download_dir = output_dir / "downloads"
        for url in chart_urls[: args.download_first]:
            dest = download_dir / Path(url).name
            if not dest.exists():
                log.info("Downloading USACE IENC chart: %s", dest.name)
                download_file(url, dest)
                time.sleep(0.2)
            downloaded.append({"id": dest.stem, "path": str(dest)})

    print(
        json.dumps(
            {
                "manifest": str(manifest_path),
                "chart_count": len(chart_urls),
                "downloaded": downloaded,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
