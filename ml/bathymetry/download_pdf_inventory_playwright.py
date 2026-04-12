#!/usr/bin/env python3
"""
Download protected PDF inventory rows using a real browser session.

Some agency sites return 403 to requests/curl even with browser-like headers.
This script uses Playwright/Chromium to navigate the PDF URLs as a browser and
save successful PDF responses to disk.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


def read_inventory(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return cleaned or "document"


def build_filename(row: dict[str, str], index: int) -> str:
    for key in ("lake_name", "waterbody", "reservoir_name"):
        value = (row.get(key) or "").strip()
        if value:
            return f"{slugify(value)}.pdf"
    return f"document_{index:05d}.pdf"


def filter_rows(rows: list[dict[str, str]], where_column: str | None, where_value: str | None) -> list[dict[str, str]]:
    if not where_column or where_value is None:
        return rows
    return [row for row in rows if (row.get(where_column) or "").strip() == where_value]


def main() -> None:
    parser = argparse.ArgumentParser(description="Download protected official PDFs through Playwright.")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest-name", default="download_manifest.json")
    parser.add_argument("--where-column", default=None)
    parser.add_argument("--where-value", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--headless", action="store_true", default=False)
    args = parser.parse_args()

    inventory_path = Path(args.inventory)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = filter_rows(read_inventory(inventory_path), args.where_column, args.where_value)
    if args.offset:
        rows = rows[args.offset :]
    if args.limit is not None:
        rows = rows[: args.limit]

    manifest: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        page = context.new_page()

        for idx, row in enumerate(rows, start=1):
            url = (row.get("pdf_url") or "").strip()
            if not url:
                continue

            filename = build_filename(row, idx)
            destination = output_dir / filename
            record: dict[str, Any] = {
                "index": idx,
                "pdf_url": url,
                "local_path": str(destination),
                "lake_name": row.get("lake_name") or row.get("waterbody") or row.get("reservoir_name"),
                "report_type": row.get("report_type"),
                "jurisdiction": row.get("jurisdiction"),
                "status": "planned",
            }

            if destination.exists() and destination.stat().st_size > 0:
                record["status"] = "exists"
                record["size_bytes"] = destination.stat().st_size
                manifest.append(record)
                continue

            try:
                response = page.goto(url, wait_until="networkidle", timeout=120000)
                if response is None:
                    record["status"] = "no_response"
                else:
                    record["http_status"] = response.status
                    content_type = response.headers.get("content-type", "")
                    record["content_type"] = content_type
                    body = response.body()
                    if response.status == 200 and "pdf" in content_type.lower() and body.startswith(b"%PDF"):
                        destination.write_bytes(body)
                        record["status"] = "downloaded"
                        record["size_bytes"] = destination.stat().st_size
                    else:
                        record["status"] = "non_pdf_response"
                        record["body_prefix"] = body[:120].decode("latin-1", errors="replace")
            except Exception as exc:
                record["status"] = "error"
                record["error"] = str(exc)

            manifest.append(record)
            time.sleep(args.sleep_seconds)

        context.close()
        browser.close()

    manifest_path = output_dir / args.manifest_name
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {manifest_path} ({len(manifest)} rows)")


if __name__ == "__main__":
    main()
