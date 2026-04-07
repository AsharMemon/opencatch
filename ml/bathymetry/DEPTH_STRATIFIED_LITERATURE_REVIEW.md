# Depth-Stratified Literature Review: Satellite & Remote-Sensing Bathymetry SOTA

**Date:** 2026-03-24
**Purpose:** Organize all known bathymetry accuracy benchmarks by depth band (0-100m) to set realistic per-depth targets for the OpenCatch pipeline and the reservoir monitoring product.

---

## Executive Summary

Satellite-derived bathymetry accuracy degrades monotonically with depth, governed by two hard physical limits: **optical penetration** (~1.5-2x Secchi depth) and **ICESat-2 photon penetration** (~1x Secchi, max ~40m in ultra-clear water). Beyond these limits, only indirect methods (terrain morphometry, A-E curve fitting, synthetic datasets) work, with fundamentally higher error.

Our 1,951-lake pipeline (R²=0.629, RMSE=3.49m across 0-50m) is among the first honest cross-lake inland benchmarks at this scale. No published study validates satellite bathymetry on MN/WI/MI inland lakes despite extensive DNR sonar data.

---

## 1. Master SOTA Table by Depth Band

### 0-5m: Strong Optical Signal

| Study | Method | Sensor | Ground Truth | RMSE | R² | Setting | Validation |
|---|---|---|---|---|---|---|---|
| Payandeh et al. 2026 | XGBoost+Bayesian | Sentinel-2 | ICESat-2 ATL24 | 0.45m | 0.97 | 1 coastal lagoon (SA) | Within-site, held-out tracks |
| Multi-temporal GLM (2025) | GLM compositing | Sentinel-2 | Sonar | MAE 0.34m | — | Optically complex shallow | Within-site |
| BathyFormer (2025) | Vision Transformer | Multispectral | CUDEM | 0.52m (3-4m band) | — | Chesapeake Bay (3 sites) | Cross-site |
| Depth Anything V2 (2025) | Foundation model | Sentinel-2 | NOAA NCEI | 0.27m | r=0.845 | Coastal shallow | Within-site |
| Extra Trees turbid (2026) | Extra Trees | Multispectral | Sonar | 0.46m | 0.91 | Yellow River (turbid) | Within-site |
| Inland lake RF (2025) | Random Forest | Multispectral | Sonar | 0.25m | 0.92 | 3 inland lake sites | Within-lake |
| Stumpf log-ratio (2003) | Band ratio | Any multispectral | Various | 0.24-0.65m | 0.56-0.93 | Clear coastal | Within-site |
| Evagorou et al. (2022) | RTA/MBLA/Lyzenga | WV-2/S2/Planet | Sonar | WV2: 1.01m, S2: 1.06m | — | Crete (0-10m, binned) | Within-site |

**EOMAP industry standard: LE90 = 0.5m + 10% of depth (at 5m → 1.0m LE90)**

**SOTA ceiling (0-5m): RMSE 0.2-0.5m, R² >0.90**
- Achievable with standard Sentinel-2 + ML in clear-to-moderate water
- Multi-temporal compositing reduces noise floor further
- Turbidity degrades accuracy but geographic/morphometric features compensate

### 5-15m: Signal Attenuating

| Study | Method | Sensor | Ground Truth | RMSE | R² | Setting | Validation |
|---|---|---|---|---|---|---|---|
| BathyFormer (2025) | Vision Transformer | Multispectral | CUDEM | 0.73-0.89m | — | Chesapeake Bay | Cross-site |
| Chah Nimeh Reservoir (2023) | MLP | Landsat-8 | Sonar | 0.82m | 0.97 | 1 reservoir (Iran) | Within-lake |
| Darbandikhan Reservoir (2024) | QRF | Sentinel-2+ICESat-2 | ICESat-2 | 0.55m | 0.984 | 1 reservoir (Iraq), 0-20m | Within-lake |
| PHY-SDB (2025) | Physics-guided DNN | EnMAP+ICESat-2 | In-situ | 1.21m | 0.97 | Reef (hyperspectral) | Within-site |
| Le et al. (2024) | Multi-temporal fusion | Sentinel-2+ICESat-2 | ICESat-2 | 0.39-0.65m | — | Clear coastal | Within-site |
| TransBathy (2025) | Transformer | Sentinel-2 | LiDAR/multibeam | 1.68m | 0.839 | 5 coastal regions (unseen test) | Cross-site |
| ICESat-2 ATL24 (Parrish 2025) | Photon-counting LiDAR | ICESat-2 | Sonar/LiDAR | 0.34-0.89m | >0.95 | Global coastal | Cross-site |
| Caballero & Stumpf (2022) | Physics-based OLI | Landsat OLI | Charts | <2m | — | Coastal, up to 30m | Within-site |

