# Satellite-Derived Bathymetry (SDB) Research Review — March 2026

## Context
Our spectral-only bathymetry model (KAN on 2,470 ICESat-2 + Sentinel-2 points) achieved **4.65m RMSE** — far from the sub-1m target. This document compiles the latest literature and identifies what we're doing wrong and how to fix it.

---

## 1. Literature Review: State of the Art (2024-2026)

### 1.1 BathyFormer (March 2025)
- **Paper**: "BathyFormer: A Transformer-Based Deep Learning Method to Map Nearshore Bathymetry with High-Resolution Multispectral Satellite Imagery" — Remote Sensing 17(7):1195
- **Architecture**: Vision Transformer encoder adapted for multispectral satellite bands, trained on CUDEM reference bathymetry
- **Results**: RMSE 0.55-0.73m at 2-5m depth; 0.45m RMSE when refined for depths <2m
- **Improvement over RF**: MAE reduced 44-51%, RMSE reduced 43-44%, MAPE reduced 36-56% across three Chesapeake Bay test sites
- **Key insight**: Transformer's attention mechanism captures spatial context that point-wise models miss entirely

### 1.2 PHY-SDB: Dual-Physics-Guided Framework (2025)
- **Paper**: "PHY-SDB: a dual-physics-guided deep learning framework for hyperspectral satellite-derived bathymetry" — GIScience & Remote Sensing
- **Architecture**: FC-DNN and CNN-1D with dual physics constraints:
  1. **Input-level**: Inherent optical properties (IOPs) — chlorophyll-a and CDOM absorption coefficients from a bio-optical model
  2. **Loss-level**: Stumpf log-ratio loss function enforcing depth-reflectance consistency
- **Results**: RMSE 0.94-1.21m, R^2=0.97 (Qilianyu reef). Best config: S-IOP-PHYLoss
- **Key insight**: Physics constraints at BOTH input features AND loss function are critical

### 1.3 Swin-BathyUNet (January 2026)
- **Paper**: "From Bands to Depth: Understanding Bathymetry Decisions on Sentinel-2" — arXiv:2601.12636
- **Architecture**: Swin-Transformer U-Net with decoder-conditioned cross-attention on skip connections
- **Key findings**:
  - Leave-one-band-out study: green and blue bands are dominant for clear shallow water, consistent with water optics
  - Cross-region inference: MAE rises nearly linearly with depth; bimodal depth distributions worsen mid/deep errors
  - Decoder-conditioned cross-attention improves robustness to sun glint and foam
  - Model interpretability via adapted ablation-based CAM for regression (A-CAM-R)
- **Key insight**: Understanding WHICH bands matter and WHY is critical for generalization

### 1.4 Seabed-Net: Multi-Task Bathymetry (2025)
- **Paper**: "Seabed-Net: A multi-task network for joint bathymetry estimation and seabed classification" — ISPRS J. Photogrammetry
- **Architecture**: Dual-branch encoders with Attention Feature Fusion + windowed Swin-Transformer fusion, dynamic task uncertainty weighting
- **Results**: 10-30% RMSE reduction over single-task baselines, +8% classification accuracy
- **Key insight**: Joint training with seabed/bottom classification provides regularization that improves depth estimation

### 1.5 Depth Anything V2 for SDB (2025)
- **Paper**: "Evaluation of Depth Anything Models for Satellite-Derived Bathymetry" — ISPRS Archives, Vienna 2025
- **Results**: DA V2 achieved RMSE=0.27m, MAE=0.21m on test areas
- **Caveat**: These are relative depth estimates that require calibration with known depths; results are for specific clear-water coastal sites
- **Key insight**: Foundation models trained on millions of images have strong priors about depth from visual cues, even for satellite imagery

### 1.6 Physics-Guided DNN for Sentinel-2 (2025)
- **Paper**: "Physics-guided deep neural networks for bathymetric mapping using Sentinel-2 multi-spectral imagery" — Frontiers in Marine Science
- **Approach**: Multi-temporal data fusion to reduce IOP variation interference, physics-guided architecture
- **Key insight**: Multi-temporal composites dramatically improve robustness vs single-date imagery

