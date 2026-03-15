from __future__ import annotations

import pandas as pd
import requests

from castline.validation.assembly.dataset import assemble_validation_dataset
from castline.validation.collectors.outcomes import collect_historical_outcomes, suggest_bassmaster_usgs_mappings
from castline.validation.collectors.usgs import collect_usgs_history
from castline.validation.collectors.weather import collect_weather_history
from castline.validation.models.comparison import compare_models


def test_sample_pipeline(tmp_path):
    outcomes_path = tmp_path / 'historical_outcomes.csv'
    usgs_path = tmp_path / 'usgs_history.csv'
    weather_path = tmp_path / 'weather_history.csv'
    dataset_path = tmp_path / 'validation_dataset.csv'
    report_path = tmp_path / 'report.json'

    collect_historical_outcomes(outcomes_path, sample=True)
    collect_usgs_history(usgs_path, sample=True)
    collect_weather_history(weather_path, sample=True)
    dataset = assemble_validation_dataset(outcomes_path, usgs_path, dataset_path, weather_path=weather_path)
    summary = compare_models(dataset_path, report_path)

    assert len(dataset) >= 1
    assert 'precip_24h_mm' in dataset.columns
    assert 'weather_stability_index' in dataset.columns
    assert report_path.exists()
    assert summary.thesis_rating in {'weak', 'viable', 'strong'}


def test_weather_collection_from_source_manifest(tmp_path):
    weather_source = tmp_path / 'source_weather.csv'
    pd.DataFrame(
        [
            {
                'event_id': 'evt-001',
                'air_temp_c': 12.5,
                'pressure_mb': 1009.4,
                'wind_speed_kph': 7.2,
                'cloud_cover_pct': 41.0,
                'precip_24h_mm': 3.8,
            }
        ]
    ).to_csv(weather_source, index=False)

    normalized_weather = tmp_path / 'weather_history.csv'
    df = collect_weather_history(normalized_weather, source_path=weather_source)

    assert len(df) == 1
    row = df.iloc[0]
    assert row['event_id'] == 'evt-001'
    assert row['pressure_mb'] == 1009.4
    assert row['source_mode'] == 'csv:source_weather.csv'



def test_real_iem_weather_collection_from_outcomes_manifest(tmp_path):
    outcomes_source = tmp_path / 'source_outcomes.csv'
    pd.DataFrame(
        [
            {
                'event_id': 'evt-001',
                'event_name': 'River Open',
                'date': '2024-04-10',
                'location': 'Saginaw Bay, Saginaw, MI',
                'city': 'Saginaw',
                'state': 'MI',
                'species': 'smallmouth_bass',
                'median_weight_lb': 15.2,
                'baseline_signal': 0.48,
                'usgs_site_id': '01646500',
            }
        ]
    ).to_csv(outcomes_source, index=False)

    class FakeResponse:
        def __init__(self, *, payload=None, text=''):
            self._payload = payload
            self.text = text

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            if 'geojson/network.php' in url:
                assert params['network'] == 'MI_ASOS'
                return FakeResponse(
                    payload={
                        'features': [
                            {
                                'id': 'MBS',
                                'properties': {'sname': 'SAGINAW', 'state': 'MI'},
                                'geometry': {'coordinates': [-84.08, 43.53]},
                            },
                            {
                                'id': 'DTW',
                                'properties': {'sname': 'DETROIT', 'state': 'MI'},
                                'geometry': {'coordinates': [-83.36, 42.21]},
                            },
                        ]
                    }
                )
            if 'cgi-bin/request/asos.py' in url:
                assert params['station'] == 'MBS'
                return FakeResponse(
                    text='station,valid,lon,lat,elevation,tmpf,mslp,skyc1,skyc2,skyc3,skyc4,sknt,p01i\n'
                    'MBS,2024-04-10 00:00,-84.08,43.53,204,50.0,1009.0,SCT,,,,10.0,0.10\n'
                    'MBS,2024-04-10 12:00,-84.08,43.53,204,59.0,1011.0,BKN,,,,12.0,0.00\n'
                )
            raise AssertionError(f'unexpected URL {url}')

        def close(self):
            return None

    normalized_weather = tmp_path / 'weather_history.csv'
    df = collect_weather_history(normalized_weather, outcomes_path=outcomes_source, session=FakeSession())

    assert len(df) == 1
    row = df.iloc[0]
    assert row['event_id'] == 'evt-001'
    assert row['iem_station'] == 'MBS'
    assert round(row['air_temp_c'], 4) == round(((50.0 - 32.0) * (5.0 / 9.0) + (59.0 - 32.0) * (5.0 / 9.0)) / 2.0, 4)
    assert row['pressure_mb'] == 1010.0
    assert round(row['wind_speed_kph'], 4) == round(((10.0 * 1.852) + (12.0 * 1.852)) / 2.0, 4)
    assert row['cloud_cover_pct'] == 68.75
    assert row['precip_24h_mm'] == 2.54
    assert row['source_mode'] == 'iem_asos_api'



