#!/usr/bin/env python3
"""
Build structured calibration metadata from North Carolina reservoir reports.

The downloaded NC reservoir reports are useful mainly as calibration/context
artifacts rather than direct contour geometry. This script converts the raw
report-signal extraction into a cleaner metadata table for downstream model and
evaluation workflows.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path


SIGNALS_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/nc/extracted/nc_reservoir_report_signals.json")
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/nc/calibration")
OUT_CSV = OUT_DIR / "nc_reservoir_calibration_metadata.csv"
OUT_JSON = OUT_DIR / "nc_reservoir_calibration_metadata.json"

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
METER_RES_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*-\s*m\b|\b(\d+(?:\.\d+)?)\s*m resolution\b", re.IGNORECASE)


def load_rows() -> list[dict]:
    return json.loads(SIGNALS_JSON.read_text())


def extract_resolution(text: str) -> str:
    match = METER_RES_RE.search(text)
    if not match:
        return ""
    return match.group(1) or match.group(2) or ""


def extract_years(text: str) -> list[str]:
    years = sorted(set(YEAR_RE.findall(text)))
    # YEAR_RE has a capture group for prefix; recompute full years directly
    full = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", text)))
    return full[:10]


def classify_report(row: dict) -> dict:
    excerpt = row.get("clue_excerpt", "")
    calibration_ready = any(
        [
            int(row.get("bathymetry_hits") or 0) > 0,
            int(row.get("stage_area_hits") or 0) > 0,
            int(row.get("stage_volume_hits") or 0) > 0,
            int(row.get("calibration_hits") or 0) > 0,
        ]
    )
    metadata = {
        "lake_name": row.get("lake_name", ""),
        "report_type": row.get("report_type", ""),
        "local_path": row.get("local_path", ""),
        "bathymetry_backed": "yes" if int(row.get("bathymetry_hits") or 0) > 0 else "no",
        "stage_area_backed": "yes" if int(row.get("stage_area_hits") or 0) > 0 else "no",
        "stage_volume_backed": "yes" if int(row.get("stage_volume_hits") or 0) > 0 else "no",
        "segmented_reservoir_model": "yes" if int(row.get("segment_hits") or 0) >= 3 else "no",
        "calibration_ready": "yes" if calibration_ready else "no",
        "monitoring_support": "yes" if int(row.get("monitoring_hits") or 0) > 0 else "no",
        "bathymetry_pages": row.get("bathymetry_pages", ""),
        "calibration_pages": row.get("calibration_pages", ""),
        "survey_pages": row.get("survey_pages", ""),
        "stage_area_pages": row.get("stage_area_pages", ""),
        "stage_volume_pages": row.get("stage_volume_pages", ""),
        "resolution_m": extract_resolution(excerpt),
        "mentioned_years": ",".join(extract_years(excerpt)),
        "support_excerpt": excerpt[:1200],
    }
    return metadata


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [classify_report(row) for row in load_rows()]

    fieldnames = [
        "lake_name",
        "report_type",
        "local_path",
        "bathymetry_backed",
        "stage_area_backed",
        "stage_volume_backed",
        "segmented_reservoir_model",
        "calibration_ready",
        "monitoring_support",
        "bathymetry_pages",
        "calibration_pages",
        "survey_pages",
        "stage_area_pages",
        "stage_volume_pages",
        "resolution_m",
        "mentioned_years",
        "support_excerpt",
    ]

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    OUT_JSON.write_text(json.dumps(rows, indent=2))
    print(f"Wrote {OUT_CSV} and {OUT_JSON} ({len(rows)} reports)")


if __name__ == "__main__":
    main()
