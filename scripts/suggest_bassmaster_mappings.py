#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from castline.validation.collectors.outcomes import suggest_bassmaster_usgs_mappings
from castline.validation.config import ValidationPaths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Suggest first-pass USGS gauge mappings for Bassmaster tournaments.'
    )
    parser.add_argument('--bassmaster-start-year', type=int, required=True)
    parser.add_argument('--bassmaster-end-year', type=int, required=True)
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='Optional output CSV path. Defaults to castline/validation/data/raw/bassmaster_usgs_mapping_suggestions.csv',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = ValidationPaths()
    paths.ensure()
    output_path = args.output or (paths.raw_data / 'bassmaster_usgs_mapping_suggestions.csv')
    df = suggest_bassmaster_usgs_mappings(
        start_year=args.bassmaster_start_year,
        end_year=args.bassmaster_end_year,
        output_path=output_path,
    )
    print(f'wrote {len(df)} mapping suggestions to {output_path}')


if __name__ == '__main__':
    main()
