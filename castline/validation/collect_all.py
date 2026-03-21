#!/usr/bin/env python3
"""Comprehensive data collection script for Phase 0 validation.

Collects maximum tournament outcomes from Bassmaster (2014-2025),
pulls USGS environmental data and IEM weather data for each event,
and assembles the full validation dataset.

Usage:
    python -m castline.validation.collect_all [--start-year 2014] [--end-year 2025]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from castline.validation.config import ValidationPaths
from castline.validation.collectors.outcomes import collect_historical_outcomes
from castline.validation.collectors.usgs import collect_usgs_history
from castline.validation.collectors.weather import collect_weather_history
from castline.validation.assembly.dataset import assemble_validation_dataset
from castline.validation.models.comparison import compare_models


def main() -> None:
    parser = argparse.ArgumentParser(description='Collect all Phase 0 validation data')
    parser.add_argument('--start-year', type=int, default=2014, help='Start year for tournament scraping')
    parser.add_argument('--end-year', type=int, default=2025, help='End year for tournament scraping')
    parser.add_argument('--skip-scrape', action='store_true', help='Skip scraping, use existing outcomes CSV')
    parser.add_argument('--skip-usgs', action='store_true', help='Skip USGS collection, use existing CSV')
    parser.add_argument('--skip-weather', action='store_true', help='Skip weather collection, use existing CSV')
    args = parser.parse_args()

    paths = ValidationPaths()
    paths.ensure()

    outcomes_path = paths.raw_data / 'historical_outcomes.csv'
    usgs_path = paths.raw_data / 'usgs_history.csv'
    weather_path = paths.raw_data / 'weather_history.csv'
    dataset_path = paths.processed_data / 'validation_dataset.csv'
    mapping_path = paths.raw_data / 'bassmaster_usgs_mapping.csv'
    coverage_path = paths.raw_data / 'bassmaster_usgs_mapping_coverage.csv'

    # Use coverage mapping if available (has more resolved gauges), fallback to basic mapping
    effective_mapping = coverage_path if coverage_path.exists() else (mapping_path if mapping_path.exists() else None)

    # Step 1: Collect tournament outcomes
    if not args.skip_scrape:
        print(f'\n=== Collecting Bassmaster tournament outcomes ({args.start_year}-{args.end_year}) ===')
        print(f'Using mapping: {effective_mapping}')
        try:
            outcomes = collect_historical_outcomes(
                output_path=outcomes_path,
                bassmaster_years=(args.start_year, args.end_year),
                mapping_path=effective_mapping,
            )
            print(f'Collected {len(outcomes)} tournament event rows')
        except Exception as exc:
            print(f'ERROR collecting outcomes: {exc}', file=sys.stderr)
            if not outcomes_path.exists():
                print('No existing outcomes file to fall back on. Exiting.', file=sys.stderr)
                sys.exit(1)
            print(f'Falling back to existing outcomes at {outcomes_path}')
    else:
        print(f'\n=== Skipping scrape, using existing outcomes at {outcomes_path} ===')

    if not outcomes_path.exists():
        print('No outcomes file found. Run without --skip-scrape first.', file=sys.stderr)
        sys.exit(1)

    import pandas as pd
    outcomes = pd.read_csv(outcomes_path)
    print(f'Outcomes file has {len(outcomes)} rows')

    # Step 2: Collect USGS environmental data
    if not args.skip_usgs:
        print(f'\n=== Collecting USGS water data for {len(outcomes)} events ===')
        try:
            usgs = collect_usgs_history(
                output_path=usgs_path,
                outcomes_path=outcomes_path,
                lookback_days=7,
            )
            print(f'Collected USGS data for {len(usgs)} events')
        except Exception as exc:
            print(f'ERROR collecting USGS data: {exc}', file=sys.stderr)
            if not usgs_path.exists():
                print('No existing USGS file. Exiting.', file=sys.stderr)
                sys.exit(1)
    else:
        print(f'\n=== Skipping USGS, using existing data at {usgs_path} ===')

    # Step 3: Collect weather data
    if not args.skip_weather:
        print(f'\n=== Collecting IEM weather data for {len(outcomes)} events ===')
        try:
            weather = collect_weather_history(
                output_path=weather_path,
                outcomes_path=outcomes_path,
            )
            print(f'Collected weather data for {len(weather)} events')
        except Exception as exc:
            print(f'ERROR collecting weather data: {exc}', file=sys.stderr)
            if not weather_path.exists():
                print('No existing weather file. Exiting.', file=sys.stderr)
                sys.exit(1)
    else:
        print(f'\n=== Skipping weather, using existing data at {weather_path} ===')

    # Step 4: Assemble validation dataset
    print('\n=== Assembling validation dataset ===')
    dataset = assemble_validation_dataset(
        outcomes_path=outcomes_path,
        usgs_path=usgs_path,
        output_path=dataset_path,
        weather_path=weather_path if weather_path.exists() else None,
    )
    print(f'Assembled dataset: {len(dataset)} rows')

    # Step 5: Show summary
    print('\n=== Dataset Summary ===')
    if 'date' in dataset.columns:
        dates = pd.to_datetime(dataset['date'])
        print(f'Date range: {dates.min().date()} to {dates.max().date()}')
        print(f'Years covered: {sorted(dates.dt.year.unique().tolist())}')
    if 'location' in dataset.columns:
        print(f'Unique locations: {dataset["location"].nunique()}')
    if 'water_temp_c' in dataset.columns:
        has_temp = (dataset['water_temp_c'] > 0).sum()
        print(f'Events with water temp data: {has_temp}/{len(dataset)}')
    if 'discharge_cfs' in dataset.columns:
        has_flow = (dataset['discharge_cfs'] > 0).sum()
        print(f'Events with discharge data: {has_flow}/{len(dataset)}')
    if 'pressure_mb' in dataset.columns:
        has_pressure = (dataset['pressure_mb'] > 0).sum()
        print(f'Events with pressure data: {has_pressure}/{len(dataset)}')

    # Step 6: Run comparison if enough data
    print('\n=== Running model comparison ===')
    try:
        report = compare_models(
            outcomes_path=outcomes_path,
            usgs_path=usgs_path,
            weather_path=weather_path if weather_path.exists() else None,
        )
        print(f'Result: {report}')
    except Exception as exc:
        print(f'Model comparison failed: {exc}', file=sys.stderr)
        print('(This is expected if insufficient data)')


if __name__ == '__main__':
    main()
