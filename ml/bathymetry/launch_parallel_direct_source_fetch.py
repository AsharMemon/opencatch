#!/usr/bin/env python3
"""
Plan and optionally launch parallel direct-source fetch jobs from the
GPS Nautical direct-source inventory manifest.

This is the direct-source analogue to the PDF shard launcher: it turns the
materialized jurisdiction inventories into executable family-specific jobs and
fans out the supported subset across Vast workers.

Currently executable lanes:
  - usace_reservoir_program -> fetch_usace_direct_sources.py
  - usbr_reservoir_program  -> deploy_usbr_survey_batch_vast.sh
  - tva_reservoir_program   -> fetch_tva_reservoir_links.py
  - state_dnr_program       -> fetch_state_bathymetry.py --source wisconsin
  - state_dec_program       -> build_ny_dec_contour_index.py
  - state_agency_program    -> build_pa_pfbc_direct_sources.py (Pennsylvania)

Any remaining unsupported lanes are still written into the plan so we keep a
complete queue instead of pretending they're handled.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path("/Users/Ashar/Documents/fish")
VASTAI = Path("/Users/Ashar/Library/Python/3.14/bin/vastai")
DEFAULT_MANIFEST_CSV = ROOT / "data" / "bathymetry" / "gpsnautical" / "gpsnautical_direct_source_inventory_manifest.csv"
DEFAULT_PLAN_CSV = ROOT / "data" / "bathymetry" / "gpsnautical" / "parallel_direct_source_fetch_plan.csv"
DEFAULT_SUMMARY_JSON = ROOT / "data" / "bathymetry" / "gpsnautical" / "parallel_direct_source_fetch_plan_summary.json"
USBR_DEPLOY_SCRIPT = ROOT / "ml" / "bathymetry" / "reservoir" / "deploy_usbr_survey_batch_vast.sh"
STATE_FETCH_SCRIPT = ROOT / "ml" / "bathymetry" / "fetch_state_bathymetry.py"
NY_DEC_INDEX_SCRIPT = ROOT / "ml" / "bathymetry" / "build_ny_dec_contour_index.py"
USACE_DIRECT_SCRIPT = ROOT / "ml" / "bathymetry" / "fetch_usace_direct_sources.py"
TVA_LINK_SCRIPT = ROOT / "ml" / "bathymetry" / "fetch_tva_reservoir_links.py"
PA_PFBC_SCRIPT = ROOT / "ml" / "bathymetry" / "build_pa_pfbc_direct_sources.py"
NY_DEC_INPUT_CSV = ROOT / "data" / "bathymetry" / "ny" / "ny_dec_contour_pages.csv"
PA_PFBC_FOOTPRINTS = ROOT / "data" / "bathymetry" / "pa" / "pa_lake_footprints.geojson"

JURISDICTION_TO_USPS = {
    "Arizona": "AZ",
    "California": "CA",
    "Colorado": "CO",
    "Idaho": "ID",
    "Nevada": "NV",
    "New Mexico": "NM",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Wyoming": "WY",
}

SUPPORTED_LANES = {
    "usace_reservoir_program",
    "usbr_reservoir_program",
    "tva_reservoir_program",
    "state_dnr_program",
    "state_dec_program",
    "state_agency_program",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-csv", type=Path, default=DEFAULT_MANIFEST_CSV)
    parser.add_argument("--plan-csv", type=Path, default=DEFAULT_PLAN_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument(
        "--skip-plan-csv",
        type=Path,
        action="append",
        default=[],
        help="Optional prior launch plan; rows already marked launched are skipped by jurisdiction+lane. Repeatable.",
    )
    parser.add_argument("--instance-ids", default="", help="Comma-separated Vast instance ids.")
    parser.add_argument("--launch", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
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


def read_inventory_rows(path: Path) -> list[dict[str, str]]:
    return read_csv(path)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def build_plan(manifest_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    plan: list[dict[str, Any]] = []

    for manifest_row in manifest_rows:
        jurisdiction = (manifest_row.get("jurisdiction") or "").strip()
        dir_slug = (manifest_row.get("dir_slug") or "").strip()
        inventory_csv = Path(manifest_row["inventory_csv"])
        inventory_rows = read_inventory_rows(inventory_csv)

        grouped: dict[str, list[dict[str, str]]] = {}
        for row in inventory_rows:
            lane = (row.get("fetch_inventory_lane") or "").strip() or "unknown"
            grouped.setdefault(lane, []).append(row)

        for lane, lane_rows in sorted(grouped.items()):
            lane_slug = lane.replace("_program", "").replace("_", "-")
            job_name = f"direct_{slugify(jurisdiction)}_{lane_slug}"
            launch_mode = "unsupported"
            state_source = ""
            state_code = ""
            launch_reason = ""

            if lane == "usace_reservoir_program":
                launch_mode = "usace_direct_inventory"
                launch_reason = "USACE NID + IENC manifest matcher is available"
            elif lane == "usbr_reservoir_program":
                state_code = JURISDICTION_TO_USPS.get(jurisdiction, "")
                if state_code:
                    launch_mode = "usbr_batch"
                    launch_reason = "existing USBR batch fetcher is available"
                else:
                    launch_reason = "USBR state code mapping missing"
            elif lane == "tva_reservoir_program":
                launch_mode = "tva_link_resolver"
                launch_reason = "TVA official-link resolver is available"
            elif lane == "state_dnr_program" and jurisdiction == "Wisconsin":
                launch_mode = "state_bathymetry"
                state_source = "wisconsin"
                launch_reason = "existing multi-state bathymetry fetcher supports Wisconsin"
            elif lane == "state_dec_program" and jurisdiction == "New York":
                launch_mode = "ny_dec_index"
                launch_reason = "existing New York DEC contour index builder is available"
            elif lane == "state_agency_program" and jurisdiction == "Pennsylvania":
                launch_mode = "pa_pfbc_inventory"
                launch_reason = "existing Pennsylvania PFBC footprint lane can be materialized directly"
            else:
                launch_reason = "lane materialized, but no honest executable fetcher is wired yet"

            remote_root = f"/data/{job_name}_{timestamp}"
            plan.append(
                {
                    "jurisdiction": jurisdiction,
                    "dir_slug": dir_slug,
                    "fetch_inventory_lane": lane,
                    "row_count": len(lane_rows),
                    "job_name": job_name,
                    "launch_mode": launch_mode,
                    "launch_reason": launch_reason,
                    "inventory_csv": str(inventory_csv),
                    "state_code": state_code,
                    "state_source": state_source,
                    "remote_root": remote_root,
                    "instance_id": "",
                    "launch_status": "planned" if launch_mode != "unsupported" else "planner_only",
                    "launch_stdout": "",
                    "launch_stderr": "",
                    "remote_log": f"{remote_root}/run.log",
                }
            )

    supported = [row for row in plan if row["launch_mode"] != "unsupported"]
    unsupported = [row for row in plan if row["launch_mode"] == "unsupported"]
    supported.sort(key=lambda row: (-int(row["row_count"]), row["jurisdiction"], row["fetch_inventory_lane"]))
    unsupported.sort(key=lambda row: (-int(row["row_count"]), row["jurisdiction"], row["fetch_inventory_lane"]))
    return supported + unsupported


def resolve_ssh(instance_id: str) -> tuple[str, str]:
    out = subprocess.check_output([str(VASTAI), "ssh-url", str(instance_id)], text=True)
    ssh_url = out.strip().splitlines()[-1].strip()
    match = re.match(r"ssh://[^@]+@([^:]+):(\d+)", ssh_url)
    if not match:
        raise RuntimeError(f"Could not parse ssh-url for instance {instance_id}: {ssh_url}")
    return match.group(1), match.group(2)


def run_cmd(cmd: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def launch_usbr_job(row: dict[str, Any], instance_id: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["REMOTE_ROOT"] = row["remote_root"]
    cmd = [
        "bash",
        str(USBR_DEPLOY_SCRIPT),
        row["job_name"],
        row["state_code"],
        instance_id,
    ]
    return run_cmd(cmd, env=env)


def launch_usace_job(row: dict[str, Any], instance_id: str) -> subprocess.CompletedProcess[str]:
    host, port = resolve_ssh(instance_id)
    remote_code_root = "/root/ml/bathymetry"
    remote_script = f"{remote_code_root}/fetch_usace_direct_sources.py"
    remote_inventory = f"{row['remote_root']}/inventory.csv"
    remote_log = f"{row['remote_root']}/run.log"

    setup = run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}",
        f"mkdir -p {shlex.quote(remote_code_root)} {shlex.quote(row['remote_root'])}",
    ])
    if setup.returncode != 0:
        return setup

    for local_path, remote_path in [
        (USACE_DIRECT_SCRIPT, f"root@{host}:{remote_code_root}/"),
        (Path(row["inventory_csv"]), f"root@{host}:{remote_inventory}"),
    ]:
        copy = run_cmd([
            "scp", "-o", "StrictHostKeyChecking=no", "-P", port,
            str(local_path),
            remote_path,
        ])
        if copy.returncode != 0:
            return copy

    remote_cmd = (
        "nohup bash -lc "
        + shlex.quote(
            "python3 -m pip install --quiet --upgrade pip && "
            "python3 -m pip install --quiet --no-cache-dir requests pandas >/dev/null 2>&1 && "
            f"python3 {remote_script} --inventory-csv {remote_inventory} --output-dir {row['remote_root']} "
            f"> {remote_log} 2>&1"
        )
        + " >/dev/null 2>&1 </dev/null & echo $!"
    )
    return run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}", remote_cmd,
    ])


def launch_tva_job(row: dict[str, Any], instance_id: str) -> subprocess.CompletedProcess[str]:
    host, port = resolve_ssh(instance_id)
    remote_code_root = "/root/ml/bathymetry"
    remote_script = f"{remote_code_root}/fetch_tva_reservoir_links.py"
    remote_inventory = f"{row['remote_root']}/inventory.csv"
    remote_log = f"{row['remote_root']}/run.log"

    setup = run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}",
        f"mkdir -p {shlex.quote(remote_code_root)} {shlex.quote(row['remote_root'])}",
    ])
    if setup.returncode != 0:
        return setup

    for local_path, remote_path in [
        (TVA_LINK_SCRIPT, f"root@{host}:{remote_code_root}/"),
        (Path(row["inventory_csv"]), f"root@{host}:{remote_inventory}"),
    ]:
        copy = run_cmd([
            "scp", "-o", "StrictHostKeyChecking=no", "-P", port,
            str(local_path),
            remote_path,
        ])
        if copy.returncode != 0:
            return copy

    remote_cmd = (
        "nohup bash -lc "
        + shlex.quote(
            "python3 -m pip install --quiet --upgrade pip && "
            "python3 -m pip install --quiet --no-cache-dir requests >/dev/null 2>&1 && "
            f"python3 {remote_script} --inventory-csv {remote_inventory} --output-dir {row['remote_root']} "
            f"> {remote_log} 2>&1"
        )
        + " >/dev/null 2>&1 </dev/null & echo $!"
    )
    return run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}", remote_cmd,
    ])


def launch_pa_pfbc_job(row: dict[str, Any], instance_id: str) -> subprocess.CompletedProcess[str]:
    host, port = resolve_ssh(instance_id)
    remote_code_root = "/root/ml/bathymetry"
    remote_script = f"{remote_code_root}/build_pa_pfbc_direct_sources.py"
    remote_inventory = f"{row['remote_root']}/inventory.csv"
    remote_footprints = f"{row['remote_root']}/pa_lake_footprints.geojson"
    remote_log = f"{row['remote_root']}/run.log"

    setup = run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}",
        f"mkdir -p {shlex.quote(remote_code_root)} {shlex.quote(row['remote_root'])}",
    ])
    if setup.returncode != 0:
        return setup

    for local_path, remote_path in [
        (PA_PFBC_SCRIPT, f"root@{host}:{remote_code_root}/"),
        (Path(row["inventory_csv"]), f"root@{host}:{remote_inventory}"),
        (PA_PFBC_FOOTPRINTS, f"root@{host}:{remote_footprints}"),
    ]:
        copy = run_cmd([
            "scp", "-o", "StrictHostKeyChecking=no", "-P", port,
            str(local_path),
            remote_path,
        ])
        if copy.returncode != 0:
            return copy

    remote_cmd = (
        "nohup bash -lc "
        + shlex.quote(
            f"python3 {remote_script} --inventory-csv {remote_inventory} "
            f"--footprints-geojson {remote_footprints} --output-dir {row['remote_root']} "
            f"> {remote_log} 2>&1"
        )
        + " >/dev/null 2>&1 </dev/null & echo $!"
    )
    return run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}", remote_cmd,
    ])


def launch_state_bathymetry_job(row: dict[str, Any], instance_id: str) -> subprocess.CompletedProcess[str]:
    host, port = resolve_ssh(instance_id)
    remote_code_root = "/root/ml/bathymetry"
    remote_script = f"{remote_code_root}/fetch_state_bathymetry.py"
    remote_output = f"{row['remote_root']}/output"
    remote_log = f"{row['remote_root']}/run.log"

    setup = run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}",
        f"mkdir -p {shlex.quote(remote_code_root)} {shlex.quote(row['remote_root'])}",
    ])
    if setup.returncode != 0:
        return setup

    copy = run_cmd([
        "scp", "-o", "StrictHostKeyChecking=no", "-P", port,
        str(STATE_FETCH_SCRIPT),
        f"root@{host}:{remote_code_root}/",
    ])
    if copy.returncode != 0:
        return copy

    remote_cmd = (
        "nohup bash -lc "
        + shlex.quote(
            "python3 -m pip install --quiet --upgrade pip && "
            "python3 -m pip install --quiet --no-cache-dir requests numpy tqdm pyarrow >/dev/null 2>&1 && "
            f"python3 {remote_script} --output {remote_output} --source {row['state_source']} "
            f"> {remote_log} 2>&1"
        )
        + " >/dev/null 2>&1 </dev/null & echo $!"
    )
    return run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}", remote_cmd,
    ])


def launch_ny_dec_job(row: dict[str, Any], instance_id: str) -> subprocess.CompletedProcess[str]:
    host, port = resolve_ssh(instance_id)
    remote_code_root = "/root/ml/bathymetry"
    remote_script = f"{remote_code_root}/build_ny_dec_contour_index.py"
    remote_input = f"{row['remote_root']}/ny_dec_contour_pages.csv"
    remote_output_csv = f"{row['remote_root']}/ny_dec_contour_inventory.csv"
    remote_output_geojson = f"{row['remote_root']}/ny_waterbody_index.geojson"
    remote_log = f"{row['remote_root']}/run.log"

    setup = run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}",
        f"mkdir -p {shlex.quote(remote_code_root)} {shlex.quote(row['remote_root'])}",
    ])
    if setup.returncode != 0:
        return setup

    for local_path, remote_path in [
        (NY_DEC_INDEX_SCRIPT, f"root@{host}:{remote_code_root}/"),
        (NY_DEC_INPUT_CSV, f"root@{host}:{remote_input}"),
    ]:
        copy = run_cmd([
            "scp", "-o", "StrictHostKeyChecking=no", "-P", port,
            str(local_path),
            remote_path,
        ])
        if copy.returncode != 0:
            return copy

    remote_cmd = (
        "nohup bash -lc "
        + shlex.quote(
            "python3 -m pip install --quiet --upgrade pip && "
            "python3 -m pip install --quiet --no-cache-dir requests >/dev/null 2>&1 && "
            f"python3 {remote_script} --input-csv {remote_input} "
            f"--output-csv {remote_output_csv} --output-geojson {remote_output_geojson} "
            f"> {remote_log} 2>&1"
        )
        + " >/dev/null 2>&1 </dev/null & echo $!"
    )
    return run_cmd([
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-p", port, f"root@{host}", remote_cmd,
    ])


def launch_rows(plan: list[dict[str, Any]], instance_ids: list[str]) -> list[dict[str, Any]]:
    supported = [row for row in plan if row["launch_mode"] != "unsupported"]
    launch_count = min(len(supported), len(instance_ids))
    for row, instance_id in zip(supported[:launch_count], instance_ids):
        row["instance_id"] = instance_id
        if row["launch_mode"] == "usace_direct_inventory":
            result = launch_usace_job(row, instance_id)
        elif row["launch_mode"] == "usbr_batch":
            result = launch_usbr_job(row, instance_id)
        elif row["launch_mode"] == "tva_link_resolver":
            result = launch_tva_job(row, instance_id)
        elif row["launch_mode"] == "state_bathymetry":
            result = launch_state_bathymetry_job(row, instance_id)
        elif row["launch_mode"] == "ny_dec_index":
            result = launch_ny_dec_job(row, instance_id)
        elif row["launch_mode"] == "pa_pfbc_inventory":
            result = launch_pa_pfbc_job(row, instance_id)
        else:
            continue

        row["launch_status"] = "launched" if result.returncode == 0 else "launch_failed"
        row["launch_stdout"] = (result.stdout or "").strip()[-4000:]
        row["launch_stderr"] = (result.stderr or "").strip()[-4000:]
    return plan


def filter_previously_launched(
    plan: list[dict[str, Any]],
    skip_plan_csvs: list[Path] | None,
) -> list[dict[str, Any]]:
    if not skip_plan_csvs:
        return plan

    launched_pairs: set[tuple[str, str]] = set()
    for skip_plan_csv in skip_plan_csvs:
        if not skip_plan_csv.exists():
            continue
        prior_rows = read_csv(skip_plan_csv)
        launched_pairs.update(
            {
                (
                    (row.get("jurisdiction") or "").strip(),
                    (row.get("fetch_inventory_lane") or "").strip(),
                )
                for row in prior_rows
                if (row.get("launch_status") or "").strip() == "launched"
            }
        )
    if not launched_pairs:
        return plan

    return [
        row
        for row in plan
        if (
            (row.get("jurisdiction") or "").strip(),
            (row.get("fetch_inventory_lane") or "").strip(),
        )
        not in launched_pairs
    ]


def main() -> None:
    args = parse_args()
    manifest_rows = read_csv(args.manifest_csv)
    plan = build_plan(manifest_rows)
    plan = filter_previously_launched(plan, args.skip_plan_csv)

    instance_ids = [part.strip() for part in args.instance_ids.split(",") if part.strip()]
    if args.launch:
        if not instance_ids:
            raise SystemExit("--launch requires --instance-ids")
        plan = launch_rows(plan, instance_ids)

    write_csv(args.plan_csv, plan)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manifest_csv": str(args.manifest_csv),
        "plan_row_count": len(plan),
        "supported_job_count": sum(1 for row in plan if row["launch_mode"] != "unsupported"),
        "unsupported_job_count": sum(1 for row in plan if row["launch_mode"] == "unsupported"),
        "launch_mode_counts": dict(Counter(row["launch_mode"] for row in plan)),
        "lane_counts": dict(Counter(row["fetch_inventory_lane"] for row in plan)),
        "instance_ids": instance_ids,
        "launched_count": sum(1 for row in plan if row["launch_status"] == "launched"),
        "failed_count": sum(1 for row in plan if row["launch_status"] == "launch_failed"),
        "planner_only_count": sum(1 for row in plan if row["launch_status"] == "planner_only"),
        "plan_csv": str(args.plan_csv),
    }
    write_json(args.summary_json, summary)

    print(f"Plan rows: {len(plan)}")
    print(f"Supported jobs: {summary['supported_job_count']}")
    print(f"Launched: {summary['launched_count']}")
    print(f"Plan CSV: {args.plan_csv}")
    print(f"Summary JSON: {args.summary_json}")


if __name__ == "__main__":
    main()
