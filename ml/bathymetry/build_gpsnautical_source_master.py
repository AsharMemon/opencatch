#!/usr/bin/env python3
"""
Build a lake-by-lake source queue from GPS Nautical discovery inventories.

This treats GPS Nautical / i-Boating pages as a coverage manifest only. The
goal is to classify each lake into a next-step bucket so we can fetch the real
upstream source ourselves:

- direct_official_source_found
- needs_jurisdiction_search
- pdf_chart_digitization_candidate
- ml_fallback_only

The builder uses:
1. Lake inventory rows from the US and Canada GPS discovery crawls.
2. Jurisdiction-level acquisition knowledge from the current OpenCatch tracker.
3. Optional page-level source probing for explicit official hints on chart pages.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical")
DEFAULT_US_INPUT = BASE_DIR / "gpsnautical_lake_inventory.csv"
DEFAULT_CA_INPUT = BASE_DIR / "gpsnautical_ca_lake_inventory.csv"
DEFAULT_OUTPUT_CSV = BASE_DIR / "gpsnautical_source_master.csv"
DEFAULT_OUTPUT_JSON = BASE_DIR / "gpsnautical_source_master.json"
DEFAULT_SUMMARY_JSON = BASE_DIR / "gpsnautical_source_master_summary.json"

USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"

SURVEY_LIVE = {
    "Alabama",
    "Alaska",
    "Alberta",
    "Arkansas",
    "British Columbia",
    "Connecticut",
    "Delaware",
    "Florida",
    "Illinois",
    "Indiana",
    "Kansas",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Montana",
    "Nebraska",
    "New Hampshire",
    "North Dakota",
    "Ohio",
    "Ontario",
    "Quebec",
    "Texas",
    "Vermont",
    "Washington",
}

SUPPORTING_LIVE = {
    "Hawaii": {
        "source_family": "derived_depth_band_lane",
        "access_mode": "existing_supporting_lane",
        "next_step": "Keep current derived lane while searching for a stronger official statewide source.",
    },
    "Iowa": {
        "source_family": "iowa_dnr_depth_band_lane",
        "access_mode": "existing_supporting_lane",
        "next_step": "Promote Iowa DNR lake assets into richer contour geometry.",
    },
    "Maine": {
        "source_family": "maine_ifw_survey_index",
        "access_mode": "survey_index_search",
        "next_step": "Resolve the lake against Maine IF&W survey index assets and fetch downloadable maps/data.",
    },
    "Missouri": {
        "source_family": "sounding_depth_point_lane",
        "access_mode": "supporting_lane_search",
        "next_step": "Extend beyond the current sounding pilot using official Missouri lake sources.",
    },
    "New Jersey": {
        "source_family": "dep_lake_plan_pdf_index",
        "access_mode": "pdf_index_search",
        "next_step": "Resolve the lake against NJ DEP lake-plan PDFs and related official reports.",
    },
    "New York": {
        "source_family": "dec_contour_page_index",
        "access_mode": "map_page_search",
        "next_step": "Resolve the lake against NY DEC contour/map pages and linked official assets.",
    },
    "Northwest Territories": {
        "source_family": "gnwt_inland_water_leads",
        "access_mode": "service_and_project_search",
        "next_step": "Inspect GNWT inland-water service layers and project material for direct geometry.",
    },
    "Oklahoma": {
        "source_family": "owrb_depth_point_lane",
        "access_mode": "supporting_lane_search",
        "next_step": "Promote OWRB point/summary assets into richer geometry.",
    },
    "Pennsylvania": {
        "source_family": "pfbc_lake_footprint_lane",
        "access_mode": "footprint_plus_contour_search",
        "next_step": "Pair PFBC lake footprints with actual contour/depth assets.",
    },
    "Rhode Island": {
        "source_family": "dem_project_index",
        "access_mode": "project_index_search",
        "next_step": "Promote RI DEM project/index assets into lake-specific contour retrieval.",
    },
    "Saskatchewan": {
        "source_family": "sask_bathymetric_viewer_index",
        "access_mode": "viewer_geometry_search",
        "next_step": "Resolve the lake through the Saskatchewan bathymetric viewer/index and extract geometry.",
    },
    "Virginia": {
        "source_family": "dwr_waterbody_index",
        "access_mode": "map_pdf_subset_search",
        "next_step": "Resolve the lake against DWR waterbody pages and linked PDF/map subsets.",
    },
    "West Virginia": {
        "source_family": "wvdnr_lake_map_pdf_index",
        "access_mode": "pdf_index_search",
        "next_step": "Resolve the lake against WVDNR map PDFs and related downloadable assets.",
    },
    "Wisconsin": {
        "source_family": "hypsography_surrogate_lane",
        "access_mode": "surrogate_plus_official_search",
        "next_step": "Use the current hypsography surrogate while continuing official contour source discovery.",
    },
}

PDF_UPGRADE = {
    "Georgia": {
        "source_family": "georgia_dnr_pfa_pdfs",
        "access_mode": "pdf_or_chart_digitization",
        "next_step": "Resolve the lake against Georgia DNR PFAs, verified guides, and the official PFA polygon layer.",
    },
    "Kentucky": {
        "source_family": "kdfwr_lake_maps_and_fins",
        "access_mode": "pdf_or_chart_digitization",
        "next_step": "Resolve the lake against KDFWR lake maps, FINs, and fisheries bulletins.",
    },
    "Louisiana": {
        "source_family": "ldwf_lake_management_plan_pdfs",
        "access_mode": "manual_pdf_retrieval",
        "next_step": "Use the Louisiana manual retrieval queue and search-index hits, then digitize the public chart or plan.",
    },
    "Manitoba": {
        "source_family": "manitoba_named_lake_reports",
        "access_mode": "pdf_or_report_search",
        "next_step": "Harvest official Manitoba lake reports and bathymetry PDFs for the named lake.",
    },
    "Maryland": {
        "source_family": "maryland_geological_survey_bathymetry",
        "access_mode": "pdf_or_data_page_search",
        "next_step": "Resolve the lake against Maryland Geological Survey reservoir bathymetry pages and downloads.",
    },
    "Mississippi": {
        "source_family": "mdwfp_lake_depth_maps",
        "access_mode": "pdf_or_chart_digitization",
        "next_step": "Resolve the lake against MDWFP lake depth maps and digitize the public chart if needed.",
    },
    "New Brunswick": {
        "source_family": "nb_interactive_bathymetry_and_pdfs",
        "access_mode": "pdf_or_service_crosswalk",
        "next_step": "Resolve the lake against the New Brunswick bathymetry inventory, PDFs, and official bathy map service.",
    },
    "Nova Scotia": {
        "source_family": "nova_scotia_lake_inventory_pdfs",
        "access_mode": "pdf_or_chart_digitization",
        "next_step": "Resolve the lake against the Nova Scotia Lake Inventory PDFs and digitize the public map.",
    },
    "South Carolina": {
        "source_family": "scdnr_lake_brochures_and_maps",
        "access_mode": "pdf_or_chart_digitization",
        "next_step": "Resolve the lake against SCDNR brochures and lake map PDFs.",
    },
    "South Dakota": {
        "source_family": "sd_gfp_lake_maps_and_survey_reports",
        "access_mode": "pdf_or_chart_digitization",
        "next_step": "Resolve the lake against South Dakota GFP Lake Maps first, then survey reports.",
    },
}

RESERVOIR_SUBSET = {
    "Arizona": {
        "source_family": "usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check USBR reservoir surveys for this named reservoir before falling back to broader state search.",
    },
    "California": {
        "source_family": "dwr_plus_usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check California DWR and USBR reservoir programs for the named reservoir or lake.",
    },
    "Colorado": {
        "source_family": "usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check USBR reservoir surveys and related Colorado summaries for the named reservoir.",
    },
    "Idaho": {
        "source_family": "usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check USBR reservoir surveys for the named Idaho reservoir.",
    },
    "Nevada": {
        "source_family": "usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check USBR reservoir surveys for the named Nevada reservoir.",
    },
    "New Mexico": {
        "source_family": "usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check USBR reservoir surveys for the named New Mexico reservoir.",
    },
    "North Carolina": {
        "source_family": "nc_reservoir_reports_and_models",
        "access_mode": "reservoir_program_search",
        "next_step": "Resolve the waterbody against NC reservoir reports and bathymetry-backed studies like Jordan Lake.",
    },
    "Oregon": {
        "source_family": "usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check USBR reservoir surveys for the named Oregon reservoir.",
    },
    "Tennessee": {
        "source_family": "tva_and_usace_reservoir_charts",
        "access_mode": "reservoir_program_search",
        "next_step": "Resolve the lake against TVA and USACE reservoir charts and surveys.",
    },
    "Utah": {
        "source_family": "usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check USBR reservoir surveys for the named Utah reservoir.",
    },
    "Wyoming": {
        "source_family": "usbr_reservoir_surveys",
        "access_mode": "reservoir_program_search",
        "next_step": "Check USBR reservoir surveys for the named Wyoming reservoir.",
    },
}

PROJECT_INDEX = {
    "Newfoundland & Labrador": {
        "source_family": "nl_water_resources_project_index",
        "access_mode": "project_index_search",
        "next_step": "Resolve the lake against Newfoundland & Labrador water-resources atlas/report/project links.",
    },
    "Nunavut": {
        "source_family": "nunavut_project_and_procurement_trail",
        "access_mode": "project_index_search",
        "next_step": "Use the Nunavut project and procurement trail to find lake-specific or regional bathymetry assets.",
    },
    "Prince Edward Island": {
        "source_family": "pei_project_and_publication_index",
        "access_mode": "project_index_search",
        "next_step": "Resolve the waterbody against PEI angling/publication/project assets.",
    },
}

WITHDRAWN = {
    "Yukon": {
        "source_family": "withdrawn_official_bathymetry",
        "access_mode": "fallback_only",
        "next_step": "Use ML fallback plus Yukon e-chart context until a newer official inland bathymetry program appears.",
    }
}

PROVINCE_CODE_MAP = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland & Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}

REGION_FALLBACK_MAP = {
    "Alberta Lakes": ["Alberta"],
    "Georgian Bay": ["Ontario"],
    "Great lakes Canada and St. Lawrence river": ["Ontario", "Quebec"],
    "Gulf of St. Lawrence North - Newfoundland West": ["Newfoundland & Labrador", "Quebec"],
    "Gulf of St. Lawrence South": ["New Brunswick", "Nova Scotia", "Prince Edward Island"],
    "Labrador Coast": ["Newfoundland & Labrador"],
    "Lakes and Rivers in Manitoba": ["Manitoba"],
    "Lakes and Rivers of British Columbia": ["British Columbia"],
    "Lakes around Georgian Bay": ["Ontario"],
    "New Brunswick Fishing Maps": ["New Brunswick"],
    "Newfoundland East and South": ["Newfoundland & Labrador"],
    "Northern Canada": ["Northwest Territories", "Nunavut", "Yukon"],
    "Nova Scotia South - Bay of Fundy": ["Nova Scotia"],
    "Nunavut Fishing Maps": ["Nunavut"],
    "Ontario Lakes": ["Ontario"],
    "Pacific Coast - Vancouver Island East & West - Haida Gwaii": ["British Columbia"],
    "Quebec to Anticosti Island West": ["Quebec"],
    "Rainy Lake and Lake of the Woods": ["Ontario", "Manitoba"],
    "Rideau Canal - Ottawa River": ["Ontario", "Quebec"],
    "Saskatchewan Fishing Maps": ["Saskatchewan"],
    "Trent-Severn Waterway": ["Ontario"],
    "Yukon Fishing Maps": ["Yukon"],
}

OFFICIAL_PAGE_PATTERNS = [
    ("NOAA", re.compile(r"\bNOAA\b", re.I)),
    ("CHS", re.compile(r"\bCHS\b|Canadian Hydrographic Service", re.I)),
    ("USACE", re.compile(r"\bUSACE\b|Army Corps|Corps of Engineers", re.I)),
    ("USBR", re.compile(r"\bUSBR\b|Bureau of Reclamation", re.I)),
    ("TVA", re.compile(r"\bTVA\b|Tennessee Valley Authority", re.I)),
    ("DNR", re.compile(r"\bDNR\b|Department of Natural Resources", re.I)),
    ("DEC", re.compile(r"\bDEC\b|Department of Environmental Conservation", re.I)),
    ("DNR/DEC/DEP", re.compile(r"\bDEP\b|\bDEQ\b|\bDEM\b|\bIFW\b|\bPFBC\b", re.I)),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-us", type=Path, default=DEFAULT_US_INPUT)
    parser.add_argument("--input-ca", type=Path, default=DEFAULT_CA_INPUT)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument(
        "--probe-page-sources",
        action="store_true",
        help="Fetch lake detail pages and search for explicit official source hints.",
    )
    parser.add_argument(
        "--max-probes",
        type=int,
        default=0,
        help="Cap page probes for debugging. 0 means no cap.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.15,
        help="Delay between page probes.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout seconds for page probes.",
    )
    return parser.parse_args()


class PageProbe:
    def __init__(self, timeout: int, delay_seconds: float) -> None:
        self.timeout = timeout
        self.delay_seconds = delay_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.cache: dict[str, dict[str, str]] = {}

    def detect(self, url: str) -> dict[str, str]:
        if url in self.cache:
            return self.cache[url]
        result = {
            "page_source_hint": "",
            "page_source_family": "",
            "page_source_origin": "",
            "page_source_confidence": "",
        }
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            text = " ".join(soup.get_text(" ", strip=True).split())
            for label, pattern in OFFICIAL_PAGE_PATTERNS:
                match = pattern.search(text)
                if not match:
                    continue
                snippet = text[max(0, match.start() - 60) : match.start() + 120]
                result = {
                    "page_source_hint": snippet.strip(),
                    "page_source_family": label,
                    "page_source_origin": "page_probe",
                    "page_source_confidence": "medium",
                }
                break
        except Exception:
            pass
        self.cache[url] = result
        time.sleep(self.delay_seconds)
        return result


def infer_canada_jurisdictions(row: dict[str, str]) -> list[str]:
    chart_id = (row.get("chart_id") or "").strip()
    if chart_id.startswith("CA_") and len(chart_id) >= 5:
        province = PROVINCE_CODE_MAP.get(chart_id[3:5].upper())
        if province:
            return [province]

    lake_url = (row.get("lake_url") or "").strip()
    match = re.search(r"/main/ca_([a-z]{2})_", lake_url, re.I)
    if match:
        province = PROVINCE_CODE_MAP.get(match.group(1).upper())
        if province:
            return [province]

    return REGION_FALLBACK_MAP.get((row.get("region") or "").strip(), ["Unknown"])


def jurisdiction_meta(jurisdiction: str) -> dict[str, str]:
    if jurisdiction in SURVEY_LIVE:
        return {
            "jurisdiction_status": "Survey live",
            "default_source_family": "existing_live_official_survey",
            "default_access_mode": "existing_live_lane",
            "default_next_step": "Crosswalk this lake against the existing live official survey-backed source for the jurisdiction.",
        }
    if jurisdiction in PDF_UPGRADE:
        meta = PDF_UPGRADE[jurisdiction]
        return {
            "jurisdiction_status": "PDF upgrade",
            "default_source_family": meta["source_family"],
            "default_access_mode": meta["access_mode"],
            "default_next_step": meta["next_step"],
        }
    if jurisdiction in RESERVOIR_SUBSET:
        meta = RESERVOIR_SUBSET[jurisdiction]
        return {
            "jurisdiction_status": "Reservoir subset",
            "default_source_family": meta["source_family"],
            "default_access_mode": meta["access_mode"],
            "default_next_step": meta["next_step"],
        }
    if jurisdiction in SUPPORTING_LIVE:
        meta = SUPPORTING_LIVE[jurisdiction]
        return {
            "jurisdiction_status": "Supporting live",
            "default_source_family": meta["source_family"],
            "default_access_mode": meta["access_mode"],
            "default_next_step": meta["next_step"],
        }
    if jurisdiction in PROJECT_INDEX:
        meta = PROJECT_INDEX[jurisdiction]
        return {
            "jurisdiction_status": "Project/index-supported",
            "default_source_family": meta["source_family"],
            "default_access_mode": meta["access_mode"],
            "default_next_step": meta["next_step"],
        }
    if jurisdiction in WITHDRAWN:
        meta = WITHDRAWN[jurisdiction]
        return {
            "jurisdiction_status": "Withdrawn official data",
            "default_source_family": meta["source_family"],
            "default_access_mode": meta["access_mode"],
            "default_next_step": meta["next_step"],
        }
    return {
        "jurisdiction_status": "Unmapped",
        "default_source_family": "unknown",
        "default_access_mode": "jurisdiction_search",
        "default_next_step": "Search official state/province sources for this lake; no jurisdiction mapping exists yet.",
    }


def combine_meta(jurisdictions: list[str]) -> dict[str, str]:
    metas = [jurisdiction_meta(j) for j in jurisdictions]
    statuses = [m["jurisdiction_status"] for m in metas]
    source_families = [m["default_source_family"] for m in metas]
    access_modes = [m["default_access_mode"] for m in metas]
    next_steps = [m["default_next_step"] for m in metas]

    if len(set(statuses)) == 1:
        status = statuses[0]
    else:
        status = " / ".join(dict.fromkeys(statuses))

    return {
        "jurisdiction_status": status,
        "default_source_family": " | ".join(dict.fromkeys(source_families)),
        "default_access_mode": " | ".join(dict.fromkeys(access_modes)),
        "default_next_step": " ".join(dict.fromkeys(next_steps)),
    }


def classify(meta: dict[str, str], page_source_family: str) -> tuple[str, str]:
    status = meta["jurisdiction_status"]
    if page_source_family:
        return "direct_official_source_found", "high"
    if status == "Survey live":
        return "direct_official_source_found", "low"
    if "PDF upgrade" in status:
        return "pdf_chart_digitization_candidate", "high"
    if status == "Withdrawn official data":
        return "ml_fallback_only", "low"
    return "needs_jurisdiction_search", "medium"


def build_search_seed(lake_name: str, jurisdictions: list[str], source_family: str) -> str:
    parts = [lake_name.strip()]
    if jurisdictions:
        parts.append(" / ".join(jurisdictions))
    if source_family and source_family != "unknown":
        parts.append(source_family.replace("_", " "))
    parts.append("bathymetry")
    return " ".join(part for part in parts if part)


def normalize_row(catalog: str, row: dict[str, str], probe: PageProbe | None, probes_done: list[int], max_probes: int) -> dict[str, Any]:
    if catalog == "us":
        jurisdictions = [(row.get("state") or "").strip() or "Unknown"]
        region_or_state = (row.get("state") or "").strip()
        subregion_or_county = (row.get("county") or "").strip()
    else:
        jurisdictions = infer_canada_jurisdictions(row)
        region_or_state = (row.get("region") or "").strip()
        subregion_or_county = (row.get("subregion") or "").strip()

    meta = combine_meta(jurisdictions)
    page_probe_result = {
        "page_source_hint": "",
        "page_source_family": "",
        "page_source_origin": "",
        "page_source_confidence": "",
    }
    if probe is not None and row.get("lake_url"):
        if max_probes == 0 or probes_done[0] < max_probes:
            page_probe_result = probe.detect(row["lake_url"])
            probes_done[0] += 1

    classification, priority = classify(meta, page_probe_result["page_source_family"])
    if page_probe_result["page_source_family"]:
        source_family = page_probe_result["page_source_family"]
        access_mode = "direct_official_fetch"
        next_step = "Fetch the official upstream source referenced on the chart page and replace the commercial page as the canonical source."
    else:
        source_family = meta["default_source_family"]
        access_mode = meta["default_access_mode"]
        next_step = meta["default_next_step"]

    return {
        "catalog": catalog,
        "jurisdiction": " / ".join(jurisdictions),
        "jurisdiction_status": meta["jurisdiction_status"],
        "region_or_state": region_or_state,
        "subregion_or_county": subregion_or_county,
        "lake_name": (row.get("lake_name") or "").strip(),
        "lake_url": (row.get("lake_url") or "").strip(),
        "chart_id": (row.get("chart_id") or "").strip(),
        "listed_scale": (row.get("listed_scale") or "").strip(),
        "title": (row.get("title") or "").strip(),
        "detail_fetch_status": (row.get("detail_fetch_status") or "").strip(),
        "page_source_hint": page_probe_result["page_source_hint"],
        "page_source_family": page_probe_result["page_source_family"],
        "page_source_origin": page_probe_result["page_source_origin"],
        "page_source_confidence": page_probe_result["page_source_confidence"],
        "default_source_family": meta["default_source_family"],
        "access_mode": access_mode,
        "classification": classification,
        "priority": priority,
        "recommended_next_step": next_step,
        "search_seed": build_search_seed((row.get("lake_name") or "").strip(), jurisdictions, source_family),
        "inventory_source": (row.get("inventory_source") or "").strip(),
        "license_note": (row.get("license_note") or "").strip(),
        "source_page_domain": urlparse((row.get("lake_url") or "").strip()).netloc,
    }


def read_inventory(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
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


def main() -> None:
    args = parse_args()
    probe = PageProbe(timeout=args.timeout, delay_seconds=args.delay_seconds) if args.probe_page_sources else None
    probes_done = [0]

    rows: list[dict[str, Any]] = []
    for raw in read_inventory(args.input_us):
        rows.append(normalize_row("us", raw, probe, probes_done, args.max_probes))
    for raw in read_inventory(args.input_ca):
        rows.append(normalize_row("ca", raw, probe, probes_done, args.max_probes))

    rows.sort(key=lambda row: (row["classification"], row["jurisdiction"], row["lake_name"]))
    write_csv(args.output_csv, rows)
    write_json(args.output_json, rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(rows),
        "classification_counts": dict(Counter(row["classification"] for row in rows)),
        "jurisdiction_status_counts": dict(Counter(row["jurisdiction_status"] for row in rows)),
        "jurisdiction_counts": dict(Counter(row["jurisdiction"] for row in rows)),
        "page_source_hits": sum(1 for row in rows if row["page_source_family"]),
        "probe_count": probes_done[0],
        "inputs": {
            "us": str(args.input_us),
            "ca": str(args.input_ca),
        },
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(rows)} rows")
    print(f"CSV: {args.output_csv}")
    print(f"JSON: {args.output_json}")
    print(f"Summary: {args.summary_json}")
    print("Classification counts:")
    for key, value in summary["classification_counts"].items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
