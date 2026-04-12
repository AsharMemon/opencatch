#!/usr/bin/env python3
"""
Scrape GPS Nautical Charts / iBoating Canada folio pages into a discovery
inventory.

Important:
  This is an index/discovery scraper only. It does not attempt to ingest or
  reproduce proprietary bathymetry. We use the public HTML pages as a coverage
  manifest of lake names, page URLs, and lightweight stats that can later be
  resolved against official/public sources.

Source:
  https://www.gpsnauticalcharts.com/main/nautical-charts-by-folio/ca-nautical-charts-by-folio.html
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.gpsnauticalcharts.com"
INDEX_URL = f"{BASE_URL}/main/nautical-charts-by-folio/ca-nautical-charts-by-folio.html"
USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"

OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical")
REGION_CSV = OUTPUT_DIR / "gpsnautical_ca_region_index.csv"
LAKE_CSV = OUTPUT_DIR / "gpsnautical_ca_lake_inventory.csv"
SUMMARY_JSON = OUTPUT_DIR / "gpsnautical_ca_summary.json"

CANADA_REGION_NAMES = {
    "Alberta Lakes",
    "Georgian Bay",
    "Great lakes Canada and St. Lawrence river",
    "Gulf of St. Lawrence North - Newfoundland West",
    "Gulf of St. Lawrence South",
    "Labrador Coast",
    "Lakes and Rivers in Manitoba",
    "Lakes and Rivers of British Columbia",
    "Lakes around Georgian Bay",
    "New Brunswick Fishing Maps",
    "Newfoundland East and South",
    "Northern Canada",
    "Nova Scotia South - Bay of Fundy",
    "Nunavut Fishing Maps",
    "Ontario Lakes",
    "Pacific Coast - Vancouver Island East & West - Haida Gwaii",
    "Quebec to Anticosti Island West",
    "Rainy Lake and Lake of the Woods",
    "Rideau Canal - Ottawa River",
    "Saskatchewan Fishing Maps",
    "Trent-Severn Waterway",
    "Yukon Fishing Maps",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regions",
        help="Comma-separated list of Canada region names to crawl. Defaults to all listed folios.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.25,
        help="Polite delay between requests.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout seconds.",
    )
    parser.add_argument(
        "--max-regions",
        type=int,
        default=0,
        help="Debug cap; 0 means no cap.",
    )
    parser.add_argument(
        "--max-subregions-per-region",
        type=int,
        default=0,
        help="Debug cap; 0 means no cap.",
    )
    parser.add_argument(
        "--max-direct-lakes-per-region",
        type=int,
        default=0,
        help="Debug cap on direct region lake links; 0 means no cap.",
    )
    parser.add_argument(
        "--max-lakes-per-subregion",
        type=int,
        default=0,
        help="Debug cap; 0 means no cap.",
    )
    return parser.parse_args()


class Scraper:
    def __init__(self, delay_seconds: float, timeout: int) -> None:
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.cache: dict[str, BeautifulSoup] = {}

    def fetch_soup(self, url: str) -> BeautifulSoup:
        if url in self.cache:
            return self.cache[url]
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        self.cache[url] = soup
        time.sleep(self.delay_seconds)
        return soup


def clean_text(value: str) -> str:
    return " ".join(value.split())


def lake_detail_href(href: str) -> bool:
    href = href.strip()
    return bool(
        href.endswith(".html")
        and "/main/" in href
        and "county-folio" not in href
        and "nautical-charts-by-folio" not in href
        and "nautical-charts-folio/" not in href
        and "nautical-chart-by-state" not in href
        and "us-nautical-chart-by-state" not in href
    )


def extract_region_links(scraper: Scraper, selected_regions: set[str] | None) -> list[dict[str, str]]:
    soup = scraper.fetch_soup(INDEX_URL)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        text = clean_text(anchor.get_text(" ", strip=True))
        href = anchor["href"].strip()
        if not text or text not in CANADA_REGION_NAMES:
            continue
        if selected_regions and text.lower() not in selected_regions:
            continue
        url = urljoin(INDEX_URL, href)
        key = f"{text.lower()}|{url}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"region": text, "region_url": url})
    return rows


def extract_subregion_links(scraper: Scraper, region_url: str) -> list[dict[str, str]]:
    soup = scraper.fetch_soup(region_url)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if "county-folio.html" not in href and not re.search(r"/main/c_ca_[^/]+\.html$", href):
            continue
        text = clean_text(anchor.get_text(" ", strip=True))
        if not text:
            continue
        url = urljoin(region_url, href)
        key = f"{text.lower()}|{url}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"subregion": text, "subregion_url": url})
    return rows


def extract_lake_links_from_table(page_url: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    tables = soup.find_all("table", class_="marine-table-fix")
    candidate_tables: list[BeautifulSoup] = []
    if tables:
        for table in tables:
            nested = table.find("table")
            if nested is not None:
                candidate_tables.append(nested)
    else:
        candidate_tables = soup.find_all("table")

    for table in candidate_tables:
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            anchor = cells[0].find("a", href=True)
            if anchor is None:
                continue
            href = anchor["href"].strip()
            text = clean_text(anchor.get_text(" ", strip=True))
            if not text or not lake_detail_href(href):
                continue
            url = urljoin(page_url, href)
            key = f"{text.lower()}|{url}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "lake_name": text,
                    "lake_url": url,
                    "listed_scale": clean_text(cells[1].get_text(" ", strip=True)),
                }
            )
    return rows


def extract_direct_region_lakes(scraper: Scraper, region_url: str) -> list[dict[str, str]]:
    return extract_lake_links_from_table(region_url, scraper.fetch_soup(region_url))


def extract_subregion_lakes(scraper: Scraper, subregion_url: str) -> list[dict[str, str]]:
    try:
        soup = scraper.fetch_soup(subregion_url)
    except requests.RequestException as exc:
        print(f"warning: skipping subregion {subregion_url} ({exc})", flush=True)
        return []

    rows = extract_lake_links_from_table(subregion_url, soup)
    if rows:
        return rows

    fallback: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = clean_text(anchor.get_text(" ", strip=True))
        if not text or not lake_detail_href(href):
            continue
        url = urljoin(subregion_url, href)
        key = f"{text.lower()}|{url}"
        if key in seen:
            continue
        seen.add(key)
        fallback.append({"lake_name": text, "lake_url": url, "listed_scale": ""})
    return fallback


def first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.I | re.M)
    return clean_text(match.group(1)) if match else ""


def float_match(pattern: str, text: str) -> str:
    value = first_match(pattern, text)
    return value.replace(",", "")


def blank_lake_stats(status: str, error: str = "") -> dict[str, str]:
    return {
        "title": "",
        "scale": "",
        "counties": "",
        "nearby_cities": "",
        "area_acres": "",
        "shoreline_miles": "",
        "min_longitude": "",
        "min_latitude": "",
        "max_longitude": "",
        "max_latitude": "",
        "chart_id": "",
        "country": "",
        "projection": "",
        "detail_fetch_status": status,
        "detail_error": error,
    }


def extract_lake_stats(scraper: Scraper, lake_url: str) -> dict[str, str]:
    try:
        soup = scraper.fetch_soup(lake_url)
    except requests.RequestException as exc:
        return blank_lake_stats("http_error", str(exc))
    text = soup.get_text("\n", strip=True)
    return {
        "title": first_match(r"^Title\s+(.+)$", text),
        "scale": first_match(r"^Scale\s+(.+)$", text),
        "counties": first_match(r"^Counties\s+(.+)$", text),
        "nearby_cities": first_match(r"^Nearby Cities\s+(.+)$", text),
        "area_acres": float_match(r"^Area\s+\*?([0-9.,]+)\s+acres$", text),
        "shoreline_miles": float_match(r"^Shoreline\s+\*?([0-9.,]+)\s+miles$", text),
        "min_longitude": float_match(r"^Min Longitude\s*(-?[0-9.]+)$", text),
        "min_latitude": float_match(r"^Min Latitude\s*(-?[0-9.]+)$", text),
        "max_longitude": float_match(r"^Max Longitude\s*(-?[0-9.]+)$", text),
        "max_latitude": float_match(r"^Max Latitude\s*(-?[0-9.]+)$", text),
        "chart_id": first_match(r"^Id\s+(.+)$", text),
        "country": first_match(r"^Country\s+(.+)$", text),
        "projection": first_match(r"^Projection\s+(.+)$", text),
        "detail_fetch_status": "ok",
        "detail_error": "",
    }


def write_csv(path: Path, rows: Iterable[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    selected_regions = None
    if args.regions:
        selected_regions = {clean_text(name).lower() for name in args.regions.split(",") if name.strip()}

    scraper = Scraper(delay_seconds=args.delay_seconds, timeout=args.timeout)
    region_rows = extract_region_links(scraper, selected_regions)
    if args.max_regions:
        region_rows = region_rows[: args.max_regions]

    lake_rows: list[dict[str, str]] = []
    region_summary: list[dict[str, str | int]] = []
    total_regions = len(region_rows)

    for index, region_row in enumerate(region_rows, start=1):
        print(f"[{index}/{total_regions}] Scraping {region_row['region']} ...", flush=True)

        direct_lakes = extract_direct_region_lakes(scraper, region_row["region_url"])
        if args.max_direct_lakes_per_region:
            direct_lakes = direct_lakes[: args.max_direct_lakes_per_region]

        subregions = extract_subregion_links(scraper, region_row["region_url"])
        if args.max_subregions_per_region:
            subregions = subregions[: args.max_subregions_per_region]

        seen_urls: set[str] = set()
        region_lake_count = 0

        for lake_row in direct_lakes:
            stats = extract_lake_stats(scraper, lake_row["lake_url"])
            lake_rows.append(
                {
                    "region": region_row["region"],
                    "region_url": region_row["region_url"],
                    "subregion": "",
                    "subregion_url": "",
                    "lake_name": lake_row["lake_name"],
                    "lake_url": lake_row["lake_url"],
                    "listed_scale": lake_row.get("listed_scale", ""),
                    **stats,
                    "inventory_source": "gpsnautical_index_only",
                    "license_note": "Commercial derived product; use as discovery index, not canonical bathymetry.",
                }
            )
            seen_urls.add(lake_row["lake_url"])
            region_lake_count += 1

        for subregion_row in subregions:
            lakes = extract_subregion_lakes(scraper, subregion_row["subregion_url"])
            if args.max_lakes_per_subregion:
                lakes = lakes[: args.max_lakes_per_subregion]
            for lake_row in lakes:
                if lake_row["lake_url"] in seen_urls:
                    continue
                stats = extract_lake_stats(scraper, lake_row["lake_url"])
                lake_rows.append(
                    {
                        "region": region_row["region"],
                        "region_url": region_row["region_url"],
                        "subregion": subregion_row["subregion"],
                        "subregion_url": subregion_row["subregion_url"],
                        "lake_name": lake_row["lake_name"],
                        "lake_url": lake_row["lake_url"],
                        "listed_scale": lake_row.get("listed_scale", ""),
                        **stats,
                        "inventory_source": "gpsnautical_index_only",
                        "license_note": "Commercial derived product; use as discovery index, not canonical bathymetry.",
                    }
                )
                seen_urls.add(lake_row["lake_url"])
                region_lake_count += 1

        region_summary.append(
            {
                "region": region_row["region"],
                "region_url": region_row["region_url"],
                "subregion_count": len(subregions),
                "direct_region_lake_count": len(direct_lakes),
                "total_lake_count": region_lake_count,
            }
        )
        print(
            f"[{index}/{total_regions}] {region_row['region']}: {len(subregions)} subregions, {len(direct_lakes)} direct lakes, {region_lake_count} total lake pages",
            flush=True,
        )

    write_csv(
        REGION_CSV,
        region_summary,
        ["region", "region_url", "subregion_count", "direct_region_lake_count", "total_lake_count"],
    )
    write_csv(
        LAKE_CSV,
        lake_rows,
        [
            "region",
            "region_url",
            "subregion",
            "subregion_url",
            "lake_name",
            "lake_url",
            "listed_scale",
            "title",
            "scale",
            "counties",
            "nearby_cities",
            "area_acres",
            "shoreline_miles",
            "min_longitude",
            "min_latitude",
            "max_longitude",
            "max_latitude",
            "chart_id",
            "country",
            "projection",
            "detail_fetch_status",
            "detail_error",
            "inventory_source",
            "license_note",
        ],
    )

    summary = {
        "source_url": INDEX_URL,
        "regions_scraped": len(region_summary),
        "lakes_scraped": len(lake_rows),
        "output_files": {
            "region_csv": str(REGION_CSV),
            "lake_csv": str(LAKE_CSV),
        },
        "note": "Discovery inventory only; not a bathymetry ingestion license.",
        "regions": region_summary,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Wrote {REGION_CSV}")
    print(f"Wrote {LAKE_CSV}")
    print(f"Wrote {SUMMARY_JSON}")
    print(f"Regions scraped: {len(region_summary)}")
    print(f"Lakes scraped: {len(lake_rows)}")


if __name__ == "__main__":
    main()
