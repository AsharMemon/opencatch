from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_OUTCOME_COLUMNS = [
    'event_id',
    'event_name',
    'date',
    'location',
    'species',
    'median_weight_lb',
    'baseline_signal',
    'usgs_site_id',
]

SAMPLE_OUTCOMES = [
    {
        'event_id': 'sample-001',
        'event_name': 'Sample River Open',
        'date': '2024-04-10',
        'location': 'Sample River',
        'species': 'smallmouth_bass',
        'median_weight_lb': 11.2,
        'baseline_signal': 0.42,
        'usgs_site_id': '0000001',
    },
    {
        'event_id': 'sample-002',
        'event_name': 'Sample River Open',
        'date': '2024-04-11',
        'location': 'Sample River',
        'species': 'smallmouth_bass',
        'median_weight_lb': 12.1,
        'baseline_signal': 0.47,
        'usgs_site_id': '0000001',
    },
    {
        'event_id': 'sample-003',
        'event_name': 'Reservoir Derby',
        'date': '2024-05-02',
        'location': 'Blue Reservoir',
        'species': 'largemouth_bass',
        'median_weight_lb': 14.9,
        'baseline_signal': 0.51,
        'usgs_site_id': '0000002',
    },
    {
        'event_id': 'sample-004',
        'event_name': 'Reservoir Derby',
        'date': '2024-05-03',
        'location': 'Blue Reservoir',
        'species': 'largemouth_bass',
        'median_weight_lb': 13.4,
        'baseline_signal': 0.49,
        'usgs_site_id': '0000002',
    },
    {
        'event_id': 'sample-005',
        'event_name': 'Current Cup',
        'date': '2024-06-14',
        'location': 'Tailwater Reach',
        'species': 'smallmouth_bass',
        'median_weight_lb': 10.1,
        'baseline_signal': 0.39,
        'usgs_site_id': '0000003',
    },
    {
        'event_id': 'sample-006',
        'event_name': 'Current Cup',
        'date': '2024-06-15',
        'location': 'Tailwater Reach',
        'species': 'smallmouth_bass',
        'median_weight_lb': 10.8,
        'baseline_signal': 0.41,
        'usgs_site_id': '0000003',
    },
]


def build_sample_outcomes() -> pd.DataFrame:
    return pd.DataFrame(SAMPLE_OUTCOMES)


def _normalize_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_OUTCOME_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f'missing required outcome columns: {missing}')

    normalized = df.copy()
    normalized['event_id'] = normalized['event_id'].astype(str).str.strip()
    normalized['event_name'] = normalized['event_name'].astype(str).str.strip()
    normalized['location'] = normalized['location'].astype(str).str.strip()
    normalized['species'] = normalized['species'].astype(str).str.strip()
    normalized['usgs_site_id'] = normalized['usgs_site_id'].astype(str).str.replace('USGS-', '', regex=False).str.strip()
    normalized['date'] = pd.to_datetime(normalized['date'], utc=False).dt.strftime('%Y-%m-%d')
    normalized['median_weight_lb'] = pd.to_numeric(normalized['median_weight_lb'])
    normalized['baseline_signal'] = pd.to_numeric(normalized['baseline_signal'])

    if normalized['event_id'].duplicated().any():
        duplicates = normalized.loc[normalized['event_id'].duplicated(), 'event_id'].tolist()
        raise ValueError(f'duplicate event_id values found: {duplicates}')

    return normalized.sort_values(['date', 'event_id']).reset_index(drop=True)


def collect_historical_outcomes(
    output_path: Path,
    sample: bool = False,
    source_path: Path | None = None,
) -> pd.DataFrame:
    if sample:
        df = build_sample_outcomes()
        source_mode = 'sample'
    elif source_path is not None:
        df = pd.read_csv(source_path, dtype={'event_id': str, 'usgs_site_id': str})
        source_mode = f'csv:{Path(source_path).name}'
    else:
        df = build_sample_outcomes()
        source_mode = 'scaffold_placeholder'

    normalized = _normalize_outcomes(df)
    normalized['source_mode'] = source_mode
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(output_path, index=False)
    return normalized