**EOMAP industry standard: LE90 = 0.5m + 10% of depth (at 10m → 1.5m LE90)**

**SOTA ceiling (5-15m): RMSE 0.5-1.5m, R² 0.85-0.97**
- Still achievable optically in clear water (Secchi >5m)
- ICESat-2 ATL24 provides reliable along-track depths in this range
- Physics-informed models (HybridBathNet, PHY-SDB) improve generalization
- Cross-site transfer degrades to RMSE ~1.5-2m without local calibration

### 15-30m: Near/At Optical Limit

| Study | Method | Sensor | Ground Truth | RMSE | R² | Setting | Validation |
|---|---|---|---|---|---|---|---|
| TransBathy (2025) | Transformer | Sentinel-2 | LiDAR | MAE ~11.89m (>15m) | — | Coastal (cross-site) | Cross-site |
| Caballero & Stumpf (2022) | Physics-based | Landsat | Charts | ~2m (to 30m) | — | Clear coastal only | Within-site |
| Copernicus Optical SDB (2025) | RTE inversion | Sentinel-2 | Various | 2.57m | 0.79 | Global coastal | Cross-site |
| SG-XGBoost turbid (2025) | XGBoost+coords | Multispectral | Sonar | 1.66m | 0.91 | 1 reservoir (China), 2-31m | Within-lake |
| 3D-LAKES (2025) | A-E curve fitting | ICESat-2+Landsat | In-situ | 1.37m | 0.61 (A-E) | 510K global lakes | Cross-lake |
| ICESat-2 clear water | Photon LiDAR | ICESat-2 | — | ~0.7m | — | Clear water only, max ~38m | Along-track |

**SOTA ceiling (15-30m): RMSE 1.5-3m, R² 0.5-0.8**
- Optical methods only work here in oligotrophic/ultra-clear water (Secchi >10m)
- Most inland lakes are opaque at this depth — must rely on non-optical methods
- A-E curve fitting (3D-LAKES approach) is the best generalizable method
- ICESat-2 can reach ~25-30m in clear coastal water but rarely in inland lakes

### 30-50m: Beyond Optical Limit

| Study | Method | Sensor | Ground Truth | RMSE | R² | Setting | Validation |
|---|---|---|---|---|---|---|---|
| 3D-LAKES (2025) | A-E + synthetic | ICESat-2+Landsat | In-situ | 1.37m (aggregate) | — | Global | Cross-lake |
| GLOBathy (Khazaei 2022) | Morphometric GIS | HydroLAKES attrs | 1,503 lakes | 1.37m (3D) | 0.61 | 1.4M global lakes | Cross-lake |
| GRDL (Hao 2024) | DL on simulated res. | SRTM | 54 test reservoirs | MAE 7.87m | — | 7,250 global reservoirs | Cross-lake |
| Martinsen U-Net (2023) | Terrain DL | DEM | In-situ surveys | MAE 2.15m | — | 153 Danish lakes | Cross-lake |
| Hollister (2011) | Terrain slope regression | NED | EPA NLA | 5.09-5.95m | r=0.69-0.82 | 28K NE US lakes | Cross-lake |
| HydroLAKES (Messager 2016) | Geo-statistical | Terrain slope | 12,150 records | MAE 0.86m (mean depth) | r=0.91 | 1.42M global | Cross-lake |

**SOTA ceiling (30-50m): RMSE 2-8m depending on method**
- No optical signal reaches here in any inland lake
- Terrain morphometry (Martinsen) and A-E curves (3D-LAKES) are the only options
- Morphometric features (area, terrain slope, relief, shoreline dev.) predict aggregate depth well
- Spatial distribution within the lake requires terrain extrapolation with multi-meter uncertainty

### 50-100m: Deep Water

| Study | Method | Data | RMSE | Setting |
|---|---|---|---|---|
| Copernicus wave-kinematics (2025) | Wave dispersion | Sentinel-2 | 6-9m | Coastal only (requires waves) |
| Gravity-derived (Harper & Sandwell 2024) | Neural net + gravity | Satellite gravity + soundings | 138m (ocean), 25m (regional) | Ocean basins only |
| GRDL (Hao 2024) | DL on simulated reservoirs | SRTM | MAE ~8m | Reservoirs |
| Morphometric extrapolation | Statistical | Lake/terrain features | 5-15m+ | Any inland lake |

