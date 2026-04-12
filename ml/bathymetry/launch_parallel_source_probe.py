#!/usr/bin/env python3
"""
Plan and optionally launch sharded GPS Nautical page-source probe runs.

This is the source-matching equivalent of the PDF shard launcher: it splits the
source-master queue into offset/limit shards, writes a machine-readable plan,
and can launch multiple threaded probe workers in parallel. Each worker can
scan many lake detail pages concurrently, so the total throughput scales as:

    shard_processes * workers_per_shard

That lets us push source matching into the hundreds of simultaneous requests
without turning one giant monolithic run into a single-point bottleneck.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path("/Users/Ashar/Documents/fish")
PROBE_SCRIPT = ROOT / "ml" / "bathymetry" / "probe_gpsnautical_page_sources.py"
DEFAULT_INPUT = ROOT / "data" / "bathymetry" / "gpsnautical" / "gpsnautical_source_master.csv"
DEFAULT_PLAN_CSV = ROOT / "data" / "bathymetry" / "gpsnautical" / "parallel_source_probe_plan.csv"
DEFAULT_SUMMARY_JSON = ROOT / "data" / "bathymetry" / "gpsnautical" / "parallel_source_probe_plan_summary.json"
DEFAULT_OUT_DIR = ROOT / "data" / "bathymetry" / "gpsnautical" / "parallel_source_probe_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--classification", default="needs_jurisdiction_search")
    parser.add_argument("--jurisdictions", default="", help="Comma-separated jurisdictions to include.")
    parser.add_argument("--rows-per-shard", type=int, default=500)
    parser.add_argument("--workers-per-shard", type=int, default=16)
    parser.add_argument("--max-parallel-shards", type=int, default=8)
    parser.add_argument("--shard-offset", type=int, default=0, help="0-based shard index to start launching from.")
    parser.add_argument("--delay-seconds", type=float, default=0.05)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--plan-csv", type=Path, default=DEFAULT_PLAN_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--launch-local", action="store_true")
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def filter_target_rows(
    rows: list[dict[str, str]],
    classification: str,
    jurisdictions: set[str],
) -> list[dict[str, str]]:
    targets = [
        row
        for row in rows
        if row.get("classification") == classification
        and not row.get("page_source_family")
        and row.get("lake_url")
        and (not jurisdictions or (row.get("jurisdiction") or "") in jurisdictions)
    ]
    targets.sort(
        key=lambda row: (
            -(int((row.get("work_priority") or "0").strip() or "0")),
            row.get("jurisdiction") or "",
            row.get("lake_name") or "",
        )
    )
    return targets


def build_plan(
    rows: list[dict[str, str]],
    args: argparse.Namespace,
    jurisdictions: set[str],
) -> list[dict[str, Any]]:
    shard_count = math.ceil(len(rows) / args.rows_per_shard) if rows else 0
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    plan: list[dict[str, Any]] = []
    for shard_index in range(shard_count):
        offset = shard_index * args.rows_per_shard
        limit = min(args.rows_per_shard, len(rows) - offset)
        shard_name = f"probe_s{shard_index + 1:03d}"
        out_csv = args.out_dir / f"{timestamp}_{shard_name}.csv"
        summary_json = args.out_dir / f"{timestamp}_{shard_name}.summary.json"
        log_path = args.out_dir / f"{timestamp}_{shard_name}.log"
        cmd = [
            "python3",
            str(PROBE_SCRIPT),
            "--input",
            str(args.input),
            "--classification",
            args.classification,
            "--offset",
            str(offset),
            "--limit",
            str(limit),
            "--workers",
            str(args.workers_per_shard),
            "--delay-seconds",
            str(args.delay_seconds),
            "--timeout",
            str(args.timeout),
            "--output-csv",
            str(out_csv),
            "--summary-json",
            str(summary_json),
        ]
        if jurisdictions:
            cmd.extend(["--jurisdictions", ",".join(sorted(jurisdictions))])
        plan.append(
            {
                "shard_name": shard_name,
                "offset": offset,
                "limit": limit,
                "workers": args.workers_per_shard,
                "output_csv": str(out_csv),
                "summary_json": str(summary_json),
                "log_path": str(log_path),
                "command": " ".join(cmd),
                "launch_status": "planned",
                "pid": "",
            }
        )
    return plan


def launch_local(plan: list[dict[str, Any]], max_parallel: int, shard_offset: int) -> list[dict[str, Any]]:
    eligible = plan[shard_offset:]
    launch_count = min(max_parallel, len(eligible))
    for row in eligible[:launch_count]:
        log_path = Path(row["log_path"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(  # noqa: S603
            row["command"],
            shell=True,
            stdout=handle,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT),
            start_new_session=True,
            env=dict(os.environ),
        )
        row["launch_status"] = "launched"
        row["pid"] = str(proc.pid)
    return plan


def main() -> None:
    args = parse_args()
    jurisdictions = {part.strip() for part in args.jurisdictions.split(",") if part.strip()}
    rows = read_rows(args.input)
    target_rows = filter_target_rows(rows, args.classification, jurisdictions)
    plan = build_plan(target_rows, args, jurisdictions)

    if args.launch_local:
        plan = launch_local(plan, args.max_parallel_shards, args.shard_offset)

    write_csv(args.plan_csv, plan)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input": str(args.input),
        "classification": args.classification,
        "jurisdictions": sorted(jurisdictions),
        "eligible_row_count": len(target_rows),
        "rows_per_shard": args.rows_per_shard,
        "workers_per_shard": args.workers_per_shard,
        "shard_count": len(plan),
        "max_parallel_shards": args.max_parallel_shards,
        "shard_offset": args.shard_offset,
        "potential_parallel_requests": args.max_parallel_shards * args.workers_per_shard,
        "launch_local": args.launch_local,
        "launched_count": sum(1 for row in plan if row["launch_status"] == "launched"),
        "plan_csv": str(args.plan_csv),
    }
    write_json(args.summary_json, summary)

    print(f"Eligible rows: {len(target_rows)}")
    print(f"Shard count: {len(plan)}")
    print(f"Plan CSV: {args.plan_csv}")
    print(f"Summary: {args.summary_json}")
    if args.launch_local:
        print(f"Launched shards: {summary['launched_count']}")


if __name__ == "__main__":
    main()
