#!/usr/bin/env python3
"""
Scrape GPS Nautical Charts / iBoating state, county, and lake pages into a
discovery inventory.

Important:
  This is an index/discovery scraper only. It does not attempt to ingest or
  reproduce proprietary bathymetry. We use the public HTML pages as a coverage
  manifest of lake names, page URLs, and lightweight stats that can later be
  resolved against official/public sources.

Source:
  https://www.gpsnauticalcharts.com/main/us-nautical-chart-by-state/us-nautical-charts-by-state.html
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.gpsnauticalcharts.com"
INDEX_URL = f"{BASE_URL}/main/us-nautical-chart-by-state/us-nautical-charts-by-state.html"
USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"

OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical")
STATE_CSV = OUTPUT_DIR / "gpsnautical_state_index.csv"
LAKE_CSV = OUTPUT_DIR / "gpsnautical_lake_inventory.csv"
SUMMARY_JSON = OUTPUT_DIR / "gpsnautical_summary.json"

US_STATE_NAMES = {
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Virgnia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
    "Puerto Rico",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--states",
        help="Comma-separated list of state names to crawl. Defaults to all listed state pages.",
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
        "--max-counties-per-state",
        type=int,
        default=0,
        help="Debug cap; 0 means no cap.",
    )
    parser.add_argument(
        "--max-lakes-per-county",
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


def normalized_state_name(name: str) -> str:
    return "Virginia" if name == "Virgnia" else name


def extract_state_links(scraper: Scraper, selected_states: set[str] | None) -> list[dict[str, str]]:
    soup = scraper.fetch_soup(INDEX_URL)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        text = clean_text(anchor.get_text(" ", strip=True))
        href = anchor["href"].strip()
        if not text or text not in US_STATE_NAMES:
            continue
        state_name = normalized_state_name(text)
        if selected_states and state_name.lower() not in selected_states:
            continue
        url = urljoin(INDEX_URL, href)
        key = f"{state_name.lower()}|{url}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"state": state_name, "state_url": url})
    return rows


def extract_county_links(scraper: Scraper, state_url: str) -> list[dict[str, str]]:
    soup = scraper.fetch_soup(state_url)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if "county-folio.html" not in href and not re.search(r"/main/c_[^/]+\.html$", href):
            continue
        county_name = clean_text(anchor.get_text(" ", strip=True))
        if not county_name:
            continue
        url = urljoin(state_url, href)
        key = f"{county_name.lower()}|{url}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"county": county_name, "county_url": url})
    return rows


def lake_detail_href(href: str) -> bool:
    href = href.strip()
    return bool(
        href.endswith(".html")
        and "/main/" in href
        and "county-folio" not in href
        and "us-nautical-chart-by-state" not in href
        and "uk-nautical-chart-by-region" not in href
        and "nautical-chart-by-state" not in href
    )


def extract_lake_links(scraper: Scraper, county_url: str) -> list[dict[str, str]]:
    soup = scraper.fetch_soup(county_url)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    marine_table = soup.find("table", class_="marine-table-fix")
    if marine_table is not None:
        left_table = marine_table.find("table")
        if left_table is not None:
            for row in left_table.find_all("tr"):
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
                url = urljoin(county_url, href)
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
    if rows:
        return rows

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = clean_text(anchor.get_text(" ", strip=True))
        if not text or not lake_detail_href(href):
            continue
        url = urljoin(county_url, href)
        if url == county_url:
            continue
        key = f"{text.lower()}|{url}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"lake_name": text, "lake_url": url, "listed_scale": ""})
    return rows


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

    selected_states = None
    if args.states:
        selected_states = {normalized_state_name(clean_text(name)).lower() for name in args.states.split(",") if name.strip()}

    scraper = Scraper(delay_seconds=args.delay_seconds, timeout=args.timeout)
    state_rows = extract_state_links(scraper, selected_states)

    lake_rows: list[dict[str, str]] = []
    state_summary: list[dict[str, str | int]] = []

    total_states = len(state_rows)
    for index, state_row in enumerate(state_rows, start=1):
        print(f"[{index}/{total_states}] Scraping {state_row['state']} ...", flush=True)
        counties = extract_county_links(scraper, state_row["state_url"])
        if args.max_counties_per_state:
            counties = counties[: args.max_counties_per_state]

        state_lake_count = 0
        for county_row in counties:
            lakes = extract_lake_links(scraper, county_row["county_url"])
            if args.max_lakes_per_county:
                lakes = lakes[: args.max_lakes_per_county]

            for lake_row in lakes:
                stats = extract_lake_stats(scraper, lake_row["lake_url"])
                lake_rows.append(
                    {
                        "state": state_row["state"],
                        "state_url": state_row["state_url"],
                        "county": county_row["county"],
                        "county_url": county_row["county_url"],
                        "lake_name": lake_row["lake_name"],
                        "lake_url": lake_row["lake_url"],
                        "listed_scale": lake_row.get("listed_scale", ""),
                        **stats,
                        "inventory_source": "gpsnautical_index_only",
                        "license_note": "Commercial derived product; use as discovery index, not canonical bathymetry.",
                    }
                )
            state_lake_count += len(lakes)

        state_summary.append(
            {
                "state": state_row["state"],
                "state_url": state_row["state_url"],
                "county_count": len(counties),
                "lake_count": state_lake_count,
            }
        )
        print(
            f"[{index}/{total_states}] {state_row['state']}: {len(counties)} counties, {state_lake_count} lake pages",
            flush=True,
        )

    write_csv(
        STATE_CSV,
        state_summary,
        ["state", "state_url", "county_count", "lake_count"],
    )
    write_csv(
        LAKE_CSV,
        lake_rows,
        [
            "state",
            "state_url",
            "county",
            "county_url",
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
        "states_scraped": len(state_summary),
        "lakes_scraped": len(lake_rows),
        "output_files": {
            "state_csv": str(STATE_CSV),
            "lake_csv": str(LAKE_CSV),
        },
        "note": "Discovery inventory only; not a bathymetry ingestion license.",
        "states": state_summary,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Wrote {STATE_CSV}")
    print(f"Wrote {LAKE_CSV}")
    print(f"Wrote {SUMMARY_JSON}")
    print(f"States scraped: {len(state_summary)}")
    print(f"Lakes scraped: {len(lake_rows)}")


if __name__ == "__main__":
    main()
