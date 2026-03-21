from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# ScienceBase item for CreelCat (DOI: 10.5066/P1WOQBRN)
# Original item (10.5066/P9DSOPHD) deprecated April 2024; data moved here.
# ---------------------------------------------------------------------------
SCIENCEBASE_ITEM_ID = "66183787d34e7eb9eb7d7b1c"
SCIENCEBASE_ITEM_URL = (
    f"https://www.sciencebase.gov/catalog/item/{SCIENCEBASE_ITEM_ID}?format=json"
)
SCIENCEBASE_FILE_BASE = (
    f"https://www.sciencebase.gov/catalog/file/get/{SCIENCEBASE_ITEM_ID}"
)

# The three tables we care about.
CREEL_TABLES: dict[str, str] = {
    "fish": "FishDataCompiled.csv",
    "survey": "Survey_Data.csv",
    "effort": "AngEffort_Data.csv",
}

# Bass species we want to keep (case-insensitive matching).
BASS_COMMON_NAMES = {
    "largemouth bass",
    "smallmouth bass",
    "spotted bass",
}

# Taxonomic scientific names as a fallback.
BASS_SCIENTIFIC_NAMES = {
    "micropterus salmoides",   # largemouth bass
    "micropterus dolomieu",    # smallmouth bass
    "micropterus punctulatus", # spotted bass
}

# ---------------------------------------------------------------------------
# Column name resolution helpers
# ---------------------------------------------------------------------------

def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first column name in *df* that matches any candidate (case-insensitive)."""
    lower_map = {c.lower(): c for c in df.columns}
    for name in candidates:
        actual = lower_map.get(name.lower())
        if actual is not None:
            return actual
    return None


def _require_column(df: pd.DataFrame, candidates: list[str], label: str) -> str:
    col = _find_column(df, candidates)
    if col is None:
        raise KeyError(
            f"Could not find {label} column; tried {candidates}. "
            f"Available columns: {list(df.columns)}"
        )
    return col


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _discover_download_urls(
    session: requests.Session,
    timeout: int = 30,
) -> dict[str, str]:
    """Query ScienceBase item JSON and return {filename: download_url}."""
    resp = session.get(SCIENCEBASE_ITEM_URL, timeout=timeout)
    resp.raise_for_status()
    meta = resp.json()

    urls: dict[str, str] = {}
    for file_info in meta.get("files", []):
        name = file_info.get("name", "")
        # Prefer publishedS3Uri (direct S3) over downloadUri (returns HTML for S3-backed files)
        url = (
            file_info.get("publishedS3Uri")
            or file_info.get("downloadUri")
            or file_info.get("url", "")
        )
        if name and url:
            urls[name] = url
    return urls


def _fallback_url(filename: str) -> str:
    """Construct a plausible download URL when the API doesn't cooperate."""
    return f"{SCIENCEBASE_FILE_BASE}?f__0={filename}"


def download_creel_data(
    output_dir: Path,
    session: requests.Session | None = None,
    timeout: int = 120,
) -> dict[str, Path]:
    """Download CreelCat CSV tables from USGS ScienceBase.

    Returns a dict mapping table key (``fish``, ``survey``, ``effort``) to the
    local file path where each CSV was saved.
    """
    sess = session or requests.Session()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Try to discover real download URLs from the ScienceBase API.
    discovered: dict[str, str] = {}
    try:
        discovered = _discover_download_urls(sess, timeout=min(timeout, 30))
        print(
            f"creel: discovered {len(discovered)} files from ScienceBase metadata",
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            f"creel: warning: could not fetch ScienceBase metadata ({exc}); "
            "falling back to constructed URLs",
            file=sys.stderr,
        )

    result: dict[str, Path] = {}
    for table_key, filename in CREEL_TABLES.items():
        url = discovered.get(filename) or _fallback_url(filename)
        dest = output_dir / filename
        print(f"creel: downloading {filename} ...", file=sys.stderr)
        try:
            resp = sess.get(url, timeout=timeout, stream=True)
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
            result[table_key] = dest
            print(f"creel: saved {dest} ({dest.stat().st_size:,} bytes)", file=sys.stderr)
        except Exception as exc:
            print(
                f"creel: error downloading {filename}: {exc}",
                file=sys.stderr,
            )

    return result


# ---------------------------------------------------------------------------
# CPUE dataset builder
# ---------------------------------------------------------------------------

