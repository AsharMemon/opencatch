#!/usr/bin/env python3
"""
OpenCatch — Access Points & Boat Launch Data Pipeline

Fetches boat launches, fishing access points, parking areas, and shore
fishing spots from multiple sources:

1. OpenStreetMap (Overpass API) — boat ramps, fishing spots, parking
2. US Fish & Wildlife Service — National Wildlife Refuge boat launches
3. State DNR data — via GIS servers (varies by state)

Outputs a unified GeoJSON of access points for each water body.

Usage:
    python fetch_access_points.py \
        --bbox -97,44,-89,48 \
        --output /data/access_points \
        --types boat_launch,parking,shore_access,campground

Requirements:
    pip install requests geopandas shapely tqdm
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# Overpass API endpoint
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Rate limiting for Overpass
OVERPASS_DELAY = 2  # seconds between requests


def query_overpass(query: str, timeout: int = 120) -> dict:
    """Execute an Overpass API query and return GeoJSON."""
    full_query = f"[out:json][timeout:{timeout}];\n{query}\nout body geom;"

    log.debug(f"Overpass query: {full_query[:200]}...")
    resp = requests.post(
        OVERPASS_URL,
        data={'data': full_query},
        timeout=timeout + 30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_boat_launches(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """
    Fetch boat launches / ramps from OpenStreetMap.

    Tags: leisure=slipway, leisure=marina, seamark:type=small_craft_facility
    """
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    bb = f"{south},{west},{north},{east}"

    query = f"""(
        node["leisure"="slipway"]({bb});
        way["leisure"="slipway"]({bb});
        node["leisure"="marina"]({bb});
        way["leisure"="marina"]({bb});
        node["seamark:type"="small_craft_facility"]({bb});
        node["waterway"="boat_ramp"]({bb});
        way["waterway"="boat_ramp"]({bb});
    );"""

    data = query_overpass(query)
    points = []

    for element in data.get('elements', []):
        tags = element.get('tags', {})

        # Get coordinates
        if element['type'] == 'node':
            lat, lon = element['lat'], element['lon']
        elif 'center' in element:
            lat, lon = element['center']['lat'], element['center']['lon']
        elif 'bounds' in element:
            b = element['bounds']
            lat = (b['minlat'] + b['maxlat']) / 2
            lon = (b['minlon'] + b['maxlon']) / 2
        else:
            continue

        name = tags.get('name', tags.get('description', 'Boat Launch'))
        fee = tags.get('fee', 'unknown')
        access_type = tags.get('access', 'yes')

        points.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'name': name,
                'access_type': 'boat_launch',
                'fee': fee != 'no',
                'public': access_type in ('yes', 'public', 'permissive'),
                'source': 'osm',
                'osm_id': element.get('id'),
                'tags': tags,
            },
        })

    log.info(f"Found {len(points)} boat launches")
    return points


def fetch_fishing_access(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """
    Fetch fishing access points / piers from OpenStreetMap.

    Tags: leisure=fishing, man_made=pier + fishing=yes
    """
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    bb = f"{south},{west},{north},{east}"

    query = f"""(
        node["leisure"="fishing"]({bb});
        way["leisure"="fishing"]({bb});
        node["man_made"="pier"]["fishing"="yes"]({bb});
        way["man_made"="pier"]["fishing"="yes"]({bb});
        node["sport"="fishing"]({bb});
        way["sport"="fishing"]({bb});
    );"""

    data = query_overpass(query)
    points = []

    for element in data.get('elements', []):
        tags = element.get('tags', {})

        if element['type'] == 'node':
            lat, lon = element['lat'], element['lon']
        elif 'center' in element:
            lat, lon = element['center']['lat'], element['center']['lon']
        elif 'bounds' in element:
            b = element['bounds']
            lat = (b['minlat'] + b['maxlat']) / 2
            lon = (b['minlon'] + b['maxlon']) / 2
        else:
            continue

        name = tags.get('name', 'Fishing Access')

        points.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'name': name,
                'access_type': 'shore_access',
                'fee': tags.get('fee', 'no') != 'no',
                'public': tags.get('access', 'yes') in ('yes', 'public', 'permissive'),
                'source': 'osm',
                'osm_id': element.get('id'),
                'pier': tags.get('man_made') == 'pier',
            },
        })

    log.info(f"Found {len(points)} fishing access points")
    return points


def fetch_parking_near_water(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """
    Fetch parking areas near water bodies from OpenStreetMap.
    Specifically targets parking that's within ~200m of water.
    """
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    bb = f"{south},{west},{north},{east}"

    # Parking near water features
    query = f"""(
        node["amenity"="parking"]["access"!="private"]({bb});
        way["amenity"="parking"]["access"!="private"]({bb});
    );"""

    data = query_overpass(query)
    points = []

    for element in data.get('elements', []):
        tags = element.get('tags', {})

        if element['type'] == 'node':
            lat, lon = element['lat'], element['lon']
        elif 'center' in element:
            lat, lon = element['center']['lat'], element['center']['lon']
        elif 'bounds' in element:
            b = element['bounds']
            lat = (b['minlat'] + b['maxlat']) / 2
            lon = (b['minlon'] + b['maxlon']) / 2
        else:
            continue

        name = tags.get('name', 'Parking')
        fee = tags.get('fee', 'unknown')
        capacity = tags.get('capacity', None)

        points.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'name': name,
                'access_type': 'parking',
                'fee': fee != 'no' and fee != 'unknown',
                'free': fee == 'no',
                'capacity': int(capacity) if capacity and capacity.isdigit() else None,
                'source': 'osm',
                'osm_id': element.get('id'),
            },
        })

    log.info(f"Found {len(points)} parking areas")
    return points


def fetch_campgrounds(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Fetch campgrounds near water from OpenStreetMap."""
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    bb = f"{south},{west},{north},{east}"

    query = f"""(
        node["tourism"="camp_site"]({bb});
        way["tourism"="camp_site"]({bb});
        node["tourism"="caravan_site"]({bb});
        way["tourism"="caravan_site"]({bb});
    );"""

    data = query_overpass(query)
    points = []

    for element in data.get('elements', []):
        tags = element.get('tags', {})

        if element['type'] == 'node':
            lat, lon = element['lat'], element['lon']
        elif 'center' in element:
            lat, lon = element['center']['lat'], element['center']['lon']
        elif 'bounds' in element:
            b = element['bounds']
            lat = (b['minlat'] + b['maxlat']) / 2
            lon = (b['minlon'] + b['maxlon']) / 2
        else:
            continue

        points.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'name': tags.get('name', 'Campground'),
                'access_type': 'campground',
                'fee': tags.get('fee', 'unknown') != 'no',
                'public': tags.get('access', 'yes') in ('yes', 'public', 'permissive'),
                'source': 'osm',
                'osm_id': element.get('id'),
            },
        })

    log.info(f"Found {len(points)} campgrounds")
    return points


