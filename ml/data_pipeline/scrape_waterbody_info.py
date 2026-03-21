#!/usr/bin/env python3
"""
OpenCatch — Water Body Enrichment Pipeline

For each water body in the master catalog, scrapes enrichment data from
multiple public sources:

1. USGS NWIS — stream gauge stations, flow/level data availability
2. State DNR / Fish & Wildlife — stocking records (via public APIs)
3. iNaturalist API — species observations near water bodies
4. GBIF — species occurrence records (backup for iNaturalist)
5. EPA WATERS — water quality, impairment status

Outputs per-water-body enrichment as Parquet with one row per water body,
containing nested JSON columns for each data source.

Size estimates:
    - USGS NWIS site lookup: ~50K gauge stations, ~2 GB response total
    - iNaturalist observations: rate-limited, ~10K requests/day
    - Output enrichment parquet: ~2 GB for all US+CA water bodies
    - Peak RAM: ~1 GB

Data is NOT plagiarized — we store structured facts only (species names,
stocking counts, gauge IDs, water quality parameters). No prose is copied.

Usage:
    # Enrich all water bodies from master catalog
    python scrape_waterbody_info.py \
        --catalog /data/merged/master_waterbody_catalog.parquet \
        --output /data/enrichment \
        --sources nwis,inat,epa

    # Enrich a single water body by ID
    python scrape_waterbody_info.py \
        --waterbody-id "12345678" \
        --output /data/enrichment

    # Resume interrupted run
    python scrape_waterbody_info.py \
        --catalog /data/merged/master_waterbody_catalog.parquet \
        --output /data/enrichment --resume

    # Process in batches (e.g. for parallel workers)
    python scrape_waterbody_info.py \
        --catalog /data/merged/master_waterbody_catalog.parquet \
        --output /data/enrichment \
        --batch-start 0 --batch-size 10000

Requirements:
    pip install requests pandas pyarrow tqdm
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("scrape_waterbody_info.log")],
)
log = logging.getLogger("scrape_waterbody")

# ---------------------------------------------------------------------------
# Rate limiting configuration
# ---------------------------------------------------------------------------

RATE_LIMITS = {
    "nwis": 0.5,       # USGS is generous — 0.5s between requests
    "inat": 1.2,       # iNaturalist: max 60 req/min → 1 req/s + margin
    "gbif": 0.5,       # GBIF: generous
    "epa": 1.0,        # EPA WATERS: moderate
    "stocking": 2.0,   # State DNR sites: be very polite
}

MAX_RETRIES = 3
CHECKPOINT_FILE = "enrichment_checkpoint.json"

# ---------------------------------------------------------------------------
# USGS NWIS — Stream gauge stations and data availability
# ---------------------------------------------------------------------------

NWIS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
NWIS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"


def fetch_nwis_sites_near(
    lat: float,
    lon: float,
    radius_miles: float = 5.0,
) -> list[dict]:
    """
    Find USGS NWIS stream gauge sites near a coordinate.

    Returns list of site records with:
        site_no, station_name, site_type, dec_lat, dec_lon, state_cd,
        available_parameters
    """
    params = {
        "format": "rdb",
        "bBox": f"{lon - 0.1},{lat - 0.1},{lon + 0.1},{lat + 0.1}",
        "siteType": "LK,ST,SP",  # Lakes, Streams, Springs
        "siteStatus": "all",
        "hasDataTypeCd": "iv,dv",  # Has instantaneous or daily values
    }

    try:
        r = requests.get(NWIS_SITE_URL, params=params, timeout=30)
        r.raise_for_status()
        return _parse_nwis_rdb(r.text)
    except Exception as e:
        log.debug(f"NWIS query failed for ({lat}, {lon}): {e}")
        return []


def _parse_nwis_rdb(text: str) -> list[dict]:
    """Parse USGS RDB (tab-delimited) format into list of dicts."""
    lines = text.strip().split("\n")
    # Skip comment lines (start with #)
    data_lines = [l for l in lines if not l.startswith("#")]
    if len(data_lines) < 2:
        return []

    headers = data_lines[0].split("\t")
    # Skip the format line (second data line with types like 15s, 8s, etc.)
    records = []
    for line in data_lines[2:]:
        values = line.split("\t")
        if len(values) >= len(headers):
            record = dict(zip(headers, values))
            records.append({
                "site_no": record.get("site_no", "").strip(),
                "station_name": record.get("station_nm", "").strip(),
                "site_type": record.get("site_tp_cd", "").strip(),
                "lat": _safe_float(record.get("dec_lat_va", "")),
                "lon": _safe_float(record.get("dec_long_va", "")),
                "state_cd": record.get("state_cd", "").strip(),
                "huc_cd": record.get("huc_cd", "").strip(),
            })

    return records


def fetch_nwis_parameters(site_no: str) -> list[str]:
    """Get available parameter codes for a NWIS site."""
    params = {
        "format": "rdb",
        "sites": site_no,
        "siteStatus": "all",
        "outputDataTypeCd": "iv,dv",
    }

    try:
        r = requests.get(NWIS_SITE_URL, params=params, timeout=30)
        r.raise_for_status()
        # Extract parameter codes from response
        params_found = set()
        for line in r.text.split("\n"):
            if line.startswith("#") and "Parameter" in line:
                # Parse parameter codes from comments
                parts = line.split()
                for p in parts:
                    if p.isdigit() and len(p) == 5:
                        params_found.add(p)
        return list(params_found)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# iNaturalist — Species observations
# ---------------------------------------------------------------------------

INAT_API = "https://api.inaturalist.org/v1"


def fetch_inat_species(
    lat: float,
    lon: float,
    radius_km: float = 5.0,
    taxon_ids: Optional[list[int]] = None,
) -> list[dict]:
    """
    Query iNaturalist for species observations near a water body.

    Default taxon_ids:
        47178 = Actinopterygii (ray-finned fishes)
        20978 = Amphibia
        47584 = Reptilia (turtles, etc.)

    Returns list of species with observation counts.
    """
    if taxon_ids is None:
        taxon_ids = [47178, 20978]  # Fish + amphibians

    all_species = []

    for taxon_id in taxon_ids:
        params = {
            "lat": lat,
            "lng": lon,
            "radius": radius_km,
            "taxon_id": taxon_id,
            "quality_grade": "research",
            "per_page": 200,
            "order_by": "species_count",
            "verifiable": "true",
        }

        try:
            r = requests.get(f"{INAT_API}/observations/species_counts", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()

            for result in data.get("results", []):
                taxon = result.get("taxon", {})
                all_species.append({
                    "taxon_id": taxon.get("id"),
                    "name": taxon.get("name", ""),
                    "common_name": taxon.get("preferred_common_name", ""),
                    "rank": taxon.get("rank", ""),
                    "observation_count": result.get("count", 0),
                    "iconic_taxon": taxon.get("iconic_taxon_name", ""),
                })

        except Exception as e:
            log.debug(f"iNaturalist query failed for ({lat}, {lon}): {e}")

    return all_species


# ---------------------------------------------------------------------------
# GBIF — Backup species occurrence data
# ---------------------------------------------------------------------------

GBIF_API = "https://api.gbif.org/v1"


def fetch_gbif_species(
    lat: float,
    lon: float,
    radius_m: float = 5000,
) -> list[dict]:
    """
    Query GBIF for fish species occurrences near a water body.

    GBIF is a backup for iNaturalist — larger dataset, more global coverage.
    """
    params = {
        "decimalLatitude": f"{lat - 0.05},{lat + 0.05}",
        "decimalLongitude": f"{lon - 0.05},{lon + 0.05}",
        "taxonKey": 204,  # Actinopterygii
        "hasCoordinate": "true",
        "limit": 300,
        "facet": "speciesKey",
        "facetLimit": 100,
    }

    try:
        r = requests.get(f"{GBIF_API}/occurrence/search", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        # Extract unique species from facets
        species = []
        seen = set()

        for result in data.get("results", []):
            sp_key = result.get("speciesKey")
            if sp_key and sp_key not in seen:
                seen.add(sp_key)
                species.append({
                    "gbif_key": sp_key,
                    "name": result.get("species", ""),
                    "common_name": result.get("vernacularName", ""),
                    "family": result.get("family", ""),
                    "order": result.get("order", ""),
                })

        return species

    except Exception as e:
        log.debug(f"GBIF query failed for ({lat}, {lon}): {e}")
        return []


# ---------------------------------------------------------------------------
# EPA WATERS — Water quality and impairment
# ---------------------------------------------------------------------------

EPA_WATERS_URL = "https://watersgeo.epa.gov/arcgis/rest/services"


def fetch_epa_water_quality(
    lat: float,
    lon: float,
) -> dict:
    """
    Query EPA WATERS for water quality information including:
    - 303(d) impairment status
    - Assessment data
    """
    # Query the ATTAINS (Assessment TMDL Tracking & Implementation System)
    url = f"{EPA_WATERS_URL}/OWRAD_NP21/ATTAINS_Assessment/MapServer/3/query"
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "distance": 5000,  # meters
        "units": "esriSRUnit_Meter",
        "outFields": "assessmentunitname,organizationid,reportingcycle,ircategory,overallstatus",
        "f": "json",
        "returnGeometry": "false",
        "resultRecordCount": 5,
    }

    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        features = data.get("features", [])
        if not features:
            return {}

        # Take the nearest/most relevant assessment
        attrs = features[0].get("attributes", {})
        return {
            "assessment_unit": attrs.get("assessmentunitname", ""),
            "organization": attrs.get("organizationid", ""),
            "reporting_cycle": attrs.get("reportingcycle", ""),
            "ir_category": attrs.get("ircategory", ""),
            "overall_status": attrs.get("overallstatus", ""),
        }

    except Exception as e:
        log.debug(f"EPA query failed for ({lat}, {lon}): {e}")
        return {}


# ---------------------------------------------------------------------------
# State Fish Stocking Records
# ---------------------------------------------------------------------------

# Known state stocking data endpoints (public APIs / data portals)
STATE_STOCKING_URLS = {
    # Many states publish via Socrata open data portals
    "MN": "https://gisdata.mn.gov/api/3/action/datastore_search",
    "WI": "https://data.wi.gov/resource/jbh2-v5qi.json",
    "MI": "https://data.michigan.gov/resource/he9p-5bnm.json",
    "NY": "https://data.ny.gov/resource/e52k-ymww.json",
    "PA": "https://data.pa.gov/resource/bgai-4vpe.json",
    # Add more states as their APIs are discovered
}


def fetch_stocking_records(
    state: str,
    waterbody_name: str,
    lat: float,
    lon: float,
) -> list[dict]:
    """
    Query state fish stocking databases for records matching a water body.

    Returns list of stocking events with species, count, date.
    """
    state = state.upper()[:2]

    if state not in STATE_STOCKING_URLS:
        return []

    url = STATE_STOCKING_URLS[state]
    records = []

    try:
        # Socrata-style query
        params = {
            "$where": f"upper(lake) LIKE '%{waterbody_name.upper()}%'",
            "$limit": 100,
            "$order": "date DESC",
        }

        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        for row in data if isinstance(data, list) else data.get("result", {}).get("records", []):
            records.append({
                "species": row.get("species", row.get("Species", "")),
                "count": _safe_int(row.get("number", row.get("Number", row.get("count", 0)))),
                "date": row.get("date", row.get("Date", row.get("stock_date", ""))),
                "strain": row.get("strain", row.get("Strain", "")),
                "source": f"state_dnr_{state}",
            })

    except Exception as e:
        log.debug(f"Stocking query failed for {state}/{waterbody_name}: {e}")

    return records


# ---------------------------------------------------------------------------
# Enrichment orchestrator
# ---------------------------------------------------------------------------


def enrich_waterbody(
    wb_id: str,
    name: str,
    lat: float,
    lon: float,
    state: str = "",
    sources: list[str] = None,
) -> dict:
    """
    Run all enrichment sources for a single water body.

    Returns a dict with source-keyed enrichment data.
    """
    if sources is None:
        sources = ["nwis", "inat", "epa"]

    result = {
        "waterbody_id": wb_id,
        "name": name,
        "lat": lat,
        "lon": lon,
    }

    if "nwis" in sources:
        try:
            sites = fetch_nwis_sites_near(lat, lon)
            result["nwis_sites"] = sites
            result["nwis_site_count"] = len(sites)
            result["has_gauge"] = len(sites) > 0
            time.sleep(RATE_LIMITS["nwis"])
        except Exception as e:
            log.debug(f"NWIS enrichment failed for {wb_id}: {e}")
            result["nwis_sites"] = []
            result["nwis_site_count"] = 0
            result["has_gauge"] = False

    if "inat" in sources:
        try:
            species = fetch_inat_species(lat, lon)
            result["inat_species"] = species
            result["inat_fish_count"] = len([s for s in species if s.get("iconic_taxon") == "Actinopterygii"])
            result["inat_total_species"] = len(species)
            time.sleep(RATE_LIMITS["inat"])
        except Exception as e:
            log.debug(f"iNat enrichment failed for {wb_id}: {e}")
            result["inat_species"] = []
            result["inat_fish_count"] = 0
            result["inat_total_species"] = 0

    if "gbif" in sources:
        try:
            species = fetch_gbif_species(lat, lon)
            result["gbif_species"] = species
            result["gbif_species_count"] = len(species)
            time.sleep(RATE_LIMITS["gbif"])
        except Exception as e:
            log.debug(f"GBIF enrichment failed for {wb_id}: {e}")
            result["gbif_species"] = []
            result["gbif_species_count"] = 0

    if "epa" in sources:
        try:
            wq = fetch_epa_water_quality(lat, lon)
            result["epa_water_quality"] = wq
            result["epa_impaired"] = wq.get("overall_status", "") in ("Not Supporting", "Impaired")
            time.sleep(RATE_LIMITS["epa"])
        except Exception as e:
            log.debug(f"EPA enrichment failed for {wb_id}: {e}")
            result["epa_water_quality"] = {}
            result["epa_impaired"] = None

    if "stocking" in sources and state:
        try:
            records = fetch_stocking_records(state, name, lat, lon)
            result["stocking_records"] = records
            result["stocking_count"] = len(records)
            result["stocked_species"] = list(set(r["species"] for r in records if r.get("species")))
            time.sleep(RATE_LIMITS["stocking"])
        except Exception as e:
            log.debug(f"Stocking enrichment failed for {wb_id}: {e}")
            result["stocking_records"] = []
            result["stocking_count"] = 0

    return result


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


def load_checkpoint(output_dir: Path) -> dict:
    cp = output_dir / CHECKPOINT_FILE
    if cp.exists():
        with open(cp) as f:
            return json.load(f)
    return {"completed_ids": [], "batch_index": 0}


def save_checkpoint(output_dir: Path, checkpoint: dict):
    with open(output_dir / CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f)


def run_batch(
    catalog_path: Path,
    output_dir: Path,
    sources: list[str],
    batch_start: int = 0,
    batch_size: int = 0,
    resume: bool = True,
    save_interval: int = 100,
):
    """
    Enrich water bodies from the master catalog in batches.

    Saves enrichment data as Parquet, with checkpoint/resume support.
    JSON columns (species lists, stocking records) are serialized as strings.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Loading catalog from {catalog_path}...")
    catalog = pd.read_parquet(catalog_path)
    log.info(f"Catalog: {len(catalog)} water bodies")

    # Apply batch window
    if batch_size > 0:
        catalog = catalog.iloc[batch_start : batch_start + batch_size]
        log.info(f"Processing batch [{batch_start}:{batch_start + batch_size}]")

    # Load checkpoint
    checkpoint = load_checkpoint(output_dir) if resume else {"completed_ids": [], "batch_index": 0}
    completed = set(checkpoint.get("completed_ids", []))

    # Identify required columns
    id_col = "permanent_id" if "permanent_id" in catalog.columns else catalog.columns[0]
    name_col = "name" if "name" in catalog.columns else None
    lat_col = "centroid_lat" if "centroid_lat" in catalog.columns else "lake_lat"
    lon_col = "centroid_lon" if "centroid_lon" in catalog.columns else "lake_lon"
    state_col = None
    for cand in ["state_fips", "state", "province"]:
        if cand in catalog.columns:
            state_col = cand
            break

    # Process
    results = []
    batch_file_idx = checkpoint.get("batch_index", 0)

    for idx, row in tqdm(catalog.iterrows(), total=len(catalog), desc="Enriching"):
        wb_id = str(row[id_col])

        if wb_id in completed:
            continue

        lat = row.get(lat_col)
        lon = row.get(lon_col)
        name = str(row.get(name_col, "")) if name_col else ""
        state = str(row.get(state_col, "")) if state_col else ""

        if pd.isna(lat) or pd.isna(lon):
            continue

        enrichment = enrich_waterbody(wb_id, name, lat, lon, state, sources)

        # Serialize nested structures for Parquet compatibility
        for key in ["nwis_sites", "inat_species", "gbif_species", "stocking_records",
                     "epa_water_quality", "stocked_species"]:
            if key in enrichment and isinstance(enrichment[key], (list, dict)):
                enrichment[key] = json.dumps(enrichment[key])

        results.append(enrichment)
        completed.add(wb_id)

        # Periodic save
        if len(results) >= save_interval:
            _save_batch(results, output_dir, batch_file_idx)
            batch_file_idx += 1
            checkpoint["completed_ids"] = list(completed)
            checkpoint["batch_index"] = batch_file_idx
            save_checkpoint(output_dir, checkpoint)
            results = []

    # Final save
    if results:
        _save_batch(results, output_dir, batch_file_idx)
        checkpoint["completed_ids"] = list(completed)
        checkpoint["batch_index"] = batch_file_idx + 1
        save_checkpoint(output_dir, checkpoint)

    log.info(f"Enrichment complete: {len(completed)} water bodies processed")


