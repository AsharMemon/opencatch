# Bathymetry Breakthroughs 2025-2026: Path to Sub-0.5m RMSE

**Date:** 2026-03-23
**Goal:** Achieve sub-0.5m RMSE for inland lake bathymetry prediction

---

## I. HIGHEST-IMPACT BREAKTHROUGHS (Implement First)

### 1. Depth Anything V2 for Satellite Bathymetry
- **Paper:** "Evaluation of Depth Anything Models for Satellite-Derived Bathymetry" (ISPRS 2025)
- **What:** Foundation vision model (Depth Anything V2) applied zero-shot to Sentinel-2 imagery
- **Accuracy:** V2 achieves RMSE=0.268m, MAE=0.209m (vs V1: RMSE=0.414m). R=84.5%
- **Data:** Sentinel-2 imagery, validated against NOAA NCEI bathymetric data
- **Why this matters:** Sub-0.3m RMSE from a pretrained model with NO in-situ training data. Fine-tuning on our MN DNR lakes could push even lower.
- **Action:** Fine-tune Depth Anything V2 on our MN DNR ground truth. Use as ensemble member. Code: `finetune_depth_anything.py` already exists -- update to V2 weights.

### 2. Multispectral ML for Inland Lakes (RMSE=0.25m achieved)
- **Paper:** "Development of an Inland Lake Bathymetry Estimation Model Based on Multispectral Data" (Sensors, 2025)
- **What:** Optimized Random Forest on multispectral bands for inland lake depth
- **Accuracy:** R²=0.92, RMSE=0.25m, MAE=0.11m (best site); R²=0.89/RMSE=0.30m and R²=0.80/RMSE=0.41m at other sites
- **Data:** Multispectral remote sensing (Sentinel-2 equivalent bands)
- **Why this matters:** Someone already hit 0.25m RMSE on inland lakes. Their feature engineering is key -- water quality indices + spectral ratios.
- **Action:** Replicate their feature engineering pipeline. Extract turbidity proxies, chlorophyll indices, CDOM indicators alongside raw bands. This is our most direct path to sub-0.5m.

### 3. BathyFormer: Transformer for Bathymetry
- **Paper:** "BathyFormer: A Transformer-Based Deep Learning Method" (Remote Sensing, 2025)
- **What:** First vision transformer applied to multispectral satellite bathymetry
- **Accuracy:** RMSE=0.45-0.73m (0.45m for <2m depth, 0.55-0.73m for 2-5m)
- **Data:** High-resolution multispectral imagery + CUDEM reference
- **Why this matters:** Transformers capture long-range spatial dependencies that CNNs miss. First proof that attention mechanisms help bathymetry.
- **Action:** Adapt BathyFormer architecture for inland lakes. Replace CUDEM with MN DNR ground truth. Already have `train_bathyformer.py`.

### 4. HybridBathNet: Physics-Guided Deep Learning
- **Paper:** "Physics-guided deep neural networks for bathymetric mapping" (Frontiers Marine Science, 2025)
- **What:** U-Net with embedded physical constraints (Beer-Lambert law) for depth-reflectance relationship
- **Accuracy:** Better generalization than pure DL; maintains physical consistency
- **Data:** Sentinel-2 L2A Bands 2, 3, 4
- **Why this matters:** Physics constraints prevent unphysical depth predictions, improve out-of-distribution generalization. Critical for unseen lakes.
- **Action:** Implement physics loss term (Beer-Lambert attenuation) into our ensemble. This directly addresses our unseen-lake R² problem.

---

## II. NEW DATA SOURCES (Major Enablers)

### 5. 3D-LAKES Global Dataset (510K lakes)
- **Paper:** "3D-LAKES: Three-Dimensional Global Lake and Reservoir Bathymetry" (Scientific Data, 2025)
- **What:** Area-elevation relationships + 3D bathymetry for 510,530 lakes globally
- **Accuracy:** A-E RMSE=0.60m, 3D bathymetry RMSE=1.37m
- **Data:** ICESat-2 altimetry + Landsat SWO, available on Zenodo
- **Why this matters:** Massive training data. Our current 4.5K MN lakes could be augmented with 510K global lakes for pretraining.
- **Action:** Already have `extract_3dlakes_depths.py`. Use 3D-LAKES A-E curves as pretraining targets, then fine-tune on MN DNR sonar data. The A-E curves alone give us hypsometric priors for every lake.

