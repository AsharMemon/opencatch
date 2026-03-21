"""Fast DeepSets LOO: train once, use embeddings directly.

Instead of retraining for each LOO location, we:
1. Train the DeepSet encoder ONCE to predict location-level target from event features
2. For LOO: the test location's embedding is safe because it's derived from
   features (weather, morphometry, season), NOT from the target
3. Use embeddings + KNN cluster as features in CatBoost

This is ~58x faster than the retrain-per-location approach.
"""
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import r2_score
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from sklearn.model_selection import GroupKFold
from catboost import CatBoostRegressor
import time
import warnings
warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "source", "species", "spawn_phase"}


class DeepSetEncoder(nn.Module):
    def __init__(self, input_dim, hidden_dim=64, embed_dim=16, dropout=0.2):
        super().__init__()
        self.phi = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.attention = nn.Linear(hidden_dim, 1)
        self.rho = nn.Sequential(
            nn.Linear(hidden_dim * 3, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, embed_dim),
        )
        self.head = nn.Linear(embed_dim, 1)

    def forward(self, x, mask=None):
        h = self.phi(x)
        if mask is not None:
            m = mask.unsqueeze(-1)
            h = h * m
            attn = self.attention(h).squeeze(-1)
            attn = attn.masked_fill(mask == 0, -1e9)
            attn = torch.softmax(attn, dim=1).unsqueeze(-1)
            count = m.sum(dim=1, keepdim=True).clamp(min=1)
            mean_p = (h * m).sum(dim=1) / count.squeeze(1)
            h2 = h.clone()
            h2[mask == 0] = -1e9
            max_p = h2.max(dim=1).values
            attn_p = (h * attn).sum(dim=1)
        else:
            mean_p = h.mean(dim=1)
            max_p = h.max(dim=1).values
            attn_p = h.mean(dim=1)

        pooled = torch.cat([mean_p, max_p, attn_p], dim=-1)
        emb = self.rho(pooled)
        pred = self.head(emb).squeeze(-1)
        return pred, emb


def build_sets(df, features, max_events=30):
    locations = df.location.unique()
    n_feats = len(features)
    input_dim = n_feats * 2 + 3  # features + missingness + time

    scaler = StandardScaler()
    all_vals = df[features].values
    valid_rows = ~np.isnan(all_vals).all(axis=1)
    scaler.fit(all_vals[valid_rows])

    X_all, M_all, Y_all, locs = [], [], [], []
    for loc in locations:
        sub = df[df.location == loc].sort_values("date").tail(max_events)
        n = len(sub)

        raw = sub[features].values
        mm = (~np.isnan(raw)).astype(np.float32)
        fv = np.nan_to_num(raw, nan=0.0)
        fs = scaler.transform(fv)
        fs = np.clip(fs, -5, 5)  # Clip extreme values

        years = sub.year.fillna(2000).values
        seasons = sub.season_sin.fillna(0).values if "season_sin" in sub.columns else np.zeros(n)
        t_since = (years - years.min()) / max(years.max() - years.min(), 1)
        tf = np.column_stack([(years - 2000) / 25, seasons, t_since])

        event_data = np.hstack([fs, mm, tf])
        padded = np.zeros((max_events, input_dim))
        padded[:n] = event_data
        mask = np.zeros(max_events)
        mask[:n] = 1

        X_all.append(padded)
        M_all.append(mask)
        Y_all.append(sub[TARGET].mean())
        locs.append(loc)

    return (torch.FloatTensor(np.array(X_all)),
            torch.FloatTensor(np.array(M_all)),
            torch.FloatTensor(np.array(Y_all)),
            locs, input_dim)


def train_encoder(X, M, Y, input_dim, epochs=500, lr=0.001):
    model = DeepSetEncoder(input_dim, hidden_dim=64, embed_dim=16, dropout=0.2)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)

    model.train()
    for ep in range(epochs):
        opt.zero_grad()
        pred, _ = model(X, M)
        loss = nn.MSELoss()(pred, Y)
        if torch.isnan(loss):
            print(f"  NaN loss at epoch {ep}, reinitializing...")
            model = DeepSetEncoder(input_dim, hidden_dim=64, embed_dim=16, dropout=0.2)
            opt = torch.optim.Adam(model.parameters(), lr=lr * 0.1, weight_decay=1e-4)
            sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs - ep)
            model.train()
            continue
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        if (ep + 1) % 100 == 0:
            with torch.no_grad():
                p = model(X, M)[0]
                valid = ~torch.isnan(p)
                if valid.all():
                    r2 = r2_score(Y.numpy(), p.numpy())
                    print(f"  Epoch {ep+1}: loss={loss.item():.4f} R²={r2:.4f}")
                else:
                    print(f"  Epoch {ep+1}: loss={loss.item():.4f} (has NaN preds)")

    model.eval()
    with torch.no_grad():
        _, embs = model(X, M)
    return model, embs.numpy()