### 1.7 Domain-Adaptive SDB (2025)
- **Paper**: "Generalized satellite-derived bathymetry across spatial and temporal domains: a domain-adaptive deep learning approach"
- **Results**: RMSE reduced by 0.27m and MAPE by 21.51% through domain adaptation
- **Key insight**: Models trained on one region fail badly on others without adaptation. Transfer learning with minimal local calibration data (as few as 15 samples) can reduce RMSE by >50%

### 1.8 SMART-SDB: Sample-Specific Band Ratios (2020)
- **Paper**: "SMART-SDB: Sample-specific multiple band ratio technique for satellite-derived bathymetry" — Remote Sensing of Environment
- **Approach**: Partitions spectral feature space into subspaces, each with its own optimal band ratio model. Uses k-NN to select the best local model for each pixel.
- **Key insight**: A single global band-ratio model is fundamentally insufficient for heterogeneous waters. Bottom type, water clarity, and surface conditions all vary spatially.

### 1.9 Blended Physical + ML Approaches (2025)
- **Paper**: "Blending physical and artificial intelligence models to improve satellite-derived bathymetry mapping" — Ecological Informatics
- **Results**: R^2 >= 0.8, RMSE <= 1.5m across all test sections
- **Key insight**: Ensemble of physics-based (Stumpf/Lyzenga) + ML models outperforms either alone

### 1.10 Inland Lake-Specific Studies (2025)
- **Paper**: "Research on the Development of an Inland Lake Bathymetry Estimation Model Based on Multispectral Data" — Sensors 25(7):2236
- **Tibetan Plateau studies**: MLP, Transformer, and KAN models achieved sub-meter RMSEs on clear plateau lakes using ICESat-2 + Sentinel-2
- **Key insight**: Clear inland lakes CAN achieve sub-meter with enough data (100K+ points) and proper preprocessing

### 1.11 Fast Feature Cascade for Turbid Waters (2025)
- **Paper**: "Satellite-Derived Bathymetry Using a Fast Feature Cascade Learning Model in Turbid Coastal Waters" — Journal of Remote Sensing
- **Approach**: Designed for small training datasets in turbid conditions
- **Key insight**: Specialized architectures exist for data-scarce scenarios

### 1.12 Multi-Temporal Sentinel-2 + ICESat-2 Fusion (2024)
- **Paper**: "High-accuracy shallow-water bathymetric method including reliability evaluation based on Sentinel-2 time-series images and ICESat-2 data" — Frontiers in Marine Science
- **Approach**: Fuses bathymetric estimates from all time-series images, not just one date
- **Results**: R^2=0.97, RMSE < 10% of max depth
- **Key insight**: Using multiple dates and fusing results is far superior to single-date imagery

---

## 2. 3D-LAKES Dataset Analysis

### 2.1 Overview
- **Paper**: Huang et al. (2025) "3D-LAKES: Three-Dimensional Global Lake and Reservoir Bathymetry from ICESat-2 Altimetry and Landsat Imagery" — Scientific Data 12, 1625
- **Coverage**: 510,530 global lakes and reservoirs (98.9% of global surface water storage capacity)
- **Data on Zenodo**: https://zenodo.org/records/13107867

### 2.2 Methodology
1. Pair Landsat Surface Water Occurrence (SWO) contours with ICESat-2 elevation data
2. Extract "Preliminary A-E Data Points" (area-elevation relationships)
3. Apply quality control to establish cleaned A-E relationships
4. **Level 1 (L1)**: Direct ICESat-2 + Landsat products
5. **Level 2 (L2)**: Interpolated and extrapolated from L1

### 2.3 Accuracy
- A-E relationships: **RMSE = 0.60m**, NRMSE = 0.14, R^2 = 0.61
- 3D bathymetry maps: **RMSE = 1.37m**, NRMSE = 0.26
- Validated against 214 A-E relationships and 12 in-situ bathymetry maps
- Superior to GLOBathy (1.42M lakes, but higher RMSE, especially for large lakes)

