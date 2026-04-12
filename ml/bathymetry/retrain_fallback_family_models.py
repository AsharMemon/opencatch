#!/usr/bin/env python3
"""
Retrain OpenCatch fallback bathymetry families from acquired survey data.

This script builds a lake-level max-depth benchmark out of the normalized
survey sources in `data/bathymetry/normalized`, then trains:

- `glacial_model`   → specialist on glacial-region lakes
- `mountain_model`  → specialist on mountain/western lakes
- `unified_fallback`→ generalist trained across all available regions

For each family, it reports:
- standard shuffled K-fold CV (across lakes)
- leave-one-region-out CV (holding out one source/jurisdiction at a time)

The goal is to refresh the max-depth priors used by the ML fallback contour
generator with the newest acquired state/province bathymetry stack.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import pickle
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from shapely.geometry import MultiPoint, box
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, LeaveOneGroupOut
from sklearn.pipeline import Pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fallback_family_models")

EPS = 1e-8
RNG = random.Random(42)

GLACIAL_CODES = {
    "CT", "FL", "IA", "IL", "IN", "MA", "ME", "MI", "MN", "NB",
    "NE", "NH", "NJ", "NS", "NY", "OH", "ON", "PA", "PE", "QC",
    "RI", "SK", "VT", "WI", "MB", "NL",
}
MOUNTAIN_CODES = {
    "AK", "AB", "AZ", "BC", "CO", "ID", "MT", "NM", "NV",
    "OR", "UT", "WA", "WY", "YT", "NT", "NU",
}

FEATURE_COLUMNS = [
    "area_km2",
    "log_area_km2",
    "perimeter_km",
    "compactness",
    "elongation",
    "bbox_fill_ratio",
    "lat",
    "lon",
    "abs_lat",
]

TRAINABLE_FEATURE_KINDS = {
    "contour_line",
    "depth_band",
    "depth_polygon",
    "depth_point",
    "lake_summary",
}


def classify_family(source_id: str) -> str:
    source_id = source_id.upper()
    if source_id in GLACIAL_CODES:
        return "glacial"
    if source_id in MOUNTAIN_CODES:
        return "mountain"
    return "unified"


def iter_geojson_features(path: Path) -> Iterable[dict]:
    """
    Stream newline-delimited features from the normalized GeoJSON files.

    Our normalized files are written as:
      {"type":"FeatureCollection","features":[
      {feature...},
      {feature...}
      ]}
    so line-by-line parsing is enough and avoids loading 1GB files whole.
    """
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith('{"type":"FeatureCollection"') or line.startswith('{"type": "FeatureCollection"'):
                continue
            if line in {"]}", "]", "}"}:
                continue
            if line.endswith(","):
                line = line[:-1]
            if not line.startswith("{"):
                continue
            yield json.loads(line)


def extract_target_depth_m(props: dict) -> float | None:
    candidates = (
        props.get("max_depth_m"),
        props.get("depth_max_m"),
        props.get("depth_m"),
    )
    valid = []
    for value in candidates:
        if value is None:
            continue
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value_f) and value_f > 0:
            valid.append(value_f)
    return max(valid) if valid else None


def iter_geometry_points(geometry: dict) -> Iterable[tuple[float, float]]:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if coords is None:
        return

    if gtype == "Point":
        yield float(coords[0]), float(coords[1])
    elif gtype in {"MultiPoint", "LineString"}:
        for x, y in coords:
            yield float(x), float(y)
    elif gtype in {"MultiLineString", "Polygon"}:
        for part in coords:
            for x, y in part:
                yield float(x), float(y)
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                for x, y in ring:
                    yield float(x), float(y)


@dataclass
class LakeAccumulator:
    lake_id: str
    lake_name: str
    source_id: str
    family: str
    feature_count: int = 0
    feature_kind_counts: Counter = field(default_factory=Counter)
    target_max_depth_m: float = math.nan
    sample_points: list[tuple[float, float]] = field(default_factory=list)
    points_seen: int = 0
    minx: float = math.inf
    miny: float = math.inf
    maxx: float = -math.inf
    maxy: float = -math.inf

    def add_feature(self, feature: dict) -> None:
        props = feature.get("properties", {})
        geometry = feature.get("geometry") or {}
        self.feature_count += 1
        kind = str(props.get("feature_kind") or "").strip().lower()
        if kind:
            self.feature_kind_counts[kind] += 1

        depth_m = extract_target_depth_m(props)
        if depth_m is not None and (not math.isfinite(self.target_max_depth_m) or depth_m > self.target_max_depth_m):
            self.target_max_depth_m = depth_m

        for x, y in iter_geometry_points(geometry):
            self.minx = min(self.minx, x)
            self.miny = min(self.miny, y)
            self.maxx = max(self.maxx, x)
            self.maxy = max(self.maxy, y)
            self.points_seen += 1
            if len(self.sample_points) < 768:
                self.sample_points.append((x, y))
            else:
                idx = RNG.randint(0, self.points_seen - 1)
                if idx < len(self.sample_points):
                    self.sample_points[idx] = (x, y)

    def to_row(self) -> dict | None:
        if not math.isfinite(self.target_max_depth_m) or self.target_max_depth_m <= 0:
            return None
        if not self.sample_points:
            return None

        lon0 = float(np.mean([pt[0] for pt in self.sample_points]))
        lat0 = float(np.mean([pt[1] for pt in self.sample_points]))
        scale_x = 111.320 * math.cos(math.radians(lat0))
        scale_y = 110.574

        metric_points = [((x - lon0) * scale_x, (y - lat0) * scale_y) for x, y in self.sample_points]
        hull = MultiPoint(metric_points).convex_hull

        if hull.area <= EPS or hull.length <= EPS:
            minx_km = (self.minx - lon0) * scale_x
            maxx_km = (self.maxx - lon0) * scale_x
            miny_km = (self.miny - lat0) * scale_y
            maxy_km = (self.maxy - lat0) * scale_y
            hull = box(minx_km, miny_km, maxx_km, maxy_km)

        minx_km = (self.minx - lon0) * scale_x
        maxx_km = (self.maxx - lon0) * scale_x
        miny_km = (self.miny - lat0) * scale_y
        maxy_km = (self.maxy - lat0) * scale_y
        width_km = max(abs(maxx_km - minx_km), 0.01)
        height_km = max(abs(maxy_km - miny_km), 0.01)
        bbox_area_km2 = width_km * height_km
        major = max(width_km, height_km)
        minor = max(min(width_km, height_km), 0.01)

        area_km2 = max(float(hull.area), 0.0001)
        perimeter_km = max(float(hull.length), 0.01)

        return {
            "lake_id": self.lake_id,
            "lake_name": self.lake_name,
            "source_id": self.source_id,
            "family": self.family,
            "target_max_depth_m": float(self.target_max_depth_m),
            "area_km2": area_km2,
            "log_area_km2": float(math.log1p(area_km2)),
            "perimeter_km": perimeter_km,
            "compactness": float((4.0 * math.pi * area_km2) / max(perimeter_km ** 2, EPS)),
            "elongation": float(major / minor),
            "bbox_fill_ratio": float(area_km2 / max(bbox_area_km2, EPS)),
            "lat": lat0,
            "lon": lon0,
            "abs_lat": abs(lat0),
            "feature_count": self.feature_count,
            "feature_kinds": ",".join(sorted(self.feature_kind_counts.keys())),
        }


def build_lake_dataset(normalized_dir: Path) -> pd.DataFrame:
    manifest_path = normalized_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest_by_source = {item["source_id"]: item for item in manifest}

    lakes: dict[tuple[str, str], LakeAccumulator] = {}

    for geojson_path in sorted(normalized_dir.glob("*_normalized.geojson")):
        source_id = geojson_path.name.replace("_normalized.geojson", "")
        meta = manifest_by_source.get(source_id, {})
        output_mode = meta.get("output_mode", "")
        log.info("Scanning %s (%s)", geojson_path.name, output_mode or "unknown")

        for feature in iter_geojson_features(geojson_path):
            props = feature.get("properties", {})
            lake_id = str(props.get("lake_id") or "").strip()
            if not lake_id:
                continue

            feature_kind = str(props.get("feature_kind") or "").strip().lower()
            if feature_kind and feature_kind not in TRAINABLE_FEATURE_KINDS:
                continue

            if extract_target_depth_m(props) is None:
                continue

            lake_name = str(props.get("lake_name") or lake_id)
            key = (source_id, lake_id)
            if key not in lakes:
                lakes[key] = LakeAccumulator(
                    lake_id=lake_id,
                    lake_name=lake_name,
                    source_id=source_id,
                    family=classify_family(source_id),
                )
            lakes[key].add_feature(feature)

    rows = []
    for acc in lakes.values():
        row = acc.to_row()
        if row is not None:
            rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("No trainable lake rows were built from normalized sources.")

    df = df.dropna(subset=["target_max_depth_m"])
    df = df[df["target_max_depth_m"] > 0].reset_index(drop=True)
    return df


def build_model(model_name: str) -> Pipeline:
    if model_name == "extra_trees":
        reg = ExtraTreesRegressor(
            n_estimators=250,
            min_samples_leaf=2,
            max_features="sqrt",
            random_state=42,
            n_jobs=-1,
        )
    elif model_name == "random_forest":
        reg = RandomForestRegressor(
            n_estimators=250,
            min_samples_leaf=2,
            max_features="sqrt",
            random_state=42,
            n_jobs=-1,
        )
    elif model_name == "hist_gbr":
        reg = HistGradientBoostingRegressor(
            max_depth=8,
            learning_rate=0.05,
            max_iter=250,
            min_samples_leaf=10,
            random_state=42,
        )
    else:
        raise ValueError(f"Unsupported model: {model_name}")

    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("regressor", reg),
    ])


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "rmse_m": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "mae_m": float(mean_absolute_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
        "n": int(len(y_true)),
    }


def downsample_by_group(df: pd.DataFrame, group_col: str, max_rows: int) -> pd.DataFrame:
    if max_rows <= 0 or len(df) <= max_rows:
        return df.reset_index(drop=True)

    parts = []
    total = len(df)
    for _, group in df.groupby(group_col, sort=False):
        share = len(group) / total
        take = max(2, int(round(max_rows * share)))
        take = min(take, len(group))
        parts.append(group.sample(n=take, random_state=42))

    sampled = pd.concat(parts, ignore_index=True)
    if len(sampled) > max_rows:
        sampled = sampled.sample(n=max_rows, random_state=42).reset_index(drop=True)
    return sampled.reset_index(drop=True)


def run_cv(
    df: pd.DataFrame,
    feature_cols: list[str],
    model_name: str,
    mode: str,
) -> dict:
    X = df[feature_cols]
    y = np.log1p(df["target_max_depth_m"].values.astype(np.float64))
    groups = df["source_id"].astype(str).values

    if mode == "kfold":
        n_splits = min(5, max(2, len(df) // 8))
        splitter = KFold(n_splits=n_splits, shuffle=True, random_state=42)
        split_iter = splitter.split(X)
    elif mode == "logo":
        if len(np.unique(groups)) < 2:
            return {"folds": [], "overall": None, "per_region": []}
        splitter = LeaveOneGroupOut()
        split_iter = splitter.split(X, y, groups=groups)
    else:
        raise ValueError(f"Unsupported CV mode: {mode}")

    oof = np.full(len(df), np.nan, dtype=np.float64)
    fold_rows = []

    for fold_idx, (train_idx, test_idx) in enumerate(split_iter, start=1):
        model = build_model(model_name)
        model.fit(X.iloc[train_idx], y[train_idx])
        pred_log = model.predict(X.iloc[test_idx])
        oof[test_idx] = pred_log

        pred = np.expm1(pred_log)
        true = df["target_max_depth_m"].values[test_idx]
        fold_metrics = compute_metrics(true, pred)
        fold_regions = sorted(set(df.iloc[test_idx]["source_id"].astype(str)))
        fold_rows.append({
            "fold": fold_idx,
            "regions": fold_regions,
            **fold_metrics,
        })

    valid = np.isfinite(oof)
    if valid.sum() < 2:
        return {"folds": fold_rows, "overall": None, "per_region": []}

    overall = compute_metrics(
        df.loc[valid, "target_max_depth_m"].values,
        np.expm1(oof[valid]),
    )

    per_region = []
    for region, group in df.groupby("source_id", sort=True):
        mask = valid & (df["source_id"].values == region)
        if mask.sum() < 2:
            continue
        per_region.append({
            "source_id": region,
            **compute_metrics(
                df.loc[mask, "target_max_depth_m"].values,
                np.expm1(oof[mask]),
            ),
        })

    return {
        "folds": fold_rows,
        "overall": overall,
        "per_region": per_region,
        "oof_pred_m": np.expm1(oof).tolist(),
    }


def select_best_model(df: pd.DataFrame, feature_cols: list[str], candidate_models: list[str]) -> tuple[str, dict]:
    best_name = ""
    best_report = {}
    best_rmse = math.inf
    for model_name in candidate_models:
        report = run_cv(df, feature_cols, model_name, mode="kfold")
        overall = report.get("overall")
        if overall is None:
            continue
        rmse = overall["rmse_m"]
        log.info(
            "  %s -> RMSE %.3fm | R2 %.4f | n=%d",
            model_name,
            rmse,
            overall["r2"],
            overall["n"],
        )
        if rmse < best_rmse:
            best_rmse = rmse
            best_name = model_name
            best_report = report
    if not best_name:
        raise RuntimeError("Could not select a best model.")
    return best_name, best_report


def train_final_model(df: pd.DataFrame, feature_cols: list[str], model_name: str) -> Pipeline:
    model = build_model(model_name)
    y = np.log1p(df["target_max_depth_m"].values.astype(np.float64))
    model.fit(df[feature_cols], y)
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrain regional/unified fallback depth models")
    parser.add_argument(
        "--normalized-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/normalized"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/models/fallback_families"),
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["hist_gbr", "random_forest", "extra_trees"],
        choices=["extra_trees", "hist_gbr", "random_forest"],
    )
    parser.add_argument(
        "--max-eval-rows-per-family",
        type=int,
        default=80000,
        help="Balanced per-family sample size for CV and leave-one-region-out evaluation",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    df = build_lake_dataset(args.normalized_dir)
    df.to_csv(args.output_dir / "fallback_lake_dataset.csv", index=False)
    log.info(
        "Built lake dataset: %d lakes across %d regions",
        len(df),
        df["source_id"].nunique(),
    )

    family_slices = {
        "glacial_model": df[df["family"] == "glacial"].copy(),
        "mountain_model": df[df["family"] == "mountain"].copy(),
        "unified_fallback": df.copy(),
    }

    summary = {
        "dataset": {
            "n_lakes": int(len(df)),
            "n_regions": int(df["source_id"].nunique()),
            "regions": sorted(df["source_id"].unique().tolist()),
        },
        "families": {},
        "features": FEATURE_COLUMNS,
    }

    for family_name, family_df in family_slices.items():
        family_df = family_df.reset_index(drop=True)
        if len(family_df) < 10 or family_df["source_id"].nunique() < 2:
            log.warning("Skipping %s; not enough data (%d lakes, %d regions)", family_name, len(family_df), family_df["source_id"].nunique())
            continue

        log.info(
            "\n=== %s ===\nLakes: %d | Regions: %d",
            family_name,
            len(family_df),
            family_df["source_id"].nunique(),
        )

        eval_df = downsample_by_group(family_df, "source_id", args.max_eval_rows_per_family)
        log.info(
            "Evaluation sample for %s: %d lakes across %d regions",
            family_name,
            len(eval_df),
            eval_df["source_id"].nunique(),
        )

        best_model_name, kfold_report = select_best_model(eval_df, FEATURE_COLUMNS, args.models)
        logo_report = run_cv(eval_df, FEATURE_COLUMNS, best_model_name, mode="logo")
        final_model = train_final_model(family_df, FEATURE_COLUMNS, best_model_name)

        artifact = {
            "model": final_model,
            "feature_columns": FEATURE_COLUMNS,
            "family_name": family_name,
            "best_model_name": best_model_name,
        }
        with open(args.output_dir / f"{family_name}.pkl", "wb") as handle:
            pickle.dump(artifact, handle)

        family_summary = {
            "n_lakes": int(len(family_df)),
            "n_regions": int(family_df["source_id"].nunique()),
            "eval_lakes": int(len(eval_df)),
            "regions": sorted(family_df["source_id"].unique().tolist()),
            "best_model": best_model_name,
            "kfold": {
                "overall": kfold_report["overall"],
                "folds": kfold_report["folds"],
            },
            "leave_one_region_out": {
                "overall": logo_report["overall"],
                "folds": logo_report["folds"],
                "per_region": logo_report["per_region"],
            },
        }
        summary["families"][family_name] = family_summary

        if kfold_report["overall"] is not None:
            log.info(
                "%s standard CV -> RMSE %.3fm | R2 %.4f",
                family_name,
                kfold_report["overall"]["rmse_m"],
                kfold_report["overall"]["r2"],
            )
        if logo_report["overall"] is not None:
            log.info(
                "%s leave-one-region-out -> RMSE %.3fm | R2 %.4f",
                family_name,
                logo_report["overall"]["rmse_m"],
                logo_report["overall"]["r2"],
            )

    with open(args.output_dir / "metrics.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    markdown_lines = [
        "# Fallback Family Retraining Results",
        "",
        f"- Lakes: `{summary['dataset']['n_lakes']}`",
        f"- Regions: `{summary['dataset']['n_regions']}`",
        "",
        "| Family | Best Model | Standard CV RMSE (m) | Standard CV R² | Leave-One-Region RMSE (m) | Leave-One-Region R² |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for family_name, metrics in summary["families"].items():
        kfold = metrics["kfold"]["overall"] or {}
        logo = metrics["leave_one_region_out"]["overall"] or {}
        markdown_lines.append(
            f"| {family_name} | {metrics['best_model']} | "
            f"{kfold.get('rmse_m', float('nan')):.3f} | {kfold.get('r2', float('nan')):.4f} | "
            f"{logo.get('rmse_m', float('nan')):.3f} | {logo.get('r2', float('nan')):.4f} |"
        )
    (args.output_dir / "metrics.md").write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")
    log.info("Saved models and metrics to %s", args.output_dir)


if __name__ == "__main__":
    main()
