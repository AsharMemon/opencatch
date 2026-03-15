#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from castline.validation.collectors.outcomes import collect_historical_outcomes
from castline.validation.config import ValidationPaths


def main() -> None:
    parser = argparse.ArgumentParser(description='Collect historical fishing outcome data scaffold.')
    parser.add_argument('--sample', action='store_true', help='Use bundled sample data.')
    parser.add_argument(
        '--source',
        type=Path,
        default=None,
        help='Path to a normalized CSV with tournament/outcome rows.',
    )
    args = parser.parse_args()

    paths = ValidationPaths()
    paths.ensure()
    out = paths.raw_data / 'historical_outcomes.csv'
    df = collect_historical_outcomes(out, sample=args.sample, source_path=args.source)
    print(f'wrote {len(df)} outcome rows to {out}')


if __name__ == '__main__':
    main()
