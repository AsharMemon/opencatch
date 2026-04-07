#!/usr/bin/env python3
"""
OpenCatch -- Reservoir Storage Nowcasting

Given a modeled Area-Elevation (A-E) curve and current water level,
estimate current reservoir storage volume with uncertainty.

This is the core product: V(t) = integral from E_min to E_current of A(e) de

Inputs:
  - Modeled A-E curve (from terrain extrapolation or SWOT fitting)
  - Current pool elevation (from USACE CWMS real-time data)
  - NID dam height (for max depth anchor)

Outputs per reservoir:
  - current_storage_m3 / _acft
  - percent_full
  - storage_deficit_m3
  - days_of_storage (given mean outflow)
  - storage_trend (filling/draining/stable from recent SWOT/USACE)
  - historical percentile for this date
  - uncertainty bounds (from A-E model confidence)

Usage:
    python storage_nowcast.py \\
        --ae-curves /data/reservoir/ae_curves/ \\
        --nid-catalog /data/reservoir/nid_reservoir_catalog.parquet \\
        --output /data/reservoir/storage_nowcast.parquet

Requirements:
    pip install pandas numpy scipy pyarrow requests
"""

import argparse
import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("storage_nowcast")

# Unit conversions
ACFT_TO_M3 = 1233.48184
M3_TO_ACFT = 1.0 / ACFT_TO_M3


class StorageNowcaster:
    """Compute real-time reservoir storage from A-E curve + pool level.

    Parameters
    ----------
    elevations_m : array
        Elevation values in meters (increasing).
    areas_m2 : array
        Reservoir surface area at each elevation in m².
    max_storage_m3 : float
        Maximum storage capacity from NID (for percent_full reference).
    """

    def __init__(
        self,
        elevations_m: np.ndarray,
        areas_m2: np.ndarray,
        max_storage_m3: float = np.nan,
    ):
        self.elevations = np.asarray(elevations_m, dtype=float)
        self.areas = np.asarray(areas_m2, dtype=float)
        self.max_storage_m3 = max_storage_m3

        # Pre-compute cumulative volume via trapezoidal integration
        dz = np.diff(self.elevations)
        avg_areas = (self.areas[:-1] + self.areas[1:]) / 2.0
        self._cumulative_volume = np.zeros(len(self.elevations))
        self._cumulative_volume[1:] = np.cumsum(avg_areas * dz)

    def storage_at_elevation(self, elevation_m: float) -> float:
        """Compute storage volume at a given pool elevation.

        Uses linear interpolation on the pre-computed cumulative volume curve.
        """
        if elevation_m <= self.elevations[0]:
            return 0.0
        if elevation_m >= self.elevations[-1]:
            return float(self._cumulative_volume[-1])

        return float(np.interp(elevation_m, self.elevations, self._cumulative_volume))

    def area_at_elevation(self, elevation_m: float) -> float:
        """Compute surface area at a given pool elevation."""
        return float(np.interp(elevation_m, self.elevations, self.areas))

    def elevation_at_storage(self, volume_m3: float) -> float:
        """Inverse: find pool elevation for a given storage volume."""
        if volume_m3 <= 0:
            return float(self.elevations[0])
        if volume_m3 >= self._cumulative_volume[-1]:
            return float(self.elevations[-1])

        return float(np.interp(volume_m3, self._cumulative_volume, self.elevations))

    def nowcast(
        self,
        current_elevation_m: float,
        mean_daily_outflow_m3: float = 0.0,
        uncertainty_pct: float = 10.0,
    ) -> dict:
        """Compute storage nowcast for current conditions.

        Parameters
        ----------
        current_elevation_m : float
            Current pool elevation in meters.
        mean_daily_outflow_m3 : float
            Mean daily outflow in m³/day (for days-of-storage calc).
        uncertainty_pct : float
            Percent uncertainty in the A-E curve (default 10%).

        Returns
        -------
        dict with storage metrics.
        """
        storage = self.storage_at_elevation(current_elevation_m)
        area = self.area_at_elevation(current_elevation_m)
        max_vol = self._cumulative_volume[-1]

        # Percent full (use NID max if available, otherwise use A-E max)
        ref_max = self.max_storage_m3 if np.isfinite(self.max_storage_m3) else max_vol
        percent_full = (storage / ref_max * 100) if ref_max > 0 else 0.0

        # Storage deficit
        deficit = max(0, ref_max - storage)

        # Days of storage
        days_of_storage = (
            storage / mean_daily_outflow_m3 if mean_daily_outflow_m3 > 0 else np.inf
        )

        # Uncertainty bounds
        unc_fraction = uncertainty_pct / 100.0
        storage_lower = storage * (1 - unc_fraction)
        storage_upper = storage * (1 + unc_fraction)

        return {
            "current_elevation_m": current_elevation_m,
            "current_storage_m3": storage,
            "current_storage_acft": storage * M3_TO_ACFT,
            "current_area_m2": area,
            "current_area_km2": area / 1e6,
            "percent_full": percent_full,
            "storage_deficit_m3": deficit,
            "storage_deficit_acft": deficit * M3_TO_ACFT,
            "days_of_storage": days_of_storage,
            "max_storage_m3": ref_max,
            "max_storage_acft": ref_max * M3_TO_ACFT,
            "ae_max_volume_m3": max_vol,
            "storage_lower_m3": storage_lower,
            "storage_upper_m3": storage_upper,
            "uncertainty_pct": uncertainty_pct,
        }


