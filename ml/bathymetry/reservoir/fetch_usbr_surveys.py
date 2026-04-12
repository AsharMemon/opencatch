#!/usr/bin/env python3
"""
OpenCatch -- Fetch USBR Published Reservoir Survey Data

The US Bureau of Reclamation publishes bathymetric survey reports containing
Area-Elevation-Capacity tables for western US reservoirs. These are our
primary ground truth for validating the terrain extrapolation model.

Survey reports are at: https://www.usbr.gov/tsc/techreferences/reservoir.html

Each survey provides:
  - Area at each elevation (surface area in acres)
  - Capacity at each elevation (cumulative volume in acre-feet)
  - Survey year and methodology

Typical survey cost: C($) = 10,425 * days + 32,276 (USBR cost model 2023)
Survey cadence: ideally once/decade, many are decades overdue.

Usage:
    # List known surveys
    python fetch_usbr_surveys.py --list

    # Download and parse all Tier 1 surveys
    python fetch_usbr_surveys.py --tier 1 --output /data/reservoir/surveys

    # Parse a single survey PDF (requires manual PDF extraction to CSV first)
    python fetch_usbr_surveys.py --parse-csv /data/surveys/lake_powell_2018.csv \\
        --reservoir-name "Lake Powell" --output /data/reservoir/surveys

Requirements:
    pip install pandas numpy pyarrow requests
"""

import argparse
import html
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_usbr")
USBR_RESERVOIR_INDEX_URL = "https://www.usbr.gov/tsc/techreferences/reservoir.html"

# -- Known USBR Survey Catalog -----------------------------------------------
# Manually curated from https://www.usbr.gov/tsc/techreferences/reservoir.html
# Each entry includes the survey report URL and key metadata.
# A-E tables must be extracted manually from PDFs → CSV for parsing.

