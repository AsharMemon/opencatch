from __future__ import annotations

import pandas as pd
import requests

from castline.validation.assembly.dataset import assemble_validation_dataset
from castline.validation.collectors.outcomes import (
    _extract_weight_candidates,
    _fetch_usgs_site_candidates,
    _water_body_query_variants,
    collect_historical_outcomes,
    export_curated_bassmaster_mappings,
    suggest_bassmaster_usgs_mappings,
)
from castline.validation.collectors.usgs import collect_usgs_history, evaluate_usgs_mapping_candidates
from castline.validation.collectors.weather import collect_weather_history
from castline.validation.models.comparison import compare_models


def test_inline_bassmaster_weight_extraction_handles_compact_pdf_text():
    text = (
        'STANDINGS BOATER DAY 2 '
        '1  520-10 1043-12 5 10Peyton Harris - Dalton HeadUniversity of Montevallo 250.00 '
        '2  521- 7 1042- 7 5 10Bryce Dimauro - Tripp BerlinskyBryan College 249.00 '
        '3  519-13 1041- 5 5 10Elliot Wielgopolski - Aaron JagdfeldAdrian College 248.00'
    )

    weights = _extract_weight_candidates(text)

    assert weights == [20.625, 21.4375, 19.8125]



def test_inline_bassmaster_weight_extraction_handles_dense_bass_nation_pdf_text():
    text = (
        '2024 Mercury B.A.S.S. Nation Qualifier at Arkansas River presented by Lowrance '
        'Today\'s ActivityNameCity, State# FishLbs - OzAccumulativeLbs - Oz# Live# FishPTS# Live '
        '1Chris JohnsonFarmington, AR 513-14 1551- 3 0 5 15 '
        '2Blake CappsMuskogee, OK 516-13 1549-10 0 5 15 '
        '3Jeremy NorrisAma, LA 514- 4 1546-15 0 5 15'
    )

    weights = _extract_weight_candidates(text)

    assert weights == [13.875, 16.8125, 14.25]



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
    assert summary.thesis_rating in {'weak', 'viable', 'strong', 'insufficient_data'}
    if summary.thesis_rating == 'insufficient_data':
        assert summary.withheld_reason
        assert summary.usable_row_count <= summary.row_count


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



