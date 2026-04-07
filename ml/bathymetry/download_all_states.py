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


def paginated_arcgis_download(base_url, output_path, name, max_features=50000, batch=1000):
    """Download all features from an ArcGIS REST service with pagination."""
    all_features = []
    for offset in range(0, max_features, batch):
        url = (
            f"{base_url}?where=1%3D1&outFields=*&f=geojson"
            f"&resultOffset={offset}&resultRecordCount={batch}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            if r.status_code != 200:
                log.warning(f"  {name}: HTTP {r.status_code} at offset {offset}")
                break
            data = r.json()
            features = data.get("features", [])
            if not features:
                break
            all_features.extend(features)
            log.info(f"  {name}: {len(all_features)} features (offset {offset})")
        except Exception as e:
            log.warning(f"  {name}: Error at offset {offset}: {e}")
            break
        time.sleep(0.5)  # Rate limit

    if all_features:
        with open(output_path, "w") as f:
            json.dump({"type": "FeatureCollection", "features": all_features}, f)
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
    },
    "NH_lines": {
        "type": "arcgis",
        "url": "https://nhgeodata.unh.edu/nhgeodata/rest/services/EDP/Bathymetry_Lakes/MapServer/0/query",
        "name": "New Hampshire GRANIT Bathymetry Lines",
        "expected": 9285,
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
    },
    "OH": {
        "type": "arcgis",
        "url": "https://gis.ohiodnr.gov/arcgis/rest/services/OhioIT_ODNR/ODNR_DOW_Lakes_Bathymetry/MapServer/0/query",
        "name": "Ohio DNR Lakes Bathymetry",
        "expected": 2809,
    },
    "CT": {
        "type": "arcgis",
        "url": "https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer/0/query",
        "name": "Connecticut DEEP Lake Contours",
        "expected": 7495,
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
        "url": "https://maps.indiana.edu/arcgis/rest/services/Hydrology/Water_Bodies_Lakes_Bathymetry/MapServer/0/query",
        "name": "Indiana DNR Lakes Bathymetry",
        "expected": 5000,
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
        "batch": 2000,
    },
    "SK": {
        "type": "arcgis",
        "url": "https://gis.saskatchewan.ca/arcgis/rest/services/Bathymetric/FeatureServer/0/query",
        "name": "Saskatchewan Bathymetry Index",
        "expected": 945,
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
