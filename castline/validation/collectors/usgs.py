from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class CandidateEvaluationEvent:
    tournament_slug: str
    event_id: str
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


def _load_candidate_evaluation_events(outcomes_path: Path) -> list[CandidateEvaluationEvent]:
    outcomes = pd.read_csv(outcomes_path, dtype={'event_id': str, 'tournament_slug': str, 'date': str})
    missing = {'event_id', 'tournament_slug', 'date'}.difference(outcomes.columns)
    if missing:
        raise ValueError(f'outcomes file missing required columns for USGS coverage evaluation: {sorted(missing)}')

    events: list[CandidateEvaluationEvent] = []
    for _, row in outcomes.iterrows():
        events.append(
            CandidateEvaluationEvent(
                tournament_slug=str(row['tournament_slug']).strip(),
                event_id=str(row['event_id']).strip(),
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


def _history_for_window(
    *,
    site_id: str,
    event_date: pd.Timestamp,
    lookback_days: int,
    session: requests.Session | None,
    cache: dict[tuple[str, str, str], pd.DataFrame],
) -> pd.DataFrame:
    start_date = (event_date - pd.Timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    end_date = event_date.strftime('%Y-%m-%d')
    cache_key = (site_id, start_date, end_date)
    history = cache.get(cache_key)
    if history is None:
        history = fetch_usgs_daily_values(
            site_id,
            start_date,
            end_date,
            session=session,
        )
        cache[cache_key] = history
    return history


def _evaluate_history_for_event(*, event_date: pd.Timestamp, history: pd.DataFrame) -> tuple[bool, str, int, int, str]:
    if history.empty:
        return False, 'no_daily_values', 0, 0, ''

    history = history.sort_values('date').reset_index(drop=True)
    usable = history.loc[history['date'] <= event_date].copy()
    if usable.empty:
        return False, 'only_future_values', int(len(history)), 0, ''

    current = usable.iloc[-1]
    available_columns = [column for column in PARAMETER_CODES.values() if pd.notna(current.get(column))]
    if not available_columns:
        return False, 'no_supported_parameters', int(len(history)), 0, pd.Timestamp(current['date']).strftime('%Y-%m-%d')

    return True, 'usable', int(len(history)), len(available_columns), pd.Timestamp(current['date']).strftime('%Y-%m-%d')


def evaluate_usgs_mapping_candidates(
    *,
    outcomes_path: Path,
    suggestions_path: Path,
    output_path: Path,
    lookback_days: int = 7,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    events = _load_candidate_evaluation_events(outcomes_path)
    suggestions = pd.read_csv(suggestions_path, dtype=str).fillna('')
    if suggestions.empty:
        raise ValueError('suggestions file is empty; nothing to evaluate')

    required = {'tournament_slug', 'candidate_rank', 'suggested_usgs_site_id'}
    missing = required.difference(suggestions.columns)
    if missing:
        raise ValueError(f'suggestions file missing required columns: {sorted(missing)}')

    suggestions['candidate_rank'] = pd.to_numeric(suggestions['candidate_rank'], errors='coerce').fillna(9999).astype(int)
    suggestions['suggested_usgs_site_id'] = suggestions['suggested_usgs_site_id'].astype(str).str.replace('USGS-', '', regex=False).str.strip()

    event_rows = pd.DataFrame(
        [
            {
                'tournament_slug': event.tournament_slug,
                'event_id': event.event_id,
                'event_date': event.event_date,
            }
            for event in events
        ]
    )
    candidate_rows = suggestions[['tournament_slug', 'candidate_rank', 'suggested_usgs_site_id']].copy()
    candidate_rows['candidate_rank'] = pd.to_numeric(candidate_rows['candidate_rank'], errors='coerce').fillna(9999).astype(int)
    candidate_rows['suggested_usgs_site_id'] = candidate_rows['suggested_usgs_site_id'].astype(str).str.replace('USGS-', '', regex=False).str.strip()
    candidate_rows = candidate_rows.loc[candidate_rows['suggested_usgs_site_id'].ne('')].reset_index(drop=True)
    if candidate_rows.empty:
        raise ValueError('suggestions file does not contain any suggested_usgs_site_id values to evaluate')

    evaluation_targets = event_rows.merge(candidate_rows, on='tournament_slug', how='inner')
    history_cache: dict[tuple[str, str, str], pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []

    for _, target in evaluation_targets.iterrows():
        site_id = str(target['suggested_usgs_site_id'])
        event_date = pd.Timestamp(target['event_date']).normalize()
        history = _history_for_window(
            site_id=site_id,
            event_date=event_date,
            lookback_days=lookback_days,
            session=session,
            cache=history_cache,
        )
        usable, status, observation_count, parameter_count, matched_date = _evaluate_history_for_event(
            event_date=event_date,
            history=history,
        )
        rows.append(
            {
                'tournament_slug': str(target['tournament_slug']),
                'event_id': str(target['event_id']),
                'event_date': event_date.strftime('%Y-%m-%d'),
                'candidate_rank': int(target['candidate_rank']),
                'suggested_usgs_site_id': site_id,
                'coverage_status': status,
                'has_usable_history': usable,
                'observation_count': observation_count,
                'parameter_count': parameter_count,
                'matched_observation_date': matched_date,
            }
        )

    coverage = pd.DataFrame(rows)
    if coverage.empty:
        raise ValueError('no candidate/event combinations were available for evaluation')

    grouped = coverage.groupby(['tournament_slug', 'candidate_rank', 'suggested_usgs_site_id'], as_index=False).agg(
        event_count=('event_id', 'count'),
        usable_event_count=('has_usable_history', 'sum'),
        min_parameter_count=('parameter_count', 'min'),
        avg_parameter_count=('parameter_count', 'mean'),
        total_observation_count=('observation_count', 'sum'),
        coverage_statuses=('coverage_status', lambda values: ','.join(sorted(set(str(value) for value in values)))),
        matched_observation_dates=('matched_observation_date', lambda values: ','.join(sorted({str(value) for value in values if str(value)}))),
    )
    grouped['usable_event_pct'] = grouped['usable_event_count'] / grouped['event_count']
    grouped['coverage_score'] = (
        (grouped['usable_event_count'] * 1000)
        + (grouped['min_parameter_count'] * 100)
        + grouped['total_observation_count']
        - grouped['candidate_rank']
    )

    ranked = suggestions.merge(
        grouped,
        on=['tournament_slug', 'candidate_rank', 'suggested_usgs_site_id'],
        how='left',
    )
    ranked['event_count'] = ranked['event_count'].fillna(0).astype(int)
    ranked['usable_event_count'] = ranked['usable_event_count'].fillna(0).astype(int)
    ranked['min_parameter_count'] = ranked['min_parameter_count'].fillna(0).astype(int)
    ranked['avg_parameter_count'] = ranked['avg_parameter_count'].fillna(0.0)
    ranked['total_observation_count'] = ranked['total_observation_count'].fillna(0).astype(int)
    ranked['usable_event_pct'] = ranked['usable_event_pct'].fillna(0.0)
    ranked['coverage_score'] = ranked['coverage_score'].fillna(-1.0)
    ranked['coverage_statuses'] = ranked['coverage_statuses'].fillna('not_evaluated')
    ranked['matched_observation_dates'] = ranked['matched_observation_dates'].fillna('')

    ranked['recommended_by_coverage'] = False
    usable_ranked = ranked.loc[ranked['usable_event_count'] > 0].copy()
    if not usable_ranked.empty:
        best_idx = usable_ranked.groupby('tournament_slug')['coverage_score'].idxmax()
        ranked.loc[best_idx, 'recommended_by_coverage'] = True
    ranked['selected_usgs_site_id'] = ranked['selected_usgs_site_id'].astype(str).str.replace('USGS-', '', regex=False).str.strip()
    ranked['recommended_usgs_site_id'] = ranked.apply(
        lambda row: row['suggested_usgs_site_id'] if row['recommended_by_coverage'] else '',
        axis=1,
    )
    ranked['review_status'] = ranked.apply(
        lambda row: (
            'coverage-recommended'
            if row['recommended_by_coverage'] and row['usable_event_count'] > 0 and row['selected_usgs_site_id'] != row['suggested_usgs_site_id']
            else row['review_status']
        ),
        axis=1,
    )

    ranked = ranked.sort_values(['tournament_slug', 'coverage_score', 'candidate_rank'], ascending=[True, False, True]).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(output_path, index=False)
    return ranked


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
            history = _history_for_window(
                site_id=event.site_id,
                event_date=event.event_date,
                lookback_days=lookback_days,
                session=session,
                cache=cache,
            )
            try:
                rows.append(_build_event_feature_row(event, history))
            except ValueError as exc:
                print(
                    f"warning: skipping USGS event {event.event_id} for site {event.site_id}: {exc}",
                    file=sys.stderr,
                )
                continue
        df = pd.DataFrame(rows)
    else:
        df = build_sample_usgs_history()
        df['source_mode'] = 'scaffold_placeholder'

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return df