### 6. ICESat-2 ATL13 V7 + ATL24 (2025 releases)
- **What:** ATL13 V7 adds improved shallow water bathymetry parameters, better small lake support. ATL24 is new coastal/nearshore bathymetry product with refraction-corrected seafloor heights.
- **Accuracy:** ATL24 achieves R²>0.95 in 5-25m depth range, residuals as low as 0.34m
- **Data:** Global along-track profiles, free from NASA Earthdata
- **Why this matters:** ATL13 V7 specifically improved small lake/reservoir signal processing. More photon returns = more depth points per lake.
- **Action:** Re-fetch ICESat-2 data using V7. The improved small-lake processing directly helps our target lakes.

### 7. Copernicus Marine Global Coastal SDB (Nov 2025)
- **What:** Copernicus launched global satellite-derived bathymetry using 3 methods (intertidal, optical, wave-kinematics) with quality indices
- **Data:** Free via Copernicus Marine Service
- **Why this matters:** Another validation/pretraining source. Quality indices let us filter for reliable depths.
- **Action:** Download Copernicus SDB for US lake-adjacent coastal areas as additional training data.

### 8. GEBCO_2025 Grid
- **What:** Annual global terrain model update at 15 arc-second
- **Data:** Free from GEBCO
- **Why this matters:** Background bathymetric context for large lakes

---

## III. ARCHITECTURE INNOVATIONS (Medium-Term)

### 9. Swin-BathyUNet: No In-Situ Data Required
- **Paper:** "Deep learning-based bathymetry retrieval without in-situ depths" (ISPRS J. 2025)
- **What:** Swin Transformer + U-Net + cross-attention. Uses SfM-MVS derived DSMs as training targets instead of sonar data
- **Accuracy:** Improved coverage and noise reduction vs conventional methods
- **Data:** Remote sensing imagery + Structure-from-Motion derived depths
- **Why this matters:** Could generate training data for lakes without sonar surveys using only multi-view satellite imagery
- **Action:** Explore SfM from multi-date Sentinel-2 for lake margin depths. Use as supplementary training data.

### 10. Domain-Adaptive SDB for Cross-Region Generalization
- **Paper:** "Generalized satellite-derived bathymetry across spatial and temporal domains" (ISPRS J. 2025)
- **What:** Domain adaptation transfers bathymetry models across regions. Fine-tuning with just 15 samples reduced RMSE by >50%
- **Why this matters:** Directly addresses our unseen-lake problem. Train on MN lakes, adapt to other states with minimal data.
- **Action:** Implement domain adaptation layer in our ensemble. Use adversarial training to learn region-invariant features.

### 11. Adaptive Super-Resolution for Bathymetric Maps
- **Paper:** "Adaptive Super-Resolution for Ocean Bathymetric Maps" (Earth and Space Science, 2025)
- **What:** ESPCN network for 4x resolution enhancement of bathymetric maps. Data augmentation improved RMSE by 14.3%
- **Why this matters:** Can upscale our coarse predictions (30m Sentinel-2) to finer resolution
- **Action:** Post-processing step after ensemble prediction. Train on MN DNR high-res sonar vs downsampled versions.

### 12. Sentinel-2 + ICESat-2 Time-Series Fusion
- **Paper:** "High-accuracy shallow-water bathymetric method" (Frontiers Marine Science, 2024)
- **What:** Multi-temporal Sentinel-2 fusion removes surface noise, ICESat-2 provides depth calibration
- **Accuracy:** RMSE=0.39-0.65m after refraction correction
- **Action:** Stack 10+ Sentinel-2 images per lake to reduce noise. Fuse with ICESat-2 ATL13 V7 profiles.

---

## IV. GEOMORPHIC/PHYSICS APPROACHES

### 13. Topographic Continuity + Lake Recession (DEM-only)
- **Paper:** "Integrating Topographic Continuity and Lake Recession Dynamics" (EGUsphere preprint, 2025)
- **What:** Infers underwater terrain from shoreline DEM gradients by simulating water recession
- **Accuracy:** Validated on 12 Tibetan lakes + Lake Mead
- **Why this matters:** Works with FABDEM alone -- no optical/sonar needed. Excellent for initial depth estimates.
- **Action:** Implement as baseline/prior. FABDEM (30m bare-earth DEM) is freely available via GEE.

