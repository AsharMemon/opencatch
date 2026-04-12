#!/usr/bin/env python3
"""
Scrape Texas TWDB completed lake survey shapefile links into a repeatable inventory.

The old workbook URL recorded in the repo is actually an HTML page, not a real
Excel file. This script crawls the official completed-surveys page, extracts all
`Shapefiles*.zip` links, derives lake/survey metadata from the URL structure,
and optionally probes file sizes so we can prioritize high-value acquisitions.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import List
from urllib.parse import urljoin, urlparse

import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scrape_tx_twdb_inventory")

LIST_URL = "https://www.twdb.texas.gov/surfacewater/surveys/completed/list/index.asp"
DEFAULT_OUTPUT = Path("/Users/Ashar/Documents/fish/data/bathymetry/tx_inventory/tx_twdb_shapefile_inventory.csv")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[tuple[str, str]] = []
        self._href: str | None = None
        self._chunks: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._chunks = []

    def handle_data(self, data):
        if self._href is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join(" ".join(self._chunks).split())
            self.links.append((text, self._href))
            self._href = None
            self._chunks = []


def parse_entries(html: str) -> List[dict]:
    parser = LinkParser()
    parser.feed(html)

    entries: List[dict] = []
    seen: set[str] = set()
    for text, href in parser.links:
        absolute = urljoin(LIST_URL, href)
        if "surfacewater/surveys/completed/files/" not in absolute.lower():
            continue
        if not absolute.lower().endswith(".zip"):
            continue
        if "shapefiles" not in absolute.lower():
            continue
        if absolute in seen:
            continue
        seen.add(absolute)

        parsed = urlparse(absolute)
        path_parts = [part for part in parsed.path.split("/") if part]
        filename = path_parts[-1] if path_parts else ""

        lake_slug = ""
        survey_folder = ""
        release_folder = ""
        if "files" in path_parts:
            files_idx = path_parts.index("files")
            tail = path_parts[files_idx + 1 :]
            if len(tail) >= 1:
                lake_slug = tail[0]
            if len(tail) >= 2:
                survey_folder = tail[1]
            if len(tail) >= 4:
                release_folder = tail[2]

        date_match = re.search(r"(19|20)\d{2}-\d{2}", absolute)
        recalculated = "recalculated" in absolute.lower()

        entries.append(
            {
                "lake_slug": lake_slug,
                "survey_folder": survey_folder,
                "release_folder": release_folder,
                "survey_date": date_match.group(0) if date_match else "",
                "recalculated": recalculated,
                "link_text": text,
                "filename": filename,
                "url": absolute,
            }
        )

    entries.sort(
        key=lambda row: (
            row["lake_slug"].lower(),
            row["survey_date"],
            row["release_folder"].lower(),
            row["url"],
        )
    )
    return entries


def probe_size(session: requests.Session, url: str) -> tuple[str, str]:
    try:
        response = session.get(url, timeout=30, stream=True)
        size = response.headers.get("content-length", "")
        content_type = response.headers.get("content-type", "")
        response.close()
        return size, content_type
    except Exception as exc:  # pragma: no cover - network variability
        log.warning("Size probe failed for %s: %s", url, exc)
        return "", ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a repeatable Texas TWDB shapefile inventory.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--probe-size", action="store_true", help="Fetch Content-Length/Content-Type for each shapefile URL")
    args = parser.parse_args()

    session = requests.Session()
    html = session.get(LIST_URL, timeout=60).text
    entries = parse_entries(html)
    if not entries:
        raise SystemExit("No Texas TWDB shapefile links found.")

    if args.probe_size:
        for entry in entries:
            size, content_type = probe_size(session, entry["url"])
            entry["content_length"] = size
            entry["content_type"] = content_type

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "lake_slug",
        "survey_folder",
        "release_folder",
        "survey_date",
        "recalculated",
        "link_text",
        "filename",
        "url",
        "content_length",
        "content_type",
    ]
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            writer.writerow({key: entry.get(key, "") for key in fieldnames})

    unique_lakes = sorted({entry["lake_slug"] for entry in entries if entry["lake_slug"]})
    log.info("Wrote %s shapefile entries spanning %s unique lakes to %s", len(entries), len(unique_lakes), args.output)


if __name__ == "__main__":
    main()
