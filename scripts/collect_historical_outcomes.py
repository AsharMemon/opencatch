#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    parser.add_argument(
        '--bassmaster-start-year',
        type=int,
        default=None,
        help='Start year for scraping Bassmaster tournament result PDFs via the official WordPress API.',
    )
    parser.add_argument(
        '--bassmaster-end-year',
        type=int,
        default=None,
        help='End year for scraping Bassmaster tournament result PDFs via the official WordPress API.',
    )
    parser.add_argument(
        '--mapping',
        type=Path,
        default=None,
        help='CSV mapping for source adapters. For Bassmaster this can be either a compact tournament_slug/usgs_site_id mapping file or an edited ranked review sheet with selected_usgs_site_id values.',
    )
    args = parser.parse_args()

    bassmaster_years: tuple[int, int] | None = None
    if args.bassmaster_start_year is not None or args.bassmaster_end_year is not None:
        if args.bassmaster_start_year is None or args.bassmaster_end_year is None:
            parser.error('Bassmaster scraping requires both --bassmaster-start-year and --bassmaster-end-year')
        bassmaster_years = (args.bassmaster_start_year, args.bassmaster_end_year)

    paths = ValidationPaths()
    paths.ensure()
    out = paths.raw_data / 'historical_outcomes.csv'
    df = collect_historical_outcomes(
        out,
        sample=args.sample,
        source_path=args.source,
        bassmaster_years=bassmaster_years,
        mapping_path=args.mapping,
    )
    print(f'wrote {len(df)} outcome rows to {out}')


if __name__ == '__main__':
    main()