### 2.4 Can We Use 3D-LAKES as Training Labels?
**YES, with caveats:**
- The L1 A-E data points are derived from ICESat-2 (same source we use) but with better QC
- The L2 interpolated bathymetry could serve as pseudo-labels for spatial training (U-Net style)
- **RMSE of 1.37m on their 3D maps means using them as "ground truth" caps our accuracy at ~1.4m** unless we use only the L1 points
- **Best use**: Download L1 A-E points for US lakes as additional training data to supplement our ICESat-2 pipeline. Could 10x our training set.
- Access via Google Earth Engine code or Python API

### 2.5 Comparison with GLOBathy
- GLOBathy: 1.42M lakes but uses empirical equations (less accurate for individual lakes)
- 3D-LAKES: 510K lakes but uses actual satellite measurements (more accurate)
- 3D-LAKES has the smallest RMSE range across all lake sizes — more robust

---

## 3. Depth Anything V3 Status

### 3.1 Release
- **Published**: November 14, 2025 (arXiv: 2511.10647)
- **Code + Models**: Released on GitHub (ByteDance-Seed/Depth-Anything-3)
- **Streaming version**: DA3-Streaming released December 11, 2025
- **Academic paper**: Planned for ICLR 2026

### 3.2 Architecture
- Single plain transformer backbone (vanilla DINOv2 encoder)
- Unified depth-ray prediction target (no multi-task complexity)
- Supports monocular, multi-view, and camera pose estimation

### 3.3 Performance
- Surpasses prior SOTA (VGGT) by **35.7% in camera pose accuracy** and **23.6% in geometric accuracy**
- Outperforms DA V2 in monocular depth estimation
- Model variants: DA3Metric-Large (metric depth), DA3Mono-Large (relative depth)

### 3.4 Relevance for Our Bathymetry Task
**Mixed:**
- DA V2 was already evaluated for SDB with impressive results (RMSE 0.27m) but on specific clear coastal sites
- DA V3's improvements are primarily in multi-view consistency and camera pose — less relevant for satellite bathymetry
- **The metric depth variant (DA3Metric-Large) could be useful as a feature extractor** for transfer learning
- **Main limitation**: Trained on natural images, not satellite imagery. The spectral characteristics of water (especially in non-visible bands like NIR, SWIR, RedEdge) are completely outside its training distribution
- **Recommended approach**: Use DA V2/V3 as a pretrained backbone, fine-tune on satellite bathymetry data (our existing finetune_depth_anything.py approach is sound, but needs more data)

---

## 4. Why Our Model Failed: Diagnosis

### 4.1 Insufficient Training Data (CRITICAL)
- **Our data**: 2,470 points
- **Successful studies**: 10,000-100,000+ points minimum
  - Tibetan Plateau studies: 100K-1.4M ICESat-2 points
  - BathyFormer: Trained on CUDEM (continuous elevation model) — millions of points
  - Deep learning study: 75K training + 15K validation patches (40x40x4 px)
  - One well-resourced study: 52,000 in-situ measurements
- **Minimum viable**: ~10,000 points for ML, ~50,000+ for deep learning
- **Our 2,470 points is 4-40x too few for ML, 20-400x too few for deep learning**

### 4.2 Missing Preprocessing (CRITICAL)
What we're NOT doing that the literature requires:

1. **Atmospheric Correction**: We use raw L2A reflectance but don't verify or apply additional correction. The literature shows atmospheric correction errors can cause **up to 30% depth error**.

2. **Sun Glint Removal**: We have no sun glint filtering or correction. NIR-based de-glinting is standard practice. Sun glint can cause **30% error in SDB maps**.

3. **Water Column Correction**: No correction for water clarity variation (CDOM, chlorophyll, turbidity). PHY-SDB shows that including IOPs as features dramatically improves results.

