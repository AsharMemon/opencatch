from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

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

BASSMASTER_API_URL = 'https://www.bassmaster.com/wp-json/wp/v2/tournament'
BASSMASTER_SEARCH_API_URL = 'https://www.bassmaster.com/wp-json/wp/v2/search'
USGS_SITE_SERVICE_URL = 'https://waterservices.usgs.gov/nwis/site/'
DEFAULT_HEADERS = {'User-Agent': 'Mozilla/5.0 (CASTLINE validation collector)'}
DEFAULT_BASSMASTER_SPECIES = 'black_bass'
DEFAULT_SITE_TYPES = ('LK', 'ST', 'ST-CA', 'ST-DCH', 'ES')


@dataclass(slots=True)
class BassmasterTournamentResult:
    tournament_slug: str
    event_name: str
    location: str
    water_body: str
    city: str
    state: str
    start_date: pd.Timestamp
    results_pdf_url: str


def build_sample_outcomes() -> pd.DataFrame:
    return pd.DataFrame(SAMPLE_OUTCOMES)


def _seasonal_baseline_signal(date_value: str) -> float:
    timestamp = pd.Timestamp(date_value)
    day_of_year = timestamp.day_of_year
    signal = 0.5 + 0.3 * math.sin((2 * math.pi * day_of_year) / 365.25)
    return round(max(0.0, min(1.0, signal)), 4)


def _normalize_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()

    if 'baseline_signal' not in normalized.columns:
        normalized['baseline_signal'] = normalized['date'].map(_seasonal_baseline_signal)
    if 'species' not in normalized.columns:
        normalized['species'] = DEFAULT_BASSMASTER_SPECIES
    if 'usgs_site_id' not in normalized.columns:
        normalized['usgs_site_id'] = ''

    missing = [column for column in REQUIRED_OUTCOME_COLUMNS if column not in normalized.columns]
    if missing:
        raise ValueError(f'missing required outcome columns: {missing}')

    normalized['event_id'] = normalized['event_id'].astype(str).str.strip()
    normalized['event_name'] = normalized['event_name'].astype(str).str.strip()
    normalized['location'] = normalized['location'].astype(str).str.strip()
    normalized['species'] = normalized['species'].astype(str).str.strip()
    normalized['usgs_site_id'] = normalized['usgs_site_id'].fillna('').astype(str).str.replace('USGS-', '', regex=False).str.strip()
    normalized['date'] = pd.to_datetime(normalized['date'], utc=False).dt.strftime('%Y-%m-%d')
    normalized['median_weight_lb'] = pd.to_numeric(normalized['median_weight_lb'])
    normalized['baseline_signal'] = pd.to_numeric(normalized['baseline_signal'])

    if normalized['event_id'].duplicated().any():
        duplicates = normalized.loc[normalized['event_id'].duplicated(), 'event_id'].tolist()
        raise ValueError(f'duplicate event_id values found: {duplicates}')

    missing_gauges = normalized['usgs_site_id'].eq('')
    if missing_gauges.any():
        missing_events = normalized.loc[missing_gauges, 'event_id'].tolist()
        raise ValueError(
            'missing usgs_site_id values for event_id(s): '
            f"{missing_events}. Provide a mapping CSV when using source adapters."
        )

    return normalized.sort_values(['date', 'event_id']).reset_index(drop=True)


def _fetch_json(url: str, *, session: requests.Session, params: dict[str, Any] | None = None) -> Any:
    response = session.get(url, params=params, timeout=30, headers=DEFAULT_HEADERS)
    response.raise_for_status()
    return response.json()


def _extract_results_pdf_url(rendered_html: str) -> str | None:
    soup = BeautifulSoup(rendered_html, 'html.parser')
    for anchor in soup.find_all('a', href=True):
        href = anchor['href'].strip()
        if href.lower().endswith('.pdf'):
            return urljoin('https://www.bassmaster.com', href)
    return None


def _extract_parent_tournament_slug(results_link: str) -> str:
    match = re.search(r'/tournament/([^/]+)/results/?$', results_link.strip())
    return match.group(1) if match else ''


def _fetch_bassmaster_tournament_parent(*, session: requests.Session, tournament_slug: str) -> dict[str, Any] | None:
    if not tournament_slug:
        return None
    payload = _fetch_json(
        BASSMASTER_API_URL,
        session=session,
        params={
            'slug': tournament_slug,
            'per_page': 1,
            '_fields': 'id,slug,link,title,content,meta',
        },
    )
    if isinstance(payload, list) and payload:
        return payload[0]
    return None


