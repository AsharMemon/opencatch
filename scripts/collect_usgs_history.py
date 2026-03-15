#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from castline.validation.collectors.usgs import collect_usgs_history
from castline.validation.config import ValidationPaths


def main() -> None:
    parser = argparse.ArgumentParser(description='Collect USGS historical data scaffold.')
    parser.add_argument('--sample', action='store_true', help='Use bundled sample data.')
    parser.add_argument(
        '--outcomes',
        type=Path,
        default=None,
        help='Normalized outcomes CSV with event dates and usgs_site_id values.',
    )
    parser.add_argument(
        '--lookback-days',
        type=int,
        default=7,
        help='Number of days before each event date to request from USGS.',
    )
    args = parser.parse_args()

    paths = ValidationPaths()
    paths.ensure()
    out = paths.raw_data / 'usgs_history.csv'
    outcomes_path = args.outcomes or (paths.raw_data / 'historical_outcomes.csv')
    df = collect_usgs_history(
        out,
        sample=args.sample,
        outcomes_path=None if args.sample else outcomes_path,
        lookback_days=args.lookback_days,
    )
    print(f'wrote {len(df)} USGS rows to {out}')


if __name__ == '__main__':
    main()
