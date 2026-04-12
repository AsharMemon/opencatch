#!/usr/bin/env python3
"""
Download lake bathymetry data from ALL US states and Canadian provinces.

Run this from a US-based machine (local laptop) since many state GIS
servers block non-US IPs. Downloads are saved to ~/Documents/fish/data/bathymetry/

Usage:
    python3 ml/bathymetry/download_all_states.py --output ~/Documents/fish/data/bathymetry
"""
import requests
import json
import os
import sys
import time
import logging
import argparse
from pathlib import Path
from urllib.parse import urlencode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("download_all")

# Browser-like headers to avoid blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, */*",
    "Referer": "https://www.google.com",
}


def arcgis_layer_url(base_url):
    return base_url[:-6] if base_url.endswith("/query") else base_url


def arcgis_query_url(base_url, **params):
    return f"{base_url}?{urlencode(params, doseq=True)}"


def write_geojson(output_path, features):
    with open(output_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)


def download_arcgis_by_object_ids(base_url, name, batch=1000):
    """Fallback for ArcGIS layers that do not support resultOffset pagination."""
    layer_url = arcgis_layer_url(base_url)
    ids_url = arcgis_query_url(
        base_url,
        where="1=1",
        returnIdsOnly="true",
        f="json",
    )
    try:
        ids_resp = requests.get(ids_url, headers=HEADERS, timeout=120)
        ids_resp.raise_for_status()
        ids_data = ids_resp.json()
        object_ids = ids_data.get("objectIds") or ids_data.get("objectids") or []
    except Exception as e:
        log.warning(f"  {name}: returnIdsOnly failed: {e}")
        return []

    if not object_ids:
        return []

    object_ids = sorted(object_ids)
    all_features = []
    object_id_field = ids_data.get("objectIdFieldName") or ids_data.get("objectIdFieldName".lower()) or "OBJECTID"

    def fetch_batch(batch_ids, batch_index, depth=0):
        url = arcgis_query_url(
            base_url,
            objectIds=",".join(str(v) for v in batch_ids),
            outFields="*",
            f="geojson",
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            if r.status_code != 200:
                if len(batch_ids) == 1:
                    log.warning(f"  {name}: HTTP {r.status_code} for objectId {batch_ids[0]}")
                    return []
                midpoint = len(batch_ids) // 2
                log.warning(
                    f"  {name}: HTTP {r.status_code} for objectIds batch {batch_index}; "
                    f"splitting {len(batch_ids)} ids at recursion depth {depth}"
                )
                left = fetch_batch(batch_ids[:midpoint], f"{batch_index}L", depth + 1)
                right = fetch_batch(batch_ids[midpoint:], f"{batch_index}R", depth + 1)
                return left + right
            data = r.json()
            features = data.get("features", [])
            if not features:
                if len(batch_ids) == 1:
                    log.warning(f"  {name}: empty objectId response for {batch_ids[0]}")
                    return []
                midpoint = len(batch_ids) // 2
                log.warning(
                    f"  {name}: empty objectIds batch {batch_index}; "
                    f"splitting {len(batch_ids)} ids at recursion depth {depth}"
                )
                left = fetch_batch(batch_ids[:midpoint], f"{batch_index}L", depth + 1)
                right = fetch_batch(batch_ids[midpoint:], f"{batch_index}R", depth + 1)
                return left + right
            returned_ids = {
                (
                    (feat.get("properties") or {}).get(object_id_field)
                    or (feat.get("properties") or {}).get(object_id_field.lower())
                    or feat.get("id")
                )
                for feat in features
            }
            requested_ids = set(batch_ids)
            if not returned_ids or not returned_ids.issubset(requested_ids):
                if len(batch_ids) == 1:
                    log.warning(
                        f"  {name}: objectId {batch_ids[0]} not respected by the service; "
                        "falling back to resultOffset pagination"
                    )
                    raise ValueError("objectIds not respected")
                midpoint = len(batch_ids) // 2
                log.warning(
                    f"  {name}: objectIds not respected on batch {batch_index}; "
                    f"splitting {len(batch_ids)} ids at recursion depth {depth}"
                )
                left = fetch_batch(batch_ids[:midpoint], f"{batch_index}L", depth + 1)
                right = fetch_batch(batch_ids[midpoint:], f"{batch_index}R", depth + 1)
                return left + right
            return features
        except Exception as e:
            if len(batch_ids) == 1:
                log.warning(f"  {name}: objectIds batch failed for {batch_ids[0]}: {e}")
                return []
            midpoint = len(batch_ids) // 2
            log.warning(
                f"  {name}: objectIds batch {batch_index} failed with {type(e).__name__}: {e}; "
                f"splitting {len(batch_ids)} ids at recursion depth {depth}"
            )
            left = fetch_batch(batch_ids[:midpoint], f"{batch_index}L", depth + 1)
            right = fetch_batch(batch_ids[midpoint:], f"{batch_index}R", depth + 1)
            return left + right
        finally:
            time.sleep(0.4)

    for i in range(0, len(object_ids), batch):
        batch_ids = object_ids[i:i + batch]
        features = fetch_batch(batch_ids, str(i // batch + 1))
        if not features:
            log.warning(f"  {name}: no features recovered for objectIds batch {i // batch + 1}")
            continue
        all_features.extend(features)
        log.info(f"  {name}: {len(all_features)} features via objectIds")

    return all_features


def paginated_arcgis_download(base_url, output_path, name, max_features=50000, batch=1000):
    """Download all features from an ArcGIS REST service with pagination."""
    all_features = []

    # Prefer objectId batching when possible: it works on services that don't
    # support resultOffset/resultRecordCount.
    objectid_features = download_arcgis_by_object_ids(base_url, name, batch=batch)
    if objectid_features:
        write_geojson(output_path, objectid_features)
        log.info(f"  {name}: SAVED {len(objectid_features)} features to {output_path}")
        return len(objectid_features)

    offset = 0
    current_batch = batch
    while offset < max_features:
        url = arcgis_query_url(
            base_url,
            where="1=1",
            outFields="*",
            f="geojson",
            resultOffset=offset,
            resultRecordCount=current_batch,
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            if r.status_code != 200:
                if current_batch > 25:
                    current_batch = max(25, current_batch // 2)
                    log.warning(
                        f"  {name}: HTTP {r.status_code} at offset {offset}, retrying with batch {current_batch}"
                    )
                    continue
                log.warning(f"  {name}: HTTP {r.status_code} at offset {offset}")
                break
            data = r.json()
            features = data.get("features", [])
            if not features:
                break
            all_features.extend(features)
            log.info(f"  {name}: {len(all_features)} features (offset {offset}, batch {current_batch})")
            offset += current_batch
            current_batch = batch
        except Exception as e:
            if current_batch > 25:
                current_batch = max(25, current_batch // 2)
                log.warning(
                    f"  {name}: Error at offset {offset}: {e}; retrying with batch {current_batch}"
                )
                continue
            log.warning(f"  {name}: Error at offset {offset}: {e}")
            break
        time.sleep(0.5)  # Rate limit

    if all_features:
        write_geojson(output_path, all_features)
        log.info(f"  {name}: SAVED {len(all_features)} features to {output_path}")
    else:
        log.warning(f"  {name}: NO FEATURES downloaded")
    return len(all_features)


def direct_download(url, output_path, name):
    """Download a file directly."""
    try:
        r = requests.get(url, headers=HEADERS, stream=True, timeout=300, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > 500:
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
            size_mb = os.path.getsize(output_path) / 1e6
            log.info(f"  {name}: SAVED {size_mb:.1f} MB to {output_path}")
            return True
        else:
            log.warning(f"  {name}: HTTP {r.status_code}, size {len(r.content)}")
            return False
    except Exception as e:
        log.warning(f"  {name}: Error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# ALL DATA SOURCES
# ═══════════════════════════════════════════════════════════════

SOURCES = {
    # === TIER 1: ArcGIS REST API (paginated query) ===
    "AL": {
        "type": "arcgis",
        "url": "https://conservationgis.alabama.gov/adcnrweb/rest/services/PFLBathymetry/MapServer/0/query",
        "name": "Alabama ADCNR Lake Contours",
        "expected": 3041,
        "batch": 500,
    },
    "AK": {
        "type": "arcgis",
        "url": "https://arcgis.adfg.alaska.gov/arcgis/rest/services/SpeciesAndHabitat/Bathymetry/MapServer/0/query",
        "name": "Alaska ADF&G Bathymetry",
        "expected": 998,
    },
    "NH": {
        "type": "arcgis",
        "url": "https://nhgeodata.unh.edu/nhgeodata/rest/services/EDP/Bathymetry_Lakes/MapServer/1/query",
        "name": "New Hampshire GRANIT Bathymetry Polygons",
        "expected": 7351,
        "batch": 250,
    },
    "NH_lines": {
        "type": "arcgis",
        "url": "https://nhgeodata.unh.edu/nhgeodata/rest/services/EDP/Bathymetry_Lakes/MapServer/0/query",
        "name": "New Hampshire GRANIT Bathymetry Lines",
        "expected": 9285,
        "batch": 250,
    },
    "IA": {
        "type": "arcgis",
        "url": "https://programs.iowadnr.gov/geospatial/rest/services/Recreation/Fishing/MapServer/2/query",
        "name": "Iowa DNR Lake Contours",
        "expected": 6489,
    },
    "IL": {
        "type": "arcgis",
        "url": "https://maps.dnr.illinois.gov/geoservices/rest/services/WaterResources/LakeDepthAndCapacity/MapServer/0/query",
        "name": "Illinois DNR Lake Depth Contours",
        "expected": 4828,
        "batch": 250,
    },
    "OH": {
        "type": "arcgis",
        "url": "https://gis2.ohiodnr.gov/ArcGIS/rest/services/DOW_Services/DOW_Lakes_Bathymetry/MapServer/0/query",
        "name": "Ohio DNR Lakes Bathymetry",
        "expected": 2809,
    },
    "CT": {
        "type": "arcgis",
        "url": "https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer/0/query",
        "name": "Connecticut DEEP Lake Contours",
        "expected": 7495,
    },
    "DE": {
        "type": "arcgis",
        "url": "https://enterprise.firstmaptest.delaware.gov/arcgis/rest/services/Hydrology/DE_Public_Ponds/MapServer/5/query",
        "name": "Delaware DNREC Public Ponds Bathymetry",
        "expected": 769,
        "batch": 250,
    },
    "FL": {
        "type": "arcgis",
        "url": "https://gis.myfwc.com/hosting/rest/services/Open_Data/Bathymetry_of_Select_Lakes_in_Florida/MapServer/3/query",
        "name": "Florida FWC Lake Bathymetry",
        "expected": 24311,
        "batch": 2000,
    },
    "IN": {
        "type": "arcgis",
        "url": "https://gisdata.in.gov/server/rest/services/Hosted/Lake_Bathymetry_RO/FeatureServer/0/query",
        "name": "Indiana DNR Lakes Bathymetry",
        "expected": 10260,
        "batch": 100,
    },
    "AB": {
        "type": "arcgis",
        "url": "https://services2.arcgis.com/jQV6VMr2Loovu7GU/arcgis/rest/services/Alberta_Lake_Bathymetry/FeatureServer/0/query",
        "name": "Alberta AGS Lake Bathymetry",
        "expected": 3375,
        "batch": 2000,
    },
    "ND": {
        "type": "arcgis",
        "url": "https://ndgishub.nd.gov/arcgis/rest/services/Applications/GNF_LakeContoursCached/MapServer/0/query",
        "name": "North Dakota Lake Contours",
        "expected": 4765,
        "batch": 20,
    },
    "SK": {
        "type": "arcgis",
        "url": "https://gis.saskatchewan.ca/arcgis/rest/services/Bathymetric/FeatureServer/0/query",
        "name": "Saskatchewan Bathymetry Index",
        "expected": 945,
    },
    "KS": {
        "type": "arcgis",
        "url": "https://itprdkarsap.home.ku.edu/arcgis/rest/services/WaterResources/BathymetryContour/MapServer/0/query",
        "name": "Kansas Bathymetry Contours",
        "expected": 7667,
        "batch": 500,
    },

    # === TIER 2: Direct downloads ===
    "ON_lines": {
        "type": "direct",
        "url": "https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Packages/fgdb/BATHYML.zip",
        "name": "Ontario OMNRF Bathymetry Lines (FGDB)",
        "ext": ".zip",
    },
    "ON_points": {
        "type": "direct",
        "url": "https://ws.gisetl.lrc.gov.on.ca/fmedatadownload/Packages/fgdb/BATHYMP.zip",
        "name": "Ontario OMNRF Bathymetry Points (FGDB)",
        "ext": ".zip",
    },
    "QC": {
        "type": "direct",
        "url": "https://stqc380donopppdtce01.blob.core.windows.net/donnees-ouvertes/Bathymetrie/Lacs/DQ/GBLQ_gdb.zip",
        "name": "Quebec GBLQ Lake Bathymetry (FGDB)",
        "ext": ".zip",
    },
    "MT": {
        "type": "direct",
        "url": "https://fwp-gis.mt.gov/arcgis/rest/directories/arcgisoutput/webResources/shapefiles/refrnc/lakesBathymetry.zip",
        "name": "Montana FWP Lakes Bathymetry",
        "ext": ".zip",
    },
    "WA": {
        "type": "direct",
        "url": "https://fortress.wa.gov/ecy/gispublic/DataDownload/ECY_ELV_LakeBathymetry.zip",
        "name": "Washington Ecology Lake Bathymetry",
        "ext": ".zip",
    },
    "MB": {
        "type": "direct",
        "url": "https://geoportal.gov.mb.ca/api/download/v1/items/593e253698a648e88789a661832b2ac0/geojson?layers=0",
        "name": "Manitoba Waterbody Data",
        "ext": ".geojson",
    },
    "MA": {
        "type": "direct",
        "url": "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/bathymetry.zip",
        "name": "Massachusetts Bathymetry",
        "ext": ".zip",
    },
    "VT": {
        "type": "direct",
        "url": "https://raw.githubusercontent.com/cboone/vermont-lakes-and-ponds-bathymetry/main/bathymetry/vt-bathymetry.geojson",
        "name": "Vermont Lake Bathymetry",
        "ext": ".geojson",
    },
    "ME": {
        "type": "direct",
        "url": "https://www.maine.gov/ifw/fishing/kml/Lake_Depths.kml",
        "name": "Maine Lake Depths KML",
        "ext": ".kml",
    },
    "NE": {
        "type": "direct",
        "url": "https://nebraskamap-ne.hub.arcgis.com/api/download/v1/items/ne::ngpc-lake-contours/shapefile",
        "name": "Nebraska NGPC Lake Contours",
        "ext": ".zip",
    },
    "TX_inventory": {
        "type": "direct",
        "url": "https://www.twdb.texas.gov/surfacewater/surveys/completed/doc/TWDB_SurveyDataRelease_Inventory.xlsx",
        "name": "Texas TWDB Survey Inventory",
        "ext": ".xlsx",
    },
}


def main():
    parser = argparse.ArgumentParser(description="Download all NA lake bathymetry data")
    parser.add_argument("--output", default=os.path.expanduser("~/Documents/fish/data/bathymetry"),
                        help="Output directory")
    parser.add_argument("--states", default=None, help="Comma-separated state codes (default: all)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip already downloaded files")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    states_to_download = args.states.split(",") if args.states else list(SOURCES.keys())

    log.info(f"Downloading {len(states_to_download)} sources to {out_dir}")
    log.info("=" * 70)

    results = {}
    for code in states_to_download:
        if code not in SOURCES:
            log.warning(f"Unknown source: {code}")
            continue

        src = SOURCES[code]
        state_dir = out_dir / code.lower()
        state_dir.mkdir(exist_ok=True)

        log.info(f"\n{'='*50}")
        log.info(f"[{code}] {src['name']}")
        log.info(f"{'='*50}")

        if src["type"] == "arcgis":
            output_path = state_dir / f"{code.lower()}_contours.geojson"
            if args.skip_existing and output_path.exists() and output_path.stat().st_size > 100:
                log.info(f"  Skipping (exists): {output_path}")
                results[code] = "skipped"
                continue
            batch = src.get("batch", 1000)
            n = paginated_arcgis_download(
                src["url"], str(output_path), code,
                max_features=src.get("expected", 50000) + 1000,
                batch=batch,
            )
            results[code] = f"{n} features" if n > 0 else "FAILED"

        elif src["type"] == "direct":
            ext = src.get("ext", ".zip")
            output_path = state_dir / f"{code.lower()}_data{ext}"
            if args.skip_existing and output_path.exists() and output_path.stat().st_size > 500:
                log.info(f"  Skipping (exists): {output_path}")
                results[code] = "skipped"
                continue
            ok = direct_download(src["url"], str(output_path), code)
            results[code] = "OK" if ok else "FAILED"

        time.sleep(1)  # Be polite between sources

    # Summary
    log.info("\n" + "=" * 70)
    log.info("DOWNLOAD SUMMARY")
    log.info("=" * 70)
    for code, status in sorted(results.items()):
        name = SOURCES[code]["name"]
        log.info(f"  {code:6s} {status:20s} {name}")

    succeeded = sum(1 for s in results.values() if s not in ("FAILED",))
    failed = sum(1 for s in results.values() if s == "FAILED")
    log.info(f"\nSucceeded: {succeeded}, Failed: {failed}, Total: {len(results)}")

    # Save results
    with open(out_dir / "download_results.json", "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"Results saved to {out_dir / 'download_results.json'}")


if __name__ == "__main__":
    main()
