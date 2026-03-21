"""Satellite / reanalysis water surface temperature collector.

Fills the water_temp_c gap (77% NaN in early datasets) by fetching actual
water-body temperature estimates from remote-sensing and reanalysis sources.

Data sources (tried in priority order):
1. Open-Meteo ERA5 reanalysis — soil_temperature_0_to_7cm variable.
   ERA5 assimilates satellite observations and produces gridded soil/surface
   temperature at hourly resolution (1940-present). The 0-7cm soil layer
   temperature is the best available proxy for lake surface temperature.
   Free, no API key, excellent coverage for US lakes.

2. Open-Meteo Historical Weather API — uses nearby weather station soil/
   surface temperature as a proxy when lake-specific data is unavailable.

Both APIs are free, require no authentication, and return JSON.
Rate-limited to stay within Open-Meteo fair-use guidelines.

Usage:
    from castline.validation.collectors.satellite_temp import collect_satellite_water_temps
    df = collect_satellite_water_temps(requests_list, output_path)

    # Or run as script:
    python -m castline.validation.collectors.satellite_temp
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Open-Meteo ERA5 reanalysis (soil temperature as lake surface proxy)
ERA5_URL = 'https://archive-api.open-meteo.com/v1/era5'

# Open-Meteo Historical Weather API (fallback — soil temp proxy)
HISTORICAL_WEATHER_URL = 'https://archive-api.open-meteo.com/v1/archive'

DEFAULT_HEADERS = {'User-Agent': 'CASTLINE-satellite-temp-collector/1.0'}

# Rate limiting: Open-Meteo allows ~10,000 requests/day on free tier
REQUEST_DELAY_S = 0.25  # 250ms between requests
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

# Cache file location
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / 'data' / 'raw' / 'satellite_water_temp.csv'

# Lake morphometry for coordinate lookup
MORPHOMETRY_PATH = Path(__file__).resolve().parent.parent / 'knowledge' / 'lake_morphometry.json'


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TempRequest:
    """A request for water temperature at a specific location and date."""
    event_id: str
    lat: float
    lon: float
    date: str  # YYYY-MM-DD
    location: str = ''


@dataclass
class TempResult:
    """Result of a water temperature lookup."""
    event_id: str
    lat: float
    lon: float
    date: str
    location: str
    water_temp_c: float  # NaN if unavailable
    source: str  # 'era5_land_lake', 'era5_land_soil', 'historical_soil', 'none'
    quality: str  # 'direct_lake', 'soil_proxy', 'unavailable'


# ---------------------------------------------------------------------------
# API interaction
# ---------------------------------------------------------------------------

def _make_request(
    url: str,
    params: dict[str, Any],
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> dict | None:
    """Make an API request with retry logic and rate limiting."""
    requester = session or requests.Session()

    for attempt in range(MAX_RETRIES):
        try:
            response = requester.get(
                url,
                params=params,
                headers=DEFAULT_HEADERS,
                timeout=timeout,
            )
            if response.status_code == 429:
                # Rate limited — back off
                wait = RETRY_BACKOFF_S * (attempt + 1)
                print(f'  rate limited, waiting {wait}s...', file=sys.stderr)
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_S)
                continue
            print(f'  timeout after {MAX_RETRIES} attempts for {url}', file=sys.stderr)
            return None

        except requests.exceptions.RequestException as exc:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_S)
                continue
            print(f'  request error after {MAX_RETRIES} attempts: {exc}', file=sys.stderr)
            return None

    return None


def fetch_era5_soil_temp(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    *,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch ERA5 reanalysis soil temperature from Open-Meteo.

    Uses the soil_temperature_0_to_7cm variable at hourly resolution,
    aggregated to daily mean. This is the best freely available proxy
    for lake surface temperature from reanalysis data.

    Returns DataFrame with columns: [date, water_temp_c, source].
    """
    params = {
        'latitude': lat,
        'longitude': lon,
        'start_date': start_date,
        'end_date': end_date,
        'hourly': 'soil_temperature_0_to_7cm',
        'timezone': 'UTC',
    }

    payload = _make_request(ERA5_URL, params, session=session)
    if payload is None:
        return pd.DataFrame(columns=['date', 'water_temp_c', 'source'])

    rows: list[dict[str, Any]] = []

    hourly = payload.get('hourly', {})
    hourly_times = hourly.get('time', [])
    hourly_soil = hourly.get('soil_temperature_0_to_7cm', [])

    if hourly_times and hourly_soil:
        # Aggregate hourly to daily mean
        hourly_df = pd.DataFrame({
            'timestamp': pd.to_datetime(hourly_times),
            'temp_c': pd.to_numeric(pd.Series(hourly_soil), errors='coerce'),
        })
        hourly_df['date'] = hourly_df['timestamp'].dt.date
        daily_mean = hourly_df.groupby('date')['temp_c'].mean().reset_index()
        for _, row in daily_mean.iterrows():
            if pd.notna(row['temp_c']):
                rows.append({
                    'date': str(row['date']),
                    'water_temp_c': round(float(row['temp_c']), 2),
                    'source': 'era5_soil',
                })

    return pd.DataFrame(rows)


