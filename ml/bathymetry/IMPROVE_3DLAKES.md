# Improving Beyond 3D-LAKES: Research & Action Plan

**Date**: 2026-03-23
**Goal**: Replicate 3D-LAKES results (~1.37m RMSE for 3D bathymetry), then exceed them
**Target**: Sub-1m RMSE for US/North American lakes

---

## 1. Exact 3D-LAKES Methodology (Step by Step)

**Paper**: Huang, C.H., Zhang, S., Shah, D. et al. (2025). "3D-LAKES: Three-Dimensional
Global Lake and Reservoir Bathymetry from ICESat-2 Altimetry and Landsat Imagery."
*Scientific Data* 12, 1625. DOI: 10.1038/s41597-025-05911-y

**Dataset scope**: 510,530 global lakes/reservoirs (505,332 natural + 5,198 reservoirs),
covering 98.9% of global surface water storage capacity.

### Core Insight
Surface Water Occurrence (SWO) from Landsat acts as a proxy for depth contours. Pixels
that are underwater 100% of the time are the deepest. Pixels underwater only 10% of the
time are near the shoreline/shallowest. By linking SWO values to actual elevation
measurements from ICESat-2, you convert frequency contours into depth contours.

### Data Sources
1. **HydroLAKES** - polygons of global lakes and reservoirs
2. **Landsat Global Surface Water (GSW)** - Surface Water Occurrence (SWO) images
   (1984-2021, 30m resolution). SWO = (times detected as water) / (total valid observations)
3. **ICESat-2 ATL08** - Land and Vegetation Height product (terrain elevations, 2018-2022)

### Step-by-Step Algorithm

**Step 1: Build SWO-Area (SWO-A) Relationship**
- For each lake polygon, extract the SWO raster
- For each SWO percentile (0-100%), calculate cumulative water area
- Pixels with SWO=100% form the permanent water core (deepest area)
- Pixels with SWO=5% form the maximum historical extent (shallowest margins)
- This gives you a mapping: SWO percentile -> cumulative area

**Step 2: Link ICESat-2 Elevations to SWO Values**
- Extract all ICESat-2 ATL08 ground tracks that pass through the lake
- Each ATL08 footprint has a terrain elevation + geographic location
- Look up the SWO value at each ICESat-2 footprint location
- This pairs: (SWO value, elevation) for actual measured points
- Multiple ICESat-2 passes may sample different parts of the lake at different water levels

**Step 3: Build Area-Elevation (A-E) Relationship (L1 Product)**
- Combine Steps 1 and 2: SWO -> Area (from Step 1), SWO -> Elevation (from Step 2)
- This gives: Area -> Elevation (the A-E curve)
- Run multiple Monte Carlo simulations to find the best-fitting A-E curve
- Select the simulation with the largest elevation range as the best fit
- **L1 Product**: Raw A-E relationship directly from satellite observations
- Created for all 510,530 lakes

**Step 4: Interpolate/Extrapolate to L2 Product**
- L1 may have gaps (not every SWO percentile has an ICESat-2 measurement)
- **Linear interpolation** fills gaps within the observed range
- **Linear extrapolation** extends beyond the observed range
- **L2 Product**: A-E relationship from 5% to 95% SWO at 1% increments
- L2 provides smooth, complete curves but "may contain errors" from extrapolation

**Step 5: Convert A-E to 3D Bathymetry Map**
- For each pixel in the lake's SWO image, look up its SWO value
- Map SWO -> elevation using the L2 A-E curve
- Result: every pixel gets an elevation value = 3D bathymetry map
- Resolution: 30m (Landsat pixel size)

### Validation Results
- **A-E relationships**: RMSE = 0.60m, NRMSE = 0.14, R2 = 0.61 (validated against 214 in-situ A-E curves)
- **3D bathymetry maps**: RMSE = 1.37m, NRMSE = 0.26 (validated against 12 in-situ bathymetry maps)

---

