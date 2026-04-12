#!/usr/bin/env python3
"""
Scrape official Kentucky Fish & Wildlife waterbody pages for contour map sources.

Many KDFWR waterbody detail pages expose lake-specific contour maps through
public Google My Maps links. Those links can export directly as KML, which
gives us an official vector contour lane instead of a PDF-only digitization
path for the matched Kentucky lakes.
"""

from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


INDEX_URL = "https://app.fw.ky.gov/fisheries/waterbody.aspx?AccessType=R&County=%2A&Handicap=off&Species=%2A&WaterBody=%2A"
DETAIL_BASE = "https://app.fw.ky.gov/fisheries/"
USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"

OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/ky")
CSV_PATH = OUTPUT_DIR / "ky_kdfwr_contour_inventory.csv"
SUMMARY_PATH = OUTPUT_DIR / "ky_kdfwr_contour_inventory_summary.json"


def fetch_text(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def parse_detail_urls(index_html: str) -> list[str]:
    wids = sorted(set(re.findall(r"waterbodydetail\.aspx\?wid=(\d+)", index_html, re.I)), key=int)
    return [urljoin(DETAIL_BASE, f"waterbodydetail.aspx?wid={wid}") for wid in wids]


def extract_lake_name(soup: BeautifulSoup, detail_url: str) -> str:
    tag = soup.find(id=re.compile(r"Label_waterbody_name", re.I))
    if tag:
        text = " ".join(tag.get_text(" ", strip=True).split())
        if text:
            return text
    header = soup.find(["h1", "h2", "h3"])
    if header:
        text = " ".join(header.get_text(" ", strip=True).split())
        if text:
            return text
    wid = parse_qs(urlparse(detail_url).query).get("wid", [""])[0]
    return f"KDFWR Waterbody {wid}"


def kml_export_url(google_map_url: str) -> str:
    parsed = urlparse(google_map_url)
    mid = parse_qs(parsed.query).get("mid", [""])[0]
    if not mid:
        return ""
    return f"https://www.google.com/maps/d/kml?mid={mid}&forcekml=1"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index_html = fetch_text(INDEX_URL)
    detail_urls = parse_detail_urls(index_html)

    rows: list[dict[str, str]] = []
    contour_pages = 0
    for idx, detail_url in enumerate(detail_urls, start=1):
        html = fetch_text(detail_url)
        soup = BeautifulSoup(html, "html.parser")
        lake_name = extract_lake_name(soup, detail_url)
        contour_map_url = ""
        contour_map_label = ""
        contour_kml_url = ""
        fish_attractor_map_url = ""

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            text = " ".join(anchor.get_text(" ", strip=True).split())
            if "google.com/maps/d" not in href.lower():
                continue
            if "contour" in text.lower() and not contour_map_url:
                contour_map_url = href
                contour_map_label = text
                contour_kml_url = kml_export_url(href)
            elif "attractor" in text.lower() and not fish_attractor_map_url:
                fish_attractor_map_url = href

        if contour_kml_url:
            contour_pages += 1
            rows.append(
                {
                    "lake_name": lake_name,
                    "source_page": detail_url,
                    "contour_map_label": contour_map_label,
                    "contour_map_url": contour_map_url,
                    "contour_kml_url": contour_kml_url,
                    "fish_attractor_map_url": fish_attractor_map_url,
                }
            )

        if idx % 20 == 0 or idx == len(detail_urls):
            print(f"processed {idx}/{len(detail_urls)} Kentucky waterbody pages ({contour_pages} contour pages)")
        time.sleep(0.02)

    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "lake_name",
                "source_page",
                "contour_map_label",
                "contour_map_url",
                "contour_kml_url",
                "fish_attractor_map_url",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "index_url": INDEX_URL,
        "detail_page_count": len(detail_urls),
        "contour_row_count": len(rows),
        "output_csv": str(CSV_PATH),
        "examples": rows[:10],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {CSV_PATH} ({len(rows)} contour-ready Kentucky rows)")
    print(f"Summary: {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
