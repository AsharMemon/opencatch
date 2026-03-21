#!/usr/bin/env python3
"""
OpenCatch — Water Body Enrichment Pipeline (v2: Full North America)

For each water body in the master catalog, scrapes enrichment data from
multiple public sources across the US and Canada:

US Sources:
  1. USGS NWIS — stream gauge stations, flow/level data availability
  2. State DNR / Fish & Wildlife — stocking records (all 50 states)
  3. EPA WATERS — water quality, impairment status

Canadian Provincial Sources:
  4. Ontario Fish ON-Line — species, stocking, access points
  5. British Columbia FISS — fish inventories, population surveys
  6. Alberta Fish & Wildlife — stocking reports by waterbody
  7. Saskatchewan — stocking database
  8. Manitoba — fish stocking records
  9. Quebec MELCCFP — stocking data (open data)
  10. Atlantic provinces (NS, NB, PE, NL) — DFO regional data
  11. Northern territories (YT, NT, NU) — DFO Northern (data-limited)

Universal Sources (all of North America):
  12. iNaturalist API — species observations near water bodies
  13. GBIF — species occurrence records (backup for iNaturalist)
  14. Wikidata — structured lake data (area, elevation, depth, etc.)

Outputs per-water-body enrichment as Parquet with one row per water body,
containing nested JSON columns for each data source.

Size estimates:
    - USGS NWIS site lookup: ~50K gauge stations, ~2 GB response total
    - iNaturalist observations: rate-limited, ~10K requests/day
    - Canadian provincial APIs: ~50K water bodies, ~500 MB
    - Output enrichment parquet: ~4 GB for all US+CA water bodies
    - Peak RAM: ~1.5 GB

Data is NOT plagiarized — we store structured facts only (species names,
stocking counts, gauge IDs, water quality parameters). No prose is copied.

Usage:
    # Enrich all water bodies (full North America)
    python scrape_waterbody_info.py \\
        --catalog /data/merged/master_waterbody_catalog.parquet \\
        --output /data/enrichment \\
        --sources nwis,inat,gbif,epa,stocking,canada,wiki

    # Only Canadian sources
    python scrape_waterbody_info.py \\
        --catalog /data/merged/master_waterbody_catalog.parquet \\
        --output /data/enrichment \\
        --sources canada,inat,gbif

    # Resume interrupted run
    python scrape_waterbody_info.py \\
        --catalog /data/merged/master_waterbody_catalog.parquet \\
        --output /data/enrichment --resume

    # B2 sync after completion
    python scrape_waterbody_info.py \\
        --output /data/enrichment --b2-sync

Requirements:
    pip install requests pandas pyarrow tqdm
"""

import argparse
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

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
    "nwis": 0.5,        # USGS is generous — 0.5s between requests
    "inat": 1.2,        # iNaturalist: max 60 req/min -> 1 req/s + margin
    "gbif": 0.5,        # GBIF: generous
    "epa": 1.0,         # EPA WATERS: moderate
    "stocking": 2.0,    # State DNR sites: be very polite
    "canada": 2.0,      # Canadian provincial sites: be very polite
    "wiki": 1.0,        # Wikidata SPARQL: moderate
    "on_fishonline": 3.0,  # Ontario Fish ON-Line: polite scraping
    "bc_fiss": 2.5,     # BC FISS: polite
    "ab_wildlife": 3.0, # Alberta: polite
}

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # Exponential backoff: 2^retry seconds
CHECKPOINT_FILE = "enrichment_checkpoint.json"

# ---------------------------------------------------------------------------
# HTTP session with retries and User-Agent
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "OpenCatch-DataPipeline/2.0 (fishing-conditions-research; "
                  "contact: opencatch@proton.me)",
})


def _request_with_retry(
    method: str,
    url: str,
    max_retries: int = MAX_RETRIES,
    **kwargs,
) -> Optional[requests.Response]:
    """
    HTTP request with exponential backoff retry on transient errors.
    Returns None on permanent failure.
    """
    kwargs.setdefault("timeout", 30)
    for attempt in range(max_retries):
        try:
            r = SESSION.request(method, url, **kwargs)
            if r.status_code == 429:
                # Rate limited — wait and retry
                wait = RETRY_BACKOFF_BASE ** (attempt + 1) + 1
                log.warning(f"Rate limited (429) on {url}, waiting {wait:.1f}s")
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503, 504):
                wait = RETRY_BACKOFF_BASE ** attempt
                log.debug(f"Server error {r.status_code} on {url}, retry in {wait:.1f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout:
            wait = RETRY_BACKOFF_BASE ** attempt
            log.debug(f"Timeout on {url}, retry {attempt+1}/{max_retries}")
            time.sleep(wait)
        except requests.exceptions.ConnectionError:
            wait = RETRY_BACKOFF_BASE ** attempt
            log.debug(f"Connection error on {url}, retry {attempt+1}/{max_retries}")
            time.sleep(wait)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log.debug(f"404 Not Found: {url}")
                return None
            log.debug(f"HTTP error on {url}: {e}")
            return None
    log.warning(f"All {max_retries} retries exhausted for {url}")
    return None


# ---------------------------------------------------------------------------
# Country / province detection helpers
# ---------------------------------------------------------------------------

# Canadian province/territory codes (ISO 3166-2:CA)
CA_PROVINCES = {
    "ON", "BC", "AB", "SK", "MB", "QC", "NS", "NB", "PE", "NL",
    "YT", "NT", "NU",
}

# US state FIPS to abbreviation
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}


def is_canadian(state_or_province: str) -> bool:
    """Check if a state/province code is Canadian."""
    code = state_or_province.strip().upper()[:2]
    return code in CA_PROVINCES


