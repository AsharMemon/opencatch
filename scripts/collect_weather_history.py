#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from castline.validation.collectors.weather import collect_weather_history
from castline.validation.config import ValidationPaths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Collect or normalize historical weather/IEM features for CASTLINE validation')
    parser.add_argument('--sample', action='store_true', help='Write bundled sample weather rows')
    parser.add_argument('--source', type=Path, help='Path to a normalized weather CSV keyed by event_id')
    parser.add_argument(
        '--outcomes',
        type=Path,
        help='Path to normalized historical outcomes CSV; when provided, fetch IEM ASOS history keyed by event metadata',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = ValidationPaths()
    paths.ensure()
    output_path = paths.raw_data / 'weather_history.csv'
    df = collect_weather_history(
        output_path=output_path,
        sample=args.sample,
        source_path=args.source,
        outcomes_path=args.outcomes,
    )
    print(f'wrote {len(df)} historical weather rows to {output_path}')


if __name__ == '__main__':
    main()