def test_real_usgs_collection_from_outcomes_manifest(tmp_path, monkeypatch):
    outcomes_source = tmp_path / 'source_outcomes.csv'
    pd.DataFrame(
        [
            {
                'event_id': 'evt-001',
                'event_name': 'River Open',
                'date': '2024-04-10',
                'location': 'River Reach',
                'species': 'smallmouth_bass',
                'median_weight_lb': 15.2,
                'baseline_signal': 0.48,
                'usgs_site_id': '01646500',
            }
        ]
    ).to_csv(outcomes_source, index=False)

    normalized_outcomes = tmp_path / 'historical_outcomes.csv'
    usgs_path = tmp_path / 'usgs_history.csv'
    collect_historical_outcomes(normalized_outcomes, source_path=outcomes_source)

    def fake_fetch(site_id: str, start_date: str, end_date: str, *, session=None, timeout: int = 30):
        assert site_id == '01646500'
        assert end_date == '2024-04-10'
        return pd.DataFrame(
            [
                {'date': pd.Timestamp('2024-04-09'), 'water_temp_c': 11.0, 'discharge_cfs': 100.0, 'gage_height_ft': 2.2, 'site_id': site_id},
                {'date': pd.Timestamp('2024-04-10'), 'water_temp_c': 12.5, 'discharge_cfs': 110.0, 'gage_height_ft': 2.4, 'site_id': site_id},
            ]
        )

    monkeypatch.setattr('castline.validation.collectors.usgs.fetch_usgs_daily_values', fake_fetch)

    df = collect_usgs_history(usgs_path, outcomes_path=normalized_outcomes, lookback_days=7)

    assert len(df) == 1
    row = df.iloc[0]
    assert row['event_id'] == 'evt-001'
    assert row['site_id'] == 'USGS-01646500'
    assert row['water_temp_c'] == 12.5
    assert row['temp_delta_24h_c'] == 1.5
    assert round(row['flow_delta_24h_pct'], 4) == 10.0
    assert row['source_mode'] == 'usgs_daily_values'


def test_bassmaster_mapping_suggestions_from_official_results_pages(tmp_path, monkeypatch):
    tournament_payload = [
        {
            'link': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
            'title': {'rendered': '2024 Test Open - Results'},
            'content': {
                'rendered': '<h2><a href="https://example.com/test-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
            },
            'meta': {
                'bassmaster_tournament_start_date': '2024-06-06',
                'bassmaster_tournament_body_of_water': 'Saginaw Bay',
                'bassmaster_tournament_city': 'Saginaw',
                'bassmaster_tournament_state': 'Michigan',
            },
        }
    ]

    usgs_rdb = """# ----------------------------------
# Data provided for test
agency_cd	site_no	station_nm	site_tp_cd
5s	15s	50s	7s
USGS	04157005	SAGINAW RIVER AT SAGINAW, MI	ST
USGS	04156800	BAY COUNTY LAKE MONITOR AT SAGINAW BAY	LK
"""

    class FakeResponse:
        def __init__(self, payload=None, content: bytes = b'', text: str = ''):
            self._payload = payload
            self.content = content
            self.text = text

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            if 'wp-json/wp/v2/tournament' in url:
                if params and params.get('page') == 1:
                    return FakeResponse(payload=tournament_payload)
                return FakeResponse(payload=[])
            if 'waterservices.usgs.gov/nwis/site/' in url:
                assert params['stateCd'] == 'MI'
                assert params['siteName'] == 'Saginaw Bay'
                return FakeResponse(text=usgs_rdb)
            raise AssertionError(f'unexpected URL {url}')

        def close(self):
            return None

    monkeypatch.setattr('castline.validation.collectors.outcomes.requests.Session', lambda: FakeSession())

    output_path = tmp_path / 'mapping_suggestions.csv'
    df = suggest_bassmaster_usgs_mappings(
        start_year=2024,
        end_year=2024,
        output_path=output_path,
        top_n=2,
    )

    assert len(df) == 2
    top_row = df.iloc[0]
    second_row = df.iloc[1]
    assert top_row['tournament_slug'] == '2024-test-open'
    assert top_row['state'] == 'Michigan'
    assert top_row['candidate_rank'] == 1
    assert top_row['suggested_usgs_site_id'] == '04156800'
    assert top_row['suggested_site_type'] == 'LK'
    assert top_row['review_status'] == 'suggested'
    assert top_row['selected_usgs_site_id'] == '04156800'
    assert top_row['candidate_count'] == 2
    assert second_row['candidate_rank'] == 2
    assert second_row['suggested_usgs_site_id'] == '04157005'
    assert second_row['review_status'] == 'candidate'
    assert output_path.exists()