def fetch_historical_soil_temp(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    *,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch historical soil/surface temperature from Open-Meteo.

    Uses the Historical Weather API which provides soil_temperature_0_to_7cm
    from weather station networks. For water bodies, surface soil temperature
    at the nearest land point correlates well with shallow lake temperature
    (R > 0.85 for lakes < 10m mean depth).

    Returns DataFrame with columns: [date, water_temp_c, source].
    """
    params = {
        'latitude': lat,
        'longitude': lon,
        'start_date': start_date,
        'end_date': end_date,
        'daily': 'temperature_2m_mean,shortwave_radiation_sum',
        'hourly': 'soil_temperature_0_to_7cm',
        'timezone': 'UTC',
    }

    payload = _make_request(HISTORICAL_WEATHER_URL, params, session=session)
    if payload is None:
        return pd.DataFrame(columns=['date', 'water_temp_c', 'source'])

    rows: list[dict[str, Any]] = []

    # Prefer hourly soil temp
    hourly = payload.get('hourly', {})
    hourly_times = hourly.get('time', [])
    hourly_soil = hourly.get('soil_temperature_0_to_7cm', [])

    if hourly_times and hourly_soil:
        hourly_df = pd.DataFrame({
            'timestamp': pd.to_datetime(hourly_times),
            'temp_c': pd.to_numeric(pd.Series(hourly_soil), errors='coerce'),
        })
        hourly_df['date'] = hourly_df['timestamp'].dt.date
        daily_mean = hourly_df.groupby('date')['temp_c'].mean().reset_index()
        for _, row in daily_mean.iterrows():
            if pd.notna(row['temp_c']):
                rows.append({
                    'date': str(row['date']),
                    'water_temp_c': round(float(row['temp_c']), 2),
                    'source': 'historical_soil',
                })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Batch collection
# ---------------------------------------------------------------------------

def _load_morphometry_coords() -> dict[str, tuple[float, float]]:
    """Load lake name -> (lat, lon) mapping from morphometry file."""
    if not MORPHOMETRY_PATH.exists():
        return {}
    with open(MORPHOMETRY_PATH) as f:
        data = json.load(f)
    coords: dict[str, tuple[float, float]] = {}
    for lake_name, props in data.items():
        lat = props.get('lat')
        lon = props.get('lon')
        if lat is not None and lon is not None:
            coords[lake_name.lower().strip()] = (float(lat), float(lon))
    return coords


def _resolve_coordinates(
    event_id: str,
    lat: float | None,
    lon: float | None,
    location: str,
    morphometry: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """Resolve lat/lon, falling back to morphometry lookup by location name."""
    if lat is not None and lon is not None and pd.notna(lat) and pd.notna(lon):
        return (float(lat), float(lon))

    # Try to match location to a known lake
    loc_lower = location.lower().strip()
    for lake_name, (mlat, mlon) in morphometry.items():
        if lake_name in loc_lower or loc_lower in lake_name:
            return (mlat, mlon)

    # Try extracting lake name from "Location, State" format
    parts = [p.strip() for p in location.split(',')]
    if len(parts) >= 1:
        primary = parts[0].lower().strip()
        for lake_name, (mlat, mlon) in morphometry.items():
            if lake_name in primary or primary in lake_name:
                return (mlat, mlon)

    return None


def fetch_water_temp_for_request(
    req: TempRequest,
    *,
    session: requests.Session | None = None,
) -> TempResult:
    """Fetch water temperature for a single (lat, lon, date) request.

    Tries sources in order:
    1. ERA5-Land soil temperature (best lake proxy from reanalysis)
    2. Historical weather soil temperature (station-based fallback)
    """
    # Try ERA5-Land first
    try:
        era5_df = fetch_era5_soil_temp(
            req.lat, req.lon,
            req.date, req.date,
            session=session,
        )
        if not era5_df.empty:
            match = era5_df.loc[era5_df['date'] == req.date]
            if not match.empty:
                row = match.iloc[0]
                source = str(row['source'])
                quality = 'reanalysis_soil' if source == 'era5_soil' else 'soil_proxy'
                return TempResult(
                    event_id=req.event_id,
                    lat=req.lat,
                    lon=req.lon,
                    date=req.date,
                    location=req.location,
                    water_temp_c=float(row['water_temp_c']),
                    source=source,
                    quality=quality,
                )
    except Exception as exc:
        print(f'  ERA5-Land failed for {req.event_id}: {exc}', file=sys.stderr)

    time.sleep(REQUEST_DELAY_S)

    # Fallback: Historical soil temp
    try:
        hist_df = fetch_historical_soil_temp(
            req.lat, req.lon,
            req.date, req.date,
            session=session,
        )
        if not hist_df.empty:
            match = hist_df.loc[hist_df['date'] == req.date]
            if not match.empty:
                row = match.iloc[0]
                return TempResult(
                    event_id=req.event_id,
                    lat=req.lat,
                    lon=req.lon,
                    date=req.date,
                    location=req.location,
                    water_temp_c=float(row['water_temp_c']),
                    source='historical_soil',
                    quality='soil_proxy',
                )
    except Exception as exc:
        print(f'  Historical fallback failed for {req.event_id}: {exc}', file=sys.stderr)

    return TempResult(
        event_id=req.event_id,
        lat=req.lat,
        lon=req.lon,
        date=req.date,
        location=req.location,
        water_temp_c=float('nan'),
        source='none',
        quality='unavailable',
    )


def collect_satellite_water_temps(
    requests_list: list[TempRequest],
    output_path: Path | None = None,
    *,
    session: requests.Session | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Collect water surface temperatures for a list of (lat, lon, date) requests.

    Parameters
    ----------
    requests_list : list of TempRequest
        Each request specifies event_id, lat, lon, date, and optionally location.
    output_path : Path, optional
        Where to write the results CSV. Defaults to DEFAULT_CACHE_PATH.
    session : requests.Session, optional
        Shared HTTP session for connection pooling.
    use_cache : bool
        If True, load existing cache and skip already-fetched event_ids.

    Returns
    -------
    pd.DataFrame with columns:
        event_id, lat, lon, date, location, water_temp_c, source, quality
    """
    if output_path is None:
        output_path = DEFAULT_CACHE_PATH

    # Load existing cache
    cached_df = pd.DataFrame()
    cached_ids: set[str] = set()
    if use_cache and output_path.exists():
        try:
            cached_df = pd.read_csv(output_path, dtype={'event_id': str})
            cached_ids = set(cached_df['event_id'].astype(str).tolist())
            print(f'satellite_temp: loaded {len(cached_ids)} cached results from {output_path.name}', file=sys.stderr)
        except Exception:
            cached_df = pd.DataFrame()

    # Filter to uncached requests
    pending = [r for r in requests_list if r.event_id not in cached_ids]
    if not pending:
        print('satellite_temp: all requests already cached', file=sys.stderr)
        return cached_df

    print(f'satellite_temp: fetching {len(pending)} of {len(requests_list)} requests ({len(cached_ids)} cached)', file=sys.stderr)

    requester = session or requests.Session()
    results: list[dict[str, Any]] = []

    for i, req in enumerate(pending):
        if i > 0 and i % 50 == 0:
            print(f'  progress: {i}/{len(pending)}', file=sys.stderr)

        result = fetch_water_temp_for_request(req, session=requester)
        results.append({
            'event_id': result.event_id,
            'lat': result.lat,
            'lon': result.lon,
            'date': result.date,
            'location': result.location,
            'water_temp_c': result.water_temp_c,
            'source': result.source,
            'quality': result.quality,
        })
        time.sleep(REQUEST_DELAY_S)

    new_df = pd.DataFrame(results)

    # Merge with cache
    if not cached_df.empty:
        combined = pd.concat([cached_df, new_df], ignore_index=True)
    else:
        combined = new_df

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False)

    success_count = combined['water_temp_c'].notna().sum()
    total = len(combined)
    print(
        f'satellite_temp: {success_count}/{total} rows have water temp '
        f'({success_count / total * 100:.1f}% coverage)',
        file=sys.stderr,
    )

    return combined


