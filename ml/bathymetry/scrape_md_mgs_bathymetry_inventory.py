#!/usr/bin/env python3
"""
Build a Maryland Geological Survey reservoir bathymetry inventory.

MGS publishes a small but high-value reservoir bathymetry program with:
- a reservoir index page
- per-reservoir pages with bathymetric map/report PDFs
- a shared data page with bathymetry ZIP downloads

The site currently presents an SSL chain that fails strict verification, so
this scraper intentionally uses `verify=False` against the official MGS domain.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib3
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


INDEX_URL = "https://www.mgs.md.gov/coastal_geology/bathy_index.html"
DATA_URL = "https://www.mgs.md.gov/publications/data_pages/reservoir_bathymetry.html"
DEFAULT_OUTPUT_CSV = Path("/Users/Ashar/Documents/fish/data/bathymetry/md/md_mgs_bathymetry_inventory.csv")
DEFAULT_SUMMARY_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/md/md_mgs_bathymetry_inventory_summary.json")


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    return parser.parse_args()


def fetch_html(url: str) -> str:
    response = requests.get(url, timeout=60, verify=False)
    response.raise_for_status()
    return response.text


def normalize_name(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("t. howard duckett", "rocky gorge")
    value = value.replace("duckett", "rocky gorge")
    value = value.replace("(rocky gorge)", "rocky gorge")
    value = value.replace("reservoir", " ")
    value = value.replace("lake", " ")
    value = value.replace("  ", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def extract_year(text: str) -> str:
    match = re.search(r"(19|20)\d{2}", text or "")
    return match.group(0) if match else ""


def parse_data_downloads(data_html: str) -> dict[str, list[dict[str, str]]]:
    soup = BeautifulSoup(data_html, "lxml")
    downloads: dict[str, list[dict[str, str]]] = {}
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if not href or ".zip" not in href.lower():
            continue
        full_url = urljoin(DATA_URL, href)
        name_key = normalize_name(label)
        if not name_key:
            continue
        downloads.setdefault(name_key, []).append(
            {
                "data_zip_url": full_url,
                "data_label": label,
                "data_year": extract_year(label) or extract_year(href),
            }
        )
    return downloads


def parse_reservoir_pages(index_html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(index_html, "lxml")
    pages: list[tuple[str, str]] = []
    for anchor in soup.select("section.twoColumnLeft.content ul li a[href]"):
        href = anchor.get("href", "").strip()
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if not href or not label:
            continue
        full_url = urljoin(INDEX_URL, href)
        if full_url == DATA_URL:
            continue
        pages.append((label, full_url))
    return pages


def parse_reservoir_page(lake_name: str, page_url: str, download_index: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    html = fetch_html(page_url)
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    data_rows = download_index.get(normalize_name(lake_name), [])

    seen_pdf_urls: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href or ".pdf" not in href.lower():
            continue
        pdf_url = urljoin(page_url, href)
        if pdf_url in seen_pdf_urls:
            continue
        seen_pdf_urls.add(pdf_url)

        label = " ".join(anchor.get_text(" ", strip=True).split())
        surrounding = anchor.parent.get_text(" ", strip=True)
        year = extract_year(label) or extract_year(surrounding) or extract_year(pdf_url)
        lower = f"{label} {surrounding} {pdf_url}".lower()
        if "report" in lower and "plate" not in lower and "map" not in lower:
            asset_kind = "report_pdf"
        else:
            asset_kind = "bathymetric_map_pdf"

        matching_data = [item for item in data_rows if not year or item["data_year"] == year]
        if not matching_data and data_rows:
            matching_data = data_rows

        if matching_data:
            for data_row in matching_data:
                rows.append(
                    {
                        "lake_name": lake_name,
                        "pdf_url": pdf_url,
                        "asset_kind": asset_kind,
                        "asset_year": year,
                        "source_page": page_url,
                        "evidence_url": INDEX_URL,
                        "data_zip_url": data_row["data_zip_url"],
                        "data_label": data_row["data_label"],
                        "jurisdiction": "md",
                        "status": "official_mgs_reservoir_bathymetry",
                        "access_mode": "public_download",
                    }
                )
        else:
            rows.append(
                {
                    "lake_name": lake_name,
                    "pdf_url": pdf_url,
                    "asset_kind": asset_kind,
                    "asset_year": year,
                    "source_page": page_url,
                    "evidence_url": INDEX_URL,
                    "data_zip_url": "",
                    "data_label": "",
                    "jurisdiction": "md",
                    "status": "official_mgs_reservoir_bathymetry",
                    "access_mode": "public_download",
                }
            )

    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "lake_name",
        "pdf_url",
        "asset_kind",
        "asset_year",
        "source_page",
        "evidence_url",
        "data_zip_url",
        "data_label",
        "jurisdiction",
        "status",
        "access_mode",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    index_html = fetch_html(INDEX_URL)
    data_html = fetch_html(DATA_URL)
    download_index = parse_data_downloads(data_html)
    reservoir_pages = parse_reservoir_pages(index_html)

    rows: list[dict[str, Any]] = []
    for lake_name, page_url in reservoir_pages:
        rows.extend(parse_reservoir_page(lake_name, page_url, download_index))

    rows.sort(key=lambda row: (row["lake_name"].lower(), row["asset_kind"], row["asset_year"], row["pdf_url"]))
    write_csv(args.output_csv, rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(rows),
        "lake_count": len({row["lake_name"] for row in rows}),
        "asset_kind_counts": {
            key: sum(1 for row in rows if row["asset_kind"] == key)
            for key in sorted({row["asset_kind"] for row in rows})
        },
        "data_zip_count": sum(1 for row in rows if row["data_zip_url"]),
        "source_pages": [page_url for _, page_url in reservoir_pages],
        "index_url": INDEX_URL,
        "data_url": DATA_URL,
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(rows)} rows across {summary['lake_count']} lakes")
    print(f"CSV: {args.output_csv}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
