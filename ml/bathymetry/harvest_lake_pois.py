#!/usr/bin/env python3
"""
Resumable lake-centric POI harvester for OpenCatch.

Reads the generated GPS lake catalog and queries OSM Overpass around each lake
bounding box to collect:

- marinas / harbors
- bait & tackle shops
- boat rentals
- ramps / slipways / launches
- shore-fishing access / piers
- parking
- trailheads
- picnic sites
- fish-cleaning stations

The output is JSONL so the run can checkpoint after each lake and resume safely.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests


OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def looks_marine_related(text: str) -> bool:
    normalized = (text or "").lower()
    return any(
        token in normalized
        for token in (
            "marina",
            "yacht",
            "harbour",
            "harbor",
            "boat",
            "dock",
            "landing",
            "marine",
            "wharf",
        )
    )


def is_marine_fuel(tags: dict[str, str]) -> bool:
    if tags.get("seamark:type") == "fuel_station":
        return True
    if tags.get("amenity") != "fuel":
        return False
    if tags.get("boat") == "yes":
        return True
    if tags.get("harbour") == "yes":
        return True
    if tags.get("waterway") in {"dock", "boatyard", "fuel"}:
        return True
    if looks_marine_related(tags.get("name", "")) or looks_marine_related(tags.get("operator", "")):
        return True
    return False


def parse_fuel_types(tags: dict[str, str]) -> list[str]:
    fuel_types: list[str] = []
    if tags.get("fuel:diesel") == "yes":
        fuel_types.append("diesel")
    if (
        tags.get("fuel:gasoline") == "yes"
        or tags.get("fuel:octane_87") == "yes"
        or tags.get("fuel:octane_89") == "yes"
        or tags.get("fuel:octane_91") == "yes"
        or tags.get("fuel:octane_93") == "yes"
    ):
        fuel_types.append("gasoline")
    if tags.get("fuel:e85") == "yes":
        fuel_types.append("E85")
    if tags.get("fuel:lpg") == "yes":
        fuel_types.append("LPG")
    if not fuel_types and tags.get("amenity") == "fuel":
        fuel_types.append("fuel")
    return fuel_types


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--state-filter", default="", help="Comma-separated jurisdictions to include.")
    parser.add_argument("--country-filter", default="", help="Comma-separated countries to include.")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="0 = all remaining.")
    parser.add_argument("--delay-seconds", type=float, default=0.35)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def build_query(entry: dict[str, Any]) -> str:
    bbox = entry["bbox"]
    center_lat = entry["lat"]
    lat_pad = 0.008
    lon_pad = max(0.008, 0.008 / max(math.cos(math.radians(center_lat)), 0.2))
    south = bbox["south"] - lat_pad
    north = bbox["north"] + lat_pad
    west = bbox["west"] - lon_pad
    east = bbox["east"] + lon_pad
    area = f"({south},{west},{north},{east})"
    return f"""
