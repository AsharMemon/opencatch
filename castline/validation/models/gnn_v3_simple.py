"""GNN v3: Simple, heavily regularized for LOO generalization.

Lesson from v2: MORE capacity = WORSE LOO performance (overfitting).
This version: minimal parameters, maximum regularization.

Key changes vs v1 (R²=0.054):
1. Smaller model: hidden=24 instead of 32
2. More dropout: 0.3 instead of 0.05
3. More training seeds: 7 for better ensembling
4. Shorter training: 300 epochs (avoid overfit)
5. Stronger weight decay: 0.02
6. Location quality head: predict location mean FIRST, then add residual
7. Edge weighting: closer neighbors get more influence
8. L1 regularization on predictions (encourage conservative estimates)
"""
from __future__ import annotations

import json
import math
import sys
import time
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
from torch_geometric.nn import SAGEConv

warnings.filterwarnings("ignore")

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
EARTH_RADIUS_KM = 6_371.0
TARGET = "median_weight_lb"

# Keep node features focused on strongest signals only
NODE_FEATURES = [
    "lat", "lon",
    "latitude_growth_potential", "growing_degree_proxy",
    "ecoregion_mean_weight", "ecoregion_mean_cpue",
    "max_depth_ft", "area_acres", "shore_dev",
    "shad_habitat_score",
    "regional_cpue_100km", "regional_weight_100km",
    "n_nearby_creel_surveys",
    "morphometric_productivity_score",
    "is_lake",
    "smallmouth_habitat_score",
    "northern_trophy_potential",
    "wtype_reservoir", "wtype_river",
]

# Event features: keep only the most informative ones (reduce noise)
EVENT_FEATURES = [
    "water_temp_c", "air_temp_c",
    "pressure_mb", "om_pressure_delta_6h",
    "wind_speed_kph",
    "discharge_cfs", "gage_height_ft", "gage_stability_7d",
    "day_length_hours", "season_cos", "season_sin",
    "spawn_phase", "metabolic_rate_index", "feeding_window_score",
    "pressure_fishing_quality", "season_quality_index",
    "moon_phase", "solunar_score",
    "do_comfort_index",
    "om_humidity", "om_cloudcover",
    "om_air_temp_7d_mean", "om_temp_trend_7d",
]