def _clean_text(value: Any) -> str:
    return BeautifulSoup(str(value or ''), 'html.parser').get_text(' ', strip=True)


def _get_tournament_place(meta: dict[str, Any]) -> tuple[str, str, str, str]:
    water_body = _clean_text(meta.get('bassmaster_tournament_body_of_water', ''))
    city = _clean_text(meta.get('bassmaster_tournament_city', ''))
    state = _clean_text(meta.get('bassmaster_tournament_state', ''))
    parts = [water_body, city, state]
    return water_body, city, state, ', '.join(part for part in parts if part)


def _build_location(meta: dict[str, Any]) -> str:
    return _get_tournament_place(meta)[-1]


def _iter_bassmaster_results_search_entries(*, session: requests.Session) -> list[dict[str, Any]]:
    page = 1
    entries: list[dict[str, Any]] = []

    while True:
        try:
            payload = _fetch_json(
                BASSMASTER_SEARCH_API_URL,
                session=session,
                params={
                    'search': 'Results',
                    'type': 'post',
                    'subtype': 'tournament',
                    'per_page': 100,
                    'page': page,
                },
            )
        except requests.HTTPError as exc:
            response = getattr(exc, 'response', None)
            if response is not None and response.status_code in {400, 404} and page > 1:
                break
            raise
        if not payload:
            break
        entries.extend(payload)
        page += 1

    return entries


def _infer_year_from_results_link(link: str) -> int | None:
    match = re.search(r'/tournament/(\d{4})-', link)
    return int(match.group(1)) if match else None


def _fetch_bassmaster_results_index(
    *,
    session: requests.Session,
    start_year: int,
    end_year: int,
) -> list[BassmasterTournamentResult]:
    tournaments: list[BassmasterTournamentResult] = []
    seen_links: set[str] = set()

    for entry in _iter_bassmaster_results_search_entries(session=session):
        link = str(entry.get('url', '')).strip()
        if not link.endswith('/results/') or link in seen_links:
            continue

        inferred_year = _infer_year_from_results_link(link)
        if inferred_year is not None and not (start_year <= inferred_year <= end_year):
            continue

        detail_url = ''
        links = entry.get('_links', {}) or {}
        self_links = links.get('self') or []
        if self_links:
            detail_url = str((self_links[0] or {}).get('href', '')).strip()
        if not detail_url:
            detail_url = f"{BASSMASTER_API_URL}/{entry.get('id')}"

        result_item = _fetch_json(detail_url, session=session)
        seen_links.add(link)

        pdf_url = _extract_results_pdf_url(result_item.get('content', {}).get('rendered', ''))
        if not pdf_url:
            continue

        tournament_slug = _extract_parent_tournament_slug(link)
        parent_item = _fetch_bassmaster_tournament_parent(session=session, tournament_slug=tournament_slug)
        item = parent_item or result_item

        meta = item.get('meta', {}) or {}
        start_date_raw = meta.get('bassmaster_tournament_start_date')
        if not start_date_raw:
            continue
        start_date = pd.to_datetime(start_date_raw, errors='coerce')
        if pd.isna(start_date):
            continue
        if not (start_year <= int(start_date.year) <= end_year):
            continue

        water_body, city, state, location = _get_tournament_place(meta)
        tournaments.append(
            BassmasterTournamentResult(
                tournament_slug=tournament_slug or link.rstrip('/').split('/')[-2],
                event_name=_clean_text(item.get('title', {}).get('rendered', 'Results')).replace(' – Results', '').replace(' - Results', ''),
                location=location,
                water_body=water_body,
                city=city,
                state=state,
                start_date=start_date,
                results_pdf_url=pdf_url,
            )
        )

    return sorted(tournaments, key=lambda item: (item.start_date, item.tournament_slug))


_ROW_PATTERN = re.compile(r'^(\d+)\s+(\d)(\d+)-\s*(\d+)\s+(\d)(\d+)-\s*(\d+)', re.MULTILINE)


def _weight_to_pounds(pounds: str, ounces: str) -> float:
    return int(pounds) + (int(ounces) / 16.0)


def _extract_median_weight_from_pdf(pdf_bytes: bytes) -> float:
    reader = PdfReader(BytesIO(pdf_bytes))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    weights = [
        _weight_to_pounds(match.group(3), match.group(4))
        for match in _ROW_PATTERN.finditer(text)
        if int(match.group(2)) >= 0
    ]
    if not weights:
        raise ValueError('could not extract competitor daily weights from results PDF')
    return round(float(pd.Series(weights).median()), 4)