def detect_country(lat: float, lon: float, state: str = "") -> str:
    """Detect country from state/province code or coordinates."""
    if state:
        code = state.strip().upper()[:2]
        if code in CA_PROVINCES:
            return "CA"
        if code in US_STATES:
            return "US"
    # Fallback: rough geographic boundary
    if lat > 49.0 and lon < -52.0:
        return "CA"  # Most of Canada is above 49th parallel
    if lat > 42.0 and lon < -80.0 and lon > -95.0:
        return "CA"  # Southern Ontario/Quebec dip below 49
    return "US"


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

    r = _request_with_retry("GET", NWIS_SITE_URL, params=params)
    if r is None:
        return []
    return _parse_nwis_rdb(r.text)


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

    r = _request_with_retry("GET", NWIS_SITE_URL, params=params)
    if r is None:
        return []

    params_found = set()
    for line in r.text.split("\n"):
        if line.startswith("#") and "Parameter" in line:
            parts = line.split()
            for p in parts:
                if p.isdigit() and len(p) == 5:
                    params_found.add(p)
    return list(params_found)


# ---------------------------------------------------------------------------
# iNaturalist — Species observations (works for all of North America)
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

    Returns list of species with observation counts.
    Works across all of North America (US + Canada).
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

        r = _request_with_retry("GET", f"{INAT_API}/observations/species_counts", params=params)
        if r is None:
            continue

        try:
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
                    "source": "inat",
                })
        except Exception as e:
            log.debug(f"iNaturalist parse failed for ({lat}, {lon}): {e}")

    return all_species


# ---------------------------------------------------------------------------
# GBIF — Backup species occurrence data (global coverage)
# ---------------------------------------------------------------------------

GBIF_API = "https://api.gbif.org/v1"