### 14. XGBoost Surface-to-Underwater Terrain Transfer
- **Paper:** "Simulation of lake underwater terrain based on XGBoost" (Big Earth Data, 2025)
- **What:** Uses surrounding DEM features to predict underwater terrain via XGBoost
- **Accuracy:** Relative error -11.8% mean depth, -26.9% max depth
- **Why this matters:** Quick feature engineering approach. Surface morphometry predicts underwater shape.
- **Action:** Already have `terrain_depth_model.py`. Enhance with features from this paper.

### 15. Gudasz et al. Lake Hypsography Framework
- **Paper:** "A comprehensive framework for integrating lake hypsography and function" (Nature Water, July 2025)
- **What:** Global composite lake analysis revealing 5 hypsometric clusters. Lakes mirror surrounding land topography.
- **Key insight:** Globally, shallow areas dominate; systematic differences between glaciated/non-glaciated regions
- **Why this matters:** MN lakes are glaciated -- we can use cluster-specific hypsometric priors. 43% of lake volume lies within the mixed layer.
- **Action:** Classify our target lakes into Gudasz clusters. Use cluster-matched A-E templates as Bayesian priors.

### 16. Martinsen U-Net: Terrain-to-Bathymetry
- **Paper:** "Predicting lake bathymetry from the topography of the surrounding terrain using deep learning" (L&O Methods, 2023 -- still SOTA for this approach)
- **What:** U-Net predicts bathymetry from surrounding DEM. MAE=1.75m (validation), 2.15m (test)
- **Why this matters:** Turbidity-independent. Works for eutrophic lakes where optical methods fail.
- **Action:** Already have `terrain_depth_model.py`. Consider as ensemble member for turbid lakes.

---

## V. NOVEL TECHNIQUES (Experimental)

### 17. Refractive NeRF for Bathymetry (NeRFrac)
- **Paper:** "Analysis of refraction aware Neural Radiance Fields" + "Exploring the Potential of Refractive NeRFs for Photogrammetric Bathymetry" (ISPRS 2025)
- **What:** NeRF with Snell's law for refraction modeling. Applied to UAV imagery of rivers.
- **Why this matters:** Could work with drone imagery over shallow lake margins. Provides 3D point clouds.
- **Feasibility:** Experimental. Requires multi-view UAV imagery. Not scalable to thousands of lakes yet.

### 18. SAR-Based Depth Inversion
- **Paper:** "Enhancing water depth inversion accuracy via SAR" (Frontiers Marine Science, 2025)
- **What:** Variable window sliding segmentation for SAR bathymetry
- **Limitation:** SAR bathymetry relies on wave refraction patterns -- primarily works in coastal/ocean settings with surface waves. NOT directly applicable to calm inland lakes.
- **Action:** Skip for inland lakes unless wind-wave patterns are significant.

### 19. ICESat-2 Advanced Photon Processing
- **Papers:** Multiple 2025 papers on improved photon classification
- **What:** ISDAF filtering, concentric ellipse algorithms, high-precision refraction correction (up to 5.46m displacement correction)
- **Accuracy:** ATL24 validated at 0.34m residuals (Ballyteige Bay)
- **Action:** Implement advanced photon filtering on ATL03 raw data for our lakes. The improved algorithms extract more valid depth photons.

---

## VI. DATA INFRASTRUCTURE

### 20. Crowdsourced Bathymetry (CSB)
- **Status:** IHO DCDB database growing weekly. FarSounder CSB Data Explorer launched 2024.
- **Limitation:** Primarily marine/coastal. Inland lake coverage minimal.
- **Opportunity:** Partner with Fishbrain/fishing communities to crowdsource fish-finder sonar readings from anglers. Even coarse depth readings at GPS coordinates are valuable training data.
- **Action:** Build sonar data ingestion pipeline for community-contributed fish finder readings.

### 21. MagicBathyNet Dataset
- **What:** New multimodal remote sensing dataset for bathymetry prediction and pixel-based classification
- **Data:** Available on Zenodo
- **Action:** Evaluate as additional training/benchmarking data.

---

## VII. IMPLEMENTATION PRIORITY MATRIX

### Phase 1: Quick Wins (1-2 weeks)
| Priority | Technique | Expected Impact |
|----------|-----------|----------------|
| 1 | Fine-tune Depth Anything V2 on MN DNR | Potential RMSE < 0.3m for clear lakes |
| 2 | Replicate inland multispectral RF pipeline (water quality features) | RMSE 0.25-0.41m range |
| 3 | Re-fetch ICESat-2 ATL13 V7 data | More depth points per lake |
| 4 | Download 3D-LAKES for pretraining | 510K lake A-E curves |