def test_bassmaster_results_index_stops_cleanly_on_wordpress_page_overflow(tmp_path, monkeypatch):
    tournament_payload = [
        {
            'link': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
            'title': {'rendered': '2024 Test Open - Results'},
            'content': {
                'rendered': '<h2><a href="https://example.com/test-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
            },
            'meta': {
                'bassmaster_tournament_start_date': '2024-06-06',
                'bassmaster_tournament_body_of_water': 'Saginaw Bay',
                'bassmaster_tournament_city': 'Saginaw',
                'bassmaster_tournament_state': 'MI',
            },
        }
    ]

    class FakeResponse:
        def __init__(self, payload=None, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            if 'wp-json/wp/v2/tournament' not in url:
                raise AssertionError(f'unexpected URL {url}')
            if params and params.get('page') == 1:
                return FakeResponse(payload=tournament_payload)
            return FakeResponse(payload=None, status_code=400)

        def close(self):
            return None

    monkeypatch.setattr('castline.validation.collectors.outcomes.requests.Session', lambda: FakeSession())
    monkeypatch.setattr(
        'castline.validation.collectors.outcomes._fetch_usgs_site_candidates',
        lambda **kwargs: pd.DataFrame(
            [
                {
                    'site_no': '04156800',
                    'station_nm': 'BAY COUNTY LAKE MONITOR AT SAGINAW BAY',
                    'site_tp_cd': 'LK',
                }
            ]
        ),
    )

    output_path = tmp_path / 'mapping_suggestions.csv'
    df = suggest_bassmaster_usgs_mappings(
        start_year=2024,
        end_year=2024,
        output_path=output_path,
        top_n=1,
    )

    assert len(df) == 1
    assert df.iloc[0]['tournament_slug'] == '2024-test-open'


def test_bassmaster_collection_from_official_results_pages(tmp_path, monkeypatch):
    mapping_path = tmp_path / 'bassmaster_mapping.csv'
    pd.DataFrame(
        [
            {
                'tournament_slug': '2024-test-open',
                'usgs_site_id': '01646500',
                'species': 'smallmouth_bass',
            }
        ]
    ).to_csv(mapping_path, index=False)

    tournament_payload = [
        {
            'link': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
            'title': {'rendered': '2024 Test Open - Results'},
            'content': {
                'rendered': '<h2><a href="https://example.com/test-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
            },
            'meta': {
                'bassmaster_tournament_start_date': '2024-06-06',
                'bassmaster_tournament_body_of_water': 'Saginaw Bay',
                'bassmaster_tournament_city': 'Saginaw',
                'bassmaster_tournament_state': 'MI',
            },
        }
    ]

    pdf_text = (
        '2024 Test Open\n'
        'STANDINGS BOATER DAY 2\n'
        '1  519- 2 1035- 2 5 10Alpha Team 250.00\n'
        '2  515- 0 1031- 0 5 10Beta Team 249.00\n'
        '3  510- 8 1028- 4 5 10Gamma Team 248.00\n'
    )

    class FakeResponse:
        def __init__(self, payload=None, content: bytes = b''):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            if 'wp-json/wp/v2/tournament' in url:
                if params and params.get('page') == 1:
                    return FakeResponse(payload=tournament_payload)
                return FakeResponse(payload=[])
            if url == 'https://example.com/test-open-day-2.pdf':
                return FakeResponse(content=b'%PDF-1.3 fake')
            raise AssertionError(f'unexpected URL {url}')

        def close(self):
            return None

    class FakePage:
        def extract_text(self):
            return pdf_text

    class FakePdfReader:
        def __init__(self, _buffer):
            self.pages = [FakePage()]

    monkeypatch.setattr('castline.validation.collectors.outcomes.requests.Session', lambda: FakeSession())
    monkeypatch.setattr('castline.validation.collectors.outcomes.PdfReader', FakePdfReader)

    output_path = tmp_path / 'historical_outcomes.csv'
    df = collect_historical_outcomes(
        output_path,
        bassmaster_years=(2024, 2024),
        mapping_path=mapping_path,
    )

    assert len(df) == 1
    row = df.iloc[0]
    assert row['event_id'] == '2024-test-open-day-2'
    assert row['date'] == '2024-06-07'
    assert row['location'] == 'Saginaw Bay, Saginaw, MI'
    assert row['species'] == 'smallmouth_bass'
    assert round(row['median_weight_lb'], 4) == 15.0
    assert row['usgs_site_id'] == '01646500'
    assert row['source_mode'] == 'bassmaster:2024-2024'