def _extract_day_from_pdf_url(pdf_url: str) -> int:
    match = re.search(r'day[-_ ]?(\d+)', pdf_url, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 1


def _tokenize_location_text(value: str) -> set[str]:
    return {token for token in re.findall(r'[a-z0-9]+', (value or '').lower()) if len(token) >= 3}


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


def _normalize_state_code(value: str) -> str:
    cleaned = re.sub(r'[^A-Za-z]', '', str(value or '')).strip()
    if not cleaned:
        return ''
    if len(cleaned) == 2:
        return cleaned.upper()
    return _STATE_TOKEN_ALIASES.get(cleaned.lower(), '')


def _parse_rdb_table(text: str) -> pd.DataFrame:
    lines = [line for line in text.splitlines() if line and not line.startswith('#')]
    if len(lines) < 3:
        return pd.DataFrame()
    return pd.read_csv(StringIO('\n'.join([lines[0], *lines[2:]])), sep='\t', dtype=str).fillna('')


def _score_site_match(*, water_body: str, city: str, station_name: str, site_type: str) -> float:
    water_tokens = _tokenize_location_text(water_body)
    city_tokens = _tokenize_location_text(city)
    station_tokens = _tokenize_location_text(station_name)
    score = 0.0
    score += len(water_tokens & station_tokens) * 3.0
    score += len(city_tokens & station_tokens) * 1.0
    preferred_types = set(DEFAULT_SITE_TYPES)
    if site_type in preferred_types:
        score += 1.5
    return score


def _fetch_usgs_site_candidates(*, water_body: str, state: str, session: requests.Session, timeout: int = 30) -> pd.DataFrame:
    state_code = _normalize_state_code(state)
    if not water_body or not state_code:
        return pd.DataFrame()

    response = session.get(
        USGS_SITE_SERVICE_URL,
        params={
            'format': 'rdb',
            'siteStatus': 'all',
            'stateCd': state_code,
            'siteType': ','.join(DEFAULT_SITE_TYPES),
            'siteOutput': 'expanded',
            'siteName': water_body,
        },
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    if response.status_code == 404:
        return pd.DataFrame()
    response.raise_for_status()
    return _parse_rdb_table(response.text)


def suggest_bassmaster_usgs_mappings(
    *,
    start_year: int,
    end_year: int,
    output_path: Path,
    session: requests.Session | None = None,
    top_n: int = 3,
) -> pd.DataFrame:
    owned_session = session is None
    session = session or requests.Session()
    top_n = max(1, int(top_n))

    try:
        tournaments = _fetch_bassmaster_results_index(session=session, start_year=start_year, end_year=end_year)
        rows: list[dict[str, Any]] = []
        candidate_cache: dict[tuple[str, str], pd.DataFrame] = {}

        for tournament in tournaments:
            cache_key = (tournament.water_body, tournament.state)
            candidates = candidate_cache.get(cache_key)
            if candidates is None:
                candidates = _fetch_usgs_site_candidates(
                    water_body=tournament.water_body,
                    state=tournament.state,
                    session=session,
                )
                candidate_cache[cache_key] = candidates

            if candidates.empty:
                rows.append(
                    {
                        'tournament_slug': tournament.tournament_slug,
                        'event_name': tournament.event_name,
                        'water_body': tournament.water_body,
                        'city': tournament.city,
                        'state': tournament.state,
                        'location': tournament.location,
                        'candidate_rank': 1,
                        'candidate_count': 0,
                        'suggested_usgs_site_id': '',
                        'suggested_station_name': '',
                        'suggested_site_type': '',
                        'match_score': 0.0,
                        'review_status': 'needs-research',
                        'selected_usgs_site_id': '',
                        'species': DEFAULT_BASSMASTER_SPECIES,
                        'review_notes': '',
                    }
                )
                continue

            scored = candidates.copy()
            scored['match_score'] = scored.apply(
                lambda row: _score_site_match(
                    water_body=tournament.water_body,
                    city=tournament.city,
                    station_name=str(row.get('station_nm', '')),
                    site_type=str(row.get('site_tp_cd', '')),
                ),
                axis=1,
            )
            scored = scored.sort_values(['match_score', 'site_no'], ascending=[False, True]).reset_index(drop=True)
            top_candidates = scored.head(top_n).reset_index(drop=True)
            for candidate_rank, (_, candidate) in enumerate(top_candidates.iterrows(), start=1):
                rows.append(
                    {
                        'tournament_slug': tournament.tournament_slug,
                        'event_name': tournament.event_name,
                        'water_body': tournament.water_body,
                        'city': tournament.city,
                        'state': tournament.state,
                        'location': tournament.location,
                        'candidate_rank': candidate_rank,
                        'candidate_count': int(len(scored)),
                        'suggested_usgs_site_id': str(candidate.get('site_no', '')).replace('USGS-', ''),
                        'suggested_station_name': str(candidate.get('station_nm', '')),
                        'suggested_site_type': str(candidate.get('site_tp_cd', '')),
                        'match_score': float(candidate.get('match_score', 0.0) or 0.0),
                        'review_status': 'suggested' if candidate_rank == 1 else 'candidate',
                        'selected_usgs_site_id': str(candidate.get('site_no', '')).replace('USGS-', '') if candidate_rank == 1 else '',
                        'species': DEFAULT_BASSMASTER_SPECIES,
                        'review_notes': '',
                    }
                )
    finally:
        if owned_session:
            session.close()

    suggestions = pd.DataFrame(rows)
    if suggestions.empty:
        suggestions = pd.DataFrame(
            columns=[
                'tournament_slug',
                'event_name',
                'water_body',
                'city',
                'state',
                'location',
                'candidate_rank',
                'candidate_count',
                'suggested_usgs_site_id',
                'suggested_station_name',
                'suggested_site_type',
                'match_score',
                'review_status',
                'selected_usgs_site_id',
                'species',
                'review_notes',
            ]
        )
    else:
        suggestions = suggestions.sort_values(['state', 'water_body', 'tournament_slug', 'candidate_rank']).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suggestions.to_csv(output_path, index=False)
    return suggestions


def _load_mapping(mapping_path: Path | None) -> pd.DataFrame | None:
    if mapping_path is None:
        return None
    mapping = pd.read_csv(mapping_path, dtype=str).fillna('')
    if 'tournament_slug' not in mapping.columns or 'usgs_site_id' not in mapping.columns:
        raise ValueError('mapping file must include tournament_slug and usgs_site_id columns')
    if 'species' not in mapping.columns:
        mapping['species'] = DEFAULT_BASSMASTER_SPECIES
    return mapping[['tournament_slug', 'usgs_site_id', 'species']]


def _collect_bassmaster_outcomes(
    *,
    start_year: int,
    end_year: int,
    mapping_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    owned_session = session is None
    session = session or requests.Session()
    mapping = _load_mapping(mapping_path)

    try:
        tournaments = _fetch_bassmaster_results_index(session=session, start_year=start_year, end_year=end_year)
        rows: list[dict[str, Any]] = []

        for tournament in tournaments:
            response = session.get(tournament.results_pdf_url, timeout=30, headers=DEFAULT_HEADERS)
            response.raise_for_status()
            median_weight_lb = _extract_median_weight_from_pdf(response.content)
            day_number = _extract_day_from_pdf_url(tournament.results_pdf_url)
            event_date = (tournament.start_date + timedelta(days=day_number - 1)).strftime('%Y-%m-%d')
            rows.append(
                {
                    'event_id': f'{tournament.tournament_slug}-day-{day_number}',
                    'tournament_slug': tournament.tournament_slug,
                    'event_name': tournament.event_name,
                    'date': event_date,
                    'location': tournament.location,
                    'species': DEFAULT_BASSMASTER_SPECIES,
                    'median_weight_lb': median_weight_lb,
                    'baseline_signal': _seasonal_baseline_signal(event_date),
                    'results_pdf_url': tournament.results_pdf_url,
                }
            )
    finally:
        if owned_session:
            session.close()

    outcomes = pd.DataFrame(rows)
    if outcomes.empty:
        raise ValueError(f'no Bassmaster tournament result rows found for years {start_year}-{end_year}')

    if mapping is not None:
        outcomes = outcomes.merge(mapping, on='tournament_slug', how='left', suffixes=('', '_mapping'))
        outcomes['species'] = outcomes['species_mapping'].where(outcomes['species_mapping'].notna() & outcomes['species_mapping'].ne(''), outcomes['species'])
        outcomes = outcomes.drop(columns=['species_mapping'])
    if 'usgs_site_id' not in outcomes.columns:
        outcomes['usgs_site_id'] = ''

    return outcomes


def collect_historical_outcomes(
    output_path: Path,
    sample: bool = False,
    source_path: Path | None = None,
    bassmaster_years: tuple[int, int] | None = None,
    mapping_path: Path | None = None,
) -> pd.DataFrame:
    if sample:
        df = build_sample_outcomes()
        source_mode = 'sample'
    elif bassmaster_years is not None:
        start_year, end_year = bassmaster_years
        df = _collect_bassmaster_outcomes(start_year=start_year, end_year=end_year, mapping_path=mapping_path)
        source_mode = f'bassmaster:{start_year}-{end_year}'
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
