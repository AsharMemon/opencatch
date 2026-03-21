from __future__ import annotations

import math
import re
import sys
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
INTERSTATE_SEARCH_STATE_OVERRIDES: dict[tuple[str, str], tuple[str, ...]] = {
    ('clarks hill reservoir', 'GA'): ('SC',),
    ('lake hartwell', 'SC'): ('GA',),
    ('kentucky lake', 'TN'): ('KY',),
    ('lake eufaula', 'AL'): ('GA',),
    ('toledo bend reservoir', 'TX'): ('LA',),
    ('toledo bend', 'TX'): ('LA',),
    ('pickwick lake', 'TN'): ('AL',),
    ('pickwick lake', 'AL'): ('TN',),
    ('lake seminole', 'GA'): ('FL',),
    ('lake seminole', 'FL'): ('GA',),
    ('ross barnett reservoir', 'MS'): (),
    ('wheeler lake', 'AL'): (),
    ('table rock lake', 'MO'): ('AR',),
    ('bull shoals lake', 'AR'): ('MO',),
    ('st. lawrence river', 'NY'): (),
    ('lake champlain', 'NY'): ('VT',),
    ('lake champlain', 'VT'): ('NY',),
    ('lake erie', 'OH'): ('NY', 'PA', 'MI'),
    ('santee cooper', 'SC'): (),
    ('potomac river', 'MD'): ('VA', 'DC'),
    ('potomac river', 'VA'): ('MD', 'DC'),
    ('james river', 'VA'): (),
    ('lake amistad', 'TX'): (),
    ('sabine river', 'TX'): ('LA',),
    ('sabine river', 'LA'): ('TX',),
}
WATER_BODY_ALIASES: dict[str, tuple[str, ...]] = {
    'clarks hill reservoir': ('Clarks Hill', 'Strom Thurmond', 'Thurmond Lake', 'Savannah River'),
    'lake hartwell': ('Hartwell',),
    'sam rayburn reservoir': ('Sam Rayburn',),
    'grand lake': ("Lake O' the Cherokees", 'Lake O', 'Neosho River', 'Grand Lake'),
    'lake eufaula': ('Walter F. George', 'Walter F George Reservoir', 'Chattahoochee River'),
    'harris chain': ('Lake Harris', 'Lake Eustis', 'Apopka', 'Beauclair'),
    'kentucky lake': ('Kentucky Dam', 'Barkley', 'Tennessee River'),
    'toledo bend reservoir': ('Toledo Bend', 'Sabine River'),
    'lake guntersville': ('Guntersville', 'Tennessee River'),
    'wheeler lake': ('Wheeler', 'Tennessee River'),
    'pickwick lake': ('Pickwick', 'Tennessee River'),
    'chickamauga lake': ('Chickamauga', 'Tennessee River'),
    'lake okeechobee': ('Okeechobee',),
    'st. johns river': ('St Johns', 'Saint Johns'),
    'table rock lake': ('Table Rock', 'White River'),
    'bull shoals lake': ('Bull Shoals', 'White River'),
    'beaver lake': ('Beaver',),
    'lake dardanelle': ('Dardanelle', 'Arkansas River'),
    'lake fork': ('Fork',),
    'lake seminole': ('Seminole', 'Flint River', 'Chattahoochee'),
    'santee cooper': ('Santee', 'Cooper', 'Moultrie', 'Marion'),
    'ross barnett reservoir': ('Ross Barnett', 'Barnett', 'Pearl River'),
    'potomac river': ('Potomac',),
    'james river': ('James',),
    'st. lawrence river': ('St Lawrence', 'Saint Lawrence'),
    'lake champlain': ('Champlain',),
    'oneida lake': ('Oneida',),
    'cayuga lake': ('Cayuga',),
    'lake st. clair': ('St Clair', 'Saint Clair'),
    'lake erie': ('Erie',),
    'lake norman': ('Norman',),
    'neely henry lake': ('Neely Henry', 'Coosa River'),
    'lay lake': ('Lay', 'Coosa River'),
    'logan martin lake': ('Logan Martin', 'Coosa River'),
    'lewis smith lake': ('Smith Lake', 'Lewis Smith'),
    'lake toho': ('Tohopekaliga', 'Toho'),
    'kissimmee chain': ('Kissimmee', 'Lake Kissimmee'),
    'lake murray': ('Murray', 'Saluda River'),
    'lake amistad': ('Amistad',),
    'sabine river': ('Sabine',),
    'chesapeake bay': ('Chesapeake',),
    'false river': ('False River',),
    'lake conroe': ('Conroe',),
    'sam houston lake': ('Lake Livingston', 'Livingston', 'Trinity River'),
    'lake texoma': ('Texoma', 'Red River'),
    'tenkiller lake': ('Tenkiller', 'Illinois River'),
    'fort gibson lake': ('Fort Gibson',),
    'delta': ('California Delta', 'Sacramento River', 'San Joaquin'),
    'clear lake': ('Clear Lake',),
    'lake havasu': ('Havasu',),
    'lake mead': ('Mead',),
    'lake powell': ('Powell',),
}
# Pre-researched USGS gauge IDs for major B.A.S.S. and MLF tournament lakes.
# These bypass the USGS site search API and provide reliable gauge matches.
KNOWN_LAKE_GAUGES: dict[str, str] = {
    # Alabama
    'lake guntersville': '03574500',          # Tennessee River at Guntersville
    'wheeler lake': '03572110',               # Tennessee River at Wheeler Dam
    'pickwick lake': '03592718',              # Pickwick Dam tailwater
    'neely henry lake': '02401390',           # Coosa River near Neely Henry Dam
    'lay lake': '02407000',                   # Coosa River near Lay Dam
    'logan martin lake': '02405500',          # Coosa River at Logan Martin Dam
    'lewis smith lake': '02450250',           # Sipsey Fork near Grayson
    'lake eufaula': '02343940',              # Chattahoochee River below Eufaula Dam
    # Arkansas
    'beaver lake': '07048600',               # White River near Beaver
    'bull shoals lake': '07054500',          # Bull Shoals Dam outflow
    'lake dardanelle': '07258000',           # Arkansas River at Dardanelle
    'lake ouachita': '07360200',             # Ouachita River near Buckville
    'arkansas river': '07194500',            # Arkansas River near Muskogee
    # California
    'delta': '11447650',                     # Sacramento River at Freeport
    'clear lake': '11450000',               # Clear Lake near Lakeport
    # Florida
    'lake okeechobee': '02274010',           # Okeechobee canal near Moore Haven
    'harris chain': '02237700',              # Palatlakaha River at Cherry Lake
    'st. johns river': '02232400',           # St. Johns River near Deland
    'lake toho': '02262900',                 # Tohopekaliga outflow
    'kissimmee chain': '02267000',           # Kissimmee River near Okeechobee
    # Georgia / South Carolina
    'clarks hill reservoir': '02196000',     # Stevens Creek near Clarks Hill
    'lake hartwell': '02186000',             # Keowee River near Newry
    'lake seminole': '02357000',             # Flint River at Bainbridge
    'santee cooper': '02171500',             # Santee River near Pineville
    # Kentucky / Tennessee
    'kentucky lake': '03282000',             # Kentucky River at Lock 10
    'chickamauga lake': '03566500',          # Chickamauga Creek near Chattanooga
    'douglas lake': '03467609',              # French Broad River near Douglas Dam (has daily values)
    'cherokee lake': '03465500',             # Nolichucky River near Morristown
    'norris lake': '03532000',               # Clinch River near Norris
    'dale hollow lake': '03416000',          # Obey River near Byrdstown
    'fort loudoun lake': '03495500',         # Tennessee River near Knoxville
    'watts bar lake': '03540500',            # Clinch River above Tazewell
    'center hill lake': '03419500',          # Caney Fork near Cookeville
    'old hickory lake': '03425413',          # Cumberland River at Hendersonville
    'percy priest lake': '03431599',         # Percy Priest Dam outflow
    # Michigan
    'saginaw bay': '04157060',               # Saginaw River near Essexville
    'lake st. clair': '04159492',            # Clinton River at Mt. Clemens
    # Mississippi
    'ross barnett reservoir': '02485600',    # Pearl River near Jackson
    'mississippi river': '05344500',         # Mississippi River at Prescott
    'grenada lake': '07285500',              # Yalobusha River near Grenada
    # Missouri
    'table rock lake': '07053810',           # James River at Table Rock Lake
    'lake of the ozarks': '06926000',        # Osage River near Bagnell
    'truman lake': '06918000',               # Osage River near Schell City
    'stockton lake': '06918070',             # Sac River near Stockton
    # New York / Vermont
    'lake champlain': '04294413',            # Otter Creek at Middlebury
    'oneida lake': '04245840',               # Oneida Lake at Brewerton
    'cayuga lake': '04232730',               # Cayuga Inlet near Ithaca
    'st. lawrence river': '04264331',        # St. Lawrence River at Ogdensburg
    'lake erie': '04213500',                 # Cattaraugus Creek near Gowanda
    # North Carolina
    'lake norman': '02124000',               # Rocky River near Norwood
    'jordan lake': '02098206',               # Haw River near Bynum
    'falls lake': '02087570',                # Neuse River near Falls
    'high rock lake': '02120780',            # Yadkin River at High Rock
    # Ohio
    'mosquito lake': '03094600',             # Mosquito Creek near Cortland
    # Oklahoma
    'grand lake': '07185000',                # Spring River near Quapaw (near Grand Lake)
    'tenkiller lake': '07196500',            # Illinois River near Tahlequah
    'fort gibson lake': '07193000',          # Neosho River near Wagoner
    'lake texoma': '07332500',               # Red River at Denison Dam
    # South Carolina
    'lake murray': '02168500',               # Saluda River near Columbia
    'winyah bay': '02135000',                # Little Pee Dee River at Galivants Ferry
    'lake wylie': '02146000',                # Catawba River near Rock Hill
    # Tennessee
    'old hickory lake': '03425413',          # Cumberland River
    # Texas
    'sam rayburn reservoir': '08039300',     # Angelina River near Sam Rayburn
    'toledo bend reservoir': '08028500',     # Sabine River near Bon Wier
    'lake fork': '08018500',                 # Sabine River near Mineola
    'lake conroe': '08068000',               # West Fork San Jacinto River
    'lake amistad': '08449400',              # Devils River at Pafford Crossing
    'falcon lake': '08459000',               # Rio Grande below Falcon Dam
    'lake travis': '08154700',               # Bull Creek near Austin
    'lake ray roberts': '03044000',          # Elm Fork Trinity River near Pilot Point
    # Virginia / Maryland
    'james river': '02035000',               # James River at Cartersville
    'potomac river': '01646500',             # Potomac River near Wash DC
    'chesapeake bay': '01491000',             # Choptank River near Greensboro
    # Wisconsin
    'mississippi river la crosse': '05344500',  # Mississippi River at Prescott
    'sturgeon bay': '04085200',              # Fox River at Rapide Croche Dam
    # Pennsylvania
    'delaware river': '01467200',            # Delaware River at Penn's Landing, Philadelphia
    # Arizona
    'lake havasu': '09427500',               # Lake Havasu near Parker Dam
    # South Dakota
    'lake oahe': '06440000',                 # Missouri River at Pierre
    # Georgia
    'lake lanier': '02334430',               # Chattahoochee River at Buford Dam
    # North Carolina
    'pasquotank river': '0204382800',        # Pasquotank River near South Mills
    'albemarle sound': '0204382800',         # Same as Pasquotank
}
# Aliases: some tournament locations use different names for the same lake
_GAUGE_ALIAS_MAP: dict[str, str] = {
    'lake o\' the cherokees': 'grand lake',
    'neosho river': 'grand lake',
    'thurmond lake': 'clarks hill reservoir',
    'strom thurmond': 'clarks hill reservoir',
    'savannah river': 'clarks hill reservoir',
    'walter f. george': 'lake eufaula',
    'walter f george reservoir': 'lake eufaula',
    'tohopekaliga': 'lake toho',
    'lake livingston': 'sam houston lake',
    'barkley': 'kentucky lake',
    'tennessee river guntersville': 'lake guntersville',
    'tennessee river wheeler': 'wheeler lake',
    'coosa river': 'neely henry lake',
    'sabine river': 'toledo bend reservoir',
    'tennessee river': 'fort loudoun lake',
    'california delta': 'delta',
    'sacramento river': 'delta',
    'white river': 'table rock lake',
}