**SOTA ceiling (50-100m): RMSE 5-15m+**
- Wave-kinematics works to ~100m but only in open coastal water with surface waves
- Gravity-derived is ocean-only, resolution >20km
- For inland lakes, this depth range is effectively unobservable from space
- Only prior surveys, dam construction records, or morphometric inference apply

---

## 2. Depth Limit by Method

| Method | Max Effective Depth | Best RMSE in Range | Limiting Factor |
|---|---|---|---|
| Stumpf/Lyzenga band ratio | 1.5-2x Secchi (~5-20m) | 0.24-0.65m | Light attenuation |
| ML on spectral bands (XGBoost/RF) | 1.5-2x Secchi (~5-20m) | 0.25-0.55m | Light attenuation |
| Deep learning (U-Net, Transformer) | Same optical limit | 0.52-0.89m | Light attenuation |
| Physics-informed DL (HybridBathNet) | Same optical limit | Better generalization | Light attenuation |
| Foundation models (Depth Anything V2) | Same optical limit | 0.27m (zero-shot) | Domain shift + attenuation |
| Multi-temporal compositing | Same limit, reduced noise | MAE 0.34m | Light attenuation |
| ICESat-2 photon-counting LiDAR | ~1x Secchi, max ~40m clear | 0.34-0.89m | Photon penetration + turbidity |
| SWOT radar altimetry | Surface only (0m) | WSE error ~0.11-0.18m | Cannot penetrate water |
| A-E curve fitting (3D-LAKES) | Depth of water level fluctuation | A-E RMSE 0.60m | Requires multi-year drawdown |
| Terrain morphometry (Martinsen) | No depth limit | MAE 2.15m | Statistical, not physical measurement |
| Morphometric regression (Hollister) | No depth limit | RMSE 5.09m | Coarse statistical model |
| Wave kinematics (S2Shores) | ~100m | 6-9m | Requires surface waves |
| Gravity-derived | Ocean basins only | 25-138m | Resolution >20km |
| Multi-beam sonar | Unlimited | 0.1-0.3m | Requires boat access |

---

## 3. Inland Lake vs Coastal Performance Gap

A critical finding: nearly all sub-meter RMSE results come from **single-site coastal** studies. Cross-lake inland validation is fundamentally harder.

| Validation Type | Typical RMSE | Typical R² | Why |
|---|---|---|---|
| Within-site coastal (clear) | 0.3-0.5m | >0.95 | Homogeneous optics, single calibration |
| Cross-site coastal | 1.5-2.5m | 0.7-0.85 | Variable bottom type, turbidity |
| Within-lake inland | 0.5-1.7m | 0.91-0.97 | Per-lake calibration compensates |
| **Cross-lake inland** | **1.4-6.0m** | **0.42-0.74** | **Variable clarity, depth, bottom, size** |
| Global morphometric | 1.4-6.0m | 0.61-0.90 | Statistical, no direct observation |

**Our pipeline (cross-lake inland, 1,951 lakes, 0-50m): R²=0.629, RMSE=3.49m**
- This is competitive with 3D-LAKES (RMSE=1.37m but only A-E, not full 3D)
- Better than Hollister (RMSE=5.09-5.95m)
- Our depth range (0-50m) is much harder than most optical studies (0-10m)

---

## 4. What This Means for OpenCatch Targets

### Realistic per-depth-band targets for our pipeline:

| Depth Band | Current (est.) | Achievable Target | Method to Get There |
|---|---|---|---|
| 0-5m | ~1.5m RMSE | 0.5-0.8m | Optical SDB + multi-temporal compositing + physics features |
| 5-10m | ~2.5m RMSE | 1.0-1.5m | Optical + ICESat-2 fusion + water-type-adaptive models |
| 10-20m | ~3.5m RMSE | 1.5-2.5m | ICESat-2 (clear lakes) + terrain prior + A-E constraints |
| 20-35m | ~5.0m RMSE | 2.5-4.0m | Morphometric + A-E + terrain U-Net; optical fails |
| 35-50m | ~6.0m RMSE | 4.0-6.0m | Terrain/morphometric only; 3D-LAKES priors |

### Reservoir monitoring targets (different from general lakes):

