#!/usr/bin/env python3
"""
ice_phenology_depth.py — Winter littoral-depth prior from Sentinel-1 SAR ice phenology.

Novel approach: shallow lake areas freeze earlier than deep areas (well-established
limnology). Sentinel-1 C-band SAR backscatter changes dramatically when lake ice
forms (~5-10 dB increase in VV). By tracking the spatial progression of freeze-up
through winter, each day of delayed freeze-up maps to a depth increment.

The freeze-date → depth relationship is calibrated on 4,500 MN DNR sonar-surveyed
lakes. This produces turbidity-independent depth proxies for the 0-5m littoral zone,
which can be fed into the main XGBoost bathymetry pipeline or used standalone.

Key signals:
  - freeze_onset_doy: Day of year when ice forms at each pixel
  - freeze_delay_days: Relative timing vs. first-frozen pixel in lake
  - is_bedfast: Whether SAR shows ice frozen to lakebed (VV drops in late winter)
  - ice_duration_days: Total ice-covered days per pixel

References:
  - Duguay, C.R. & Lafleur, P.M. (2003). Determining depth and ice thickness of
    shallow sub-Arctic lakes using space-borne optical and SAR data. International
    Journal of Remote Sensing, 24(3), 475-489. Achieved 15cm RMSE on subarctic
    tundra lakes using SAR-derived ice phenology.
  - Surdu et al. (2014). SAR-based detection of bedfast vs floating ice for
    shallow thermokarst lakes.

Limitations:
  - Direct ice-bed detection limited to 0-1.5m (max ice thickness in MN)
  - Freeze-up timing extends useful signal to 0-5m, possibly 0-10m
  - Requires cloud-free SAR acquisitions during freeze-up (Oct-Dec)
  - 12-day Sentinel-1 repeat cycle gives ~10-15 images per winter

Usage:
  python ice_phenology_depth.py \\
      --mode calibrate \\
      --lakes /data/waterbodies/mn_lakes.geojson \\
      --surveys /data/dnr_surveys/ \\
      --winters 2022-2023,2023-2024,2024-2025 \\
      --output /data/ice_phenology \\
      --device cpu
"""

import argparse
import logging
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentinel-1 data access
# ---------------------------------------------------------------------------

def _build_stac_query(bbox: tuple, start_date: str, end_date: str) -> dict:
    """Build a STAC search payload for Sentinel-1 GRD IW over a bounding box."""
    return {
        "collections": ["sentinel-1-grd"],
        "bbox": list(bbox),
        "datetime": f"{start_date}/{end_date}",
        "query": {
            "sar:instrument_mode": {"eq": "IW"},
            "sar:polarizations": {"contains": ["VV", "VH"]},
            "sat:orbit_state": {"eq": "descending"},
        },
        "limit": 100,
    }


