#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from castline.validation.config import ValidationPaths
from castline.validation.models.comparison import compare_models
from castline.validation.reporting.summary import write_validation_summary


def main() -> None:
    paths = ValidationPaths()
    paths.ensure()
    summary = compare_models(
        dataset_path=paths.processed_data / 'validation_dataset.csv',
        output_path=paths.artifacts / 'baseline_vs_full_report.json',
    )
    write_validation_summary(summary, paths.artifacts / 'validation_summary.md')
    print(
        f'baseline R2={summary.baseline_r2:.4f} full R2={summary.full_r2:.4f} '
        f'improvement={summary.improvement_pct:.2f}% thesis={summary.thesis_rating}'
    )


if __name__ == '__main__':
    main()