USBR_SURVEY_CATALOG = [
    # Tier 1: Best ground truth, largest reservoirs, published A-E tables
    {
        "reservoir_name": "Lake Powell",
        "nid_id": "UT00053",
        "state": "UT",
        "dam_name": "Glen Canyon Dam",
        "survey_years": [1986, 2001, 2018],
        "latest_survey_year": 2018,
        "max_depth_m": 170,
        "surface_area_km2": 658,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/LakePowell2018BathymetricSurvey.pdf",
        "tier": 1,
        "notes": "Multiple surveys. Large drawdown provides SWOT validation.",
    },
    {
        "reservoir_name": "Lake Mead",
        "nid_id": "NV00003",
        "state": "NV",
        "dam_name": "Hoover Dam",
        "survey_years": [1935, 1948, 1963, 2001],
        "latest_survey_year": 2001,
        "max_depth_m": 150,
        "surface_area_km2": 640,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/LakeMead2001.pdf",
        "tier": 1,
        "notes": "Extreme drawdown since 2000. Long sedimentation history.",
    },
    {
        "reservoir_name": "Flaming Gorge Reservoir",
        "nid_id": "UT00059",
        "state": "UT",
        "dam_name": "Flaming Gorge Dam",
        "survey_years": [2006],
        "latest_survey_year": 2006,
        "max_depth_m": 130,
        "surface_area_km2": 170,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/FlamingGorge2006.pdf",
        "tier": 1,
        "notes": "Deep canyon reservoir. Good terrain extrapolation candidate.",
    },
    {
        "reservoir_name": "Blue Mesa Reservoir",
        "nid_id": "CO00734",
        "state": "CO",
        "dam_name": "Blue Mesa Dam",
        "survey_years": [2005],
        "latest_survey_year": 2005,
        "max_depth_m": 100,
        "surface_area_km2": 37,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/BlueMesa2005.pdf",
        "tier": 1,
        "notes": "Moderate size. Gunnison River canyon.",
    },
    # Tier 2: Published surveys, moderate data quality
    {
        "reservoir_name": "Navajo Reservoir",
        "nid_id": "NM00140",
        "state": "NM",
        "dam_name": "Navajo Dam",
        "survey_years": [2014],
        "latest_survey_year": 2014,
        "max_depth_m": 120,
        "surface_area_km2": 62,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/NavajoReservoir2014BathymetricSurvey.pdf",
        "tier": 2,
        "notes": "San Juan River. Significant sedimentation.",
    },
    {
        "reservoir_name": "Elephant Butte Reservoir",
        "nid_id": "NM00035",
        "state": "NM",
        "dam_name": "Elephant Butte Dam",
        "survey_years": [2007],
        "latest_survey_year": 2007,
        "max_depth_m": 60,
        "surface_area_km2": 148,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/ElephantButte2007.pdf",
        "tier": 2,
        "notes": "Rio Grande. Heavy sedimentation history.",
    },
    {
        "reservoir_name": "Theodore Roosevelt Lake",
        "nid_id": "AZ00054",
        "state": "AZ",
        "dam_name": "Theodore Roosevelt Dam",
        "survey_years": [1995],
        "latest_survey_year": 1995,
        "max_depth_m": 105,
        "surface_area_km2": 69,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/TheodoreRooseveltLake1995.pdf",
        "tier": 2,
        "notes": "Salt River. Older survey.",
    },
    {
        "reservoir_name": "San Carlos Reservoir",
        "nid_id": "AZ00055",
        "state": "AZ",
        "dam_name": "Coolidge Dam",
        "survey_years": [2002],
        "latest_survey_year": 2002,
        "max_depth_m": 70,
        "surface_area_km2": 77,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/SanCarlosReservoir2002.pdf",
        "tier": 2,
        "notes": "Gila River. Severe sedimentation.",
    },
    {
        "reservoir_name": "Millerton Lake",
        "nid_id": "CA00365",
        "state": "CA",
        "dam_name": "Friant Dam",
        "survey_years": [2005],
        "latest_survey_year": 2005,
        "max_depth_m": 90,
        "surface_area_km2": 20,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/MillertonLake2005.pdf",
        "tier": 2,
        "notes": "San Joaquin River.",
    },
    # Tier 3: California DWR, other agencies
    {
        "reservoir_name": "Shasta Lake",
        "nid_id": "CA00134",
        "state": "CA",
        "dam_name": "Shasta Dam",
        "survey_years": [],
        "latest_survey_year": None,
        "max_depth_m": 150,
        "surface_area_km2": 121,
        "report_url": None,
        "tier": 3,
        "notes": "Largest CA reservoir. May have DWR survey data.",
    },
    {
        "reservoir_name": "Folsom Lake",
        "nid_id": "CA00135",
        "state": "CA",
        "dam_name": "Folsom Dam",
        "survey_years": [2005],
        "latest_survey_year": 2005,
        "max_depth_m": 96,
        "surface_area_km2": 44,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/FolsomReservoir2005.pdf",
        "tier": 3,
        "notes": "American River. Sacramento metro water supply.",
    },
    {
        "reservoir_name": "Pueblo Reservoir",
        "nid_id": "CO00250",
        "state": "CO",
        "dam_name": "Pueblo Dam",
        "survey_years": [2012],
        "latest_survey_year": 2012,
        "max_depth_m": 50,
        "surface_area_km2": 17,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/Pueblo_Reservoir_2012%20Bathymetric_Survey.pdf",
        "tier": 3,
        "notes": "Arkansas River. Published A-E tables available.",
    },
    {
        "reservoir_name": "Heron Reservoir",
        "nid_id": "NM00076",
        "state": "NM",
        "dam_name": "Heron Dam",
        "survey_years": [2010],
        "latest_survey_year": 2010,
        "max_depth_m": 40,
        "surface_area_km2": 24,
        "report_url": "https://www.usbr.gov/tsc/techreferences/reservoir/Heron%20Reservoir%202010%20Bathymetric%20Survey.pdf",
        "tier": 3,
        "notes": "Small-medium reservoir. Good for testing.",
    },
]


def parse_ae_csv(
    csv_path: Path,
    reservoir_name: str,
    survey_year: Optional[int] = None,
    elevation_col: str = "elevation_ft",
    area_col: str = "area_acres",
    capacity_col: str = "capacity_acft",
) -> pd.DataFrame:
    """Parse a manually-extracted A-E-C table from CSV.

    Expected CSV format (columns can be named differently, specify via args):
        elevation_ft, area_acres, capacity_acft
        3700, 0, 0
        3710, 50, 250
        3720, 150, 1250
        ...

    Returns standardized DataFrame with metric units.
    """
    df = pd.read_csv(csv_path)

    # Try to find columns by name matching
    col_map = {}
    for col in df.columns:
        cl = col.lower().strip()
        if "elev" in cl:
            col_map["elevation_ft"] = col
        elif "area" in cl:
            col_map["area_acres"] = col
        elif "cap" in cl or "vol" in cl or "storage" in cl:
            col_map["capacity_acft"] = col

    # Override with explicit column names
    if elevation_col in df.columns:
        col_map["elevation_ft"] = elevation_col
    if area_col in df.columns:
        col_map["area_acres"] = area_col
    if capacity_col in df.columns:
        col_map["capacity_acft"] = capacity_col

    result = pd.DataFrame()
    if "elevation_ft" in col_map:
        result["elevation_ft"] = pd.to_numeric(df[col_map["elevation_ft"]], errors="coerce")
        result["elevation_m"] = result["elevation_ft"] * 0.3048
    if "area_acres" in col_map:
        result["area_acres"] = pd.to_numeric(df[col_map["area_acres"]], errors="coerce")
        result["area_km2"] = result["area_acres"] * 0.00404686
    if "capacity_acft" in col_map:
        result["capacity_acft"] = pd.to_numeric(df[col_map["capacity_acft"]], errors="coerce")
        result["capacity_m3"] = result["capacity_acft"] * 1233.48184

    result["reservoir_name"] = reservoir_name
    result["survey_year"] = survey_year
    result = result.dropna(subset=["elevation_ft"])
    result = result.sort_values("elevation_ft").reset_index(drop=True)

    log.info(f"Parsed {len(result)} elevation points for {reservoir_name} "
             f"({result['elevation_ft'].min():.0f}-{result['elevation_ft'].max():.0f} ft)")

    return result


