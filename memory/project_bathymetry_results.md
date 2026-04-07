# Bathymetry Pipeline Results (2026-03-27)

## Frozen Benchmark — Hash d2ce2dc5
Split: transect (wf=0.15, seed=42). All results below are transect-validated.

### Tier-1 (0-15m, 152 lakes) — PRODUCTION BASELINE
- **Method A (soft blend + residual): 2.23m mean, 1.99m median RMSE**
- 50% sub-2m, 26% sub-1.5m, 8% sub-1m
- Coverage-error curve:
  - Top 80%: 1.81m mean
  - Top 60%: 1.53m mean
  - Top 40%: 1.27m mean
  - Top 20%: 1.01m mean ← sub-1m!

### Tier-2a (15-20m, 31 lakes)
- Method A: 3.51m mean (with K-donor priors: -0.10m improvement)
- Coverage top 40%: 2.74m

### Tier-2b (20-30m, 50 lakes)
- Method A: 6.25m mean
- Abs+Rel blend: 6.26m (marginal)
- Coverage top 20%: 4.16m
- Coarse inference: Spearman rho=0.598, rank accuracy=37% (5-class)

### Tier-3 (30m+, 18 lakes)
- 9.14m mean — optical signal fully attenuated
- Needs new signal (sonar, terrain, stereo) not better learners

### Reservoir Pipeline
- Sed-corrected NID depth: MAPE=6.2% (4 USBR reservoirs validated)
- Storage nowcasting: <1% error vs USBR published capacity
- 508 Tier 1+2 reservoirs cataloged (111 Tier-1 >=100m, 397 Tier-2 50-100m)

## Production Architecture
- Soft regime routing (probability-weighted blend of shallow/mid/deep GBR specialists)
- Per-lake residual correction (RF trained on transect anchors)
- 30 spectral + 11 spatial + 19 morphometric features
- GBR models per regime, RF classifier for routing

## What Works vs What Doesn't

### Works
- Soft probability routing (consistently best)
- Per-lake residual correction
- Spatial features (elongation, centerline dist, center proximity)
- K-donor similar-lake priors (small but real for Tier-2a)
- Coverage-error gating (strongest product story)
- Relative depth blend for T3 (-0.10m)

### Doesn't Work
- Hard regime routing (3.41m vs 2.23m — worse)
- 3D-LAKES max_depth cap (destroys performance — estimates too low)
- 3D-LAKES soft penalties (still hurts — estimates too wrong)
- Shallow specialist v2 (bounded 0-2m, near-shore — doesn't generalize spatially)
- Lake-level scale correction (R2~0 — morphometry can't predict per-lake bias)
- Temperature scaling (T_opt=0.993 — not the bottleneck)
- Naive stacking
- Depth-stratified models on sparse per-lake data

## Depth-Stratified Error Profile
| Depth | RMSE | Bias | Notes |
|-------|------|------|-------|
| 0-1m  | 3.47m | +2.17m | Massive overprediction |
| 1-2m  | 3.20m | +1.86m | Overprediction |
| 2-3m  | 3.25m | +1.39m | |
| 3-5m  | 3.12m | +1.05m | |
| 5-7m  | 2.76m | -0.21m | **SWEET SPOT** |
| 7-10m | 3.68m | -0.49m | |
| 10-15m| 4.51m | -2.63m | Underprediction begins |
| 15-20m| 6.76m | -4.65m | Beyond optical |
| 20-30m| 11.10m| -9.45m | Fully non-optical |

## Spatial Leakage Quantified
| Split | RMSE | vs Random |
|-------|------|-----------|
| Random | 3.06m | baseline |
| Spatial block | 3.98m | +30% |
| Transect | 4.21m | +38% |

## Novel Approaches (Code Written, Ready to Test)
- `implicit_bathymetric_field.py` — SIREN/Fourier coordinate MLP (publishable, genuinely novel)
- `ice_phenology_depth.py` — Sentinel-1 SAR winter littoral depth (0-5m specialist)
- `fetch_icesat2_atl24.py` — Free ICESat-2 training labels
- `multitemporal_composite.py` — 16-scene MIWC compositing

## Data Expansion Status
- ✅ MN DNR: 2,010 lakes, 1.49M contour points downloaded (was 1,806 lakes / 102K pts)
- ⏳ MI DNR: 2,000+ lakes, ArcGIS endpoint needs manual download
- ⏳ LAGOS-US DEPTH: 17,675 US lakes with max/mean depth (EDI repository)
- ⏳ WI DNR: PDF maps, limited digital GIS data
- ⏳ NOAA ENC: US navigable water soundings extractable via GDAL S57 driver
- ⚠️ MN DNR requires written commercial agreement

## Open Bathymetry Sources (Exhaustive Research)
### Survey-Grade (use as-is):
- MN DNR: 4,500 lakes, 5m DEM, requires agreement
- MI DNR: 2,000+ lakes, contour shapefiles, open data
- TX TWDB: 116 reservoirs, survey-grade
- NOAA CUDEM: US coastal 3m, public domain
- NOAA NOS: Multibeam/singlebeam archives, public domain
- NOAA ENC: Chart soundings, public domain
- CHS NONNA: Canadian waters 10m/100m, open license
- AusSeabed: Australian waters 50m, CC-BY
### Modeled/Derived (use as priors):
- GLOBathy: 1.4M lakes at 30m, CC0 (public domain)
- 3D-LAKES: 510K lakes, A-E + 30m raster, CC-BY
- HydroLAKES: 1.4M lakes, avg depth/volume
- GRDL: 7K reservoirs, A-E profiles
- LAGOS-US: 17,675 US lakes, max+mean depth
### Cannot Use:
- Navionics SonarChart: Display-only, non-commercial
- C-MAP Genesis: Proprietary extraction prohibited

## Next Steps for Improvement (Ranked by ROI)
1. **Retrain with 1.49M MN DNR points** — 14.6x more data, immediate
2. **MI DNR contours** — 2,000+ additional lakes, manual download needed
3. **LAGOS-US max depth** — 17,675 US lake-level priors
4. **ICESat-2 ATL03 inland photons** — free global anchors
5. **NOAA ENC soundings** — Great Lakes + navigable waters
6. **Crowdsourced sonar integration** — OpenCatch user flywheel

## Ocean Bathymetry Strategy
- Lakes: OpenCatch ML algorithms (competitive moat)
- US coastal: NOAA CUDEM (3m, free, public domain)
- Global ocean: GEBCO 2025 (450m, free, public domain)
- European coastal: EMODnet (115m, free)
- Canadian: CHS NONNA (10-100m, open license)
- ⚠️ Navionics Web API: sub-meter tiles, free but NON-COMMERCIAL display only
