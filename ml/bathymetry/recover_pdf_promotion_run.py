#!/usr/bin/env python3
"""
Recover/package/upload an existing PDF-promotion run directory.

This is useful when a long-running remote digitization finishes but the
wrapper died during packaging/upload, or when the run was launched without
working B2 env vars.
"""

from __future__ import annotations

import argparse
import csv
import json
import tarfile
from pathlib import Path


def compute_summary(run_root: Path, job_name: str) -> dict:
    pdf_dir = run_root / "pdfs"
    digitized_dir = run_root / "digitized"
    quality_report = digitized_dir / "_quality_report.csv"
    rows = []
    if quality_report.exists():
        with quality_report.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    scores: list[float] = []
    for row in rows:
        try:
            scores.append(float(row.get("score") or 0))
        except Exception:
            continue

    return {
        "job_name": job_name,
        "pdf_count": len(list(pdf_dir.glob("*.pdf"))),
        "digitized_geojson_count": len(list(digitized_dir.glob("*.geojson"))),
        "quality_report_rows": len(rows),
        "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
        "max_score": round(max(scores), 4) if scores else None,
        "min_score": round(min(scores), 4) if scores else None,
    }


def build_archive(run_root: Path, job_name: str, force: bool) -> Path:
    archive_path = run_root / f"{job_name}_digitized.tar.gz"
    if archive_path.exists() and not force:
        return archive_path

    with tarfile.open(archive_path, "w:gz") as tf:
        for subdir in ("digitized", "pdfs"):
            path = run_root / subdir
            if path.exists():
                tf.add(path, arcname=subdir)
    return archive_path


def upload_b2(
    run_root: Path,
    job_name: str,
    bucket_name: str,
    prefix: str,
    key_id: str,
    app_key: str,
) -> list[str]:
    from b2sdk.v2 import B2Api, InMemoryAccountInfo

    files = [
        run_root / "summary.json",
        run_root / "run.log",
        run_root / f"{job_name}_digitized.tar.gz",
        run_root / "pdfs" / "download_manifest.json",
        run_root / "digitized" / "_quality_report.csv",
    ]
    info = InMemoryAccountInfo()
    api = B2Api(info)
    api.authorize_account("production", key_id, app_key)
    bucket = api.get_bucket_by_name(bucket_name)

    uploaded = []
    for path in files:
        if not path.exists():
            continue
        remote_name = f"{prefix.rstrip('/')}/{job_name}/{path.name}"
        bucket.upload_local_file(local_file=str(path), file_name=remote_name)
        uploaded.append(f"b2://{bucket_name}/{remote_name}")
    return uploaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover/package/upload an existing PDF-promotion run.")
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--job-name", default=None)
    parser.add_argument("--force-archive", action="store_true")
    parser.add_argument("--bucket", default=None)
    parser.add_argument("--prefix", default="castline/experiments/pdf_promotion")
    parser.add_argument("--key-id", default=None)
    parser.add_argument("--app-key", default=None)
    args = parser.parse_args()

    run_root: Path = args.run_root
    job_name = args.job_name or run_root.name
    summary = compute_summary(run_root, job_name)
    summary_path = run_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    archive_path = build_archive(run_root, job_name, force=args.force_archive)

    result = {
        "summary_path": str(summary_path),
        "archive_path": str(archive_path),
        "summary": summary,
        "uploaded": [],
    }

    if args.bucket and args.key_id and args.app_key:
        result["uploaded"] = upload_b2(
            run_root,
            job_name,
            args.bucket,
            args.prefix,
            args.key_id,
            args.app_key,
        )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
