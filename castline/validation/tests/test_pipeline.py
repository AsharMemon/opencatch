from __future__ import annotations

import pandas as pd

from castline.validation.assembly.dataset import assemble_validation_dataset
from castline.validation.collectors.outcomes import collect_historical_outcomes
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
