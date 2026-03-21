"""Fetch USGS instantaneous values for all enriched dataset events and compute intraday features."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from castline.validation.collectors.usgs import (
    compute_intraday_features,
    fetch_usgs_instantaneous_values,
)

ENRICHED_PATH = PROJECT_ROOT / "castline/validation/data/assembled/validation_dataset_enriched.csv"
OUTPUT_PATH = PROJECT_ROOT / "castline/validation/data/raw/usgs_iv_features.csv"
REQUEST_DELAY = 0.5  # seconds between API requests


def main() -> None:
    df = pd.read_csv(ENRICHED_PATH, dtype={"event_id": str, "site_id": str, "date": str})
    print(f"Loaded {len(df)} rows from enriched dataset")

    # Build lookup: (site_id, date) -> list of event_ids
    combo_to_events: dict[tuple[str, str], list[str]] = {}
    for _, row in df.iterrows():
        key = (str(row["site_id"]), str(row["date"]))
        combo_to_events.setdefault(key, []).append(str(row["event_id"]))

    unique_combos = list(combo_to_events.keys())
    print(f"Unique (site_id, date) combinations: {len(unique_combos)}")

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    results: list[dict] = []
    successes = 0
    failures = 0

    for i, (site_id_raw, date_str) in enumerate(unique_combos):
        # Strip USGS- prefix and zero-pad to 8 digits
        numeric_id = site_id_raw.replace("USGS-", "").strip().zfill(8)
        event_date = pd.Timestamp(date_str).normalize()

        if (i + 1) % 25 == 0 or i == 0:
            print(f"  [{i+1}/{len(unique_combos)}] site={numeric_id} date={date_str}")

        try:
            # Fetch IV data for the event date (include previous day for 6h deltas)
            start_dt = (event_date - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            end_dt = event_date.strftime("%Y-%m-%d")

            iv_df = fetch_usgs_instantaneous_values(
                numeric_id, start_dt, end_dt, session=session, timeout=60
            )

            features = compute_intraday_features(iv_df, event_date)
            successes += 1

            # Assign features to all events sharing this (site_id, date)
            for event_id in combo_to_events[(site_id_raw, date_str)]:
                row_out = {"event_id": event_id}
                row_out.update(features)
                results.append(row_out)

        except Exception as exc:
            failures += 1
            print(
                f"  WARNING: failed site={numeric_id} date={date_str}: {exc}",
                file=sys.stderr,
            )
            # Still produce rows with NaN features so every event_id appears
            for event_id in combo_to_events[(site_id_raw, date_str)]:
                results.append({"event_id": event_id})

        # Rate limiting
        if i < len(unique_combos) - 1:
            time.sleep(REQUEST_DELAY)

    out_df = pd.DataFrame(results)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(OUTPUT_PATH, index=False)

    print(f"\nDone. Successes: {successes}, Failures: {failures}")
    print(f"Output: {OUTPUT_PATH} ({len(out_df)} rows, {len(out_df.columns)} columns)")
    if not out_df.empty:
        feature_cols = [c for c in out_df.columns if c != "event_id"]
        non_null_counts = out_df[feature_cols].notna().sum()
        print(f"Feature coverage:\n{non_null_counts.to_string()}")


if __name__ == "__main__":
    main()