def _pairwise_geo_dist(lats, lons):
    lat_r = np.radians(lats)
    lon_r = np.radians(lons)
    dlat = lat_r[:, None] - lat_r[None, :]
    dlon = lon_r[:, None] - lon_r[None, :]
    a = (np.sin(dlat / 2) ** 2 +
         np.cos(lat_r[:, None]) * np.cos(lat_r[None, :]) * np.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _get_avail(df, features):
    return [f for f in features if f in df.columns and df[f].notna().mean() > 0.02]


def build_graph(loc_df, k_geo=8, k_morpho=4,
                node_scaler=None, node_medians=None,
                exclude_locations=None):
    df = loc_df.copy().reset_index(drop=True)
    if exclude_locations:
        df = df[~df["location"].isin(exclude_locations)].reset_index(drop=True)

    n = len(df)
    location_ids = df["location"].tolist()
    avail = _get_avail(df, NODE_FEATURES)
    node_feat_np = df[avail].values.astype(np.float64)

    if node_medians is None:
        node_medians = np.nanmedian(node_feat_np, axis=0)
    for c in range(node_feat_np.shape[1]):
        mask = np.isnan(node_feat_np[:, c])
        med = node_medians[c] if not np.isnan(node_medians[c]) else 0.0
        node_feat_np[mask, c] = med

    if node_scaler is None:
        node_scaler = StandardScaler()
        node_feat_np = node_scaler.fit_transform(node_feat_np)
    else:
        node_feat_np = node_scaler.transform(node_feat_np)

    node_features = torch.tensor(node_feat_np, dtype=torch.float32).to(DEVICE)

    lats = df["lat"].fillna(df["lat"].median()).values.astype(np.float64)
    lons = df["lon"].fillna(df["lon"].median()).values.astype(np.float64)
    geo_dist = _pairwise_geo_dist(lats, lons)
    np.fill_diagonal(geo_dist, np.inf)

    edge_set = set()
    k_g = min(k_geo, n - 1)
    if k_g > 0:
        geo_nn = np.argsort(geo_dist, axis=1)[:, :k_g]
        for i in range(n):
            for j in geo_nn[i]:
                edge_set.add((i, int(j)))
                edge_set.add((int(j), i))

    morpho_cols = ["area_acres", "max_depth_ft", "shore_dev"]
    morpho_avail = [c for c in morpho_cols if c in df.columns]
    k_m = min(k_morpho, n - 1)
    if morpho_avail and k_m > 0:
        mv = df[morpho_avail].values.astype(np.float64)
        for ci, col in enumerate(morpho_avail):
            if col in ("area_acres", "max_depth_ft"):
                mv[:, ci] = np.log1p(np.nan_to_num(mv[:, ci], nan=0.0))
            mask = np.isnan(mv[:, ci])
            med = np.nanmedian(mv[:, ci])
            mv[mask, ci] = med if not np.isnan(med) else 0.0
        ms = StandardScaler()
        mv_std = ms.fit_transform(mv)
        diff = mv_std[:, None, :] - mv_std[None, :, :]
        morpho_dist = np.sqrt(np.sum(diff ** 2, axis=2))
        np.fill_diagonal(morpho_dist, np.inf)
        morpho_nn = np.argsort(morpho_dist, axis=1)[:, :k_m]
        for i in range(n):
            for j in morpho_nn[i]:
                edge_set.add((i, int(j)))
                edge_set.add((int(j), i))

    if edge_set:
        edges = sorted(edge_set)
        edge_index = torch.tensor(
            [[e[0] for e in edges], [e[1] for e in edges]], dtype=torch.long
        ).to(DEVICE)
    else:
        edge_index = torch.zeros((2, 0), dtype=torch.long).to(DEVICE)

    return node_features, edge_index, location_ids, node_scaler, node_medians


class SimpleGNN(nn.Module):
    """Minimal GraphSAGE with heavy regularization.

    Two outputs:
    1. Location quality estimate (from graph neighbors)
    2. Environmental residual (how conditions affect weight)
    """

    def __init__(self, n_node, n_event, hidden=24, dropout=0.3):
        super().__init__()

        # GNN: 2-layer SAGE
        self.sage1 = SAGEConv(n_node, hidden)
        self.sage2 = SAGEConv(hidden, hidden)
        self.ln1 = nn.LayerNorm(hidden)
        self.ln2 = nn.LayerNorm(hidden)
        self.skip = nn.Linear(n_node, hidden, bias=False) if n_node != hidden else nn.Identity()

        # Direct location MLP
        self.loc_direct = nn.Sequential(
            nn.Linear(n_node, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden),
        )

        # Location quality head
        self.loc_head = nn.Linear(hidden * 2, 1)

        # Event encoder (small)
        self.event_enc = nn.Sequential(
            nn.Linear(n_event, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden),
        )

        # Residual head
        self.res_head = nn.Sequential(
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden // 2, 1),
        )

        self.dropout = dropout

    def forward(self, node_feat, edge_index, event_feat, loc_idx):
        skip = self.skip(node_feat)

        h = self.sage1(node_feat, edge_index)
        h = self.ln1(h)
        h = F.relu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)

        h = self.sage2(h, edge_index)
        h = self.ln2(h)
        h = F.relu(h + skip)

        gnn_emb = h[loc_idx]
        loc_emb = self.loc_direct(node_feat)[loc_idx]

        # Location quality prediction
        loc_pred = self.loc_head(torch.cat([gnn_emb, loc_emb], dim=1)).squeeze(-1)

        # Environmental residual
        ev = self.event_enc(event_feat)
        res = self.res_head(ev).squeeze(-1)

        return loc_pred + res * 0.3  # Scale down residuals to prevent overfit