def list_surveys(tier: Optional[int] = None) -> pd.DataFrame:
    """List all known USBR surveys, optionally filtered by tier."""
    df = pd.DataFrame(USBR_SURVEY_CATALOG)
    if tier is not None:
        df = df[df["tier"] == tier]
    return df


def filter_surveys(
    surveys: list[dict],
    *,
    tier: Optional[int] = None,
    states: Optional[list[str]] = None,
) -> list[dict]:
    filtered = surveys
    if tier is not None:
        filtered = [s for s in filtered if s["tier"] == tier]
    if states:
        wanted = {state.strip().upper() for state in states if state.strip()}
        filtered = [s for s in filtered if str(s.get("state", "")).upper() in wanted]
    return filtered


@lru_cache(maxsize=1)
def load_usbr_report_index() -> list[dict[str, str]]:
    """Scrape the live USBR reservoir survey page for current report links."""
    import requests

    resp = requests.get(USBR_RESERVOIR_INDEX_URL, timeout=60)
    resp.raise_for_status()
    text = resp.text

    entries: list[dict[str, str]] = []
    for block in re.findall(r"<li\b[^>]*>(.*?)</li>", text, flags=re.IGNORECASE | re.DOTALL):
        pdf_links = re.findall(r'href="([^"]+?\.pdf)"', block, flags=re.IGNORECASE)
        if not pdf_links:
            continue
        href = html.unescape(pdf_links[0]).strip()
        label = re.sub(r"<[^>]+>", " ", block)
        label = " ".join(html.unescape(label).split())
        if href.startswith("/"):
            href = "https://www.usbr.gov" + href
        elif href.startswith("../"):
            href = "https://www.usbr.gov/tsc/techreferences/" + href[3:]
        elif href.startswith("./"):
            href = USBR_RESERVOIR_INDEX_URL.rsplit("/", 1)[0] + "/" + href[2:]
        elif not href.startswith("http"):
            href = USBR_RESERVOIR_INDEX_URL.rsplit("/", 1)[0] + "/" + href
        entries.append({"label": label, "url": href})
    return entries


def resolve_live_report_url(survey: dict) -> Optional[str]:
    """Resolve a current live report URL from the USBR reservoir index page."""
    entries = load_usbr_report_index()
    name = str(survey.get("reservoir_name", "")).lower()
    year = survey.get("latest_survey_year")

    name_tokens = [
        token for token in re.split(r"[^a-z0-9]+", name)
        if token and token not in {"lake", "reservoir", "the", "mount", "mt"}
    ]
    candidates: list[tuple[int, int, str]] = []
    for entry in entries:
        label_l = entry["label"].lower()
        overlap = sum(1 for token in name_tokens if token in label_l)
        if overlap == 0:
            continue
        year_match = 0
        if year and str(year) in label_l:
            year_match = 2
        elif "survey" in label_l or "bathymetric" in label_l or "sedimentation" in label_l:
            year_match = 1
        years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", label_l)]
        best_year = max(years) if years else 0
        candidates.append((overlap, year_match, best_year, entry["url"]))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][3]