4. **Multi-temporal Compositing**: We use single-date imagery. The literature strongly recommends multi-temporal fusion:
   - Reduces cloud/glint/atmospheric noise
   - Averages out temporal water clarity variations
   - Studies using multi-date achieve R^2=0.97

5. **Temporal Mismatch**: We don't control for time difference between ICESat-2 pass and Sentinel-2 scene. Turbidity, algal blooms, and water level can change significantly between dates.

### 4.3 Missing Physics Features (IMPORTANT)
Our features: raw S2 bands + log-ratios + NDWI + simple indices

What successful models add:
- **Stumpf log-ratio** (ln(blue)/ln(green)) — the foundational SDB feature
- **Lyzenga multi-band** linear transform
- **Inherent Optical Properties**: Chlorophyll-a concentration, CDOM absorption at 440nm, diffuse attenuation coefficient (Kd)
- **Bottom reflectance** estimates
- **Water surface roughness** indicators
- We have `cdom_proxy` and `log_blue_green` but these are crude approximations

### 4.4 Point-Wise vs Spatial Models (IMPORTANT)
- Our KAN is point-wise: each point predicted independently
- BathyFormer, Swin-BathyUNet, Seabed-Net all use **spatial context** (patches/tiles)
- Depth varies smoothly in space — spatial models capture this naturally
- Even a 64x64 pixel patch at 10m resolution captures 640m of spatial context

### 4.5 No Physics-Informed Loss
- Our loss: simple MSE
- PHY-SDB: Stumpf log-ratio loss enforcing Beer-Lambert law consistency
- BathyFormer: Physics-informed loss penalizing negative depth + monotonicity
- Physics losses prevent the model from learning physically impossible depth-reflectance relationships

### 4.6 Single Global Model
- SMART-SDB showed that a single band-ratio model fails on heterogeneous waters
- Different bottom types, water clarity levels, and sun angles all shift the depth-reflectance relationship
- We need either: ensemble of local models, or a model that conditions on water type

### 4.7 No Depth-Specific Treatment
- Water optics are fundamentally different at 0-2m, 2-5m, 5-15m, 15-30m
- Shallow water: bottom reflectance dominates
- Deep water: water column absorption dominates
- BathyFormer uses depth-gated heads (shallow/deep)
- Our KAN has multi-scale heads but trained on too little data to learn the distinction

---

## 5. Empirical Methods We Should Be Using

### 5.1 Stumpf Log-Ratio Model
```
depth = m1 * (ln(Rw_blue) / ln(Rw_green)) - m0
```
- Works up to ~25m in clear water
- Robust to bottom type variation (the ratio cancels it out)
- **We compute log_blue_green but don't use the raw Stumpf model as a baseline or ensemble member**

### 5.2 Lyzenga Multi-Band Linear Model
```
depth = a0 + a1*X1 + a2*X2 + ... + an*Xn
where Xi = ln(Ri - Ri_deep)
```
- Linear combination of log-transformed water-leaving reflectance (deep water subtracted)
- Works up to ~15m
- Requires deep-water pixel identification for Ri_deep correction

### 5.3 SMART-SDB (Best Empirical)
- Partitions feature space into subspaces via clustering
- Fits separate band-ratio models per subspace
- Uses k-NN to weight nearby models for each prediction pixel
- Handles heterogeneous waters much better than global models

### 5.4 Recommended Ensemble Stack
```
Layer 1 (Base models):
  - Stumpf log-ratio (simple, robust)
  - Lyzenga multi-band (captures multi-spectral info)
  - Random Forest on physics features
  - XGBoost on physics + derived features
  - KAN on all features (our existing model)

Layer 2 (Meta-learner):
  - Ridge regression or LightGBM on Layer 1 predictions
  - Include water clarity indicators as meta-features
```

---

## 6. Data Requirements

