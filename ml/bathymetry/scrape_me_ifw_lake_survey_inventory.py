#!/usr/bin/env python3
"""
Scrape the official Maine IF&W lake survey map index into a PDF inventory.

Maine IF&W publishes a statewide table of lake survey map PDFs on a single
official page. This script converts that table into a concrete inventory so
GPS Nautical lakes can be matched against real downloadable bathymetry charts.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


INDEX_URL = "https://www.maine.gov/ifw/fishing-boating/fishing/lake-survey-maps/index.html"
USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"

OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/me")
DEFAULT_OUTPUT_CSV = OUTPUT_DIR / "me_ifw_lake_survey_inventory.csv"
DEFAULT_SUMMARY_JSON = OUTPUT_DIR / "me_ifw_lake_survey_inventory_summary.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    return parser.parse_args()


def fetch_text(url: str, timeout: int = 60) -> str:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.text


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def parse_survey_id(pdf_url: str) -> str:
    match = re.search(r"-(\d{4})\.pdf$", pdf_url, re.I)
    return match.group(1) if match else ""


def parse_rows(index_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(index_html, "html.parser")
    table = soup.find("table", id="dataTable")
    if table is None:
        raise RuntimeError("Official Maine IF&W survey table not found on the index page")

    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue
        anchor = cells[0].find("a", href=True)
        if anchor is None:
            continue
        pdf_url = urljoin(INDEX_URL, anchor.get("href", "").strip())
        if not pdf_url.lower().endswith(".pdf"):
            continue
        if pdf_url in seen_urls:
            continue
        seen_urls.add(pdf_url)

        lake_name = clean_text(anchor.get_text(" ", strip=True))
        rows.append(
            {
                "lake_name": lake_name,
                "zone": clean_text(cells[1].get_text(" ", strip=True)),
                "towns": clean_text(cells[2].get_text(" ", strip=True)),
                "counties": clean_text(cells[3].get_text(" ", strip=True)),
                "pdf_url": pdf_url,
                "survey_id": parse_survey_id(pdf_url),
                "source_page": INDEX_URL,
                "asset_kind": "bathymetric_map_pdf",
                "status": "official_me_ifw_lake_survey_map",
                "access_mode": "public_download",
            }
        )

    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "lake_name",
        "zone",
        "towns",
        "counties",
        "pdf_url",
        "survey_id",
        "source_page",
        "asset_kind",
        "status",
        "access_mode",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    html = fetch_text(INDEX_URL)
    rows = parse_rows(html)
    rows.sort(key=lambda row: (row["lake_name"].lower(), row["pdf_url"]))
    write_csv(args.output_csv, rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "index_url": INDEX_URL,
        "row_count": len(rows),
        "unique_lake_count": len({row["lake_name"] for row in rows}),
        "zone_counts": {
            key: sum(1 for row in rows if row["zone"] == key)
            for key in sorted({row["zone"] for row in rows})
        },
        "county_examples": sorted({row["counties"] for row in rows if row["counties"]})[:20],
        "output_csv": str(args.output_csv),
        "examples": rows[:10],
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(rows)} Maine IF&W survey rows")
    print(f"CSV: {args.output_csv}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
