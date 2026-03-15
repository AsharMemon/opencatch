from __future__ import annotations

import re
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

REQUIRED_WEATHER_COLUMNS = [
    'event_id',
    'air_temp_c',
    'pressure_mb',
    'wind_speed_kph',
    'cloud_cover_pct',
    'precip_24h_mm',
]

SAMPLE_WEATHER = [
    {
        'event_id': 'sample-001',
        'air_temp_c': 17.0,
        'pressure_mb': 1007.0,
        'wind_speed_kph': 12.0,
        'cloud_cover_pct': 64.0,
        'precip_24h_mm': 4.2,
    },
    {
        'event_id': 'sample-002',
        'air_temp_c': 18.0,
        'pressure_mb': 1005.0,
        'wind_speed_kph': 10.0,
        'cloud_cover_pct': 58.0,
        'precip_24h_mm': 2.5,
    },
    {
        'event_id': 'sample-003',
        'air_temp_c': 23.0,
        'pressure_mb': 1011.0,
        'wind_speed_kph': 9.0,
        'cloud_cover_pct': 32.0,
        'precip_24h_mm': 0.0,
    },
    {
        'event_id': 'sample-004',
        'air_temp_c': 24.0,
        'pressure_mb': 1009.0,
        'wind_speed_kph': 11.0,
        'cloud_cover_pct': 44.0,
        'precip_24h_mm': 1.3,
    },
    {
        'event_id': 'sample-005',
        'air_temp_c': 21.0,
        'pressure_mb': 1004.0,
        'wind_speed_kph': 18.0,
        'cloud_cover_pct': 72.0,
        'precip_24h_mm': 8.7,
    },
    {
        'event_id': 'sample-006',
        'air_temp_c': 20.0,
        'pressure_mb': 1006.0,
        'wind_speed_kph': 13.0,
        'cloud_cover_pct': 61.0,
        'precip_24h_mm': 3.1,
    },
]

IEM_NETWORK_URL = 'https://mesonet.agron.iastate.edu/geojson/network.php'
IEM_ASOS_REQUEST_URL = 'https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py'
DEFAULT_HEADERS = {'User-Agent': 'Mozilla/5.0 (CASTLINE weather collector)'}

SKY_COVER_TO_PCT = {
    'CLR': 0.0,
    'SKC': 0.0,
    'FEW': 25.0,
    'SCT': 50.0,
    'BKN': 87.5,
    'OVC': 100.0,
    'VV': 100.0,
}

_STATE_TOKEN_ALIASES = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
    'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'florida': 'FL', 'georgia': 'GA',
    'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD', 'massachusetts': 'MA',
    'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT',
    'nebraska': 'NE', 'nevada': 'NV', 'newhampshire': 'NH', 'newjersey': 'NJ', 'newmexico': 'NM',
    'newyork': 'NY', 'northcarolina': 'NC', 'northdakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhodeisland': 'RI', 'southcarolina': 'SC', 'southdakota': 'SD',
    'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA',
    'westvirginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY', 'districtcolumbia': 'DC',
}


@dataclass(frozen=True)
class WeatherEvent:
    event_id: str
    event_date: pd.Timestamp
    location: str
    state: str
    city: str
    iem_station: str


def build_sample_weather_history() -> pd.DataFrame:
    return pd.DataFrame(SAMPLE_WEATHER)


def _normalize_state_code(value: str) -> str:
    cleaned = re.sub(r'[^A-Za-z]', '', str(value or '')).strip()
    if not cleaned:
        return ''
    if len(cleaned) == 2:
        return cleaned.upper()
    return _STATE_TOKEN_ALIASES.get(cleaned.lower(), '')


def _tokenize_text(value: str) -> set[str]:
    return {token for token in re.findall(r'[a-z0-9]+', str(value or '').lower()) if len(token) >= 3}


def _extract_state_from_location(location: str) -> str:
    parts = [part.strip() for part in str(location or '').split(',') if part.strip()]
    if not parts:
        return ''
    return _normalize_state_code(parts[-1])


def _extract_city_from_location(location: str) -> str:
    parts = [part.strip() for part in str(location or '').split(',') if part.strip()]
    if len(parts) >= 2:
        return parts[-2]
    return ''