def _save_batch(records: list[dict], output_dir: Path, batch_idx: int):
    """Save a batch of enrichment records as Parquet."""
    df = pd.DataFrame(records)
    out = output_dir / f"enrichment_batch_{batch_idx:05d}.parquet"
    df.to_parquet(out, index=False)
    log.info(f"  Saved batch {batch_idx}: {len(records)} records → {out.name}")


def merge_enrichment(output_dir: Path):
    """Merge all enrichment batch files into a single Parquet."""
    files = sorted(output_dir.glob("enrichment_batch_*.parquet"))
    if not files:
        log.warning("No enrichment batch files found")
        return

    log.info(f"Merging {len(files)} enrichment batches...")
    dfs = [pd.read_parquet(f) for f in files]
    merged = pd.concat(dfs, ignore_index=True)

    # Deduplicate
    if "waterbody_id" in merged.columns:
        before = len(merged)
        merged = merged.drop_duplicates(subset="waterbody_id", keep="last")
        if len(merged) < before:
            log.info(f"  Deduped: {before} → {len(merged)}")

    out = output_dir / "enrichment_all.parquet"
    merged.to_parquet(out, index=False)
    log.info(f"Merged enrichment: {len(merged)} water bodies → {out}")

    # Summary stats
    if "has_gauge" in merged.columns:
        log.info(f"  With NWIS gauge: {merged['has_gauge'].sum()}")
    if "inat_total_species" in merged.columns:
        log.info(f"  With iNat species: {(merged['inat_total_species'] > 0).sum()}")
    if "epa_impaired" in merged.columns:
        log.info(f"  EPA impaired: {merged['epa_impaired'].sum()}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch — Water body enrichment pipeline"
    )
    parser.add_argument(
        "--catalog", type=str, default=None,
        help="Path to master waterbody catalog parquet",
    )
    parser.add_argument(
        "--waterbody-id", type=str, default=None,
        help="Enrich a single water body by ID (requires --catalog)",
    )
    parser.add_argument(
        "--output", type=str, default="/data/enrichment",
        help="Output directory",
    )
    parser.add_argument(
        "--sources", type=str, default="nwis,inat,epa",
        help="Comma-separated sources: nwis, inat, gbif, epa, stocking",
    )
    parser.add_argument(
        "--batch-start", type=int, default=0,
        help="Start index for batch processing",
    )
    parser.add_argument(
        "--batch-size", type=int, default=0,
        help="Batch size (0 = process all)",
    )
    parser.add_argument(
        "--save-interval", type=int, default=100,
        help="Save enrichment every N water bodies",
    )
    parser.add_argument(
        "--resume", action="store_true", default=True,
    )
    parser.add_argument(
        "--no-resume", action="store_true",
    )
    parser.add_argument(
        "--merge", action="store_true",
        help="Merge all batch files into one",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    sources = [s.strip() for s in args.sources.split(",")]
    resume = not args.no_resume

    if args.merge:
        merge_enrichment(output_dir)
        return

    if not args.catalog:
        log.error("--catalog is required")
        return

    catalog_path = Path(args.catalog)

    if args.waterbody_id:
        # Single water body mode
        df = pd.read_parquet(catalog_path)
        id_col = "permanent_id" if "permanent_id" in df.columns else df.columns[0]
        row = df[df[id_col].astype(str) == args.waterbody_id]
        if row.empty:
            log.error(f"Water body {args.waterbody_id} not found in catalog")
            return

        row = row.iloc[0]
        result = enrich_waterbody(
            args.waterbody_id,
            str(row.get("name", "")),
            row.get("centroid_lat", row.get("lake_lat")),
            row.get("centroid_lon", row.get("lake_lon")),
            str(row.get("state", row.get("province", ""))),
            sources,
        )
        print(json.dumps(result, indent=2, default=str))
    else:
        run_batch(
            catalog_path, output_dir, sources,
            args.batch_start, args.batch_size, resume, args.save_interval,
        )


if __name__ == "__main__":
    main()
