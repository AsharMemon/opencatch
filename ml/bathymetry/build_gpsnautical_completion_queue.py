#!/usr/bin/env python3
"""
Build a per-lake completion queue from the GPS Nautical source master.

This takes the lake-by-lake source classification queue and translates it into
execution stages we can work through to completion as new inventory rows arrive.

Output stages are designed to match the current OpenCatch bathymetry workflow:

- crosswalk_existing_live
- fetch_direct_official_source
- resolve_official_pdf_and_digitize
- reservoir_program_search
- supporting_lane_upgrade_search
- project_index_resolution
- manual_retrieval
- ml_fallback
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical")
DEFAULT_INPUT = BASE_DIR / "gpsnautical_source_master.csv"
DEFAULT_OUTPUT_CSV = BASE_DIR / "gpsnautical_completion_queue.csv"
DEFAULT_OUTPUT_JSON = BASE_DIR / "gpsnautical_completion_queue.json"
DEFAULT_SUMMARY_JSON = BASE_DIR / "gpsnautical_completion_queue_summary.json"


JURISDICTION_ARTIFACTS: dict[str, dict[str, str]] = {
    "Georgia": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/ga/ga_pfa_pdf_inventory.csv",
        "upstream_manifest_path": "/Users/Ashar/Documents/fish/data/bathymetry/ga/pdfs/download_manifest.json",
        "pipeline_hint": "digitize_pdf_bathymetry.py + package_pdf_promotion_run.py",
    },
    "Kentucky": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/ky/ky_kdfwr_contour_inventory.csv",
        "upstream_manifest_path": "",
        "pipeline_hint": "scrape_ky_kdfwr_contour_inventory.py -> fetch official KML contours -> normalize_survey_geojson.py -> build_survey_pmtiles.py",
    },
    "Louisiana": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/la/la_ldwf_plan_inventory.csv",
        "upstream_manifest_path": "/Users/Ashar/Documents/fish/data/bathymetry/la/manual_queue/la_ldwf_manual_retrieval_queue.csv",
        "pipeline_hint": "manual retrieval queue -> digitize_pdf_bathymetry.py",
    },
    "Maryland": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/md/md_mgs_bathymetry_inventory.csv",
        "upstream_manifest_path": "",
        "pipeline_hint": "scrape_md_mgs_bathymetry_inventory.py -> digitize_pdf_bathymetry.py -> package_pdf_promotion_run.py",
    },
    "Mississippi": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv",
        "upstream_manifest_path": "",
        "pipeline_hint": "download_pdf_inventory_batch.py -> digitize_pdf_bathymetry.py -> package_pdf_promotion_run.py",
    },
    "New Brunswick": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv",
        "upstream_manifest_path": "/Users/Ashar/Documents/fish/data/bathymetry/nb/nbhn_waterbody_polygons.geojson",
        "pipeline_hint": "digitize_pdf_bathymetry.py + rerun_pdf_georef_failures.py + package_pdf_promotion_run.py",
    },
    "Newfoundland & Labrador": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/nl/nl_water_resources_index.csv",
        "upstream_manifest_path": "",
        "pipeline_hint": "project/index resolution -> targeted public asset harvest",
    },
    "North Carolina": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/nc/nc_reservoir_report_inventory.csv",
        "upstream_manifest_path": "/Users/Ashar/Documents/fish/data/bathymetry/nc/calibration/nc_reservoir_calibration_metadata.csv",
        "pipeline_hint": "reservoir report crosswalk -> calibration metadata -> targeted geometry search",
    },
    "Nova Scotia": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv",
        "upstream_manifest_path": "/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_survey_points.geojson",
        "pipeline_hint": "digitize_pdf_bathymetry.py + rerun_pdf_georef_failures.py + package_pdf_promotion_run.py",
    },
    "Prince Edward Island": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/pe/pei_publication_inventory.csv",
        "upstream_manifest_path": "",
        "pipeline_hint": "project/publication resolution -> targeted fetch/digitize",
    },
    "Saskatchewan": {
        "upstream_inventory_path": "",
        "upstream_manifest_path": "",
        "pipeline_hint": "viewer/index resolution -> geometry extraction",
    },
    "South Dakota": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv",
        "upstream_manifest_path": "/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_big_waterbody_polygons.geojson",
        "pipeline_hint": "digitize_pdf_bathymetry.py + official polygon crosswalk + package_pdf_promotion_run.py",
    },
    "South Carolina": {
        "upstream_inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/sc/scdnr_lake_brochure_inventory.csv",
        "upstream_manifest_path": "",
        "pipeline_hint": "scrape_scdnr_lake_brochure_inventory.py -> digitize_pdf_bathymetry.py -> package_pdf_promotion_run.py",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def split_jurisdictions(raw: str) -> list[str]:
    return [part.strip() for part in raw.split("/") if part.strip()]


def classify_stage(row: dict[str, str]) -> tuple[str, str, str]:
    classification = row["classification"]
    status = row["jurisdiction_status"]
    page_source_family = row["page_source_family"]
    access_mode = row["access_mode"]

    if page_source_family:
        return "fetch_direct_official_source", "ready_now", "Explicit official source hint found on the page."
    if classification == "direct_official_source_found":
        return "crosswalk_existing_live", "ready_now", "Jurisdiction is already survey live; crosswalk the lake against the existing official lane."
    if classification == "pdf_chart_digitization_candidate":
        if access_mode == "manual_retrieval":
            return "manual_retrieval", "blocked_manual", "Public official asset trail exists, but retrieval is still blocked/manual."
        return "resolve_official_pdf_and_digitize", "ready_now", "Official PDF/chart lane exists and should be resolved and digitized."
    if classification == "ml_fallback_only":
        return "ml_fallback", "fallback_ready", "No reliable official inland source is currently available."
    if "Reservoir subset" in status:
        return "reservoir_program_search", "ready_now", "Waterbody should be checked against reservoir-specific federal/state survey programs."
    if "Supporting live" in status:
        return "supporting_lane_upgrade_search", "ready_now", "A supporting lane exists, but we still need stronger official geometry."
    if "Project/index-supported" in status:
        return "project_index_resolution", "ready_now", "Project/index trail exists, but lake-specific geometry still needs to be resolved."
    return "supporting_lane_upgrade_search", "ready_now", "Needs a jurisdiction-level official source search."


def priority_for_stage(stage: str) -> str:
    high = {
        "fetch_direct_official_source",
        "resolve_official_pdf_and_digitize",
        "reservoir_program_search",
    }
    medium = {
        "crosswalk_existing_live",
        "supporting_lane_upgrade_search",
        "project_index_resolution",
    }
    if stage in high:
        return "high"
    if stage in medium:
        return "medium"
    return "low"


def choose_artifact(jurisdictions: list[str]) -> dict[str, str]:
    chosen = {
        "upstream_inventory_path": "",
        "upstream_manifest_path": "",
        "pipeline_hint": "",
    }
    for jurisdiction in jurisdictions:
        artifact = JURISDICTION_ARTIFACTS.get(jurisdiction)
        if artifact:
            return artifact
    return chosen


def build_queue_row(row: dict[str, str]) -> dict[str, Any]:
    jurisdictions = split_jurisdictions(row["jurisdiction"])
    stage, execution_status, blocking_reason = classify_stage(row)
    artifacts = choose_artifact(jurisdictions)
    return {
        **row,
        "completion_stage": stage,
        "execution_status": execution_status,
        "work_priority": priority_for_stage(stage),
        "blocking_reason": blocking_reason,
        "upstream_inventory_path": artifacts["upstream_inventory_path"],
        "upstream_manifest_path": artifacts["upstream_manifest_path"],
        "pipeline_hint": artifacts["pipeline_hint"],
        "completion_target": {
            "crosswalk_existing_live": "Lake mapped to an existing live official survey-backed source.",
            "fetch_direct_official_source": "Official upstream source fetched and recorded for this lake.",
            "resolve_official_pdf_and_digitize": "Public chart/PDF fetched, digitized, normalized, tiled, and published.",
            "reservoir_program_search": "Reservoir-specific official survey source found and queued through fetch/normalize/tile.",
            "supporting_lane_upgrade_search": "Lake upgraded from supporting lane to richer contour geometry.",
            "project_index_resolution": "Project/index lead resolved into a fetchable public source or an explicit fallback decision.",
            "manual_retrieval": "Blocked official asset manually recovered, then digitized/normalized/tiled if legal and public.",
            "ml_fallback": "No better official source found; lake stays in ML fallback with explicit provenance.",
        }[stage],
    }


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


def main() -> None:
    args = parse_args()
    source_rows = load_rows(args.input)
    queue_rows = [build_queue_row(row) for row in source_rows]
    queue_rows.sort(key=lambda row: (row["work_priority"], row["completion_stage"], row["jurisdiction"], row["lake_name"]))
    write_csv(args.output_csv, queue_rows)
    write_json(args.output_json, queue_rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(queue_rows),
        "stage_counts": dict(Counter(row["completion_stage"] for row in queue_rows)),
        "execution_status_counts": dict(Counter(row["execution_status"] for row in queue_rows)),
        "work_priority_counts": dict(Counter(row["work_priority"] for row in queue_rows)),
        "jurisdiction_counts": dict(Counter(row["jurisdiction"] for row in queue_rows)),
        "input": str(args.input),
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(queue_rows)} rows")
    print(f"CSV: {args.output_csv}")
    print(f"JSON: {args.output_json}")
    print(f"Summary: {args.summary_json}")
    print("Stage counts:")
    for key, value in summary["stage_counts"].items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
