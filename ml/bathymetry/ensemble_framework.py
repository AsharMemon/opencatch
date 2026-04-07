#!/usr/bin/env python3
"""
OpenCatch — Multi-Model Ensemble Framework for SDB

A generic stacking ensemble framework that:
1. Trains N heterogeneous models independently
2. Evaluates each on validation set (spatial CV by lake)
3. Learns optimal ensemble weights via Ridge meta-learner (stacking)
4. Supports diverse model types: XGBoost, RF, KAN, BathyFormer,
   Stumpf, Lyzenga, morphometric, autoencoder-derived
5. Reports per-model and ensemble metrics with depth-stratified analysis
6. Proper spatial CV ensuring no lake appears in both train and val

The meta-learner (Ridge regression) takes all base model predictions as input
and outputs the final depth. This learns when each model is reliable
(e.g., spectral models in clear water, morphometric in turbid).

Usage:
    python ensemble_framework.py \
        --data /data/sdb_preprocessed.parquet \
        --output /data/models/ensemble \
        --models xgboost rf kan stumpf lyzenga morphometric \
        --device cuda

Requirements:
    pip install xgboost scikit-learn pandas pyarrow numpy torch
"""

import argparse
import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ensemble")

EPS = 1e-8


# ── Base Model Interface ─────────────────────────────────────────────

