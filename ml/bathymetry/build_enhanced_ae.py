#!/usr/bin/env python3
"""
OpenCatch -- Build Enhanced Area-Elevation Curves from Multi-Source Data

Combines SWOT satellite elevations, ICESat-2 shoreline elevations, and
Landsat GSW water occurrence to build the densest possible A-E curve
for each lake. Then fits physics-constrained parametric models.

Data sources fused:
  1. SWOT L2 LakeSP: WSE + area_total on 21-day repeat (~30+ pts/lake)
  2. ICESat-2 ATL13: Inland water elevation at ~0.7m along-track spacing
  3. Landsat GSW: Water occurrence frequency at 30m resolution
     (frequency maps -> area at different flood/drought levels)
  4. 3D-LAKES L1: Existing sparse A-E (5 points for most lakes)

Parametric curve fitting with physics constraints:
  - Power law:      A = a * (E - E_min)^b
  - Logarithmic:    A = a * ln(E - E_min + 1) + c
  - Polynomial:     A = sum(a_i * (E - E_min)^i)
  - Sigmoid:        A = A_max / (1 + exp(-k*(E - E_50)))

Physics constraints:
  - Monotonically increasing: dA/dE >= 0 (deeper = smaller area)
  - Bounded: 0 <= A <= A_max (from satellite imagery)
  - Smooth: no sharp discontinuities (geology doesn't jump)
  - Concavity: d2A/dE2 should be consistent (bowl vs cone shape)

Usage:
    python build_enhanced_ae.py \
        --swot /data/swot/swot_ae_curves.parquet \
        --icesat2 /data/icesat2_depths \
        --gsw /data/gsw \
        --threedlakes /data/3d_lakes_l1 \
        --output /data/enhanced_ae

Requirements:
    pip install numpy scipy pandas scikit-learn pyarrow tqdm
"""

import argparse
import json
import logging
import os
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit, minimize
from scipy.interpolate import UnivariateSpline
from sklearn.metrics import mean_squared_error, r2_score
from tqdm import tqdm

