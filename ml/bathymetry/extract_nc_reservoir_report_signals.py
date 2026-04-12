#!/usr/bin/env python3
"""
Extract bathymetry / calibration signals from North Carolina reservoir reports.

These NC PDFs are not contour maps, but they contain useful structured evidence:
mentions of bathymetry surveys, stage-area / stage-volume relationships, segment
structure, and monitoring/calibration setups. This script turns those reports
into machine-readable summary artifacts for downstream lake calibration work.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from pypdf import PdfReader


INPUT_MANIFEST = Path("/Users/Ashar/Documents/fish/data/bathymetry/nc/pdfs/download_manifest.json")
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/nc/extracted")
OUT_CSV = OUT_DIR / "nc_reservoir_report_signals.csv"
OUT_JSON = OUT_DIR / "nc_reservoir_report_signals.json"

KEYWORDS = {
    "bathymetry": re.compile(r"\bbathymetr\w*\b", re.IGNORECASE),
    "survey": re.compile(r"\bsurvey\b", re.IGNORECASE),
    "stage_area": re.compile(r"stage[- ]area", re.IGNORECASE),
    "stage_volume": re.compile(r"stage[- ]volume", re.IGNORECASE),
    "storage": re.compile(r"\bstorage\b", re.IGNORECASE),
    "segment": re.compile(r"\bsegment\b", re.IGNORECASE),
    "calibration": re.compile(r"\bcalibrat\w*\b", re.IGNORECASE),
    "monitoring": re.compile(r"\bmonitor\w*\b", re.IGNORECASE),
    "contour": re.compile(r"\bcontour\w*\b", re.IGNORECASE),
    "depth": re.compile(r"\bdepth\b", re.IGNORECASE),
}


def load_manifest(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def extract_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def summarize_report(record: dict) -> dict:
    pdf_path = Path(record["local_path"])
    pages = extract_pages(pdf_path)
    full_text = "\n".join(pages)

    summary = {
        "lake_name": record.get("lake_name") or "",
        "report_type": record.get("report_type") or "",
        "local_path": str(pdf_path),
        "page_count": len(pages),
    }

    page_hits: dict[str, list[int]] = {}
    for label, pattern in KEYWORDS.items():
        hits = [idx + 1 for idx, text in enumerate(pages) if pattern.search(text)]
        summary[f"{label}_hits"] = len(hits)
        summary[f"{label}_pages"] = ",".join(str(p) for p in hits[:10])
        page_hits[label] = hits

    clue_lines: list[str] = []
    for line in re.split(r"[\r\n]+", full_text):
        normalized = " ".join(line.split())
        if not normalized:
            continue
        lower = normalized.lower()
        if any(
            key in lower
            for key in (
                "bathymetry",
                "stage-area",
                "stage area",
                "stage-volume",
                "stage volume",
                "reservoir segment",
                "calibration",
            )
        ):
            clue_lines.append(normalized)
    summary["clue_excerpt"] = " | ".join(clue_lines[:8])[:2400]
    summary["_page_hits"] = page_hits
    return summary


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(INPUT_MANIFEST)
    rows = [summarize_report(record) for record in manifest]

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "lake_name",
            "report_type",
            "local_path",
            "page_count",
            "bathymetry_hits",
            "bathymetry_pages",
            "survey_hits",
            "survey_pages",
            "stage_area_hits",
            "stage_area_pages",
            "stage_volume_hits",
            "stage_volume_pages",
            "storage_hits",
            "storage_pages",
            "segment_hits",
            "segment_pages",
            "calibration_hits",
            "calibration_pages",
            "monitoring_hits",
            "monitoring_pages",
            "contour_hits",
            "contour_pages",
            "depth_hits",
            "depth_pages",
            "clue_excerpt",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            output = {key: row.get(key, "") for key in fieldnames}
            writer.writerow(output)

    OUT_JSON.write_text(json.dumps(rows, indent=2))
    print(f"Wrote {OUT_CSV} and {OUT_JSON} ({len(rows)} reports)")


if __name__ == "__main__":
    main()