def _auto_resolve_usgs_site(water_body: str) -> str:
    """Try to resolve a USGS gauge ID from KNOWN_LAKE_GAUGES using the water body name."""
    if not water_body:
        return ''
    normalized = ' '.join(str(water_body).split()).lower()

    # Direct match
    if normalized in KNOWN_LAKE_GAUGES:
        return KNOWN_LAKE_GAUGES[normalized]

    # Alias match
    if normalized in _GAUGE_ALIAS_MAP:
        canonical = _GAUGE_ALIAS_MAP[normalized]
        if canonical in KNOWN_LAKE_GAUGES:
            return KNOWN_LAKE_GAUGES[canonical]

    # Strip common suffixes and try again
    for suffix in (' reservoir', ' lake', ' river', ' chain', ' bay'):
        if normalized.endswith(suffix):
            stripped = normalized[:-len(suffix)].strip()
            for key in KNOWN_LAKE_GAUGES:
                if stripped in key or key.startswith(stripped):
                    return KNOWN_LAKE_GAUGES[key]

    # Prefix match (e.g., "lake guntersville" matches "guntersville")
    for key, gauge_id in KNOWN_LAKE_GAUGES.items():
        key_words = set(key.split())
        body_words = set(normalized.split()) - {'lake', 'reservoir', 'river', 'the', 'of'}
        if body_words and body_words & key_words:
            return gauge_id

    return ''


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
    # Try /tournament/2024-... pattern
    match = re.search(r'/tournament/(\d{4})-', link)
    if match:
        return int(match.group(1))
    # Try any 4-digit year in the URL
    match = re.search(r'/(\d{4})[-/]', link)
    if match:
        year = int(match.group(1))
        if 2010 <= year <= 2030:
            return year
    return None


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
_INLINE_ROW_PATTERN = re.compile(r'(?:^|\b)(\d{1,3})\s+(\d{2,3}-\s*\d{1,2})\s+(\d{2,4}-\s*\d{1,2})')
_DENSE_ROW_PATTERN = re.compile(r'(?<=[A-Z]{2}\s)(\d{3,4})-\s*(\d{1,2})\s+(\d{3,5})-\s*(\d{1,2})(?=\s+\d+\s+\d+\s+\d+|\s+\d+[A-Z]|$)')