Reservoirs have advantages over natural lakes for remote sensing:
1. **Known dam height** → max depth prior from NID
2. **Large water level fluctuation** → more A-E data points
3. **Construction survey baseline** → only need to detect *change*
4. **USACE/USBR operations data** → pool level, releases, inflows as covariates

| Product | Target Accuracy | Feasibility | Method |
|---|---|---|---|
| Storage at elevation (volume) | <10% relative error | HIGH | A-E curve + SWOT + Sentinel-2 area |
| Sedimentation rate (annual) | <15% relative error | MODERATE | Multi-year A-E curve shift detection |
| Delta/shallow zone mapping (0-10m) | RMSE 0.5-1.5m | HIGH | Optical SDB, best in clear water |
| Mid-depth change (10-30m) | RMSE 2-4m | MODERATE | A-E + terrain extrapolation |
| Deep-zone volume estimate (>30m) | RMSE 5-10m | LOW | Morphometric + dam records |
| Survey prioritization ranking | Top-decile recall >80% | HIGH | Uncertainty + change + time-since-survey |

---

## 5. Key Papers by Relevance to Our Work

### Must-read (directly applicable):

1. **3D-LAKES (2025)** — A-E relationships for 510K lakes. Our SWOT pipeline extends this.
   - Scientific Data. doi: 10.1038/s41597-025-05911-y

2. **GRDL (Hao et al. 2024)** — DL on simulated reservoirs from SRTM. Direct competitor.
   - Water Resources Research. doi: 10.1029/2023WR035781

3. **Martinsen et al. (2023)** — U-Net from terrain for 153 lakes. Our terrain approach builds on this.
   - Limnology & Oceanography: Methods. doi: 10.1002/lom3.10573

4. **ICESat-2 ATL24 (Parrish et al. 2025)** — Global along-track bathymetry product validation.
   - Earth and Space Science. doi: 10.1029/2025EA004391

5. **HybridBathNet (Qian et al. 2025)** — Physics-embedded architecture improves generalization.
   - Frontiers in Marine Science. doi: 10.3389/fmars.2025.1636124

6. **TransBathy (Zhang & Al Shehhi 2025)** — Cross-site transformer transfer; shows depth-dependent degradation.
   - Scientific Reports. doi: 10.1038/s41598-024-83705-9

7. **Sancha Lake SG-XGBoost (2025)** — Turbid reservoir, coords > spectral. Validates our morphometric approach.
   - Results in Optics. doi: 10.1016/j.rio.2025.100610

8. **Extra Trees turbid rivers (Wang et al. 2026)** — Geographic params dominate in turbid water.
   - Frontiers in Marine Science. doi: 10.3389/fmars.2026.1693671

9. **Payandeh et al. (2026)** — The paper that prompted this review. Good workflow, limited generalizability.
   - Frontiers in Remote Sensing. doi: 10.3389/frsen.2026.1751006

10. **Ma et al. (2026)** — SWOT reservoir storage monitoring with gap-filling.
    - Water Resources Research. doi: 10.1029/2025WR041223

### Important context:

11. **Messager et al. (2016)** — HydroLAKES morphometric depth estimation baseline.
    - Nature Communications. doi: 10.1038/ncomms13603

12. **Khazaei et al. (2022)** — GLOBathy synthetic bathymetry for 1.4M waterbodies.
    - Scientific Data. doi: 10.1038/s41597-022-01132-9

13. **Hollister et al. (2011)** — Terrain slope → max depth for 28K US lakes.
    - PLoS ONE. doi: 10.1371/journal.pone.0025764

14. **Zhan et al. (2023)** — ML from max depth to mean depth/volume, global.
    - Journal of Hydrology. doi: 10.1016/j.jhydrol.2022.128938

15. **Seabed-Net (Agrafiotis & Demir 2026)** — Multi-task (depth + classification) improves both.
    - ISPRS J. Photogrammetry. doi: 10.1016/j.isprsjprs.2025.01.001

16. **Depth Anything V2 for SDB (2025)** — Foundation model, promising but domain shift.
    - ISPRS Archives XLVIII-2-W10-2025

17. **Wei et al. (2024)** — Turbidity impact on SDB across 7 ports.
    - Remote Sensing. doi: 10.3390/rs16234349

18. **Copernicus Marine Global SDB (2025)** — Three-method global coastal product.
    - marine.copernicus.eu

19. **UQ for ML bathymetry (2025)** — Industry standard: ~10% of water depth.
    - Remote Sensing. doi: 10.3390/rs17173060

