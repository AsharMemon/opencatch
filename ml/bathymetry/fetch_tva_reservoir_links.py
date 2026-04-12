#!/usr/bin/env python3
"""
Resolve official TVA links for reservoir names using public search results.

TVA's dynamic lake-level subpages are frequently shielded behind Cloudflare for
plain scripted clients. This fetcher keeps the workflow honest by collecting:
  - the official TVA lake-levels overview page
  - reservoir-specific official TVA URLs discovered via public search restricted
    to `site:tva.com`
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests


USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"
DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"
TVA_OVERVIEW_URL = "https://www.tva.com/Environment/Lake-Levels"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-results", type=int, default=5)
    return parser.parse_args()


def read_inventory(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def extract_result_urls(html: str, max_results: int) -> list[str]:
    urls: list[str] = []
    for href in re.findall(r'href="([^"]+)"', html, re.I):
        if "duckduckgo.com/l/?" not in href:
            continue
        parsed = urlparse(unescape(href))
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        target = unquote(target)
        if "tva.com" not in target.lower():
            continue
        if target not in urls:
            urls.append(target)
        if len(urls) >= max_results:
            break
    return urls


def search_tva_links(lake_name: str, max_results: int) -> list[str]:
    query = f'site:tva.com "{lake_name}" TVA'
    resp = requests.post(
        DUCKDUCKGO_HTML,
        data={"q": query},
        timeout=60,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    return extract_result_urls(resp.text, max_results)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    inventory_rows = read_inventory(args.inventory_csv)
    output_rows: list[dict[str, object]] = []

    for row in inventory_rows:
        links = search_tva_links(row["lake_name"], args.max_results)
        output_rows.append(
            {
                **row,
                "tva_overview_url": TVA_OVERVIEW_URL,
                "search_query": f'site:tva.com "{row["lake_name"]}" TVA',
                "official_result_count": len(links),
                "official_urls": "|".join(links),
            }
        )
        time.sleep(1.0)

    write_csv(args.output_dir / "tva_official_links.csv", output_rows)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inventory_csv": str(args.inventory_csv),
        "row_count": len(output_rows),
        "with_official_links": sum(1 for row in output_rows if row["official_result_count"]),
        "overview_url": TVA_OVERVIEW_URL,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
