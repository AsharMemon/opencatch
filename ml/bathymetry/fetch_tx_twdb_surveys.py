#!/usr/bin/env python3
"""
Fetch Texas TWDB completed-survey shapefile bundles.

This reads the scraped TWDB shapefile inventory, selects the best available
bundle per lake (latest survey date, preferring recalculated releases when
available), downloads the ZIPs, and writes a local manifest for downstream
extraction/normalization.
"""

from __future__ import annotations

import argparse
import csv
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_tx_twdb_surveys")

DEFAULT_INVENTORY = Path("/Users/Ashar/Documents/fish/data/bathymetry/tx_inventory/tx_twdb_shapefile_inventory.csv")
DEFAULT_OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/tx/raw")
DEFAULT_MANIFEST = DEFAULT_OUTPUT_DIR / "tx_twdb_download_manifest.csv"


def parse_month(value: str) -> tuple[int, int]:
    try:
        dt = datetime.strptime(value, "%Y-%m")
        return dt.year, dt.month
    except Exception:
        return (0, 0)


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def pick_best_rows(rows: Iterable[dict], prefer_recalculated: bool = True) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        lake_slug = (row.get("lake_slug") or "").strip()
        if not lake_slug:
            continue
        grouped[lake_slug].append(row)

    selected: list[dict] = []
    for lake_slug, entries in grouped.items():
        best = max(
            entries,
            key=lambda row: (
                parse_month(row.get("survey_date", "")),
                1 if prefer_recalculated and str(row.get("recalculated", "")).lower() == "true" else 0,
                row.get("release_folder", "") or "",
                row.get("url", ""),
            ),
        )
        selected.append(best)

    selected.sort(key=lambda row: row["lake_slug"].lower())
    return selected


def download_file(session: requests.Session, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with session.get(url, timeout=120, stream=True) as response:
        response.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
    tmp.replace(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the best TWDB shapefile bundle for selected Texas lakes.")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lakes", help="Comma-separated lake slugs to fetch.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of lakes to download after filtering.")
    parser.add_argument("--no-prefer-recalculated", action="store_true", help="Do not prefer recalculated releases when dates tie.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip downloads whose ZIP already exists.")
    args = parser.parse_args()

    rows = load_rows(args.inventory)
    selected = pick_best_rows(rows, prefer_recalculated=not args.no_prefer_recalculated)

    if args.lakes:
        wanted = {item.strip().lower() for item in args.lakes.split(",") if item.strip()}
        selected = [row for row in selected if row["lake_slug"].lower() in wanted]

    if args.limit > 0:
        selected = selected[: args.limit]

    session = requests.Session()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict] = []

    for row in selected:
        lake_slug = row["lake_slug"]
        survey_date = row.get("survey_date") or "unknown"
        release_folder = row.get("release_folder") or "base"
        filename = row.get("filename") or "Shapefiles.zip"
        target_dir = args.output_dir / lake_slug / f"{survey_date}__{release_folder}"
        local_zip = target_dir / filename

        if args.skip_existing and local_zip.exists():
            log.info("Skipping existing %s", local_zip)
        else:
            log.info("Downloading %s -> %s", row["url"], local_zip)
            download_file(session, row["url"], local_zip)

        manifest_rows.append({**row, "local_zip_path": str(local_zip)})

    fieldnames = list(manifest_rows[0].keys()) if manifest_rows else [
        "lake_slug",
        "survey_folder",
        "release_folder",
        "survey_date",
        "recalculated",
        "link_text",
        "filename",
        "url",
        "content_length",
        "content_type",
        "local_zip_path",
    ]
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest_rows)

    log.info("Wrote download manifest with %s Texas lakes to %s", len(manifest_rows), args.manifest)


if __name__ == "__main__":
    main()
