#!/usr/bin/env python3
"""
Download official bathymetry PDFs from an inventory CSV.

This is the bridge between "we discovered the official PDF source" and
"we can actually promote this jurisdiction into extractable geometry."

Expected CSV columns:
    - pdf_url (required)
    - lake_name, report_id, report_type, region_code (optional metadata)

Usage examples:
    python download_pdf_inventory_batch.py \
        --inventory /Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv \
        --output-dir /Users/Ashar/Documents/fish/data/bathymetry/ms/pdfs \
        --download --limit 10 --insecure

    python download_pdf_inventory_batch.py \
        --inventory /Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv \
        --output-dir /Users/Ashar/Documents/fish/data/bathymetry/sd/lake_maps \
        --where-column report_type --where-value "Lake Maps" \
        --download
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("download_pdf_inventory_batch")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Referer": "https://www.google.com",
}


def read_inventory(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return cleaned or "document"


def build_filename(row: Dict[str, str], index: int) -> str:
    for key in ("lake_name", "waterbody", "reservoir_name"):
        value = (row.get(key) or "").strip()
        if value:
            return f"{slugify(value)}.pdf"

    report_id = (row.get("report_id") or "").strip()
    if report_id:
        report_type = slugify((row.get("report_type") or "report").strip())
        return f"{report_type}_{report_id}.pdf"

    url = row.get("pdf_url") or ""
    basename = Path(urlparse(url).path).name
    if basename:
        return basename

    return f"document_{index:05d}.pdf"


def filter_rows(
    rows: Iterable[Dict[str, str]],
    *,
    where_column: str | None,
    where_value: str | None,
    region_code: str | None,
) -> List[Dict[str, str]]:
    filtered = []
    for row in rows:
        if where_column and where_value is not None:
            if (row.get(where_column) or "").strip() != where_value:
                continue
        if region_code:
            if (row.get("region_code") or "").strip().upper() != region_code.upper():
                continue
        filtered.append(row)
    return filtered


def download_file(
    session: requests.Session,
    url: str,
    path: Path,
    *,
    insecure: bool,
) -> Dict[str, Any]:
    try:
        response = session.get(url, headers=HEADERS, timeout=120, stream=True, verify=not insecure)
        response.raise_for_status()
        with path.open("wb") as f:
            for chunk in response.iter_content(1 << 16):
                if chunk:
                    f.write(chunk)
        size_bytes = path.stat().st_size
        return {"status": "downloaded", "size_bytes": size_bytes, "http_status": response.status_code}
    except Exception as exc:
        if path.exists():
            path.unlink(missing_ok=True)
        return {"status": "error", "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Download official bathymetry PDFs from an inventory CSV.")
    parser.add_argument("--inventory", required=True, help="Path to inventory CSV with pdf_url column.")
    parser.add_argument("--output-dir", required=True, help="Directory to store downloaded PDFs and manifest.")
    parser.add_argument("--manifest-name", default="download_manifest.json", help="Manifest filename.")
    parser.add_argument("--where-column", default=None, help="Optional CSV column to filter on.")
    parser.add_argument("--where-value", default=None, help="Exact value for --where-column filtering.")
    parser.add_argument("--region-code", default=None, help="Optional NS-style region code filter.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of rows to process.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N filtered rows.")
    parser.add_argument("--download", action="store_true", help="Actually download files; otherwise preview manifest only.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for sources with broken cert chains.")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="Delay between downloads.")
    args = parser.parse_args()

    inventory_path = Path(args.inventory)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = read_inventory(inventory_path)
    rows = filter_rows(
        rows,
        where_column=args.where_column,
        where_value=args.where_value,
        region_code=args.region_code,
    )

    if args.offset:
        rows = rows[args.offset:]
    if args.limit is not None:
        rows = rows[: args.limit]

    log.info("Selected %s inventory rows from %s", len(rows), inventory_path)
    manifest: List[Dict[str, Any]] = []
    session = requests.Session()

    for idx, row in enumerate(rows, start=1):
        url = (row.get("pdf_url") or "").strip()
        if not url:
            continue

        filename = build_filename(row, idx)
        destination = output_dir / filename

        record: Dict[str, Any] = {
            "index": idx,
            "pdf_url": url,
            "local_path": str(destination),
            "exists": destination.exists(),
            "lake_name": row.get("lake_name") or row.get("waterbody") or row.get("reservoir_name"),
            "report_id": row.get("report_id"),
            "report_type": row.get("report_type"),
            "region_code": row.get("region_code"),
            "jurisdiction": row.get("jurisdiction"),
        }

        if args.download:
            if destination.exists() and destination.stat().st_size > 0:
                record["status"] = "exists"
                record["size_bytes"] = destination.stat().st_size
            else:
                result = download_file(session, url, destination, insecure=args.insecure)
                record.update(result)
                time.sleep(args.sleep_seconds)
        else:
            record["status"] = "planned"

        manifest.append(record)

    manifest_path = output_dir / args.manifest_name
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {manifest_path} ({len(manifest)} rows)")


if __name__ == "__main__":
    main()