def _normalize_weather(df: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_WEATHER_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f'missing required weather columns: {missing}')

    normalized = df.copy()
    normalized['event_id'] = normalized['event_id'].astype(str).str.strip()
    for column in REQUIRED_WEATHER_COLUMNS:
        if column == 'event_id':
            continue
        normalized[column] = pd.to_numeric(normalized[column])

    if normalized['event_id'].duplicated().any():
        duplicates = normalized.loc[normalized['event_id'].duplicated(), 'event_id'].tolist()
        raise ValueError(f'duplicate weather event_id values found: {duplicates}')

    return normalized.sort_values('event_id').reset_index(drop=True)


def _load_outcome_events(outcomes_path: Path) -> list[WeatherEvent]:
    outcomes = pd.read_csv(outcomes_path, dtype=str).fillna('')
    missing = {'event_id', 'date', 'location'}.difference(outcomes.columns)
    if missing:
        raise ValueError(f'outcomes file missing required columns for weather collection: {sorted(missing)}')

    events: list[WeatherEvent] = []
    for _, row in outcomes.iterrows():
        location = str(row.get('location', '')).strip()
        state = _normalize_state_code(row.get('state', '')) or _extract_state_from_location(location)
        city = str(row.get('city', '')).strip() or _extract_city_from_location(location)
        events.append(
            WeatherEvent(
                event_id=str(row['event_id']).strip(),
                event_date=pd.to_datetime(row['date']).normalize(),
                location=location,
                state=state,
                city=city,
                iem_station=str(row.get('iem_station', row.get('weather_station', ''))).strip().upper(),
            )
        )
    return events


def _score_station_match(*, station_id: str, station_name: str, city: str, location: str) -> float:
    station_tokens = _tokenize_text(f'{station_id} {station_name}')
    city_tokens = _tokenize_text(city)
    location_tokens = _tokenize_text(location)
    score = 0.0
    score += len(city_tokens & station_tokens) * 4.0
    score += len(location_tokens & station_tokens) * 1.5
    if city and city.lower() in station_name.lower():
        score += 3.0
    return score


