# Payandeh 2026 Replication Path

This is a separate, paper-style shallow/coastal benchmark path for:

- `ATL24` bathymetry labels
- `Sentinel-2` reflectance predictors
- whole-track holdout validation
- per-scene bathymetry metrics
- scene-aware split fallback when date filtering would otherwise collapse a scene onto a degenerate holdout
- depth-stratified `RMSE` / `R²` reporting
- aggregated held-out point metrics across scenes
- multi-scene averaging
- optional Gaussian smoothing

It is intentionally separate from the harder inland all-lakes sonar benchmark.

## Script

- [replicate_payandeh_2026.py](/Users/Ashar/Documents/fish/ml/bathymetry/replicate_payandeh_2026.py)

## What It Replicates

The default feature set matches the paper:

- `B2/B3`
- `B2/B4`
- `B3/B8`
- `B4/B8`
- `NDWI`
- `B5`
- `B6`
- `B7`

There is also an optional extended feature set for shallow within-site
experiments:

- the paper features above
- raw `blue`, `green`, `red`, `nir`
- `shore_dist_m`
- `row_norm`
- `col_norm`

The evaluation protocol is explicit:

- split by whole `ATL24` track IDs
- prefer the global track split, but fall back to a scene-specific track split
  when scene/date pairing would otherwise leave an unusable or unstable holdout
- choose the best model on one reference scene
- re-fit that model spec per scene
- report per-scene `RMSE` and `R²`
- report per-depth-bin metrics such as `0-1m`, `1-2m`, `2-5m`, `5-10m`
- aggregate held-out point predictions across scenes for a more stable summary
- average the per-scene depth maps
- optionally apply a Gaussian smoothing pass

## Inputs

You can run it in two main ways.

### 1. Local ATL24 + STAC Sentinel-2

If you already have ATL24 points in parquet/csv:

```bash
python ml/bathymetry/replicate_payandeh_2026.py \
  --atl24 /data/atl24/lagoon_points.parquet \
  --bbox 17.88 -33.17 18.07 -32.95 \
  --start 2023-01-01 \
  --end 2025-12-31 \
  --max-scenes 16 \
  --output /data/payandeh_replication
```

### 2. Live ATL24 query + STAC Sentinel-2

This uses SlideRule's `atl24x` endpoint:

```bash
python ml/bathymetry/replicate_payandeh_2026.py \
  --query-atl24 \
  --bbox 17.88 -33.17 18.07 -32.95 \
  --start 2023-01-01 \
  --end 2025-12-31 \
  --feature-set paper \
  --max-scenes 16 \
  --output /data/payandeh_replication
```

## ACOLITE / Local Raster Mode

If you have local corrected rasters, provide a scene manifest CSV/parquet with:

- `scene_id`
- `scene_date`
- `blue`
- `green`
- `red`
- `rededge1`
- `rededge2`
- `rededge3`
- `nir`
- optional `swir16`
- optional `scl`
- optional `input_scale`

If the ACOLITE directory already contains per-band rasters, you can try the
helper first:

```bash
python ml/bathymetry/build_acolite_scene_manifest.py \
  --input /data/acolite \
  --output /data/acolite/scene_manifest.csv \
  --recursive
```

Example:

```bash
python ml/bathymetry/replicate_payandeh_2026.py \
  --atl24 /data/atl24/lagoon_points.parquet \
  --scene-manifest /data/acolite/scene_manifest.csv \
  --bbox 17.88 -33.17 18.07 -32.95 \
  --feature-set paper \
  --output /data/payandeh_replication_acolite
```

## Outputs

The script writes:

- `atl24_points.parquet`
- `scene_manifest_resolved.parquet`
- `track_split.json`
- one `*_pairs.parquet` per usable scene
- `scene_pairs_all.parquet`
- `scene_val_point_predictions.parquet`
- `scene_val_point_aggregated.parquet`
- one `*_depth_pred.tif` per scene unless `--skip-map-prediction`
- `mean_depth_map.tif`
- `mean_depth_map_gaussian.tif` when `scipy` is available
- `metrics.json`

## Self-Test

You can smoke-test the full model-selection path without live data:

```bash
python ml/bathymetry/replicate_payandeh_2026.py \
  --synthetic-demo \
  --output /tmp/payandeh_demo
```

## Useful Tuning Flags

For stricter ATL24 cleanup and scene-date matching:

```bash
python ml/bathymetry/replicate_payandeh_2026.py \
  --query-atl24 \
  --bbox 17.88 -33.17 18.07 -32.95 \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --scene-date-tolerance-days 60 \
  --track-min-points 3 \
  --track-edge-trim-frac 0.05 \
  --track-outlier-window 7 \
  --track-outlier-mad-mult 3.5 \
  --track-outlier-abs-tol 1.5 \
  --min-scene-train-points 12 \
  --min-scene-val-points 6 \
  --min-scene-val-tracks 2 \
  --min-scene-val-depth-span 0.5 \
  --output /data/payandeh_replication_filtered
```

For a shallow, geometry-assisted extension beyond the strict paper feature set:

```bash
python ml/bathymetry/replicate_payandeh_2026.py \
  --query-atl24 \
  --bbox 17.88 -33.17 18.07 -32.95 \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --feature-set extended \
  --max-depth 5 \
  --scene-date-tolerance-days 60 \
  --min-scene-train-points 12 \
  --min-scene-val-points 6 \
  --min-scene-val-tracks 2 \
  --min-scene-val-depth-span 0.5 \
  --skip-map-prediction \
  --output /data/payandeh_replication_extended_shallow
```

## Important Caveat

This path is best interpreted as a:

- single-site
- shallow-water
- optically detectable
- best-case replication benchmark

It should not be compared directly to the full inland all-lakes sonar benchmark
without stating that the problem definition is much easier.