## 2. Known Limitations of 3D-LAKES

### Fundamental Limitations of the A-E Approach

1. **Sparse ICESat-2 Coverage**: ICESat-2 has narrow ground tracks (~11m wide) with
   limited spatial coverage. Many small lakes have few or zero tracks crossing them.
   Lakes < 1 km2 are particularly under-sampled.

2. **Temporal Mismatch**: ICESat-2 data (2018-2022) overlaps with only the end of the
   Landsat GSW record (1984-2021). Water level at the time of ICESat-2 pass may not
   correspond to the historical SWO value at that location.

3. **Assumes Quiescent (Flat) Water Surface**: The method assumes all pixels at the
   same SWO have the same elevation. This breaks down for:
   - Rivers and flowing water (water surface slopes)
   - Very large lakes with wind setup
   - Reservoirs with steep draw-down gradients

4. **SWO Resolution Limitations**: Landsat's 30m resolution and 16-day revisit mean:
   - Small water level changes between revisits are missed
   - Narrow shoreline margins are poorly resolved
   - Cloud cover further reduces temporal sampling
   - Mixed pixels at lake edges introduce noise

5. **Linear Interpolation/Extrapolation (L2)**: Using linear interpolation between
   A-E points ignores the actual curvature of lake basins. Real lake bathymetry
   has complex, non-linear depth profiles. Extrapolation beyond observed range
   is particularly error-prone.

6. **No Within-Contour Spatial Detail**: The A-E approach assigns the SAME depth to
   ALL pixels with the same SWO value. A pixel on the north shore at SWO=50% gets
   the same depth as a pixel on the south shore at SWO=50%, even if the actual
   bathymetry differs. The method produces concentric ring-like depth patterns.

7. **Worst-Performing Lake Types**:
   - **Deep, steep-sided lakes**: Small SWO variation masks large depth changes
   - **Reservoirs with irregular draw-down**: Non-monotonic A-E relationships
   - **Very small lakes**: Insufficient ICESat-2 samples and SWO resolution
   - **Lakes with stable water levels**: Narrow SWO range = poor depth discrimination
   - **Turbid/eutrophic lakes**: SWO may misclassify water pixels
   - **Ice-covered lakes**: Seasonal ice confounds water detection

8. **No Below-Minimum Bathymetry**: The method can only map depths between the
   historical minimum and maximum water extent. Permanent deep areas below the
   minimum water level are extrapolated, not observed.

---

## 3. Improvement Techniques from Literature

### 3.1 SWOT Satellite Integration (HIGHEST IMPACT)

**Source**: SWOT launched Dec 2022; data available since Feb 2024 (Version C)

**Why it is transformative for A-E**:
- SWOT measures water surface elevation with 0.18m accuracy (vs ICESat-2's ~0.10m
  but only along sparse tracks)
- SWOT provides SIMULTANEOUS area + elevation on every pass (21-day repeat)
- SWOT covers lakes >= 250m x 250m globally with wide swath (120km)
- Provides DENSE time series of (area, elevation) pairs -- exactly what A-E needs
- The SWOT Prior Lake Database already stores hypsometric curves (WSE-area pairs)

**Specific improvement**: Instead of building A-E from sparse ICESat-2 points +
historical SWO, build A-E directly from SWOT's paired (area, WSE) measurements.
Each SWOT pass gives one point on the A-E curve. After 1-2 years of data, you get
a dense, directly-measured A-E curve with no SWO proxy needed.

**Expected impact**: Could reduce A-E RMSE from 0.60m to <0.30m by eliminating
the SWO-to-elevation mapping uncertainty.

### 3.2 Multi-Sensor Water Extent Fusion

**Sources**:
- Sentinel-2: 10m resolution, 5-day revisit (vs Landsat 30m/16-day)
- Sentinel-1 SAR: Cloud-penetrating, 10m resolution, 6-day revisit
- Harmonized Landsat-Sentinel (HLS): Combined 2-3 day revisit