### Phase 2: Architecture Upgrades (2-4 weeks)
| Priority | Technique | Expected Impact |
|----------|-----------|----------------|
| 5 | Add physics loss (Beer-Lambert) to ensemble | Better unseen-lake generalization |
| 6 | Implement BathyFormer/transformer encoder | Capture long-range spatial patterns |
| 7 | Domain adaptation for cross-lake transfer | >50% RMSE reduction on new lakes |
| 8 | Multi-temporal Sentinel-2 stacking | Noise reduction, more stable predictions |

### Phase 3: Advanced Methods (4-8 weeks)
| Priority | Technique | Expected Impact |
|----------|-----------|----------------|
| 9 | Gudasz cluster-based hypsometric priors | Better max-depth estimates |
| 10 | Super-resolution post-processing | Finer spatial detail |
| 11 | Topographic continuity baseline | DEM-only depth estimates for data-sparse lakes |
| 12 | Crowdsourced angler sonar pipeline | Community-contributed training data |

---

## VIII. COMBINED ENSEMBLE ARCHITECTURE

```
Input Layer:
  Sentinel-2 multi-temporal stack (10+ dates)
  + ICESat-2 ATL13 V7 depth profiles
  + FABDEM terrain features
  + 3D-LAKES A-E priors
  + Water quality indices (turbidity, chl-a, CDOM)

Model Stack:
  1. Depth Anything V2 (fine-tuned) --> coarse depth map
  2. BathyFormer (transformer) --> spectral-spatial depth
  3. HybridBathNet (physics-constrained) --> physically consistent depth
  4. XGBoost (terrain features) --> morphometric depth
  5. Random Forest (water quality + spectral) --> turbidity-aware depth

Meta-Learner:
  Stacked ensemble with domain adaptation
  + Gudasz hypsometric cluster prior
  + Physics consistency check (Beer-Lambert)

Post-Processing:
  Super-resolution (4x via ESPCN)
  + Contour smoothing
  + Uncertainty quantification
```

**Target:** Sub-0.5m RMSE on seen lakes, sub-0.8m on unseen lakes

---

## IX. KEY PAPERS REFERENCE LIST

1. Depth Anything V2 for SDB -- ISPRS 2025
2. Inland Lake Multispectral ML (RMSE=0.25m) -- Sensors 2025, https://www.mdpi.com/1424-8220/25/7/2236
3. BathyFormer -- Remote Sensing 2025, https://www.mdpi.com/2072-4292/17/7/1195
4. HybridBathNet -- Frontiers Marine Sci 2025, https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2025.1636124/full
5. 3D-LAKES -- Scientific Data 2025, https://www.nature.com/articles/s41597-025-05911-y
6. Gudasz et al. Hypsography -- Nature Water 2025, https://www.nature.com/articles/s44221-025-00461-4
7. Swin-BathyUNet -- ISPRS J. 2025, https://arxiv.org/abs/2504.11416
8. Domain-Adaptive SDB -- ISPRS J. 2025, https://www.sciencedirect.com/science/article/abs/pii/S092427162500379X
9. Topographic Continuity -- EGUsphere 2025, https://egusphere.copernicus.org/preprints/2025/egusphere-2025-4180/
10. XGBoost Tibetan Lakes -- Big Earth Data 2025, https://www.tandfonline.com/doi/full/10.1080/20964471.2025.2515713
11. ICESat-2 ATL24 Validation -- Marine Geodesy 2025
12. ICESat-2 Bathymetry Algorithms Review -- ISPRS J. 2025, https://www.sciencedirect.com/science/article/pii/S0924271625001145
13. Refractive NeRFrac -- ISPRS 2025
14. Adaptive Super-Resolution -- Earth & Space Sci 2025, https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2024EA003610
15. Sentinel-2 + ICESat-2 Fusion -- Frontiers Marine Sci 2024
16. SWOT-derived SYSU_Topo -- Scientific Data 2026, https://www.nature.com/articles/s41597-026-06641-5
17. Martinsen U-Net Terrain -- L&O Methods 2023, https://aslopubs.onlinelibrary.wiley.com/doi/10.1002/lom3.10573
18. Deep Learning SDB Review -- Remote Sensing 2025, https://www.mdpi.com/2072-4292/18/5/720
19. FABDEM+ Multi-Source DEM -- Fathom Global 2025
20. Copernicus Marine Global Coastal SDB -- Nov 2025
