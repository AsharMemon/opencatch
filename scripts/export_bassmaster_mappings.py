#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from castline.validation.collectors.outcomes import export_curated_bassmaster_mappings
from castline.validation.config import ValidationPaths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Export a reviewed Bassmaster USGS mapping sheet into the compact mapping CSV used by collectors.'
    )
    parser.add_argument(
        '--review-sheet',
        type=Path,
        required=True,
        help='CSV produced by suggest_bassmaster_mappings.py, optionally edited in-place.',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='Optional output CSV path. Defaults to castline/validation/data/raw/bassmaster_usgs_mapping.csv',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = ValidationPaths()
    paths.ensure()
    output_path = args.output or (paths.raw_data / 'bassmaster_usgs_mapping.csv')
    df = export_curated_bassmaster_mappings(review_sheet_path=args.review_sheet, output_path=output_path)
    print(f'wrote {len(df)} curated mappings to {output_path}')


if __name__ == '__main__':
    main()