### 6.1 Training Data Volumes by Method
| Method | Minimum Points | Optimal Points | Notes |
|--------|---------------|----------------|-------|
| Stumpf/Lyzenga | 50-200 | 500+ | Per-site calibration |
| Random Forest | 1,000 | 10,000+ | Generalizes OK with fewer |
| SVM/SVR | 500 | 5,000+ | Best for small samples |
| XGBoost | 2,000 | 20,000+ | Needs diversity |
| KAN (ours) | 5,000 | 50,000+ | Current 2,470 is insufficient |
| CNN/Transformer | 10,000 | 100,000+ | Needs spatial patches |
| Fine-tuned DA V2 | 1,000 | 10,000+ | Transfer learning helps |

### 6.2 Our Path to Sufficient Data
1. **3D-LAKES L1 data** for US lakes: Could provide 50K-200K+ additional A-E points
2. **Expanded ICESat-2 via SlideRule**: Our build_icesat2_training_set.py covers 30+ US regions — if fully executed, could yield 50K-200K points
3. **State DNR bathymetry data** (MN has 4,500+ lake surveys): Already in fetch_state_bathymetry.py
4. **Multi-temporal S2**: Extract spectral values from 3-5 dates per point (not just one), effectively 3-5x multiplier on training data diversity
5. **MagicBathyNet benchmark** (3,300+ patches, 2 coastal sites): For pre-training/transfer learning
6. **CUDEM** (Continuously Updated DEM): Used by BathyFormer — covers US coastal areas with ~1m resolution bathymetry

### 6.3 Target Dataset
- **Phase 1**: 20,000+ points (ICESat-2 + state DNR surveys) — enables XGBoost/RF ensemble
- **Phase 2**: 100,000+ points (+ 3D-LAKES + expanded regions) — enables KAN/Transformer
- **Phase 3**: 500K+ spatial patches — enables BathyFormer/U-Net spatial models

---

## 7. Preprocessing Pipeline Improvements Needed

### 7.1 Atmospheric Correction (Priority: HIGH)
**Current**: Using L2A (Sen2Cor processed) reflectance — good but not sufficient
**Needed**:
- Verify L2A quality flags (SCL band) — mask cirrus, thin cloud, cloud shadow
- Consider additional correction via 6S radiative transfer code for water pixels
- At minimum: filter scenes by AOT (Aerosol Optical Thickness) < 0.15

### 7.2 Sun Glint Removal (Priority: HIGH)
**Current**: None
**Needed**:
- NIR-based de-glinting: For each pixel, estimate glint contribution from NIR band (where water-leaving radiance is ~0)
- Implementation: `R_deglint(band) = R(band) - slope * R(NIR)` where slope is computed from regression over water pixels
- Filter images with high glint (solar zenith angle constraints)
- Select scenes with solar zenith > 30 degrees, view zenith < 20 degrees

### 7.3 Water Pixel Masking (Priority: HIGH)
**Current**: Basic lake polygon masking
**Needed**:
- Modified NDWI threshold for water detection
- Remove mixed pixels at shoreline (1-2 pixel buffer inward from boundary)
- Remove floating vegetation, ice, and foam pixels
- Use S2 SCL (Scene Classification Layer) band 6 (water) and band 3 (cloud shadow)

### 7.4 Deep Water Correction (Priority: MEDIUM)
**Current**: None
**Needed for Lyzenga model**:
- Identify optically deep water pixels (where bottom is not visible)
- Compute Ri_deep (deep-water reflectance) for each band
- Subtract from all water pixels: Xi = ln(Ri - Ri_deep)
- This separates bottom signal from water column signal

### 7.5 Multi-Temporal Compositing (Priority: HIGH)
**Current**: Single best scene
**Needed**:
- Retrieve 3-10 cloud-free scenes per lake (across summer months)
- Per-pixel median composite (reduces noise, glint, atmospheric effects)
- Or: extract features from all dates, use temporal statistics (mean, std, min, max per band)
- build_s2_composites.py exists but needs integration with training pipeline

