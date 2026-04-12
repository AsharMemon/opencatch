#!/usr/bin/env python3
"""
Scrape the official South Carolina DNR lakes portal into a brochure/PDF inventory.

This harvests the public SCDNR lake/reservoir pages linked from the statewide
search page, then records any brochure/map PDFs exposed on each page so the
GPS Nautical lake queue can be crosswalked against a real official source lane.
"""

from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


SEARCH_URL = "https://www.dnr.sc.gov/lakes/search.html"
BASE_URL = "https://www.dnr.sc.gov/lakes/"
USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"

OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/sc")
CSV_PATH = OUTPUT_DIR / "scdnr_lake_brochure_inventory.csv"
SUMMARY_PATH = OUTPUT_DIR / "scdnr_lake_brochure_inventory_summary.json"

SKIP_LINKS = {
    "/index.html",
    "index.html",
    "brochures.html",
    "owners.html",
    "state/index.html",
    "../spanish/index.html",
}


def fetch_text(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def normalize_name(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    value = re.sub(r"\s*\(.*?\)\s*", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def page_lake_name(soup: BeautifulSoup, fallback: str) -> str:
    generic_titles = {
        "south carolina lakes and waterways",
        "lakes and waterways",
        "lakes and reservoirs",
        "general information",
        "information",
        "scdnr",
        "sc lakes and waterways",
    }
    selectors = [
        "h1",
        "h2",
        "h3",
        "title",
    ]
    for selector in selectors:
        tag = soup.select_one(selector)
        if not tag:
            continue
        text = normalize_name(tag.get_text(" ", strip=True))
        if not text:
            continue
        text = re.sub(r"\s*[-|].*$", "", text).strip()
        if text.lower() in generic_titles or text.lower() == "south carolina department of natural resources":
            continue
        return text
    return fallback


def pick_primary_pdf(urls: list[str]) -> str:
    if not urls:
        return ""
    scored = []
    for url in urls:
        lower = url.lower()
        score = 0
        if "map" in lower:
            score += 4
        if "brochure" in lower:
            score += 3
        if "depth" in lower or "bath" in lower:
            score += 2
        scored.append((score, url))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


def discover_detail_pages() -> list[tuple[str, str]]:
    html = fetch_text(SEARCH_URL)
    soup = BeautifulSoup(html, "html.parser")
    discovered: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href in SKIP_LINKS:
            continue
        if not re.search(r"(?:description|index)\.html$", href, re.I):
            continue
        if href.startswith("../"):
            continue
        absolute = urljoin(SEARCH_URL, href)
        if absolute == SEARCH_URL:
            continue
        text = normalize_name(anchor.get_text(" ", strip=True))
        if not text:
            continue
        discovered.setdefault(absolute, text)
    return sorted(discovered.items(), key=lambda item: item[1].lower())


def extract_pdf_links(page_url: str, soup: BeautifulSoup) -> list[str]:
    pdfs: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or ".pdf" not in href.lower():
            continue
        absolute = urljoin(page_url, href)
        if absolute not in pdfs:
            pdfs.append(absolute)
    return pdfs


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pages = discover_detail_pages()

    rows: list[dict[str, str]] = []
    pdf_pages = 0
    for idx, (page_url, anchor_name) in enumerate(pages, start=1):
        html = fetch_text(page_url)
        soup = BeautifulSoup(html, "html.parser")
        lake_name = page_lake_name(soup, anchor_name)
        pdf_urls = extract_pdf_links(page_url, soup)
        if pdf_urls:
            pdf_pages += 1
        rows.append(
            {
                "lake_name": lake_name,
                "anchor_name": anchor_name,
                "source_page": page_url,
                "pdf_url": pick_primary_pdf(pdf_urls),
                "report_pdf_urls": "|".join(pdf_urls),
            }
        )
        if idx % 10 == 0 or idx == len(pages):
            print(f"processed {idx}/{len(pages)} SCDNR lake pages")
        time.sleep(0.02)

    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["lake_name", "anchor_name", "source_page", "pdf_url", "report_pdf_urls"],
        )
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "search_url": SEARCH_URL,
        "row_count": len(rows),
        "pages_with_pdfs": pdf_pages,
        "pages_without_pdfs": len(rows) - pdf_pages,
        "output_csv": str(CSV_PATH),
        "examples": rows[:10],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {CSV_PATH} ({len(rows)} lake pages, {pdf_pages} with PDFs)")
    print(f"Summary: {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
