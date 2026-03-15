#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from castline.validation.collectors.usgs import evaluate_usgs_mapping_candidates
from castline.validation.config import ValidationPaths


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Evaluate ranked Bassmaster->USGS mapping suggestions against real USGS daily-value coverage.'
    )
    parser.add_argument(
        '--outcomes',
        type=Path,
        default=None,
        help='Normalized outcomes CSV with tournament_slug, event_id, and date columns.',
    )
    parser.add_argument(
        '--suggestions',
        type=Path,
        default=None,
        help='Ranked mapping suggestions CSV to evaluate.',
    )
    parser.add_argument(
        '--lookback-days',
        type=int,
        default=7,
        help='Number of days before each event date to request from USGS when checking coverage.',
    )
    args = parser.parse_args()

    paths = ValidationPaths()
    paths.ensure()
    outcomes_path = args.outcomes or (paths.raw_data / 'historical_outcomes.csv')
    suggestions_path = args.suggestions or (paths.raw_data / 'bassmaster_usgs_mapping_suggestions.csv')
    output_path = paths.raw_data / 'bassmaster_usgs_mapping_coverage.csv'

    df = evaluate_usgs_mapping_candidates(
        outcomes_path=outcomes_path,
        suggestions_path=suggestions_path,
        output_path=output_path,
        lookback_days=args.lookback_days,
    )

    recommended = df.loc[df['recommended_by_coverage'] & (df['usable_event_count'] > 0)]
    print(
        f'wrote {len(df)} evaluated candidate rows to {output_path} '
        f'({len(recommended)} tournaments now have a coverage-backed recommendation)'
    )


if __name__ == '__main__':
    main()
