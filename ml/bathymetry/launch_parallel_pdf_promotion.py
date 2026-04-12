#!/usr/bin/env python3
"""
Plan and optionally launch sharded PDF-promotion jobs in parallel.

This is the orchestration layer for "digitize all lakes in parallel, 10 at a
time" style execution. It discovers or accepts inventory CSVs, splits them into
offset/limit shards, writes a machine-readable launch plan, and can dispatch a
bounded number of shards to Vast using the existing deploy script.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path("/Users/Ashar/Documents/fish")
DEFAULT_PLAN_CSV = ROOT / "data" / "bathymetry" / "parallel_pdf_promotion_plan.csv"
DEFAULT_SUMMARY_JSON = ROOT / "data" / "bathymetry" / "parallel_pdf_promotion_plan_summary.json"
DEPLOY_SCRIPT = ROOT / "ml" / "bathymetry" / "deploy_pdf_promotion_vast.sh"

DEFAULT_DISCOVERY_GLOBS = [
    str(ROOT / "data" / "bathymetry" / "*" / "*_gpsnautical_matched_inventory.csv"),
]

DEFAULT_INSECURE_JURISDICTIONS = {
    "Mississippi",
}

DEFAULT_LAKE_POLYGONS_BY_JURISDICTION = {
    "Georgia": str(ROOT / "data" / "bathymetry" / "ga" / "ga_pfa_polygons.geojson"),
    "Maryland": str(ROOT / "data" / "bathymetry" / "md" / "md_mgs_waterbody_polygons.geojson"),
    "Mississippi": str(ROOT / "data" / "bathymetry" / "ms" / "ms_nhd_waterbody_polygons.geojson"),
    "New Brunswick": str(ROOT / "data" / "bathymetry" / "nb" / "nbhn_waterbody_polygons.geojson"),
    "New Jersey": str(ROOT / "data" / "bathymetry" / "nj" / "nj_usgs_waterbody_polygons.geojson"),
    "South Dakota": str(ROOT / "data" / "bathymetry" / "sd" / "sd_big_waterbody_polygons.geojson"),
    "Virginia": str(ROOT / "data" / "bathymetry" / "va" / "va_usgs_waterbody_polygons.geojson"),
    "West Virginia": str(ROOT / "data" / "bathymetry" / "wv" / "wv_usgs_waterbody_polygons.geojson"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", action="append", default=[], help="Inventory CSV path. Repeatable.")
    parser.add_argument(
        "--discover-glob",
        action="append",
        default=[],
        help="Glob pattern(s) used when --inventory is omitted. Repeatable.",
    )
    parser.add_argument("--rows-per-shard", type=int, default=20)
    parser.add_argument("--max-parallel", type=int, default=10)
    parser.add_argument(
        "--instance-ids",
        default="",
        help="Comma-separated Vast instance ids. If omitted, the plan is generated but not launched.",
    )
    parser.add_argument(
        "--insecure-jurisdictions",
        default="",
        help="Comma-separated jurisdictions that require INSECURE=1 for downloads.",
    )
    parser.add_argument("--plan-csv", type=Path, default=DEFAULT_PLAN_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--launch", action="store_true", help="Actually launch the first N shards.")
    return parser.parse_args()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def infer_jurisdiction(rows: list[dict[str, str]], path: Path) -> str:
    if rows:
        for key in ("jurisdiction", "region_or_state", "state", "province"):
            value = (rows[0].get(key) or "").strip()
            if value:
                return value
    stem = path.stem.replace("_gpsnautical_matched_inventory", "")
    return stem.replace("_", " ").title()


def discover_inventory_paths(args: argparse.Namespace) -> list[Path]:
    explicit = [Path(value) for value in args.inventory]
    if explicit:
        return explicit

    globs = args.discover_glob or DEFAULT_DISCOVERY_GLOBS
    discovered: list[Path] = []
    for pattern in globs:
        if pattern.startswith(str(ROOT) + "/"):
            rel = pattern.replace(str(ROOT) + "/", "", 1)
            discovered.extend(sorted(ROOT.glob(rel)))
        elif pattern.startswith("/"):
            discovered.extend(sorted(Path("/").glob(pattern.lstrip("/"))))
        else:
            discovered.extend(sorted(ROOT.glob(pattern)))
    unique: dict[str, Path] = {}
    for path in discovered:
        if path.is_file():
            unique[str(path)] = path
    return list(unique.values())


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


def build_plan(
    inventories: list[Path],
    rows_per_shard: int,
    insecure_jurisdictions: set[str],
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for inventory_path in inventories:
        rows = read_csv_rows(inventory_path)
        jurisdiction = infer_jurisdiction(rows, inventory_path)
        shard_count = max(1, math.ceil(len(rows) / rows_per_shard)) if rows else 0
        for shard_index in range(shard_count):
            offset = shard_index * rows_per_shard
            limit = min(rows_per_shard, len(rows) - offset)
            job_slug = slugify(jurisdiction)
            shard_name = f"{job_slug}_s{shard_index + 1:02d}"
            plan.append(
                {
                    "jurisdiction": jurisdiction,
                    "inventory_csv": str(inventory_path),
                    "inventory_rows": len(rows),
                    "rows_per_shard": rows_per_shard,
                    "shard_index": shard_index + 1,
                    "shard_count": shard_count,
                    "offset": offset,
                    "limit": limit,
                    "job_name": shard_name,
                    "remote_root": f"/data/pdf_promotion_{shard_name}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}",
                    "insecure": "1" if jurisdiction in insecure_jurisdictions else "0",
                    "lake_polygons_path": DEFAULT_LAKE_POLYGONS_BY_JURISDICTION.get(jurisdiction, ""),
                    "launch_status": "planned",
                    "instance_id": "",
                }
            )
    return plan


def launch_plan_rows(plan: list[dict[str, Any]], instance_ids: list[str], max_parallel: int) -> list[dict[str, Any]]:
    launch_count = min(max_parallel, len(plan), len(instance_ids))
    for i in range(launch_count):
        row = plan[i]
        instance_id = instance_ids[i]
        env = {
            **dict(os.environ),
            "REMOTE_ROOT": row["remote_root"],
            "OFFSET": str(row["offset"]),
            "LIMIT": str(row["limit"]),
            "INSECURE": row["insecure"],
        }
        if row.get("lake_polygons_path"):
            env["LAKE_POLYGONS_PATH"] = row["lake_polygons_path"]
        cmd = [
            "bash",
            str(DEPLOY_SCRIPT),
            row["job_name"],
            row["inventory_csv"],
            instance_id,
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        row["instance_id"] = instance_id
        row["launch_status"] = "launched" if result.returncode == 0 else "launch_failed"
        row["launch_returncode"] = result.returncode
        row["launch_stdout"] = result.stdout.strip()[-4000:]
        row["launch_stderr"] = result.stderr.strip()[-4000:]
    return plan


def main() -> None:
    args = parse_args()
    inventories = discover_inventory_paths(args)
    if not inventories:
        raise SystemExit("No inventory CSVs found.")

    insecure = {part.strip() for part in args.insecure_jurisdictions.split(",") if part.strip()}
    insecure |= DEFAULT_INSECURE_JURISDICTIONS

    plan = build_plan(inventories, args.rows_per_shard, insecure)

    instance_ids = [part.strip() for part in args.instance_ids.split(",") if part.strip()]
    if args.launch:
        if not instance_ids:
            raise SystemExit("--launch requires --instance-ids")
        plan = launch_plan_rows(plan, instance_ids, args.max_parallel)

    write_csv(args.plan_csv, plan)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inventory_count": len(inventories),
        "plan_row_count": len(plan),
        "max_parallel": args.max_parallel,
        "instance_ids": instance_ids,
        "launched_count": sum(1 for row in plan if row.get("launch_status") == "launched"),
        "planned_count": sum(1 for row in plan if row.get("launch_status") == "planned"),
        "failed_count": sum(1 for row in plan if row.get("launch_status") == "launch_failed"),
        "plan_csv": str(args.plan_csv),
    }
    write_json(args.summary_json, summary)

    print(f"Inventories: {len(inventories)}")
    print(f"Plan rows: {len(plan)}")
    print(f"Plan CSV: {args.plan_csv}")
    print(f"Summary: {args.summary_json}")
    if args.launch:
        print(f"Launched: {summary['launched_count']}")


if __name__ == "__main__":
    main()
