#!/usr/bin/env python3
"""
Re-run only the PDF digitization outputs that failed georeferencing.

Useful after improving lake-name matching or lake polygon coverage without
throwing away an entire long-running PDF promotion batch.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pandas as pd


def load_digitizer_module():
    module_path = Path(__file__).with_name("digitize_pdf_bathymetry.py")
    spec = importlib.util.spec_from_file_location("digitize_pdf_bathymetry", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load digitizer module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_if_exists(path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path) if path.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run only PDF outputs with missing/bad georeferencing.")
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--lake-polygons", default=None)
    parser.add_argument("--max-depths", default=None)
    parser.add_argument("--name-manifest", default=None)
    parser.add_argument("--mode", choices=["simple", "full"], default="full")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--num-bands", type=int, default=6)
    parser.add_argument(
        "--selection",
        choices=["missing", "bad", "all"],
        default="missing",
        help="Which records to retry based on georeferencing columns in _quality_report.csv",
    )
    parser.add_argument(
        "--bad-threshold-m",
        type=float,
        default=250.0,
        help="Retry rows with georef_rmse_m above this threshold when --selection=bad",
    )
    parser.add_argument(
        "--update-main-report",
        action="store_true",
        help="Overwrite the run's _quality_report.csv with updated rows for retried files.",
    )
    args = parser.parse_args()

    run_root = args.run_root
    pdf_dir = run_root / "pdfs"
    digitized_dir = run_root / "digitized"
    report_path = digitized_dir / "_quality_report.csv"
    if not report_path.exists():
        raise FileNotFoundError(f"Quality report not found: {report_path}")

    report_df = pd.read_csv(report_path)
    if "pdf_file" not in report_df.columns:
        raise ValueError(f"{report_path} does not contain a pdf_file column")

    georef_series = pd.to_numeric(report_df.get("georef_rmse_m"), errors="coerce")
    if args.selection == "missing":
        retry_mask = georef_series.isna()
    elif args.selection == "bad":
        retry_mask = georef_series.isna() | (georef_series > args.bad_threshold_m)
    else:
        retry_mask = pd.Series(True, index=report_df.index)

    retry_df = report_df[retry_mask].copy()
    if retry_df.empty:
        print(json.dumps({"status": "nothing_to_retry", "run_root": str(run_root)}, indent=2))
        return

    digitizer = load_digitizer_module()
    lake_polygons = args.lake_polygons or default_if_exists(run_root / "hydrolakes_na.parquet") or default_if_exists(Path("/data/hydrolakes_na.parquet"))
    max_depths = args.max_depths or default_if_exists(run_root / "lagos_depth.csv")
    name_manifest = args.name_manifest or default_if_exists(pdf_dir / "download_manifest.json")

    lake_gdf = digitizer.load_lake_polygons(lake_polygons) if lake_polygons else None
    max_depths_df = digitizer.load_max_depths(max_depths) if max_depths else None
    name_map = digitizer.load_name_manifest(name_manifest) if name_manifest else {}

    retry_records: list[dict] = []
    updated_rows = 0

    for idx, row in retry_df.iterrows():
        pdf_name = str(row["pdf_file"])
        pdf_path = pdf_dir / pdf_name
        output_path = digitized_dir / f"{Path(pdf_name).stem}.geojson"
        if not pdf_path.exists():
            retry_records.append(
                {
                    "pdf_file": pdf_name,
                    "status": "missing_pdf",
                    "output_path": str(output_path),
                }
            )
            continue

        try:
            _, quality = digitizer.process_single_pdf(
                str(pdf_path),
                lake_gdf=lake_gdf,
                max_depths_df=max_depths_df,
                mode=args.mode,
                num_bands=args.num_bands,
                dpi=args.dpi,
                output_path=str(output_path),
                lake_name_override=name_map.get(pdf_name),
            )
            quality_row = {
                field: getattr(quality, field)
                for field in quality.__dataclass_fields__.keys()
            }
            quality_row["error"] = ""
            retry_records.append({"pdf_file": pdf_name, "status": "ok", **quality_row})
            if args.update_main_report:
                for key, value in quality_row.items():
                    report_df.loc[idx, key] = value
                report_df.loc[idx, "error"] = ""
                updated_rows += 1
        except Exception as exc:  # noqa: BLE001
            retry_records.append(
                {
                    "pdf_file": pdf_name,
                    "status": "error",
                    "error": str(exc),
                    "output_path": str(output_path),
                }
            )

    retry_report_path = digitized_dir / "_retry_georef_report.csv"
    pd.DataFrame(retry_records).to_csv(retry_report_path, index=False)

    if args.update_main_report and updated_rows:
        report_df.to_csv(report_path, index=False)

    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "retried": int(len(retry_df)),
                "updated_rows": updated_rows,
                "retry_report": str(retry_report_path),
                "lake_polygons": lake_polygons,
                "max_depths": max_depths,
                "name_manifest": name_manifest,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
