#!/usr/bin/env python3
from __future__ import annotations

from castline.validation.assembly.dataset import assemble_validation_dataset
from castline.validation.config import ValidationPaths


def main() -> None:
    paths = ValidationPaths()
    paths.ensure()
    dataset = assemble_validation_dataset(
        outcomes_path=paths.raw_data / 'historical_outcomes.csv',
        usgs_path=paths.raw_data / 'usgs_history.csv',
        output_path=paths.processed_data / 'validation_dataset.csv',
    )
    print(f'wrote dataset with {len(dataset)} rows to {paths.processed_data / "validation_dataset.csv"}')


if __name__ == '__main__':
    main()
