from __future__ import annotations

from pathlib import Path

from castline.validation.cli import main
from castline.validation.dataset import write_template_csv
from castline.validation.evaluate import evaluate_csv


def test_environmental_model_outperforms_baseline(tmp_path: Path, capsys) -> None:
    dataset_path = tmp_path / "validation.csv"
    rows = []
    for year in range(2018, 2026):
        for event_index in range(1, 6):
            moon_phase = 0.1 * (event_index % 2)
            air_temp = 14.0 + ((year + event_index) % 3)
            pressure = 1008.0 + (event_index % 2)
            wind_speed = 6.0 + (year % 2)
            cloud_cover = 20.0 + ((event_index + year) % 4)

            water_temp = 8.0 + (year - 2018) * 0.8 + event_index * 2.5
            water_temp_delta = 0.5 + event_index * 0.8
            discharge = 90.0 + event_index * 18.0 + (year - 2018) * 3.0
            discharge_change = event_index * 4.0 + (year - 2018) * 0.5
            gage_height = 2.5 + event_index * 0.2
            precip = (6 - event_index) * 1.8 + (year % 3)

            target = (
                4.0
                + 1.7 * water_temp
                + 0.75 * water_temp_delta
                + 0.55 * discharge_change
                + 0.35 * precip
                + 0.08 * gage_height
            )
            rows.append(
                {
                    "event_id": f"{year}-evt-{event_index}",
                    "year": year,
                    "target": round(target, 4),
                    "moon_phase": round(moon_phase, 4),
                    "air_temp_c": round(air_temp, 4),
                    "pressure_mb": round(pressure, 4),
                    "wind_speed_kph": round(wind_speed, 4),
                    "cloud_cover_pct": round(cloud_cover, 4),
                    "water_temp_c": round(water_temp, 4),
                    "water_temp_6h_delta": round(water_temp_delta, 4),
                    "discharge_cfs": round(discharge, 4),
                    "discharge_6h_pct_change": round(discharge_change, 4),
                    "gage_height_ft": round(gage_height, 4),
                    "precip_24h_mm": round(precip, 4),
                }
            )

    write_template_csv(dataset_path, rows)
    comparison = evaluate_csv(dataset_path, train_max_year=2022, test_min_year=2023)
    assert comparison.enriched.r2 > comparison.baseline.r2
    assert comparison.judgment == "strong"

    report_path = tmp_path / "report.md"
    import sys

    argv = sys.argv
    try:
        sys.argv = ["castline-validation", str(dataset_path), "--write-report", str(report_path)]
        assert main() == 0
    finally:
        sys.argv = argv

    output = capsys.readouterr().out
    assert "CASTLINE Phase 0 Validation Summary" in output
    assert report_path.exists()
