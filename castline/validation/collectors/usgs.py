from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

USGS_DV_URL = 'https://waterservices.usgs.gov/nwis/dv/'
PARAMETER_CODES = {
    '00010': 'water_temp_c',
    '00060': 'discharge_cfs',
    '00065': 'gage_height_ft',
}

SAMPLE_USGS = [
    {'event_id': 'sample-001', 'site_id': 'USGS-0000001', 'water_temp_c': 15.4, 'discharge_cfs': 520.0, 'gage_height_ft': 3.6, 'temp_delta_24h_c': 1.1, 'flow_delta_24h_pct': -8.0},
    {'event_id': 'sample-002', 'site_id': 'USGS-0000001', 'water_temp_c': 16.2, 'discharge_cfs': 498.0, 'gage_height_ft': 3.5, 'temp_delta_24h_c': 1.3, 'flow_delta_24h_pct': -4.0},
    {'event_id': 'sample-003', 'site_id': 'USGS-0000002', 'water_temp_c': 20.0, 'discharge_cfs': 310.0, 'gage_height_ft': 2.1, 'temp_delta_24h_c': 0.4, 'flow_delta_24h_pct': 3.0},
    {'event_id': 'sample-004', 'site_id': 'USGS-0000002', 'water_temp_c': 19.1, 'discharge_cfs': 345.0, 'gage_height_ft': 2.3, 'temp_delta_24h_c': -0.6, 'flow_delta_24h_pct': 11.0},
    {'event_id': 'sample-005', 'site_id': 'USGS-0000003', 'water_temp_c': 17.8, 'discharge_cfs': 760.0, 'gage_height_ft': 4.9, 'temp_delta_24h_c': 0.2, 'flow_delta_24h_pct': 14.0},
    {'event_id': 'sample-006', 'site_id': 'USGS-0000003', 'water_temp_c': 18.0, 'discharge_cfs': 722.0, 'gage_height_ft': 4.7, 'temp_delta_24h_c': 0.3, 'flow_delta_24h_pct': -5.0},
]


@dataclass(frozen=True)
class UsgsEvent:
    event_id: str
    site_id: str
    event_date: pd.Timestamp


def build_sample_usgs_history() -> pd.DataFrame:
    return pd.DataFrame(SAMPLE_USGS)


def _parse_usgs_json(payload: dict, *, site_id: str) -> pd.DataFrame:
    rows: dict[pd.Timestamp, dict[str, float | pd.Timestamp]] = {}
    series_items = payload.get('value', {}).get('timeSeries', [])
    for series in series_items:
        variable = series.get('variable', {})
        variable_code = None
        for code_item in variable.get('variableCode', []):
            code = code_item.get('value')
            if code in PARAMETER_CODES:
                variable_code = code
                break
        if variable_code is None:
            continue

        target_column = PARAMETER_CODES[variable_code]
        for values_group in series.get('values', []):
            for item in values_group.get('value', []):
                timestamp = pd.to_datetime(item.get('dateTime')).tz_localize(None).normalize()
                value_text = item.get('value')
                if value_text in (None, ''):
                    continue
                row = rows.setdefault(timestamp, {'date': timestamp})
                row[target_column] = float(value_text)

    if not rows:
        return pd.DataFrame(columns=['date', *PARAMETER_CODES.values()])

    df = pd.DataFrame(rows.values()).sort_values('date').reset_index(drop=True)
    df['site_id'] = site_id
    return df


def fetch_usgs_daily_values(
    site_id: str,
    start_date: str,
    end_date: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> pd.DataFrame:
    requester = session or requests.Session()
    response = requester.get(
        USGS_DV_URL,
        params={
            'format': 'json',
            'sites': site_id,
            'startDT': start_date,
            'endDT': end_date,
            'parameterCd': ','.join(PARAMETER_CODES.keys()),
            'siteStatus': 'all',
        },
        headers={'Accept': 'application/json'},
        timeout=timeout,
    )
    response.raise_for_status()
    return _parse_usgs_json(response.json(), site_id=site_id)


def _load_events(outcomes_path: Path) -> list[UsgsEvent]:
    outcomes = pd.read_csv(outcomes_path, dtype={'event_id': str, 'usgs_site_id': str, 'date': str})
    missing = {'event_id', 'date', 'usgs_site_id'}.difference(outcomes.columns)
    if missing:
        raise ValueError(f'outcomes file missing required columns for USGS collection: {sorted(missing)}')

    events: list[UsgsEvent] = []
    for _, row in outcomes.iterrows():
        events.append(
            UsgsEvent(
                event_id=str(row['event_id']).strip(),
                site_id=str(row['usgs_site_id']).replace('USGS-', '').strip(),
                event_date=pd.to_datetime(row['date']).normalize(),
            )
        )
    return events


def _build_event_feature_row(event: UsgsEvent, history: pd.DataFrame) -> dict[str, object]:
    if history.empty:
        raise ValueError(f'no USGS daily values returned for site {event.site_id} around {event.event_date.date()}')

    history = history.sort_values('date').reset_index(drop=True)
    exact_match = history.loc[history['date'] == event.event_date]
    if exact_match.empty:
        usable = history.loc[history['date'] <= event.event_date]
        if usable.empty:
            raise ValueError(f'no USGS values on or before event date for site {event.site_id}')
        current = usable.iloc[-1]
    else:
        current = exact_match.iloc[-1]

    current_index = history.index[history['date'] == current['date']][-1]
    previous = history.iloc[current_index - 1] if current_index > 0 else current

    current_discharge = float(current.get('discharge_cfs') or 0.0)
    previous_discharge = float(previous.get('discharge_cfs') or 0.0)
    flow_delta_pct = 0.0
    if previous_discharge:
        flow_delta_pct = ((current_discharge - previous_discharge) / previous_discharge) * 100.0

    return {
        'event_id': event.event_id,
        'site_id': f'USGS-{event.site_id}',
        'observation_date': pd.Timestamp(current['date']).strftime('%Y-%m-%d'),
        'water_temp_c': float(current.get('water_temp_c') or 0.0),
        'discharge_cfs': current_discharge,
        'gage_height_ft': float(current.get('gage_height_ft') or 0.0),
        'temp_delta_24h_c': float((current.get('water_temp_c') or 0.0) - (previous.get('water_temp_c') or 0.0)),
        'flow_delta_24h_pct': flow_delta_pct,
        'source_mode': 'usgs_daily_values',
    }


def collect_usgs_history(
    output_path: Path,
    sample: bool = False,
    outcomes_path: Path | None = None,
    lookback_days: int = 7,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    if sample:
        df = build_sample_usgs_history()
    elif outcomes_path is not None:
        rows: list[dict[str, object]] = []
        cache: dict[tuple[str, str, str], pd.DataFrame] = {}
        for event in _load_events(outcomes_path):
            start_date = (event.event_date - pd.Timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            end_date = event.event_date.strftime('%Y-%m-%d')
            cache_key = (event.site_id, start_date, end_date)
            history = cache.get(cache_key)
            if history is None:
                history = fetch_usgs_daily_values(
                    event.site_id,
                    start_date,
                    end_date,
                    session=session,
                )
                cache[cache_key] = history
            rows.append(_build_event_feature_row(event, history))
        df = pd.DataFrame(rows)
    else:
        df = build_sample_usgs_history()
        df['source_mode'] = 'scaffold_placeholder'

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return df