**Why it helps**:
- 3x better spatial resolution (10m vs 30m) = better shoreline delineation
- 3x better temporal resolution = more water level states captured
- SAR works through clouds = no data gaps in tropical/cloudy regions
- Denser SWO time series = smoother, more accurate SWO contours

**Specific approach**: Replace Landsat GSW with a fused Sentinel-1 + Sentinel-2
surface water occurrence product. This would give ~10m resolution SWO with 3-6 day
effective revisit, dramatically increasing the number of unique water levels captured.

**Expected impact**: Moderate-High. Better SWO resolution improves spatial detail
of bathymetry maps. May reduce 3D RMSE by 0.2-0.5m.

### 3.3 ML-Predicted Hypsometric Curves

**Source**: Gudasz et al. (2025). "A comprehensive framework for integrating lake
hypsography and function on a global scale." *Nature Water* 3, 818-830.

**Key finding**: Lake hypsometry can be predicted from surrounding terrain features
using Random Forest ML. The shape parameter q (from Imboden model: q = Zmax/Zmean - 1)
parameterizes the hypsometric curve:
- q=2: perfect cone
- q<2: convex basin (shallow bowl)
- q>2: concave basin (steep sides, flat bottom)

**Features that predict hypsometry**:
- Elevation, slope, roughness of surrounding terrain (at multiple buffer scales)
- Topographic position index, profile/tangential curvature
- Terrain roughness index
- Separate models for small (<10 km2) and large (>10 km2) lakes

**How to use**: Instead of linear interpolation between A-E points (3D-LAKES L2),
use ML-predicted q parameter to fit a physics-consistent hypsometric curve through
the observed A-E points. This constrains the curve shape to be geomorphologically
plausible.

**Expected impact**: Moderate. Improves interpolation between A-E points. Most
beneficial for lakes with sparse A-E data. Could reduce RMSE by 0.1-0.3m.

### 3.4 Deep Learning from Surrounding Terrain (U-Net)

**Source**: Martinsen, Sand-Jensen & Selvan (2023). "Predicting lake bathymetry from
the topography of the surrounding terrain using deep learning." *Limnology and
Oceanography: Methods*. DOI: 10.1002/lom3.10573

**Methodology**: U-Net trained on 153 Danish lakes to predict bathymetry directly
from surrounding DEM topography. Input: DEM tiles around each lake. Output: predicted
depth at each pixel.

**Results**: MAE = 1.75m (validation), 2.15m (test). Much better than baseline
interpolation (3.12m). Generates realistic bathymetry without interpolation artifacts.

**How to combine with A-E**: Use the terrain-predicted bathymetry as a PRIOR, then
update/correct it using A-E observations. Where A-E data is dense, use A-E. Where
A-E is sparse, fall back to terrain prediction. This is essentially Bayesian fusion.

**Expected impact**: Moderate. Provides spatial detail within depth contours that
A-E cannot. Most valuable for lakes with complex morphometry.

### 3.5 DEM-Based Topographic Continuity Method

**Source**: EGUsphere preprint (2025). "Integrating Topographic Continuity and Lake
Recession Dynamics for Improved Bathymetry Mapping from DEMs."

**Methodology**: Simulates lake level recession using DEM, predicting underwater
terrain from shoreline topographic gradients. Requires only DEM data.

**Results**: Average NRMSE of 19.08% on 12 Tibetan Plateau lakes + Lake Mead.

**How to combine**: Use as another prior/constraint for bathymetry prediction,
especially for lakes near mountains where terrain continuity is strong.

### 3.6 Physics-Informed Constraints on A-E Curves

**Sources**: HybridBathNet (Frontiers 2025), PINNs for shallow water equations

**Key concepts**:
- A-E curves must be monotonically increasing (more area at higher elevations)
- Curves should be smooth (no sudden jumps in natural lakes)
- Physical concavity constraints (q parameter from hypsometry)
- Volume conservation: integral of A-E must match observed storage changes
- Depth positivity: all depths >= 0

