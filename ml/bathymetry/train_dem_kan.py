#!/usr/bin/env python3
"""Quick DEM-only depth prediction using point-wise training."""
import numpy as np, pandas as pd, rasterio, torch, torch.nn as nn, os, glob, logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

# Load depth points
df = pd.read_parquet('/data/icesat2_depths/mn_sampled_depths.parquet')
log.info(f'Loaded {len(df)} depth points')

# Extract DEM features per lake (batch reads, much faster)
lake_dirs = {os.path.basename(d): d for d in glob.glob('/data/training/v2/[0-9]*')}
features, depths = [], []

# Group points by lake for efficient I/O
grouped = df.groupby('lake_id')
log.info(f'Processing {len(grouped)} lakes...')

for i, (lake_id, group) in enumerate(grouped):
    ld = lake_dirs.get(lake_id)
    if not ld: continue
    cp = os.path.join(ld, 'composite.tif')
    if not os.path.exists(cp): continue
    try:
        with rasterio.open(cp) as ds:
            data = ds.read()  # Read entire raster once
            for _, row in group.iterrows():
                py, px = ds.index(row['lon'], row['lat'])
                if 0 <= py < ds.height and 0 <= px < ds.width:
                    dem = data[10:14, py, px]
                    if np.all(np.isfinite(dem)) and row['depth_m'] > 0:
                        features.append(dem)
                        depths.append(row['depth_m'])
    except Exception as e:
        pass
    if (i+1) % 200 == 0:
        log.info(f'  {i+1}/{len(grouped)} lakes, {len(features)} features')

X = np.array(features, dtype=np.float32)
y = np.array(depths, dtype=np.float32)
log.info(f'Valid: {len(X)} samples, {X.shape[1]} features')
log.info(f'Depth range: {y.min():.1f} - {y.max():.1f}m, mean: {y.mean():.1f}m')

# Split
n_val = int(len(X) * 0.2)
idx = np.random.permutation(len(X))
X_tr, X_va = X[idx[n_val:]], X[idx[:n_val]]
y_tr, y_va = y[idx[n_val:]], y[idx[:n_val]]

class DepthNet(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d, 256), nn.SiLU(), nn.Dropout(0.1),
            nn.Linear(256, 256), nn.SiLU(), nn.Dropout(0.1),
            nn.Linear(256, 128), nn.SiLU(),
            nn.Linear(128, 1), nn.Softplus()
        )
    def forward(self, x): return self.net(x).squeeze(-1)

model = DepthNet(X.shape[1]).cuda()
opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=200)

Xt = torch.tensor(X_tr).cuda()
yt = torch.tensor(y_tr).cuda()
Xv = torch.tensor(X_va).cuda()
yv = torch.tensor(y_va).cuda()

best = float('inf')
os.makedirs('/data/models/dem_kan', exist_ok=True)

for ep in range(200):
    model.train()
    # Mini-batch
    for i in range(0, len(Xt), 4096):
        xb = Xt[i:i+4096]
        yb = yt[i:i+4096]
        pred = model(xb)
        loss = nn.functional.huber_loss(pred, yb)
        opt.zero_grad()
        loss.backward()
        opt.step()
    sched.step()

    model.eval()
    with torch.no_grad():
        vp = model(Xv)
        rmse = ((vp - yv)**2).mean().sqrt().item()
        mae = (vp - yv).abs().mean().item()

    if rmse < best:
        best = rmse
        torch.save(model.state_dict(), '/data/models/dem_kan/best.pt')

    if (ep+1) % 5 == 0:
        log.info(f'Epoch {ep+1}/200: RMSE={rmse:.3f}m MAE={mae:.3f}m (best={best:.3f}m)')

log.info(f'FINAL best RMSE: {best:.3f}m')