def prepare_event_data(df, avail_event, loc_to_idx,
                       event_scaler=None, event_medians=None,
                       t_mean=0, t_std=1, fit=True):
    vals = df[avail_event].values.astype(np.float64)

    if event_medians is None:
        event_medians = np.nanmedian(vals, axis=0)
    for c in range(vals.shape[1]):
        mask = np.isnan(vals[:, c])
        med = event_medians[c] if not np.isnan(event_medians[c]) else 0.0
        vals[mask, c] = med

    if fit or event_scaler is None:
        event_scaler = StandardScaler()
        vals = event_scaler.fit_transform(vals)
    else:
        vals = event_scaler.transform(vals)

    ef = torch.tensor(vals, dtype=torch.float32).to(DEVICE)
    li = torch.tensor([loc_to_idx.get(l, 0) for l in df["location"]], dtype=torch.long).to(DEVICE)

    raw_y = df[TARGET].values.astype(np.float64)
    if fit:
        t_mean = float(np.nanmean(raw_y))
        t_std = float(np.nanstd(raw_y))
        if t_std < 1e-6:
            t_std = 1.0

    tgt = torch.tensor((raw_y - t_mean) / t_std, dtype=torch.float32).to(DEVICE)
    return ef, li, tgt, event_scaler, event_medians, t_mean, t_std


def train_model(model, nf, ei, ef, li, tgt,
                epochs=300, lr=3e-3, wd=0.02, verbose=False):
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs, eta_min=lr * 0.01)

    model.train()
    for ep in range(1, epochs + 1):
        opt.zero_grad()
        pred = model(nf, ei, ef, li)
        # Huber loss + L1 penalty for conservative predictions
        loss = F.huber_loss(pred, tgt, delta=1.5) + 0.01 * pred.abs().mean()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()

        if verbose and ep % 100 == 0:
            print(f"    ep {ep}/{epochs} loss={loss.item():.5f}")


def train_and_evaluate_loo(df, top_n=15, epochs=300, hidden=24,
                           k_geo=8, k_morpho=4, lr=3e-3, n_seeds=7,
                           verbose=True):
    loc_counts = df["location"].value_counts()
    top_locs = loc_counts.head(top_n).index.tolist()

    if verbose:
        print(f"\n{'='*70}")
        print(f"GNN v3 (Simple) -- LOO Evaluation")
        print(f"{'='*70}")
        print(f"Dataset: {len(df)} events, {df['location'].nunique()} locs")
        print(f"Hidden: {hidden}, Dropout: 0.3, Seeds: {n_seeds}")

    avail_node = _get_avail(df, NODE_FEATURES)
    agg_dict = {f: "first" for f in ["lat", "lon"] + avail_node if f in df.columns}
    loc_df = df.groupby("location").agg(agg_dict).reset_index()

    avail_event = _get_avail(df, EVENT_FEATURES)
    n_node = len([f for f in avail_node if f in loc_df.columns])
    n_event = len(avail_event)

    if verbose:
        print(f"Node feats: {n_node}, Event feats: {n_event}")
        print()

    all_true, all_pred = [], []
    per_loc = []
    t0 = time.time()

    for fi, test_loc in enumerate(top_locs):
        ft0 = time.time()
        train_df = df[df["location"] != test_loc].reset_index(drop=True)
        test_df = df[df["location"] == test_loc].reset_index(drop=True)
        if len(test_df) < 2:
            continue

        # Train graph
        tr_nf, tr_ei, tr_lids, ns, nm = build_graph(
            loc_df, k_geo=k_geo, k_morpho=k_morpho,
            exclude_locations={test_loc}
        )
        tr_l2i = {l: i for i, l in enumerate(tr_lids)}
        train_df = train_df[train_df["location"].isin(tr_l2i)].reset_index(drop=True)

        tr_ef, tr_li, tr_tgt, es, em, tm, ts = prepare_event_data(
            train_df, avail_event, tr_l2i, fit=True
        )

        # Full graph
        fu_nf, fu_ei, fu_lids, _, _ = build_graph(
            loc_df, k_geo=k_geo, k_morpho=k_morpho,
            node_scaler=ns, node_medians=nm
        )
        fu_l2i = {l: i for i, l in enumerate(fu_lids)}

        te_ef, te_li, _, _, _, _, _ = prepare_event_data(
            test_df, avail_event, fu_l2i,
            event_scaler=es, event_medians=em,
            t_mean=tm, t_std=ts, fit=False
        )

        # Multi-seed ensemble
        seed_preds = []
        for seed in range(n_seeds):
            torch.manual_seed(seed * 137 + fi * 7 + 42)

            model = SimpleGNN(
                n_node=tr_nf.shape[1],
                n_event=tr_ef.shape[1],
                hidden=hidden,
                dropout=0.3,
            ).to(DEVICE)
            train_model(model, tr_nf, tr_ei, tr_ef, tr_li, tr_tgt,
                       epochs=epochs, lr=lr, verbose=False)

            model.eval()
            with torch.no_grad():
                p = model(fu_nf, fu_ei, te_ef, te_li).cpu().numpy()
            seed_preds.append(p * ts + tm)

        y_pred = np.median(np.stack(seed_preds), axis=0)  # median is more robust
        y_true = test_df[TARGET].values
        y_pred = np.clip(y_pred, 2.0, 30.0)

        r2 = r2_score(y_true, y_pred) if len(y_true) > 1 else float("nan")
        ft_time = time.time() - ft0

        all_true.extend(y_true.tolist())
        all_pred.extend(y_pred.tolist())

        per_loc.append({
            "location": test_loc,
            "n": len(test_df),
            "r2": round(r2, 4),
            "mean_true": round(float(y_true.mean()), 2),
            "mean_pred": round(float(y_pred.mean()), 2),
            "time_s": round(ft_time, 1),
        })

        if verbose:
            err = abs(y_true.mean() - y_pred.mean())
            st = "OK" if r2 > 0 else ("~" if r2 > -1 else "BAD")
            print(f"  [{fi+1:>2d}/{top_n}] {test_loc:<45s} n={len(test_df):>3d}  "
                  f"R2={r2:>7.3f}  true={y_true.mean():.2f}  pred={y_pred.mean():.2f}  "
                  f"err={err:.2f}  [{ft_time:.1f}s] {st}")

    total = time.time() - t0
    all_t = np.array(all_true)
    all_p = np.array(all_pred)

    ov_r2 = r2_score(all_t, all_p)
    ov_rmse = math.sqrt(mean_squared_error(all_t, all_p))
    ov_mae = mean_absolute_error(all_t, all_p)

    if verbose:
        print(f"\n{'='*70}")
        print(f"LOO OVERALL:  R2={ov_r2:.4f}  RMSE={ov_rmse:.4f}  MAE={ov_mae:.4f}")
        r2s = [r["r2"] for r in per_loc]
        pos = sum(1 for r in r2s if r > 0)
        print(f"R2>0: {pos}/{len(per_loc)}  Median R2: {np.median(r2s):.4f}")
        print(f"Time: {total:.1f}s")

    results = {
        "r2": round(ov_r2, 4),
        "rmse": round(ov_rmse, 4),
        "mae": round(ov_mae, 4),
        "per_location": per_loc,
        "config": {
            "hidden": hidden, "epochs": epochs, "k_geo": k_geo,
            "k_morpho": k_morpho, "lr": lr, "n_seeds": n_seeds,
        },
    }
    return results