**How to apply**: Add physics loss terms to any ML model predicting A-E curves:
- Monotonicity loss: penalize non-increasing A-E
- Smoothness loss: penalize high second derivatives
- Concavity constraint: enforce q > 0
- Volume consistency: match SWOT/altimetry volume change observations

### 3.7 Spectral SDB for Shallow Water Detail

**Sources**: BathyFormer (2025), HybridBathNet (2025), ICESat-2 + Sentinel-2 fusion

**Key insight**: Spectral satellite-derived bathymetry (SDB) uses water color/reflectance
to estimate depth in shallow, clear water. Works well for 0-10m depth in clear lakes.

**How to combine with A-E**:
- A-E gives the overall depth PROFILE (how deep at each contour)
- SDB gives within-contour SPATIAL VARIATION (local depth differences)
- Fusion: use A-E as the large-scale depth structure, SDB for small-scale detail
- A-E constrains the mean depth at each contour; SDB adds residual spatial variation

**Limitation**: Only works in clear-water lakes. Many productive/turbid lakes are excluded.

**Expected impact**: Low-Moderate for inland lakes (most are turbid). High impact for
a subset of clear-water lakes (alpine, oligotrophic).

### 3.8 Multi-Mission Altimetry Fusion

**Sources**: ICESat-2, SWOT, GEDI, Jason-3, Sentinel-6 Michael Freis

**Approach**: Combine water level measurements from all available satellites:
- ICESat-2: precise point elevations along tracks
- SWOT: wide-swath elevation + area (21-day repeat)
- GEDI: laser altimetry (2019-2023, some water targets)
- Jason-3/Sentinel-6: radar altimetry over large lakes (10-day repeat)

**Benefit**: Dramatically increases the number of (water level, water area) pairs
for building A-E curves. More temporal samples = better A-E estimation.

### 3.9 Transfer Learning from Surveyed to Unsurveyed Lakes

**Concept**: Train models on lakes with known bathymetry, then predict for unsurveyed
lakes using transferable features.

**Features for transfer**:
- Lake area, perimeter, shoreline development index
- Surrounding terrain: slope, elevation, geology
- Climate zone, latitude
- Hypsometric shape parameter q
- A-E curve shape from satellite data

**Approach**: Train a model that takes partial A-E data + morphometric features
and predicts the full high-resolution bathymetry. Use surveyed lakes (e.g., MN DNR's
4,500+ lakes) as training data.

### 3.10 Temporal Stacking for Denser SWO

**Concept**: Instead of a single SWO image (1984-2021), create multiple SWO epochs:
- SWO from wet years vs dry years
- SWO from different decades
- Seasonal SWO (summer vs winter)

**Benefit**: Captures more unique water level states. Drought periods expose lake
margins that are normally underwater, providing depth information for those areas.

---

## 4. Ranked Improvements by Impact and Feasibility

| Rank | Technique | Expected RMSE Improvement | Feasibility | Timeline |
|------|-----------|--------------------------|-------------|----------|
| 1 | SWOT Integration | 0.3-0.5m reduction | HIGH - data freely available | 2-3 months |
| 2 | Sentinel-1/2 Fused SWO | 0.2-0.5m reduction | HIGH - well-established tools | 2-3 months |
| 3 | ML Hypsometric Constraints | 0.1-0.3m reduction | HIGH - simple to implement | 1-2 months |
| 4 | Transfer Learning (MN DNR) | 0.2-0.4m reduction | MEDIUM - need training data pipeline | 3-4 months |
| 5 | Terrain DL Prior (U-Net) | 0.1-0.3m reduction | MEDIUM - needs GPU training | 2-3 months |
| 6 | Physics-Informed Loss | 0.1-0.2m reduction | HIGH - add to existing pipeline | 1 month |
| 7 | Multi-Mission Altimetry | 0.1-0.2m reduction | MEDIUM - data wrangling | 2-3 months |
| 8 | Spectral SDB Fusion | 0.1-0.3m (clear lakes only) | LOW - limited applicability | 3-4 months |
| 9 | DEM Recession Method | 0.1-0.2m reduction | MEDIUM | 2 months |
| 10 | Temporal SWO Stacking | 0.05-0.1m reduction | HIGH - simple | 1 month |

