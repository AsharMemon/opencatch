#!/usr/bin/env python3
"""
Resolve or bootstrap the official PMTiles CLI.

This is used by remote packaging jobs that run on bare Python images with
`tippecanoe` installed but without `pmtiles` already on PATH.
"""

from __future__ import annotations

import io
import json
import platform
import shutil
import stat
import tarfile
import urllib.request
from pathlib import Path


PMTILES_LATEST_RELEASE = "https://api.github.com/repos/protomaps/go-pmtiles/releases/latest"


def _candidate_paths(name: str) -> list[Path]:
    home = Path.home()
    return [
        Path(found) for found in [shutil.which(name)] if found
    ] + [
        home / ".local" / "bin" / name,
        home / ".cache" / "opencatch" / "bin" / name,
        Path("/usr/local/bin") / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/bin") / name,
    ]


def find_binary(name: str) -> str:
    for candidate in _candidate_paths(name):
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(f"Required binary not found: {name}")


def _detect_release_asset() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture for PMTiles bootstrap: {machine}")

    if system == "Linux":
        return f"Linux_{arch}"
    if system == "Darwin":
        return f"Darwin_{arch}"
    raise RuntimeError(f"Unsupported OS for PMTiles bootstrap: {system}")


def ensure_pmtiles_cli() -> str:
    for name in ("pmtiles", "pmtiles-convert"):
        try:
            return find_binary(name)
        except FileNotFoundError:
            pass

    target_dir = Path.home() / ".local" / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "pmtiles"

    asset_suffix = _detect_release_asset()
    with urllib.request.urlopen(PMTILES_LATEST_RELEASE, timeout=30) as resp:
        release = json.load(resp)

    asset = None
    for item in release.get("assets", []):
        name = item.get("name") or ""
        if asset_suffix in name:
            asset = item
            break
    if asset is None:
        raise RuntimeError(f"No PMTiles release asset matched {asset_suffix}")

    with urllib.request.urlopen(asset["browser_download_url"], timeout=120) as resp:
        blob = resp.read()

    name = asset.get("name") or ""
    if name.endswith(".tar.gz"):
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            member = next((m for m in tf.getmembers() if Path(m.name).name == "pmtiles"), None)
            if member is None:
                raise RuntimeError(f"PMTiles archive {name} did not contain a pmtiles binary")
            extracted = tf.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"Could not extract pmtiles from {name}")
            target_path.write_bytes(extracted.read())
    elif name.endswith(".zip"):
        import zipfile

        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            member = next((n for n in zf.namelist() if Path(n).name == "pmtiles"), None)
            if member is None:
                raise RuntimeError(f"PMTiles archive {name} did not contain a pmtiles binary")
            target_path.write_bytes(zf.read(member))
    else:
        raise RuntimeError(f"Unsupported PMTiles release asset: {name}")

    target_path.chmod(target_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(target_path)
