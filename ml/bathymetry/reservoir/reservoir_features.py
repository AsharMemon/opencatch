#!/usr/bin/env python3
"""
OpenCatch -- Reservoir-Specific Feature Engineering

Computes features for reservoir bathymetry modeling that go beyond the
standard morphometric features used for natural lakes. Reservoir-specific
features include dam properties, cross-section geometry, SWOT time series
characteristics, and watershed attributes.

Usage:
    from reservoir.reservoir_features import compute_reservoir_features
    features = compute_reservoir_features(nid_row, cross_section_result, swot_df)
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("reservoir_features")


def compute_nid_features(nid_row: pd.Series) -> dict:
    """Extract features from NID dam catalog entry."""
    f = {}

    # Direct features
    f["dam_height_m"] = nid_row.get("dam_height_m", np.nan)
    f["surface_area_km2"] = nid_row.get("surface_area_km2", np.nan)
    f["max_storage_m3"] = nid_row.get("max_storage_m3", np.nan)
    f["normal_storage_m3"] = nid_row.get("normal_storage_m3", np.nan)
    f["drainage_area_km2"] = nid_row.get("drainage_area_km2", np.nan)
    f["crest_elevation_m"] = nid_row.get("crest_elevation_m", np.nan)
    f["base_elevation_m"] = nid_row.get("base_elevation_m", np.nan)
    f["dam_age_years"] = nid_row.get("dam_age_years", np.nan)
    f["latitude"] = nid_row.get("latitude", np.nan)
    f["longitude"] = nid_row.get("longitude", np.nan)

    # Derived ratios
    f["height_to_area_ratio"] = nid_row.get("height_to_area_ratio", np.nan)
    f["storage_ratio"] = nid_row.get("storage_ratio", np.nan)
    f["watershed_lake_ratio"] = nid_row.get("watershed_lake_ratio", np.nan)

    # Log-transformed
    f["log_dam_height_m"] = nid_row.get("log_dam_height_m", np.nan)
    f["log_area_km2"] = nid_row.get("log_area_km2", np.nan)
    f["log_storage_m3"] = nid_row.get("log_storage_m3", np.nan)

    # Categorical (encoded)
    f["hazard_numeric"] = nid_row.get("hazard_numeric", 0)

    # Purpose flags
    for col in nid_row.index:
        if col.startswith("purpose_"):
            f[col] = int(nid_row[col]) if pd.notna(nid_row[col]) else 0

    return f


def compute_cross_section_features(extraction_result) -> dict:
    """Extract features from the cross-section extraction result."""
    f = {}

    if extraction_result is None:
        return {
            "mean_valley_width_m": np.nan,
            "valley_width_std_m": np.nan,
            "mean_shape_exponent": np.nan,
            "shape_consistency": np.nan,
            "valley_asymmetry": np.nan,
            "n_valid_sections": 0,
            "mean_fit_r2": np.nan,
            "mean_confidence": np.nan,
            "thalweg_gradient_m_per_km": np.nan,
            "estimated_max_depth_m": np.nan,
        }

    cs_list = extraction_result.cross_sections

    # Valley width at water surface
    widths = []
    for cs in cs_list:
        if cs.is_water.any():
            water_offsets = cs.offsets_m[cs.is_water]
            width = water_offsets.max() - water_offsets.min()
            widths.append(width)

    f["mean_valley_width_m"] = float(np.mean(widths)) if widths else np.nan
    f["valley_width_std_m"] = float(np.std(widths)) if widths else np.nan
    f["valley_width_cv"] = (
        float(np.std(widths) / np.mean(widths)) if widths and np.mean(widths) > 0
        else np.nan
    )

    # Shape parameters from fitted models
    shape_exps = []
    for cs in cs_list:
        if cs.best_model == "v_shape" and "p2" in cs.model_params:
            shape_exps.append(cs.model_params["p2"])

    f["mean_shape_exponent"] = float(np.mean(shape_exps)) if shape_exps else np.nan
    f["shape_consistency"] = float(np.std(shape_exps)) if len(shape_exps) > 1 else np.nan
    f["frac_v_shaped"] = (
        sum(1 for cs in cs_list if cs.best_model == "v_shape") / max(len(cs_list), 1)
    )
    f["frac_u_shaped"] = (
        sum(1 for cs in cs_list if cs.best_model == "u_shape") / max(len(cs_list), 1)
    )

    # Fit quality
    f["n_valid_sections"] = extraction_result.n_valid_sections
    f["mean_fit_r2"] = extraction_result.mean_fit_r2
    f["mean_confidence"] = extraction_result.mean_confidence

    # Thalweg profile features
    dists = extraction_result.thalweg_profile_distances_m
    elevs = extraction_result.thalweg_profile_elevations_m

    if len(dists) > 1 and len(elevs) > 1:
        # Gradient (m drop per km length)
        total_drop = elevs[0] - elevs[-1]  # upstream - downstream
        total_length_km = (dists[-1] - dists[0]) / 1000.0
        f["thalweg_gradient_m_per_km"] = (
            float(total_drop / total_length_km) if total_length_km > 0 else np.nan
        )

        # Gradient variability
        local_gradients = np.diff(elevs) / np.diff(dists) * 1000  # m/km
        f["thalweg_gradient_std"] = float(np.std(local_gradients)) if len(local_gradients) > 0 else np.nan

        # Estimated max depth
        f["estimated_max_depth_m"] = float(
            extraction_result.water_surface_elevation_m - np.nanmin(elevs)
        )
    else:
        f["thalweg_gradient_m_per_km"] = np.nan
        f["thalweg_gradient_std"] = np.nan
        f["estimated_max_depth_m"] = np.nan

    return f


def compute_swot_features(swot_df: Optional[pd.DataFrame]) -> dict:
    """Extract features from SWOT time series for this reservoir."""
    f = {}

    if swot_df is None or len(swot_df) == 0:
        return {
            "wse_range_m": np.nan,
            "wse_std_m": np.nan,
            "wse_mean_m": np.nan,
            "n_swot_observations": 0,
            "area_range_km2": np.nan,
            "area_mean_km2": np.nan,
            "swot_data_available": False,
        }

    wse = pd.to_numeric(swot_df.get("wse", pd.Series()), errors="coerce")
    area = pd.to_numeric(swot_df.get("area_total", pd.Series()), errors="coerce")

    # Filter invalid
    wse_valid = wse[wse > -999999]
    area_valid = area[area > 0]

    f["n_swot_observations"] = len(wse_valid)
    f["swot_data_available"] = len(wse_valid) > 0

    if len(wse_valid) > 0:
        f["wse_range_m"] = float(wse_valid.max() - wse_valid.min())
        f["wse_std_m"] = float(wse_valid.std())
        f["wse_mean_m"] = float(wse_valid.mean())
    else:
        f["wse_range_m"] = np.nan
        f["wse_std_m"] = np.nan
        f["wse_mean_m"] = np.nan

    if len(area_valid) > 0:
        f["area_range_km2"] = float((area_valid.max() - area_valid.min()) / 1e6)
        f["area_mean_km2"] = float(area_valid.mean() / 1e6)
    else:
        f["area_range_km2"] = np.nan
        f["area_mean_km2"] = np.nan

    return f


def compute_flowline_features(flowline_metrics: Optional[dict]) -> dict:
    """Extract features from NHDPlus flowline analysis."""
    if flowline_metrics is None:
        return {
            "thalweg_length_m": np.nan,
            "sinuosity": np.nan,
            "n_tributaries": 0,
            "max_stream_order": 0,
        }

    return {
        "thalweg_length_m": flowline_metrics.get("thalweg_length_m", np.nan),
        "sinuosity": flowline_metrics.get("sinuosity", np.nan),
        "n_tributaries": flowline_metrics.get("n_tributaries", 0),
        "max_stream_order": flowline_metrics.get("max_stream_order", 0),
    }


def compute_reservoir_features(
    nid_row: pd.Series,
    extraction_result=None,
    swot_df: Optional[pd.DataFrame] = None,
    flowline_metrics: Optional[dict] = None,
) -> dict:
    """Compute all reservoir-specific features.

    Combines NID, cross-section, SWOT, and flowline features into a
    single feature dict suitable for ML model input.
    """
    features = {}
    features.update(compute_nid_features(nid_row))
    features.update(compute_cross_section_features(extraction_result))
    features.update(compute_swot_features(swot_df))
    features.update(compute_flowline_features(flowline_metrics))

    # Interaction features
    if np.isfinite(features.get("dam_height_m", np.nan)):
        dh = features["dam_height_m"]
        if np.isfinite(features.get("mean_valley_width_m", np.nan)):
            features["height_width_ratio"] = dh / max(features["mean_valley_width_m"], 1.0)
        if np.isfinite(features.get("thalweg_length_m", np.nan)):
            features["height_length_ratio"] = dh / max(features["thalweg_length_m"], 1.0) * 1000

    return features