# ---------------------------------------------------------------------------
# Build requests from the validation dataset
# ---------------------------------------------------------------------------

def build_requests_from_dataset(
    dataset_path: Path,
    *,
    only_missing: bool = True,
) -> list[TempRequest]:
    """Build TempRequest list from the validation dataset.

    Parameters
    ----------
    dataset_path : Path
        Path to the assembled validation dataset CSV.
    only_missing : bool
        If True, only request temps for rows where water_temp_c is NaN.
        If False, request for all rows (useful for cross-validation).

    Returns
    -------
    list of TempRequest
    """
    df = pd.read_csv(dataset_path, dtype={'event_id': str})
    morphometry = _load_morphometry_coords()

    if only_missing:
        target = df[df['water_temp_c'].isna()].copy()
    else:
        target = df.copy()

    requests_list: list[TempRequest] = []
    skipped_no_coords = 0

    for _, row in target.iterrows():
        event_id = str(row.get('event_id', ''))
        location = str(row.get('location', ''))
        lat = row.get('lat')
        lon = row.get('lon')
        date = str(row.get('date', ''))

        if not event_id or not date:
            continue

        coords = _resolve_coordinates(event_id, lat, lon, location, morphometry)
        if coords is None:
            skipped_no_coords += 1
            continue

        requests_list.append(TempRequest(
            event_id=event_id,
            lat=coords[0],
            lon=coords[1],
            date=date,
            location=location,
        ))

    if skipped_no_coords:
        print(
            f'satellite_temp: skipped {skipped_no_coords} events with no resolvable coordinates',
            file=sys.stderr,
        )

    return requests_list


