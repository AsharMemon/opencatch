#!/usr/bin/env python3
"""
Merge shard JSONL outputs from parallel lake-POI harvest jobs into one JSONL.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-csv", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    return parser.parse_args()


def read_plan(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    rows = read_plan(args.plan_csv)
    seen: set[tuple[str, str]] = set()
    kept = 0
    shard_count = 0
    poi_type_counts: Counter[str] = Counter()

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as out:
        for row in rows:
            remote_jsonl = Path(row["remote_output_jsonl"])
            if not remote_jsonl.exists():
                continue
            shard_count += 1
            with remote_jsonl.open(encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    payload = json.loads(raw)
                    key = (str(payload.get("lake_id") or ""), str(payload.get("osm_id") or ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    poi_type_counts[str(payload.get("poi_type") or "unknown")] += 1
                    out.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    kept += 1

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "plan_csv": str(args.plan_csv),
        "shards_merged": shard_count,
        "poi_count": kept,
        "poi_type_counts": dict(poi_type_counts),
        "output_jsonl": str(args.output_jsonl),
    }
    write_json(args.summary_json, summary)

    print(f"Merged {kept} POIs from {shard_count} shard outputs")
    print(f"Output: {args.output_jsonl}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
