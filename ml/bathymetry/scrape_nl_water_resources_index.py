#!/usr/bin/env python3
"""
Build a Newfoundland & Labrador water-resources index lane.

NL does not currently expose a clean province-wide inland bathymetry dataset,
but it does expose official atlas/report/project pages that are worth tracking
as a project/index-supported source lane.
"""

from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/nl")
OUT_CSV = OUT_DIR / "nl_water_resources_index.csv"

SOURCES = {
    "water_reports": "https://www.gov.nl.ca/eccc/waterres/reports/",
    "atlas": "https://www.gov.nl.ca/mca/atlas/",
}

KEYWORDS = (
    "water",
    "lake",
    "pond",
    "reservoir",
    "hydro",
    "hydrology",
    "hydrotechnical",
    "atlas",
    "flood",
)


def fetch_html(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    return resp.text


def classify_link(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".dwg", ".dxf", ".zip", ".tif", ".tiff", ".jpg", ".jpeg")):
        return "download"
    return "page"


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def should_keep(title: str, url: str) -> bool:
    hay = f"{title} {url}".lower()
    return any(keyword in hay for keyword in KEYWORDS)


def scrape_source(source_name: str, url: str) -> list[dict]:
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    rows: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        title = normalize_text(a.get_text(" ", strip=True))
        if not href.startswith("http"):
            continue
        if href in seen:
            continue
        if not should_keep(title, href):
            continue
        seen.add(href)
        rows.append(
            {
                "title": title or Path(urlparse(href).path).name,
                "url": href,
                "source_page": url,
                "source_name": source_name,
                "link_kind": classify_link(href),
                "jurisdiction": "nl",
            }
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for source_name, url in SOURCES.items():
        rows.extend(scrape_source(source_name, url))

    deduped = {row["url"]: row for row in rows}
    final_rows = sorted(deduped.values(), key=lambda r: (r["source_name"], r["title"].lower()))

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["title", "url", "source_page", "source_name", "link_kind", "jurisdiction"],
        )
        writer.writeheader()
        writer.writerows(final_rows)

    print(f"Wrote {OUT_CSV} ({len(final_rows)} links)")


if __name__ == "__main__":
    main()
