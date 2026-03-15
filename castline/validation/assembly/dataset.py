from __future__ import annotations

from pathlib import Path

import pandas as pd

WEATHER_COLUMNS = [
    'air_temp_c',
    'pressure_mb',
    'wind_speed_kph',
    'cloud_cover_pct',
    'precip_24h_mm',
]


def _load_weather(weather_path: Path | None) -> pd.DataFrame | None:
    if weather_path is None or not weather_path.exists():
        return None
    return pd.read_csv(weather_path)


def assemble_validation_dataset(
    outcomes_path: Path,
    usgs_path: Path,
    output_path: Path,
    weather_path: Path | None = None,
) -> pd.DataFrame:
    outcomes = pd.read_csv(outcomes_path)
    usgs = pd.read_csv(usgs_path)
    dataset = outcomes.merge(usgs, on='event_id', how='inner', suffixes=('', '_usgs'))

    weather = _load_weather(weather_path)
    if weather is not None:
        dataset = dataset.merge(weather, on='event_id', how='left', suffixes=('', '_weather'))

    for column in WEATHER_COLUMNS:
        if column not in dataset.columns:
            dataset[column] = 0.0
        dataset[column] = pd.to_numeric(dataset[column], errors='coerce').fillna(0.0)

    dataset['target_success_score'] = dataset['median_weight_lb']
    dataset['water_temp_x_flow'] = dataset['water_temp_c'] * dataset['discharge_cfs']
    dataset['weather_stability_index'] = (
        dataset['pressure_mb'] * 0.02
        - dataset['wind_speed_kph'] * 0.15
        - dataset['cloud_cover_pct'] * 0.01
        - dataset['precip_24h_mm'] * 0.12
    )
    dataset['env_signal'] = (
        dataset['water_temp_c'] * 0.35
        + dataset['temp_delta_24h_c'] * 1.25
        + dataset['flow_delta_24h_pct'] * -0.08
        + dataset['gage_height_ft'] * 0.15
        + dataset['precip_24h_mm'] * -0.18
        + dataset['pressure_mb'] * 0.01
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(output_path, index=False)
    return dataset