def _weight_to_pounds(pounds: str, ounces: str) -> float:
    return int(pounds) + (int(ounces) / 16.0)


def _parse_dense_weight_token(token: str) -> tuple[int, int]:
    cleaned = str(token).strip()
    if len(cleaned) == 3:
        return int(cleaned[0]), int(cleaned[1:])
    if len(cleaned) == 4:
        return int(cleaned[:2]), int(cleaned[2:])
    raise ValueError(f'unexpected dense weight token: {token}')


def _extract_weight_candidates(text: str) -> list[float]:
    weights = [
        _weight_to_pounds(match.group(3), match.group(4))
        for match in _ROW_PATTERN.finditer(text)
        if int(match.group(2)) >= 0
    ]
    if weights:
        return weights

    compact_text = re.sub(r'\s+', ' ', text)
    weights = []
    for match in _INLINE_ROW_PATTERN.finditer(compact_text):
        today_token = match.group(2)
        fish_count = int(today_token[0])
        pounds_part, ounces_part = today_token[1:].split('-', 1)
        if fish_count >= 0:
            weights.append(_weight_to_pounds(pounds_part.strip(), ounces_part.strip()))
    if weights:
        return weights

    dense_weights: list[float] = []
    for match in _DENSE_ROW_PATTERN.finditer(compact_text):
        fish_count, pounds = _parse_dense_weight_token(match.group(1))
        if fish_count >= 0:
            dense_weights.append(_weight_to_pounds(str(pounds), match.group(2)))
    return dense_weights