def fetch_trails_near_water(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Fetch hiking/walking trails near water from OpenStreetMap."""
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    bb = f"{south},{west},{north},{east}"

    query = f"""(
        way["highway"="path"]["sac_scale"]({bb});
        way["highway"="footway"]["name"]({bb});
        way["highway"="track"]["name"]({bb});
        relation["route"="hiking"]({bb});
    );"""

    data = query_overpass(query)
    features = []

    for element in data.get('elements', []):
        tags = element.get('tags', {})
        name = tags.get('name')
        if not name:
            continue

        # Get geometry
        if 'geometry' in element:
            coords = [[p['lon'], p['lat']] for p in element['geometry']]
            geom = {'type': 'LineString', 'coordinates': coords}
        elif element['type'] == 'node':
            geom = {'type': 'Point', 'coordinates': [element['lon'], element['lat']]}
        else:
            continue

        features.append({
            'type': 'Feature',
            'geometry': geom,
            'properties': {
                'name': name,
                'access_type': 'trail',
                'difficulty': tags.get('sac_scale', 'unknown'),
                'surface': tags.get('surface', 'unknown'),
                'source': 'osm',
            },
        })

    log.info(f"Found {len(features)} trails")
    return features


def associate_with_waterbodies(
    access_points: list[dict],
    waterbodies_path: Optional[Path] = None,
    max_distance_deg: float = 0.005,  # ~500m
) -> list[dict]:
    """
    Associate access points with the nearest water body.
    Adds 'nearest_waterbody' property to each feature.
    """
    if waterbodies_path is None or not waterbodies_path.exists():
        return access_points

    try:
        import geopandas as gpd
        from shapely.geometry import Point

        wb = gpd.read_file(waterbodies_path)
        if wb.empty:
            return access_points

        for feat in access_points:
            coords = feat['geometry']['coordinates']
            if feat['geometry']['type'] == 'Point':
                pt = Point(coords[0], coords[1])
            else:
                # For linestrings, use midpoint
                mid = len(coords) // 2
                pt = Point(coords[mid][0], coords[mid][1])

            # Find nearest waterbody
            distances = wb.geometry.distance(pt)
            nearest_idx = distances.idxmin()
            nearest_dist = distances[nearest_idx]

            if nearest_dist <= max_distance_deg:
                wb_row = wb.iloc[nearest_idx]
                feat['properties']['nearest_waterbody'] = wb_row.get('GNIS_Name', 'Unknown')
                feat['properties']['waterbody_area_sqkm'] = wb_row.get('AreaSqKm', None)
                feat['properties']['distance_to_water_m'] = round(nearest_dist * 111000, 0)

    except ImportError:
        log.warning("geopandas not available — skipping waterbody association")
    except Exception as e:
        log.warning(f"Waterbody association failed: {e}")

    return access_points


def fetch_all(
    bbox: tuple[float, float, float, float],
    output_dir: Path,
    types: list[str],
    waterbodies_path: Optional[Path] = None,
):
    """Fetch all requested access point types and save."""
    output_dir.mkdir(parents=True, exist_ok=True)

    all_features = []

    type_fetchers = {
        'boat_launch': fetch_boat_launches,
        'shore_access': fetch_fishing_access,
        'parking': fetch_parking_near_water,
        'campground': fetch_campgrounds,
        'trail': fetch_trails_near_water,
    }

    for access_type in types:
        if access_type not in type_fetchers:
            log.warning(f"Unknown type: {access_type}")
            continue

        log.info(f"Fetching {access_type}...")
        try:
            features = type_fetchers[access_type](bbox)
            all_features.extend(features)

            # Save per-type file
            type_geojson = {
                'type': 'FeatureCollection',
                'features': features,
            }
            with open(output_dir / f'{access_type}.geojson', 'w') as f:
                json.dump(type_geojson, f, indent=2)

        except Exception as e:
            log.error(f"Failed to fetch {access_type}: {e}")

        time.sleep(OVERPASS_DELAY)

    # Associate with waterbodies
    if waterbodies_path:
        log.info("Associating access points with waterbodies...")
        all_features = associate_with_waterbodies(all_features, waterbodies_path)

    # Save merged file
    merged = {
        'type': 'FeatureCollection',
        'features': all_features,
    }
    merged_path = output_dir / 'all_access_points.geojson'
    with open(merged_path, 'w') as f:
        json.dump(merged, f, indent=2)

    log.info(f"\nTotal: {len(all_features)} access points → {merged_path}")

    # Print summary
    from collections import Counter
    type_counts = Counter(f['properties'].get('access_type') for f in all_features)
    for t, c in type_counts.most_common():
        log.info(f"  {t}: {c}")


def main():
    parser = argparse.ArgumentParser(description='Fetch access points and boat launches')
    parser.add_argument('--bbox', type=str, required=True,
                       help='Bounding box: min_lon,min_lat,max_lon,max_lat')
    parser.add_argument('--output', type=str, default='/data/access_points',
                       help='Output directory')
    parser.add_argument('--types', type=str, default='boat_launch,shore_access,parking,campground',
                       help='Comma-separated access types')
    parser.add_argument('--waterbodies', type=str, default=None,
                       help='GeoJSON of water bodies for association')
    args = parser.parse_args()

    bbox = tuple(float(x) for x in args.bbox.split(','))
    types = [t.strip() for t in args.types.split(',')]

    wb_path = Path(args.waterbodies) if args.waterbodies else None

    fetch_all(bbox, Path(args.output), types, wb_path)


if __name__ == '__main__':
    main()