def build_creel_cpue_dataset(
    fish_data_path: Path,
    survey_data_path: Path,
    effort_data_path: Path,
    output_path: Path,
) -> pd.DataFrame:
    """Build a normalised bass CPUE dataset from CreelCat CSV tables.

    Parameters
    ----------
    fish_data_path
        Path to ``FishDataCompiled.csv``.
    survey_data_path
        Path to ``Survey_Data.csv``.
    effort_data_path
        Path to ``AngEffort_Data.csv``.
    output_path
        Where to write the resulting CSV.

    Returns
    -------
    pd.DataFrame
        Normalised CPUE rows with columns: ``survey_id``, ``waterbody_name``,
        ``state``, ``latitude``, ``longitude``, ``date``, ``species``,
        ``cpue_fish_per_hour``, ``total_catch``, ``total_effort_hours``,
        ``n_anglers``.
    """

    # ------------------------------------------------------------------
    # 1. Load raw tables
    # ------------------------------------------------------------------
    print("creel: loading FishDataCompiled ...", file=sys.stderr)
    fish = pd.read_csv(fish_data_path, low_memory=False)
    print(f"creel: fish rows={len(fish):,}  cols={len(fish.columns)}", file=sys.stderr)

    print("creel: loading Survey_Data ...", file=sys.stderr)
    survey = pd.read_csv(survey_data_path, low_memory=False)
    print(f"creel: survey rows={len(survey):,}  cols={len(survey.columns)}", file=sys.stderr)

    print("creel: loading AngEffort_Data ...", file=sys.stderr)
    effort = pd.read_csv(effort_data_path, low_memory=False)
    print(f"creel: effort rows={len(effort):,}  cols={len(effort.columns)}", file=sys.stderr)

    # ------------------------------------------------------------------
    # 2. Resolve column names flexibly
    # ------------------------------------------------------------------

    # --- fish table ---
    fish_survey_col = _require_column(
        fish,
        ["SurveyID", "Survey_ID", "surveyid", "survey_id", "SURVEYID"],
        "survey id (fish)",
    )
    # CreelCat uses a combined "Taxa" column with format "Scientific Name (Common Name)"
    fish_taxa_col = _find_column(
        fish,
        ["Taxa", "taxa", "TAXA"],
    )
    fish_species_col = _find_column(
        fish,
        ["CommonName", "Common_Name", "common_name", "Species", "species",
         "SpeciesName", "species_name"],
    )
    fish_sciname_col = _find_column(
        fish,
        ["ScientificName", "Scientific_Name", "scientific_name", "SciName",
         "sci_name", "Genus_Species"],
    )
    fish_catch_col = _find_column(
        fish,
        ["Catch", "catch", "TotalCatch", "Total_Catch", "total_catch",
         "NumCaught", "num_caught", "Number", "number", "N", "Count",
         "Harvest", "harvest", "TotalHarvest", "Total_Harvest"],
    )
    # CreelCat pre-computes CPUE — use it directly if available
    fish_cpue_col = _find_column(
        fish,
        ["Catch_Per_Hour", "catch_per_hour", "CatchPerHour", "CPUE", "cpue"],
    )

    if fish_taxa_col is None and fish_species_col is None and fish_sciname_col is None:
        raise KeyError(
            "Cannot find any species identifier column in fish data. "
            f"Available: {list(fish.columns)}"
        )

    # --- survey table ---
    survey_id_col = _require_column(
        survey,
        ["SurveyID", "Survey_ID", "surveyid", "survey_id", "SURVEYID"],
        "survey id (survey)",
    )
    waterbody_col = _find_column(
        survey,
        ["WaterbodyName", "Waterbody_Name", "waterbody_name", "Waterbody",
         "waterbody", "LakeName", "Lake_Name", "lake_name", "Lake", "lake",
         "Water_Body", "WaterBody"],
    )
    state_col = _find_column(
        survey,
        ["State", "state", "ST", "st", "StateCode", "state_code"],
    )
    lat_col = _find_column(
        survey,
        ["Latitude", "latitude", "Lat", "lat", "LAT", "DecLat", "dec_lat",
         "Latitude_DD"],
    )
    lon_col = _find_column(
        survey,
        ["Longitude", "longitude", "Long", "long", "Lon", "lon", "LON",
         "DecLon", "dec_lon", "Longitude_DD"],
    )
    date_col = _find_column(
        survey,
        ["StartDate", "Start_Date", "start_date", "Date", "date",
         "SurveyDate", "Survey_Date", "survey_date", "Year", "year",
         "BeginDate", "Begin_Date", "StartYear", "Start_Year"],
    )

    # --- effort table ---
    effort_survey_col = _require_column(
        effort,
        ["SurveyID", "Survey_ID", "surveyid", "survey_id", "SURVEYID"],
        "survey id (effort)",
    )
    hours_col = _find_column(
        effort,
        ["TotalHours", "Total_Hours", "total_hours", "AnglerHours",
         "Angler_Hours", "angler_hours", "EffortHours", "Effort_Hours",
         "effort_hours", "Hours", "hours", "TotalEffort", "Total_Effort",
         "total_effort"],
    )
    anglers_col = _find_column(
        effort,
        ["NumAnglers", "Num_Anglers", "num_anglers", "Anglers", "anglers",
         "TotalAnglers", "Total_Anglers", "total_anglers", "NAnglers",
         "n_anglers", "AnglerCount", "Angler_Count"],
    )

    # ------------------------------------------------------------------
    # 3. Filter for bass species
    # ------------------------------------------------------------------
    # Black bass keywords for matching against the combined Taxa column
    _MICROPTERUS_TAXA_PATTERNS = [
        "micropterus salmoides",
        "micropterus dolomieu",
        "micropterus punctulatus",
        "micropterus coosae",
        "micropterus henshalli",
        "micropterus notius",
        "micropterus treculii",
        "micropterus (black basses)",
        "largemouth bass",
    ]

    bass_mask = pd.Series(False, index=fish.index)

    if fish_taxa_col is not None:
        taxa_lower = fish[fish_taxa_col].astype(str).str.strip().str.lower()
        for pattern in _MICROPTERUS_TAXA_PATTERNS:
            bass_mask |= taxa_lower.str.contains(pattern, na=False)
    if fish_species_col is not None:
        common_lower = fish[fish_species_col].astype(str).str.strip().str.lower()
        bass_mask |= common_lower.isin(BASS_COMMON_NAMES)
    if fish_sciname_col is not None:
        sci_lower = fish[fish_sciname_col].astype(str).str.strip().str.lower()
        bass_mask |= sci_lower.isin(BASS_SCIENTIFIC_NAMES)

    fish_bass = fish.loc[bass_mask].copy()
    print(f"creel: bass rows after species filter: {len(fish_bass):,}", file=sys.stderr)
    if fish_bass.empty:
        warnings.warn("No bass records found after species filtering.")
        empty = pd.DataFrame(
            columns=[
                "survey_id", "waterbody_name", "state", "latitude", "longitude",
                "date", "species", "cpue_fish_per_hour", "total_catch",
                "total_effort_hours", "n_anglers",
            ]
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        empty.to_csv(output_path, index=False)
        return empty

    # Normalise species label from the Taxa column.
    _TAXA_SPECIES_MAP = {
        "micropterus salmoides": "largemouth_bass",
        "micropterus dolomieu": "smallmouth_bass",
        "micropterus punctulatus": "spotted_bass",
        "micropterus coosae": "coosa_bass",
        "micropterus henshalli": "alabama_bass",
        "micropterus notius": "suwannee_bass",
        "micropterus treculii": "guadalupe_bass",
        "largemouth bass": "largemouth_bass",
    }

    def _resolve_species(taxa_str: str) -> str:
        taxa_lower = taxa_str.strip().lower()
        for key, label in _TAXA_SPECIES_MAP.items():
            if key in taxa_lower:
                return label
        if "micropterus" in taxa_lower:
            return "black_bass"
        return "unknown_bass"

    species_source = fish_taxa_col or fish_species_col or fish_sciname_col
    fish_bass["_species"] = fish_bass[species_source].astype(str).apply(_resolve_species)

    # ------------------------------------------------------------------
    # 4. Join with survey metadata
    # ------------------------------------------------------------------
    merged = fish_bass.merge(
        survey,
        left_on=fish_survey_col,
        right_on=survey_id_col,
        how="left",
        suffixes=("", "_survey"),
    )

    # ------------------------------------------------------------------
    # 5. Join with effort data
    # ------------------------------------------------------------------
    merged = merged.merge(
        effort,
        left_on=fish_survey_col,
        right_on=effort_survey_col,
        how="left",
        suffixes=("", "_effort"),
    )

    # ------------------------------------------------------------------
    # 6. Build normalised output
    # ------------------------------------------------------------------
    rows: list[dict[str, object]] = []
    skipped = 0

    for idx, row in merged.iterrows():
        try:
            survey_id = str(row[fish_survey_col])

            wb = str(row.get(waterbody_col, "")) if waterbody_col else ""
            st = str(row.get(state_col, "")) if state_col else ""
            lat_val = _safe_float(row.get(lat_col)) if lat_col else None
            lon_val = _safe_float(row.get(lon_col)) if lon_col else None
            dt = _safe_date(row.get(date_col)) if date_col else ""

            total_catch = _safe_float(row.get(fish_catch_col)) if fish_catch_col else None
            total_hours = _safe_float(row.get(hours_col)) if hours_col else None
            n_ang = _safe_float(row.get(anglers_col)) if anglers_col else None

            # Prefer pre-computed CPUE from CreelCat, fall back to computing it
            cpue = _safe_float(row.get(fish_cpue_col)) if fish_cpue_col else None
            if cpue is None and total_catch is not None and total_hours is not None and total_hours > 0:
                cpue = total_catch / total_hours

            rows.append(
                {
                    "survey_id": survey_id,
                    "waterbody_name": wb,
                    "state": st,
                    "latitude": lat_val,
                    "longitude": lon_val,
                    "date": dt,
                    "species": row["_species"],
                    "cpue_fish_per_hour": cpue,
                    "total_catch": total_catch,
                    "total_effort_hours": total_hours,
                    "n_anglers": n_ang,
                }
            )
        except Exception as exc:
            skipped += 1
            if skipped <= 10:
                print(
                    f"creel: warning: skipping row {idx}: {exc}",
                    file=sys.stderr,
                )
            continue

    if skipped > 10:
        print(
            f"creel: warning: {skipped} total rows skipped due to errors",
            file=sys.stderr,
        )

    result = pd.DataFrame(rows)
    print(f"creel: output rows: {len(result):,}", file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    print(f"creel: saved {output_path}", file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# Small conversion helpers
# ---------------------------------------------------------------------------

def _safe_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _safe_date(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value).strip()