**Combined potential**: If techniques 1-6 are stacked, theoretical RMSE reduction
from 1.37m to potentially 0.5-0.8m. Sub-1m is achievable with SWOT + Sentinel fusion
+ ML constraints alone.

---

## 5. Concrete Action Plan

### Phase 1: Replicate 3D-LAKES Baseline (Weeks 1-4)

1. **Download 3D-LAKES data** from Zenodo (zenodo.org/records/13107867)
   - Get L1 and L2 A-E products, summary table, QA data
   - Focus on US lakes with known bathymetry for validation

2. **Collect validation data**
   - MN DNR lake bathymetry (4,500+ lakes with surveys)
   - USGS lake surveys
   - State agency bathymetric maps

3. **Replicate 3D-LAKES methodology**
   - Download Landsat GSW SWO images via GEE
   - Download ICESat-2 ATL08 data for target lakes
   - Implement the SWO-to-elevation mapping algorithm
   - Validate against MN DNR data to establish our baseline RMSE

4. **Benchmark**: Establish RMSE for US lakes specifically (3D-LAKES 1.37m is global average)

### Phase 2: SWOT Integration (Weeks 3-6, overlapping)

5. **Access SWOT data**
   - SWOT Level 2 Lake products from PO.DAAC
   - SWOT Prior Lake Database (PLD) with hypsometric curves
   - Use Hydrocron API for time series extraction

6. **Build SWOT-enhanced A-E curves**
   - Extract (WSE, area) pairs from each SWOT pass
   - Compare SWOT-derived A-E with 3D-LAKES A-E for validation lakes
   - Quantify improvement from direct measurement vs SWO proxy

7. **Fuse ICESat-2 + SWOT**
   - ICESat-2 for precise point elevations
   - SWOT for area-wide coverage and temporal density
   - Build combined A-E with uncertainty estimates

### Phase 3: Enhanced Water Extent (Weeks 5-8)

8. **Build Sentinel-based SWO**
   - Generate Sentinel-2 water occurrence maps at 10m resolution
   - Add Sentinel-1 SAR water extent for cloud-free coverage
   - Create fused 10m SWO product for target lakes

9. **Compare with Landsat SWO**
   - Quantify resolution improvement
   - Measure impact on A-E curve quality
   - Validate against known bathymetry

### Phase 4: ML Improvements (Weeks 7-12)

10. **Implement ML hypsometric prediction**
    - Extract terrain features around each lake (multiple buffer scales)
    - Train Random Forest to predict q parameter (Gudasz et al. approach)
    - Use predicted q to constrain A-E curve interpolation

11. **Train U-Net terrain model**
    - Train on MN DNR surveyed lakes
    - Input: DEM tiles around lake; Output: predicted bathymetry
    - Use as prior for unsurveyed lakes

12. **Build physics-informed ensemble**
    - Combine A-E (SWOT-enhanced), terrain DL, hypsometric ML
    - Physics constraints: monotonicity, smoothness, volume conservation
    - Weighted fusion based on data quality/availability per lake

### Phase 5: Transfer Learning Pipeline (Weeks 10-14)

13. **Create training dataset from surveyed lakes**
    - MN DNR surveys + satellite features
    - Surrounding terrain, A-E curve shape, morphometric indices
    - Train model to predict full bathymetry from partial satellite data

14. **Apply to unsurveyed lakes**
    - Generate predictions for all US lakes
    - Validate on held-out surveyed lakes
    - Quantify uncertainty per lake

### Phase 6: Validation & Publication (Weeks 13-16)

