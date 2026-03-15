from __future__ import annotations

import argparse
from pathlib import Path

from castline.validation.evaluate import evaluate_csv
from castline.validation.reporting.summary import render_markdown_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CASTLINE Phase 0 baseline-vs-environment validation")
    parser.add_argument("dataset", type=Path, help="CSV containing event_id, year, target, and numeric features")
    parser.add_argument("--train-max-year", type=int, default=2022)
    parser.add_argument("--test-min-year", type=int, default=2023)
    parser.add_argument("--write-report", type=Path, default=None)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    comparison = evaluate_csv(args.dataset, train_max_year=args.train_max_year, test_min_year=args.test_min_year)
    report = render_markdown_report(comparison, args.dataset)
    print(report)
    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