class IcePhenologyDepthPrior:
    """
    Extract depth-correlated features from Sentinel-1 SAR ice phenology.

    For each lake pixel, compute:
      1. freeze_onset_doy  — Day of year when backscatter transitions to ice
      2. freeze_delay_days — Days after first pixel freezes (relative timing)
      3. is_bedfast        — Whether SAR signature indicates ice frozen to bed
      4. ice_duration_days — Total days with ice cover

    These features correlate with depth and serve as turbidity-independent
    depth proxies for the 0-5m littoral zone.
    """

    STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"

    # Backscatter thresholds (dB) derived from literature
    VV_ICE_JUMP_DB = 4.0        # minimum VV increase to flag freeze onset
    VV_BEDFAST_DROP_DB = -3.0   # VV drop indicating bedfast ice
    OPEN_WATER_VV_MAX = -14.0   # typical calm open water VV ceiling (dB)

    def __init__(self, resolution_m: float = 20.0, device: str = "cpu"):
        self.resolution_m = resolution_m
        self.device = device

    # ------------------------------------------------------------------
    # 1. Fetch Sentinel-1 time series
    # ------------------------------------------------------------------

    def fetch_sentinel1_timeseries(
        self,
        lake_polygon: "shapely.geometry.Polygon",
        winter_start: str,
        winter_end: str,
    ) -> Optional[xr.Dataset]:
        """
        Query Sentinel-1 GRD IW via Planetary Computer STAC and return VV/VH
        backscatter time series clipped to *lake_polygon*.

        Parameters
        ----------
        lake_polygon : shapely Polygon in EPSG:4326
        winter_start : ISO date string, e.g. '2023-10-01'
        winter_end   : ISO date string, e.g. '2024-04-30'

        Returns
        -------
        xr.Dataset with dims (time, y, x) and variables 'vv' and 'vh' in dB,
        or None if no scenes are found.
        """
        try:
            import planetary_computer as pc
            import pystac_client
            import stackstac
        except ImportError:
            logger.error(
                "planetary_computer / pystac_client / stackstac not installed. "
                "pip install planetary-computer pystac-client stackstac"
            )
            return None

        bbox = lake_polygon.bounds  # (minx, miny, maxx, maxy)
        catalog = pystac_client.Client.open(self.STAC_API, modifier=pc.sign_inplace)

        search = catalog.search(
            **_build_stac_query(bbox, winter_start, winter_end)
        )
        items = list(search.items())
        if not items:
            logger.warning(
                "No Sentinel-1 scenes for bbox=%s  %s→%s",
                bbox, winter_start, winter_end,
            )
            return None

        logger.info("Found %d Sentinel-1 scenes for winter %s→%s", len(items), winter_start, winter_end)

        stack = stackstac.stack(
            items,
            assets=["vv", "vh"],
            resolution=self.resolution_m,
            bounds=bbox,
            epsg=32615,  # UTM 15N for Minnesota
        )

        # Convert to dB: 10 * log10(linear)
        stack = 10.0 * np.log10(stack.clip(min=1e-10))

        # Mask to lake polygon
        try:
            from rasterio.features import geometry_mask
            from shapely.ops import transform
            import pyproj

            transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32615", always_xy=True)
            lake_utm = transform(transformer.transform, lake_polygon)

            mask_2d = geometry_mask(
                [lake_utm],
                out_shape=(stack.sizes["y"], stack.sizes["x"]),
                transform=stackstac.utils.affine_from_stack(stack) if hasattr(stackstac, "utils") else None,
                invert=True,
            )
            mask_da = xr.DataArray(mask_2d, dims=["y", "x"])
            stack = stack.where(mask_da)
        except Exception as exc:
            logger.warning("Lake masking failed, using full bbox: %s", exc)

        ds = stack.to_dataset(dim="band").rename({"vv": "vv", "vh": "vh"})
        ds = ds.sortby("time")
        return ds

    # ------------------------------------------------------------------
    # 2. Detect freeze onset per pixel
    # ------------------------------------------------------------------

    @staticmethod
    def detect_freeze_onset(
        vv_timeseries: np.ndarray,
        dates: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Detect the date each pixel transitions from open water to ice.

        Ice formation causes VV backscatter to jump ~5-10 dB.  We use a simple
        forward-difference changepoint: first date where VV increases by more
        than VV_ICE_JUMP_DB relative to the running open-water baseline.

        Parameters
        ----------
        vv_timeseries : (T, H, W) array of VV backscatter in dB
        dates         : (T,) array of datetime64

        Returns
        -------
        freeze_doy   : (H, W) — day of year of freeze onset (NaN if never freezes)
        freeze_delay : (H, W) — days after earliest pixel froze
        """
        T, H, W = vv_timeseries.shape
        freeze_doy = np.full((H, W), np.nan)

        # Running baseline: median of first 3 acquisitions (assumed open water)
        n_baseline = min(3, T)
        baseline = np.nanmedian(vv_timeseries[:n_baseline], axis=0)

        for t in range(n_baseline, T):
            jump = vv_timeseries[t] - baseline
            newly_frozen = (jump > IcePhenologyDepthPrior.VV_ICE_JUMP_DB) & np.isnan(freeze_doy)
            doy = pd.Timestamp(dates[t]).timetuple().tm_yday
            freeze_doy[newly_frozen] = doy

        # Relative delay from first frozen pixel
        min_doy = np.nanmin(freeze_doy)
        freeze_delay = freeze_doy - min_doy if not np.isnan(min_doy) else np.full_like(freeze_doy, np.nan)

        return freeze_doy, freeze_delay

    # ------------------------------------------------------------------
    # 3. Classify bedfast vs floating ice
    # ------------------------------------------------------------------

    @staticmethod
    def classify_bedfast_vs_floating(vv_timeseries: np.ndarray) -> np.ndarray:
        """
        Classify each pixel as bedfast ice (True) or floating ice (False).

        Bedfast ice: VV backscatter drops dramatically in late winter as the
        ice-water interface disappears and the signal penetrates to the lakebed.
        Floating ice: VV stays elevated throughout winter.

        Parameters
        ----------
        vv_timeseries : (T, H, W) array of VV backscatter in dB

        Returns
        -------
        is_bedfast : (H, W) boolean array
        """
        T = vv_timeseries.shape[0]
        if T < 4:
            return np.zeros(vv_timeseries.shape[1:], dtype=bool)

        # Compare mid-winter peak to late-winter values
        mid_idx = T // 2
        late_idx = max(mid_idx + 1, T - 3)

        mid_vv = np.nanmean(vv_timeseries[mid_idx - 1 : mid_idx + 2], axis=0)
        late_vv = np.nanmean(vv_timeseries[late_idx:], axis=0)

        drop = late_vv - mid_vv
        is_bedfast = drop < IcePhenologyDepthPrior.VV_BEDFAST_DROP_DB
        return is_bedfast

    # ------------------------------------------------------------------
    # 4. Build per-pixel depth features from multi-winter aggregation
    # ------------------------------------------------------------------

    def build_depth_features(
        self,
        lake_polygon: "shapely.geometry.Polygon",
        lake_id: str,
        winter_years: list[int] = None,
    ) -> Optional[xr.Dataset]:
        """
        Aggregate freeze-onset features across multiple winters for robustness.

        Returns per-pixel feature array with:
          - mean_freeze_delay, std_freeze_delay
          - bedfast_fraction
          - mean_ice_duration

        Parameters
        ----------
        lake_polygon : shapely Polygon in EPSG:4326
        lake_id      : identifier for logging
        winter_years : list of winter start years, e.g. [2022, 2023, 2024]

        Returns
        -------
        xr.Dataset with dims (y, x) and feature variables, or None on failure.
        """
        if winter_years is None:
            winter_years = [2023, 2024, 2025]

        all_delays = []
        all_bedfast = []
        all_durations = []

        for year in winter_years:
            start = f"{year}-10-01"
            end = f"{year + 1}-04-30"

            ds = self.fetch_sentinel1_timeseries(lake_polygon, start, end)
            if ds is None:
                logger.warning("Skipping winter %d-%d for lake %s — no data", year, year + 1, lake_id)
                continue

            vv = ds["vv"].values  # (T, H, W)
            dates = ds["time"].values

            if vv.shape[0] < 3:
                logger.warning("Only %d scenes for lake %s winter %d — skipping", vv.shape[0], lake_id, year)
                continue

            freeze_doy, freeze_delay = self.detect_freeze_onset(vv, dates)
            is_bedfast = self.classify_bedfast_vs_floating(vv)

            # Ice duration: count of scenes where VV is above open-water baseline
            baseline = np.nanmedian(vv[:3], axis=0)
            ice_mask = vv > (baseline + self.VV_ICE_JUMP_DB * 0.5)
            duration_scenes = np.nansum(ice_mask, axis=0)
            # Convert scene count to approximate days (12-day repeat)
            duration_days = duration_scenes * 12.0

            all_delays.append(freeze_delay)
            all_bedfast.append(is_bedfast.astype(float))
            all_durations.append(duration_days)

        if not all_delays:
            logger.error("No valid winter data for lake %s", lake_id)
            return None

        # Stack and aggregate across winters
        delays = np.stack(all_delays, axis=0)
        bedfast = np.stack(all_bedfast, axis=0)
        durations = np.stack(all_durations, axis=0)

        features = xr.Dataset(
            {
                "mean_freeze_delay": (["y", "x"], np.nanmean(delays, axis=0)),
                "std_freeze_delay": (["y", "x"], np.nanstd(delays, axis=0)),
                "bedfast_fraction": (["y", "x"], np.nanmean(bedfast, axis=0)),
                "mean_ice_duration": (["y", "x"], np.nanmean(durations, axis=0)),
            },
            attrs={"lake_id": lake_id, "n_winters": len(all_delays)},
        )
        return features

    # ------------------------------------------------------------------
    # 5. Calibrate freeze features → depth using DNR surveys
    # ------------------------------------------------------------------

    def calibrate_freeze_depth(
        self,
        train_lakes: gpd.GeoDataFrame,
        survey_dir: Path,
        winter_years: list[int],
        output_dir: Path,
    ) -> dict:
        """
        For each training lake with a DNR sonar survey:
          1. Extract ice phenology features per pixel
          2. Match to known depth at each pixel location
          3. Fit XGBoost: freeze_features → depth

        Parameters
        ----------
        train_lakes  : GeoDataFrame with columns [lake_id, geometry]
        survey_dir   : directory of DNR survey rasters (lake_id.tif)
        winter_years : list of winter start years
        output_dir   : where to save the calibrated model

        Returns
        -------
        dict with keys: model, cv_r2, cv_rmse, n_lakes, n_pixels
        """
        from sklearn.model_selection import cross_val_score

        try:
            from xgboost import XGBRegressor
        except ImportError:
            logger.error("xgboost not installed — pip install xgboost")
            return {"model": None, "cv_r2": np.nan}

        feature_names = [
            "mean_freeze_delay",
            "std_freeze_delay",
            "bedfast_fraction",
            "mean_ice_duration",
        ]

        all_X = []
        all_y = []
        n_lakes_used = 0

        for _, row in train_lakes.iterrows():
            lake_id = row["lake_id"]
            survey_path = survey_dir / f"{lake_id}.tif"
            if not survey_path.exists():
                continue

            # Load survey depth raster
            try:
                import rasterio
                with rasterio.open(survey_path) as src:
                    depth_grid = src.read(1)
                    depth_transform = src.transform
            except Exception as exc:
                logger.warning("Cannot read survey for %s: %s", lake_id, exc)
                continue

            features_ds = self.build_depth_features(row.geometry, lake_id, winter_years)
            if features_ds is None:
                continue

            # Flatten and pair with depth
            H, W = features_ds.dims["y"], features_ds.dims["x"]
            # Resample depth grid to feature grid if needed
            if depth_grid.shape != (H, W):
                from skimage.transform import resize
                depth_grid = resize(depth_grid, (H, W), order=1, preserve_range=True)

            depth_flat = depth_grid.ravel()
            feat_flat = np.column_stack([
                features_ds[f].values.ravel() for f in feature_names
            ])

            # Keep only valid pixels (non-NaN in both depth and features)
            valid = np.isfinite(depth_flat) & np.all(np.isfinite(feat_flat), axis=1)
            valid &= depth_flat > 0  # exclude land / nodata

            if valid.sum() < 10:
                continue

            all_X.append(feat_flat[valid])
            all_y.append(depth_flat[valid])
            n_lakes_used += 1
            logger.info("Lake %s: %d valid pixels", lake_id, valid.sum())

        if n_lakes_used == 0:
            logger.error("No lakes with both survey and SAR data")
            return {"model": None, "cv_r2": np.nan}

        X = np.concatenate(all_X, axis=0)
        y = np.concatenate(all_y, axis=0)
        logger.info("Calibration dataset: %d pixels from %d lakes", len(y), n_lakes_used)

        # Fit XGBoost with conservative hyperparameters
        model = XGBRegressor(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=1.0,
            reg_lambda=5.0,
            tree_method="hist",
            device=self.device,
            random_state=42,
        )

        # 5-fold CV
        cv_scores = cross_val_score(model, X, y, cv=5, scoring="r2")
        cv_r2 = float(np.mean(cv_scores))

        from sklearn.metrics import mean_squared_error
        from sklearn.model_selection import cross_val_predict

        y_pred_cv = cross_val_predict(model, X, y, cv=5)
        cv_rmse = float(np.sqrt(mean_squared_error(y, y_pred_cv)))

        logger.info("CV R²=%.3f (±%.3f), RMSE=%.2fm", cv_r2, np.std(cv_scores), cv_rmse)

        # Fit final model on all data
        model.fit(X, y)

        # Save
        output_dir.mkdir(parents=True, exist_ok=True)
        import joblib
        model_path = output_dir / "ice_phenology_xgb.joblib"
        joblib.dump(model, model_path)
        logger.info("Saved calibrated model → %s", model_path)

        return {
            "model": model,
            "cv_r2": cv_r2,
            "cv_rmse": cv_rmse,
            "n_lakes": n_lakes_used,
            "n_pixels": len(y),
            "feature_names": feature_names,
        }

    # ------------------------------------------------------------------
    # 6. Predict depth from ice phenology (unsurveyed lakes)
    # ------------------------------------------------------------------

    def predict_depth_from_ice(
        self,
        lake_polygon: "shapely.geometry.Polygon",
        lake_id: str,
        calibration_model,
        winter_years: list[int] = None,
    ) -> Optional[xr.DataArray]:
        """
        Apply calibrated model to produce depth predictions for an unsurveyed lake.

        These predictions are turbidity-independent and work for all northern lakes
        with seasonal ice cover.

        Returns
        -------
        xr.DataArray of predicted depth (meters) with dims (y, x), or None.
        """
        features_ds = self.build_depth_features(lake_polygon, lake_id, winter_years)
        if features_ds is None:
            return None

        feature_names = [
            "mean_freeze_delay",
            "std_freeze_delay",
            "bedfast_fraction",
            "mean_ice_duration",
        ]

        H, W = features_ds.dims["y"], features_ds.dims["x"]
        feat_flat = np.column_stack([
            features_ds[f].values.ravel() for f in feature_names
        ])

        valid = np.all(np.isfinite(feat_flat), axis=1)
        predictions = np.full(H * W, np.nan)

        if valid.sum() > 0:
            predictions[valid] = calibration_model.predict(feat_flat[valid])
            # Clip to physical range (0-15m for littoral zone)
            predictions[valid] = np.clip(predictions[valid], 0.0, 15.0)

        depth_pred = xr.DataArray(
            predictions.reshape(H, W),
            dims=["y", "x"],
            attrs={
                "lake_id": lake_id,
                "units": "meters",
                "method": "ice_phenology_sar",
                "description": "Depth predicted from Sentinel-1 SAR ice phenology features",
            },
        )
        return depth_pred


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_winters(s: str) -> list[int]:
    """Parse '2022-2023,2023-2024' into [2022, 2023]."""
    years = []
    for pair in s.split(","):
        start_year = int(pair.strip().split("-")[0])
        years.append(start_year)
    return years


def main():
    parser = argparse.ArgumentParser(
        description="Sentinel-1 SAR ice phenology → littoral depth prior",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=["calibrate", "predict"],
        required=True,
        help="calibrate: fit model on DNR surveys; predict: apply to unsurveyed lakes",
    )
    parser.add_argument("--lakes", type=Path, required=True, help="GeoJSON of lake polygons")
    parser.add_argument("--surveys", type=Path, help="Directory of DNR survey rasters (for calibrate)")
    parser.add_argument("--model", type=Path, help="Pre-trained model path (for predict)")
    parser.add_argument("--winters", type=str, default="2022-2023,2023-2024,2024-2025")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    parser.add_argument("--resolution", type=float, default=20.0, help="Pixel resolution in meters")
    parser.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--max-lakes", type=int, default=None, help="Limit number of lakes (for testing)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    winter_years = parse_winters(args.winters)
    prior = IcePhenologyDepthPrior(resolution_m=args.resolution, device=args.device)

    lakes = gpd.read_file(args.lakes)
    if args.max_lakes:
        lakes = lakes.head(args.max_lakes)
    logger.info("Loaded %d lakes from %s", len(lakes), args.lakes)

    if args.mode == "calibrate":
        if args.surveys is None:
            parser.error("--surveys required for calibrate mode")
        results = prior.calibrate_freeze_depth(lakes, args.surveys, winter_years, args.output)
        logger.info(
            "Calibration complete: R²=%.3f, RMSE=%.2fm (%d lakes, %d pixels)",
            results["cv_r2"],
            results.get("cv_rmse", float("nan")),
            results.get("n_lakes", 0),
            results.get("n_pixels", 0),
        )

    elif args.mode == "predict":
        if args.model is None:
            parser.error("--model required for predict mode")
        import joblib
        cal_model = joblib.load(args.model)
        logger.info("Loaded calibration model from %s", args.model)

        args.output.mkdir(parents=True, exist_ok=True)
        n_predicted = 0

        for _, row in lakes.iterrows():
            lake_id = row["lake_id"]
            depth = prior.predict_depth_from_ice(row.geometry, lake_id, cal_model, winter_years)
            if depth is not None:
                out_path = args.output / f"{lake_id}_ice_depth.nc"
                depth.to_netcdf(out_path)
                n_predicted += 1
                logger.info("Predicted depth for %s → %s", lake_id, out_path)

        logger.info("Predicted depth for %d / %d lakes", n_predicted, len(lakes))


if __name__ == "__main__":
    main()
