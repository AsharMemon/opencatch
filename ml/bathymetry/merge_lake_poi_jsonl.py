#!/usr/bin/env python3
"""
Merge one or more lake-POI JSONL files into a single deduplicated JSONL corpus.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-jsonl", type=Path, action="append", required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    return parser.parse_args()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    seen: set[tuple[str, str]] = set()
    poi_type_counts: Counter[str] = Counter()
    lake_ids: set[str] = set()
    kept = 0
    source_counts: Counter[str] = Counter()

    with args.output_jsonl.open("w", encoding="utf-8") as out:
        for input_path in args.input_jsonl:
            source_counts[str(input_path)] += 1
            with input_path.open(encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    payload = json.loads(raw)
                    key = (str(payload.get("lake_id") or ""), str(payload.get("osm_id") or ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    lake_id = str(payload.get("lake_id") or "")
                    if lake_id:
                        lake_ids.add(lake_id)
                    poi_type_counts[str(payload.get("poi_type") or "unknown")] += 1
                    out.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    kept += 1

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": [str(path) for path in args.input_jsonl],
        "input_file_count": len(args.input_jsonl),
        "poi_count": kept,
        "lake_count": len(lake_ids),
        "poi_type_counts": dict(poi_type_counts),
        "output_jsonl": str(args.output_jsonl),
    }
    write_json(args.summary_json, summary)

    print(f"Merged {kept} POIs across {len(lake_ids)} lakes")
    print(f"Output: {args.output_jsonl}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
