"""
Minnesota DNR LakeFinder fish survey collector.

Uses the MN DNR LakeFinder CGI API to download fish catch summaries (CPUE)
from electrofishing and netting surveys across Minnesota lakes.

API endpoint: https://maps.dnr.state.mn.us/cgi-bin/lakefinder/detail.cgi
  ?type=lake_survey&id={DOW_ID}

Returns JSON with surveys containing:
  - fishCatchSummaries: species, gear, CPUE, totalCatch, averageWeight
  - lengths: length frequency data
  - narrative: text description
  - headerInfo: lake name, county, survey date/type
"""

import json
import time
import logging
import pathlib
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

RAW_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw" / "state_dnr"
OUT_DIR = RAW_DIR / "mn_dnr"

# MN DNR LakeFinder endpoints
LAKEFINDER_META_URL = "https://services.dnr.state.mn.us/api/lakefinder/by_id/v1"
LAKEFINDER_SURVEY_URL = "https://maps.dnr.state.mn.us/cgi-bin/lakefinder/detail.cgi"

# Bass-related species codes used in MN DNR surveys
BASS_SPECIES = {
    "LMB": "Largemouth Bass",
    "SMB": "Smallmouth Bass",
    "RKB": "Rock Bass",
    "WHB": "White Bass",
    "STB": "Striped Bass",
}


def get_lake_metadata(dow_id: str) -> Optional[dict]:
    """Fetch lake metadata (name, county, species, morphology) from LakeFinder API."""
    resp = requests.get(LAKEFINDER_META_URL, params={"id": dow_id}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "OK" and data.get("results"):
        return data["results"][0]
    return None


def get_lake_surveys(dow_id: str) -> Optional[dict]:
    """Fetch all fish survey data for a lake from the LakeFinder CGI endpoint."""
    resp = requests.get(
        LAKEFINDER_SURVEY_URL,
        params={"type": "lake_survey", "id": dow_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def parse_surveys(dow_id: str, survey_data: dict) -> list[dict]:
    """Parse survey JSON into flat rows for DataFrame construction."""
    rows = []
    result = survey_data.get("result", {})
    surveys = result.get("surveys", [])

    for survey in surveys:
        header = survey.get("headerInfo", {})
        base = {
            "dow_id": dow_id,
            "lake_name": header.get("wb_name", ""),
            "county": header.get("county", ""),
            "survey_date": header.get("id_date", ""),
            "survey_type": header.get("survey_type", ""),
            "survey_id": header.get("survey_id", ""),
        }

        catch_summaries = survey.get("fishCatchSummaries", [])
        if not catch_summaries:
            # Record survey even without catch data
            rows.append({**base, "species": "", "gear": "", "cpue": None})
            continue

        for catch in catch_summaries:
            rows.append({
                **base,
                "species": catch.get("species", ""),
                "gear": catch.get("gear", ""),
                "cpue": _safe_float(catch.get("CPUE")),
                "total_catch": _safe_int(catch.get("totalCatch")),
                "gear_count": _safe_float(catch.get("gearCount")),
                "total_weight_g": _safe_int(catch.get("totalWeight")),
                "avg_weight_lb": _safe_float(catch.get("averageWeight")),
                "quartile_count": catch.get("quartileCount", ""),
                "quartile_weight": catch.get("quartileWeight", ""),
            })

    return rows


def _safe_float(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def collect_lakes(dow_ids: list[str], delay: float = 0.5) -> pd.DataFrame:
    """
    Collect fish survey data for a list of MN DNR DOW lake IDs.

    Parameters
    ----------
    dow_ids : list of str
        8-digit DOW numbers (e.g., "04013500")
    delay : float
        Seconds to wait between API calls to be respectful

    Returns
    -------
    pd.DataFrame with columns:
        dow_id, lake_name, county, survey_date, survey_type, survey_id,
        species, gear, cpue, total_catch, gear_count, total_weight_g,
        avg_weight_lb, quartile_count, quartile_weight
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = []
    errors = []

    for i, dow_id in enumerate(dow_ids):
        try:
            logger.info(f"[{i+1}/{len(dow_ids)}] Fetching surveys for DOW {dow_id}")
            data = get_lake_surveys(dow_id)
            if data and data.get("result"):
                rows = parse_surveys(dow_id, data)
                all_rows.extend(rows)
                logger.info(f"  -> {len(rows)} catch records")
            else:
                logger.warning(f"  -> No survey data for {dow_id}")
        except Exception as e:
            logger.error(f"  -> Error for {dow_id}: {e}")
            errors.append({"dow_id": dow_id, "error": str(e)})

        if i < len(dow_ids) - 1:
            time.sleep(delay)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        out_path = OUT_DIR / "mn_dnr_fish_surveys.csv"
        df.to_csv(out_path, index=False)
        logger.info(f"Saved {len(df)} records to {out_path}")

    if errors:
        err_path = OUT_DIR / "mn_dnr_errors.csv"
        pd.DataFrame(errors).to_csv(err_path, index=False)

    return df


# Some well-known bass lakes in Minnesota (DOW IDs)
# These overlap with tournament locations or are regionally significant
SAMPLE_BASS_LAKES = [
    "27010600",  # Mille Lacs
    "21005700",  # Minnetonka
    "86009000",  # Vermilion
    "11020300",  # Gull Lake
    "18003900",  # Leech Lake
    "04013500",  # Beltrami
    "69006900",  # Lake of the Woods
    "56006600",  # Lake Pepin
    "34003600",  # Lake Koronis
    "73004600",  # Pelican Lake (Otter Tail)
    "56003400",  # Big Marine Lake
    "69069400",  # Rainy Lake
    "15000100",  # Cass Lake
    "29002300",  # Green Lake
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Minnesota DNR Fish Survey Collector")
    print(f"Collecting data for {len(SAMPLE_BASS_LAKES)} lakes...")
    df = collect_lakes(SAMPLE_BASS_LAKES)
    if not df.empty:
        print(f"\nCollected {len(df)} total catch records")
        print(f"Unique lakes: {df['lake_name'].nunique()}")
        print(f"Date range: {df['survey_date'].min()} to {df['survey_date'].max()}")
        # Show bass-specific records
        bass_mask = df["species"].isin(BASS_SPECIES.keys())
        print(f"Bass records: {bass_mask.sum()}")
        print("\nSpecies breakdown:")
        print(df["species"].value_counts().head(20))
    else:
        print("No data collected.")