### 7.6 Physics-Derived Features (Priority: HIGH)
**Current**: Basic indices (NDWI, MNDWI, NDTI, FAI, cdom_proxy, log-ratios)
**Needed**:
```
# Stumpf ratio (the canonical SDB feature)
stumpf_ratio = ln(blue) / ln(green)     # already have as log_blue_green

# Lyzenga-style features (requires deep water correction)
lyzenga_blue = ln(blue - deep_blue)
lyzenga_green = ln(green - deep_green)
lyzenga_red = ln(red - deep_red)

# Water optical properties
kd_490 = diffuse_attenuation_at_490nm   # from empirical S2 algorithms
chl_a = chlorophyll_a_concentration     # from standard S2 water quality algorithms
cdom_440 = cdom_absorption_at_440nm     # bio-optical model
secchi_depth = water_transparency_proxy  # from blue/green ratio

# Bottom reflectance proxy
bottom_signal = (Rw - Rw_deep) * exp(2 * Kd * depth_estimate)

# Band ratio matrix (all pairwise log-ratios)
for each pair (i,j) in visible bands:
    ratio_ij = ln(Ri) / ln(Rj)
```

### 7.7 ICESat-2 Quality Filtering (Priority: MEDIUM)
**Current**: Basic quality flags
**Needed**:
- Refraction correction (Parrish et al. 2019) — light bends at water surface, raw depths are biased shallow
- Remove ice-covered observations
- Filter by photon density (high density = higher confidence)
- Cross-validate against 3D-LAKES for consistency checks
- ICESat-2 bathymetry accuracy: RMSE = 0.69m for clear lakes <16.5m depth

---

## 8. Concrete Action Plan to Achieve Sub-1m RMSE

### Phase 1: Data Scaling (Week 1-2)
1. **Run full build_icesat2_training_set.py** across all 30+ US regions — target 50K+ points
2. **Download 3D-LAKES L1 data** for overlap US lakes — add as supplementary training labels
3. **Integrate MN DNR bathymetry** surveys (4,500+ lakes) as high-quality ground truth
4. **Multi-temporal S2 extraction**: For each depth point, extract 3-5 cloud-free scene spectra

### Phase 2: Preprocessing Pipeline (Week 2-3)
5. **Implement sun glint removal** (NIR-based de-glinting)
6. **Implement deep water correction** (Lyzenga-style)
7. **Add physics-derived features**: Kd_490, Chl-a, CDOM_440, Secchi depth proxies
8. **Multi-temporal compositing**: Median composites + temporal statistics
9. **Strict water pixel masking**: SCL filtering + shoreline buffer

### Phase 3: Model Improvements (Week 3-4)
10. **Build ensemble baseline**:
    - Stumpf log-ratio (calibrated per-lake)
    - Lyzenga multi-band
    - Random Forest on physics features
    - XGBoost on all features
    - Stack with Ridge/LightGBM meta-learner
11. **Retrain KAN** with 50K+ points and physics features
12. **Implement PHY-SDB-style loss**: Stumpf log-ratio loss + Beer-Lambert monotonicity constraint
13. **Train depth-gated model**: Separate shallow (<5m) and deep (>5m) heads

### Phase 4: Spatial Models (Week 4-6)
14. **Build 64x64 S2 patch dataset** centered on depth points
15. **Train BathyFormer** (our existing train_bathyformer.py) on patch data with 50K+ patches
16. **Fine-tune Depth Anything V2/V3** on satellite bathymetry patches
17. **Implement Seabed-Net style** multi-task learning (depth + bottom type classification)

### Phase 5: Validation and Deployment (Week 6-8)
18. **Spatial cross-validation**: Train/test by lake (no leakage)
19. **Depth-binned evaluation**: Report RMSE for 0-2m, 2-5m, 5-10m, 10-20m, 20m+ bins
20. **Cross-region evaluation**: Train on MN, test on WI/MI (generalization check)
21. **Deploy best ensemble** to production

### Expected Results by Phase
| Phase | Model | Expected RMSE | Notes |
|-------|-------|--------------|-------|
| Current | KAN (2,470 pts) | 4.65m | Baseline — insufficient data |
| Phase 1+2 | XGBoost/RF ensemble (50K+ pts) | 1.5-2.0m | Data + preprocessing gains |
| Phase 3 | Physics-informed ensemble | 1.0-1.5m | Physics features + loss |
| Phase 4 | BathyFormer spatial | 0.5-1.0m | Spatial context + scale |
| Phase 5 | Final optimized ensemble | <1.0m target | Validated, production-ready |