def fetch_iem_network_stations(
    network: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> pd.DataFrame:
    requester = session or requests.Session()
    response = requester.get(
        IEM_NETWORK_URL,
        params={'network': network},
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    rows: list[dict[str, Any]] = []
    for feature in payload.get('features', []):
        properties = feature.get('properties', {}) or {}
        geometry = feature.get('geometry', {}) or {}
        coordinates = geometry.get('coordinates', [None, None])
        rows.append(
            {
                'station': str(feature.get('id', '')).upper(),
                'station_name': str(properties.get('sname', '')),
                'state': str(properties.get('state', '')),
                'network': network,
                'lon': coordinates[0],
                'lat': coordinates[1],
            }
        )
    return pd.DataFrame(rows)


def pick_iem_station_for_event(
    event: WeatherEvent,
    *,
    session: requests.Session | None = None,
    station_cache: dict[str, pd.DataFrame] | None = None,
) -> str:
    if event.iem_station:
        return event.iem_station
    if not event.state:
        raise ValueError(f'could not infer state for event {event.event_id} from location {event.location!r}')

    network = f'{event.state}_ASOS'
    if station_cache is not None and network in station_cache:
        stations = station_cache[network]
    else:
        stations = fetch_iem_network_stations(network, session=session)
        if station_cache is not None:
            station_cache[network] = stations

    if stations.empty:
        raise ValueError(f'no IEM ASOS stations returned for network {network}')

    scored = stations.copy()
    scored['match_score'] = scored.apply(
        lambda row: _score_station_match(
            station_id=str(row.get('station', '')),
            station_name=str(row.get('station_name', '')),
            city=event.city,
            location=event.location,
        ),
        axis=1,
    )
    scored = scored.sort_values(['match_score', 'station'], ascending=[False, True]).reset_index(drop=True)
    return str(scored.iloc[0]['station'])


def _sky_cover_pct(row: pd.Series) -> float:
    sky_columns = ['skyc1', 'skyc2', 'skyc3', 'skyc4']
    cover_values = [
        SKY_COVER_TO_PCT.get(str(row.get(column, '')).strip().upper())
        for column in sky_columns
        if str(row.get(column, '')).strip()
    ]
    resolved = [value for value in cover_values if value is not None]
    if resolved:
        return max(resolved)
    return 0.0


def fetch_iem_asos_history(
    station: str,
    start_date: str,
    end_date: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> pd.DataFrame:
    requester = session or requests.Session()
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    response = requester.get(
        IEM_ASOS_REQUEST_URL,
        params={
            'station': station,
            'data': ['tmpf', 'mslp', 'skyc1', 'skyc2', 'skyc3', 'skyc4', 'sknt', 'p01i'],
            'year1': start_ts.year,
            'month1': start_ts.month,
            'day1': start_ts.day,
            'year2': end_ts.year,
            'month2': end_ts.month,
            'day2': end_ts.day,
            'tz': 'UTC',
            'format': 'onlycomma',
            'latlon': 'yes',
            'elev': 'yes',
            'missing': 'null',
            'trace': 'null',
            'direct': 'no',
            'report_type': ['1', '2', '3'],
        },
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    df = pd.read_csv(StringIO(response.text))
    if df.empty:
        return pd.DataFrame()

    df['valid'] = pd.to_datetime(df['valid'], utc=True).dt.tz_localize(None)
    df['air_temp_c'] = (pd.to_numeric(df.get('tmpf'), errors='coerce') - 32.0) * (5.0 / 9.0)
    df['pressure_mb'] = pd.to_numeric(df.get('mslp'), errors='coerce')
    df['wind_speed_kph'] = pd.to_numeric(df.get('sknt'), errors='coerce') * 1.852
    df['precip_mm'] = pd.to_numeric(df.get('p01i'), errors='coerce').fillna(0.0) * 25.4
    df['cloud_cover_pct'] = df.apply(_sky_cover_pct, axis=1)
    return df[['valid', 'air_temp_c', 'pressure_mb', 'wind_speed_kph', 'cloud_cover_pct', 'precip_mm']]


def _summarize_event_weather(event: WeatherEvent, station: str, history: pd.DataFrame) -> dict[str, Any]:
    if history.empty:
        raise ValueError(f'no IEM weather rows returned for station {station} around {event.event_date.date()}')

    event_start = event.event_date
    event_end = event.event_date + pd.Timedelta(days=1)
    event_window = history.loc[(history['valid'] >= event_start) & (history['valid'] < event_end)].copy()
    if event_window.empty:
        fallback = history.loc[history['valid'] <= event_end].sort_values('valid')
        if fallback.empty:
            raise ValueError(f'no usable IEM weather rows on or before event date for station {station}')
        event_window = fallback.tail(24).copy()

    return {
        'event_id': event.event_id,
        'air_temp_c': round(float(event_window['air_temp_c'].dropna().mean() or 0.0), 4),
        'pressure_mb': round(float(event_window['pressure_mb'].dropna().mean() or 0.0), 4),
        'wind_speed_kph': round(float(event_window['wind_speed_kph'].dropna().mean() or 0.0), 4),
        'cloud_cover_pct': round(float(event_window['cloud_cover_pct'].dropna().mean() or 0.0), 4),
        'precip_24h_mm': round(float(event_window['precip_mm'].fillna(0.0).sum()), 4),
        'iem_station': station,
        'source_mode': 'iem_asos_api',
    }


def collect_weather_history(
    output_path: Path,
    sample: bool = False,
    source_path: Path | None = None,
    outcomes_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    if sample:
        df = build_sample_weather_history()
        source_mode = 'sample'
    elif outcomes_path is not None:
        station_cache: dict[str, pd.DataFrame] = {}
        history_cache: dict[tuple[str, str, str], pd.DataFrame] = {}
        rows: list[dict[str, Any]] = []
        for event in _load_outcome_events(outcomes_path):
            station = pick_iem_station_for_event(event, session=session, station_cache=station_cache)
            start_date = (event.event_date - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            end_date = (event.event_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            cache_key = (station, start_date, end_date)
            history = history_cache.get(cache_key)
            if history is None:
                history = fetch_iem_asos_history(station, start_date, end_date, session=session)
                history_cache[cache_key] = history
            rows.append(_summarize_event_weather(event, station, history))
        df = pd.DataFrame(rows)
        source_mode = 'iem_asos_api'
    elif source_path is not None:
        df = pd.read_csv(source_path, dtype={'event_id': str})
        source_mode = f'csv:{Path(source_path).name}'
    else:
        df = build_sample_weather_history()
        source_mode = 'scaffold_placeholder'

    normalized = _normalize_weather(df)
    if 'iem_station' in df.columns and 'iem_station' not in normalized.columns:
        normalized = normalized.merge(df[['event_id', 'iem_station']], on='event_id', how='left')
    normalized['source_mode'] = source_mode
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(output_path, index=False)
    return normalized