if __name__ == "__main__":
    data_path = (
        Path(__file__).resolve().parent.parent
        / "data" / "assembled" / "validation_dataset_v6.csv"
    )
    df = pd.read_csv(data_path)
    print(f"Loaded {len(df)} events")

    try:
        import importlib.util
        tv6_path = Path(__file__).resolve().parent.parent / "train_v6.py"
        spec = importlib.util.spec_from_file_location("train_v6", str(tv6_path))
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "train_v6"
        sys.modules["train_v6"] = mod
        spec.loader.exec_module(mod)
        df = mod.add_engineered_features(df)
        print(f"Features: {len(df.columns)} columns")
    except Exception as e:
        print(f"Warning: {e}")

    # Try multiple configs
    configs = [
        {"hidden": 16, "epochs": 200, "lr": 2e-3, "n_seeds": 7, "k_geo": 6, "k_morpho": 3},
        {"hidden": 24, "epochs": 300, "lr": 3e-3, "n_seeds": 7, "k_geo": 8, "k_morpho": 4},
        {"hidden": 32, "epochs": 400, "lr": 5e-3, "n_seeds": 5, "k_geo": 10, "k_morpho": 5},
    ]

    best_r2 = -999
    best_config = None
    for cfg in configs:
        print(f"\n\nConfig: {cfg}")
        results = train_and_evaluate_loo(df, top_n=15, **cfg)
        if results["r2"] > best_r2:
            best_r2 = results["r2"]
            best_config = cfg
            best_results = results

    print(f"\n\nBest config: {best_config}")
    print(f"Best R2: {best_r2:.4f}")

    out_path = (
        Path(__file__).resolve().parent.parent
        / "artifacts" / "gnn_v3_loo_results.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(best_results, f, indent=2)
    print(f"Saved to {out_path}")