20. **Corcoran et al. (2024)** — Scalable ICESat-2 + Sentinel-2 fusion pipeline.
    - Earth and Space Science. doi: 10.1029/2024EA003735

### Emerging / watch:

21. **Swin-BathyUNet (2025)** — No in-situ data required (SfM pseudo-reference).
    - ISPRS J. github.com/pagraf/Swin-BathyUNet

22. **PHY-SDB (2025)** — Dual-physics hyperspectral SDB.
    - GIScience & Remote Sensing. doi: 10.1080/15481603.2025.2594809

23. **Domain-Adaptive SDB (2025)** — Transfer learning across water types.
    - ISPRS J. doi: 10.1016/j.isprsjprs.2025.03.079

24. **S2Shores global wave bathymetry (2025)** — Wave-kinematics to ~100m depth.
    - Scientific Data. doi: 10.1038/s41597-025-06402-w

---

## 6. The Turbidity Finding: Geography > Spectral in Turbid Water

Three independent studies (2025-2026) confirm that when water is turbid, **geographic/morphometric coordinates contribute more to depth prediction than spectral bands**:

1. **Sancha Lake (2025)**: Longitude+latitude = 85% feature importance vs spectral = 15% (R²=0.91, turbid reservoir)
2. **Extra Trees Yellow River (2026)**: Geographic parameters dominate; Stumpf/Lyzenga R² ≈ 0.00 in turbid water
3. **RF + Coordinate Attention (2024)**: Geographic coordinates more influential than spectral in turbid coastal water

**Implication for OpenCatch**: Our morphometric/terrain approach (Priority 4, RMSE 1.30m) is not a fallback — it's the primary method for the majority of inland lakes and reservoirs where optical penetration is limited. The spectral model is only dominant in the top 5-10m of clear water.

---

## 7. Gap Analysis: What's Missing in the Literature

1. **No published cross-lake inland validation at scale on US lakes** — Our 1,951-lake MN pipeline fills this gap
2. **No depth-stratified accuracy tables for inland lakes** — All per-depth-band results are coastal
3. **No reservoir sedimentation change detection from satellite bathymetry** — Storage change is published (SWOT), but bottom-change detection is not
4. **No honest cross-lake ensemble combining optical + terrain + A-E** — Papers use one method; our multi-approach pipeline is novel
5. **No turbidity-adaptive routing** — No system automatically selects optical vs morphometric based on water clarity

These gaps represent both research contributions and product differentiation for OpenCatch.

---

## 8. Industry Error Model

The EOMAP commercial SDB product uses this empirical formula as their accuracy specification:

**LE90 = 0.5m + 10% of depth**

| Depth | LE90 (90th percentile error) | Approx RMSE equivalent |
|---|---|---|
| 2m | 0.7m | ~0.4m |
| 5m | 1.0m | ~0.6m |
| 10m | 1.5m | ~0.9m |
| 15m | 2.0m | ~1.2m |
| 20m | 2.5m | ~1.5m |
| 30m | 3.5m | ~2.1m |

This is the commercial bar. Our per-depth targets in Section 4 are calibrated against this.

Additional finding from **Palaseanu-Lovejoy et al. (2026, USGS)**: Compared SaTSeaD (stereo), PBSDB (physics-based), and SatBathy (band-ratio) across WorldView, Landsat, and Sentinel-2 at 3 Caribbean sites. Key result: **error distribution is bimodal regardless of method or sensor, and location characteristics matter more than algorithm choice.** This validates turbidity-adaptive routing over chasing a single best model.

---

## 9. Recommended Citation for Our Work

When publishing results from the OpenCatch pipeline, frame against these benchmarks:

> "Our cross-lake validation across 1,951 inland lakes spanning 0-50m depth (R²=0.629, RMSE=3.49m) represents, to our knowledge, the largest honest satellite-derived bathymetry benchmark for inland freshwater lakes. This compares to: 3D-LAKES A-E RMSE=1.37m on 510K lakes (geometric only, not full 3D SDB); GLOBathy RMSE=1.37m (morphometric); Hollister et al. RMSE=5.09-5.95m (terrain slope regression on 28K US lakes); and Martinsen et al. MAE=2.15m (terrain U-Net on 153 Danish lakes). Unlike coastal SDB studies reporting sub-meter RMSE, our benchmark includes turbid, deep, and eutrophic lakes where optical methods provide no signal beyond 5-10m depth."
