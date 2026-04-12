#!/usr/bin/env python3
"""
Probe selected GPS Nautical lake detail pages for explicit upstream source hints.

This is a targeted companion to build_gpsnautical_source_master.py. Instead of
probing pages in inventory order, it lets us select the highest-priority
unmatched lakes and look for official-source text such as NOAA, CHS, USACE,
USBR, TVA, DNR/DEC/DEP references.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


DEFAULT_INPUT = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_source_master.csv")
DEFAULT_OUTPUT_CSV = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_page_source_probe.csv")
DEFAULT_SUMMARY_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_page_source_probe_summary.json")
USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"
THREAD_LOCAL = threading.local()

OFFICIAL_PAGE_PATTERNS = [
    ("NOAA", re.compile(r"\bNOAA\b", re.I)),
    ("CHS", re.compile(r"\bCHS\b|Canadian Hydrographic Service", re.I)),
    ("USACE", re.compile(r"\bUSACE\b|Army Corps|Corps of Engineers", re.I)),
    ("USBR", re.compile(r"\bUSBR\b|Bureau of Reclamation", re.I)),
    ("TVA", re.compile(r"\bTVA\b|Tennessee Valley Authority", re.I)),
    ("DNR", re.compile(r"\bDNR\b|Department of Natural Resources", re.I)),
    ("DEC", re.compile(r"\bDEC\b|Department of Environmental Conservation", re.I)),
    ("DEP/DEQ/DEM/IFW/PFBC", re.compile(r"\bDEP\b|\bDEQ\b|\bDEM\b|\bIFW\b|\bPFBC\b", re.I)),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--jurisdictions", default="", help="Comma-separated jurisdictions to include.")
    parser.add_argument("--classification", default="needs_jurisdiction_search", help="Source-master classification to target.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--delay-seconds", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=45)
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def clean_text(value: str) -> str:
    return " ".join(value.split())


def get_session() -> requests.Session:
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        THREAD_LOCAL.session = session
    return session


def detect_source(session: requests.Session, url: str, timeout: int) -> tuple[str, str]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))
    for label, pattern in OFFICIAL_PAGE_PATTERNS:
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 160)
            return label, text[start:end].strip()
    return "", ""


def probe_row(row: dict[str, str], timeout: int, delay_seconds: float) -> dict[str, Any]:
    label = ""
    snippet = ""
    error = ""
    try:
        label, snippet = detect_source(get_session(), row["lake_url"], timeout)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    return {
        "catalog": row.get("catalog") or "",
        "jurisdiction": row.get("jurisdiction") or "",
        "lake_name": row.get("lake_name") or "",
        "lake_url": row.get("lake_url") or "",
        "classification": row.get("classification") or "",
        "work_priority": row.get("work_priority") or "",
        "completion_stage": row.get("completion_stage") or "",
        "source_hint_family": label,
        "source_hint_snippet": snippet,
        "probe_error": error,
    }


def main() -> None:
    args = parse_args()
    jurisdictions = {part.strip() for part in args.jurisdictions.split(",") if part.strip()}
    rows = read_rows(args.input)
    target_rows = [
        row
        for row in rows
        if row.get("classification") == args.classification
        and not row.get("page_source_family")
        and row.get("lake_url")
        and (not jurisdictions or row.get("jurisdiction") in jurisdictions)
    ]
    target_rows.sort(
        key=lambda row: (
            -(int((row.get("work_priority") or "0").strip() or "0")),
            row.get("jurisdiction") or "",
            row.get("lake_name") or "",
        )
    )
    if args.offset:
        target_rows = target_rows[args.offset :]
    if args.limit and args.limit > 0:
        target_rows = target_rows[: args.limit]

    results: list[dict[str, Any]] = []
    label_counts: Counter[str] = Counter()
    workers = max(1, args.workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(probe_row, row, args.timeout, args.delay_seconds)
            for row in target_rows
        ]
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result["source_hint_family"]:
                label_counts[result["source_hint_family"]] += 1
            results.append(result)
            if index % 25 == 0 or index == len(target_rows):
                print(f"[{index}/{len(target_rows)}] probed")

    results.sort(key=lambda row: (row["jurisdiction"], row["lake_name"], row["lake_url"]))

    write_csv(args.output_csv, results)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(results),
        "hit_count": sum(1 for row in results if row["source_hint_family"]),
        "label_counts": dict(label_counts),
        "jurisdictions": sorted(jurisdictions),
        "classification": args.classification,
        "limit": args.limit,
        "offset": args.offset,
        "workers": workers,
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(results)} probe rows")
    print(f"CSV: {args.output_csv}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
