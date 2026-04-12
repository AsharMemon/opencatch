#!/usr/bin/env python3
"""
Build OpenCatch's North America river-network completeness overlay.

This script packages a unified river backbone from:
  - U.S. flowline parquet exported from the official NHDPlus HR pipeline
  - Canadian NHN (National Hydro Network) package zips / FileGDBs

The runtime app already has an immediate official fallback path:
  - U.S. river completeness: USGS HydroCached raster service
  - Canada river completeness: NRCan NHN WMS
  - Navigable surveyed corridors: USACE IENC on top

This packager exists so we can progressively replace those service-backed
layers with our own continent-wide PMTiles source without changing the app
again. It is resumable and processes NHN packages one-at-a-time to keep disk
usage bounded.

Examples:
  # Smoke-test the builder on an existing NHN sample package
  /Users/Ashar/Documents/fish/.venv/bin/python build_na_river_network.py \
      --skip-us \
      --package-limit 1 \
      --output-root /tmp/na_river_network

  # Build from local NHDPlus-HR flowline parquet + all NHN packages
  /Users/Ashar/Documents/fish/.venv/bin/python build_na_river_network.py \
      --us-parquet-dir /data/nhdplus/parquet \
      --output-root /data/bathymetry/river_network
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import pyogrio
import requests
from shapely.geometry import mapping
from shapely.ops import transform

from build_overlay_pmtiles import build_pmtiles


USER_AGENT = "OpenCatch/1.0 (contact@opencatch.app)"
TIMEOUT_S = 120
NHN_GDB_ROOT = "https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nhn_rhn/gdb_en/"
NHN_PRIMARY_LAYER = "NHN_HN_PrimaryDirectedNLFlow_1"
NHN_FALLBACK_LAYER = "NHN_HN_NLFLOW_1"


def fetch_text(url: str) -> str:
    resp = requests.get(url, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def stream_download(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)


def list_directory_links(url: str) -> list[str]:
    text = fetch_text(url)
    return re.findall(r'href="([^"]+)"', text)


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {
            "counts": {"US": 0, "CA": 0, "total": 0},
            "completed_nhn_packages": [],
            "us_inputs": [],
            "notes": [],
        }
    try:
        return json.loads(path.read_text())
    except Exception:
        return {
            "counts": {"US": 0, "CA": 0, "total": 0},
            "completed_nhn_packages": [],
            "us_inputs": [],
            "notes": ["Recovered from invalid manifest"],
        }


def save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))


def drop_z(geom):
    if geom is None or geom.is_empty:
        return None
    try:
        if getattr(geom, "has_z", False):
            return transform(lambda x, y, z=None: (x, y), geom)
    except Exception:
        return geom
    return geom


def classify_us_river(stream_order: int | None, length_km: float | None, name: str) -> tuple[str, int]:
    if stream_order is not None and stream_order >= 7:
        return "trunk", 4
    if stream_order is not None and stream_order >= 5:
        return "major", 3
    if stream_order is not None and stream_order >= 3:
        return "secondary", 2
    if name and (length_km or 0) >= 20:
        return "major", 3
    if name or (length_km or 0) >= 5:
        return "secondary", 2
    return "local", 1


def classify_nhn_river(level_priority: int | None, name: str, length_m: float | None) -> tuple[str, int]:
    if level_priority is not None and level_priority <= 1 and name:
        return "major", 3
    if level_priority is not None and level_priority <= 1:
        return "secondary", 2
    if name or (length_m or 0) >= 5000:
        return "secondary", 2
    return "local", 1


def write_feature(fh, geometry, properties: dict) -> None:
    feature = {
        "type": "Feature",
        "geometry": mapping(geometry),
        "properties": properties,
    }
    fh.write(json.dumps(feature, ensure_ascii=True))
    fh.write("\n")


def iter_us_parquets(parquet_dir: Path) -> Iterable[Path]:
    if not parquet_dir.exists():
        return []
    return sorted(parquet_dir.rglob("*.parquet"))


def append_us_rows(
    gdf: gpd.GeoDataFrame,
    fh,
    seen_ids: set[str],
    *,
    min_stream_order: int,
    min_unnamed_length_km: float,
) -> int:
    count = 0
    for row in gdf.itertuples(index=False):
        geom = drop_z(getattr(row, "geometry", None))
        if geom is None or geom.is_empty:
            continue

        rid = str(
            getattr(row, "permanent_id", "")
            or getattr(row, "nhdplusid", "")
            or getattr(row, "reachcode", "")
        )
        if not rid or rid in seen_ids:
            continue

        stream_order_raw = getattr(row, "stream_order", None)
        if stream_order_raw is None:
            stream_order_raw = getattr(row, "streamorde", None)
        try:
            stream_order = int(stream_order_raw) if stream_order_raw not in (None, "") else None
        except Exception:
            stream_order = None
        if stream_order is not None and stream_order < min_stream_order:
            continue

        river_name = str(getattr(row, "name", "") or getattr(row, "gnis_name", "") or "").strip()
        length_km_raw = getattr(row, "lengthkm", None)
        try:
            length_km = float(length_km_raw) if length_km_raw is not None else None
        except Exception:
            length_km = None

        if not river_name and (length_km or 0) < min_unnamed_length_km:
            continue

        river_class, display_rank = classify_us_river(stream_order, length_km, river_name)
        write_feature(
            fh,
            geom,
            {
                "feature_kind": "river_network",
                "river_name": river_name,
                "river_class": river_class,
                "display_rank": display_rank,
                "country": "US",
                "source": "usgs_nhdplus_hr",
                "coverage_lane": "hydrography_backbone",
                "stream_order": stream_order,
                "length_km": round(length_km, 3) if length_km is not None else None,
                "fallback_depth_model": "river_depth.py",
            },
        )
        seen_ids.add(rid)
        count += 1
    return count


def append_us_parquet(
    parquet_path: Path,
    fh,
    seen_ids: set[str],
    *,
    min_stream_order: int,
    min_unnamed_length_km: float,
    manifest: dict,
) -> int:
    try:
        gdf = gpd.read_parquet(parquet_path)
    except Exception as exc:
        manifest["notes"].append(f"Failed to read {parquet_path.name}: {exc}")
        return 0

    manifest["us_inputs"].append(str(parquet_path))
    return append_us_rows(
        gdf,
        fh,
        seen_ids,
        min_stream_order=min_stream_order,
        min_unnamed_length_km=min_unnamed_length_km,
    )


def append_us_flowlines(
    parquet_dir: Path,
    fh,
    seen_ids: set[str],
    *,
    min_stream_order: int,
    min_unnamed_length_km: float,
    manifest: dict,
) -> int:
    count = 0
    parquet_files = list(iter_us_parquets(parquet_dir))
    if not parquet_files:
        manifest["notes"].append(f"No US flowline parquet found in {parquet_dir}")
        return 0

    for parquet_path in parquet_files:
        count += append_us_parquet(
            parquet_path,
            fh,
            seen_ids,
            min_stream_order=min_stream_order,
            min_unnamed_length_km=min_unnamed_length_km,
            manifest=manifest,
        )

    manifest["counts"]["US"] = count
    return count


def load_fetch_nhdplus_module():
    data_pipeline_dir = Path(__file__).resolve().parents[1] / "data_pipeline"
    if not data_pipeline_dir.exists():
        raise FileNotFoundError(f"Missing data pipeline directory: {data_pipeline_dir}")
    path_str = str(data_pipeline_dir)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
    import fetch_nhdplus  # type: ignore

    return fetch_nhdplus


def append_us_flowlines_sequential(
    fh,
    seen_ids: set[str],
    *,
    manifest: dict,
    min_stream_order: int,
    min_unnamed_length_km: float,
    huc2_filter: list[str] | None,
    scratch_root: Path,
) -> int:
    fetch_nhdplus = load_fetch_nhdplus_module()
    scratch_root.mkdir(parents=True, exist_ok=True)

    huc4_urls = fetch_nhdplus.discover_huc4_urls(huc2_filter)
    count = 0
    for huc4, url in sorted(huc4_urls.items()):
        if huc2_filter and huc4[:2] not in huc2_filter:
            continue

        with tempfile.TemporaryDirectory(prefix=f"nhdplus-{huc4}-", dir=str(scratch_root)) as tmp:
            tmpdir = Path(tmp)
            zip_name = url.rstrip("/").split("/")[-1] or f"NHDPLUS_H_{huc4}_HU4_GDB.zip"
            zip_path = tmpdir / zip_name

            if not fetch_nhdplus.download_file(url, zip_path):
                manifest["notes"].append(f"Failed to download NHDPlus HR zip for HUC4 {huc4}")
                continue

            processed_root = tmpdir / "processed"
            parquet_path = fetch_nhdplus.process_huc4_gdb(
                zip_path,
                processed_root,
                huc4,
                ["NHDFlowline"],
                0.0,
                False,
            )
            if parquet_path is None:
                manifest["notes"].append(f"Failed to process NHDPlus HR flowlines for HUC4 {huc4}")
                continue

            count += append_us_parquet(
                parquet_path,
                fh,
                seen_ids,
                min_stream_order=min_stream_order,
                min_unnamed_length_km=min_unnamed_length_km,
                manifest=manifest,
            )

    manifest["counts"]["US"] = count
    return count


def choose_nhn_layer(gdb_path: Path) -> str | None:
    layers = {row[0] for row in pyogrio.list_layers(gdb_path)}
    if NHN_PRIMARY_LAYER in layers:
        return NHN_PRIMARY_LAYER
    if NHN_FALLBACK_LAYER in layers:
        return NHN_FALLBACK_LAYER
    return None


def append_nhn_gdb(
    gdb_path: Path,
    fh,
    seen_ids: set[str],
    *,
    min_unnamed_length_m: float,
) -> int:
    layer = choose_nhn_layer(gdb_path)
    if not layer:
        return 0

    gdf = pyogrio.read_dataframe(
        gdb_path,
        layer=layer,
        columns=[
            "nid",
            "name1",
            "name2",
            "networkFlowType",
            "levelPriority",
            "SHAPE_Length",
        ],
    )
    count = 0
    for row in gdf.itertuples(index=False):
        geom = drop_z(getattr(row, "geometry", None))
        if geom is None or geom.is_empty:
            continue

        rid = str(getattr(row, "nid", "") or "")
        if not rid or rid in seen_ids:
            continue

        name = str(getattr(row, "name1", "") or getattr(row, "name2", "") or "").strip()
        try:
            level_priority = int(getattr(row, "levelPriority", None))
        except Exception:
            level_priority = None
        try:
            length_m = float(getattr(row, "SHAPE_Length", None))
        except Exception:
            length_m = None

        if not name and (length_m or 0) < min_unnamed_length_m:
            continue

        river_class, display_rank = classify_nhn_river(level_priority, name, length_m)
        write_feature(
            fh,
            geom,
            {
                "feature_kind": "river_network",
                "river_name": name,
                "river_class": river_class,
                "display_rank": display_rank,
                "country": "CA",
                "source": "nrcan_nhn",
                "coverage_lane": "hydrography_backbone",
                "level_priority": level_priority,
                "length_km": round((length_m or 0) / 1000.0, 3) if length_m is not None else None,
                "fallback_depth_model": "river_depth.py",
            },
        )
        seen_ids.add(rid)
        count += 1
    return count


def iter_nhn_package_sources(
    nhn_root: Path,
    *,
    region_limit: int | None,
    package_limit: int | None,
) -> list[tuple[str, str]]:
    local_packages = sorted(str(p) for p in nhn_root.glob("sample_*.zip"))
    sources: list[tuple[str, str]] = [("local", pkg) for pkg in local_packages]

    manifest_path = nhn_root / "directory_manifest.json"
    if not manifest_path.exists():
        return sources[:package_limit] if package_limit else sources

    manifest = json.loads(manifest_path.read_text())
    gdb_root = str(manifest.get("gdb_root") or NHN_GDB_ROOT)
    regions = list(manifest.get("region_directories") or [])
    if region_limit:
        regions = regions[:region_limit]

    for region in regions:
        region_url = f"{gdb_root}{region}/"
        try:
          region_links = list_directory_links(region_url)
        except Exception:
          continue
        for href in region_links:
            if href.endswith(".zip"):
                sources.append(("remote", f"{region_url}{href}"))
                if package_limit and len(sources) >= package_limit:
                    return sources
    return sources[:package_limit] if package_limit else sources


def append_nhn_packages(
    nhn_root: Path,
    fh,
    seen_ids: set[str],
    *,
    manifest_path: Path,
    manifest: dict,
    region_limit: int | None,
    package_limit: int | None,
    min_unnamed_length_m: float,
) -> int:
    count = 0

    completed = set(manifest.get("completed_nhn_packages", []))
    for source_type, package_ref in iter_nhn_package_sources(
        nhn_root,
        region_limit=region_limit,
        package_limit=package_limit,
    ):
        if package_ref in completed:
            continue

        with tempfile.TemporaryDirectory(prefix="nhn-river-") as tmp:
            tmpdir = Path(tmp)
            if source_type == "local":
                zip_path = Path(package_ref)
            else:
                zip_path = tmpdir / Path(package_ref).name
                stream_download(package_ref, zip_path)

            extract_dir = tmpdir / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            gdb_paths = list(extract_dir.rglob("*.gdb"))
            if not gdb_paths:
                manifest["notes"].append(f"No .gdb found in {zip_path.name}")
                completed.add(package_ref)
                manifest["completed_nhn_packages"] = sorted(completed)
                save_manifest(manifest_path, manifest)
                continue

            count += append_nhn_gdb(
                gdb_paths[0],
                fh,
                seen_ids,
                min_unnamed_length_m=min_unnamed_length_m,
            )
            completed.add(package_ref)
            manifest["completed_nhn_packages"] = sorted(completed)
            save_manifest(manifest_path, manifest)

    manifest["counts"]["CA"] = count
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Build North America river network completeness PMTiles.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/river_network"),
    )
    parser.add_argument(
        "--us-parquet-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/usgs_nhdplus_flowlines/parquet"),
        help="Directory containing local USGS/NHDPlus HR flowline parquet files.",
    )
    parser.add_argument(
        "--nhn-root",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/nrcan_hydrography"),
    )
    parser.add_argument(
        "--pmtiles-output",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/infra/martin/tiles/na_river_network.pmtiles"),
    )
    parser.add_argument("--skip-us", action="store_true")
    parser.add_argument("--skip-canada", action="store_true")
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="Skip hydrography ingestion and build PMTiles only from an existing geojsonl under output-root.",
    )
    parser.add_argument("--region-limit", type=int, default=None)
    parser.add_argument("--package-limit", type=int, default=None)
    parser.add_argument("--min-us-stream-order", type=int, default=4)
    parser.add_argument("--min-unnamed-length-km", type=float, default=1.0)
    parser.add_argument("--min-unnamed-length-m", type=float, default=750.0)
    parser.add_argument(
        "--fetch-us-sequential",
        action="store_true",
        help="Fetch and process NHDPlus HR HUC4 zips one-at-a-time instead of requiring prebuilt local parquet.",
    )
    parser.add_argument(
        "--us-huc2",
        type=str,
        default=None,
        help="Optional comma-separated HUC2 filter for sequential U.S. NHDPlus fetching.",
    )
    parser.add_argument(
        "--scratch-root",
        type=Path,
        default=None,
        help="Scratch directory for sequential U.S./Canada processing. Defaults under output-root.",
    )
    args = parser.parse_args()

    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    geojsonl_path = output_root / "na_river_network.geojsonl"
    manifest_path = output_root / "na_river_network_manifest.json"
    scratch_root = args.scratch_root or (output_root / "scratch")
    us_huc2_filter = [item.strip() for item in args.us_huc2.split(",") if item.strip()] if args.us_huc2 else None
    manifest = load_manifest(manifest_path)
    seen_ids: set[str] = set()

    if not args.package_only:
        # Rebuild from scratch each run, but keep resumable NHN package state.
        manifest["counts"] = {"US": 0, "CA": 0, "total": 0}
        geojsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with geojsonl_path.open("w", encoding="utf-8") as fh:
            if not args.skip_us:
                if args.fetch_us_sequential:
                    append_us_flowlines_sequential(
                        fh,
                        seen_ids,
                        manifest=manifest,
                        min_stream_order=args.min_us_stream_order,
                        min_unnamed_length_km=args.min_unnamed_length_km,
                        huc2_filter=us_huc2_filter,
                        scratch_root=scratch_root,
                    )
                else:
                    append_us_flowlines(
                        args.us_parquet_dir,
                        fh,
                        seen_ids,
                        min_stream_order=args.min_us_stream_order,
                        min_unnamed_length_km=args.min_unnamed_length_km,
                        manifest=manifest,
                    )
            else:
                manifest["notes"].append("Skipped U.S. flowline packaging on this run")

            if not args.skip_canada:
                append_nhn_packages(
                    args.nhn_root,
                    fh,
                    seen_ids,
                    manifest_path=manifest_path,
                    manifest=manifest,
                    region_limit=args.region_limit,
                    package_limit=args.package_limit,
                    min_unnamed_length_m=args.min_unnamed_length_m,
                )
            else:
                manifest["notes"].append("Skipped Canadian NHN packaging on this run")

        manifest["counts"]["total"] = manifest["counts"]["US"] + manifest["counts"]["CA"]
        save_manifest(manifest_path, manifest)

        if manifest["counts"]["total"] == 0:
            print(json.dumps({"status": "no_features", "manifest": str(manifest_path)}, indent=2))
            return
    else:
        if not geojsonl_path.exists():
            raise FileNotFoundError(f"--package-only requested, but {geojsonl_path} does not exist")
        manifest["notes"].append("Package-only rerun from existing geojsonl")
        save_manifest(manifest_path, manifest)

    build_pmtiles(
        geojsonl_path,
        args.pmtiles_output,
        layer="river_network",
        name="OpenCatch North America River Network",
        description="Official hydrography backbone for river-network completeness",
        min_zoom=3,
        max_zoom=13,
    )
    print(
        json.dumps(
            {
                "geojsonl": str(geojsonl_path),
                "pmtiles": str(args.pmtiles_output),
                "manifest": str(manifest_path),
                "counts": manifest["counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