def _extract_median_weight_from_pdf(pdf_bytes: bytes) -> float:
    reader = PdfReader(BytesIO(pdf_bytes))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    weights = _extract_weight_candidates(text)
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


def _build_usgs_site_queries(water_body: str) -> list[str]:
    normalized = ' '.join(str(water_body or '').split())
    if not normalized:
        return []

    candidates = [normalized]
    lowered = normalized.lower()
    if lowered.startswith('lake '):
        candidates.append(normalized[5:])
    if lowered.endswith(' reservoir'):
        candidates.append(normalized[:-10])
    if lowered.endswith(' lake'):
        candidates.append(normalized[:-5])

    for alias in WATER_BODY_ALIASES.get(lowered, ()): 
        candidates.append(alias)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = ' '.join(str(candidate or '').split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _build_usgs_search_states(water_body: str, state: str) -> list[str]:
    primary = _normalize_state_code(state)
    if not primary:
        return []
    normalized_water_body = ' '.join(str(water_body or '').split()).lower()
    states = [primary, *INTERSTATE_SEARCH_STATE_OVERRIDES.get((normalized_water_body, primary), ())]
    deduped: list[str] = []
    seen: set[str] = set()
    for code in states:
        normalized_code = _normalize_state_code(code)
        if not normalized_code or normalized_code in seen:
            continue
        seen.add(normalized_code)
        deduped.append(normalized_code)
    return deduped


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


def _water_body_query_variants(*, water_body: str, city: str = '') -> list[str]:
    cleaned_water_body = re.sub(r'\s+', ' ', str(water_body or '').strip())
    if not cleaned_water_body:
        return []

    variants: list[str] = [cleaned_water_body]
    lowered = cleaned_water_body.lower()

    alias_values = WATER_BODY_ALIASES.get(lowered, ())
    variants.extend(alias_values)

    stripped = re.sub(r'\b(Lake|Reservoir)\b', '', cleaned_water_body, flags=re.IGNORECASE)
    stripped = re.sub(r'\s+', ' ', stripped).strip(' ,')
    if stripped and stripped.lower() != lowered:
        variants.append(stripped)

    if city:
        variants.append(f'{cleaned_water_body} {city}'.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for value in variants:
        normalized = re.sub(r'\s+', ' ', str(value or '').strip())
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _fetch_usgs_site_candidates(*, water_body: str, state: str, session: requests.Session, city: str = '', timeout: int = 30) -> pd.DataFrame:
    search_states = _build_usgs_search_states(water_body=water_body, state=state)
    if not water_body or not search_states:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    seen_site_ids: set[str] = set()
    for state_code in search_states:
        for query in _water_body_query_variants(water_body=water_body, city=city):
            response = session.get(
                USGS_SITE_SERVICE_URL,
                params={
                    'format': 'rdb',
                    'siteStatus': 'all',
                    'stateCd': state_code,
                    'siteType': ','.join(DEFAULT_SITE_TYPES),
                    'siteOutput': 'expanded',
                    'siteName': query,
                },
                headers=DEFAULT_HEADERS,
                timeout=timeout,
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            parsed = _parse_rdb_table(response.text)
            if parsed.empty:
                continue

            parsed = parsed.copy()
            parsed['query_site_name'] = query
            parsed['query_state'] = state_code
            parsed['site_no'] = parsed['site_no'].astype(str).str.replace('USGS-', '', regex=False).str.strip()
            parsed = parsed.loc[~parsed['site_no'].isin(seen_site_ids)].reset_index(drop=True)
            if parsed.empty:
                continue

            seen_site_ids.update(parsed['site_no'])
            frames.append(parsed)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=['site_no']).reset_index(drop=True)
    return combined


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
                    city=tournament.city,
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


def export_curated_bassmaster_mappings(*, review_sheet_path: Path, output_path: Path) -> pd.DataFrame:
    mapping = pd.read_csv(review_sheet_path, dtype=str).fillna('')
    curated = _extract_mapping_from_review_sheet(mapping)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    curated.to_csv(output_path, index=False)
    return curated


def _extract_mapping_from_review_sheet(mapping: pd.DataFrame) -> pd.DataFrame:
    if 'tournament_slug' not in mapping.columns:
        raise ValueError('review-sheet mapping file must include tournament_slug')

    working = mapping.copy().fillna('')
    if 'species' not in working.columns:
        working['species'] = DEFAULT_BASSMASTER_SPECIES
    if 'selected_usgs_site_id' not in working.columns:
        working['selected_usgs_site_id'] = ''
    if 'suggested_usgs_site_id' not in working.columns:
        working['suggested_usgs_site_id'] = ''
    if 'review_status' not in working.columns:
        working['review_status'] = ''
    if 'candidate_rank' not in working.columns:
        working['candidate_rank'] = ''
    if 'recommended_by_coverage' not in working.columns:
        working['recommended_by_coverage'] = ''
    if 'usable_event_count' not in working.columns:
        working['usable_event_count'] = ''

    working['review_status'] = working['review_status'].astype(str).str.strip().str.lower()
    working['selected_usgs_site_id'] = working['selected_usgs_site_id'].astype(str).str.replace('USGS-', '', regex=False).str.strip()
    working['suggested_usgs_site_id'] = working['suggested_usgs_site_id'].astype(str).str.replace('USGS-', '', regex=False).str.strip()
    working['candidate_rank'] = pd.to_numeric(working['candidate_rank'], errors='coerce')
    working['recommended_by_coverage'] = working['recommended_by_coverage'].astype(str).str.strip().str.lower().isin({'true', '1', 'yes'})
    working['usable_event_count'] = pd.to_numeric(working['usable_event_count'], errors='coerce').fillna(0).astype(int)

    coverage_rows = working.loc[
        working['selected_usgs_site_id'].eq('')
        & working['recommended_by_coverage']
        & working['usable_event_count'].gt(0)
        & working['suggested_usgs_site_id'].ne('')
    ].copy()
    if not coverage_rows.empty:
        coverage_rows['selected_usgs_site_id'] = coverage_rows['suggested_usgs_site_id']
        coverage_rows['review_status'] = coverage_rows['review_status'].where(
            coverage_rows['review_status'].ne(''),
            'coverage-recommended',
        )

    approved_statuses = {'approved', 'selected', 'confirmed', 'locked', 'coverage-recommended'}
    selected_rows = working.loc[working['selected_usgs_site_id'].ne('')].copy()
    if not coverage_rows.empty:
        selected_rows = pd.concat([selected_rows, coverage_rows], ignore_index=True)
    approved_rows = working.loc[
        working['selected_usgs_site_id'].eq('')
        & working['review_status'].isin(approved_statuses)
        & working['suggested_usgs_site_id'].ne('')
    ].copy()
    if not approved_rows.empty:
        approved_rows['selected_usgs_site_id'] = approved_rows['suggested_usgs_site_id']
        selected_rows = pd.concat([selected_rows, approved_rows], ignore_index=True)

    if selected_rows.empty:
        raise ValueError(
            'review-sheet mapping file does not contain any selected mappings. '
            'Populate selected_usgs_site_id or mark approved rows with a suggested_usgs_site_id.'
        )

    selected_rows['selection_priority'] = 0
    selected_rows.loc[selected_rows['recommended_by_coverage'], 'selection_priority'] += 100
    selected_rows.loc[selected_rows['usable_event_count'].gt(0), 'selection_priority'] += 10
    selected_rows.loc[selected_rows['review_status'].eq('coverage-recommended'), 'selection_priority'] += 5
    selected_rows['candidate_rank'] = pd.to_numeric(selected_rows['candidate_rank'], errors='coerce').fillna(9999)

    selected_rows = selected_rows.sort_values(
        ['tournament_slug', 'selection_priority', 'usable_event_count', 'candidate_rank'],
        ascending=[True, False, False, True],
    )
    selected_rows = selected_rows.drop_duplicates(subset=['tournament_slug'], keep='first')
    selected_rows = selected_rows.rename(columns={'selected_usgs_site_id': 'usgs_site_id'})
    return selected_rows[['tournament_slug', 'usgs_site_id', 'species']].reset_index(drop=True)


def _load_mapping(mapping_path: Path | None) -> pd.DataFrame | None:
    if mapping_path is None:
        return None
    mapping = pd.read_csv(mapping_path, dtype=str).fillna('')
    if 'tournament_slug' not in mapping.columns:
        raise ValueError('mapping file must include tournament_slug')
    if 'usgs_site_id' in mapping.columns:
        if 'species' not in mapping.columns:
            mapping['species'] = DEFAULT_BASSMASTER_SPECIES
        return mapping[['tournament_slug', 'usgs_site_id', 'species']]
    if 'selected_usgs_site_id' in mapping.columns or 'suggested_usgs_site_id' in mapping.columns:
        return _extract_mapping_from_review_sheet(mapping)
    raise ValueError(
        'mapping file must include tournament_slug plus either usgs_site_id, '
        'or review-sheet columns like selected_usgs_site_id/suggested_usgs_site_id'
    )


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
            try:
                response = session.get(tournament.results_pdf_url, timeout=30, headers=DEFAULT_HEADERS)
                response.raise_for_status()
            except Exception as exc:
                print(
                    f"warning: skipping Bassmaster PDF download for {tournament.tournament_slug} ({tournament.results_pdf_url}): {exc}",
                    file=sys.stderr,
                )
                continue
            try:
                median_weight_lb = _extract_median_weight_from_pdf(response.content)
            except Exception as exc:
                print(
                    f"warning: skipping Bassmaster PDF parse for {tournament.tournament_slug} ({tournament.results_pdf_url}): {exc}",
                    file=sys.stderr,
                )
                continue
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

    # Auto-resolve USGS gauges for unmapped tournaments using KNOWN_LAKE_GAUGES
    unmapped_mask = outcomes['usgs_site_id'].isna() | outcomes['usgs_site_id'].astype(str).str.strip().eq('')
    if unmapped_mask.any():
        for idx in outcomes.index[unmapped_mask]:
            location = str(outcomes.at[idx, 'location'])
            # Try to extract water body from location (first part before comma)
            water_body_part = location.split(',')[0].strip() if ',' in location else location
            resolved = _auto_resolve_usgs_site(water_body_part)
            if resolved:
                outcomes.at[idx, 'usgs_site_id'] = resolved
                print(
                    f"auto-resolved USGS gauge for {outcomes.at[idx, 'tournament_slug']}: "
                    f"{water_body_part} → {resolved}",
                    file=sys.stderr,
                )

    # Drop events that still have no gauge mapping
    mapped_mask = outcomes['usgs_site_id'].notna() & outcomes['usgs_site_id'].astype(str).str.strip().ne('')
    skipped = outcomes.loc[~mapped_mask, 'tournament_slug'].dropna().astype(str).unique().tolist()
    if skipped:
        print(
            f'warning: skipping {len(skipped)} unmapped tournaments: ' + ', '.join(sorted(skipped)),
            file=sys.stderr,
        )
    outcomes = outcomes.loc[mapped_mask].reset_index(drop=True)

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