def test_real_iem_weather_collection_falls_back_to_next_station_when_top_match_has_no_rows(tmp_path):
    outcomes_source = tmp_path / 'source_outcomes.csv'
    pd.DataFrame(
        [
            {
                'event_id': 'evt-001',
                'event_name': 'Reservoir Open',
                'date': '2024-02-02',
                'location': 'Clarks Hill Reservoir, Columbia County, GA',
                'city': 'Columbia County',
                'state': 'GA',
                'species': 'black_bass',
                'median_weight_lb': 12.5,
                'baseline_signal': 0.42,
                'usgs_site_id': '02197000',
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
                assert params['network'] == 'GA_ASOS'
                return FakeResponse(
                    payload={
                        'features': [
                            {
                                'id': '19A',
                                'properties': {'sname': 'AUGUSTA AREA', 'state': 'GA'},
                                'geometry': {'coordinates': [-82.16, 33.37]},
                            },
                            {
                                'id': 'AGS',
                                'properties': {'sname': 'AUGUSTA BUSH FIELD', 'state': 'GA'},
                                'geometry': {'coordinates': [-81.97, 33.37]},
                            },
                        ]
                    }
                )
            if 'cgi-bin/request/asos.py' in url and params['station'] == '19A':
                return FakeResponse(text='station,valid,lon,lat,elevation,tmpf,mslp,skyc1,skyc2,skyc3,skyc4,sknt,p01i\n')
            if 'cgi-bin/request/asos.py' in url and params['station'] == 'AGS':
                return FakeResponse(
                    text='station,valid,lon,lat,elevation,tmpf,mslp,skyc1,skyc2,skyc3,skyc4,sknt,p01i\n'
                    'AGS,2024-02-02 00:00,-81.97,33.37,144,55.0,1013.0,FEW,,,,8.0,0.00\n'
                    'AGS,2024-02-02 12:00,-81.97,33.37,144,61.0,1011.0,BKN,,,,10.0,0.05\n'
                )
            raise AssertionError(f'unexpected URL {url}')

        def close(self):
            return None

    normalized_weather = tmp_path / 'weather_history.csv'
    df = collect_weather_history(normalized_weather, outcomes_path=outcomes_source, session=FakeSession())

    assert len(df) == 1
    row = df.iloc[0]
    assert row['iem_station'] == 'AGS'
    assert row['pressure_mb'] == 1012.0
    assert row['precip_24h_mm'] == 1.27



def test_evaluate_usgs_mapping_candidates_prefers_sites_with_real_history(tmp_path, monkeypatch):
    outcomes_source = tmp_path / 'source_outcomes.csv'
    pd.DataFrame(
        [
            {
                'event_id': 'evt-001',
                'tournament_slug': 'tour-1',
                'event_name': 'River Open Day 1',
                'date': '2024-04-10',
                'location': 'River Reach',
                'species': 'smallmouth_bass',
                'median_weight_lb': 15.2,
                'baseline_signal': 0.48,
                'usgs_site_id': '01646500',
            },
            {
                'event_id': 'evt-002',
                'tournament_slug': 'tour-1',
                'event_name': 'River Open Day 2',
                'date': '2024-04-11',
                'location': 'River Reach',
                'species': 'smallmouth_bass',
                'median_weight_lb': 15.8,
                'baseline_signal': 0.49,
                'usgs_site_id': '01646500',
            },
            {
                'event_id': 'evt-003',
                'tournament_slug': 'tour-2',
                'event_name': 'Lake Open Day 1',
                'date': '2024-06-09',
                'location': 'Lake Reach',
                'species': 'smallmouth_bass',
                'median_weight_lb': 17.1,
                'baseline_signal': 0.51,
                'usgs_site_id': '02600000',
            },
        ]
    ).to_csv(outcomes_source, index=False)

    normalized_outcomes = tmp_path / 'historical_outcomes.csv'
    suggestions_path = tmp_path / 'mapping_suggestions.csv'
    coverage_path = tmp_path / 'mapping_coverage.csv'
    collect_historical_outcomes(normalized_outcomes, source_path=outcomes_source)
    pd.DataFrame(
        [
            {
                'tournament_slug': 'tour-1',
                'candidate_rank': 1,
                'suggested_usgs_site_id': '11111111',
                'selected_usgs_site_id': '11111111',
                'review_status': 'suggested',
            },
            {
                'tournament_slug': 'tour-1',
                'candidate_rank': 2,
                'suggested_usgs_site_id': '22222222',
                'selected_usgs_site_id': '',
                'review_status': 'candidate',
            },
            {
                'tournament_slug': 'tour-2',
                'candidate_rank': 1,
                'suggested_usgs_site_id': '33333333',
                'selected_usgs_site_id': '33333333',
                'review_status': 'suggested',
            },
        ]
    ).to_csv(suggestions_path, index=False)

    def fake_fetch(site_id: str, start_date: str, end_date: str, *, session=None, timeout: int = 30):
        if site_id == '11111111':
            return pd.DataFrame(columns=['date', 'water_temp_c', 'discharge_cfs', 'gage_height_ft', 'site_id'])
        if site_id == '22222222':
            return pd.DataFrame(
                [
                    {'date': pd.Timestamp('2024-04-09'), 'water_temp_c': 11.0, 'discharge_cfs': 100.0, 'gage_height_ft': 2.2, 'site_id': site_id},
                    {'date': pd.Timestamp('2024-04-10'), 'water_temp_c': 12.5, 'discharge_cfs': 110.0, 'gage_height_ft': 2.4, 'site_id': site_id},
                    {'date': pd.Timestamp('2024-04-11'), 'water_temp_c': 13.0, 'discharge_cfs': 120.0, 'gage_height_ft': 2.6, 'site_id': site_id},
                ]
            )
        if site_id == '33333333':
            return pd.DataFrame(
                [
                    {'date': pd.Timestamp('2024-06-08'), 'water_temp_c': 17.0, 'discharge_cfs': 210.0, 'gage_height_ft': 3.2, 'site_id': site_id},
                    {'date': pd.Timestamp('2024-06-09'), 'water_temp_c': 17.4, 'discharge_cfs': 214.0, 'gage_height_ft': 3.3, 'site_id': site_id},
                ]
            )
        raise AssertionError(f'unexpected site_id {site_id}')

    monkeypatch.setattr('castline.validation.collectors.usgs.fetch_usgs_daily_values', fake_fetch)

    df = evaluate_usgs_mapping_candidates(
        outcomes_path=normalized_outcomes,
        suggestions_path=suggestions_path,
        output_path=coverage_path,
        lookback_days=7,
    )

    assert coverage_path.exists()
    recommended_tour_1 = df.loc[(df['tournament_slug'] == 'tour-1') & (df['recommended_by_coverage'])].iloc[0]
    assert recommended_tour_1['suggested_usgs_site_id'] == '22222222'
    assert recommended_tour_1['review_status'] == 'coverage-recommended'
    assert recommended_tour_1['usable_event_count'] == 2

    recommended_tour_2 = df.loc[(df['tournament_slug'] == 'tour-2') & (df['recommended_by_coverage'])].iloc[0]
    assert recommended_tour_2['suggested_usgs_site_id'] == '33333333'
    assert recommended_tour_2['usable_event_count'] == 1



def test_evaluate_usgs_mapping_candidates_does_not_recommend_zero_coverage_rows(tmp_path, monkeypatch):
    outcomes_source = tmp_path / 'source_outcomes.csv'
    pd.DataFrame(
        [
            {
                'event_id': 'evt-001',
                'tournament_slug': 'tour-1',
                'event_name': 'Lake Open Day 1',
                'date': '2024-04-10',
                'location': 'Example Lake',
                'species': 'black_bass',
                'median_weight_lb': 10.0,
                'baseline_signal': 0.5,
                'usgs_site_id': '00000001',
            }
        ]
    ).to_csv(outcomes_source, index=False)
    normalized_outcomes = tmp_path / 'historical_outcomes.csv'
    collect_historical_outcomes(normalized_outcomes, source_path=outcomes_source)

    suggestions_path = tmp_path / 'mapping_suggestions.csv'
    pd.DataFrame(
        [
            {
                'tournament_slug': 'tour-1',
                'candidate_rank': 1,
                'suggested_usgs_site_id': '11111111',
                'selected_usgs_site_id': '11111111',
                'review_status': 'suggested',
            },
            {
                'tournament_slug': 'tour-1',
                'candidate_rank': 2,
                'suggested_usgs_site_id': '22222222',
                'selected_usgs_site_id': '',
                'review_status': 'candidate',
            },
        ]
    ).to_csv(suggestions_path, index=False)

    monkeypatch.setattr(
        'castline.validation.collectors.usgs.fetch_usgs_daily_values',
        lambda *args, **kwargs: pd.DataFrame(columns=['date', 'water_temp_c', 'discharge_cfs', 'gage_height_ft', 'site_id']),
    )

    coverage_path = tmp_path / 'coverage.csv'
    df = evaluate_usgs_mapping_candidates(
        outcomes_path=normalized_outcomes,
        suggestions_path=suggestions_path,
        output_path=coverage_path,
        lookback_days=7,
    )

    assert not df['recommended_by_coverage'].any()
    assert df['recommended_usgs_site_id'].fillna('').eq('').all()
    assert df['review_status'].tolist() == ['suggested', 'candidate']



def test_real_usgs_collection_skips_events_with_no_history(tmp_path, monkeypatch):
    outcomes_source = tmp_path / 'source_outcomes.csv'
    pd.DataFrame(
        [
            {
                'event_id': 'evt-001',
                'event_name': 'River Open Day 1',
                'date': '2024-04-10',
                'location': 'River Reach',
                'species': 'smallmouth_bass',
                'median_weight_lb': 15.2,
                'baseline_signal': 0.48,
                'usgs_site_id': '01646500',
            },
            {
                'event_id': 'evt-002',
                'event_name': 'River Open Day 2',
                'date': '2024-04-11',
                'location': 'River Reach',
                'species': 'smallmouth_bass',
                'median_weight_lb': 15.8,
                'baseline_signal': 0.49,
                'usgs_site_id': '01646501',
            },
        ]
    ).to_csv(outcomes_source, index=False)

    normalized_outcomes = tmp_path / 'historical_outcomes.csv'
    usgs_path = tmp_path / 'usgs_history.csv'
    collect_historical_outcomes(normalized_outcomes, source_path=outcomes_source)

    def fake_fetch(site_id: str, start_date: str, end_date: str, *, session=None, timeout: int = 30):
        if site_id == '01646500':
            return pd.DataFrame(
                [
                    {'date': pd.Timestamp('2024-04-09'), 'water_temp_c': 11.0, 'discharge_cfs': 100.0, 'gage_height_ft': 2.2, 'site_id': site_id},
                    {'date': pd.Timestamp('2024-04-10'), 'water_temp_c': 12.5, 'discharge_cfs': 110.0, 'gage_height_ft': 2.4, 'site_id': site_id},
                ]
            )
        return pd.DataFrame(columns=['date', 'water_temp_c', 'discharge_cfs', 'gage_height_ft', 'site_id'])

    monkeypatch.setattr('castline.validation.collectors.usgs.fetch_usgs_daily_values', fake_fetch)

    df = collect_usgs_history(usgs_path, outcomes_path=normalized_outcomes, lookback_days=7)

    assert len(df) == 1
    row = df.iloc[0]
    assert row['event_id'] == 'evt-001'
    assert row['site_id'] == 'USGS-01646500'
    assert usgs_path.exists()



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
    tournament_search_payload = [
        {
            'id': 123,
            'url': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
            '_links': {
                'self': [
                    {'href': 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123'}
                ]
            },
        }
    ]
    tournament_detail_payload = {
        'link': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
        'title': {'rendered': 'Results'},
        'content': {
            'rendered': '<h2><a href="https://example.com/test-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
        },
        'meta': {},
    }
    tournament_parent_payload = [
        {
            'link': 'https://www.bassmaster.com/tournament/2024-test-open/',
            'title': {'rendered': '2024 Test Open'},
            'content': {'rendered': ''},
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
        def __init__(self, payload=None, content: bytes = b'', text: str = '', status_code: int = 200):
            self._payload = payload
            self.content = content
            self.text = text
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            if 'wp-json/wp/v2/search' in url:
                if params and params.get('page') == 1:
                    return FakeResponse(payload=tournament_search_payload)
                return FakeResponse(payload=[])
            if url == 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123':
                return FakeResponse(payload=tournament_detail_payload)
            if 'wp-json/wp/v2/tournament' in url and params and params.get('slug') == '2024-test-open':
                return FakeResponse(payload=tournament_parent_payload)
            if 'waterservices.usgs.gov/nwis/site/' in url:
                assert params['stateCd'] == 'MI'
                assert 'Saginaw Bay' in params['siteName']
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
    tournament_search_payload = [
        {
            'id': 123,
            'url': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
            '_links': {
                'self': [
                    {'href': 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123'}
                ]
            },
        }
    ]
    tournament_detail_payload = {
        'link': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
        'title': {'rendered': 'Results'},
        'content': {
            'rendered': '<h2><a href="https://example.com/test-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
        },
        'meta': {},
    }
    tournament_parent_payload = [
        {
            'link': 'https://www.bassmaster.com/tournament/2024-test-open/',
            'title': {'rendered': '2024 Test Open'},
            'content': {'rendered': ''},
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
            if 'wp-json/wp/v2/search' in url:
                if params and params.get('page') == 1:
                    return FakeResponse(payload=tournament_search_payload)
                return FakeResponse(payload=None, status_code=400)
            if url == 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123':
                return FakeResponse(payload=tournament_detail_payload)
            if 'wp-json/wp/v2/tournament' in url and params and params.get('slug') == '2024-test-open':
                return FakeResponse(payload=tournament_parent_payload)
            raise AssertionError(f'unexpected URL {url}')

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


def test_water_body_query_variants_include_aliases_and_normalized_forms():
    variants = _water_body_query_variants(water_body='Sam Rayburn Reservoir', city='Jasper')

    assert variants[0] == 'Sam Rayburn Reservoir'
    assert 'Sam Rayburn' in variants
    assert 'Sam Rayburn Reservoir Jasper' in variants
    assert len(variants) == len(set(value.lower() for value in variants))



def test_usgs_site_candidate_fetch_tries_aliases_until_it_finds_matches():
    class FakeResponse:
        def __init__(self, text: str = '', status_code: int = 200):
            self.text = text
            self.status_code = status_code

        def raise_for_status(self):
            return None

    calls: list[str] = []
    usgs_rdb = """# ----------------------------------
# Data provided for test
agency_cd\tsite_no\tstation_nm\tsite_tp_cd
5s\t15s\t50s\t7s
USGS\t08038490\tSam Rayburn Res nr Zavalla, TX\tLK
USGS\t08039300\tSam Rayburn Res nr Jasper, TX\tLK
"""

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            calls.append(params['siteName'])
            if params['siteName'] == 'Sam Rayburn Reservoir':
                return FakeResponse(text='')
            if params['siteName'] == 'Sam Rayburn':
                return FakeResponse(text=usgs_rdb)
            raise AssertionError(f"unexpected siteName {params['siteName']}")

    df = _fetch_usgs_site_candidates(
        water_body='Sam Rayburn Reservoir',
        city='Jasper',
        state='TX',
        session=FakeSession(),
    )

    assert calls[:2] == ['Sam Rayburn Reservoir', 'Sam Rayburn']
    assert len(df) == 2
    assert df.iloc[0]['query_site_name'] == 'Sam Rayburn'
    assert set(df['site_no']) == {'08038490', '08039300'}



def test_usgs_site_candidate_404_returns_empty_dataframe():
    class FakeResponse:
        status_code = 404
        text = ''

        def raise_for_status(self):
            raise AssertionError('404 should be handled without raise_for_status')

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            return FakeResponse()

    df = _fetch_usgs_site_candidates(
        water_body='Clarks Hill Reservoir',
        state='GA',
        session=FakeSession(),
    )

    assert df.empty



def test_export_curated_bassmaster_mappings_from_review_sheet(tmp_path):
    review_sheet_path = tmp_path / 'mapping_review.csv'
    pd.DataFrame(
        [
            {
                'tournament_slug': '2024-lake-okeechobee',
                'candidate_rank': 1,
                'suggested_usgs_site_id': '02276400',
                'selected_usgs_site_id': '02276400',
                'review_status': 'suggested',
                'species': 'black_bass',
            },
            {
                'tournament_slug': '2024-lake-okeechobee',
                'candidate_rank': 2,
                'suggested_usgs_site_id': '264631080542600',
                'selected_usgs_site_id': '',
                'review_status': 'candidate',
                'species': 'black_bass',
            },
            {
                'tournament_slug': '2024-grand-lake',
                'candidate_rank': 1,
                'suggested_usgs_site_id': '07190000',
                'selected_usgs_site_id': '',
                'review_status': 'approved',
                'species': 'black_bass',
            },
            {
                'tournament_slug': '2024-needs-research',
                'candidate_rank': 1,
                'suggested_usgs_site_id': '',
                'selected_usgs_site_id': '',
                'review_status': 'needs-research',
                'species': 'black_bass',
            },
        ]
    ).to_csv(review_sheet_path, index=False)

    output_path = tmp_path / 'bassmaster_mapping.csv'
    df = export_curated_bassmaster_mappings(review_sheet_path=review_sheet_path, output_path=output_path)

    assert output_path.exists()
    assert len(df) == 2
    assert df.to_dict(orient='records') == [
        {
            'tournament_slug': '2024-grand-lake',
            'usgs_site_id': '07190000',
            'species': 'black_bass',
        },
        {
            'tournament_slug': '2024-lake-okeechobee',
            'usgs_site_id': '02276400',
            'species': 'black_bass',
        },
    ]



def test_export_curated_bassmaster_mappings_prefers_coverage_backed_candidate(tmp_path):
    review_sheet_path = tmp_path / 'mapping_coverage.csv'
    pd.DataFrame(
        [
            {
                'tournament_slug': '2024-douglas-lake',
                'candidate_rank': 1,
                'suggested_usgs_site_id': '03468500',
                'selected_usgs_site_id': '',
                'review_status': 'suggested',
                'recommended_by_coverage': False,
                'usable_event_count': 0,
                'species': 'black_bass',
            },
            {
                'tournament_slug': '2024-douglas-lake',
                'candidate_rank': 2,
                'suggested_usgs_site_id': '03467609',
                'selected_usgs_site_id': '',
                'review_status': 'coverage-recommended',
                'recommended_by_coverage': True,
                'usable_event_count': 1,
                'species': 'black_bass',
            },
        ]
    ).to_csv(review_sheet_path, index=False)

    output_path = tmp_path / 'bassmaster_mapping.csv'
    df = export_curated_bassmaster_mappings(review_sheet_path=review_sheet_path, output_path=output_path)

    assert df.to_dict(orient='records') == [
        {
            'tournament_slug': '2024-douglas-lake',
            'usgs_site_id': '03467609',
            'species': 'black_bass',
        }
    ]



def test_collect_historical_outcomes_skips_unparseable_bassmaster_pdf(tmp_path, monkeypatch):
    mapping_path = tmp_path / 'bassmaster_mapping.csv'
    pd.DataFrame(
        [
            {
                'tournament_slug': '2024-bad-open',
                'usgs_site_id': '01646500',
                'species': 'smallmouth_bass',
            },
            {
                'tournament_slug': '2024-good-open',
                'usgs_site_id': '01646501',
                'species': 'smallmouth_bass',
            },
        ]
    ).to_csv(mapping_path, index=False)

    tournament_search_payload = [
        {
            'id': 123,
            'url': 'https://www.bassmaster.com/tournament/2024-bad-open/results/',
            '_links': {
                'self': [
                    {'href': 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123'}
                ]
            },
        },
        {
            'id': 124,
            'url': 'https://www.bassmaster.com/tournament/2024-good-open/results/',
            '_links': {
                'self': [
                    {'href': 'https://www.bassmaster.com/wp-json/wp/v2/tournament/124'}
                ]
            },
        },
    ]
    tournament_detail_payloads = {
        'https://www.bassmaster.com/wp-json/wp/v2/tournament/123': {
            'link': 'https://www.bassmaster.com/tournament/2024-bad-open/results/',
            'title': {'rendered': 'Results'},
            'content': {
                'rendered': '<h2><a href="https://example.com/bad-open-day-1.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
            },
            'meta': {},
        },
        'https://www.bassmaster.com/wp-json/wp/v2/tournament/124': {
            'link': 'https://www.bassmaster.com/tournament/2024-good-open/results/',
            'title': {'rendered': 'Results'},
            'content': {
                'rendered': '<h2><a href="https://example.com/good-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
            },
            'meta': {},
        },
    }
    tournament_parent_payloads = {
        '2024-bad-open': [
            {
                'link': 'https://www.bassmaster.com/tournament/2024-bad-open/',
                'title': {'rendered': '2024 Bad Open'},
                'content': {'rendered': ''},
                'meta': {
                    'bassmaster_tournament_start_date': '2024-06-01',
                    'bassmaster_tournament_body_of_water': 'Bad Lake',
                    'bassmaster_tournament_city': 'Bad City',
                    'bassmaster_tournament_state': 'MI',
                },
            }
        ],
        '2024-good-open': [
            {
                'link': 'https://www.bassmaster.com/tournament/2024-good-open/',
                'title': {'rendered': '2024 Good Open'},
                'content': {'rendered': ''},
                'meta': {
                    'bassmaster_tournament_start_date': '2024-06-06',
                    'bassmaster_tournament_body_of_water': 'Good Lake',
                    'bassmaster_tournament_city': 'Good City',
                    'bassmaster_tournament_state': 'MI',
                },
            }
        ],
    }

    class FakeResponse:
        def __init__(self, payload=None, content: bytes = b'', status_code: int = 200):
            self._payload = payload
            self.content = content
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            if 'wp-json/wp/v2/search' in url:
                if params and params.get('page') == 1:
                    return FakeResponse(payload=tournament_search_payload)
                return FakeResponse(payload=[])
            if url in tournament_detail_payloads:
                return FakeResponse(payload=tournament_detail_payloads[url])
            if 'wp-json/wp/v2/tournament' in url and params and params.get('slug') in tournament_parent_payloads:
                return FakeResponse(payload=tournament_parent_payloads[params['slug']])
            if url in {'https://example.com/bad-open-day-1.pdf', 'https://example.com/good-open-day-2.pdf'}:
                return FakeResponse(content=b'%PDF-1.4 fake bytes')
            raise AssertionError(f'unexpected URL {url}')

        def close(self):
            return None

    monkeypatch.setattr('castline.validation.collectors.outcomes.requests.Session', lambda: FakeSession())

    def fake_extract(pdf_bytes: bytes) -> float:
        if pdf_bytes == b'%PDF-1.4 fake bytes':
            if not hasattr(fake_extract, 'seen'):
                fake_extract.seen = 0
            fake_extract.seen += 1
            if fake_extract.seen == 1:
                raise ValueError('could not extract competitor daily weights from results PDF')
            return 15.75
        raise AssertionError('unexpected pdf bytes')

    monkeypatch.setattr('castline.validation.collectors.outcomes._extract_median_weight_from_pdf', fake_extract)

    output_path = tmp_path / 'historical_outcomes.csv'
    df = collect_historical_outcomes(
        output_path,
        bassmaster_years=(2024, 2024),
        mapping_path=mapping_path,
    )

    assert len(df) == 1
    assert df.iloc[0]['event_id'] == '2024-good-open-day-2'
    assert df.iloc[0]['usgs_site_id'] == '01646501'
    assert output_path.exists()



def test_collect_historical_outcomes_filters_to_curated_mappings(tmp_path, monkeypatch):
    mapping_path = tmp_path / 'bassmaster_mapping.csv'
    pd.DataFrame(
        [
            {
                'tournament_slug': '2024-good-open',
                'usgs_site_id': '01646501',
                'species': 'smallmouth_bass',
            }
        ]
    ).to_csv(mapping_path, index=False)

    tournament_search_payload = [
        {
            'id': 123,
            'url': 'https://www.bassmaster.com/tournament/2024-bad-open/results/',
            '_links': {
                'self': [
                    {'href': 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123'}
                ]
            },
        },
        {
            'id': 124,
            'url': 'https://www.bassmaster.com/tournament/2024-good-open/results/',
            '_links': {
                'self': [
                    {'href': 'https://www.bassmaster.com/wp-json/wp/v2/tournament/124'}
                ]
            },
        },
    ]
    tournament_detail_payloads = {
        'https://www.bassmaster.com/wp-json/wp/v2/tournament/123': {
            'link': 'https://www.bassmaster.com/tournament/2024-bad-open/results/',
            'title': {'rendered': 'Results'},
            'content': {
                'rendered': '<h2><a href="https://example.com/bad-open-day-1.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
            },
            'meta': {},
        },
        'https://www.bassmaster.com/wp-json/wp/v2/tournament/124': {
            'link': 'https://www.bassmaster.com/tournament/2024-good-open/results/',
            'title': {'rendered': 'Results'},
            'content': {
                'rendered': '<h2><a href="https://example.com/good-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
            },
            'meta': {},
        },
    }
    tournament_parent_payloads = {
        '2024-bad-open': [
            {
                'link': 'https://www.bassmaster.com/tournament/2024-bad-open/',
                'title': {'rendered': '2024 Bad Open'},
                'content': {'rendered': ''},
                'meta': {
                    'bassmaster_tournament_start_date': '2024-06-01',
                    'bassmaster_tournament_body_of_water': 'Bad Lake',
                    'bassmaster_tournament_city': 'Bad City',
                    'bassmaster_tournament_state': 'MI',
                },
            }
        ],
        '2024-good-open': [
            {
                'link': 'https://www.bassmaster.com/tournament/2024-good-open/',
                'title': {'rendered': '2024 Good Open'},
                'content': {'rendered': ''},
                'meta': {
                    'bassmaster_tournament_start_date': '2024-06-06',
                    'bassmaster_tournament_body_of_water': 'Good Lake',
                    'bassmaster_tournament_city': 'Good City',
                    'bassmaster_tournament_state': 'MI',
                },
            }
        ],
    }

    class FakeResponse:
        def __init__(self, payload=None, content: bytes = b'', status_code: int = 200):
            self._payload = payload
            self.content = content
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            if 'wp-json/wp/v2/search' in url:
                if params and params.get('page') == 1:
                    return FakeResponse(payload=tournament_search_payload)
                return FakeResponse(payload=[])
            if url in tournament_detail_payloads:
                return FakeResponse(payload=tournament_detail_payloads[url])
            if 'wp-json/wp/v2/tournament' in url and params and params.get('slug') in tournament_parent_payloads:
                return FakeResponse(payload=tournament_parent_payloads[params['slug']])
            if url in {'https://example.com/bad-open-day-1.pdf', 'https://example.com/good-open-day-2.pdf'}:
                return FakeResponse(content=b'%PDF-1.4 fake bytes')
            raise AssertionError(f'unexpected URL {url}')

        def close(self):
            return None

    monkeypatch.setattr('castline.validation.collectors.outcomes.requests.Session', lambda: FakeSession())
    monkeypatch.setattr('castline.validation.collectors.outcomes._extract_median_weight_from_pdf', lambda pdf_bytes: 15.75)

    output_path = tmp_path / 'historical_outcomes.csv'
    df = collect_historical_outcomes(
        output_path,
        bassmaster_years=(2024, 2024),
        mapping_path=mapping_path,
    )

    assert len(df) == 1
    assert df.iloc[0]['event_id'] == '2024-good-open-day-2'
    assert df.iloc[0]['usgs_site_id'] == '01646501'
    assert output_path.exists()



def test_collect_historical_outcomes_accepts_review_sheet_mapping(tmp_path, monkeypatch):
    review_sheet_path = tmp_path / 'mapping_review.csv'
    pd.DataFrame(
        [
            {
                'tournament_slug': '2024-test-open',
                'candidate_rank': 1,
                'suggested_usgs_site_id': '01646500',
                'selected_usgs_site_id': '01646500',
                'review_status': 'suggested',
                'species': 'smallmouth_bass',
            }
        ]
    ).to_csv(review_sheet_path, index=False)

    tournament_search_payload = [
        {
            'id': 123,
            'url': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
            '_links': {
                'self': [
                    {'href': 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123'}
                ]
            },
        }
    ]
    tournament_detail_payload = {
        'link': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
        'title': {'rendered': 'Results'},
        'content': {
            'rendered': '<h2><a href="https://example.com/test-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
        },
        'meta': {},
    }
    tournament_parent_payload = [
        {
            'link': 'https://www.bassmaster.com/tournament/2024-test-open/',
            'title': {'rendered': '2024 Test Open'},
            'content': {'rendered': ''},
            'meta': {
                'bassmaster_tournament_start_date': '2024-06-06',
                'bassmaster_tournament_body_of_water': 'Saginaw Bay',
                'bassmaster_tournament_city': 'Saginaw',
                'bassmaster_tournament_state': 'MI',
            },
        }
    ]

    class FakeResponse:
        def __init__(self, payload=None, content: bytes = b'', status_code: int = 200):
            self._payload = payload
            self.content = content
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, params=None, timeout=30, headers=None):
            if 'wp-json/wp/v2/search' in url:
                if params and params.get('page') == 1:
                    return FakeResponse(payload=tournament_search_payload)
                return FakeResponse(payload=[])
            if url == 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123':
                return FakeResponse(payload=tournament_detail_payload)
            if 'wp-json/wp/v2/tournament' in url and params and params.get('slug') == '2024-test-open':
                return FakeResponse(payload=tournament_parent_payload)
            if url == 'https://example.com/test-open-day-2.pdf':
                return FakeResponse(content=b'%PDF-1.4 fake bytes')
            raise AssertionError(f'unexpected URL {url}')

        def close(self):
            return None

    monkeypatch.setattr('castline.validation.collectors.outcomes.requests.Session', lambda: FakeSession())
    monkeypatch.setattr(
        'castline.validation.collectors.outcomes._extract_median_weight_from_pdf',
        lambda pdf_bytes: 15.75,
    )

    output_path = tmp_path / 'historical_outcomes.csv'
    df = collect_historical_outcomes(
        output_path,
        bassmaster_years=(2024, 2024),
        mapping_path=review_sheet_path,
    )

    assert len(df) == 1
    assert df.iloc[0]['event_id'] == '2024-test-open-day-2'
    assert df.iloc[0]['usgs_site_id'] == '01646500'
    assert df.iloc[0]['species'] == 'smallmouth_bass'
    assert output_path.exists()



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

    tournament_search_payload = [
        {
            'id': 123,
            'url': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
            '_links': {
                'self': [
                    {'href': 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123'}
                ]
            },
        }
    ]
    tournament_detail_payload = {
        'link': 'https://www.bassmaster.com/tournament/2024-test-open/results/',
        'title': {'rendered': 'Results'},
        'content': {
            'rendered': '<h2><a href="https://example.com/test-open-day-2.pdf">LINK: TOURNAMENT RESULTS</a></h2>'
        },
        'meta': {},
    }
    tournament_parent_payload = [
        {
            'link': 'https://www.bassmaster.com/tournament/2024-test-open/',
            'title': {'rendered': '2024 Test Open'},
            'content': {'rendered': ''},
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
            if 'wp-json/wp/v2/search' in url:
                if params and params.get('page') == 1:
                    return FakeResponse(payload=tournament_search_payload)
                return FakeResponse(payload=[])
            if url == 'https://www.bassmaster.com/wp-json/wp/v2/tournament/123':
                return FakeResponse(payload=tournament_detail_payload)
            if 'wp-json/wp/v2/tournament' in url and params and params.get('slug') == '2024-test-open':
                return FakeResponse(payload=tournament_parent_payload)
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