15. **Comprehensive validation**
    - Compare against 3D-LAKES, GLOBathy baselines
    - Report per-lake-type performance (size, depth, turbidity, region)
    - Generate confidence intervals

16. **Target metrics**:
    - A-E RMSE: < 0.30m (vs 3D-LAKES 0.60m)
    - 3D Bathymetry RMSE: < 0.80m (vs 3D-LAKES 1.37m)
    - Coverage: all US lakes > 4 ha in HydroLAKES

---

## 6. Key Data Access Points

| Data Source | URL | Resolution | Notes |
|-------------|-----|------------|-------|
| 3D-LAKES | zenodo.org/records/13107867 | 30m | A-E + bathymetry for 510K lakes |
| 3D-LAKES GEE App | planet-test-projectchi.projects.earthengine.app/view/d-lakes | 30m | Interactive explorer |
| SWOT Lake Products | podaac.jpl.nasa.gov/SWOT | ~250m min | WSE + area per pass |
| SWOT Hydrocron API | hydrocron.podaac.earthdatacloud.nasa.gov | - | Time series extraction |
| Landsat GSW | global-surface-water.appspot.com | 30m | SWO 1984-2021 |
| Sentinel-2 | earthdata.nasa.gov | 10m | 5-day revisit, optical |
| Sentinel-1 | earthdata.nasa.gov | 10m | 6-day revisit, SAR |
| ICESat-2 ATL08 | nsidc.org/data/atl08 | ~11m track | Terrain elevation |
| HydroLAKES | hydrosheds.org/page/hydrolakes | - | 1.4M lake polygons |
| GLOBathy | GEE catalog | 30m | Baseline comparison |
| MN DNR Bathymetry | dnr.state.mn.us | varies | 4,500+ surveyed lakes |

---

## 7. Why We Can Beat 3D-LAKES

1. **SWOT did not exist when 3D-LAKES collected data**. SWOT provides the dense,
   paired (area, elevation) time series that the A-E method fundamentally needs.
   3D-LAKES had to use the SWO proxy because direct measurements were too sparse.

2. **Sentinel-2 was underutilized**. 3D-LAKES used only Landsat GSW (30m, 16-day).
   Sentinel-2 (10m, 5-day) + Sentinel-1 SAR provides 3x better resolution and
   3-6x better temporal coverage.

3. **ML was not applied to curve fitting**. 3D-LAKES used linear interpolation
   for L2 products. ML-predicted hypsometric shapes + physics constraints would
   produce smoother, more accurate curves.

4. **No terrain information was used**. The surrounding topography is highly
   predictive of lake morphometry (Martinsen 2023, Gudasz 2025). 3D-LAKES
   ignores this entirely.

5. **We have validation data they didn't use**. MN DNR has 4,500+ surveyed lakes
   that can train and validate our models at a scale 3D-LAKES couldn't match.

6. **We can focus on US lakes**. 3D-LAKES optimized for global coverage (510K
   lakes). By focusing on the US, we can use denser data, better validation,
   and region-specific models.

---

## 8. References

- Huang et al. (2025). 3D-LAKES. *Scientific Data* 12, 1625
- Gudasz et al. (2025). Lake hypsography framework. *Nature Water* 3, 818-830
- Martinsen et al. (2023). Terrain-based bathymetry with U-Net. *L&O Methods*
- Gao et al. (2020). Reservoir bathymetry from ICESat-2 + Landsat. *RSE* 244, 111831
- Khazaei et al. (2022). GLOBathy global dataset. *Scientific Data* 9, 36
- EGUsphere (2025). Topographic continuity + recession dynamics for bathymetry
- SWOT PLD (Wang et al. 2025). *Water Resources Research*
- BathyFormer (2025). Transformer-based nearshore SDB. *Remote Sensing* 17(7)
- HybridBathNet (2025). Physics-guided DNN for SDB. *Frontiers Marine Science*