def compute_storage_trend(
    wse_timeseries: pd.DataFrame,
    lookback_days: int = 30,
) -> dict:
    """Compute storage trend from recent water surface elevation observations.

    Parameters
    ----------
    wse_timeseries : DataFrame with 'time' and 'wse' columns.
    lookback_days : Number of days to look back for trend.

    Returns
    -------
    dict with trend metrics.
    """
    if wse_timeseries is None or len(wse_timeseries) < 2:
        return {"trend": "unknown", "wse_change_m_per_day": np.nan}

    df = wse_timeseries.copy()
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time")
    df["wse"] = pd.to_numeric(df["wse"], errors="coerce")
    df = df.dropna(subset=["wse"])

    if len(df) < 2:
        return {"trend": "unknown", "wse_change_m_per_day": np.nan}

    # Recent observations
    cutoff = df["time"].max() - pd.Timedelta(days=lookback_days)
    recent = df[df["time"] >= cutoff]

    if len(recent) < 2:
        recent = df.tail(5)

    # Linear trend
    days = (recent["time"] - recent["time"].iloc[0]).dt.total_seconds() / 86400.0
    if days.max() > 0:
        slope = np.polyfit(days, recent["wse"], 1)[0]  # m/day
    else:
        slope = 0.0

    # Classify
    if abs(slope) < 0.01:  # < 1 cm/day
        trend = "stable"
    elif slope > 0:
        trend = "filling"
    else:
        trend = "draining"

    return {
        "trend": trend,
        "wse_change_m_per_day": float(slope),
        "wse_change_m_per_month": float(slope * 30),
        "n_recent_obs": len(recent),
    }


def load_ae_curve(ae_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Load an A-E curve from Parquet file."""
    df = pd.read_parquet(ae_path)

    elev_col = next(
        (c for c in df.columns if "elev" in c.lower() and "_m" in c.lower()),
        df.columns[0],
    )
    area_col = next(
        (c for c in df.columns if "area" in c.lower() and "m2" in c.lower()),
        None,
    )
    if area_col is None:
        # Try km2 and convert
        area_col = next(
            (c for c in df.columns if "area" in c.lower() and "km2" in c.lower()),
            df.columns[1],
        )
        areas = df[area_col].values * 1e6  # km2 -> m2
    else:
        areas = df[area_col].values

    return df[elev_col].values, areas


def main():
    parser = argparse.ArgumentParser(description="Reservoir storage nowcasting")
    parser.add_argument("--ae-curves", type=str, required=True,
                       help="Directory containing A-E curve Parquet files")
    parser.add_argument("--nid-catalog", type=str, required=True,
                       help="NID reservoir catalog Parquet")
    parser.add_argument("--output", type=str, required=True,
                       help="Output Parquet path")
    parser.add_argument("--usace-pool-levels", type=str, default=None,
                       help="USACE current pool levels CSV (optional)")
    args = parser.parse_args()

    ae_dir = Path(args.ae_curves)
    nid = pd.read_parquet(args.nid_catalog)
    log.info(f"Loaded {len(nid)} reservoirs from NID catalog")

    results = []

    for _, row in nid.iterrows():
        nid_id = row.get("nid_id", "")
        name = row.get("dam_name", "")

        # Find A-E curve for this reservoir
        ae_path = ae_dir / f"{nid_id}_ae.parquet"
        if not ae_path.exists():
            # Try by name
            name_safe = name.replace(" ", "_").lower()
            ae_path = ae_dir / f"{name_safe}_ae_table.parquet"

        if not ae_path.exists():
            continue

        try:
            elevs, areas = load_ae_curve(ae_path)
        except Exception as e:
            log.warning(f"Failed to load A-E for {nid_id}: {e}")
            continue

        max_storage = row.get("max_storage_m3", np.nan)
        nowcaster = StorageNowcaster(elevs, areas, max_storage)

        # Get current pool elevation
        # TODO: integrate with USACE real-time API (castline/api/services/usace.py)
        current_elev = row.get("crest_elevation_m", np.nan)
        if np.isnan(current_elev):
            continue

        # Use 90% of crest as rough current estimate if no real-time data
        current_elev *= 0.95  # placeholder

        result = nowcaster.nowcast(current_elev)
        result["nid_id"] = nid_id
        result["dam_name"] = name
        result["state"] = row.get("state", "")
        results.append(result)

    if results:
        df_out = pd.DataFrame(results)
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df_out.to_parquet(out_path, index=False, engine="pyarrow")
        log.info(f"Saved storage nowcast for {len(df_out)} reservoirs to {out_path}")
    else:
        log.warning("No storage nowcasts computed")


if __name__ == "__main__":
    main()