---

## 9. Key Takeaways

### What the literature does that we don't:
1. **10-100x more training data** (most critical gap)
2. **Sun glint removal** (30% error source we're ignoring)
3. **Physics-derived features** (Kd, Chl-a, CDOM, Secchi depth)
4. **Multi-temporal compositing** (reduces noise from single-scene artifacts)
5. **Deep water correction** (Lyzenga-style background subtraction)
6. **Spatial context** (patch-based models, not just point-wise)
7. **Physics-informed loss functions** (enforcing Beer-Lambert law)
8. **Ensemble of empirical + ML** (Stumpf + Lyzenga + RF + DL)

### Critical path to sub-1m:
**More data + preprocessing >> model architecture**

The single most impactful change is going from 2,470 to 50,000+ training points with proper preprocessing. Model architecture improvements (transformers, ensembles) provide additional 30-50% RMSE reduction on top of the data foundation.

### Papers to implement first:
1. **PHY-SDB** (physics-guided features + loss) — directly applicable to our pipeline
2. **BathyFormer** (already have train_bathyformer.py) — needs patch data
3. **SMART-SDB** (local band-ratio models) — good ensemble member
4. **Multi-temporal fusion** (Frontiers 2024) — build_s2_composites.py needs integration

---

## 10. References

### Primary Sources
- Huang et al. (2025) 3D-LAKES — Scientific Data — https://www.nature.com/articles/s41597-025-05911-y
- 3D-LAKES Zenodo — https://zenodo.org/records/13107867
- BathyFormer (2025) — https://www.mdpi.com/2072-4292/17/7/1195
- PHY-SDB (2025) — https://www.tandfonline.com/doi/full/10.1080/15481603.2025.2594809
- Swin-BathyUNet (2026) — https://arxiv.org/abs/2601.12636
- Seabed-Net (2025) — https://arxiv.org/abs/2510.19329
- Depth Anything V3 (2025) — https://arxiv.org/abs/2511.10647
- DA V2 SDB Evaluation (2025) — https://isprs-archives.copernicus.org/articles/XLVIII-2-W10-2025/101/2025/
- SMART-SDB (2020) — https://www.sciencedirect.com/science/article/abs/pii/S0034425720304648
- MagicBathyNet — https://arxiv.org/abs/2405.15477
- GLOBathy — https://www.nature.com/articles/s41597-022-01132-9

### Additional References
- Physics-guided DNN for S2 bathymetry (2025) — https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2025.1636124/full
- Domain-adaptive SDB (2025) — https://www.sciencedirect.com/science/article/abs/pii/S092427162500379X
- S2 + hyperspectral SDB (2025) — https://www.mdpi.com/2072-4292/17/15/2594
- Blending physical + ML bathymetry (2025) — https://www.sciencedirect.com/science/article/pii/S1574954125003371
- Stumpf + RF integration (2024) — https://isprs-archives.copernicus.org/articles/XLVIII-4-W8-2023/387/2024/
- Multi-temporal S2 + ICESat-2 (2024) — https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2024.1470859/full
- ICESat-2 algorithms review (2025) — https://www.sciencedirect.com/science/article/pii/S0924271625001145
- Tibetan Plateau lake bathymetry (2024) — https://www.sciencedirect.com/science/article/abs/pii/S003442572400484X
- Inland lake bathymetry (2025) — https://www.mdpi.com/1424-8220/25/7/2236
- ICESat-2 Canadian lakes (2025) — https://www.mdpi.com/2073-4441/17/7/1098
- Copernicus global coastal SDB (2025) — https://marine.copernicus.eu/news/copernicus-marine-launches-global-coastal-satellite-derived-bathymetry
- SDB review (2025) — https://www.mdpi.com/2072-4292/18/5/720