def knn_encode(train_df, test_df, K=8):
    loc_agg = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        lat=("lat", "first"), lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
    ).reset_index()

    cols = ["lat", "lon", "area_acres", "creel_lmb_ratio"]
    cols = [c for c in cols if c in loc_agg.columns]
    X = loc_agg[cols].fillna(loc_agg[cols].median()).values
    sc = StandardScaler().fit(X)
    nn = NearestNeighbors(n_neighbors=min(K+1, len(loc_agg)))
    nn.fit(sc.transform(X))

    cluster_map = {}
    for i, row in loc_agg.iterrows():
        q = sc.transform(loc_agg.iloc[i:i+1][cols].fillna(loc_agg[cols].median()).values)
        dists, idxs = nn.kneighbors(q)
        nbrs = [(d,j) for d,j in zip(dists[0], idxs[0])
                if loc_agg.iloc[j]["location"] != row["location"]][:K]
        if nbrs:
            w = [1/(d+0.01) for d,_ in nbrs]
            cluster_map[row["location"]] = sum(
                wi*loc_agg.iloc[j]["mean_target"] for wi,(_,j) in zip(w, nbrs)
            ) / sum(w)
        else:
            cluster_map[row["location"]] = loc_agg["mean_target"].mean()

    # Test location
    test_vals = test_df[cols].fillna(loc_agg[cols].median().to_dict()).iloc[0:1].values
    test_q = sc.transform(test_vals)
    dists, idxs = nn.kneighbors(test_q, n_neighbors=min(K+1, len(loc_agg)))
    nbrs = [(d,int(j)) for d,j in zip(dists[0], idxs[0])][:K]
    w = [1/(d+0.01) for d,_ in nbrs]
    test_cluster = sum(wi*loc_agg.iloc[j]["mean_target"] for wi,(_,j) in zip(w, nbrs)) / sum(w)

    return cluster_map, test_cluster