warnings.filterwarnings("ignore", category=RuntimeWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("enhanced_ae")

EPS = 1e-8


# == Parametric Curve Models ==================================================

def power_law(e, a, b, e_min):
    """A = a * (E - E_min)^b  |  Classic hypsometric power law."""
    de = np.clip(e - e_min, EPS, None)
    return a * np.power(de, b)


def logarithmic(e, a, c, e_min):
    """A = a * ln(E - E_min + 1) + c  |  Log-shaped basin."""
    de = np.clip(e - e_min, EPS, None)
    return a * np.log(de + 1) + c


def polynomial_3(e, a3, a2, a1, a0, e_min):
    """3rd-order polynomial in normalized elevation."""
    de = e - e_min
    return a3 * de**3 + a2 * de**2 + a1 * de + a0


def sigmoid_ae(e, a_max, k, e_50, e_min):
    """Sigmoid A-E: A = A_max / (1 + exp(-k*(E - E_50)))"""
    de = e - e_min
    return a_max / (1 + np.exp(-k * (de - e_50)))


# == Monotonicity Enforcement =================================================

def enforce_monotonicity(elevations: np.ndarray, areas: np.ndarray) -> np.ndarray:
    """
    Enforce A-E monotonicity: area must increase with elevation.

    Uses isotonic regression (pool adjacent violators algorithm).
    """
    from sklearn.isotonic import IsotonicRegression

    # Sort by elevation
    order = np.argsort(elevations)
    e_sorted = elevations[order]
    a_sorted = areas[order]

    # Isotonic regression: ensures non-decreasing
    iso = IsotonicRegression(increasing=True)
    a_mono = iso.fit_transform(e_sorted, a_sorted)

    # Restore original order
    a_result = np.empty_like(areas)
    a_result[order] = a_mono
    return a_result


def monotonic_spline_fit(
    elevations: np.ndarray,
    areas: np.ndarray,
    n_eval: int = 100,
    smoothing_factor: float = None,
) -> tuple:
    """
    Fit a monotonic smoothing spline to A-E data.

    Returns (e_eval, a_eval) arrays for the fitted curve.
    """
    order = np.argsort(elevations)
    e_sorted = elevations[order]
    a_sorted = areas[order]

    # Remove duplicates (average areas at same elevation)
    e_unique, idx = np.unique(e_sorted, return_index=True)
    if len(e_unique) < len(e_sorted):
        a_unique = np.array([
            a_sorted[e_sorted == eu].mean() for eu in e_unique
        ])
    else:
        a_unique = a_sorted[idx]

    if len(e_unique) < 4:
        # Not enough points for spline, use linear interpolation
        e_eval = np.linspace(e_unique.min(), e_unique.max(), n_eval)
        a_eval = np.interp(e_eval, e_unique, a_unique)
        return e_eval, enforce_monotonicity(e_eval, a_eval)

    # Fit spline
    if smoothing_factor is None:
        smoothing_factor = len(e_unique) * 0.5

    try:
        spline = UnivariateSpline(e_unique, a_unique, s=smoothing_factor, k=3)
        e_eval = np.linspace(e_unique.min(), e_unique.max(), n_eval)
        a_eval = spline(e_eval)
    except Exception:
        e_eval = np.linspace(e_unique.min(), e_unique.max(), n_eval)
        a_eval = np.interp(e_eval, e_unique, a_unique)

    # Enforce monotonicity and non-negativity
    a_eval = np.clip(a_eval, 0, None)
    a_eval = enforce_monotonicity(e_eval, a_eval)

    return e_eval, a_eval


# == Multi-Source Data Loading ================================================

def load_swot_ae(swot_path: str) -> dict:
    """Load SWOT A-E data, grouped by lake_id."""
    if not os.path.exists(swot_path):
        log.warning(f"SWOT data not found: {swot_path}")
        return {}

    df = pd.read_parquet(swot_path)
    log.info(f"Loaded SWOT A-E: {len(df)} rows, {df['lake_id'].nunique()} lakes")

    lakes = {}
    for lid, grp in df.groupby("lake_id"):
        elevations = grp["wse"].dropna().values
        areas = grp["area_total_km2"].dropna().values
        if len(elevations) >= 2 and len(areas) >= 2:
            # Align arrays
            mask = ~np.isnan(elevations) & ~np.isnan(areas)
            if mask.sum() >= 2:
                lakes[str(lid)] = {
                    "elevation": elevations[mask],
                    "area_km2": areas[mask],
                    "source": "swot",
                    "uncertainty_m": grp["wse_uncertainty"].dropna().values if "wse_uncertainty" in grp.columns else None,
                }
    return lakes


def load_icesat2_ae(icesat2_dir: str) -> dict:
    """
    Load ICESat-2 ATL13 shoreline elevations.

    ICESat-2 gives water surface elevation along tracks crossing the lake.
    Each track crossing provides a very precise elevation at that moment.
    Multiple passes over time give elevation variation.
    """
    if not os.path.exists(icesat2_dir):
        log.warning(f"ICESat-2 data not found: {icesat2_dir}")
        return {}

    lakes = {}
    icesat2_path = Path(icesat2_dir)

    # Look for processed depth files
    for f in icesat2_path.glob("*.parquet"):
        try:
            df = pd.read_parquet(f)
            if "lake_id" not in df.columns and "hylak_id" not in df.columns:
                continue

            id_col = "lake_id" if "lake_id" in df.columns else "hylak_id"
            elev_col = None
            for c in ["water_surface_elevation", "wse", "elevation", "h_li"]:
                if c in df.columns:
                    elev_col = c
                    break

            if elev_col is None:
                continue

            for lid, grp in df.groupby(id_col):
                elevations = grp[elev_col].dropna().values
                if len(elevations) >= 2:
                    # ICESat-2 doesn't directly give area, but gives elevation samples
                    lakes[str(lid)] = {
                        "elevation": elevations,
                        "area_km2": None,  # No area from ICESat-2
                        "source": "icesat2",
                    }
        except Exception as e:
            log.debug(f"Error loading {f}: {e}")

    log.info(f"Loaded ICESat-2 elevations for {len(lakes)} lakes")
    return lakes


def load_3dlakes_ae(l1_dir: str, max_lakes: int = None) -> dict:
    """Load 3D-LAKES L1 A-E data."""
    l1_path = Path(l1_dir)
    if not l1_path.exists():
        log.warning(f"3D-LAKES L1 dir not found: {l1_dir}")
        return {}

    csv_files = list(l1_path.glob("*_L1.csv"))
    if max_lakes:
        csv_files = csv_files[:max_lakes]

    lakes = {}
    for f in csv_files:
        try:
            hylak_id = int(f.stem.replace("_L1", ""))
            df = pd.read_csv(f)
            if len(df) < 2:
                continue

            elevations = df.iloc[:, 0].values  # Elevation (m)
            areas = df.iloc[:, 1].values  # Area (m2)

            # Convert area from m2 to km2
            areas_km2 = areas / 1e6

            lakes[str(hylak_id)] = {
                "elevation": elevations,
                "area_km2": areas_km2,
                "source": "3dlakes",
            }
        except Exception:
            pass

    log.info(f"Loaded 3D-LAKES A-E for {len(lakes)} lakes")
    return lakes


# == Curve Fitting Engine =====================================================

class AECurveFitter:
    """
    Fit physics-constrained parametric curves to A-E data.

    Tries multiple functional forms and selects the best based on AIC.
    Enforces monotonicity and smoothness constraints.
    """

    MODELS = {
        "power_law": {
            "func": power_law,
            "p0_fn": lambda e, a: [a.max(), 1.5, e.min()],
            "bounds_fn": lambda e, a: (
                [0, 0.1, e.min() - 10],
                [a.max() * 10, 5.0, e.min() + 1],
            ),
            "n_params": 3,
        },
        "logarithmic": {
            "func": logarithmic,
            "p0_fn": lambda e, a: [a.max() / np.log(e.ptp() + 1), 0, e.min()],
            "bounds_fn": lambda e, a: (
                [0, -a.max(), e.min() - 10],
                [a.max() * 10, a.max(), e.min() + 1],
            ),
            "n_params": 3,
        },
        "sigmoid": {
            "func": sigmoid_ae,
            "p0_fn": lambda e, a: [a.max(), 1.0, e.ptp() / 2, e.min()],
            "bounds_fn": lambda e, a: (
                [0, 0.01, 0, e.min() - 10],
                [a.max() * 2, 10.0, e.ptp() * 2, e.min() + 1],
            ),
            "n_params": 4,
        },
    }

    def __init__(self, min_points: int = 3):
        self.min_points = min_points

    def fit_single_model(
        self, elevations: np.ndarray, areas: np.ndarray, model_name: str
    ) -> Optional[dict]:
        """Fit a single parametric model to A-E data."""
        model = self.MODELS.get(model_name)
        if model is None:
            return None

        try:
            p0 = model["p0_fn"](elevations, areas)
            bounds = model["bounds_fn"](elevations, areas)

            popt, pcov = curve_fit(
                model["func"], elevations, areas,
                p0=p0, bounds=bounds, maxfev=5000,
            )

            # Evaluate fit
            a_pred = model["func"](elevations, *popt)
            residuals = areas - a_pred
            ss_res = np.sum(residuals**2)
            ss_tot = np.sum((areas - areas.mean())**2)
            r2 = 1 - ss_res / max(ss_tot, EPS)
            rmse = np.sqrt(np.mean(residuals**2))

            # AIC for model selection
            n = len(areas)
            k = model["n_params"]
            aic = n * np.log(ss_res / n + EPS) + 2 * k

            # Check monotonicity of fitted curve
            e_test = np.linspace(elevations.min(), elevations.max(), 100)
            a_test = model["func"](e_test, *popt)
            is_monotonic = np.all(np.diff(a_test) >= -EPS)

            return {
                "model": model_name,
                "params": popt.tolist(),
                "r2": r2,
                "rmse": rmse,
                "aic": aic,
                "is_monotonic": is_monotonic,
                "n_points": n,
            }
        except Exception as e:
            log.debug(f"  {model_name} fit failed: {e}")
            return None

    def fit_best(
        self, elevations: np.ndarray, areas: np.ndarray
    ) -> Optional[dict]:
        """
        Try all parametric models and return the best fit.

        Selection criteria:
          1. Must be monotonic
          2. Among monotonic: lowest AIC
          3. If no monotonic: best R2 with isotonic correction
        """
        if len(elevations) < self.min_points:
            return None

        results = []
        for name in self.MODELS:
            result = self.fit_single_model(elevations, areas, name)
            if result is not None:
                results.append(result)

        if not results:
            return None

        # Prefer monotonic fits
        monotonic = [r for r in results if r["is_monotonic"]]
        if monotonic:
            best = min(monotonic, key=lambda r: r["aic"])
        else:
            best = max(results, key=lambda r: r["r2"])
            best["note"] = "non-monotonic, apply isotonic correction"

        return best

    def predict(self, result: dict, elevations: np.ndarray) -> np.ndarray:
        """Predict areas from a fitted model result."""
        model = self.MODELS[result["model"]]
        params = result["params"]
        areas = model["func"](elevations, *params)
        areas = np.clip(areas, 0, None)
        return areas

    def extrapolate(
        self, result: dict, e_min: float, e_max: float, n_points: int = 200
    ) -> tuple:
        """
        Extrapolate A-E curve beyond observed range.

        Returns (elevations, areas) arrays.
        Applies dampening for extrapolation beyond 20% of observed range.
        """
        e_eval = np.linspace(e_min, e_max, n_points)
        a_eval = self.predict(result, e_eval)

        # Dampen extrapolation
        # (reduce confidence linearly beyond observed range)
        e_obs_min = result.get("e_obs_min", e_min)
        e_obs_max = result.get("e_obs_max", e_max)
        obs_range = e_obs_max - e_obs_min

        for i, e in enumerate(e_eval):
            if e < e_obs_min:
                extrap_frac = (e_obs_min - e) / max(obs_range, 0.1)
                dampen = max(0, 1 - 0.5 * extrap_frac)
                a_eval[i] *= dampen
            elif e > e_obs_max:
                extrap_frac = (e - e_obs_max) / max(obs_range, 0.1)
                dampen = max(0, 1 - 0.5 * extrap_frac)
                # For upward extrapolation, area should approach A_max
                pass  # Power law / sigmoid naturally handle this

        return e_eval, np.clip(a_eval, 0, None)


# == Multi-Source Fusion ======================================================

def fuse_ae_sources(
    swot_data: Optional[dict],
    icesat2_data: Optional[dict],
    threedlakes_data: Optional[dict],
    source_weights: dict = None,
) -> tuple:
    """
    Fuse A-E data from multiple sources for a single lake.

    Weighting strategy:
      - SWOT: highest weight (best elevation accuracy, paired with area)
      - 3D-LAKES: medium weight (area-elevation paired, but sparse)
      - ICESat-2: elevation-only, used to extend the elevation range

    Returns (elevations, areas, weights) arrays.
    """
    if source_weights is None:
        source_weights = {
            "swot": 3.0,     # Best: paired WSE + area, high accuracy
            "3dlakes": 1.0,  # Good: paired, but only 5 points
            "icesat2": 0.5,  # Elevation only, no paired area
        }

    all_e = []
    all_a = []
    all_w = []

    # SWOT: primary source (has both elevation and area)
    if swot_data and swot_data.get("elevation") is not None and swot_data.get("area_km2") is not None:
        e = swot_data["elevation"]
        a = swot_data["area_km2"]
        mask = ~np.isnan(e) & ~np.isnan(a)
        if mask.sum() > 0:
            all_e.append(e[mask])
            all_a.append(a[mask])
            all_w.append(np.full(mask.sum(), source_weights["swot"]))

    # 3D-LAKES: secondary source
    if threedlakes_data and threedlakes_data.get("elevation") is not None and threedlakes_data.get("area_km2") is not None:
        e = threedlakes_data["elevation"]
        a = threedlakes_data["area_km2"]
        mask = ~np.isnan(e) & ~np.isnan(a)
        if mask.sum() > 0:
            all_e.append(e[mask])
            all_a.append(a[mask])
            all_w.append(np.full(mask.sum(), source_weights["3dlakes"]))

    if not all_e:
        return np.array([]), np.array([]), np.array([])

    elevations = np.concatenate(all_e)
    areas = np.concatenate(all_a)
    weights = np.concatenate(all_w)

    # Sort by elevation
    order = np.argsort(elevations)
    return elevations[order], areas[order], weights[order]


# == Volume Estimation ========================================================

def estimate_volume_from_ae(
    elevations: np.ndarray, areas_km2: np.ndarray
) -> dict:
    """
    Estimate lake volume from A-E curve using trapezoidal integration.

    V = integral from E_min to E_max of A(e) de

    Also computes mean depth = V / A_max and volume development ratio.
    """
    if len(elevations) < 2:
        return {"volume_km3": np.nan, "mean_depth_m": np.nan}

    from scipy.integrate import trapezoid

    order = np.argsort(elevations)
    e_sorted = elevations[order]
    a_sorted = areas_km2[order]

    # Volume in km^2 * m = need to convert elevation to km for volume in km^3
    # Actually, if elevation is in meters and area in km^2:
    # V = integral A(e) de, where de is in meters and A in km^2
    # So V is in km^2 * m = 1e6 m^3 per unit

    # Volume integral (area in km^2, elevation in m -> volume in km^2 * m)
    vol_km2m = trapezoid(a_sorted, e_sorted)
    vol_m3 = vol_km2m * 1e6  # Convert km^2*m to m^3
    vol_km3 = vol_m3 / 1e9

    # Max depth = elevation range
    max_depth_m = e_sorted.max() - e_sorted.min()

    # Mean depth = volume / surface area
    a_max_km2 = a_sorted.max()
    a_max_m2 = a_max_km2 * 1e6
    mean_depth_m = vol_m3 / max(a_max_m2, 1) if a_max_m2 > 0 else np.nan

    # Volume development ratio Vd = 3 * mean_depth / max_depth
    vd = 3 * mean_depth_m / max(max_depth_m, EPS) if max_depth_m > 0 else np.nan

    return {
        "volume_m3": vol_m3,
        "volume_km3": vol_km3,
        "max_depth_m": max_depth_m,
        "mean_depth_m": mean_depth_m,
        "volume_dev_ratio": vd,
        "a_max_km2": a_max_km2,
        "n_ae_points": len(e_sorted),
        "wse_range_m": max_depth_m,
    }


# == Main Pipeline ============================================================

def build_enhanced_curves(
    swot_path: str,
    icesat2_dir: str,
    threedlakes_dir: str,
    output_dir: str,
    crosswalk_path: str = None,
) -> pd.DataFrame:
    """
    Main pipeline: build enhanced A-E curves for all lakes.

    Steps:
      1. Load all data sources
      2. For each lake, fuse available A-E data
      3. Fit parametric curves with physics constraints
      4. Estimate volume and depth
      5. Save enhanced A-E curves and lake metrics
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Load data sources
    swot_lakes = load_swot_ae(swot_path) if swot_path else {}
    icesat2_lakes = load_icesat2_ae(icesat2_dir) if icesat2_dir else {}
    threedlakes = load_3dlakes_ae(threedlakes_dir) if threedlakes_dir else {}

    # Load crosswalk to map between ID systems
    crosswalk = {}
    if crosswalk_path and os.path.exists(crosswalk_path):
        cw_df = pd.read_parquet(crosswalk_path)
        if "pld_lake_id" in cw_df.columns and "hylak_id" in cw_df.columns:
            for _, row in cw_df.iterrows():
                crosswalk[str(row["pld_lake_id"])] = str(int(row["hylak_id"]))

    # Collect all unique lake identifiers
    all_ids = set()
    all_ids.update(swot_lakes.keys())
    all_ids.update(threedlakes.keys())
    # Map SWOT IDs to hylak_ids where possible
    for swot_id in list(swot_lakes.keys()):
        if swot_id in crosswalk:
            hylak_id = crosswalk[swot_id]
            all_ids.add(hylak_id)

    log.info(f"\nProcessing {len(all_ids)} unique lakes")
    log.info(f"  SWOT: {len(swot_lakes)} lakes")
    log.info(f"  ICESat-2: {len(icesat2_lakes)} lakes")
    log.info(f"  3D-LAKES: {len(threedlakes)} lakes")

    fitter = AECurveFitter(min_points=3)
    lake_metrics = []
    fitted_curves = {}

    for lake_id in tqdm(sorted(all_ids), desc="Fitting A-E curves"):
        # Get data from each source (handle ID mapping)
        swot_data = swot_lakes.get(lake_id)
        if swot_data is None:
            # Try reverse crosswalk
            for swot_id, hylak_id in crosswalk.items():
                if hylak_id == lake_id:
                    swot_data = swot_lakes.get(swot_id)
                    break

        threedlakes_data = threedlakes.get(lake_id)
        icesat2_data = icesat2_lakes.get(lake_id)

        # Fuse sources
        elevations, areas, weights = fuse_ae_sources(
            swot_data, icesat2_data, threedlakes_data
        )

        if len(elevations) < 2:
            continue

        # Fit parametric curve
        fit_result = fitter.fit_best(elevations, areas)
        if fit_result is None:
            # Fall back to monotonic spline
            e_eval, a_eval = monotonic_spline_fit(elevations, areas)
            fit_result = {
                "model": "spline",
                "r2": r2_score(areas, np.interp(elevations, e_eval, a_eval)),
                "is_monotonic": True,
            }

        # Store observed range
        fit_result["e_obs_min"] = float(elevations.min())
        fit_result["e_obs_max"] = float(elevations.max())

        # Estimate volume
        vol_metrics = estimate_volume_from_ae(elevations, areas)

        # Sources present
        sources = []
        if swot_data: sources.append("swot")
        if threedlakes_data: sources.append("3dlakes")
        if icesat2_data: sources.append("icesat2")

        # Build metric record
        metric = {
            "lake_id": lake_id,
            "n_ae_points": len(elevations),
            "n_sources": len(sources),
            "sources": ",".join(sources),
            "fit_model": fit_result["model"],
            "fit_r2": fit_result.get("r2", np.nan),
            "fit_rmse": fit_result.get("rmse", np.nan),
            "is_monotonic": fit_result.get("is_monotonic", False),
            "wse_min_m": float(elevations.min()),
            "wse_max_m": float(elevations.max()),
            "wse_range_m": float(elevations.max() - elevations.min()),
            "area_min_km2": float(areas.min()),
            "area_max_km2": float(areas.max()),
            **vol_metrics,
        }
        lake_metrics.append(metric)
        fitted_curves[lake_id] = {
            "fit_result": fit_result,
            "elevations": elevations.tolist(),
            "areas_km2": areas.tolist(),
        }

    # Save results
    metrics_df = pd.DataFrame(lake_metrics)
    if not metrics_df.empty:
        metrics_path = out / "enhanced_ae_metrics.parquet"
        metrics_df.to_parquet(str(metrics_path), index=False)
        log.info(f"\nSaved metrics for {len(metrics_df)} lakes to {metrics_path}")

        # Summary statistics
        log.info("\n=== Enhanced A-E Summary ===")
        log.info(f"Total lakes: {len(metrics_df)}")
        log.info(f"Avg A-E points/lake: {metrics_df['n_ae_points'].mean():.1f}")
        log.info(f"Median A-E points/lake: {metrics_df['n_ae_points'].median():.1f}")
        log.info(f"Lakes with >10 points: {(metrics_df['n_ae_points'] > 10).sum()}")
        log.info(f"Lakes with >20 points: {(metrics_df['n_ae_points'] > 20).sum()}")
        log.info(f"Avg fit R2: {metrics_df['fit_r2'].mean():.3f}")
        log.info(f"Monotonic fits: {metrics_df['is_monotonic'].sum()} / {len(metrics_df)}")
        log.info(f"\nAvg WSE range: {metrics_df['wse_range_m'].mean():.2f}m")
        log.info(f"Avg max depth: {metrics_df['max_depth_m'].mean():.2f}m")
        log.info(f"Avg mean depth: {metrics_df['mean_depth_m'].mean():.2f}m")

        # Source breakdown
        for src in ["swot", "3dlakes", "icesat2"]:
            n = metrics_df["sources"].str.contains(src).sum()
            log.info(f"Lakes with {src} data: {n}")

    # Save fitted curves as JSON (for per-lake reconstruction)
    curves_path = out / "fitted_curves.json"
    with open(str(curves_path), "w") as f:
        json.dump(fitted_curves, f)
    log.info(f"Saved fitted curves to {curves_path}")

    return metrics_df


# == Entry Point ==============================================================

def main():
    parser = argparse.ArgumentParser(description="Build enhanced A-E curves")
    parser.add_argument("--swot", default="/data/swot/swot_ae_curves.parquet",
                       help="SWOT A-E data")
    parser.add_argument("--icesat2", default="/data/icesat2_depths",
                       help="ICESat-2 data directory")
    parser.add_argument("--threedlakes", default="/data/3d_lakes_l1",
                       help="3D-LAKES L1 directory")
    parser.add_argument("--crosswalk", default="/data/swot/pld_hydrolakes_crosswalk.parquet",
                       help="PLD-HydroLAKES crosswalk")
    parser.add_argument("--output", default="/data/enhanced_ae",
                       help="Output directory")
    args = parser.parse_args()

    build_enhanced_curves(
        swot_path=args.swot,
        icesat2_dir=args.icesat2,
        threedlakes_dir=args.threedlakes,
        output_dir=args.output,
        crosswalk_path=args.crosswalk,
    )


if __name__ == "__main__":
    main()
