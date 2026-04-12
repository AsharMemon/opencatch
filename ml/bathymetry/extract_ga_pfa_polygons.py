#!/usr/bin/env python3
"""
Extract Georgia Public Fishing Area polygons from the official Georgia Outdoor Map KMZ.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


NS = {
    "kml": "http://www.opengis.net/kml/2.2",
}


def parse_coordinates(text: str) -> list[list[float]]:
    coords: list[list[float]] = []
    for token in text.replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        coords.append([lon, lat])
    return coords


def placemark_to_feature(placemark: ET.Element) -> dict | None:
    name_el = placemark.find("kml:name", NS)
    name = (name_el.text or "").strip() if name_el is not None and name_el.text else None

    polygons = []
    for poly in placemark.findall(".//kml:Polygon", NS):
        outer = poly.find(".//kml:outerBoundaryIs/kml:LinearRing/kml:coordinates", NS)
        if outer is None or not outer.text:
            continue
        ring = parse_coordinates(outer.text)
        if len(ring) < 4:
            continue
        holes = []
        for inner in poly.findall(".//kml:innerBoundaryIs/kml:LinearRing/kml:coordinates", NS):
            if inner.text:
                hole = parse_coordinates(inner.text)
                if len(hole) >= 4:
                    holes.append(hole)
        polygons.append([ring, *holes])

    if not polygons:
        return None

    geometry = {
        "type": "Polygon" if len(polygons) == 1 else "MultiPolygon",
        "coordinates": polygons[0] if len(polygons) == 1 else polygons,
    }
    properties = {
        "name": name,
        "source": "ga_outdoor_map_kmz",
    }
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": properties,
    }


def extract_features(kmz_path: Path) -> list[dict]:
    with zipfile.ZipFile(kmz_path) as zf:
        kml_names = [name for name in zf.namelist() if name.lower().endswith(".kml")]
        if not kml_names:
            raise FileNotFoundError("No KML file found inside KMZ archive")
        with zf.open(kml_names[0]) as fh:
            root = ET.parse(fh).getroot()

    features = []
    for placemark in root.findall(".//kml:Placemark", NS):
        feature = placemark_to_feature(placemark)
        if feature is not None:
            features.append(feature)
    return features


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kmz",
        default="/Users/Ashar/Documents/fish/data/bathymetry/ga/15pfa_fh2.kmz",
    )
    parser.add_argument(
        "--output",
        default="/Users/Ashar/Documents/fish/data/bathymetry/ga/ga_pfa_polygons.geojson",
    )
    args = parser.parse_args()

    features = extract_features(Path(args.kmz))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    print(json.dumps({"output": str(output), "feature_count": len(features)}, indent=2))


if __name__ == "__main__":
    main()
