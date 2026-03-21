"""Conformal prediction and calibrated uncertainty for CASTLINE V15 ensemble.

Provides distribution-free prediction intervals via split conformal inference.
Optionally weights calibration residuals by geographic distance (GeoConformal)
so that nearby locations contribute more to the uncertainty band.

Usage:
    from castline.models.conformal import ConformalPredictor

    cp = ConformalPredictor(predictor, alpha=0.10)
    cp.calibrate(X_cal, y_cal, coords_cal=coords)
    point, lo, hi, width = cp.predict_with_interval(features, lat=34.5, lon=-87.2)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class UncertaintyResult:
    """Prediction with calibrated uncertainty band."""
    point_prediction: float
    lower_bound: float
    upper_bound: float
    interval_width: float
    coverage_target: float  # 1 - alpha, e.g. 0.90
    method: str  # "conformal" or "geo_conformal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_prediction": round(self.point_prediction, 4),
            "lower_bound": round(self.lower_bound, 4),
            "upper_bound": round(self.upper_bound, 4),
            "interval_width": round(self.interval_width, 4),
            "coverage_target": self.coverage_target,
            "method": self.method,
        }

    @property
    def margin(self) -> float:
        """Half-width of the interval (for display as +/- value)."""
        return self.interval_width / 2.0


class ConformalPredictor:
    """Split conformal prediction wrapper for the V15 stacked ensemble.

    After calibration on held-out residuals, produces distribution-free
    prediction intervals with guaranteed finite-sample coverage.

    Parameters
    ----------
    predictor : V14Predictor
        The loaded V15 stacked ensemble predictor.
    alpha : float
        Miscoverage rate.  alpha=0.10 gives 90% prediction intervals.
    geo_bandwidth_km : float
        Bandwidth for inverse-distance weighting in GeoConformal mode.
        Controls how quickly geographic influence decays with distance.
        Smaller values make intervals more local.
    """

    # Earth radius in km for haversine
    _EARTH_RADIUS_KM = 6371.0

    def __init__(
        self,
        predictor: Any,
        alpha: float = 0.10,
        geo_bandwidth_km: float = 200.0,
    ):
        self.predictor = predictor
        self.alpha = alpha
        self.geo_bandwidth_km = geo_bandwidth_km

        # Populated by calibrate()
        self.scores_: np.ndarray | None = None  # nonconformity scores (|residual|)
        self.residuals_: np.ndarray | None = None  # signed residuals
        self.coords_: np.ndarray | None = None  # (n, 2) lat/lon of calibration points
        self.locations_: list[str] | None = None  # location names
        self.q_hat_: float | None = None  # conformal quantile (standard mode)
        self._calibrated = False

    # ── Calibration ───────────────────────────────────────────────

    def calibrate(
        self,
        X_cal: pd.DataFrame,
        y_cal: np.ndarray,
        locations_cal: list[str] | None = None,
        coords_cal: np.ndarray | None = None,
        route_labels: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """Calibrate on held-out data.

        Parameters
        ----------
        X_cal : DataFrame
            Feature matrix for calibration set (same features as training).
        y_cal : array-like
            True target values for calibration set.
        locations_cal : list[str], optional
            Location names, used for seen/unseen routing during calibration.
        coords_cal : array of shape (n, 2), optional
            Latitude/longitude pairs for GeoConformal weighting.
        route_labels : array of {"seen", "unseen"}, optional
            Pre-computed route labels.  If None, derived from locations_cal.

        Returns
        -------
        dict with calibration statistics.
        """
        y_cal = np.asarray(y_cal, dtype=float)
        n = len(y_cal)
        if n < 10:
            raise ValueError(f"Need at least 10 calibration points, got {n}")

        # Generate predictions for each calibration row
        preds = np.empty(n, dtype=float)
        routes = []

        for i in range(n):
            row_df = X_cal.iloc[[i]]
            loc = locations_cal[i] if locations_cal is not None else ""

            if route_labels is not None:
                route = route_labels[i]
            else:
                route = "seen" if self.predictor.is_seen_location(loc) else "unseen"

            routes.append(route)

            if route == "seen":
                log_pred = self.predictor._stacked_predict(
                    row_df.copy(), self.predictor.seen_models,
                    self.predictor.seen_ridge, self.predictor.seen_features,
                )
            else:
                log_pred = self.predictor._stacked_predict(
                    row_df.copy(), self.predictor.unseen_models,
                    self.predictor.unseen_ridge, self.predictor.unseen_features,
                )
            preds[i] = float(log_pred[0])

        # Nonconformity scores = absolute residuals
        self.residuals_ = y_cal - preds
        self.scores_ = np.abs(self.residuals_)
        self.locations_ = list(locations_cal) if locations_cal is not None else None

        if coords_cal is not None:
            self.coords_ = np.asarray(coords_cal, dtype=float)
            if self.coords_.shape != (n, 2):
                raise ValueError(
                    f"coords_cal must have shape ({n}, 2), got {self.coords_.shape}"
                )
        else:
            self.coords_ = None

        # Standard conformal quantile: ceil((n+1)(1-alpha)) / n
        level = (1.0 - self.alpha) * (1.0 + 1.0 / n)
        self.q_hat_ = float(np.quantile(self.scores_, min(level, 1.0)))
        self._calibrated = True

        # Per-route statistics
        routes_arr = np.array(routes)
        stats: dict[str, Any] = {
            "n_calibration": n,
            "alpha": self.alpha,
            "coverage_target": 1.0 - self.alpha,
            "q_hat": round(self.q_hat_, 4),
            "mean_abs_residual": round(float(np.mean(self.scores_)), 4),
            "median_abs_residual": round(float(np.median(self.scores_)), 4),
            "mean_residual": round(float(np.mean(self.residuals_)), 4),
            "std_residual": round(float(np.std(self.residuals_)), 4),
            "has_geo": self.coords_ is not None,
            "geo_bandwidth_km": self.geo_bandwidth_km,
        }

        for route in ("seen", "unseen"):
            mask = routes_arr == route
            if mask.any():
                route_scores = self.scores_[mask]
                route_q = float(np.quantile(
                    route_scores,
                    min((1.0 - self.alpha) * (1.0 + 1.0 / mask.sum()), 1.0),
                ))
                stats[f"{route}_n"] = int(mask.sum())
                stats[f"{route}_q_hat"] = round(route_q, 4)
                stats[f"{route}_mean_abs_residual"] = round(
                    float(np.mean(route_scores)), 4
                )

        logger.info(
            "Conformal calibration complete: n=%d, q_hat=%.4f (%.0f%% coverage)",
            n, self.q_hat_, (1.0 - self.alpha) * 100,
        )
        return stats

    # ── Prediction with intervals ────────────────────────────────

    def predict_with_interval(
        self,
        features_df: pd.DataFrame,
        location: str = "",
        lat: float | None = None,
        lon: float | None = None,
    ) -> UncertaintyResult:
        """Return a prediction with calibrated uncertainty band.

        Uses GeoConformal weighting when lat/lon are provided and
        geographic calibration data is available.  Falls back to
        standard conformal otherwise.

        Parameters
        ----------
        features_df : DataFrame
            Single-row feature DataFrame.
        location : str
            Location name (for seen/unseen routing).
        lat, lon : float, optional
            Coordinates for GeoConformal weighting.
        """
        if not self._calibrated:
            raise RuntimeError("Must call calibrate() before predict_with_interval()")

        # Point prediction via the ensemble
        route = "seen" if self.predictor.is_seen_location(location) else "unseen"
        if route == "seen":
            log_pred = self.predictor._stacked_predict(
                features_df.copy(), self.predictor.seen_models,
                self.predictor.seen_ridge, self.predictor.seen_features,
            )
        else:
            log_pred = self.predictor._stacked_predict(
                features_df.copy(), self.predictor.unseen_models,
                self.predictor.unseen_ridge, self.predictor.unseen_features,
            )
        point = float(log_pred[0])

        # Decide: GeoConformal or standard
        use_geo = (
            lat is not None
            and lon is not None
            and self.coords_ is not None
            and len(self.coords_) > 0
        )

        if use_geo:
            q = self._geo_conformal_quantile(lat, lon)
            method = "geo_conformal"
        else:
            q = self.q_hat_
            method = "conformal"

        lower = point - q
        upper = point + q
        width = 2.0 * q

        return UncertaintyResult(
            point_prediction=point,
            lower_bound=lower,
            upper_bound=upper,
            interval_width=width,
            coverage_target=1.0 - self.alpha,
            method=method,
        )

    def predict_batch_with_intervals(
        self,
        features_df: pd.DataFrame,
        locations: list[str],
        lats: np.ndarray | None = None,
        lons: np.ndarray | None = None,
    ) -> list[UncertaintyResult]:
        """Batch prediction with intervals."""
        n = len(features_df)
        results = []
        for i in range(n):
            lat = float(lats[i]) if lats is not None else None
            lon = float(lons[i]) if lons is not None else None
            r = self.predict_with_interval(
                features_df.iloc[[i]],
                location=locations[i] if locations else "",
                lat=lat,
                lon=lon,
            )
            results.append(r)
        return results

    # ── GeoConformal internals ───────────────────────────────────

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
        """Vectorized haversine distance in km."""
        lat1_r = np.radians(lat1)
        lon1_r = np.radians(lon1)
        lat2_r = np.radians(lat2)
        lon2_r = np.radians(lon2)

        dlat = lat2_r - lat1_r
        dlon = lon2_r - lon1_r

        a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1_r) * np.cos(lat2_r) * np.sin(dlon / 2.0) ** 2
        c = 2.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        return 6371.0 * c

    def _geo_conformal_quantile(self, lat: float, lon: float) -> float:
        """Compute a geographically-weighted conformal quantile.

        Uses inverse-distance weighting with a Gaussian kernel so that
        calibration residuals from nearby locations have more influence.
        Falls back to the standard quantile if all distances are large.

        The weighted quantile is computed as described in:
        Barber et al. (2023) "Conformal prediction beyond exchangeability"
        — using normalized weights to build a weighted empirical CDF.
        """
        dists = self._haversine_km(lat, lon, self.coords_[:, 0], self.coords_[:, 1])

        # Gaussian kernel weights: w_i = exp(-d_i^2 / (2 * h^2))
        h = self.geo_bandwidth_km
        weights = np.exp(-0.5 * (dists / h) ** 2)

        # Minimum weight floor to prevent complete zero-out for distant points
        weights = np.maximum(weights, 1e-8)

        # Add the "n+1"-th weight for the test point (conformal guarantee)
        # This corresponds to the uniform weight in standard conformal
        w_test = 1.0 / (len(weights) + 1)

        # Normalize calibration weights so they sum to (1 - w_test)
        w_sum = weights.sum()
        if w_sum < 1e-12:
            # All very far away — fall back to standard quantile
            return self.q_hat_

        norm_weights = weights * (1.0 - w_test) / w_sum

        # Weighted quantile: find smallest q such that
        # sum of weights where score_i <= q  >=  (1 - alpha)
        sorted_idx = np.argsort(self.scores_)
        sorted_scores = self.scores_[sorted_idx]
        sorted_weights = norm_weights[sorted_idx]

        cumw = np.cumsum(sorted_weights)
        # We need cumulative weight + w_test >= 1 - alpha
        # (the test point's nonconformity score is <= q by construction)
        target = 1.0 - self.alpha
        idx = np.searchsorted(cumw + w_test, target)
        idx = min(idx, len(sorted_scores) - 1)

        return float(sorted_scores[idx])

    # ── Persistence ──────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Save calibration data to a JSON file."""
        if not self._calibrated:
            raise RuntimeError("Nothing to save — call calibrate() first")

        path = Path(path)
        data: dict[str, Any] = {
            "alpha": self.alpha,
            "geo_bandwidth_km": self.geo_bandwidth_km,
            "q_hat": self.q_hat_,
            "scores": self.scores_.tolist(),
            "residuals": self.residuals_.tolist(),
        }
        if self.coords_ is not None:
            data["coords"] = self.coords_.tolist()
        if self.locations_ is not None:
            data["locations"] = self.locations_

        path.write_text(json.dumps(data, indent=2))
        logger.info("Saved conformal calibration to %s (%d scores)", path, len(self.scores_))

    @classmethod
    def load(cls, path: str | Path, predictor: Any, alpha: float | None = None) -> ConformalPredictor:
        """Load saved calibration data.

        Parameters
        ----------
        path : str or Path
            Path to the saved JSON calibration file.
        predictor : V14Predictor
            The loaded V15 predictor instance.
        alpha : float, optional
            Override the saved alpha (recomputes quantile).
        """
        path = Path(path)
        data = json.loads(path.read_text())

        saved_alpha = data["alpha"]
        use_alpha = alpha if alpha is not None else saved_alpha

        cp = cls(
            predictor=predictor,
            alpha=use_alpha,
            geo_bandwidth_km=data.get("geo_bandwidth_km", 200.0),
        )

        cp.scores_ = np.array(data["scores"], dtype=float)
        cp.residuals_ = np.array(data["residuals"], dtype=float)

        if "coords" in data:
            cp.coords_ = np.array(data["coords"], dtype=float)
        if "locations" in data:
            cp.locations_ = data["locations"]

        # Recompute quantile (may use overridden alpha)
        n = len(cp.scores_)
        level = (1.0 - cp.alpha) * (1.0 + 1.0 / n)
        cp.q_hat_ = float(np.quantile(cp.scores_, min(level, 1.0)))
        cp._calibrated = True

        logger.info(
            "Loaded conformal calibration: n=%d, alpha=%.2f, q_hat=%.4f",
            n, cp.alpha, cp.q_hat_,
        )
        return cp

    # ── Diagnostics ──────────────────────────────────────────────

    def empirical_coverage(self, X_test: pd.DataFrame, y_test: np.ndarray,
                           locations: list[str] | None = None,
                           lats: np.ndarray | None = None,
                           lons: np.ndarray | None = None) -> dict[str, float]:
        """Compute empirical coverage on a test set.

        Returns dict with overall and per-route coverage fractions.
        """
        y_test = np.asarray(y_test, dtype=float)
        n = len(y_test)
        covered = 0
        seen_covered = 0
        seen_total = 0
        unseen_covered = 0
        unseen_total = 0
        widths = []

        for i in range(n):
            loc = locations[i] if locations else ""
            lat = float(lats[i]) if lats is not None else None
            lon = float(lons[i]) if lons is not None else None

            result = self.predict_with_interval(
                X_test.iloc[[i]], location=loc, lat=lat, lon=lon,
            )
            widths.append(result.interval_width)

            in_interval = result.lower_bound <= y_test[i] <= result.upper_bound
            covered += int(in_interval)

            route = "seen" if self.predictor.is_seen_location(loc) else "unseen"
            if route == "seen":
                seen_total += 1
                seen_covered += int(in_interval)
            else:
                unseen_total += 1
                unseen_covered += int(in_interval)

        stats = {
            "n_test": n,
            "coverage": covered / n if n > 0 else 0.0,
            "target_coverage": 1.0 - self.alpha,
            "mean_interval_width": float(np.mean(widths)),
            "median_interval_width": float(np.median(widths)),
        }
        if seen_total > 0:
            stats["seen_coverage"] = seen_covered / seen_total
            stats["seen_n"] = seen_total
        if unseen_total > 0:
            stats["unseen_coverage"] = unseen_covered / unseen_total
            stats["unseen_n"] = unseen_total

        return stats
