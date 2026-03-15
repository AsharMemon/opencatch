from __future__ import annotations

from pathlib import Path

import pandas as pd

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


def build_sample_weather_history() -> pd.DataFrame:
    return pd.DataFrame(SAMPLE_WEATHER)


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


def collect_weather_history(
    output_path: Path,
    sample: bool = False,
    source_path: Path | None = None,
) -> pd.DataFrame:
    if sample:
        df = build_sample_weather_history()
        source_mode = 'sample'
    elif source_path is not None:
        df = pd.read_csv(source_path, dtype={'event_id': str})
        source_mode = f'csv:{Path(source_path).name}'
    else:
        df = build_sample_weather_history()
        source_mode = 'scaffold_placeholder'

    normalized = _normalize_weather(df)
    normalized['source_mode'] = source_mode
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(output_path, index=False)
    return normalized
