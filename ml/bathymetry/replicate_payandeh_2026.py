#!/usr/bin/env python3
"""
Payandeh et al. (2026)-style ATL24 + Sentinel-2 bathymetry replication.

This script sets up a paper-style shallow/coastal bathymetry benchmark:

1. Load ATL24 bathymetry points from a local parquet/CSV OR query SlideRule's
   ATL24 endpoint directly for a bounding box and time range.
2. Discover up to N cloud-filtered Sentinel-2 scenes from Earth Search STAC OR
   read a local scene manifest (useful for ACOLITE-corrected rasters).
3. Extract the exact spectral features reported in the paper:
      - B2/B3, B2/B4, B3/B8, B4/B8
      - NDWI
      - B5, B6, B7
4. Split ATL24 points by whole track for honest validation.
5. Compare RF/XGBoost baselines and tuned variants on a reference scene.
6. Re-fit the selected model spec per scene, report per-scene metrics, then
   average scene maps and optionally smooth with a Gaussian kernel.

The goal is not to replace the inland-lake hybrid pipeline. This is a separate,
single-site replication path for "best-case" shallow, optically detectable
bathymetry, closer to the coastal lagoon setting in the paper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import ParameterSampler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("payandeh2026")

try:
    from scipy.ndimage import distance_transform_edt, gaussian_filter
except Exception:  # pragma: no cover - optional dependency
    distance_transform_edt = None
    gaussian_filter = None

try:
    import xgboost as xgb
except Exception:  # pragma: no cover - optional dependency
    xgb = None

try:
    import optuna
except Exception:  # pragma: no cover - optional dependency
    optuna = None

sys.path.insert(0, str(Path(__file__).parent))
try:
    from build_s2_composites import hedley_glint_correction
except Exception:
    hedley_glint_correction = None


S2_STAC_URL = "https://earth-search.aws.element84.com/v1"
PAPER_FEATURES = [
    "b2_b3_ratio",
    "b2_b4_ratio",
    "b3_b8_ratio",
    "b4_b8_ratio",
    "ndwi",
    "b5",
    "b6",
    "b7",
]
EXTENDED_FEATURES = PAPER_FEATURES + [
    "blue",
    "green",
    "red",
    "nir",
    "shore_dist_m",
    "row_norm",
    "col_norm",
]
FEATURE_SETS = {
    "paper": PAPER_FEATURES,
    "extended": EXTENDED_FEATURES,
}
DEFAULT_BAND_COLUMNS = [
    "blue",
    "green",
    "red",
    "rededge1",
    "rededge2",
    "rededge3",
    "nir",
]
DEFAULT_DEPTH_BIN_EDGES = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0]
EPS = 1e-8


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return {
        "n": int(len(y_true)),
        "rmse": rmse(y_true, y_pred),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)) if len(y_true) >= 2 else float("nan"),
        "bias": float(np.mean(y_pred - y_true)) if len(y_true) else float("nan"),
    }


def format_depth_label(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def summarize_depth_distribution(y_true: np.ndarray) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=float)
    y_true = y_true[np.isfinite(y_true)]
    if len(y_true) == 0:
        return {
            "depth_min": float("nan"),
            "depth_max": float("nan"),
            "depth_span": float("nan"),
            "depth_std": float("nan"),
        }
    depth_min = float(np.min(y_true))
    depth_max = float(np.max(y_true))
    return {
        "depth_min": depth_min,
        "depth_max": depth_max,
        "depth_span": float(depth_max - depth_min),
        "depth_std": float(np.std(y_true)),
    }


def compute_depth_bins(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    min_points: int = 5,
    edges: Optional[list[float]] = None,
) -> dict[str, dict[str, float]]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    if edges is None:
        edges = list(DEFAULT_DEPTH_BIN_EDGES)

    bins: list[tuple[str, float, Optional[float]]] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        bins.append((f"{format_depth_label(lo)}-{format_depth_label(hi)}m", lo, hi))
    if len(y_true) and np.nanmax(y_true) >= edges[-1]:
        bins.append((f"{format_depth_label(edges[-1])}m+", edges[-1], None))

    out: dict[str, dict[str, float]] = {}
    for label, lo, hi in bins:
        if hi is None:
            mask = y_true >= lo
        else:
            mask = (y_true >= lo) & (y_true < hi)
        n = int(mask.sum())
        summary = summarize_depth_distribution(y_true[mask])
        if n < min_points:
            out[label] = {
                "n": n,
                **summary,
                "rmse": float("nan"),
                "mae": float("nan"),
                "r2": float("nan"),
                "bias": float("nan"),
                "sub_1m_rmse": False,
            }
            continue
        metrics = regression_metrics(y_true[mask], y_pred[mask])
        out[label] = {
            **metrics,
            **summary,
            "sub_1m_rmse": bool(metrics["rmse"] < 1.0),
        }
    return out


def serialize_float_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: serialize_float_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_float_dict(v) for v in obj]
    if isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    if isinstance(obj, (np.integer, np.int32, np.int64)):
        return int(obj)
    return obj


def safe_write_table(df: pd.DataFrame, path: Path) -> Path:
    try:
        df.to_parquet(path, index=False)
        return path
    except Exception:
        fallback = path.with_suffix(".csv")
        df.to_csv(fallback, index=False)
        return fallback


def bbox_to_poly(bbox: tuple[float, float, float, float]) -> list[dict[str, float]]:
    west, south, east, north = bbox
    return [
        {"lon": west, "lat": south},
        {"lon": east, "lat": south},
        {"lon": east, "lat": north},
        {"lon": west, "lat": north},
        {"lon": west, "lat": south},
    ]


def normalize_scene_date(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text[:10]


def standardize_atl24_columns(df: pd.DataFrame, max_depth_m: float) -> pd.DataFrame:
    df = df.copy()

    if "geometry" in df.columns and ("lon" not in df.columns or "lat" not in df.columns):
        try:
            df["lon"] = df.geometry.x
            df["lat"] = df.geometry.y
        except Exception:
            pass

    column_aliases = {
        "lon_ph": "lon",
        "lat_ph": "lat",
        "longitude": "lon",
        "latitude": "lat",
        "ellipse_h": "ellipsoid_h",
        "ortho_h": "bottom_ortho_h",
        "surface_h": "surface_ortho_h",
    }
    for src, dst in column_aliases.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    if "depth" in df.columns and "depth_m" not in df.columns:
        df["depth_m"] = df["depth"]
    elif "depth_m" not in df.columns:
        if {"surface_ortho_h", "bottom_ortho_h"}.issubset(df.columns):
            df["depth_m"] = df["surface_ortho_h"] - df["bottom_ortho_h"]
        elif {"surface_h", "ortho_h"}.issubset(df.columns):
            df["depth_m"] = df["surface_h"] - df["ortho_h"]
        else:
            raise ValueError("Could not derive depth_m from ATL24 input columns")

    if "confidence" not in df.columns:
        df["confidence"] = np.nan

    if "class_ph" in df.columns:
        df = df[df["class_ph"].isin([40, 41, "bathymetry", "sea_surface"])]
        if pd.api.types.is_numeric_dtype(df["class_ph"]):
            df = df[df["class_ph"] == 40]
        else:
            df = df[df["class_ph"].astype(str).str.lower() == "bathymetry"]

    if "gt" not in df.columns:
        for candidate in ["beam", "gtx", "ground_track"]:
            if candidate in df.columns:
                df["gt"] = df[candidate]
                break

    if "rgt" not in df.columns:
        df["rgt"] = "unknown"
    if "cycle" not in df.columns:
        df["cycle"] = "unknown"
    if "gt" not in df.columns:
        df["gt"] = "unknown"

    if "region" not in df.columns:
        df["region"] = df.get("region_number", "unknown")

    df["track_id"] = (
        df["rgt"].astype(str)
        + "_"
        + df["cycle"].astype(str)
        + "_"
        + df["gt"].astype(str)
        + "_"
        + df["region"].astype(str)
    )

    if "time" not in df.columns:
        if "time_str" in df.columns:
            df["time"] = pd.to_datetime(df["time_str"], errors="coerce")
        elif "delta_time" in df.columns:
            df["time"] = pd.to_datetime(df["delta_time"], unit="s", origin="2018-01-01", errors="coerce")
        elif "time_ns" in df.columns:
            df["time"] = pd.to_datetime(df["time_ns"], unit="ns", errors="coerce")
        else:
            df["time"] = pd.NaT
    else:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")

    required = ["lon", "lat", "depth_m", "track_id"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"ATL24 input missing required columns: {missing}")

    df = df[np.isfinite(df["lon"]) & np.isfinite(df["lat"]) & np.isfinite(df["depth_m"])]
    df = df[(df["depth_m"] > 0) & (df["depth_m"] <= max_depth_m)]
    df = df.reset_index(drop=True)
    return df


def load_atl24_points(path: Path, max_depth_m: float) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported ATL24 format: {path}")
    return standardize_atl24_columns(df, max_depth_m=max_depth_m)


def filter_atl24_tracks(
    df: pd.DataFrame,
    min_track_points: int = 3,
    trim_edge_fraction: float = 0.05,
    outlier_window: int = 7,
    outlier_mad_mult: float = 3.5,
    outlier_abs_tol: float = 1.5,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    cleaned_groups: list[pd.DataFrame] = []
    dropped_small = 0
    dropped_after_filter = 0

    for track_id, grp in df.groupby("track_id", sort=False):
        grp = grp.copy()
        sort_col = "x_atc" if "x_atc" in grp.columns else "time"
        if sort_col in grp.columns:
            grp = grp.sort_values(sort_col)

        if len(grp) < min_track_points:
            dropped_small += len(grp)
            continue

        if trim_edge_fraction > 0 and len(grp) >= max(min_track_points * 2, 10):
            trim_n = int(len(grp) * trim_edge_fraction)
            if trim_n > 0 and len(grp) - 2 * trim_n >= min_track_points:
                grp = grp.iloc[trim_n: len(grp) - trim_n].copy()

        if len(grp) >= max(outlier_window, 5):
            window = min(outlier_window, len(grp) if len(grp) % 2 == 1 else len(grp) - 1)
            if window >= 3:
                depth = grp["depth_m"].to_numpy(dtype=float)
                depth_series = pd.Series(depth)
                local_med = depth_series.rolling(window, center=True, min_periods=1).median().to_numpy()
                residual = np.abs(depth - local_med)
                local_mad = (
                    pd.Series(residual).rolling(window, center=True, min_periods=1).median().to_numpy()
                )
                local_sigma = 1.4826 * np.clip(local_mad, 0.15, None)
                threshold = np.maximum(outlier_abs_tol, outlier_mad_mult * local_sigma)
                keep = residual <= threshold
                grp = grp.loc[keep].copy()

        if len(grp) < min_track_points:
            dropped_after_filter += len(grp)
            continue

        cleaned_groups.append(grp)

    if not cleaned_groups:
        raise RuntimeError("ATL24 filtering removed all track data")

    out = pd.concat(cleaned_groups, ignore_index=True)
    log.info(
        "ATL24 track filtering: %d -> %d rows, %d -> %d tracks "
        "(dropped_small=%d, dropped_after_filter=%d)",
        len(df),
        len(out),
        df["track_id"].nunique(),
        out["track_id"].nunique(),
        dropped_small,
        dropped_after_filter,
    )
    return out


def query_atl24_sliderule(
    bbox: tuple[float, float, float, float],
    start: str,
    end: str,
    max_depth_m: float,
    confidence_threshold: float = 0.8,
) -> pd.DataFrame:
    try:
        from sliderule import sliderule
    except Exception as exc:  # pragma: no cover - env dependent
        raise RuntimeError("sliderule is required for --query-atl24 mode") from exc

    sliderule.init("slideruleearth.io", verbose=False)
    parms = {
        "poly": bbox_to_poly(bbox),
        "t0": start,
        "t1": end,
        "srt": -1,
        "cnf": -1,
        "atl24": {
            "class_ph": ["bathymetry"],
            "confidence_threshold": float(confidence_threshold),
        },
        # Request a few fields explicitly so track grouping survives.
        "anc_fields": ["index_ph", "confidence", "class_ph", "surface_h", "ortho_h"],
    }
    log.info("Querying ATL24 from SlideRule for bbox=%s time=%s..%s", bbox, start, end)
    gdf = sliderule.run("atl24x", parms)
    if gdf is None or len(gdf) == 0:
        raise RuntimeError("No ATL24 points returned from SlideRule")
    if hasattr(gdf, "to_crs"):
        try:
            gdf = gdf.to_crs("EPSG:4326")
        except Exception:
            pass
    try:
        df = gdf.copy()
    except Exception:
        df = pd.DataFrame(gdf)
    if "geometry" not in df.columns and hasattr(gdf, "geometry"):
        try:
            df["geometry"] = gdf.geometry
        except Exception:
            pass
    df = df.reset_index()
    return standardize_atl24_columns(df, max_depth_m=max_depth_m)


def prioritize_scenes(
    scenes: pd.DataFrame,
    atl24_points: pd.DataFrame,
) -> pd.DataFrame:
    scenes = scenes.copy()
    if "scene_date" not in scenes.columns or "time" not in atl24_points.columns:
        return scenes

    point_dates = pd.to_datetime(atl24_points["time"], errors="coerce").dropna().dt.normalize().unique()
    if len(point_dates) == 0:
        return scenes

    point_dates = pd.to_datetime(pd.Series(point_dates)).sort_values().to_numpy()
    deltas = []
    for _, row in scenes.iterrows():
        scene_dt = pd.to_datetime(row["scene_date"], errors="coerce")
        if pd.isna(scene_dt):
            deltas.append(np.inf)
            continue
        diff_days = np.abs((point_dates - np.datetime64(scene_dt.normalize())) / np.timedelta64(1, "D"))
        deltas.append(float(np.min(diff_days)) if len(diff_days) else np.inf)
    scenes["min_days_to_atl24"] = deltas

    sort_cols = ["min_days_to_atl24"]
    ascending = [True]
    if "cloud_cover" in scenes.columns:
        sort_cols.append("cloud_cover")
        ascending.append(True)
    scenes = scenes.sort_values(sort_cols, ascending=ascending).reset_index(drop=True)
    return scenes


def get_stac_client():
    from pystac_client import Client

    return Client.open(S2_STAC_URL)


def discover_stac_scenes(
    bbox: tuple[float, float, float, float],
    start: str,
    end: str,
    max_cloud: float,
    max_scenes: int,
    month_min: Optional[int],
    month_max: Optional[int],
) -> pd.DataFrame:
    client = get_stac_client()
    search = client.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
        sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        max_items=max(10, max_scenes * 4),
    )
    items = list(search.items())
    rows: list[dict[str, Any]] = []
    for item in items:
        dt = normalize_scene_date(item.properties.get("datetime"))
        if month_min is not None and month_max is not None and dt:
            month = int(dt[5:7])
            if month < month_min or month > month_max:
                continue
        assets = item.assets
        required = ["blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir", "swir16"]
        if any(name not in assets for name in required):
            continue
        rows.append(
            {
                "scene_id": item.id,
                "scene_date": dt,
                "cloud_cover": float(item.properties.get("eo:cloud_cover", np.nan)),
                "blue": assets["blue"].href,
                "green": assets["green"].href,
                "red": assets["red"].href,
                "rededge1": assets["rededge1"].href,
                "rededge2": assets["rededge2"].href,
                "rededge3": assets["rededge3"].href,
                "nir": assets["nir"].href,
                "swir16": assets["swir16"].href,
                "scl": assets["scl"].href if "scl" in assets else None,
                "input_scale": 10000.0,
                "source": "stac_l2a",
            }
        )
        if len(rows) >= max_scenes:
            break
    if not rows:
        raise RuntimeError("No usable Sentinel-2 scenes found from STAC")
    return pd.DataFrame(rows)


def load_scene_manifest(path: Path, default_scale: float) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    required = ["scene_id", "scene_date", "blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Scene manifest missing columns: {missing}")
    if "input_scale" not in df.columns:
        df["input_scale"] = float(default_scale)
    if "source" not in df.columns:
        df["source"] = "manifest"
    return df.reset_index(drop=True)


@dataclass
class SceneGrid:
    scene_id: str
    scene_date: str
    features: dict[str, np.ndarray]
    water_mask: np.ndarray
    transform: Any
    crs: Any
    profile: dict[str, Any]
    lon_grid: Optional[np.ndarray]
    lat_grid: Optional[np.ndarray]


@dataclass
class SceneTrackSplit:
    train_tracks: list[str]
    val_tracks: list[str]
    strategy: str
    adequate: bool
    r2_stable: bool
    n_train: int
    n_val: int
    n_train_tracks: int
    n_val_tracks: int
    val_depth_min: float
    val_depth_max: float
    val_depth_span: float
    val_depth_std: float
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "train_tracks": self.train_tracks,
            "val_tracks": self.val_tracks,
            "strategy": self.strategy,
            "adequate": self.adequate,
            "r2_stable": self.r2_stable,
            "n_train": self.n_train,
            "n_val": self.n_val,
            "n_train_tracks": self.n_train_tracks,
            "n_val_tracks": self.n_val_tracks,
            "val_depth_min": self.val_depth_min,
            "val_depth_max": self.val_depth_max,
            "val_depth_span": self.val_depth_span,
            "val_depth_std": self.val_depth_std,
            "notes": self.notes,
        }


def build_scene_stack(
    row: pd.Series,
    bbox: tuple[float, float, float, float],
    apply_deglint: bool,
    ndwi_threshold: float,
) -> SceneGrid:
    import rasterio
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    band_names = ["blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir", "swir16"]
    ref_href = row["blue"]

    with rasterio.open(ref_href) as ref_ds:
        ref_crs = ref_ds.crs
        transformer = Transformer.from_crs("EPSG:4326", ref_crs, always_xy=True)
        west, south = transformer.transform(bbox[0], bbox[1])
        east, north = transformer.transform(bbox[2], bbox[3])
        window = from_bounds(west, south, east, north, ref_ds.transform)
        window = window.intersection(rasterio.windows.Window(0, 0, ref_ds.width, ref_ds.height))
        ref_data = ref_ds.read(1, window=window, boundless=True)
        ref_transform = ref_ds.window_transform(window)
        ref_profile = ref_ds.profile.copy()
        ref_shape = ref_data.shape

    features: dict[str, np.ndarray] = {}
    scale = float(row.get("input_scale", 1.0) or 1.0)
    for band_name in band_names:
        href = row.get(band_name)
        if href is None or (isinstance(href, float) and np.isnan(href)):
            if band_name == "swir16":
                continue
            raise ValueError(f"Scene {row['scene_id']} missing band {band_name}")
        with rasterio.open(href) as ds:
            transformer = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
            west, south = transformer.transform(bbox[0], bbox[1])
            east, north = transformer.transform(bbox[2], bbox[3])
            window = from_bounds(west, south, east, north, ds.transform)
            window = window.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
            data = ds.read(
                1,
                window=window,
                out_shape=ref_shape,
                resampling=Resampling.bilinear if band_name != "scl" else Resampling.nearest,
                boundless=True,
            )
            arr = data.astype(np.float32)
            if scale and scale != 1.0:
                arr = arr / scale
            arr = np.where(np.isfinite(arr), arr, np.nan)
            features[band_name] = arr

    scl_mask = np.ones(ref_shape, dtype=bool)
    scl_href = row.get("scl")
    if scl_href is not None and not (isinstance(scl_href, float) and np.isnan(scl_href)):
        with rasterio.open(scl_href) as ds:
            transformer = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
            west, south = transformer.transform(bbox[0], bbox[1])
            east, north = transformer.transform(bbox[2], bbox[3])
            window = from_bounds(west, south, east, north, ds.transform)
            window = window.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
            scl = ds.read(
                1,
                window=window,
                out_shape=ref_shape,
                resampling=Resampling.nearest,
                boundless=True,
            )
            scl_mask = np.isin(scl, [4, 5, 6])

    if apply_deglint and hedley_glint_correction is not None and "swir16" in features:
        stack = np.stack(
            [
                features["blue"],
                features["green"],
                features["red"],
                features["rededge1"],
                features["rededge2"],
                features["rededge3"],
                features["nir"],
                features["swir16"],
            ],
            axis=0,
        )
        deglinted = hedley_glint_correction(
            stack,
            nir_idx=6,
            swir_idx=7,
            water_mask=scl_mask,
        )
        for idx, name in enumerate(["blue", "green", "red", "rededge1", "rededge2", "rededge3", "nir", "swir16"]):
            features[name] = deglinted[idx]

    ndwi = (features["green"] - features["nir"]) / (features["green"] + features["nir"] + EPS)
    water_mask = scl_mask & np.isfinite(ndwi) & (ndwi > ndwi_threshold)

    # Lat/lon grids are useful when points are in WGS84 and rasters in UTM.
    rows, cols = np.indices(ref_shape)
    features["row_norm"] = rows.astype(np.float32) / max(ref_shape[0] - 1, 1)
    features["col_norm"] = cols.astype(np.float32) / max(ref_shape[1] - 1, 1)
    if distance_transform_edt is not None:
        pixel_size_x = float(math.hypot(ref_transform.a, ref_transform.b))
        pixel_size_y = float(math.hypot(ref_transform.d, ref_transform.e))
        pixel_size_m = float(np.nanmean([pixel_size_x, pixel_size_y]))
        features["shore_dist_m"] = distance_transform_edt(water_mask.astype(np.uint8)).astype(np.float32) * pixel_size_m
    else:
        features["shore_dist_m"] = np.where(water_mask, np.nan, np.nan).astype(np.float32)
    xs, ys = rasterio.transform.xy(ref_transform, rows, cols)
    xs = np.asarray(xs)
    ys = np.asarray(ys)
    lon_grid, lat_grid = None, None
    try:
        to_wgs84 = Transformer.from_crs(ref_crs, "EPSG:4326", always_xy=True)
        lon_grid, lat_grid = to_wgs84.transform(xs, ys)
        lon_grid = np.asarray(lon_grid, dtype=np.float64)
        lat_grid = np.asarray(lat_grid, dtype=np.float64)
    except Exception:
        pass

    ref_profile.update(
        {
            "height": ref_shape[0],
            "width": ref_shape[1],
            "count": 1,
            "dtype": "float32",
            "transform": ref_transform,
            "crs": ref_crs,
        }
    )

    return SceneGrid(
        scene_id=str(row["scene_id"]),
        scene_date=normalize_scene_date(row["scene_date"]),
        features=features,
        water_mask=water_mask,
        transform=ref_transform,
        crs=ref_crs,
        profile=ref_profile,
        lon_grid=lon_grid,
        lat_grid=lat_grid,
    )


def sample_scene_at_points(
    scene: SceneGrid,
    points: pd.DataFrame,
    max_scene_date_delta_days: Optional[int] = None,
) -> pd.DataFrame:
    import rasterio
    from pyproj import Transformer

    scene_points = points
    if max_scene_date_delta_days is not None and "time" in points.columns:
        scene_dt = pd.to_datetime(scene.scene_date, errors="coerce")
        point_dt = pd.to_datetime(points["time"], errors="coerce")
        if pd.notna(scene_dt):
            delta_days = np.abs((point_dt - scene_dt).dt.total_seconds()) / 86400.0
            mask = delta_days <= float(max_scene_date_delta_days)
            scene_points = points.loc[mask].copy()
            if scene_points.empty:
                return pd.DataFrame()
        else:
            scene_points = points.copy()

    if scene.crs and str(scene.crs).upper() != "EPSG:4326":
        transformer = Transformer.from_crs("EPSG:4326", scene.crs, always_xy=True)
        xs, ys = transformer.transform(scene_points["lon"].to_numpy(), scene_points["lat"].to_numpy())
        rows, cols = rasterio.transform.rowcol(scene.transform, xs, ys)
    else:
        rows, cols = rasterio.transform.rowcol(
            scene.transform,
            scene_points["lon"].to_numpy(),
            scene_points["lat"].to_numpy(),
        )

    sampled: dict[str, list[Any]] = {
        "point_id": [],
        "scene_id": [],
        "scene_date": [],
        "track_id": [],
        "depth_m": [],
        "confidence": [],
        "time": [],
    }
    for key in PAPER_FEATURES:
        sampled[key] = []
    for key in ["blue", "green", "red", "nir", "shore_dist_m", "row_norm", "col_norm"]:
        sampled[key] = []

    for idx, (row_i, col_i) in enumerate(zip(rows, cols)):
        if row_i < 0 or col_i < 0 or row_i >= scene.water_mask.shape[0] or col_i >= scene.water_mask.shape[1]:
            continue
        if not scene.water_mask[row_i, col_i]:
            continue
        values = {
            "blue": scene.features["blue"][row_i, col_i],
            "green": scene.features["green"][row_i, col_i],
            "red": scene.features["red"][row_i, col_i],
            "rededge1": scene.features["rededge1"][row_i, col_i],
            "rededge2": scene.features["rededge2"][row_i, col_i],
            "rededge3": scene.features["rededge3"][row_i, col_i],
            "nir": scene.features["nir"][row_i, col_i],
        }
        if not all(np.isfinite(v) for v in values.values()):
            continue
        feats = compute_payandeh_features(values)
        if not all(np.isfinite(feats[k]) for k in PAPER_FEATURES):
            continue
        extra = {
            "blue": float(values["blue"]),
            "green": float(values["green"]),
            "red": float(values["red"]),
            "nir": float(values["nir"]),
            "shore_dist_m": float(scene.features["shore_dist_m"][row_i, col_i]),
            "row_norm": float(scene.features["row_norm"][row_i, col_i]),
            "col_norm": float(scene.features["col_norm"][row_i, col_i]),
        }
        if not all(np.isfinite(extra[k]) for k in extra):
            continue
        sampled["point_id"].append(int(scene_points.iloc[idx]["point_id"]))
        sampled["scene_id"].append(scene.scene_id)
        sampled["scene_date"].append(scene.scene_date)
        sampled["track_id"].append(scene_points.iloc[idx]["track_id"])
        sampled["depth_m"].append(float(scene_points.iloc[idx]["depth_m"]))
        sampled["confidence"].append(float(scene_points.iloc[idx].get("confidence", np.nan)))
        sampled["time"].append(scene_points.iloc[idx].get("time"))
        for key in PAPER_FEATURES:
            sampled[key].append(float(feats[key]))
        for key, value in extra.items():
            sampled[key].append(value)

    return pd.DataFrame(sampled)


def compute_payandeh_features(values: dict[str, float] | pd.Series) -> dict[str, float]:
    blue = float(values["blue"])
    green = float(values["green"])
    red = float(values["red"])
    b5 = float(values["rededge1"])
    b6 = float(values["rededge2"])
    b7 = float(values["rededge3"])
    nir = float(values["nir"])
    return {
        "b2_b3_ratio": blue / max(green, EPS),
        "b2_b4_ratio": blue / max(red, EPS),
        "b3_b8_ratio": green / max(nir, EPS),
        "b4_b8_ratio": red / max(nir, EPS),
        "ndwi": (green - nir) / (green + nir + EPS),
        "b5": b5,
        "b6": b6,
        "b7": b7,
    }


def split_tracks(
    atl24_points: pd.DataFrame,
    val_fraction: float,
    seed: int,
) -> tuple[list[str], list[str]]:
    tracks = sorted(pd.Series(atl24_points["track_id"].unique()).astype(str).tolist())
    if len(tracks) < 2:
        raise ValueError("Need at least two unique track IDs for a track-holdout split")
    rng = random.Random(seed)
    rng.shuffle(tracks)
    n_val = max(1, int(round(len(tracks) * val_fraction)))
    val_tracks = sorted(tracks[:n_val])
    train_tracks = sorted(tracks[n_val:])
    return train_tracks, val_tracks


def stable_scene_seed(seed: int, scene_id: str) -> int:
    digest = hashlib.sha1(scene_id.encode("utf-8")).hexdigest()
    return seed + int(digest[:8], 16)


def summarize_scene_split(
    scene_df: pd.DataFrame,
    train_tracks: list[str],
    val_tracks: list[str],
    strategy: str,
    min_train_points: int,
    min_val_points: int,
    min_val_tracks: int,
    min_val_depth_span: float,
) -> SceneTrackSplit:
    train_df = scene_df[scene_df["track_id"].isin(train_tracks)].copy()
    val_df = scene_df[scene_df["track_id"].isin(val_tracks)].copy()
    total_tracks = int(scene_df["track_id"].nunique())
    required_val_tracks = max(1, min(min_val_tracks, max(1, total_tracks - 1)))
    val_depth = summarize_depth_distribution(val_df["depth_m"].to_numpy(dtype=float))
    n_train_tracks = int(train_df["track_id"].nunique())
    n_val_tracks = int(val_df["track_id"].nunique())

    adequate = (
        len(train_df) >= min_train_points
        and len(val_df) >= min_val_points
        and n_train_tracks >= 1
        and n_val_tracks >= required_val_tracks
    )
    r2_stable = adequate and val_depth["depth_span"] >= float(min_val_depth_span)

    notes: list[str] = []
    if len(train_df) < min_train_points:
        notes.append(f"train_points<{min_train_points}")
    if len(val_df) < min_val_points:
        notes.append(f"val_points<{min_val_points}")
    if n_val_tracks < required_val_tracks:
        notes.append(f"val_tracks<{required_val_tracks}")
    if np.isfinite(val_depth["depth_span"]) and val_depth["depth_span"] < float(min_val_depth_span):
        notes.append(f"val_depth_span<{min_val_depth_span:.2f}m")
    if not np.isfinite(val_depth["depth_span"]):
        notes.append("val_depth_span=nan")

    return SceneTrackSplit(
        train_tracks=sorted(pd.Series(train_tracks).astype(str).tolist()),
        val_tracks=sorted(pd.Series(val_tracks).astype(str).tolist()),
        strategy=strategy,
        adequate=bool(adequate),
        r2_stable=bool(r2_stable),
        n_train=int(len(train_df)),
        n_val=int(len(val_df)),
        n_train_tracks=n_train_tracks,
        n_val_tracks=n_val_tracks,
        val_depth_min=val_depth["depth_min"],
        val_depth_max=val_depth["depth_max"],
        val_depth_span=val_depth["depth_span"],
        val_depth_std=val_depth["depth_std"],
        notes=notes,
    )


def choose_scene_track_split(
    scene_id: str,
    scene_df: pd.DataFrame,
    global_train_tracks: list[str],
    global_val_tracks: list[str],
    val_fraction: float,
    seed: int,
    min_train_points: int,
    min_val_points: int,
    min_val_tracks: int,
    min_val_depth_span: float,
) -> SceneTrackSplit:
    available_tracks = sorted(pd.Series(scene_df["track_id"].unique()).astype(str).tolist())
    if len(available_tracks) < 2:
        return summarize_scene_split(
            scene_df=scene_df,
            train_tracks=available_tracks,
            val_tracks=[],
            strategy="insufficient_scene_tracks",
            min_train_points=min_train_points,
            min_val_points=min_val_points,
            min_val_tracks=min_val_tracks,
            min_val_depth_span=min_val_depth_span,
        )

    candidates: list[SceneTrackSplit] = []

    global_train = sorted(set(available_tracks).intersection(global_train_tracks))
    global_val = sorted(set(available_tracks).intersection(global_val_tracks))
    if global_train and global_val:
        candidates.append(
            summarize_scene_split(
                scene_df=scene_df,
                train_tracks=global_train,
                val_tracks=global_val,
                strategy="global_track_split",
                min_train_points=min_train_points,
                min_val_points=min_val_points,
                min_val_tracks=min_val_tracks,
                min_val_depth_span=min_val_depth_span,
            )
        )

    scene_tracks = available_tracks.copy()
    rng = random.Random(stable_scene_seed(seed, scene_id))
    rng.shuffle(scene_tracks)
    n_val = max(1, int(round(len(scene_tracks) * val_fraction)))
    if len(scene_tracks) >= 4:
        n_val = max(2, n_val)
    n_val = min(n_val, len(scene_tracks) - 1)
    scene_val = sorted(scene_tracks[:n_val])
    scene_train = sorted(scene_tracks[n_val:])
    if scene_train and scene_val:
        candidates.append(
            summarize_scene_split(
                scene_df=scene_df,
                train_tracks=scene_train,
                val_tracks=scene_val,
                strategy="scene_specific_track_split",
                min_train_points=min_train_points,
                min_val_points=min_val_points,
                min_val_tracks=min_val_tracks,
                min_val_depth_span=min_val_depth_span,
            )
        )

    if not candidates:
        return summarize_scene_split(
            scene_df=scene_df,
            train_tracks=available_tracks[:-1],
            val_tracks=available_tracks[-1:],
            strategy="fallback_last_track",
            min_train_points=min_train_points,
            min_val_points=min_val_points,
            min_val_tracks=min_val_tracks,
            min_val_depth_span=min_val_depth_span,
        )

    def score(split: SceneTrackSplit) -> tuple[int, int, int, float, int, int, int]:
        # Prefer stable, adequate splits with broader validation depth range.
        strategy_bonus = 1 if split.strategy == "global_track_split" else 0
        val_span = split.val_depth_span if np.isfinite(split.val_depth_span) else -1.0
        return (
            int(split.r2_stable),
            int(split.adequate),
            split.n_val_tracks,
            float(val_span),
            split.n_val,
            split.n_train,
            strategy_bonus,
        )

    return max(candidates, key=score)


def choose_reference_scene_id(
    scene_splits: dict[str, SceneTrackSplit],
) -> str:
    if not scene_splits:
        raise ValueError("No candidate scenes available for reference-scene selection")

    def score(item: tuple[str, SceneTrackSplit]) -> tuple[int, int, int, float, int, int]:
        scene_id, split = item
        val_span = split.val_depth_span if np.isfinite(split.val_depth_span) else -1.0
        return (
            int(split.r2_stable),
            int(split.adequate),
            split.n_val_tracks,
            float(val_span),
            split.n_val,
            split.n_train,
        )

    return max(scene_splits.items(), key=score)[0]


def make_model_candidates(random_state: int) -> dict[str, tuple[str, dict[str, Any]]]:
    candidates: dict[str, tuple[str, dict[str, Any]]] = {
        "rf_manual": (
            "rf",
            {
                "n_estimators": 400,
                "max_depth": 16,
                "min_samples_leaf": 2,
                "random_state": random_state,
                "n_jobs": -1,
            },
        ),
    }
    if xgb is not None:
        candidates["xgb_manual"] = (
            "xgb",
            {
                "n_estimators": 500,
                "max_depth": 6,
                "learning_rate": 0.05,
                "subsample": 0.85,
                "colsample_bytree": 0.85,
                "objective": "reg:squarederror",
                "tree_method": "hist",
                "random_state": random_state,
                "n_jobs": 4,
            },
        )
    return candidates


def instantiate_model(model_type: str, params: dict[str, Any]):
    if model_type == "rf":
        return RandomForestRegressor(**params)
    if model_type == "xgb":
        if xgb is None:
            raise RuntimeError("xgboost is not installed")
        return xgb.XGBRegressor(**params)
    raise ValueError(f"Unsupported model type: {model_type}")


def fallback_random_search(
    model_type: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    seed: int,
    n_trials: int,
) -> tuple[dict[str, Any], dict[str, float]]:
    rng = np.random.default_rng(seed)

    if model_type == "rf":
        param_grid = {
            "n_estimators": [200, 300, 400, 600, 800],
            "max_depth": [8, 10, 12, 14, 16, 20, None],
            "min_samples_leaf": [1, 2, 4, 8],
            "max_features": [0.5, 0.7, 1.0, "sqrt"],
        }
        base = {"random_state": seed, "n_jobs": -1}
    elif model_type == "xgb":
        if xgb is None:
            raise RuntimeError("xgboost is not installed")
        param_grid = {
            "n_estimators": [200, 300, 500, 700, 900],
            "max_depth": [3, 4, 5, 6, 8, 10, 12],
            "learning_rate": [0.01, 0.03, 0.05, 0.08, 0.12, 0.2],
            "subsample": [0.5, 0.65, 0.8, 0.9, 1.0],
            "colsample_bytree": [0.5, 0.65, 0.8, 0.9, 1.0],
            "min_child_weight": [1, 2, 4, 8],
            "reg_lambda": [0.5, 1.0, 2.0, 4.0],
        }
        base = {
            "objective": "reg:squarederror",
            "tree_method": "hist",
            "random_state": seed,
            "n_jobs": 4,
        }
    else:
        raise ValueError(model_type)

    best_params: Optional[dict[str, Any]] = None
    best_metrics: Optional[dict[str, float]] = None
    sampled = list(ParameterSampler(param_grid, n_iter=max(1, n_trials), random_state=seed))
    rng.shuffle(sampled)

    for params in sampled:
        full_params = {**base, **params}
        model = instantiate_model(model_type, full_params)
        model.fit(X_train, y_train)
        pred = model.predict(X_val)
        metrics = regression_metrics(y_val, pred)
        if best_metrics is None or metrics["rmse"] < best_metrics["rmse"]:
            best_params = full_params
            best_metrics = metrics

    assert best_params is not None and best_metrics is not None
    return best_params, best_metrics


def optuna_search(
    model_type: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    seed: int,
    n_trials: int,
) -> tuple[dict[str, Any], dict[str, float]]:
    if optuna is None:
        raise RuntimeError("optuna is not installed")

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="minimize", sampler=sampler)

    def objective(trial: "optuna.Trial") -> float:
        if model_type == "rf":
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 200, 800, step=100),
                "max_depth": trial.suggest_int("max_depth", 8, 24, step=2),
                "min_samples_leaf": trial.suggest_int("min_samples_leaf", 1, 8),
                "max_features": trial.suggest_float("max_features", 0.5, 1.0),
                "random_state": seed,
                "n_jobs": -1,
            }
        elif model_type == "xgb":
            if xgb is None:
                raise RuntimeError("xgboost is not installed")
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 100, 1000, step=100),
                "max_depth": trial.suggest_int("max_depth", 3, 12),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "min_child_weight": trial.suggest_float("min_child_weight", 1.0, 8.0),
                "reg_lambda": trial.suggest_float("reg_lambda", 0.25, 5.0, log=True),
                "objective": "reg:squarederror",
                "tree_method": "hist",
                "random_state": seed,
                "n_jobs": 4,
            }
        else:
            raise ValueError(model_type)
        model = instantiate_model(model_type, params)
        model.fit(X_train, y_train)
        pred = model.predict(X_val)
        return rmse(y_val, pred)

    study.optimize(objective, n_trials=max(1, n_trials), show_progress_bar=False)
    best_params = dict(study.best_params)
    if model_type == "rf":
        best_params.update({"random_state": seed, "n_jobs": -1})
    else:
        best_params.update(
            {
                "objective": "reg:squarederror",
                "tree_method": "hist",
                "random_state": seed,
                "n_jobs": 4,
            }
        )
    model = instantiate_model(model_type, best_params)
    model.fit(X_train, y_train)
    pred = model.predict(X_val)
    return best_params, regression_metrics(y_val, pred)


def compare_models_on_reference_scene(
    ref_df: pd.DataFrame,
    train_tracks: list[str],
    val_tracks: list[str],
    feature_columns: list[str],
    seed: int,
    tuning_trials: int,
    min_train_points: int,
    min_val_points: int,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    train_df = ref_df[ref_df["track_id"].isin(train_tracks)].copy()
    val_df = ref_df[ref_df["track_id"].isin(val_tracks)].copy()
    X_train = train_df[feature_columns].to_numpy(dtype=np.float32)
    y_train = train_df["depth_m"].to_numpy(dtype=np.float32)
    X_val = val_df[feature_columns].to_numpy(dtype=np.float32)
    y_val = val_df["depth_m"].to_numpy(dtype=np.float32)

    if len(X_train) < min_train_points or len(X_val) < min_val_points:
        raise ValueError("Reference scene does not have enough train/val ATL24 points")
    if len(X_val) < max(10, min_val_points):
        log.warning(
            "Reference scene has only %d validation ATL24 points after track holdout; "
            "treat resulting metrics as a smoke test, not a stable benchmark.",
            len(X_val),
        )

    results: dict[str, Any] = {}
    best_name = ""
    best_type = ""
    best_params: dict[str, Any] = {}
    best_rmse = math.inf

    for name, (model_type, params) in make_model_candidates(seed).items():
        model = instantiate_model(model_type, params)
        model.fit(X_train, y_train)
        pred = model.predict(X_val)
        metrics = regression_metrics(y_val, pred)
        results[name] = {"model_type": model_type, "params": params, "metrics": metrics}
        if metrics["rmse"] < best_rmse:
            best_name = name
            best_type = model_type
            best_params = params
            best_rmse = metrics["rmse"]

    for model_type in ["rf", "xgb"]:
        if model_type == "xgb" and xgb is None:
            continue
        tuned_name = f"{model_type}_{'optuna' if optuna is not None else 'random_search'}"
        if optuna is not None:
            params, metrics = optuna_search(
                model_type,
                X_train,
                y_train,
                X_val,
                y_val,
                seed=seed,
                n_trials=tuning_trials,
            )
        else:
            params, metrics = fallback_random_search(
                model_type,
                X_train,
                y_train,
                X_val,
                y_val,
                seed=seed,
                n_trials=tuning_trials,
            )
        results[tuned_name] = {"model_type": model_type, "params": params, "metrics": metrics}
        if metrics["rmse"] < best_rmse:
            best_name = tuned_name
            best_type = model_type
            best_params = params
            best_rmse = metrics["rmse"]

    return best_name, best_type, best_params, results


def predict_scene_map(scene: SceneGrid, model, feature_columns: list[str]) -> np.ndarray:
    H, W = scene.water_mask.shape
    pred = np.full((H, W), np.nan, dtype=np.float32)
    if not scene.water_mask.any():
        return pred

    values = {
        "blue": scene.features["blue"],
        "green": scene.features["green"],
        "red": scene.features["red"],
        "rededge1": scene.features["rededge1"],
        "rededge2": scene.features["rededge2"],
        "rededge3": scene.features["rededge3"],
        "nir": scene.features["nir"],
    }

    feat_arrays = {
        "b2_b3_ratio": values["blue"] / np.clip(values["green"], EPS, None),
        "b2_b4_ratio": values["blue"] / np.clip(values["red"], EPS, None),
        "b3_b8_ratio": values["green"] / np.clip(values["nir"], EPS, None),
        "b4_b8_ratio": values["red"] / np.clip(values["nir"], EPS, None),
        "ndwi": (values["green"] - values["nir"]) / (values["green"] + values["nir"] + EPS),
        "b5": values["rededge1"],
        "b6": values["rededge2"],
        "b7": values["rededge3"],
        "blue": values["blue"],
        "green": values["green"],
        "red": values["red"],
        "nir": values["nir"],
        "shore_dist_m": scene.features["shore_dist_m"],
        "row_norm": scene.features["row_norm"],
        "col_norm": scene.features["col_norm"],
    }
    X = np.stack([feat_arrays[name][scene.water_mask] for name in feature_columns], axis=1).astype(np.float32)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    pred_values = model.predict(X).astype(np.float32)
    pred[scene.water_mask] = pred_values
    return pred


def sample_prediction_grid(scene: SceneGrid, grid: np.ndarray, points: pd.DataFrame) -> np.ndarray:
    import rasterio
    from pyproj import Transformer

    if scene.crs and str(scene.crs).upper() != "EPSG:4326":
        transformer = Transformer.from_crs("EPSG:4326", scene.crs, always_xy=True)
        xs, ys = transformer.transform(points["lon"].to_numpy(), points["lat"].to_numpy())
        rows, cols = rasterio.transform.rowcol(scene.transform, xs, ys)
    else:
        rows, cols = rasterio.transform.rowcol(scene.transform, points["lon"].to_numpy(), points["lat"].to_numpy())

    pred = np.full(len(points), np.nan, dtype=np.float32)
    for i, (row_i, col_i) in enumerate(zip(rows, cols)):
        if 0 <= row_i < grid.shape[0] and 0 <= col_i < grid.shape[1]:
            pred[i] = grid[row_i, col_i]
    return pred


def write_geotiff(path: Path, grid: np.ndarray, profile: dict[str, Any]) -> None:
    import rasterio

    out_profile = profile.copy()
    out_profile.update({"compress": "deflate", "dtype": "float32", "count": 1, "nodata": np.nan})
    with rasterio.open(path, "w", **out_profile) as ds:
        ds.write(grid.astype(np.float32), 1)


def build_synthetic_scene_tables(seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    n_tracks = 6
    n_per_track = 60
    atl_rows = []
    scene_rows = []

    for track in range(n_tracks):
        base_depth = 1.5 + track * 0.7
        for i in range(n_per_track):
            lon = -70.0 + 0.01 * rng.random()
            lat = 42.0 + 0.01 * rng.random()
            depth = base_depth + 5.0 * rng.random()
            atl_rows.append(
                {
                    "lon": lon,
                    "lat": lat,
                    "depth_m": depth,
                    "confidence": 0.9,
                    "track_id": f"rgt{track}_cyc1_gt2r_reg1",
                    "time": pd.Timestamp("2023-06-15"),
                }
            )
    atl_df = pd.DataFrame(atl_rows)

    # Synthetic paired per-scene features, bypassing raster I/O.
    for scene_idx, scene_date in enumerate(["2023-06-15", "2023-07-10", "2023-08-21"]):
        for _, row in atl_df.iterrows():
            green = 0.04 + 0.004 * rng.random()
            red = 0.025 + 0.004 * rng.random()
            nir = 0.006 + 0.002 * rng.random()
            blue = max(0.005, green * (1.6 - 0.05 * row["depth_m"]) + 0.001 * rng.normal())
            re1 = 0.02 + 0.001 * rng.random()
            re2 = 0.018 + 0.001 * rng.random()
            re3 = 0.016 + 0.001 * rng.random()
            feat = compute_payandeh_features(
                {
                    "blue": blue,
                    "green": green,
                    "red": red,
                    "rededge1": re1,
                    "rededge2": re2,
                    "rededge3": re3,
                    "nir": nir,
                }
            )
            scene_rows.append(
                {
                    "point_id": len(scene_rows),
                    "scene_id": f"synthetic_{scene_idx}",
                    "scene_date": scene_date,
                    "track_id": row["track_id"],
                    "depth_m": row["depth_m"],
                    "confidence": row["confidence"],
                    "blue": blue,
                    "green": green,
                    "red": red,
                    "nir": nir,
                    "shore_dist_m": float(10.0 + 50.0 * rng.random()),
                    "row_norm": float(rng.random()),
                    "col_norm": float(rng.random()),
                    **feat,
                }
            )
    return atl_df, pd.DataFrame(scene_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replicate Payandeh et al. (2026)-style ATL24 + S2 bathymetry")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                        help="AOI bounding box in WGS84")
    parser.add_argument("--atl24", type=Path, default=None,
                        help="Existing ATL24 parquet/csv with lon/lat/depth_m/track columns or equivalent")
    parser.add_argument("--query-atl24", action="store_true",
                        help="Query ATL24 live from SlideRule instead of loading a local parquet")
    parser.add_argument("--scene-manifest", type=Path, default=None,
                        help="CSV/parquet with local scene band paths (useful for ACOLITE outputs)")
    parser.add_argument("--start", type=str, default="2023-01-01", help="Start date for ATL24/STAC discovery")
    parser.add_argument("--end", type=str, default="2025-12-31", help="End date for ATL24/STAC discovery")
    parser.add_argument("--max-scenes", type=int, default=16, help="Maximum number of scenes to use")
    parser.add_argument("--max-cloud", type=float, default=20.0, help="Maximum scene-level cloud cover")
    parser.add_argument("--feature-set", type=str, choices=sorted(FEATURE_SETS), default="paper",
                        help="Feature set to use: strict paper features or an extended spectral+spatial set")
    parser.add_argument("--month-min", type=int, default=None, help="Optional minimum month filter")
    parser.add_argument("--month-max", type=int, default=None, help="Optional maximum month filter")
    parser.add_argument("--val-fraction", type=float, default=0.2, help="Fraction of ATL24 tracks held out")
    parser.add_argument("--max-depth", type=float, default=30.0, help="Maximum usable depth in metres")
    parser.add_argument("--min-scene-train-points", type=int, default=20,
                        help="Minimum paired ATL24 points required in a scene training split")
    parser.add_argument("--min-scene-val-points", type=int, default=8,
                        help="Minimum paired ATL24 points required in a scene validation split")
    parser.add_argument("--min-scene-val-tracks", type=int, default=2,
                        help="Preferred minimum number of validation tracks per scene when available")
    parser.add_argument("--min-scene-val-depth-span", type=float, default=1.0,
                        help="Minimum validation depth span for a scene R2 to be considered stable")
    parser.add_argument("--confidence-threshold", type=float, default=0.8,
                        help="Minimum ATL24 confidence when querying SlideRule")
    parser.add_argument("--track-min-points", type=int, default=3,
                        help="Minimum ATL24 points required to keep a track after filtering")
    parser.add_argument("--track-edge-trim-frac", type=float, default=0.05,
                        help="Fraction of each track to trim from both ends before outlier filtering")
    parser.add_argument("--track-outlier-window", type=int, default=7,
                        help="Rolling window size for along-track depth outlier filtering")
    parser.add_argument("--track-outlier-mad-mult", type=float, default=3.5,
                        help="MAD multiplier for along-track ATL24 outlier filtering")
    parser.add_argument("--track-outlier-abs-tol", type=float, default=1.5,
                        help="Absolute minimum tolerance in metres for along-track ATL24 outlier filtering")
    parser.add_argument("--tuning-trials", type=int, default=25, help="Optuna/random-search trials")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--ndwi-threshold", type=float, default=0.0, help="Water mask NDWI threshold")
    parser.add_argument("--scene-date-tolerance-days", type=int, default=None,
                        help="Optional max |ATL24 date - scene date| in days when pairing points to a scene")
    parser.add_argument("--skip-deglint", action="store_true", help="Skip Hedley/SWIR deglinting")
    parser.add_argument("--manifest-scale", type=float, default=1.0,
                        help="Default scale divisor for scene-manifest reflectances")
    parser.add_argument("--skip-map-prediction", action="store_true",
                        help="Only build paired tables and scene metrics; do not write predicted rasters")
    parser.add_argument("--gaussian-sigma", type=float, default=1.0,
                        help="Gaussian sigma (pixels) for the paper-style smoothed mean map")
    parser.add_argument("--synthetic-demo", action="store_true",
                        help="Run a synthetic self-test without ATL24/STAC/raster dependencies")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    feature_columns = FEATURE_SETS[args.feature_set]

    if args.synthetic_demo:
        atl24_points, paired = build_synthetic_scene_tables(args.seed)
        safe_write_table(atl24_points, args.output / "atl24_points.parquet")
        safe_write_table(paired, args.output / "scene_pairs.parquet")
        train_tracks, val_tracks = split_tracks(atl24_points, args.val_fraction, args.seed)
        ref_scene = sorted(paired["scene_id"].unique())[0]
        ref_df = paired[paired["scene_id"] == ref_scene].copy()
        best_name, best_type, best_params, results = compare_models_on_reference_scene(
            ref_df,
            train_tracks,
            val_tracks,
            feature_columns,
            args.seed,
            args.tuning_trials,
            args.min_scene_train_points,
            args.min_scene_val_points,
        )
        summary = {
            "mode": "synthetic_demo",
            "selected_model": best_name,
            "selected_model_type": best_type,
            "selected_params": best_params,
            "model_comparison": results,
            "train_tracks": train_tracks,
            "val_tracks": val_tracks,
        }
        (args.output / "metrics.json").write_text(json.dumps(serialize_float_dict(summary), indent=2))
        log.info("Synthetic demo complete -> %s", args.output / "metrics.json")
        return

    if args.bbox is None:
        raise ValueError("--bbox is required unless --synthetic-demo is used")
    bbox = tuple(float(v) for v in args.bbox)

    if args.atl24 is not None and args.query_atl24:
        raise ValueError("Use either --atl24 or --query-atl24, not both")
    if args.atl24 is None and not args.query_atl24:
        raise ValueError("Provide --atl24 or enable --query-atl24")

    if args.atl24 is not None:
        atl24_points = load_atl24_points(args.atl24, max_depth_m=args.max_depth)
    else:
        atl24_points = query_atl24_sliderule(
            bbox=bbox,
            start=args.start,
            end=args.end,
            max_depth_m=args.max_depth,
            confidence_threshold=args.confidence_threshold,
        )
    atl24_points = filter_atl24_tracks(
        atl24_points,
        min_track_points=args.track_min_points,
        trim_edge_fraction=args.track_edge_trim_frac,
        outlier_window=args.track_outlier_window,
        outlier_mad_mult=args.track_outlier_mad_mult,
        outlier_abs_tol=args.track_outlier_abs_tol,
    )
    atl24_points = atl24_points.reset_index(drop=True)
    atl24_points["point_id"] = np.arange(len(atl24_points), dtype=np.int64)
    safe_write_table(atl24_points, args.output / "atl24_points.parquet")
    log.info("ATL24 points: %d rows across %d tracks", len(atl24_points), atl24_points["track_id"].nunique())

    if args.scene_manifest is not None:
        scenes = load_scene_manifest(args.scene_manifest, default_scale=args.manifest_scale)
    else:
        scenes = discover_stac_scenes(
            bbox=bbox,
            start=args.start,
            end=args.end,
            max_cloud=args.max_cloud,
            max_scenes=args.max_scenes,
            month_min=args.month_min,
            month_max=args.month_max,
        )
    scenes = prioritize_scenes(scenes, atl24_points)
    safe_write_table(scenes, args.output / "scene_manifest_resolved.parquet")
    log.info("Using %d Sentinel-2 scenes", len(scenes))

    train_tracks, val_tracks = split_tracks(atl24_points, args.val_fraction, args.seed)
    split_info = {"train_tracks": train_tracks, "val_tracks": val_tracks}
    (args.output / "track_split.json").write_text(json.dumps(split_info, indent=2))
    log.info("Track split: %d train / %d val tracks", len(train_tracks), len(val_tracks))

    scene_tables: list[pd.DataFrame] = []
    scene_grids: list[SceneGrid] = []

    for _, row in scenes.iterrows():
        log.info("Reading scene %s (%s)", row["scene_id"], row["scene_date"])
        scene = build_scene_stack(
            row=row,
            bbox=bbox,
            apply_deglint=not args.skip_deglint,
            ndwi_threshold=args.ndwi_threshold,
        )
        paired = sample_scene_at_points(
            scene,
            atl24_points,
            max_scene_date_delta_days=args.scene_date_tolerance_days,
        )
        if len(paired) < 20:
            log.warning("Skipping scene %s: only %d usable ATL24 matches", scene.scene_id, len(paired))
            continue
        scene_tables.append(paired)
        scene_grids.append(scene)
        safe_write_table(paired, args.output / f"{scene.scene_id}_pairs.parquet")
        log.info("Scene %s: %d usable ATL24 pairings", scene.scene_id, len(paired))

    if not scene_tables:
        raise RuntimeError("No scenes produced enough paired ATL24 samples")

    all_pairs = pd.concat(scene_tables, ignore_index=True)
    safe_write_table(all_pairs, args.output / "scene_pairs_all.parquet")
    scene_splits: dict[str, SceneTrackSplit] = {}
    for scene in scene_grids:
        scene_df = all_pairs[all_pairs["scene_id"] == scene.scene_id].copy()
        scene_split = choose_scene_track_split(
            scene_id=scene.scene_id,
            scene_df=scene_df,
            global_train_tracks=train_tracks,
            global_val_tracks=val_tracks,
            val_fraction=args.val_fraction,
            seed=args.seed,
            min_train_points=args.min_scene_train_points,
            min_val_points=args.min_scene_val_points,
            min_val_tracks=args.min_scene_val_tracks,
            min_val_depth_span=args.min_scene_val_depth_span,
        )
        scene_splits[scene.scene_id] = scene_split
        log.info(
            "Scene %s split=%s train=%d(%d tracks) val=%d(%d tracks) val_span=%.2fm stable=%s",
            scene.scene_id,
            scene_split.strategy,
            scene_split.n_train,
            scene_split.n_train_tracks,
            scene_split.n_val,
            scene_split.n_val_tracks,
            scene_split.val_depth_span,
            scene_split.r2_stable,
        )

    reference_scene_id = choose_reference_scene_id(scene_splits)
    reference_df = all_pairs[all_pairs["scene_id"] == reference_scene_id].copy()
    reference_split = scene_splits[reference_scene_id]
    best_name, best_type, best_params, model_results = compare_models_on_reference_scene(
        reference_df,
        train_tracks=reference_split.train_tracks,
        val_tracks=reference_split.val_tracks,
        feature_columns=feature_columns,
        seed=args.seed,
        tuning_trials=args.tuning_trials,
        min_train_points=args.min_scene_train_points,
        min_val_points=args.min_scene_val_points,
    )
    log.info("Selected model on reference scene %s: %s", reference_scene_id, best_name)

    scene_metrics: dict[str, Any] = {}
    predicted_maps: list[np.ndarray] = []
    val_point_predictions: list[pd.DataFrame] = []
    reference_scene_obj = next(scene for scene in scene_grids if scene.scene_id == reference_scene_id)

    for scene in scene_grids:
        scene_df = all_pairs[all_pairs["scene_id"] == scene.scene_id].copy()
        scene_split = scene_splits[scene.scene_id]
        train_df = scene_df[scene_df["track_id"].isin(scene_split.train_tracks)].copy()
        val_df = scene_df[scene_df["track_id"].isin(scene_split.val_tracks)].copy()
        if not scene_split.adequate:
            log.warning(
                "Skipping scene metric for %s due to insufficient split support: %s",
                scene.scene_id,
                ", ".join(scene_split.notes) if scene_split.notes else "unknown",
            )
            continue
        model = instantiate_model(best_type, best_params)
        X_train = train_df[feature_columns].to_numpy(dtype=np.float32)
        y_train = train_df["depth_m"].to_numpy(dtype=np.float32)
        X_val = val_df[feature_columns].to_numpy(dtype=np.float32)
        y_val = val_df["depth_m"].to_numpy(dtype=np.float32)
        model.fit(X_train, y_train)
        pred_val = model.predict(X_val)
        metrics = regression_metrics(y_val, pred_val)
        depth_bins = compute_depth_bins(y_val, pred_val)
        scene_metrics[scene.scene_id] = {
            "scene_date": scene.scene_date,
            "n_train": int(len(train_df)),
            "n_val": int(len(val_df)),
            "split": scene_split.as_dict(),
            "metrics": metrics,
            "depth_bins": depth_bins,
        }
        log.info(
            "Scene %s val: RMSE=%.3f m R2=%.3f",
            scene.scene_id,
            metrics["rmse"],
            metrics["r2"],
        )
        if not scene_split.r2_stable:
            log.warning(
                "Scene %s validation depth span is only %.2fm; treat R2 as unstable",
                scene.scene_id,
                scene_split.val_depth_span,
            )

        val_pred_df = val_df[["point_id", "scene_id", "scene_date", "track_id", "depth_m"]].copy()
        val_pred_df["pred_depth_m"] = pred_val.astype(np.float32)
        val_pred_df["split_strategy"] = scene_split.strategy
        val_point_predictions.append(val_pred_df)

        if not args.skip_map_prediction:
            pred_map = predict_scene_map(scene, model, feature_columns=feature_columns)
            predicted_maps.append(pred_map)
            write_geotiff(args.output / f"{scene.scene_id}_depth_pred.tif", pred_map, scene.profile)

    aggregated_point_metrics = None
    aggregated_point_depth_bins = None
    aggregated_val_points = None
    if val_point_predictions:
        val_pred_all = pd.concat(val_point_predictions, ignore_index=True)
        safe_write_table(val_pred_all, args.output / "scene_val_point_predictions.parquet")
        grouped = (
            val_pred_all.groupby("point_id", as_index=False)
            .agg(
                depth_m=("depth_m", "first"),
                track_id=("track_id", "first"),
                pred_mean=("pred_depth_m", "mean"),
                pred_median=("pred_depth_m", "median"),
                n_scene_predictions=("pred_depth_m", "size"),
            )
        )
        safe_write_table(grouped, args.output / "scene_val_point_aggregated.parquet")
        aggregated_val_points = atl24_points[atl24_points["point_id"].isin(grouped["point_id"])].copy()
        aggregated_point_metrics = {
            "mean_across_scenes": regression_metrics(grouped["depth_m"], grouped["pred_mean"]),
            "median_across_scenes": regression_metrics(grouped["depth_m"], grouped["pred_median"]),
            "support": {
                "n_points": int(len(grouped)),
                "mean_scene_predictions": float(grouped["n_scene_predictions"].mean()),
                "median_scene_predictions": float(grouped["n_scene_predictions"].median()),
                "max_scene_predictions": int(grouped["n_scene_predictions"].max()),
            },
        }
        aggregated_point_depth_bins = {
            "mean_across_scenes": compute_depth_bins(grouped["depth_m"], grouped["pred_mean"]),
            "median_across_scenes": compute_depth_bins(grouped["depth_m"], grouped["pred_median"]),
        }

    averaged_metrics = None
    smoothed_metrics = None
    averaged_depth_bins = None
    smoothed_depth_bins = None
    if predicted_maps and not args.skip_map_prediction:
        mean_map = np.nanmean(np.stack(predicted_maps, axis=0), axis=0).astype(np.float32)
        write_geotiff(args.output / "mean_depth_map.tif", mean_map, reference_scene_obj.profile)

        val_points = aggregated_val_points if aggregated_val_points is not None else atl24_points[
            atl24_points["track_id"].isin(val_tracks)
        ].copy()
        mean_pred = sample_prediction_grid(reference_scene_obj, mean_map, val_points)
        valid = np.isfinite(mean_pred)
        if valid.any():
            averaged_metrics = regression_metrics(val_points.loc[valid, "depth_m"], mean_pred[valid])
            averaged_depth_bins = compute_depth_bins(val_points.loc[valid, "depth_m"], mean_pred[valid])

        if gaussian_filter is not None:
            filled = np.where(np.isfinite(mean_map), mean_map, 0.0)
            weights = np.where(np.isfinite(mean_map), 1.0, 0.0)
            smooth_num = gaussian_filter(filled, sigma=args.gaussian_sigma)
            smooth_den = gaussian_filter(weights, sigma=args.gaussian_sigma)
            smooth_map = np.where(smooth_den > EPS, smooth_num / np.clip(smooth_den, EPS, None), np.nan).astype(np.float32)
            write_geotiff(args.output / "mean_depth_map_gaussian.tif", smooth_map, reference_scene_obj.profile)
            smooth_pred = sample_prediction_grid(reference_scene_obj, smooth_map, val_points)
            valid = np.isfinite(smooth_pred)
            if valid.any():
                smoothed_metrics = regression_metrics(val_points.loc[valid, "depth_m"], smooth_pred[valid])
                smoothed_depth_bins = compute_depth_bins(val_points.loc[valid, "depth_m"], smooth_pred[valid])
        else:
            log.warning("scipy not available; skipping Gaussian-smoothed mean map")

    summary = {
        "protocol": "Payandeh-2026-style ATL24 + Sentinel-2 replication",
        "reference_scene_id": reference_scene_id,
        "reference_scene_split": reference_split.as_dict(),
        "feature_set": args.feature_set,
        "feature_columns": feature_columns,
        "selected_model": best_name,
        "selected_model_type": best_type,
        "selected_params": best_params,
        "model_comparison_on_reference": model_results,
        "n_atl24_points": int(len(atl24_points)),
        "n_tracks": int(atl24_points["track_id"].nunique()),
        "n_scenes": int(len(scene_grids)),
        "scene_metrics": scene_metrics,
        "scene_point_aggregated_metrics": aggregated_point_metrics,
        "scene_point_aggregated_depth_bins": aggregated_point_depth_bins,
        "mean_map_metrics": averaged_metrics,
        "mean_map_depth_bins": averaged_depth_bins,
        "mean_map_gaussian_metrics": smoothed_metrics,
        "mean_map_gaussian_depth_bins": smoothed_depth_bins,
        "global_train_tracks": train_tracks,
        "global_val_tracks": val_tracks,
        "scene_splits": {scene_id: split.as_dict() for scene_id, split in scene_splits.items()},
    }
    (args.output / "metrics.json").write_text(json.dumps(serialize_float_dict(summary), indent=2))
    log.info("Replication summary written -> %s", args.output / "metrics.json")


if __name__ == "__main__":
    main()