class BaseModel(ABC):
    """Abstract base class for all models in the ensemble."""

    name: str = "base"
    requires_spectral: bool = True
    requires_morphometric: bool = False

    @abstractmethod
    def fit(self, X_train: pd.DataFrame, y_train: np.ndarray,
            X_val: pd.DataFrame, y_val: np.ndarray) -> None:
        """Train the model."""

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Predict depth in meters."""

    def get_feature_importance(self) -> Optional[dict]:
        """Return feature importance dict, if available."""
        return None


# ── Model Implementations ────────────────────────────────────────────

class XGBoostModel(BaseModel):
    name = "xgboost"

    def __init__(self, max_depth: int = 8, lr: float = 0.05, n_rounds: int = 2000):
        self.max_depth = max_depth
        self.lr = lr
        self.n_rounds = n_rounds
        self.model = None
        self._feature_names = None

    def fit(self, X_train, y_train, X_val, y_val):
        import xgboost as xgb

        self._feature_names = X_train.columns.tolist()
        dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=self._feature_names)
        dval = xgb.DMatrix(X_val, label=y_val, feature_names=self._feature_names)

        params = {
            "objective": "reg:squarederror",
            "eval_metric": "rmse",
            "max_depth": self.max_depth,
            "learning_rate": self.lr,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 10,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "tree_method": "hist",
            "seed": 42,
        }

        self.model = xgb.train(
            params, dtrain, num_boost_round=self.n_rounds,
            evals=[(dval, "val")],
            early_stopping_rounds=50, verbose_eval=False,
        )

    def predict(self, X):
        import xgboost as xgb
        dmat = xgb.DMatrix(X, feature_names=self._feature_names)
        return self.model.predict(dmat)

    def get_feature_importance(self):
        if self.model is None:
            return None
        return self.model.get_score(importance_type="gain")


class RandomForestModel(BaseModel):
    name = "rf"

    def __init__(self, n_estimators: int = 500, max_depth: int = 20):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.model = None

    def fit(self, X_train, y_train, X_val, y_val):
        from sklearn.ensemble import RandomForestRegressor

        self.model = RandomForestRegressor(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            min_samples_leaf=10,
            max_features="sqrt",
            n_jobs=-1,
            random_state=42,
        )
        self.model.fit(X_train, y_train)

    def predict(self, X):
        return self.model.predict(X)

    def get_feature_importance(self):
        if self.model is None:
            return None
        return dict(zip(self.model.feature_names_in_, self.model.feature_importances_))


class KANModel(BaseModel):
    name = "kan"

    def __init__(self, epochs: int = 200, batch_size: int = 512, device: str = "cuda"):
        self.epochs = epochs
        self.batch_size = batch_size
        self.device = device
        self.model = None
        self._scaler = StandardScaler()

    def fit(self, X_train, y_train, X_val, y_val):
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        X_tr_scaled = self._scaler.fit_transform(X_train)
        X_va_scaled = self._scaler.transform(X_val)
        n_feat = X_tr_scaled.shape[1]

        X_tr_t = torch.tensor(X_tr_scaled, dtype=torch.float32)
        y_tr_t = torch.tensor(y_train, dtype=torch.float32)
        X_va_t = torch.tensor(X_va_scaled, dtype=torch.float32)

        train_dl = DataLoader(
            TensorDataset(X_tr_t, y_tr_t),
            batch_size=self.batch_size, shuffle=True, drop_last=True,
        )

        # Build model (KAN or MLP fallback)
        try:
            from efficient_kan import KANLinear

            class _KAN(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.layers = nn.ModuleList([
                        KANLinear(n_feat, 128, grid_size=5, spline_order=3),
                        KANLinear(128, 64, grid_size=5, spline_order=3),
                        KANLinear(64, 32, grid_size=5, spline_order=3),
                        KANLinear(32, 1, grid_size=5, spline_order=3),
                    ])

                def forward(self, x):
                    for layer in self.layers:
                        x = layer(x)
                    return torch.softplus(x).squeeze(-1)

        except ImportError:
            class _KAN(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.net = nn.Sequential(
                        nn.Linear(n_feat, 128), nn.BatchNorm1d(128), nn.SiLU(), nn.Dropout(0.1),
                        nn.Linear(128, 64), nn.BatchNorm1d(64), nn.SiLU(), nn.Dropout(0.1),
                        nn.Linear(64, 32), nn.BatchNorm1d(32), nn.SiLU(),
                        nn.Linear(32, 1), nn.Softplus(),
                    )

                def forward(self, x):
                    return self.net(x).squeeze(-1)

        dev = torch.device(self.device if torch.cuda.is_available() else "cpu")
        model = _KAN().to(dev)
        opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=self.epochs)

        best_loss = float("inf")
        best_state = None
        patience_counter = 0

        for epoch in range(self.epochs):
            model.train()
            for xb, yb in train_dl:
                xb, yb = xb.to(dev), yb.to(dev)
                loss = nn.functional.huber_loss(model(xb), yb, delta=2.0)
                opt.zero_grad()
                loss.backward()
                opt.step()
            scheduler.step()

            model.eval()
            with torch.no_grad():
                val_pred = model(X_va_t.to(dev)).cpu().numpy()
                val_loss = np.mean((val_pred - y_val) ** 2)

            if val_loss < best_loss:
                best_loss = val_loss
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter >= 30:
                    break

        if best_state is not None:
            model.load_state_dict(best_state)

        self.model = model.eval()
        self._device = dev

    def predict(self, X):
        import torch
        X_scaled = self._scaler.transform(X)
        X_t = torch.tensor(X_scaled, dtype=torch.float32).to(self._device)
        with torch.no_grad():
            return self.model(X_t).cpu().numpy()


class StumpfModel(BaseModel):
    """Stumpf et al. (2003) log-ratio linear model."""
    name = "stumpf"
    requires_spectral = True

    def __init__(self):
        self.m0 = None
        self.m1 = None

    def fit(self, X_train, y_train, X_val, y_val):
        # Stumpf ratio = n * ln(blue) / ln(green) + c
        if "stumpf_ratio" in X_train.columns:
            ratio = X_train["stumpf_ratio"].values
        elif "blue" in X_train.columns and "green" in X_train.columns:
            ratio = np.log(X_train["blue"].values + EPS) / (
                np.log(X_train["green"].values + EPS) + EPS
            )
        else:
            log.warning("Stumpf: no blue/green columns, using first two features")
            ratio = X_train.iloc[:, 0].values / (X_train.iloc[:, 1].values + EPS)

        # Simple linear fit: depth = m1 * ratio + m0
        from numpy.polynomial.polynomial import polyfit
        self.m0, self.m1 = polyfit(ratio, y_train, 1)

    def predict(self, X):
        if "stumpf_ratio" in X.columns:
            ratio = X["stumpf_ratio"].values
        elif "blue" in X.columns and "green" in X.columns:
            ratio = np.log(X["blue"].values + EPS) / (np.log(X["green"].values + EPS) + EPS)
        else:
            ratio = X.iloc[:, 0].values / (X.iloc[:, 1].values + EPS)

        return self.m0 + self.m1 * ratio


class LyzengaModel(BaseModel):
    """Lyzenga (1978, 1985) multi-band log-linear model."""
    name = "lyzenga"
    requires_spectral = True

    def __init__(self):
        self.model = None

    def fit(self, X_train, y_train, X_val, y_val):
        from sklearn.linear_model import Ridge as _Ridge

        # Use Lyzenga depth-invariant indices if available, else log-bands
        lyzenga_cols = [c for c in X_train.columns if c.startswith("lyzenga_")]
        if lyzenga_cols:
            features = X_train[lyzenga_cols]
        else:
            log_cols = [c for c in X_train.columns if c.startswith("log_")]
            if log_cols:
                features = X_train[log_cols]
            else:
                band_cols = ["blue", "green", "red"]
                avail = [c for c in band_cols if c in X_train.columns]
                features = np.log(X_train[avail].clip(lower=EPS))

        self._feature_cols = features.columns.tolist() if isinstance(features, pd.DataFrame) else None
        self.model = _Ridge(alpha=1.0)
        self.model.fit(features, y_train)

    def predict(self, X):
        if self._feature_cols:
            avail = [c for c in self._feature_cols if c in X.columns]
            if len(avail) == len(self._feature_cols):
                return self.model.predict(X[self._feature_cols])

        lyzenga_cols = [c for c in X.columns if c.startswith("lyzenga_")]
        if lyzenga_cols:
            return self.model.predict(X[lyzenga_cols])
        log_cols = [c for c in X.columns if c.startswith("log_")]
        if log_cols:
            return self.model.predict(X[log_cols])
        band_cols = [c for c in ["blue", "green", "red"] if c in X.columns]
        return self.model.predict(np.log(X[band_cols].clip(lower=EPS)))


class MorphometricModel(BaseModel):
    """Morphometric-only model using lake shape features."""
    name = "morphometric"
    requires_spectral = False
    requires_morphometric = True

    def __init__(self):
        self.model = None
        self._feature_cols = None

    def fit(self, X_train, y_train, X_val, y_val):
        import xgboost as xgb

        # Select only morphometric columns
        morph_cols = [
            c for c in X_train.columns
            if any(
                kw in c.lower()
                for kw in [
                    "area", "perim", "sdi", "circular", "elong", "slope", "elev",
                    "lat", "lon", "wshd", "reservoir", "natural", "vol", "discharge",
                    "res_time", "compact", "fractal", "convex",
                ]
            )
        ]

        if not morph_cols:
            log.warning("MorphometricModel: no morphometric columns found, using all")
            morph_cols = X_train.columns.tolist()

        self._feature_cols = morph_cols
        log.info(f"  Morphometric features ({len(morph_cols)}): {morph_cols[:10]}...")

        dtrain = xgb.DMatrix(X_train[morph_cols], label=y_train,
                             feature_names=morph_cols)
        dval = xgb.DMatrix(X_val[morph_cols], label=y_val,
                           feature_names=morph_cols)

        params = {
            "objective": "reg:squarederror",
            "eval_metric": "rmse",
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "seed": 42,
        }

        self.model = xgb.train(
            params, dtrain, num_boost_round=1000,
            evals=[(dval, "val")],
            early_stopping_rounds=50, verbose_eval=False,
        )

    def predict(self, X):
        import xgboost as xgb
        dmat = xgb.DMatrix(X[self._feature_cols], feature_names=self._feature_cols)
        return self.model.predict(dmat)


class AutoEncoderModel(BaseModel):
    """Autoencoder-derived depth model: encode spectral + reconstruct -> depth head."""
    name = "autoencoder"

    def __init__(self, epochs: int = 150, device: str = "cuda"):
        self.epochs = epochs
        self.device = device
        self.model = None
        self._scaler = StandardScaler()

    def fit(self, X_train, y_train, X_val, y_val):
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset

        X_tr = self._scaler.fit_transform(X_train)
        X_va = self._scaler.transform(X_val)
        n_feat = X_tr.shape[1]

        X_tr_t = torch.tensor(X_tr, dtype=torch.float32)
        y_tr_t = torch.tensor(y_train, dtype=torch.float32)
        X_va_t = torch.tensor(X_va, dtype=torch.float32)

        class _AEDepth(nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = nn.Sequential(
                    nn.Linear(n_feat, 64), nn.BatchNorm1d(64), nn.SiLU(),
                    nn.Linear(64, 32), nn.BatchNorm1d(32), nn.SiLU(),
                    nn.Linear(32, 16),
                )
                self.decoder = nn.Sequential(
                    nn.Linear(16, 32), nn.SiLU(),
                    nn.Linear(32, 64), nn.SiLU(),
                    nn.Linear(64, n_feat),
                )
                self.depth_head = nn.Sequential(
                    nn.Linear(16, 16), nn.SiLU(),
                    nn.Linear(16, 1), nn.Softplus(),
                )

            def forward(self, x):
                z = self.encoder(x)
                recon = self.decoder(z)
                depth = self.depth_head(z).squeeze(-1)
                return depth, recon

        dev = torch.device(self.device if torch.cuda.is_available() else "cpu")
        model = _AEDepth().to(dev)
        opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
        train_dl = DataLoader(
            TensorDataset(X_tr_t, y_tr_t),
            batch_size=512, shuffle=True, drop_last=True,
        )

        best_loss = float("inf")
        best_state = None

        for epoch in range(self.epochs):
            model.train()
            for xb, yb in train_dl:
                xb, yb = xb.to(dev), yb.to(dev)
                depth_pred, recon = model(xb)
                loss_depth = nn.functional.huber_loss(depth_pred, yb, delta=2.0)
                loss_recon = nn.functional.mse_loss(recon, xb)
                loss = loss_depth + 0.1 * loss_recon
                opt.zero_grad()
                loss.backward()
                opt.step()

            model.eval()
            with torch.no_grad():
                val_depth, _ = model(X_va_t.to(dev))
                val_loss = np.mean((val_depth.cpu().numpy() - y_val) ** 2)

            if val_loss < best_loss:
                best_loss = val_loss
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

        if best_state:
            model.load_state_dict(best_state)

        self.model = model.eval()
        self._device = dev

    def predict(self, X):
        import torch
        X_scaled = self._scaler.transform(X)
        X_t = torch.tensor(X_scaled, dtype=torch.float32).to(self._device)
        with torch.no_grad():
            depth, _ = self.model(X_t)
            return depth.cpu().numpy()


# ── Pre-Trained Model Wrappers ───────────────────────────────────────

class HybridSonarModel(BaseModel):
    """Wrapper for the pre-trained hybrid sonar+spectral model.

    Loads artifacts from train_hybrid_sonar.py output directory and
    runs inference through the trained pipeline.
    """

    name = "hybrid_sonar"
    requires_spectral = True

    def __init__(self, model_dir: str = "/data/models/hybrid_sonar"):
        self.model_dir = Path(model_dir)
        self._direct_model = None
        self._log_model = None
        self._config = None

    def fit(self, X_train, y_train, X_val, y_val):
        """Load pre-trained models instead of training from scratch."""
        import pickle

        config_path = self.model_dir / "config.json"
        if config_path.exists():
            with open(config_path) as f:
                self._config = json.load(f)

        # Find best direct model
        for path in sorted(self.model_dir.glob("stage2_direct_*.pkl")):
            try:
                with open(path, "rb") as f:
                    self._direct_model = pickle.load(f)
                log.info(f"  HybridSonarModel loaded: {path.name}")
                break
            except Exception as e:
                log.warning(f"  Could not load {path}: {e}")

        # Find log-depth model
        for path in sorted(self.model_dir.glob("stage2_log_depth_*.pkl")):
            try:
                with open(path, "rb") as f:
                    self._log_model = pickle.load(f)
                log.info(f"  HybridSonarModel loaded log model: {path.name}")
                break
            except Exception:
                pass

        if self._direct_model is None:
            log.warning("  HybridSonarModel: no pre-trained model found, will return NaN")

    def predict(self, X):
        if self._direct_model is None:
            return np.full(len(X), np.nan)

        direct = np.clip(self._direct_model.predict(X), 0, 60)

        if self._log_model is not None:
            log_pred = np.clip(np.expm1(self._log_model.predict(X)), 0, 60)
            alpha = np.clip(direct / 20.0, 0.0, 0.7)
            return (1.0 - alpha) * direct + alpha * log_pred
        return direct


class TerrainPriorModel(BaseModel):
    """Wrapper for the terrain depth prior model.

    Predicts depth from surrounding DEM terrain features. Works for
    turbid and deep lakes where spectral models fail.
    """

    name = "terrain_prior"
    requires_spectral = False
    requires_morphometric = True

    def __init__(self, model_dir: str = "/data/models/terrain_depth"):
        self.model_dir = Path(model_dir)
        self._model = None

    def fit(self, X_train, y_train, X_val, y_val):
        """Load pre-trained terrain U-Net model."""
        model_path = self.model_dir / "best_model.pt"
        if model_path.exists():
            try:
                import torch
                from terrain_depth_model import TerrainDepthUNet
                self._model = TerrainDepthUNet()
                self._model.load_state_dict(torch.load(model_path, map_location="cpu"))
                self._model.eval()
                log.info(f"  TerrainPriorModel loaded: {model_path}")
            except Exception as e:
                log.warning(f"  TerrainPriorModel load failed: {e}")
        else:
            log.warning(f"  TerrainPriorModel: no model at {model_path}")

    def predict(self, X):
        if self._model is None:
            return np.full(len(X), np.nan)
        import torch
        with torch.no_grad():
            X_t = torch.tensor(X.values if hasattr(X, 'values') else X, dtype=torch.float32)
            return np.clip(self._model(X_t).numpy(), 0, 60)


class TransferLearningModel(BaseModel):
    """Wrapper for K-donor morphometric transfer learning.

    Uses morphometric similarity to transfer depth profiles from
    surveyed lakes to unsurveyed ones.
    """

    name = "transfer_learning"
    requires_spectral = False
    requires_morphometric = True

    def __init__(self, k: int = 5):
        self.k = k
        self._donor_depths = None
        self._nn = None
        self._scaler = None

    def fit(self, X_train, y_train, X_val, y_val):
        """Build donor lake index from training data."""
        from sklearn.neighbors import NearestNeighbors

        self._scaler = StandardScaler()
        X_scaled = self._scaler.fit_transform(X_train)
        self._nn = NearestNeighbors(n_neighbors=min(self.k, len(X_train)), metric="euclidean")
        self._nn.fit(X_scaled)
        self._donor_depths = y_train.copy()
        log.info(f"  TransferLearningModel: {len(X_train)} donor lakes, k={self.k}")

    def predict(self, X):
        if self._nn is None or self._scaler is None:
            return np.full(len(X), np.nan)

        X_scaled = self._scaler.transform(X.values if hasattr(X, 'values') else X)
        dists, inds = self._nn.kneighbors(X_scaled)
        weights = 1.0 / (dists + 1e-3)
        weights = weights / weights.sum(axis=1, keepdims=True)
        return np.sum(weights * self._donor_depths[inds], axis=1)


# ── Model Registry ───────────────────────────────────────────────────

MODEL_REGISTRY = {
    "xgboost":          lambda **kw: XGBoostModel(**kw),
    "rf":               lambda **kw: RandomForestModel(**kw),
    "kan":              lambda **kw: KANModel(**kw),
    "stumpf":           lambda **kw: StumpfModel(),
    "lyzenga":          lambda **kw: LyzengaModel(),
    "morphometric":     lambda **kw: MorphometricModel(),
    "autoencoder":      lambda **kw: AutoEncoderModel(**kw),
    "hybrid_sonar":     lambda **kw: HybridSonarModel(**kw),
    "terrain_prior":    lambda **kw: TerrainPriorModel(**kw),
    "transfer_learning": lambda **kw: TransferLearningModel(**kw),
}


# ── Meta-Learner (Stacking) ─────────────────────────────────────────

class StackingEnsemble:
    """
    Stacking ensemble that combines base model predictions via a meta-learner.

    The meta-learner (Ridge regression by default) learns the optimal
    blending of base model predictions, effectively learning which
    model is best in different conditions.
    """

    def __init__(
        self,
        base_models: list[BaseModel],
        meta_learner=None,
        clip_range: tuple[float, float] = (0.0, 100.0),
    ):
        self.base_models = base_models
        self.meta_learner = meta_learner or Ridge(alpha=1.0)
        self.clip_range = clip_range
        self._meta_scaler = StandardScaler()
        self.weights_ = None

    def fit_meta(
        self,
        oof_predictions: dict[str, np.ndarray],
        y_true: np.ndarray,
    ):
        """
        Fit the meta-learner on out-of-fold predictions from base models.

        Args:
            oof_predictions: {model_name: oof_pred_array} for each base model.
            y_true: True depth values.
        """
        # Stack OOF predictions as meta-features
        names = sorted(oof_predictions.keys())
        meta_X = np.column_stack([oof_predictions[n] for n in names])

        # Remove rows where any model has NaN (missing folds)
        valid = ~np.any(np.isnan(meta_X), axis=1)
        meta_X = meta_X[valid]
        y = y_true[valid]

        meta_X_scaled = self._meta_scaler.fit_transform(meta_X)
        self.meta_learner.fit(meta_X_scaled, y)

        # Store learned weights for interpretability
        if hasattr(self.meta_learner, "coef_"):
            self.weights_ = dict(zip(names, self.meta_learner.coef_))
            log.info(f"Meta-learner weights: {self.weights_}")

    def predict_meta(
        self,
        predictions: dict[str, np.ndarray],
    ) -> np.ndarray:
        """Predict using fitted meta-learner on base model predictions."""
        names = sorted(predictions.keys())
        meta_X = np.column_stack([predictions[n] for n in names])
        meta_X_scaled = self._meta_scaler.transform(meta_X)
        pred = self.meta_learner.predict(meta_X_scaled)
        return np.clip(pred, *self.clip_range)

    def predict_simple_average(
        self,
        predictions: dict[str, np.ndarray],
    ) -> np.ndarray:
        """Simple average of base model predictions (no meta-learner)."""
        preds = np.stack(list(predictions.values()), axis=0)
        return np.clip(np.mean(preds, axis=0), *self.clip_range)


# ── Evaluation ───────────────────────────────────────────────────────

def evaluate_predictions(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    label: str = "",
) -> dict:
    """Comprehensive evaluation metrics."""
    valid = ~(np.isnan(y_true) | np.isnan(y_pred))
    yt, yp = y_true[valid], y_pred[valid]

    rmse = np.sqrt(mean_squared_error(yt, yp))
    mae = mean_absolute_error(yt, yp)
    r2 = r2_score(yt, yp)
    median_ae = float(np.median(np.abs(yt - yp)))
    bias = float(np.mean(yp - yt))
    mape = float(np.mean(np.abs(yt - yp) / np.clip(yt, 0.1, None)) * 100)

    metrics = {
        "rmse_m": float(rmse),
        "mae_m": float(mae),
        "median_ae_m": median_ae,
        "r2": float(r2),
        "bias_m": bias,
        "mape_pct": mape,
        "n": int(valid.sum()),
    }

    if label:
        log.info(
            f"  {label:20s}: R2={r2:.4f}, RMSE={rmse:.2f}m, "
            f"MAE={mae:.2f}m, Bias={bias:+.2f}m"
        )

    return metrics


def evaluate_depth_bins(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> dict:
    """Per-depth-bin metrics."""
    bins = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 50)]
    results = {}

    for lo, hi in bins:
        mask = (y_true >= lo) & (y_true < hi)
        n = mask.sum()
        if n < 10:
            continue
        rmse = np.sqrt(np.mean((y_true[mask] - y_pred[mask]) ** 2))
        bias = float(np.mean(y_pred[mask] - y_true[mask]))
        r2 = r2_score(y_true[mask], y_pred[mask]) if n > 2 else float("nan")
        results[f"{lo}-{hi}m"] = {
            "n": int(n), "rmse_m": float(rmse), "bias_m": bias, "r2": float(r2),
        }
        log.info(f"    {lo:2d}-{hi:2d}m (n={n:5d}): RMSE={rmse:.2f}m, R2={r2:.4f}")

    return results


# ── Spatial CV Split ─────────────────────────────────────────────────

def get_spatial_groups(df: pd.DataFrame) -> pd.Series:
    """Get group labels for spatial CV (by lake)."""
    for col in ["lake_id", "Hylak_id", "hylak_id", "permanent_id"]:
        if col in df.columns:
            return df[col]

    # Fallback: geographic grid
    lat = df.get("lat", df.get("Pour_lat", df.get("lake_lat", pd.Series(0, index=df.index))))
    lon = df.get("lon", df.get("Pour_long", df.get("lake_lon", pd.Series(0, index=df.index))))
    return (np.round(lat * 2) * 10000 + np.round(lon * 2)).astype(int)


# ── Main Training Pipeline ───────────────────────────────────────────

def run_ensemble(
    data_path: Path,
    output_dir: Path,
    model_names: list[str],
    n_folds: int = 5,
    device: str = "cuda",
):
    """
    Full ensemble training pipeline:
    1. Load data
    2. Spatial CV with N base models
    3. Collect OOF predictions
    4. Train meta-learner on OOF
    5. Report per-model and ensemble metrics
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    if data_path.suffix == ".parquet":
        df = pd.read_parquet(data_path)
    else:
        df = pd.read_csv(data_path)

    log.info(f"Loaded {len(df):,} rows from {data_path.name}")

    # Find depth column
    depth_col = None
    for col in ["depth_m", "max_depth_m", "Depth_avg"]:
        if col in df.columns:
            depth_col = col
            break
    if depth_col is None:
        raise ValueError(f"No depth column found in {list(df.columns)}")

    df = df.dropna(subset=[depth_col])
    df = df[df[depth_col] > 0]
    y = df[depth_col].values
    log.info(f"Training set: {len(df):,} samples, depth [{y.min():.1f}, {y.max():.1f}]m")

    # Feature columns (everything that's not target/metadata)
    exclude_cols = {depth_col, "lake_id", "Hylak_id", "hylak_id", "permanent_id",
                    "geometry", "lake_name", "state", "country"}
    feature_cols = [c for c in df.columns if c not in exclude_cols and df[c].dtype in [
        np.float64, np.float32, np.int64, np.int32, float, int
    ]]
    X = df[feature_cols].fillna(df[feature_cols].median())
    log.info(f"Features ({len(feature_cols)}): {feature_cols[:15]}...")

    # Spatial CV splits
    groups = get_spatial_groups(df)
    gkf = GroupKFold(n_splits=n_folds)
    cv_splits = list(gkf.split(X, y, groups))

    # Instantiate models
    base_models = []
    for name in model_names:
        if name not in MODEL_REGISTRY:
            log.warning(f"Unknown model '{name}', skipping")
            continue
        kwargs = {}
        if name in ("kan", "autoencoder"):
            kwargs["device"] = device
        base_models.append(MODEL_REGISTRY[name](**kwargs))
    log.info(f"Base models: {[m.name for m in base_models]}")

    # Collect OOF predictions for each model
    oof_predictions = {m.name: np.full(len(y), np.nan) for m in base_models}
    per_model_metrics = {m.name: [] for m in base_models}

    for fold_i, (train_idx, val_idx) in enumerate(cv_splits):
        log.info(f"\n{'='*50} Fold {fold_i + 1}/{n_folds} {'='*50}")
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]
        log.info(f"  Train: {len(train_idx):,}, Val: {len(val_idx):,}")

        for model in base_models:
            t0 = time.time()
            log.info(f"  Training {model.name}...")

            try:
                model.fit(X_train, y_train, X_val, y_val)
                val_pred = model.predict(X_val)
                val_pred = np.clip(val_pred, 0, y.max() * 1.5)
                oof_predictions[model.name][val_idx] = val_pred

                elapsed = time.time() - t0
                fold_m = evaluate_predictions(y_val, val_pred, label=f"{model.name} F{fold_i+1}")
                fold_m["time_s"] = elapsed
                per_model_metrics[model.name].append(fold_m)

            except Exception as e:
                log.error(f"  {model.name} failed on fold {fold_i+1}: {e}")
                per_model_metrics[model.name].append({"error": str(e)})

    # ── Per-Model OOF Summary ──
    log.info(f"\n{'='*60}")
    log.info("PER-MODEL OOF RESULTS")
    log.info(f"{'='*60}")

    model_oof_metrics = {}
    for model in base_models:
        oof = oof_predictions[model.name]
        valid = ~np.isnan(oof)
        if valid.sum() < 10:
            log.warning(f"  {model.name}: too few valid predictions ({valid.sum()})")
            continue

        m = evaluate_predictions(y[valid], oof[valid], label=model.name)
        evaluate_depth_bins(y[valid], oof[valid])
        model_oof_metrics[model.name] = m

    # ── Train Meta-Learner ──
    log.info(f"\n{'='*60}")
    log.info("STACKING ENSEMBLE (Meta-Learner)")
    log.info(f"{'='*60}")

    # Filter to models that produced valid predictions
    valid_models = [m for m in base_models if not np.all(np.isnan(oof_predictions[m.name]))]
    valid_oof = {m.name: oof_predictions[m.name] for m in valid_models}

    ensemble = StackingEnsemble(valid_models)
    ensemble.fit_meta(valid_oof, y)

    # Evaluate ensemble (on samples where all models have predictions)
    all_valid = np.ones(len(y), dtype=bool)
    for name, oof in valid_oof.items():
        all_valid &= ~np.isnan(oof)

    if all_valid.sum() > 10:
        # Stacking prediction
        stack_preds = {n: oof[all_valid] for n, oof in valid_oof.items()}
        ensemble_pred = ensemble.predict_meta(stack_preds)
        m_stack = evaluate_predictions(y[all_valid], ensemble_pred, label="STACKING")
        evaluate_depth_bins(y[all_valid], ensemble_pred)

        # Simple average comparison
        avg_pred = ensemble.predict_simple_average(stack_preds)
        m_avg = evaluate_predictions(y[all_valid], avg_pred, label="SIMPLE AVG")
    else:
        log.warning("Not enough overlapping predictions for ensemble evaluation")
        m_stack = {}
        m_avg = {}

    # ── Save Results ──
    results = {
        "per_model": model_oof_metrics,
        "ensemble_stacking": m_stack,
        "ensemble_average": m_avg,
        "meta_weights": ensemble.weights_,
        "n_samples": len(df),
        "n_folds": n_folds,
        "feature_cols": feature_cols,
        "models_used": [m.name for m in valid_models],
    }

    with open(output_dir / "ensemble_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Save OOF predictions
    oof_df = pd.DataFrame(valid_oof)
    oof_df["y_true"] = y
    oof_df.to_parquet(output_dir / "oof_predictions.parquet", index=False)

    # Save meta-learner
    import joblib
    joblib.dump(ensemble, output_dir / "stacking_ensemble.joblib")

    log.info(f"\nAll results saved to {output_dir}")
    return ensemble, results


def main():
    parser = argparse.ArgumentParser(description="Multi-model ensemble for SDB")
    parser.add_argument("--data", type=str, required=True)
    parser.add_argument("--output", type=str, default="/data/models/ensemble")
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument(
        "--models", nargs="+",
        default=["xgboost", "rf", "stumpf", "lyzenga"],
        help="Models to include in ensemble",
    )
    parser.add_argument("--device", type=str, default="cuda")
    args = parser.parse_args()

    run_ensemble(
        data_path=Path(args.data),
        output_dir=Path(args.output),
        model_names=args.models,
        n_folds=args.n_folds,
        device=args.device,
    )


if __name__ == "__main__":
    main()
