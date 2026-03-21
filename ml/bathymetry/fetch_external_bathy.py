#!/usr/bin/env python3
"""Download external bathymetry training data from state DNR services."""

import json
import os
import requests

OUTPUT_BASE = "/data/training/external"


def download_wi_dnr():
    """Download WI DNR bathymetric contours from ArcGIS REST."""
    out_dir = os.path.join(OUTPUT_BASE, "wi_dnr")
    os.makedirs(out_dir, exist_ok=True)

    url = "https://services2.arcgis.com/C8EMgrsFcRFL6LrL/arcgis/rest/services/Bathymetric_Contours/FeatureServer/0/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "outSR": 4326,
        "f": "geojson",
        "resultRecordCount": 5000,
    }
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    data = r.json()
    n = len(data.get("features", []))

    out_path = os.path.join(out_dir, "bathymetric_contours.geojson")
    with open(out_path, "w") as f:
        json.dump(data, f)
    print(f"WI DNR: {n} bathymetric contour features -> {out_path}")


def download_mn_dnr_lakefinder():
    """Download MN DNR LakeFinder bathymetry index."""
    out_dir = os.path.join(OUTPUT_BASE, "mn_dnr")
    os.makedirs(out_dir, exist_ok=True)

    # MN DNR Lake Bathymetric Outlines (contour index)
    url = "https://services1.arcgis.com/YGktaMq3RO2gTxGZ/arcgis/rest/services/Lake_Bathymetric_Outlines/FeatureServer/0/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "outSR": 4326,
        "f": "geojson",
        "resultRecordCount": 5000,
        "resultOffset": 0,
    }

    all_features = []
    while True:
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
        data = r.json()
        features = data.get("features", [])
        if not features:
            break
        all_features.extend(features)
        print(f"  MN DNR: fetched {len(all_features)} features so far...")
        if len(features) < params["resultRecordCount"]:
            break
        params["resultOffset"] += len(features)

    result = {"type": "FeatureCollection", "features": all_features}
    out_path = os.path.join(out_dir, "lake_bathymetric_outlines.geojson")
    with open(out_path, "w") as f:
        json.dump(result, f)
    print(f"MN DNR: {len(all_features)} lake bathymetric outline features -> {out_path}")


def download_globathy_index():
    """Download GLOBathy global lake bathymetry dataset index (CSV)."""
    out_dir = os.path.join(OUTPUT_BASE, "globathy")
    os.makedirs(out_dir, exist_ok=True)

    # GLOBathy is on figshare
    url = "https://figshare.com/ndownloader/files/35085929"
    out_path = os.path.join(out_dir, "GLOBathy_basic_statistics.csv")

    print("Downloading GLOBathy basic statistics CSV...")
    r = requests.get(url, timeout=300, stream=True)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"GLOBathy: {size_mb:.1f} MB -> {out_path}")


if __name__ == "__main__":
    print("=== Downloading external bathymetry training data ===")
    download_wi_dnr()
    download_mn_dnr_lakefinder()
    download_globathy_index()
    print("=== Done ===")