def download_survey_pdf(survey: dict, output_dir: Path) -> Optional[Path]:
    """Download a USBR survey PDF if URL is available."""
    import requests

    url = survey.get("report_url")
    if not url:
        log.warning(f"No URL for {survey['reservoir_name']}")
        return None

    name_safe = survey["reservoir_name"].replace(" ", "_").lower()
    pdf_path = output_dir / f"{name_safe}_{survey.get('latest_survey_year', 'unknown')}.pdf"

    if pdf_path.exists():
        log.info(f"PDF already exists: {pdf_path}")
        return pdf_path

    log.info(f"Downloading {survey['reservoir_name']} survey PDF...")
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(pdf_path, "wb") as f:
            f.write(resp.content)
        log.info(f"Saved {pdf_path.stat().st_size / 1e6:.1f} MB to {pdf_path}")
        return pdf_path
    except Exception as e:
        log.warning(f"Primary URL failed for {survey['reservoir_name']}: {e}")
        fallback_url = resolve_live_report_url(survey)
        if not fallback_url or fallback_url == url:
            log.error(f"Failed to download: {e}")
            return None
        log.info(f"Retrying {survey['reservoir_name']} with live USBR index URL: {fallback_url}")
        try:
            resp = requests.get(fallback_url, timeout=60)
            resp.raise_for_status()
            with open(pdf_path, "wb") as f:
                f.write(resp.content)
            log.info(f"Saved {pdf_path.stat().st_size / 1e6:.1f} MB to {pdf_path}")
            return pdf_path
        except Exception as fallback_exc:
            log.error(f"Failed to download from fallback URL: {fallback_exc}")
            return None


def write_catalog(df: pd.DataFrame, output_dir: Path, stem: str) -> Path:
    parquet_path = output_dir / f"{stem}.parquet"
    try:
        df.to_parquet(parquet_path, index=False, engine="pyarrow")
        log.info(f"Saved catalog ({len(df)} surveys) to {parquet_path}")
        return parquet_path
    except Exception as exc:
        csv_path = output_dir / f"{stem}.csv"
        json_path = output_dir / f"{stem}.json"
        df.to_csv(csv_path, index=False)
        json_path.write_text(json.dumps(df.to_dict(orient="records"), indent=2))
        log.warning(
            "Parquet write unavailable (%s). Wrote fallback catalog files: %s and %s",
            exc,
            csv_path,
            json_path,
        )
        return csv_path


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and catalog USBR reservoir survey data"
    )
    parser.add_argument("--list", action="store_true", help="List known surveys")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], default=None,
                       help="Filter surveys by tier")
    parser.add_argument("--states", type=str, default=None,
                       help="Comma-separated state filter, e.g. AZ,CA,CO")
    parser.add_argument("--download-pdfs", action="store_true",
                       help="Download survey PDFs from USBR")
    parser.add_argument("--parse-csv", type=str, default=None,
                       help="Parse a manually-extracted A-E CSV file")
    parser.add_argument("--reservoir-name", type=str, default=None,
                       help="Reservoir name for --parse-csv")
    parser.add_argument("--survey-year", type=int, default=None,
                       help="Survey year for --parse-csv")
    parser.add_argument("--output", type=str, default="/data/reservoir/surveys",
                       help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_filter = (
        [token.strip().upper() for token in args.states.split(",") if token.strip()]
        if args.states
        else None
    )

    if args.list:
        df = pd.DataFrame(filter_surveys(USBR_SURVEY_CATALOG, tier=args.tier, states=state_filter))
        print(f"\n{'='*80}")
        print(f"USBR Survey Catalog: {len(df)} reservoirs")
        print(f"{'='*80}\n")
        for _, row in df.iterrows():
            print(f"  [{row['tier']}] {row['reservoir_name']} ({row['state']})")
            print(f"      Dam: {row['dam_name']} | NID: {row['nid_id']}")
            print(f"      Depth: {row['max_depth_m']}m | Area: {row['surface_area_km2']} km2")
            print(f"      Surveys: {row['survey_years']} | Latest: {row['latest_survey_year']}")
            if row.get("report_url"):
                print(f"      URL: {row['report_url']}")
            print(f"      Notes: {row['notes']}")
            print()
        return

    if args.download_pdfs:
        surveys = filter_surveys(USBR_SURVEY_CATALOG, tier=args.tier, states=state_filter)

        pdf_dir = output_dir / "pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)

        for survey in surveys:
            download_survey_pdf(survey, pdf_dir)
        return

    if args.parse_csv:
        if not args.reservoir_name:
            log.error("Must provide --reservoir-name with --parse-csv")
            return

        df = parse_ae_csv(
            Path(args.parse_csv),
            reservoir_name=args.reservoir_name,
            survey_year=args.survey_year,
        )

        out_name = args.reservoir_name.replace(" ", "_").lower()
        write_catalog(df, output_dir, f"{out_name}_ae_table")
        return

    # Default: save catalog as Parquet
    df = pd.DataFrame(filter_surveys(USBR_SURVEY_CATALOG, tier=args.tier, states=state_filter))
    write_catalog(df, output_dir, "usbr_survey_catalog")


if __name__ == "__main__":
    main()
