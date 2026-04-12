#!/usr/bin/env python3
"""
Plan and optionally launch sharded lake-POI harvest jobs across Vast workers.

This fans the GPS lake catalog out into start-index/limit shards so we can
harvest lake-local POIs (including marine fuel) in parallel instead of waiting
on one giant sequential Overpass run.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path("/Users/Ashar/Documents/fish")
VASTAI = Path("/Users/Ashar/Library/Python/3.14/bin/vastai")
CATALOG_JSON = ROOT / "data" / "bathymetry" / "gpsnautical" / "gpsnautical_app_lake_catalog.json"
DEPLOY_SCRIPT = ROOT / "ml" / "bathymetry" / "deploy_lake_poi_harvest_vast.sh"
DEFAULT_PLAN_CSV = ROOT / "data" / "bathymetry" / "gpsnautical" / "parallel_lake_poi_harvest_plan.csv"
DEFAULT_SUMMARY_JSON = ROOT / "data" / "bathymetry" / "gpsnautical" / "parallel_lake_poi_harvest_plan_summary.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-json", type=Path, default=CATALOG_JSON)
    parser.add_argument("--plan-csv", type=Path, default=DEFAULT_PLAN_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--rows-per-shard", type=int, default=800)
    parser.add_argument("--max-parallel", type=int, default=8)
    parser.add_argument("--country-filter", default="US,CA")
    parser.add_argument("--jurisdiction-filter", default="")
    parser.add_argument("--delay-seconds", type=float, default=0.6)
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--instance-ids", default="")
    parser.add_argument("--launch", action="store_true")
    return parser.parse_args()


def read_catalog(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def filter_rows(
    rows: list[dict[str, Any]],
    country_filter: str,
    jurisdiction_filter: str,
) -> list[dict[str, Any]]:
    keep_countries = {part.strip() for part in country_filter.split(",") if part.strip()}
    keep_jurisdictions = {part.strip() for part in jurisdiction_filter.split(",") if part.strip()}
    filtered = rows
    if keep_countries:
        filtered = [row for row in filtered if row.get("country") in keep_countries]
    if keep_jurisdictions:
        filtered = [row for row in filtered if row.get("jurisdiction") in keep_jurisdictions]
    return filtered


def build_plan(
    eligible_rows: list[dict[str, Any]],
    rows_per_shard: int,
    country_filter: str,
    jurisdiction_filter: str,
    delay_seconds: float,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    shard_count = math.ceil(len(eligible_rows) / rows_per_shard) if eligible_rows else 0
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base_slug = slugify(jurisdiction_filter or country_filter or "lake_pois")
    plan: list[dict[str, Any]] = []
    for shard_index in range(shard_count):
        start_index = shard_index * rows_per_shard
        limit = min(rows_per_shard, len(eligible_rows) - start_index)
        shard_name = f"{base_slug}_pois_s{shard_index + 1:02d}"
        remote_root = f"/data/lake_poi_{shard_name}_{timestamp}"
        plan.append(
            {
                "job_name": shard_name,
                "catalog_json": str(CATALOG_JSON),
                "country_filter": country_filter,
                "jurisdiction_filter": jurisdiction_filter,
                "eligible_row_count": len(eligible_rows),
                "rows_per_shard": rows_per_shard,
                "shard_index": shard_index + 1,
                "shard_count": shard_count,
                "start_index": start_index,
                "limit": limit,
                "delay_seconds": delay_seconds,
                "timeout_seconds": timeout_seconds,
                "remote_root": remote_root,
                "remote_output_jsonl": f"{remote_root}/lake_pois.jsonl",
                "remote_summary_json": f"{remote_root}/summary.json",
                "remote_log": f"{remote_root}/run.log",
                "instance_id": "",
                "launch_status": "planned",
                "launch_stdout": "",
                "launch_stderr": "",
            }
        )
    return plan


def run_cmd(cmd: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def resolve_ssh(instance_id: str) -> tuple[str, str]:
    out = subprocess.check_output([str(VASTAI), "ssh-url", str(instance_id)], text=True)
    ssh_url = out.strip().splitlines()[-1].strip()
    match = re.match(r"ssh://[^@]+@([^:]+):(\d+)", ssh_url)
    if not match:
        raise RuntimeError(f"Could not parse ssh-url for instance {instance_id}: {ssh_url}")
    return match.group(1), match.group(2)


def ensure_instance_ready(instance_id: str, *, max_wait_seconds: int = 180) -> tuple[str, str]:
    run_cmd([str(VASTAI), "start", "instance", str(instance_id)])
    host, port = resolve_ssh(instance_id)
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        ping = run_cmd([
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            "-p", port,
            f"root@{host}",
            "echo ready",
        ])
        if ping.returncode == 0 and "ready" in ping.stdout:
            return host, port
        time.sleep(5)
    raise RuntimeError(f"Instance {instance_id} did not become SSH-ready in time")


def launch_row(row: dict[str, Any], instance_id: str) -> dict[str, Any]:
    try:
        host, port = ensure_instance_ready(instance_id)
    except Exception as exc:  # noqa: BLE001
        row["instance_id"] = instance_id
        row["launch_status"] = "launch_failed"
        row["launch_stderr"] = str(exc)
        return row

    deploy = run_cmd([
        "bash",
        str(DEPLOY_SCRIPT),
        host,
        port,
        row["remote_root"],
    ])
    row["instance_id"] = instance_id
    if deploy.returncode != 0:
        row["launch_status"] = "launch_failed"
        row["launch_stdout"] = deploy.stdout.strip()[-4000:]
        row["launch_stderr"] = deploy.stderr.strip()[-4000:]
        return row

    remote_catalog = f"{row['remote_root']}/gpsnautical_app_lake_catalog.json"
    remote_cmd = (
        "nohup env "
        + f"RUN_ROOT={shlex.quote(row['remote_root'])} "
        + f"CATALOG_PATH={shlex.quote(remote_catalog)} "
        + f"OUTPUT_JSONL={shlex.quote(row['remote_output_jsonl'])} "
        + f"SUMMARY_JSON={shlex.quote(row['remote_summary_json'])} "
        + f"COUNTRY_FILTER={shlex.quote(row['country_filter'])} "
        + f"STATE_FILTER={shlex.quote(row['jurisdiction_filter'])} "
        + f"START_INDEX={row['start_index']} "
        + f"LIMIT={row['limit']} "
        + f"DELAY_SECONDS={row['delay_seconds']} "
        + f"TIMEOUT_SECONDS={row['timeout_seconds']} "
        + "bash /root/ml/bathymetry/run_lake_poi_harvest_vast.sh "
        + f"> {shlex.quote(row['remote_log'])} 2>&1 </dev/null & echo $!"
    )
    launch = run_cmd([
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-p", port,
        f"root@{host}",
        remote_cmd,
    ])
    row["launch_status"] = "launched" if launch.returncode == 0 else "launch_failed"
    row["launch_stdout"] = launch.stdout.strip()[-4000:]
    row["launch_stderr"] = launch.stderr.strip()[-4000:]
    return row


def main() -> None:
    args = parse_args()
    catalog_rows = read_catalog(args.catalog_json)
    eligible_rows = filter_rows(catalog_rows, args.country_filter, args.jurisdiction_filter)
    if not eligible_rows:
        raise SystemExit("No eligible catalog rows after filtering.")

    plan = build_plan(
        eligible_rows,
        args.rows_per_shard,
        args.country_filter,
        args.jurisdiction_filter,
        args.delay_seconds,
        args.timeout_seconds,
    )

    instance_ids = [part.strip() for part in args.instance_ids.split(",") if part.strip()]
    if args.launch:
        if not instance_ids:
            raise SystemExit("--launch requires --instance-ids")
        launch_count = min(args.max_parallel, len(plan), len(instance_ids))
        for index in range(launch_count):
            plan[index] = launch_row(plan[index], instance_ids[index])

    write_csv(args.plan_csv, plan)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "catalog_json": str(args.catalog_json),
        "eligible_row_count": len(eligible_rows),
        "rows_per_shard": args.rows_per_shard,
        "plan_row_count": len(plan),
        "max_parallel": args.max_parallel,
        "instance_ids": instance_ids,
        "launched_count": sum(1 for row in plan if row["launch_status"] == "launched"),
        "planned_count": sum(1 for row in plan if row["launch_status"] == "planned"),
        "failed_count": sum(1 for row in plan if row["launch_status"] == "launch_failed"),
        "country_filter": args.country_filter,
        "jurisdiction_filter": args.jurisdiction_filter,
        "plan_csv": str(args.plan_csv),
    }
    write_json(args.summary_json, summary)

    print(f"Eligible lakes: {len(eligible_rows)}")
    print(f"Plan rows: {len(plan)}")
    print(f"Plan CSV: {args.plan_csv}")
    print(f"Summary: {args.summary_json}")
    if args.launch:
        print(f"Launched: {summary['launched_count']}")


if __name__ == "__main__":
    main()
