#!/usr/bin/env python3
"""
Download and flatten the official Oklahoma OWRB lake inventory workbook.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen

URL = "https://oklahoma.gov/content/dam/ok/en/owrb/documents/maps-and-data/lakes-of-oklahoma-data.xlsx"
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/ok")
OUT_XLSX = OUT_DIR / "ok_lakes_inventory.xlsx"
OUT_CSV = OUT_DIR / "ok_lakes_inventory.csv"
OUT_GEOJSON = OUT_DIR / "ok_depth_points.geojson"
NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for si in root.findall("a:si", NS):
        values.append("".join(t.text or "" for t in si.iterfind(".//a:t", NS)))
    return values


def read_rows(zf: zipfile.ZipFile, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in root.findall(".//a:sheetData/a:row", NS):
        values: list[str] = []
        for cell in row.findall("a:c", NS):
            raw_type = cell.get("t")
            value_node = cell.find("a:v", NS)
            value = value_node.text if value_node is not None else ""
            if raw_type == "s" and value.isdigit():
                value = shared[int(value)]
            values.append(value)
        rows.append(values)
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = urlopen(URL, timeout=60).read()
    OUT_XLSX.write_bytes(raw)

    zf = zipfile.ZipFile(io.BytesIO(raw))
    rows = read_rows(zf, read_shared_strings(zf))

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)

    header = rows[0] if rows else []
    features = []
    for row in rows[1:]:
        if not row:
            continue
        values = {header[i]: row[i] if i < len(row) else "" for i in range(len(header))}
        lat_raw = values.get("LATITUDE", "").strip()
        lon_raw = values.get("LONGITUDE", "").strip()
        depth_raw = values.get("MAX DEPTH (ft)", "").strip()
        if not lat_raw or not lon_raw or not depth_raw or depth_raw.lower() == "no data":
            continue
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
            depth_ft = float(depth_raw)
        except ValueError:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "LAKE_NAME": values.get("LAKE NAME", "").strip(),
                    "AUTHORITY": values.get("AUTHORITY", "").strip(),
                    "PLANNING_REGION": values.get("PLANNING REGION", "").strip(),
                    "COUNTY": values.get("COUNTY", "").strip(),
                    "STREAM": values.get("STREAM", "").strip(),
                    "NORMAL_ELEVATION_FT": values.get("NORMAL ELEVATION (ft)", "").strip(),
                    "NORMAL_AREA_AC": values.get("NORMAL AREA (ac)", "").strip(),
                    "NORMAL_CAPACITY_ACFT": values.get("NORMAL CAPACITY (ac-ft) ", "").strip(),
                    "SHORELINE_MI": values.get("SHORELINE (mi)", "").strip(),
                    "MAX_DEPTH_FT": depth_ft,
                    "BENEFICIAL_USES": values.get("DESIGNATED BENEFICIAL USES*", "").strip(),
                },
            }
        )

    OUT_GEOJSON.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )

    print(f"Wrote {OUT_XLSX}")
    print(f"Wrote {OUT_CSV} ({max(len(rows) - 1, 0)} lake rows)")
    print(f"Wrote {OUT_GEOJSON} ({len(features)} depth-point features)")


if __name__ == "__main__":
    main()