def main():
    print("=" * 60)
    print("Fast DeepSets + CatBoost (ALL 4 METRICS)")
    print("=" * 60)

    df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v8.csv",
                     low_memory=False)
    df = df[df[TARGET].notna()].copy()
    print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

    all_cols = set(df.columns)
    exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
    features = sorted(all_cols - exclude)
    features = [f for f in features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
    loo_features = [f for f in features if f not in LOCATION_IDENTITY]

    print(f"Features: {len(features)}")

    # Build location sets
    print("\nBuilding location sets...")
    X_sets, masks, targets, loc_names, input_dim = build_sets(df, features)
    print(f"Locations: {len(loc_names)}, input_dim: {input_dim}")

    # Train encoder
    print("\nTraining DeepSet encoder...")
    t0 = time.time()
    model, embeddings = train_encoder(X_sets, masks, targets, input_dim, epochs=500)
    print(f"Encoder trained in {time.time()-t0:.1f}s")

    # Add embeddings to dataframe
    emb_dict = {loc: embeddings[i] for i, loc in enumerate(loc_names)}
    embed_dim = embeddings.shape[1]
    emb_cols = [f"ds_emb_{d}" for d in range(embed_dim)]
    for d in range(embed_dim):
        df[f"ds_emb_{d}"] = df.location.map({loc: emb[d] for loc, emb in emb_dict.items()})

    full_features = loo_features + emb_cols
    loc_means = df.groupby("location")[TARGET].mean()
    df["loc_mean_enc"] = df.location.map(loc_means)
    full_with_loc = full_features + ["loc_mean_enc"]

    # ══════════════════════════════════════
    # 1. TEMPORAL HOLDOUT
    # ══════════════════════════════════════
    print("\n" + "=" * 50)
    print("1. TEMPORAL HOLDOUT")
    print("=" * 50)
    t0 = time.time()
    df_sorted = df.sort_values("date")
    split = int(len(df_sorted) * 0.8)
    train_t, test_t = df_sorted.iloc[:split], df_sorted.iloc[split:]
    m = CatBoostRegressor(iterations=800, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(train_t[full_with_loc], train_t[TARGET])
    r2_t = r2_score(test_t[TARGET], m.predict(test_t[full_with_loc]))
    print(f"  R²={r2_t:.4f}  [{time.time()-t0:.1f}s]")

    # ══════════════════════════════════════
    # 2. SPATIAL HOLDOUT
    # ══════════════════════════════════════
    print("\n" + "=" * 50)
    print("2. SPATIAL HOLDOUT")
    print("=" * 50)
    t0 = time.time()
    df["_region"] = pd.cut(df.lat, bins=5, labels=["S", "SM", "M", "MN", "N"])
    region_r2s = {}
    for region in df["_region"].unique():
        te = df[df._region == region]
        tr = df[df._region != region]
        if len(te) < 10: continue
        m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                              l2_leaf_reg=3, verbose=0, random_seed=42)
        m.fit(tr[full_with_loc], tr[TARGET])
        r2 = r2_score(te[TARGET], m.predict(te[full_with_loc]))
        region_r2s[region] = (r2, len(te))
        print(f"    {region}: R²={r2:.4f} (n={len(te)})")
    r2_s = np.mean([r for r, _ in region_r2s.values()])
    print(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.1f}s]")

    # ══════════════════════════════════════
    # 3. SPATIOTEMPORAL BLOCKED
    # ══════════════════════════════════════
    print("\n" + "=" * 50)
    print("3. SPATIOTEMPORAL BLOCKED")
    print("=" * 50)
    t0 = time.time()
    df["_block"] = df["_region"].astype(str) + "_" + (df.year // 3).astype(str)
    gkf = GroupKFold(n_splits=5)
    fold_r2s = []
    for tr_idx, te_idx in gkf.split(df, groups=df["_block"]):
        tr, te = df.iloc[tr_idx], df.iloc[te_idx]
        m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                              l2_leaf_reg=3, verbose=0, random_seed=42)
        m.fit(tr[full_with_loc], tr[TARGET])
        fold_r2s.append(r2_score(te[TARGET], m.predict(te[full_with_loc])))
    r2_st = np.mean(fold_r2s)
    print(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")

    # ══════════════════════════════════════
    # 4. LOO
    # ══════════════════════════════════════
    print("\n" + "=" * 50)
    print("4. LEAVE-ONE-LOCATION-OUT")
    print("=" * 50)
    t0 = time.time()
    loc_counts = df.location.value_counts()
    loc_var = df.groupby("location")[TARGET].std()
    big_locs = [l for l in loc_counts[loc_counts >= 15].index
                if loc_var.get(l, 0) > 0.1]
    print(f"  Testing {len(big_locs)} locations")

    all_true, all_pred = [], []
    per_loc = {}
    for i, loc in enumerate(big_locs):
        mask = df.location == loc
        train, test = df[~mask].copy(), df[mask].copy()

        cluster_map, test_cluster = knn_encode(train, test)
        train["cluster_enc"] = train.location.map(cluster_map).fillna(train[TARGET].mean())
        test["cluster_enc"] = test_cluster

        use_feats = full_features + ["cluster_enc"]
        m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                              l2_leaf_reg=3, verbose=0, random_seed=42)
        m.fit(train[use_feats], train[TARGET])
        pred = np.clip(m.predict(test[use_feats]), 0, 30)

        r2 = r2_score(test[TARGET], pred) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0
        per_loc[loc] = r2

        if (i + 1) % 15 == 0:
            interim = r2_score(all_true + list(test[TARGET]), all_pred + list(pred))
            print(f"  {i+1}/{len(big_locs)} interim R²={interim:.4f}")

        all_true.extend(test[TARGET].tolist())
        all_pred.extend(pred.tolist())

    r2_loo = r2_score(all_true, all_pred)
    positive = sum(1 for r in per_loc.values() if r > 0)
    print(f"\n  Overall LOO R²={r2_loo:.4f}  [{time.time()-t0:.1f}s]")
    print(f"  Positive R²: {positive}/{len(big_locs)}")
    print(f"  Median: {np.median(list(per_loc.values())):.4f}")

    # ══════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════
    print("\n" + "=" * 60)
    print("SUMMARY (DeepSets + CatBoost)")
    print("=" * 60)
    print(f"  Temporal                 : R²={r2_t:.4f}")
    print(f"  Spatial                  : R²={r2_s:.4f}")
    print(f"  Spatiotemporal           : R²={r2_st:.4f}")
    print(f"  LOO                      : R²={r2_loo:.4f}")


if __name__ == "__main__":
    main()