[out:json][timeout:35];
(
  node["leisure"="marina"]{area};
  way["leisure"="marina"]{area};
  node["seamark:type"="harbour"]{area};
  way["seamark:type"="harbour"]{area};
  node["harbour"="yes"]{area};
  way["harbour"="yes"]{area};
  node["amenity"="fuel"]["boat"="yes"]{area};
  way["amenity"="fuel"]["boat"="yes"]{area};
  node["amenity"="fuel"]{area};
  way["amenity"="fuel"]{area};
  node["seamark:type"="fuel_station"]{area};
  way["seamark:type"="fuel_station"]{area};
  node["amenity"="fuel"]["harbour"="yes"]{area};
  way["amenity"="fuel"]["harbour"="yes"]{area};
  node["amenity"="fuel"]["waterway"]{area};
  way["amenity"="fuel"]["waterway"]{area};
  node["shop"="fishing"]{area};
  node["shop"="bait"]{area};
  node["shop"="tackle"]{area};
  node["shop"="boat"]{area};
  node["amenity"="boat_rental"]{area};
  node["amenity"="boat_sharing"]{area};
  node["leisure"="slipway"]{area};
  way["leisure"="slipway"]{area};
  node["seamark:type"="slipway"]{area};
  node["waterway"="boat_ramp"]{area};
  way["waterway"="boat_ramp"]{area};
  node["waterway"="dock"]{area};
  way["waterway"="dock"]{area};
  node["waterway"="canoe_put_in"]{area};
  node["leisure"="fishing"]{area};
  way["leisure"="fishing"]{area};
  node["sport"="fishing"]["access"!="private"]{area};
  node["man_made"="pier"]{area};
  way["man_made"="pier"]{area};
  node["amenity"="parking"]["access"!="private"]["access"!="customers"]{area};
  way["amenity"="parking"]["access"!="private"]["access"!="customers"]{area};
  node["highway"="trailhead"]{area};
  node["information"="guidepost"]["hiking"="yes"]{area};
  node["tourism"="picnic_site"]{area};
  way["tourism"="picnic_site"]{area};
  node["amenity"="fish_cleaning"]{area};
  node["man_made"="fish_cleaning_table"]{area};
);
out center body;
""".strip()


def classify(tags: dict[str, str]) -> str:
    if is_marine_fuel(tags):
        return "marine_fuel"
    if tags.get("amenity") == "fuel":
        return "gas_station"
    if tags.get("leisure") == "marina" or tags.get("seamark:type") == "harbour" or tags.get("harbour") == "yes":
        return "marina"
    if tags.get("shop") in {"bait"}:
        return "bait_shop"
    if tags.get("shop") in {"fishing", "tackle"}:
        return "tackle_shop"
    if tags.get("shop") == "boat" or tags.get("amenity") in {"boat_rental", "boat_sharing"}:
        return "boat_rental"
    if tags.get("leisure") == "slipway" or tags.get("seamark:type") == "slipway" or tags.get("waterway") in {"boat_ramp", "dock"}:
        return "boat_ramp"
    if tags.get("waterway") == "canoe_put_in":
        return "kayak_launch"
    if tags.get("amenity") == "parking":
        return "parking"
    if tags.get("highway") == "trailhead" or (tags.get("information") == "guidepost" and tags.get("hiking") == "yes"):
        return "trailhead"
    if tags.get("amenity") == "fish_cleaning" or tags.get("man_made") == "fish_cleaning_table":
        return "fish_cleaning"
    if tags.get("tourism") == "picnic_site":
        return "picnic_site"
    if tags.get("man_made") == "pier":
        return "fishing_pier"
    return "shore_fishing"


def build_address(tags: dict[str, str]) -> str:
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:city"),
        tags.get("addr:state"),
        tags.get("addr:postcode"),
    ]
    return " ".join(part for part in parts if part)


def normalize_element(el: dict[str, Any], lake: dict[str, Any]) -> dict[str, Any] | None:
    tags = el.get("tags") or {}
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None
    return {
        "lake_id": lake["id"],
        "lake_name": lake["name"],
        "jurisdiction": lake["jurisdiction"],
        "country": lake["country"],
        "osm_id": f"{el['type']}/{el['id']}",
        "name": tags.get("name") or tags.get("name:en") or classify(tags),
        "lat": lat,
        "lon": lon,
        "poi_type": classify(tags),
        "fuel_types": parse_fuel_types(tags),
        "amenities": sorted(
            [
                label
                for label, cond in [
                    ("fuel", tags.get("fuel") == "yes" or tags.get("amenity") == "fuel"),
                    ("diesel", tags.get("fuel:diesel") == "yes"),
                    ("gasoline", tags.get("fuel:gasoline") == "yes" or tags.get("fuel:octane_87") == "yes" or tags.get("fuel:octane_89") == "yes" or tags.get("fuel:octane_91") == "yes" or tags.get("fuel:octane_93") == "yes"),
                    ("parking", tags.get("parking") in {"yes", "surface"}),
                    ("boat_ramp", tags.get("slipway") == "yes" or tags.get("leisure") == "slipway"),
                    ("restrooms", tags.get("toilets") == "yes"),
                    ("drinking_water", tags.get("drinking_water") == "yes"),
                    ("repair", tags.get("repair") == "yes"),
                    ("wifi", tags.get("wifi") == "yes" or tags.get("internet_access") == "yes"),
                ]
                if cond
            ]
        ),
        "phone": tags.get("phone") or tags.get("contact:phone") or "",
        "website": tags.get("website") or tags.get("contact:website") or "",
        "address": build_address(tags),
        "operator": tags.get("operator") or "",
        "source": "osm_overpass",
    }


def run_query(session: requests.Session, query: str, timeout: int) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for endpoint in OVERPASS_URLS:
        try:
            response = session.post(
                endpoint,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=f"data={requests.utils.quote(query, safe='')}",
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json().get("elements", [])
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise last_error or RuntimeError("Overpass query failed")


def load_catalog(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_processed(path: Path) -> set[str]:
    if not path.exists():
        return set()
    processed: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            processed.add(row.get("lake_id", ""))
    return processed


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_summary(
    args: argparse.Namespace,
    lake_count: int,
    poi_count: int,
    error_count: int,
    type_counts: Counter[str],
) -> dict[str, Any]:
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "catalog": str(args.catalog),
        "output_jsonl": str(args.output_jsonl),
        "lake_count_processed": lake_count,
        "poi_count": poi_count,
        "error_count": error_count,
        "poi_type_counts": dict(type_counts),
        "filters": {
            "state_filter": args.state_filter,
            "country_filter": args.country_filter,
            "start_index": args.start_index,
            "limit": args.limit,
        },
    }


def main() -> None:
    args = parse_args()
    lakes = load_catalog(args.catalog)
    if args.country_filter:
        keep = {part.strip() for part in args.country_filter.split(",") if part.strip()}
        lakes = [lake for lake in lakes if lake["country"] in keep]
    if args.state_filter:
        keep = {part.strip() for part in args.state_filter.split(",") if part.strip()}
        lakes = [lake for lake in lakes if lake["jurisdiction"] in keep]
    if args.start_index:
        lakes = lakes[args.start_index :]
    if args.limit:
        lakes = lakes[: args.limit]

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    if args.overwrite and args.output_jsonl.exists():
        args.output_jsonl.unlink()
    processed = load_processed(args.output_jsonl)

    session = requests.Session()
    type_counts: Counter[str] = Counter()
    lake_count = 0
    poi_count = 0
    error_count = 0

    with args.output_jsonl.open("a", encoding="utf-8") as out:
        for index, lake in enumerate(lakes, start=1):
            if lake["id"] in processed:
                continue
            try:
                elements = run_query(session, build_query(lake), args.timeout)
                rows = [normalize_element(el, lake) for el in elements]
                rows = [row for row in rows if row is not None]
                seen = set()
                deduped = []
                for row in rows:
                    key = row["osm_id"]
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(row)
                for row in deduped:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    type_counts[row["poi_type"]] += 1
                poi_count += len(deduped)
                lake_count += 1
                processed.add(lake["id"])
                out.flush()
                print(f"[{index}/{len(lakes)}] {lake['name']}: {len(deduped)} POIs", flush=True)
            except Exception as exc:  # noqa: BLE001
                error_count += 1
                print(f"[error] {lake['name']}: {exc}", flush=True)
            write_summary(
                args.summary_json,
                build_summary(args, lake_count, poi_count, error_count, type_counts),
            )
            time.sleep(args.delay_seconds)

    summary = build_summary(args, lake_count, poi_count, error_count, type_counts)
    write_summary(args.summary_json, summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
