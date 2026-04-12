#!/usr/bin/env python3
"""
Fetch and validate official USGS hydrography service metadata for
continent-scale river-network completeness.

This does not download the full national datasets. It captures the canonical
service metadata and a tiny sample-query manifest so the rest of the pipeline
can treat USGS hydrography as a first-class official source alongside USACE.

Sources:
  - NHDPlus_HR MapServer
  - 3DHP_all FeatureServer

Usage:
  python fetch_usgs_hydrography.py \
    --output /Users/Ashar/Documents/fish/data/bathymetry/usgs_hydrography
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests


USER_AGENT = "OpenCatch/1.0 (contact@opencatch.app)"
TIMEOUT_S = 30

NHDPLUS_HR = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer"
THREEDHP_ALL = "https://hydro.nationalmap.gov/arcgis/rest/services/3DHP_all/FeatureServer"


def fetch_json(url: str) -> Any:
    resp = requests.get(url, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch official USGS hydrography service metadata.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    nhd_service = fetch_json(f"{NHDPLUS_HR}?f=pjson")
    nhd_layers = fetch_json(f"{NHDPLUS_HR}/layers?f=pjson")
    nhd_flowline = fetch_json(f"{NHDPLUS_HR}/3?f=pjson")

    hp_service = fetch_json(f"{THREEDHP_ALL}?f=pjson")
    hp_flowline = fetch_json(f"{THREEDHP_ALL}/50?f=pjson")

    sample_queries = {
        "nhdplus_hr_flowline_geojson": (
            f"{NHDPLUS_HR}/3/query?where=1%3D1&outFields=GNIS_NAME%2CCOMID&"
            "returnGeometry=true&resultRecordCount=1&f=geojson"
        ),
        "3dhp_flowline_json": (
            f"{THREEDHP_ALL}/50/query?where=1%3D1&outFields=gnis_name%2Cpermanent_identifier&"
            "returnGeometry=true&resultRecordCount=1&f=json"
        ),
    }

    sample_results = {}
    for name, url in sample_queries.items():
        try:
            sample_results[name] = fetch_json(url)
        except Exception as exc:  # pragma: no cover - network variability
            sample_results[name] = {"error": str(exc), "url": url}

    files = {
        "nhdplus_hr_service": output_dir / "nhdplus_hr_service.json",
        "nhdplus_hr_layers": output_dir / "nhdplus_hr_layers.json",
        "nhdplus_hr_flowline": output_dir / "nhdplus_hr_flowline_layer.json",
        "3dhp_service": output_dir / "3dhp_service.json",
        "3dhp_flowline": output_dir / "3dhp_flowline_layer.json",
        "sample_queries": output_dir / "sample_queries.json",
    }

    files["nhdplus_hr_service"].write_text(json.dumps(nhd_service, indent=2))
    files["nhdplus_hr_layers"].write_text(json.dumps(nhd_layers, indent=2))
    files["nhdplus_hr_flowline"].write_text(json.dumps(nhd_flowline, indent=2))
    files["3dhp_service"].write_text(json.dumps(hp_service, indent=2))
    files["3dhp_flowline"].write_text(json.dumps(hp_flowline, indent=2))
    files["sample_queries"].write_text(json.dumps(sample_results, indent=2))

    manifest = {
        "sources": {
            "nhdplus_hr": {
                "service_url": NHDPLUS_HR,
                "flowline_layer_id": 3,
                "flowline_layer_name": nhd_flowline.get("name"),
                "current_version": nhd_service.get("currentVersion"),
                "spatial_reference": nhd_service.get("spatialReference"),
                "supported_query_formats": nhd_service.get("supportedQueryFormats"),
                "wms_url": f"{NHDPLUS_HR}/WMSServer",
            },
            "3dhp_all": {
                "service_url": THREEDHP_ALL,
                "flowline_layer_id": 50,
                "flowline_layer_name": hp_flowline.get("name"),
                "current_version": hp_service.get("currentVersion"),
                "spatial_reference": hp_service.get("spatialReference"),
                "supported_query_formats": hp_service.get("supportedQueryFormats"),
            },
        },
        "sample_query_urls": sample_queries,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"output": str(output_dir), "manifest": str(output_dir / "manifest.json")}, indent=2))


if __name__ == "__main__":
    main()
