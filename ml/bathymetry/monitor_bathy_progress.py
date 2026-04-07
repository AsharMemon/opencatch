#!/usr/bin/env python3
"""
Monitor isolated bathymetry experiments on Vast.ai and optionally sync results.

This is intentionally lightweight so a recurring automation can run it every
20 minutes, summarize status, and decide whether to intervene.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TOTAL_HYBRID_STAGES = 16
TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
METRIC_LINE_RE = re.compile(r"stage\d+_[a-z_]+:[a-z_]+ -> RMSE=")
REBUILD_TOTAL_RE = re.compile(r"Processing (\d+) lakes")
SONAR_TOTAL_RE = re.compile(r"Processing (\d+) lakes")
SONAR_CHECKPOINT_RE = re.compile(r"Checkpoint:\s+(\d+)\s+lakes,.*?(\d+)\s+failed")
SONAR_FINAL_RE = re.compile(r"Lakes:\s+(\d+)\s+success,\s+(\d+)\s+failed")
SONAR_TQDM_RE = re.compile(r"Lakes:\s+\d+%.*?(\d+)/(\d+)")


def latest_experiments(host: str, port: int) -> dict[str, str]:
    raw = ssh(
        host,
        port,
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "import json\n"
        "dirs=sorted([p for p in Path('/data').glob('codex_bathy_*') if p.is_dir()])\n"
        "latest_any=str(dirs[-1]) if dirs else ''\n"
        "hybrid=[p for p in dirs if (p/'hybrid_sonar.log').exists() or (p/'hybrid_sonar'/'metrics.json').exists() or (p/'hybrid_full.log').exists() or (p/'hybrid_full'/'metrics.json').exists() or (p/'hybrid_phys.log').exists() or (p/'hybrid_phys'/'metrics.json').exists()]\n"
        "rebuild=[p for p in dirs if (p/'rebuild_composites.log').exists()]\n"
        "print(json.dumps({'latest_any': latest_any, 'latest_hybrid': str(hybrid[-1]) if hybrid else '', 'latest_rebuild': str(rebuild[-1]) if rebuild else ''}))\n"
        "PY",
    )
    return json.loads(raw) if raw else {}


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def ssh(host: str, port: int, remote_cmd: str) -> str:
    result = run(
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(port),
            host,
            remote_cmd,
        ]
    )
    return result.stdout.strip()


def scp_from(host: str, port: int, remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "scp",
            "-o",
            "StrictHostKeyChecking=no",
            "-P",
            str(port),
            f"{host}:{remote_path}",
            str(local_path),
        ]
    )


def parse_log_status(log_text: str, log_kind: str) -> dict[str, object]:
    lines = [line.rstrip() for line in log_text.splitlines() if line.strip()]
    timestamps: list[dt.datetime] = []
    completed = 0
    total = TOTAL_HYBRID_STAGES if log_kind == "hybrid" else 0

    for line in lines:
        if log_kind == "hybrid" and METRIC_LINE_RE.search(line):
            completed += 1
        elif log_kind == "rebuild" and "Saved:" in line:
            completed += 1
        elif log_kind == "sonar":
            checkpoint_match = SONAR_CHECKPOINT_RE.search(line)
            final_match = SONAR_FINAL_RE.search(line)
            tqdm_match = SONAR_TQDM_RE.search(line)
            if checkpoint_match:
                completed = max(completed, int(checkpoint_match.group(1)) + int(checkpoint_match.group(2)))
            elif final_match:
                completed = max(completed, int(final_match.group(1)) + int(final_match.group(2)))
            elif tqdm_match:
                completed = max(completed, int(tqdm_match.group(1)))
                total = max(total, int(tqdm_match.group(2)))
        if log_kind == "rebuild":
            total_match = REBUILD_TOTAL_RE.search(line)
            if total_match:
                total = int(total_match.group(1))
        elif log_kind == "sonar":
            total_match = SONAR_TOTAL_RE.search(line)
            if total_match:
                total = int(total_match.group(1))
        match = TIMESTAMP_RE.match(line)
        if match:
            try:
                timestamps.append(dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue

    progress_pct = float(completed / total) if total else 0.0
    status: dict[str, object] = {
        "kind": log_kind,
        "completed_stages": completed,
        "total_stages": total,
        "progress_pct": progress_pct,
        "last_log_line": lines[-1] if lines else "",
    }
    if timestamps:
        start_ts = timestamps[0]
        last_ts = timestamps[-1]
        elapsed = max((last_ts - start_ts).total_seconds(), 0.0)
        age = max((dt.datetime.now() - last_ts).total_seconds(), 0.0)
        eta = None
        if completed > 0 and total and completed < total and elapsed > 0:
            eta = elapsed / completed * (total - completed)
        status.update(
            {
                "started_at": start_ts.isoformat(sep=" "),
                "last_update_at": last_ts.isoformat(sep=" "),
                "elapsed_sec": elapsed,
                "last_update_age_sec": age,
                "eta_sec": eta,
                "stalled": age > 30 * 60,
            }
        )
    else:
        status["stalled"] = False

    return status


def fmt_minutes(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rem = minutes % 60
    return f"{hours}h {rem}m"


def pick_first_existing(host: str, port: int, paths: list[str]) -> str:
    for candidate in paths:
        exists = ssh(host, port, f"test -f {candidate} && echo yes || true")
        if exists.strip() == "yes":
            return candidate
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor Vast bathymetry experiments")
    parser.add_argument("--host", default="root@ssh3.vast.ai")
    parser.add_argument("--port", type=int, default=16546)
    parser.add_argument("--remote-prefix", default="/data/codex_bathy_")
    parser.add_argument("--bucket", default="")
    parser.add_argument("--b2-prefix", default="")
    args = parser.parse_args()

    exp_info = latest_experiments(args.host, args.port)
    latest_exp = exp_info.get("latest_any", "").strip()
    if not latest_exp:
        print("No isolated codex bathymetry experiment found on remote.")
        return

    running = ssh(
        args.host,
        args.port,
        "pgrep -af 'train_hybrid_sonar.py|train_sonar_spectral.py|train_v2_multimodal.py|finetune_depth_anything.py|build_s2_composites.py' || true",
    )
    print(f"Experiment: {latest_exp}")
    print(f"Running jobs:\n{running or 'none'}")

    hybrid_exp = exp_info.get("latest_hybrid", "")
    rebuild_exp = exp_info.get("latest_rebuild", "")
    active_log_path = ""
    active_log_kind = ""

    if rebuild_exp == latest_exp:
        active_log_path = f"{latest_exp}/rebuild_composites.log"
        active_log_kind = "rebuild"
    elif hybrid_exp == latest_exp:
        active_log_path = pick_first_existing(
            args.host,
            args.port,
            [
                f"{latest_exp}/hybrid_phys.log",
                f"{latest_exp}/hybrid_full.log",
                f"{latest_exp}/hybrid_sonar.log",
            ],
        )
        active_log_kind = "hybrid" if active_log_path else ""
    else:
        for candidate_path, candidate_kind in [
            (f"{latest_exp}/full_sonar.log", "sonar"),
            (f"{latest_exp}/hybrid_phys.log", "hybrid"),
            (f"{latest_exp}/hybrid_full.log", "hybrid"),
            (f"{latest_exp}/hybrid_sonar.log", "hybrid"),
            (f"{latest_exp}/rebuild_composites.log", "rebuild"),
        ]:
            exists = ssh(args.host, args.port, f"test -f {candidate_path} && echo yes || true")
            if exists.strip() == "yes":
                active_log_path = candidate_path
                active_log_kind = candidate_kind
                break

    log_status = {
        "kind": active_log_kind or "unknown",
        "completed_stages": 0,
        "total_stages": TOTAL_HYBRID_STAGES if active_log_kind == "hybrid" else 0,
        "progress_pct": 0.0,
        "stalled": False,
    }
    if active_log_path:
        log_tail = ssh(
            args.host,
            args.port,
            f"tail -n 120 {active_log_path} 2>/dev/null || true",
        )
        if log_tail:
            print(f"\n--- {Path(active_log_path).name} ---")
            print(log_tail)
            log_status = parse_log_status(log_tail, active_log_kind)
            print("\n--- progress ---")
            print(
                f"kind={log_status['kind']} completed={log_status['completed_stages']}/{log_status['total_stages']} "
                f"({log_status['progress_pct']:.0%})"
            )
            if log_status.get("started_at"):
                print(f"started_at={log_status['started_at']}")
                print(f"last_update_at={log_status['last_update_at']}")
                print(f"elapsed={fmt_minutes(log_status.get('elapsed_sec'))}")
                print(f"last_update_age={fmt_minutes(log_status.get('last_update_age_sec'))}")
                print(f"eta={fmt_minutes(log_status.get('eta_sec'))}")
                print(f"stalled={log_status.get('stalled')}")
            print(f"last_log_line={log_status.get('last_log_line', '')}")

    metrics_exp = hybrid_exp or latest_exp
    metrics_path = pick_first_existing(
        args.host,
        args.port,
        [
            f"{metrics_exp}/hybrid_phys/metrics.json",
            f"{metrics_exp}/hybrid_full/metrics.json",
            f"{metrics_exp}/hybrid_sonar/metrics.json",
        ],
    )
    metrics_raw = ssh(
        args.host,
        args.port,
        f"cat {metrics_path} 2>/dev/null || true" if metrics_path else "true",
    )
    metrics = None
    if metrics_raw:
        metrics = json.loads(metrics_raw)
        print("\n--- metrics ---")
        for key in ["baseline_mean", "direct_depth", "relative_depth_scaled", "clarity_blend"]:
            if key in metrics:
                m = metrics[key]
                print(f"{key}: RMSE={m.get('rmse')} MAE={m.get('mae')} R2={m.get('r2')}")
        if "clarity_breakdown" in metrics:
            print("\nclarity_breakdown:")
            for clarity, m in metrics["clarity_breakdown"].items():
                print(f"  {clarity}: RMSE={m.get('rmse')} MAE={m.get('mae')} R2={m.get('r2')} n={m.get('n')}")

    status_payload = {
        "experiment": latest_exp,
        "latest_hybrid_experiment": hybrid_exp,
        "latest_rebuild_experiment": rebuild_exp,
        "running_jobs": running.splitlines() if running else [],
        "progress": log_status,
        "metrics": metrics,
        "metrics_experiment": metrics_exp if metrics else "",
        "metrics_path": metrics_path if metrics else "",
        "captured_at": dt.datetime.now().isoformat(),
    }

    if args.bucket and args.b2_prefix:
        b2_bin = shutil.which("b2") or "/Users/Ashar/Library/Python/3.14/bin/b2"
        with tempfile.TemporaryDirectory(prefix="bathy_sync_") as tmpdir:
            tmp = Path(tmpdir)
            if active_log_path:
                try:
                    scp_from(args.host, args.port, active_log_path, tmp / Path(active_log_path).name)
                except Exception:
                    pass
            if metrics:
                rel_paths = []
                for base in ["hybrid_phys", "hybrid_full", "hybrid_sonar"]:
                    rel_paths.extend(
                        [
                            f"{base}/metrics.json",
                            f"{base}/config.json",
                            f"{base}/per_lake_metrics.parquet",
                            f"{base}/test_predictions.parquet",
                        ]
                    )
                rel_paths.extend(["hybrid_phys.log", "hybrid_full.log", "hybrid_sonar.log"])
                for rel in rel_paths:
                    remote_path = f"{metrics_exp}/{rel}"
                    local_path = tmp / rel
                    try:
                        scp_from(args.host, args.port, remote_path, local_path)
                    except Exception:
                        continue
            status_path = tmp / "status.json"
            status_path.write_text(json.dumps(status_payload, indent=2))

            if any(tmp.rglob("*")):
                dest = f"b2://{args.bucket}/{args.b2_prefix}/{Path(latest_exp).name}/"
                result = run([b2_bin, "sync", str(tmp), dest], check=False)
                print("\n--- b2 sync ---")
                print(result.stdout.strip() or result.stderr.strip())

    local_status_dir = Path("/Users/Ashar/Documents/fish/.claude/logs")
    local_status_dir.mkdir(parents=True, exist_ok=True)
    (local_status_dir / "bathy_status_latest.json").write_text(json.dumps(status_payload, indent=2))


if __name__ == "__main__":
    main()