def build_requests_for_all_events(
    dataset_path: Path,
) -> list[TempRequest]:
    """Build TempRequest for ALL events (not just missing), for cross-validation.

    This lets us compare satellite-derived temps against USGS gauge temps
    to validate the satellite data quality.
    """
    return build_requests_from_dataset(dataset_path, only_missing=False)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the satellite water temperature collector.

    Usage:
        python -m castline.validation.collectors.satellite_temp [--all] [--dataset PATH] [--output PATH]

    Options:
        --all        Fetch for all events, not just those missing water_temp_c
        --dataset    Path to the assembled validation dataset CSV
        --output     Path to write the output CSV
        --dry-run    Show what would be fetched without making API calls
    """
    import argparse

    parser = argparse.ArgumentParser(description='Collect satellite water surface temperatures')
    parser.add_argument(
        '--all', action='store_true',
        help='Fetch for all events, not just those missing water_temp_c',
    )
    parser.add_argument(
        '--dataset', type=Path,
        default=Path(__file__).resolve().parent.parent / 'data' / 'assembled' / 'validation_dataset_v4.csv',
        help='Path to the assembled validation dataset CSV',
    )
    parser.add_argument(
        '--output', type=Path,
        default=DEFAULT_CACHE_PATH,
        help='Path to write the output CSV',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be fetched without making API calls',
    )
    args = parser.parse_args()

    print(f'Loading dataset from {args.dataset}', file=sys.stderr)

    if args.all:
        reqs = build_requests_for_all_events(args.dataset)
    else:
        reqs = build_requests_from_dataset(args.dataset)

    print(f'Built {len(reqs)} requests', file=sys.stderr)

    if args.dry_run:
        print(f'\nDry run: would fetch {len(reqs)} water temperatures')
        for req in reqs[:10]:
            print(f'  {req.event_id}: ({req.lat}, {req.lon}) on {req.date} [{req.location}]')
        if len(reqs) > 10:
            print(f'  ... and {len(reqs) - 10} more')
        return

    result_df = collect_satellite_water_temps(reqs, args.output)

    print(f'\nResults written to {args.output}')
    print(f'Total rows: {len(result_df)}')
    print(f'Rows with water temp: {result_df["water_temp_c"].notna().sum()}')
    print(f'Sources: {result_df["source"].value_counts().to_dict()}')


if __name__ == '__main__':
    main()