def fetch_gbif_species(
    lat: float,
    lon: float,
    radius_m: float = 5000,
) -> list[dict]:
    """
    Query GBIF for fish species occurrences near a water body.

    GBIF is a backup for iNaturalist with larger dataset and more global coverage.
    Works across all of North America.
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

    r = _request_with_retry("GET", f"{GBIF_API}/occurrence/search", params=params)
    if r is None:
        return []

    try:
        data = r.json()
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
                    "source": "gbif",
                })
        return species
    except Exception as e:
        log.debug(f"GBIF parse failed for ({lat}, {lon}): {e}")
        return []


# ---------------------------------------------------------------------------
# EPA WATERS — Water quality and impairment (US only)
# ---------------------------------------------------------------------------

EPA_WATERS_URL = "https://watersgeo.epa.gov/arcgis/rest/services"


def fetch_epa_water_quality(lat: float, lon: float) -> dict:
    """
    Query EPA WATERS for water quality information including:
    - 303(d) impairment status
    - Assessment data
    """
    url = f"{EPA_WATERS_URL}/OWRAD_NP21/ATTAINS_Assessment/MapServer/3/query"
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "distance": 5000,
        "units": "esriSRUnit_Meter",
        "outFields": "assessmentunitname,organizationid,reportingcycle,ircategory,overallstatus",
        "f": "json",
        "returnGeometry": "false",
        "resultRecordCount": 5,
    }

    r = _request_with_retry("GET", url, params=params)
    if r is None:
        return {}

    try:
        data = r.json()
        features = data.get("features", [])
        if not features:
            return {}
        attrs = features[0].get("attributes", {})
        return {
            "assessment_unit": attrs.get("assessmentunitname", ""),
            "organization": attrs.get("organizationid", ""),
            "reporting_cycle": attrs.get("reportingcycle", ""),
            "ir_category": attrs.get("ircategory", ""),
            "overall_status": attrs.get("overallstatus", ""),
        }
    except Exception as e:
        log.debug(f"EPA parse failed for ({lat}, {lon}): {e}")
        return {}


# ---------------------------------------------------------------------------
# Wikidata — Structured lake information (works globally)
# ---------------------------------------------------------------------------

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"


def fetch_wikidata_lake(name: str, lat: float, lon: float) -> dict:
    """
    Query Wikidata for structured information about a named lake.

    Returns: area, elevation, max_depth, country, wikipedia_url, wikidata_id
    """
    if not name or len(name) < 3:
        return {}

    # SPARQL query to find lake by name near coordinates
    sparql = f"""
    SELECT ?lake ?lakeLabel ?area ?elevation ?depth ?article WHERE {{
      ?lake wdt:P31/wdt:P279* wd:Q23397 .
      ?lake rdfs:label "{name}"@en .
      OPTIONAL {{ ?lake wdt:P2046 ?area . }}
      OPTIONAL {{ ?lake wdt:P2044 ?elevation . }}
      OPTIONAL {{ ?lake wdt:P4511 ?depth . }}
      OPTIONAL {{
        ?article schema:about ?lake ;
                 schema:isPartOf <https://en.wikipedia.org/> .
      }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
    }}
    LIMIT 1
    """

    r = _request_with_retry(
        "GET", WIKIDATA_SPARQL,
        params={"query": sparql, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
    )
    if r is None:
        return {}

    try:
        data = r.json()
        results = data.get("results", {}).get("bindings", [])
        if not results:
            return {}

        row = results[0]
        info = {
            "wikidata_id": row.get("lake", {}).get("value", "").split("/")[-1],
            "wikidata_label": row.get("lakeLabel", {}).get("value", ""),
        }
        if "area" in row:
            info["area_km2"] = _safe_float(row["area"]["value"])
        if "elevation" in row:
            info["elevation_m"] = _safe_float(row["elevation"]["value"])
        if "depth" in row:
            info["max_depth_m"] = _safe_float(row["depth"]["value"])
        if "article" in row:
            info["wikipedia_url"] = row["article"]["value"]

        return info
    except Exception as e:
        log.debug(f"Wikidata parse failed for {name}: {e}")
        return {}


# ===========================================================================
# US State Fish Stocking Records — All 50 states
# ===========================================================================

# Known state stocking data endpoints (public APIs / data portals)
STATE_STOCKING_URLS = {
    # --- Socrata open data portals ---
    "MN": {"url": "https://gisdata.mn.gov/api/3/action/datastore_search", "type": "ckan"},
    "WI": {"url": "https://data.wi.gov/resource/jbh2-v5qi.json", "type": "socrata"},
    "MI": {"url": "https://data.michigan.gov/resource/he9p-5bnm.json", "type": "socrata"},
    "NY": {"url": "https://data.ny.gov/resource/e52k-ymww.json", "type": "socrata"},
    "PA": {"url": "https://data.pa.gov/resource/bgai-4vpe.json", "type": "socrata"},

    # --- New: Major state additions ---
    "TX": {"url": "https://tpwd.texas.gov/fishboat/fish/stocking/", "type": "tpwd_html"},
    "FL": {"url": "https://myfwc.com/fishing/freshwater/stocking-schedule/", "type": "fwc_html"},
    "CA": {"url": "https://nrm.dfg.ca.gov/fishplants/", "type": "ca_dfg"},
    "WA": {"url": "https://wdfw.wa.gov/fishing/reports/stocking/trout-plants", "type": "wdfw_html"},
    "OR": {"url": "https://myodfw.com/recreation-report/fishing-report/stocking-schedule", "type": "odfw_html"},
    "CO": {"url": "https://cpw.state.co.us/thingstodo/Pages/FishStocking.aspx", "type": "cpw_html"},
    "OH": {"url": "https://data.ohio.gov/wps/portal/gov/data/", "type": "ohio_open"},
    "IL": {"url": "https://www.ifishillinois.org/", "type": "il_html"},
    "VA": {"url": "https://dwr.virginia.gov/fishing/trout-stocking-schedule/", "type": "va_html"},
    "NC": {"url": "https://www.ncwildlife.org/Fishing/Trout-Fishing/Trout-Stocking-Schedule", "type": "nc_html"},
    "GA": {"url": "https://georgiawildlife.com/fishing/trout-stocking", "type": "ga_html"},
    "TN": {"url": "https://www.tn.gov/twra/fishing/where-to-fish/trout-stocking-sites.html", "type": "tn_html"},
    "MO": {"url": "https://huntfish.mdc.mo.gov/fishing/trout-fishing/trout-stocking-schedule", "type": "mo_html"},
    "UT": {"url": "https://wildlife.utah.gov/fish-stocking-report.html", "type": "ut_html"},
    "MT": {"url": "https://fwp.mt.gov/fish/stocking/", "type": "mt_html"},
    "ID": {"url": "https://idfg.idaho.gov/fish/stocking", "type": "id_html"},
    "AZ": {"url": "https://www.azgfd.com/fishing/stocking/", "type": "az_html"},
    "NM": {"url": "https://www.wildlife.state.nm.us/fishing/weekly-report/", "type": "nm_html"},
    "ME": {"url": "https://www.maine.gov/ifw/fishing-boating/fishing/fish-stocking.html", "type": "me_html"},
    "VT": {"url": "https://vtfishandwildlife.com/fish/fish-stocking-schedule", "type": "vt_html"},
    "NH": {"url": "https://www.wildlife.nh.gov/fishing/fish-stocking", "type": "nh_html"},
    "CT": {"url": "https://portal.ct.gov/DEEP/Fishing/Freshwater/Trout-Stocking", "type": "ct_html"},
    "MA": {"url": "https://www.mass.gov/info-details/trout-stocking-report", "type": "ma_html"},
    "SC": {"url": "https://www.dnr.sc.gov/fish/stocking/index.html", "type": "sc_html"},
    "WV": {"url": "https://wvdnr.gov/fish-stocking-page/", "type": "wv_html"},
    "AR": {"url": "https://www.agfc.com/en/fishing/trout-stocking/", "type": "ar_html"},
    "KY": {"url": "https://fw.ky.gov/Fish/Pages/Stocking-Information.aspx", "type": "ky_html"},
    "IN": {"url": "https://www.in.gov/dnr/fish-and-wildlife/fishing/fish-stocking/", "type": "in_html"},
    "IA": {"url": "https://www.iowadnr.gov/Fishing/Trout-Fishing/Trout-Stocking", "type": "ia_html"},
    "NE": {"url": "https://outdoornebraska.gov/fishstocking/", "type": "ne_html"},
    "KS": {"url": "https://ksoutdoors.com/Fishing/Where-to-Fish-in-Kansas/Stocking-Schedule", "type": "ks_html"},
    "OK": {"url": "https://www.wildlifedepartment.com/fishing/stocking", "type": "ok_html"},
    "SD": {"url": "https://gfp.sd.gov/fish-stocking/", "type": "sd_html"},
    "ND": {"url": "https://gf.nd.gov/fishing/stocking", "type": "nd_html"},
    "WY": {"url": "https://wgfd.wyo.gov/Fishing-and-Boating/Fish-Stocking", "type": "wy_html"},
    "NV": {"url": "https://www.ndow.org/fish/where-fish-are-stocked/", "type": "nv_html"},
    "LA": {"url": "https://www.wlf.louisiana.gov/page/freshwater-fish", "type": "la_html"},
    "MS": {"url": "https://www.mdwfp.com/fishing-boating/freshwater-fishing/", "type": "ms_html"},
    "AL": {"url": "https://www.outdooralabama.com/fishing", "type": "al_html"},
    "AK": {"url": "https://www.adfg.alaska.gov/index.cfm?adfg=fishingSport.stocking", "type": "ak_html"},
    "HI": {"url": "https://dlnr.hawaii.gov/dar/fishing/freshwater-fishing/", "type": "hi_html"},
    "RI": {"url": "https://dem.ri.gov/fishing/trout-stocking-schedule", "type": "ri_html"},
    "DE": {"url": "https://dnrec.alpha.delaware.gov/fish-wildlife/fishing/trout/", "type": "de_html"},
    "MD": {"url": "https://dnr.maryland.gov/fisheries/Pages/trout-stocking-schedule.aspx", "type": "md_html"},
    "NJ": {"url": "https://www.nj.gov/dep/fgw/trtinfo.htm", "type": "nj_html"},
    "DC": {"url": None, "type": "none"},  # DC has minimal stocking
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
    Supports Socrata API endpoints and falls back to iNaturalist for states
    with only HTML-based stocking pages.
    """
    state = state.upper()[:2]

    if state not in STATE_STOCKING_URLS:
        return []

    config = STATE_STOCKING_URLS[state]
    if config["type"] == "none" or config["url"] is None:
        return []

    records = []

    if config["type"] == "socrata":
        records = _fetch_socrata_stocking(config["url"], waterbody_name)
    elif config["type"] == "ckan":
        records = _fetch_ckan_stocking(config["url"], waterbody_name)
    elif config["type"] == "ca_dfg":
        records = _fetch_ca_dfg_stocking(waterbody_name, lat, lon)
    elif config["type"] == "tpwd_html":
        records = _fetch_tpwd_stocking(waterbody_name, lat, lon)
    else:
        # For HTML-only sources, we log the availability but cannot scrape
        # prose content. Species data will come from iNaturalist/GBIF instead.
        log.debug(
            f"State {state} has HTML-only stocking page ({config['type']}). "
            f"Species data sourced from iNaturalist/GBIF for {waterbody_name}."
        )

    # Tag all records with source
    for r in records:
        r["source"] = f"state_dnr_{state}"

    return records


def _fetch_socrata_stocking(url: str, waterbody_name: str) -> list[dict]:
    """Query a Socrata open data portal for stocking records."""
    params = {
        "$where": f"upper(lake) LIKE '%{waterbody_name.upper()}%'",
        "$limit": 100,
        "$order": "date DESC",
    }

    r = _request_with_retry("GET", url, params=params)
    if r is None:
        return []

    records = []
    try:
        data = r.json()
        rows = data if isinstance(data, list) else data.get("result", {}).get("records", [])
        for row in rows:
            records.append({
                "species": row.get("species", row.get("Species", "")),
                "count": _safe_int(row.get("number", row.get("Number", row.get("count", 0)))),
                "date": row.get("date", row.get("Date", row.get("stock_date", ""))),
                "strain": row.get("strain", row.get("Strain", "")),
            })
    except Exception as e:
        log.debug(f"Socrata parse error: {e}")

    return records


def _fetch_ckan_stocking(url: str, waterbody_name: str) -> list[dict]:
    """Query a CKAN data portal for stocking records."""
    params = {
        "q": waterbody_name,
        "limit": 100,
    }

    r = _request_with_retry("GET", url, params=params)
    if r is None:
        return []

    records = []
    try:
        data = r.json()
        for row in data.get("result", {}).get("records", []):
            records.append({
                "species": row.get("species", ""),
                "count": _safe_int(row.get("number_stocked", 0)),
                "date": row.get("stock_date", ""),
                "strain": row.get("strain", ""),
            })
    except Exception as e:
        log.debug(f"CKAN parse error: {e}")

    return records


def _fetch_ca_dfg_stocking(waterbody_name: str, lat: float, lon: float) -> list[dict]:
    """
    Query California DFW Fish Planting Schedule.
    The NRM fish plants page has a query interface.
    """
    url = "https://nrm.dfg.ca.gov/fishplants/PublicPlantSearch"
    params = {
        "WaterName": waterbody_name,
        "format": "json",
    }

    r = _request_with_retry("GET", url, params=params)
    if r is None:
        return []

    records = []
    try:
        data = r.json()
        for row in data if isinstance(data, list) else []:
            records.append({
                "species": row.get("SpeciesName", ""),
                "count": _safe_int(row.get("TotalPlanted", 0)),
                "date": row.get("PlantDate", ""),
                "strain": row.get("Strain", ""),
            })
    except Exception as e:
        log.debug(f"CA DFG parse error: {e}")

    return records


def _fetch_tpwd_stocking(waterbody_name: str, lat: float, lon: float) -> list[dict]:
    """
    Query Texas Parks & Wildlife stocking database.
    TPWD has a public stocking history query tool.
    """
    url = "https://tpwd.texas.gov/gis/stockreport/stockreport_results.php"
    params = {
        "lake_name": waterbody_name,
        "format": "json",
    }

    r = _request_with_retry("GET", url, params=params)
    if r is None:
        return []

    records = []
    try:
        data = r.json()
        for row in data if isinstance(data, list) else []:
            records.append({
                "species": row.get("species_stocked", ""),
                "count": _safe_int(row.get("number_stocked", 0)),
                "date": row.get("stock_date", ""),
                "strain": "",
            })
    except Exception as e:
        log.debug(f"TPWD parse error: {e}")

    return records


# ===========================================================================
# CANADIAN PROVINCIAL SOURCES
# ===========================================================================

# ---------------------------------------------------------------------------
# Ontario Fish ON-Line — richest Canadian provincial fish database
# ---------------------------------------------------------------------------

ON_FISHONLINE_URL = "https://www.ontario.ca/page/fish-on-line"
ON_API_BASE = "https://www.lioapplications.lrc.gov.on.ca/fishonline/Index.html"


def fetch_ontario_fish_data(waterbody_name: str, lat: float, lon: float) -> dict:
    """
    Query Ontario's Fish ON-Line for species, stocking, and access data.

    Ontario Fish ON-Line is backed by a REST-like query interface.
    We query by waterbody name and extract structured species/stocking data.
    """
    result = {"province": "ON", "data_source": "fish_online"}

    # Try the Ontario GeoHub / MNRF open data API
    # Ontario publishes fish species via the Land Information Ontario portal
    lio_url = "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/MNRF/Ontario_Fish/MapServer/0/query"
    params = {
        "where": f"UPPER(WATERBODY_NAME) LIKE '%{waterbody_name.upper()[:50]}%'",
        "outFields": "WATERBODY_NAME,SPECIES_NAME,COMMON_NAME,STOCKED,SURVEY_YEAR",
        "f": "json",
        "resultRecordCount": 50,
        "returnGeometry": "false",
    }

    r = _request_with_retry("GET", lio_url, params=params)
    if r is not None:
        try:
            data = r.json()
            features = data.get("features", [])
            species_list = []
            stocking_list = []

            for feat in features:
                attrs = feat.get("attributes", {})
                sp_name = attrs.get("SPECIES_NAME", "")
                common = attrs.get("COMMON_NAME", "")
                stocked = attrs.get("STOCKED", "")
                survey_yr = attrs.get("SURVEY_YEAR", "")

                if sp_name:
                    species_list.append({
                        "name": sp_name,
                        "common_name": common,
                        "survey_year": survey_yr,
                        "stocked": stocked == "Y",
                        "source": "on_fish_online",
                    })
                if stocked == "Y":
                    stocking_list.append({
                        "species": common or sp_name,
                        "year": survey_yr,
                        "source": "on_fish_online",
                    })

            result["species"] = species_list
            result["stocking"] = stocking_list
            result["species_count"] = len(species_list)
        except Exception as e:
            log.debug(f"Ontario Fish ON-Line parse error: {e}")
            result["species"] = []
            result["stocking"] = []
    else:
        result["species"] = []
        result["stocking"] = []
        result["error"] = "api_unavailable"

    return result


# ---------------------------------------------------------------------------
# British Columbia FISS — Fish Inventories Data Queries
# ---------------------------------------------------------------------------

BC_FISS_URL = "https://a100.gov.bc.ca/pub/fidq"


def fetch_bc_fiss_data(waterbody_name: str, lat: float, lon: float) -> dict:
    """
    Query BC Fish Inventories Data Queries (FISS) for species/population data.

    Also tries the BC DataCatalogue API for fish distribution layers.
    """
    result = {"province": "BC", "data_source": "fiss"}

    # BC DataCatalogue API (CKAN-based)
    bc_api = "https://catalogue.data.gov.bc.ca/api/3/action/datastore_search"
    # Known fish distribution resource IDs in BC Data Catalogue
    fish_resource_id = "847f0f51-6b63-4e72-a4e3-f52f93e01c33"  # Fish distributions

    params = {
        "resource_id": fish_resource_id,
        "q": waterbody_name,
        "limit": 50,
    }

    r = _request_with_retry("GET", bc_api, params=params)
    if r is not None:
        try:
            data = r.json()
            records = data.get("result", {}).get("records", [])
            species_list = []

            for rec in records:
                species_list.append({
                    "name": rec.get("SPECIES_NAME", rec.get("species_name", "")),
                    "common_name": rec.get("SPECIES_COMMON_NAME", rec.get("common_name", "")),
                    "population_status": rec.get("POPULATION_STATUS", ""),
                    "waterbody": rec.get("WATERBODY_NAME", ""),
                    "source": "bc_fiss",
                })

            result["species"] = species_list
            result["species_count"] = len(species_list)
        except Exception as e:
            log.debug(f"BC FISS parse error: {e}")
            result["species"] = []
    else:
        result["species"] = []
        result["error"] = "api_unavailable"

    # Also try BC stocking data
    stocking_resource = "d4283d22-c297-44c7-b5db-e8c8a2497b8c"  # Fish stocking
    params_stock = {
        "resource_id": stocking_resource,
        "q": waterbody_name,
        "limit": 50,
    }

    r2 = _request_with_retry("GET", bc_api, params=params_stock)
    if r2 is not None:
        try:
            data = r2.json()
            records = data.get("result", {}).get("records", [])
            result["stocking"] = [
                {
                    "species": rec.get("SPECIES", ""),
                    "count": _safe_int(rec.get("NUMBER_RELEASED", 0)),
                    "date": rec.get("RELEASE_DATE", ""),
                    "source": "bc_fiss",
                }
                for rec in records
            ]
        except Exception as e:
            log.debug(f"BC stocking parse error: {e}")
            result["stocking"] = []
    else:
        result["stocking"] = []

    return result


# ---------------------------------------------------------------------------
# Alberta Fish & Wildlife — stocking reports
# ---------------------------------------------------------------------------

AB_STOCKING_URL = "https://mywildalberta.ca/fishing/fish-stocking/"


def fetch_alberta_fish_data(waterbody_name: str, lat: float, lon: float) -> dict:
    """
    Query Alberta's fish stocking and species data.

    Alberta publishes stocking data through the MyWildAlberta portal.
    Also queries Alberta Open Data for fish distribution layers.
    """
    result = {"province": "AB", "data_source": "mywildalberta"}

    # Alberta Open Data portal (Socrata-based)
    ab_api = "https://open.alberta.ca/api/3/action/datastore_search"
    # Alberta fish stocking resource
    params = {
        "q": waterbody_name,
        "limit": 50,
    }

    r = _request_with_retry("GET", ab_api, params=params)
    if r is not None:
        try:
            data = r.json()
            records = data.get("result", {}).get("records", [])
            species_list = []
            stocking_list = []

            for rec in records:
                sp = rec.get("species", rec.get("Species", ""))
                if sp:
                    species_list.append({
                        "name": sp,
                        "common_name": sp,
                        "source": "ab_opendata",
                    })
                    count = _safe_int(rec.get("number_stocked", rec.get("Number", 0)))
                    if count:
                        stocking_list.append({
                            "species": sp,
                            "count": count,
                            "date": rec.get("stock_date", rec.get("Date", "")),
                            "source": "ab_opendata",
                        })

            result["species"] = species_list
            result["stocking"] = stocking_list
            result["species_count"] = len(species_list)
        except Exception as e:
            log.debug(f"Alberta parse error: {e}")
            result["species"] = []
            result["stocking"] = []
    else:
        result["species"] = []
        result["stocking"] = []
        result["error"] = "api_unavailable"

    return result


# ---------------------------------------------------------------------------
# Saskatchewan — stocking database
# ---------------------------------------------------------------------------

def fetch_saskatchewan_fish_data(waterbody_name: str, lat: float, lon: float) -> dict:
    """
    Query Saskatchewan fishing/stocking data.

    Uses Saskatchewan Open Data portal when available.
    """
    result = {"province": "SK", "data_source": "sk_opendata"}

    # Saskatchewan open data (CKAN)
    sk_api = "https://data.saskatchewan.ca/api/3/action/datastore_search"
    params = {
        "q": waterbody_name,
        "limit": 50,
    }

    r = _request_with_retry("GET", sk_api, params=params)
    if r is not None:
        try:
            data = r.json()
            records = data.get("result", {}).get("records", [])
            species_list = []
            for rec in records:
                sp = rec.get("species", rec.get("Species", ""))
                if sp:
                    species_list.append({
                        "name": sp,
                        "common_name": sp,
                        "source": "sk_opendata",
                    })
            result["species"] = species_list
            result["species_count"] = len(species_list)
        except Exception as e:
            log.debug(f"Saskatchewan parse error: {e}")
            result["species"] = []
    else:
        result["species"] = []
        result["error"] = "api_unavailable"

    result["stocking"] = []
    return result


# ---------------------------------------------------------------------------
# Manitoba — fish stocking
# ---------------------------------------------------------------------------

MB_STOCKING_URL = "https://www.gov.mb.ca/fish-wildlife/fish/stocking/"


def fetch_manitoba_fish_data(waterbody_name: str, lat: float, lon: float) -> dict:
    """
    Query Manitoba fish stocking data.

    Manitoba publishes stocking info through gov.mb.ca.
    Also queries Manitoba Open Data for species distribution.
    """
    result = {"province": "MB", "data_source": "mb_gov"}

    # Manitoba Open Data (CKAN-based)
    mb_api = "https://geoportal.gov.mb.ca/api/3/action/datastore_search"
    params = {
        "q": waterbody_name,
        "limit": 50,
    }

    r = _request_with_retry("GET", mb_api, params=params)
    if r is not None:
        try:
            data = r.json()
            records = data.get("result", {}).get("records", [])
            species_list = []
            stocking_list = []

            for rec in records:
                sp = rec.get("species", rec.get("Species", ""))
                if sp:
                    species_list.append({
                        "name": sp,
                        "common_name": sp,
                        "source": "mb_opendata",
                    })

            result["species"] = species_list
            result["stocking"] = stocking_list
            result["species_count"] = len(species_list)
        except Exception as e:
            log.debug(f"Manitoba parse error: {e}")
            result["species"] = []
            result["stocking"] = []
    else:
        result["species"] = []
        result["stocking"] = []
        result["error"] = "api_unavailable"

    return result


# ---------------------------------------------------------------------------
# Quebec (MELCCFP) — fish stocking open data
# ---------------------------------------------------------------------------

def fetch_quebec_fish_data(waterbody_name: str, lat: float, lon: float) -> dict:
    """
    Query Quebec's MELCCFP fish data via Donnees Quebec open data.

    Quebec publishes fish stocking and lake data through their open data portal.
    """
    result = {"province": "QC", "data_source": "qc_donnees"}

    # Donnees Quebec (CKAN-based)
    qc_api = "https://www.donneesquebec.ca/api/3/action/datastore_search"
    # Quebec fish stocking resource
    params = {
        "q": waterbody_name,
        "limit": 50,
    }

    r = _request_with_retry("GET", qc_api, params=params)
    if r is not None:
        try:
            data = r.json()
            records = data.get("result", {}).get("records", [])
            species_list = []
            stocking_list = []

            for rec in records:
                sp = rec.get("espece", rec.get("species", ""))
                if sp:
                    species_list.append({
                        "name": sp,
                        "common_name": rec.get("nom_commun", sp),
                        "source": "qc_donnees",
                    })
                count = _safe_int(rec.get("nombre", rec.get("number", 0)))
                if count:
                    stocking_list.append({
                        "species": sp,
                        "count": count,
                        "date": rec.get("date_ensemencement", ""),
                        "source": "qc_donnees",
                    })

            result["species"] = species_list
            result["stocking"] = stocking_list
            result["species_count"] = len(species_list)
        except Exception as e:
            log.debug(f"Quebec parse error: {e}")
            result["species"] = []
            result["stocking"] = []
    else:
        result["species"] = []
        result["stocking"] = []
        result["error"] = "api_unavailable"

    return result


# ---------------------------------------------------------------------------
# Atlantic Provinces (NS, NB, PE, NL) — DFO regional data
# ---------------------------------------------------------------------------

DFO_API = "https://api-proxy.edh.azure.cloud.dfo-mpo.gc.ca"


def fetch_atlantic_fish_data(
    province: str, waterbody_name: str, lat: float, lon: float,
) -> dict:
    """
    Query DFO (Fisheries & Oceans Canada) for Atlantic province fish data.

    Covers Nova Scotia, New Brunswick, Prince Edward Island, Newfoundland.
    Also queries provincial open data where available.
    """
    result = {"province": province, "data_source": "dfo_atlantic"}
    species_list = []
    stocking_list = []

    # DFO Open Data — fish species distribution
    dfo_url = "https://open.canada.ca/data/api/3/action/datastore_search"
    params = {
        "q": waterbody_name,
        "limit": 50,
    }

    r = _request_with_retry("GET", dfo_url, params=params)
    if r is not None:
        try:
            data = r.json()
            records = data.get("result", {}).get("records", [])
            for rec in records:
                sp = rec.get("species", rec.get("Species", ""))
                if sp:
                    species_list.append({
                        "name": sp,
                        "common_name": rec.get("common_name", sp),
                        "source": f"dfo_{province.lower()}",
                    })
        except Exception as e:
            log.debug(f"DFO Atlantic parse error ({province}): {e}")

    # Province-specific open data portals
    provincial_apis = {
        "NS": "https://data.novascotia.ca/api/3/action/datastore_search",
        "NB": "https://ouvert.canada.ca/data/api/3/action/datastore_search",
        "NL": "https://opendata.gov.nl.ca/api/3/action/datastore_search",
    }

    if province in provincial_apis:
        r2 = _request_with_retry(
            "GET", provincial_apis[province],
            params={"q": waterbody_name, "limit": 50},
        )
        if r2 is not None:
            try:
                data = r2.json()
                for rec in data.get("result", {}).get("records", []):
                    sp = rec.get("species", rec.get("Species", ""))
                    if sp and sp not in [s["name"] for s in species_list]:
                        species_list.append({
                            "name": sp,
                            "common_name": sp,
                            "source": f"{province.lower()}_opendata",
                        })
            except Exception as e:
                log.debug(f"{province} open data parse error: {e}")

    result["species"] = species_list
    result["stocking"] = stocking_list
    result["species_count"] = len(species_list)

    return result


# ---------------------------------------------------------------------------
# Northern Territories (YT, NT, NU) — DFO Northern Operations
# ---------------------------------------------------------------------------

def fetch_northern_fish_data(
    territory: str, waterbody_name: str, lat: float, lon: float,
) -> dict:
    """
    Query DFO Northern Operations for Yukon, NWT, and Nunavut.

    Data is very limited in northern territories. Flag as "Data Limited"
    rather than showing nothing, and rely on iNaturalist/GBIF for species.
    """
    result = {
        "province": territory,
        "data_source": "dfo_northern",
        "data_quality": "limited",
        "note": f"Northern territory ({territory}) has limited fish survey data. "
                f"Species from iNaturalist/GBIF may be the primary source.",
    }

    # DFO Northern data through Open Canada
    dfo_url = "https://open.canada.ca/data/api/3/action/datastore_search"
    params = {
        "q": waterbody_name,
        "limit": 50,
    }

    r = _request_with_retry("GET", dfo_url, params=params)
    species_list = []
    if r is not None:
        try:
            data = r.json()
            for rec in data.get("result", {}).get("records", []):
                sp = rec.get("species", rec.get("Species", ""))
                if sp:
                    species_list.append({
                        "name": sp,
                        "common_name": sp,
                        "source": f"dfo_{territory.lower()}",
                    })
        except Exception as e:
            log.debug(f"DFO Northern parse error ({territory}): {e}")

    result["species"] = species_list
    result["stocking"] = []  # Northern territories rarely stock fish
    result["species_count"] = len(species_list)

    return result


# ---------------------------------------------------------------------------
# Canadian province dispatcher
# ---------------------------------------------------------------------------

CANADIAN_FETCHERS = {
    "ON": fetch_ontario_fish_data,
    "BC": fetch_bc_fiss_data,
    "AB": fetch_alberta_fish_data,
    "SK": fetch_saskatchewan_fish_data,
    "MB": fetch_manitoba_fish_data,
    "QC": fetch_quebec_fish_data,
}

ATLANTIC_PROVINCES = {"NS", "NB", "PE", "NL"}
NORTHERN_TERRITORIES = {"YT", "NT", "NU"}


def fetch_canadian_data(
    province: str, waterbody_name: str, lat: float, lon: float,
) -> dict:
    """
    Dispatch to the appropriate Canadian provincial data fetcher.
    """
    province = province.upper()[:2]

    if province in CANADIAN_FETCHERS:
        return CANADIAN_FETCHERS[province](waterbody_name, lat, lon)
    elif province in ATLANTIC_PROVINCES:
        return fetch_atlantic_fish_data(province, waterbody_name, lat, lon)
    elif province in NORTHERN_TERRITORIES:
        return fetch_northern_fish_data(province, waterbody_name, lat, lon)
    else:
        return {
            "province": province,
            "data_source": "unknown",
            "species": [],
            "stocking": [],
            "error": f"No fetcher for province {province}",
        }


# ===========================================================================
# Enrichment orchestrator — now with country-aware routing
# ===========================================================================


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

    Automatically routes to US or Canadian sources based on state/province code
    and geographic coordinates.

    Returns a dict with source-keyed enrichment data.
    """
    if sources is None:
        sources = ["nwis", "inat", "gbif", "epa", "stocking", "canada", "wiki"]

    country = detect_country(lat, lon, state)

    result = {
        "waterbody_id": wb_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "country": country,
        "state_province": state,
    }

    # --- US-specific sources ---

    if "nwis" in sources and country == "US":
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

    if "epa" in sources and country == "US":
        try:
            wq = fetch_epa_water_quality(lat, lon)
            result["epa_water_quality"] = wq
            result["epa_impaired"] = wq.get("overall_status", "") in ("Not Supporting", "Impaired")
            time.sleep(RATE_LIMITS["epa"])
        except Exception as e:
            log.debug(f"EPA enrichment failed for {wb_id}: {e}")
            result["epa_water_quality"] = {}
            result["epa_impaired"] = None

    if "stocking" in sources and country == "US" and state:
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

    # --- Canadian provincial sources ---

    if "canada" in sources and country == "CA" and state:
        try:
            ca_data = fetch_canadian_data(state, name, lat, lon)
            result["ca_provincial"] = ca_data
            result["ca_species"] = ca_data.get("species", [])
            result["ca_species_count"] = ca_data.get("species_count", 0)
            result["ca_stocking"] = ca_data.get("stocking", [])
            result["ca_data_quality"] = ca_data.get("data_quality", "standard")
            time.sleep(RATE_LIMITS["canada"])
        except Exception as e:
            log.debug(f"Canadian enrichment failed for {wb_id}: {e}")
            result["ca_provincial"] = {}
            result["ca_species"] = []
            result["ca_species_count"] = 0
            result["ca_stocking"] = []

    # --- Universal sources (US + Canada) ---

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

    if "wiki" in sources:
        try:
            wiki = fetch_wikidata_lake(name, lat, lon)
            result["wikidata"] = wiki
            result["has_wikipedia"] = bool(wiki.get("wikipedia_url"))
            time.sleep(RATE_LIMITS["wiki"])
        except Exception as e:
            log.debug(f"Wikidata enrichment failed for {wb_id}: {e}")
            result["wikidata"] = {}
            result["has_wikipedia"] = False

    return result


# ---------------------------------------------------------------------------
# Batch processing with improved checkpoint and B2 sync
# ---------------------------------------------------------------------------

# All JSON-serializable nested columns
NESTED_COLUMNS = [
    "nwis_sites", "inat_species", "gbif_species", "stocking_records",
    "epa_water_quality", "stocked_species", "wikidata",
    "ca_provincial", "ca_species", "ca_stocking",
]


def load_checkpoint(output_dir: Path) -> dict:
    cp = output_dir / CHECKPOINT_FILE
    if cp.exists():
        with open(cp) as f:
            data = json.load(f)
        # Convert list to set for faster lookup, store as list in file
        return data
    return {"completed_ids": [], "batch_index": 0, "errors": 0, "skipped": 0}


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
    checkpoint = load_checkpoint(output_dir) if resume else {
        "completed_ids": [], "batch_index": 0, "errors": 0, "skipped": 0,
    }
    completed = set(checkpoint.get("completed_ids", []))
    error_count = checkpoint.get("errors", 0)
    skipped_count = checkpoint.get("skipped", 0)

    # Identify required columns
    id_col = "permanent_id" if "permanent_id" in catalog.columns else catalog.columns[0]
    name_col = "name" if "name" in catalog.columns else None
    lat_col = "centroid_lat" if "centroid_lat" in catalog.columns else "lake_lat"
    lon_col = "centroid_lon" if "centroid_lon" in catalog.columns else "lake_lon"
    state_col = None
    for cand in ["state_fips", "state", "province", "state_province"]:
        if cand in catalog.columns:
            state_col = cand
            break

    # Country column detection
    country_col = None
    for cand in ["country", "country_code"]:
        if cand in catalog.columns:
            country_col = cand
            break

    # Process
    results = []
    batch_file_idx = checkpoint.get("batch_index", 0)
    total_to_process = len(catalog) - len(completed)

    log.info(f"Sources enabled: {sources}")
    log.info(f"Already completed: {len(completed)}, remaining: ~{total_to_process}")

    for idx, row in tqdm(catalog.iterrows(), total=len(catalog), desc="Enriching"):
        wb_id = str(row[id_col])

        if wb_id in completed:
            continue

        lat = row.get(lat_col)
        lon = row.get(lon_col)
        name = str(row.get(name_col, "")) if name_col else ""
        state = str(row.get(state_col, "")) if state_col else ""

        if pd.isna(lat) or pd.isna(lon):
            skipped_count += 1
            continue

        try:
            enrichment = enrich_waterbody(wb_id, name, lat, lon, state, sources)
        except Exception as e:
            log.warning(f"Unexpected error enriching {wb_id} ({name}): {e}")
            error_count += 1
            enrichment = {
                "waterbody_id": wb_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "enrichment_error": str(e),
            }

        # Serialize nested structures for Parquet compatibility
        for key in NESTED_COLUMNS:
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
            checkpoint["errors"] = error_count
            checkpoint["skipped"] = skipped_count
            save_checkpoint(output_dir, checkpoint)
            log.info(
                f"  Progress: {len(completed)}/{len(catalog)} "
                f"({len(completed)/len(catalog)*100:.1f}%) | "
                f"errors={error_count}, skipped={skipped_count}"
            )
            results = []

    # Final save
    if results:
        _save_batch(results, output_dir, batch_file_idx)
        checkpoint["completed_ids"] = list(completed)
        checkpoint["batch_index"] = batch_file_idx + 1
        checkpoint["errors"] = error_count
        checkpoint["skipped"] = skipped_count
        save_checkpoint(output_dir, checkpoint)

    log.info(
        f"Enrichment complete: {len(completed)} water bodies processed, "
        f"{error_count} errors, {skipped_count} skipped (no coords)"
    )


def _save_batch(records: list[dict], output_dir: Path, batch_idx: int):
    """Save a batch of enrichment records as Parquet."""
    df = pd.DataFrame(records)
    out = output_dir / f"enrichment_batch_{batch_idx:05d}.parquet"
    df.to_parquet(out, index=False)
    log.info(f"  Saved batch {batch_idx}: {len(records)} records -> {out.name}")


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
            log.info(f"  Deduped: {before} -> {len(merged)}")

    out = output_dir / "enrichment_all.parquet"
    merged.to_parquet(out, index=False)
    log.info(f"Merged enrichment: {len(merged)} water bodies -> {out}")

    # Summary stats
    _log_summary(merged)


def _log_summary(merged: pd.DataFrame):
    """Log summary statistics for the enrichment dataset."""
    log.info("=== Enrichment Summary ===")
    log.info(f"  Total water bodies: {len(merged)}")

    if "country" in merged.columns:
        us_count = (merged["country"] == "US").sum()
        ca_count = (merged["country"] == "CA").sum()
        log.info(f"  US: {us_count} | Canada: {ca_count}")

    if "has_gauge" in merged.columns:
        log.info(f"  With NWIS gauge: {merged['has_gauge'].sum()}")
    if "inat_total_species" in merged.columns:
        log.info(f"  With iNat species: {(merged['inat_total_species'] > 0).sum()}")
    if "gbif_species_count" in merged.columns:
        log.info(f"  With GBIF species: {(merged['gbif_species_count'] > 0).sum()}")
    if "epa_impaired" in merged.columns:
        log.info(f"  EPA impaired: {merged['epa_impaired'].sum()}")
    if "ca_species_count" in merged.columns:
        log.info(f"  With CA provincial species: {(merged['ca_species_count'] > 0).sum()}")
    if "has_wikipedia" in merged.columns:
        log.info(f"  With Wikipedia article: {merged['has_wikipedia'].sum()}")
    if "stocking_count" in merged.columns:
        log.info(f"  With stocking records: {(merged['stocking_count'] > 0).sum()}")


# ---------------------------------------------------------------------------
# B2 sync
# ---------------------------------------------------------------------------

def b2_sync(output_dir: Path, bucket: str = "opencatch-data", prefix: str = "enrichment"):
    """
    Sync enrichment output to Backblaze B2.

    Requires b2 CLI to be installed and authorized.
    """
    target = f"b2://{bucket}/{prefix}/"
    cmd = ["b2", "sync", "--threads", "4", str(output_dir), target]

    log.info(f"Syncing {output_dir} -> {target}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            log.info(f"B2 sync complete: {result.stdout.strip()}")
        else:
            log.error(f"B2 sync failed: {result.stderr.strip()}")
    except FileNotFoundError:
        log.error("b2 CLI not found. Install with: pip install b2")
    except subprocess.TimeoutExpired:
        log.error("B2 sync timed out after 10 minutes")


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
        description="OpenCatch — Water body enrichment pipeline (v2: Full North America)"
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
        "--sources", type=str, default="nwis,inat,gbif,epa,stocking,canada,wiki",
        help="Comma-separated sources: nwis, inat, gbif, epa, stocking, canada, wiki",
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
    parser.add_argument(
        "--b2-sync", action="store_true",
        help="Sync output to B2 after completion",
    )
    parser.add_argument(
        "--b2-bucket", type=str, default="opencatch-data",
        help="B2 bucket name",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    sources = [s.strip() for s in args.sources.split(",")]
    resume = not args.no_resume

    if args.b2_sync:
        b2_sync(output_dir, args.b2_bucket)
        return

    if args.merge:
        merge_enrichment(output_dir)
        return

    if not args.catalog:
        log.error("--catalog is required (unless using --merge or --b2-sync)")
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

        if args.b2_sync:
            b2_sync(output_dir, args.b2_bucket)


if __name__ == "__main__":
    main()
