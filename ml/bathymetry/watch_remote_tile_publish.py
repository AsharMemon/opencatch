#!/usr/bin/env python3
"""
Watch remote tile outputs and publish them to the Martin host when ready.

This is a lightweight bridge for long-running Vast builds that finish after the
main shell command returns. It polls for remote PMTiles files, streams them to
the tile server, and can optionally patch Martin config / restart Martin.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class WatchTarget:
    name: str
    ssh_port: int
    remote_host: str
    remote_path: str
    server_path: str
    configure_martin_source: bool = False


def run(cmd: list[str], *, stdin=None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdin=stdin, check=check, text=False)


def remote_exists(target: WatchTarget) -> bool:
    cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        str(target.ssh_port),
        f"root@{target.remote_host}",
        f"test -f {target.remote_path}",
    ]
    return run(cmd, check=False).returncode == 0


def publish_stream(target: WatchTarget, server_host: str) -> bool:
    src = subprocess.Popen(
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-p",
            str(target.ssh_port),
            f"root@{target.remote_host}",
            f"cat {target.remote_path}",
        ],
        stdout=subprocess.PIPE,
    )
    try:
        server_cmd = (
            f"cat > {target.server_path}.tmp && "
            f"mv {target.server_path}.tmp {target.server_path}"
        )
        result = run(
            ["ssh", "-o", "StrictHostKeyChecking=no", f"root@{server_host}", server_cmd],
            stdin=src.stdout,
            check=False,
        )
    finally:
        if src.stdout:
            src.stdout.close()
        src.wait()
    return result.returncode == 0 and src.returncode == 0


def enable_martin_source(target: WatchTarget, server_host: str) -> bool:
    if not target.configure_martin_source:
        return True
    source_line = f"    {target.name}: /tiles/{target.name}.pmtiles\n"
    anchor_line = "    usace_river_navigation: /tiles/usace_river_navigation.pmtiles\n"
    remote_script = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "p = Path('/opt/opencatch/infra/martin/config.yaml')\n"
        "text = p.read_text()\n"
        f"line = {source_line!r}\n"
        f"anchor = {anchor_line!r}\n"
        "if line not in text:\n"
        "    if anchor in text:\n"
        "        text = text.replace(anchor, anchor + line)\n"
        "    else:\n"
        "        text += '\\n' + line\n"
        "    p.write_text(text)\n"
        "PY\n"
        "docker restart infra-martin-1 >/dev/null"
    )
    return run(
        ["ssh", "-o", "StrictHostKeyChecking=no", f"root@{server_host}", remote_script],
        check=False,
    ).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch remote PMTiles and publish them to Martin.")
    parser.add_argument("--server-host", default="24.199.80.77")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument(
        "--targets",
        nargs="+",
        choices=["nb", "na_river_network"],
        default=["nb", "na_river_network"],
    )
    args = parser.parse_args()

    known_targets = {
        "nb": WatchTarget(
            name="nb_contours",
            ssh_port=16546,
            remote_host="ssh3.vast.ai",
            remote_path="/data/production_contours/tiles/nb_contours.pmtiles",
            server_path="/opt/opencatch/infra/martin/tiles/nb_contours.pmtiles",
        ),
        "na_river_network": WatchTarget(
            name="na_river_network",
            ssh_port=34376,
            remote_host="ssh3.vast.ai",
            remote_path="/data/na_river_network_20260410T063032Z/na_river_network.pmtiles",
            server_path="/opt/opencatch/infra/martin/tiles/na_river_network.pmtiles",
            configure_martin_source=True,
        ),
    }

    pending = [known_targets[name] for name in args.targets]
    print(f"watching {[t.name for t in pending]}", flush=True)
    while pending:
        remaining: list[WatchTarget] = []
        for target in pending:
            if not remote_exists(target):
                print(f"{target.name}: not ready", flush=True)
                remaining.append(target)
                continue
            print(f"{target.name}: remote file detected", flush=True)
            if not publish_stream(target, args.server_host):
                print(f"{target.name}: publish failed", flush=True)
                remaining.append(target)
                continue
            if not enable_martin_source(target, args.server_host):
                print(f"{target.name}: published but Martin update failed", flush=True)
                remaining.append(target)
                continue
            print(f"{target.name}: published", flush=True)
        pending = remaining
        if pending:
            time.sleep(args.poll_seconds)
    print("all targets published", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
