from __future__ import annotations

from pathlib import Path

import pandas as pd


def assemble_validation_dataset(outcomes_path: Path, usgs_path: Path, output_path: Path) -> pd.DataFrame:
    outcomes = pd.read_csv(outcomes_path)
    usgs = pd.read_csv(usgs_path)
    dataset = outcomes.merge(usgs, on='event_id', how='inner')
    dataset['target_success_score'] = dataset['median_weight_lb']
    dataset['water_temp_x_flow'] = dataset['water_temp_c'] * dataset['discharge_cfs']
    dataset['env_signal'] = (
        dataset['water_temp_c'] * 0.35
        + dataset['temp_delta_24h_c'] * 1.25
        + dataset['flow_delta_24h_pct'] * -0.08
        + dataset['gage_height_ft'] * 0.15
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(output_path, index=False)
    return dataset
