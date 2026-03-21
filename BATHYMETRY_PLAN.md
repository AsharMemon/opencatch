# Bathymetry Prediction Pipeline: Technical Plan

## Executive Summary

Generate depth/contour maps for ALL inland lakes and rivers in the US and Canada by combining satellite imagery (Sentinel-2, Landsat), ICESat-2 LiDAR transects, existing survey data, and deep learning. The system trains on ~15,000 surveyed lakes, then predicts bathymetry for the remaining ~300,000+ unsurveyed water bodies via domain-adaptive transfer learning. Output: vector tile contour maps served through MapLibre.

---

## 1. How Navionics Does It (Competitive Context)

Navionics (owned by Garmin) builds bathymetry from three sources:

1. **Official charts** -- Government hydrographic surveys (NOAA, CHS) form the base layer for navigable waters.
2. **SonarChart crowdsourced sonar** -- Boaters with compatible fishfinders/GPS automatically upload sonar logs. Navionics processes millions of these into 1ft/0.5m contour maps. This is their moat: years of accumulated sonar data from their installed base.
3. **Community Edits** -- Users manually correct errors (moved buoys, new docks, etc.).

**Key insight:** Navionics coverage is biased toward popular, navigable lakes. Thousands of smaller lakes, ponds, and rivers have zero sonar coverage. Satellite-derived bathymetry can fill these gaps at scale, covering water bodies no boat has ever mapped.

**Limitations of Navionics approach:**
- Requires physical boat presence
- Biased toward popular fishing lakes
- No coverage for remote/small water bodies
- Proprietary, expensive data

---

## 2. Data Sources and How to Access Them

### 2.1 Training Data: Known Bathymetry (Ground Truth)

| Source | Coverage | Access | Format | Approx Lakes |
|--------|----------|--------|--------|---------------|
| **Minnesota DNR LakeFinder** | MN, 4,500+ lakes | dnr.state.mn.us/lakefind | Shapefiles, contour PDFs | 4,500 |
| **Wisconsin DNR** | WI lakes | dnr.wi.gov | Scanned maps, some GIS | 2,000+ |
| **Indiana DNR** | IN lakes | in.gov/dnr | Depth maps | 500+ |
| **USGS Inland Bathymetry** | Select US lakes/rivers | usgs.gov/3dep | GeoTIFF DEMs, 1m resolution | 100+ (high quality) |
| **NOAA NCEI Bathymetry** | Great Lakes, coastal | ncei.noaa.gov/maps/bathymetry | Grid data, various formats | Great Lakes + coastal |
| **GLOBathy** | Global, 1.4M waterbodies | Google Earth Engine catalog | Raster, 1 arc-second | 1.4M (estimated max depth + synthetic shape) |
| **LAGOS-NE** | 17 NE/MW US states | lagoslakes.org | R package, CSV | 51,101 lakes (10K with depth) |
| **CHS NONNA** | All Canadian waters | data.chs-shc.ca | 10m/100m raster, WCS/WMS | Extensive |
| **MagicBathyNet** | 2 coastal sites | magicbathy.eu | Sentinel-2 patches + depth rasters | 2 sites (benchmark) |
| **State fish & wildlife agencies** | Per-state | Varies | PDF maps, shapefiles | Varies |

**Priority collection order:**
1. Minnesota DNR (richest GIS bathymetry dataset, 4,500+ lakes with digital contours)
2. LAGOS-NE (51K lakes with morphometry; ~10K have max/mean depth)
3. GLOBathy via GEE (1.4M lakes with estimated max depth -- synthetic but useful for pretraining)
4. USGS high-resolution DEMs (small count but extremely high quality)
5. CHS NONNA for Canadian waters
6. Other state DNRs as available

### 2.2 Satellite Imagery (Predictor Features)

| Source | Resolution | Bands | Revisit | Access |
|--------|-----------|-------|---------|--------|
| **Sentinel-2 MSI** | 10m (VNIR), 20m (SWIR) | 13 bands | 5 days | Google Earth Engine, Copernicus Open Access Hub |
| **Landsat 8/9 OLI** | 30m | 11 bands | 16 days | Google Earth Engine, USGS EarthExplorer |
| **ICESat-2 ATLAS** | 0.7m along-track | Photon-counting LiDAR | 91 days | NSIDC, OpenAltimetry |

**Key Sentinel-2 bands for bathymetry:**
- B1 (443nm, Coastal aerosol) -- deepest water penetration
- B2 (490nm, Blue) -- strong water penetration, primary depth band
- B3 (560nm, Green) -- moderate penetration, primary ratio band
- B4 (665nm, Red) -- shallow water only, bottom detection
- B8 (842nm, NIR) -- water/land masking (water absorbs NIR)

**ICESat-2 as training data:** Single-photon LiDAR can measure lake depths up to ~16.5m in clear water (RMSE = 0.69m vs in-situ). Use DBSCAN clustering to extract bathymetric photon returns. This provides sparse but accurate depth transects across thousands of lakes globally -- crucial training signal for lakes without survey data.

### 2.3 Auxiliary Datasets

| Dataset | Purpose | Access |
|---------|---------|--------|
| **NHDPlus HR** | Lake/river polygons, connectivity | apps.nationalmap.gov |
| **HydroLAKES** | Global lake polygons with attributes | hydrosheds.org |
| **LAGOS-NE GEO module** | Land use, climate, geology per lake | lagoslakes.org |
| **OpenStreetMap** | Lake outlines, names | overpass-turbo.eu |
| **SRTM/Copernicus DEM** | Surrounding terrain elevation | Google Earth Engine |
| **NLCD** | Land cover around lakes | mrlc.gov |

---

## 3. The Physics: Why Satellite Imagery Reveals Depth

### 3.1 Beer-Lambert Law

Light intensity decreases exponentially with depth:

```
I(z) = I(0) * exp(-K * z)
```

Where K is the diffuse attenuation coefficient (wavelength-dependent). Blue light penetrates deepest (~20-25m in clear water), green ~15m, red ~5m. By measuring how much light reflects back from the bottom at different wavelengths, depth can be inferred.

### 3.2 Stumpf Log-Ratio Model (Empirical Baseline)

The classic approach (Stumpf et al., 2003):

```
depth = m1 * (ln(Rw_blue) / ln(Rw_green)) - m0
```

Where m1 and m0 are calibrated against known depths, and Rw is water-leaving reflectance. The ratio cancels out bottom albedo effects -- a bright sandy bottom and dark muddy bottom at the same depth produce different absolute reflectances but similar ratios.

**Strengths:** Simple, interpretable, works well for uniform water bodies.
**Weaknesses:** Assumes spatially uniform water properties (turbidity, chlorophyll). Fails in turbid inland waters where optical properties vary. Maxes out at ~15-20m.

### 3.3 Why Deep Learning Beats Empirical Models

Inland lakes present challenges that break Stumpf assumptions:
- **Variable turbidity** (sediment plumes, algae blooms)
- **Mixed substrates** (rock, sand, vegetation, muck)
- **Shallow vegetated areas** (submerged aquatic vegetation confuses spectral signal)
- **Small lakes** where 10m pixels cover significant depth gradients

Deep learning can:
- Learn spatially-varying attenuation from multi-temporal imagery
- Incorporate auxiliary features (lake morphometry, surrounding terrain)
- Handle mixed pixels at lake edges
- Transfer patterns from surveyed to unsurveyed lakes

---

## 4. Model Architecture

### 4.1 Recommended: Two-Stage Architecture

#### Stage 1: Per-Lake Maximum Depth Estimator

**Purpose:** Predict Dmax for every lake, including those too deep/turbid for optical methods.

**Architecture:** Gradient-boosted ensemble (XGBoost/LightGBM)

**Features:**
- Lake surface area, perimeter, shape index (from NHDPlus)
- Surrounding terrain: mean slope, max elevation, relief ratio (from DEM)
- Watershed area, stream order (from NHDPlus connectivity)
- Geological substrate (from LAGOS-NE GEO)
- Latitude, longitude, elevation
- GLOBathy estimated Dmax as a feature (not target)
- Sentinel-2 spectral statistics (mean, std of bands across lake surface)
- Water clarity proxy (Secchi depth estimate from blue/green ratio)

**Training data:** LAGOS-NE lakes with known max depth (~10,000 lakes) + Minnesota DNR + other state surveys.

**Expected performance:** Based on GLOBathy validation, R-squared ~0.7-0.8 for max depth estimation from morphometric features alone.

#### Stage 2: Spatial Depth Distribution (the actual bathymetry map)

**Purpose:** Given a lake outline and estimated Dmax, predict the full 2D depth raster.

**Architecture:** U-Net variant with physics-informed loss

**Input channels (per pixel, multi-temporal composite):**
- Sentinel-2 bands B1-B4 (water-penetrating visible bands), median composite
- Sentinel-2 B8 (NIR, for water mask)
- Band ratios: ln(B2)/ln(B3), ln(B1)/ln(B3), ln(B2)/ln(B4)
- Distance-to-shore (computed from lake polygon)
- Surrounding terrain DEM (for lake basin shape context)
- Temporal features: seasonal median composites (summer/fall for minimum turbidity)

**Output:** Single-channel depth raster at 10m resolution

**Architecture details:**

```
Input: [H x W x C] multi-channel image patch centered on lake
  |
  v
Encoder (ResNet-34 backbone, pretrained on ImageNet)
  |-- Skip connections at each level
  v
Bottleneck (with spatial attention)
  |
  v
Decoder (transposed convolutions + skip connections)
  |
  v
Output: [H x W x 1] predicted depth (meters)
```

**Recommended variant:** Swin-BathyUNet -- combines U-Net with Swin Transformer self-attention to capture long-range spatial dependencies within the lake. Critical because depth at one point is correlated with the overall basin shape.

### 4.2 Physics-Informed Loss Function

The loss function should encode physical constraints:

```
L_total = L_data + lambda_1 * L_physics + lambda_2 * L_shape + lambda_3 * L_smooth

Where:
- L_data = MSE between predicted and observed depth at known points
- L_physics = penalty for violating Beer-Lambert attenuation relationship
- L_shape = penalty for predicted Dmax deviating from Stage 1 estimate
- L_smooth = gradient regularization (real lake beds are smooth, not jagged)
```

**Physics constraint (Beer-Lambert):**
If a pixel has reflectance R at wavelength lambda and predicted depth z, then:
```
L_physics = |z - (-1/K_lambda) * ln(R/R_bottom)|
```
This encourages the model to respect the exponential attenuation relationship even where ground truth is sparse.

**Shape constraint:**
Real lake basins follow predictable morphometric patterns. The depth profile along any transect from shore to center approximately follows:
```
d(x) = Dmax * (x / x_center)^n
```
where n is a shape parameter (typically 1.5-3.0). Penalize deviations from this.

### 4.3 Alternative Architectures Worth Evaluating

| Architecture | Pros | Cons | When to Use |
|-------------|------|------|-------------|
| **BathyFormer** (Vision Transformer) | Best at capturing global context | Needs more data, slower training | If dataset is large enough (>5K lakes) |
| **SegNet** | Memory efficient, good for small patches | Less accurate than U-Net variants | Resource-constrained deployment |
| **DeepLabv3+** | Strong multi-scale feature extraction | Heavier than U-Net | Lakes with complex multi-scale features |
| **ConvLSTM** | Handles temporal sequences natively | Complex training | If using time-series imagery |
| **Physics-informed CNN** | Encodes Beer-Lambert directly | Custom implementation needed | When optical conditions are well-characterized |

---

## 5. Training Strategy

### 5.1 Phase 1: Pretraining on GLOBathy (Synthetic Depth)

GLOBathy provides estimated bathymetry for 1.4M lakes based on morphometric relationships. While these are approximations (not survey-quality), they provide:
- Basin shape patterns (where is the deepest part relative to the outline)
- Scale relationships (how depth varies with lake size)
- Geographic patterns (mountain lakes vs prairie lakes)

**Pretrain the U-Net on GLOBathy rasters paired with Sentinel-2 imagery.** This teaches the model the general relationship between spectral signals and depth. The model learns to "read" the bottom through the water.

**Training set:** ~50,000 lakes with GLOBathy rasters + cloud-free Sentinel-2 composites.

### 5.2 Phase 2: Fine-Tuning on Survey-Quality Data

Fine-tune on lakes with actual bathymetric surveys:
- Minnesota DNR: ~4,500 lakes with contour-derived DEMs
- USGS high-res surveys: ~100 lakes with 1m DEMs
- ICESat-2 transects: sparse but accurate depth points across thousands of lakes

**Domain adaptation approach (DA-SDB):**
Use a domain-adversarial training setup with three components:
1. **Feature extractor** (shared encoder) -- learns spectral-to-depth features
2. **Depth predictor** (decoder) -- predicts bathymetry
3. **Domain aligner** -- minimizes distribution shift between surveyed lakes (source domain) and unsurveyed lakes (target domain) by aligning feature statistics

This lets the model generalize from well-surveyed Minnesota lakes to unsurveyed lakes in other states.

### 5.3 Phase 3: ICESat-2 Self-Supervised Refinement

ICESat-2 provides depth measurements along narrow ground tracks across many lakes globally. For each lake with an ICESat-2 overpass:
1. Extract photon returns using DBSCAN clustering
2. Apply refraction correction for water/air interface
3. Use these sparse transects as additional supervision

This is effectively semi-supervised learning: the model predicts a full 2D depth map, but is supervised only along the 1D ICESat-2 ground track. The physics-informed loss and smoothness constraints fill in the gaps.

### 5.4 Validation Strategy

**Hold-out lakes:** Reserve 20% of surveyed lakes (stratified by state, size, depth, trophic status) for final evaluation.

**Cross-validation:** Leave-one-state-out CV to test geographic generalization. Train on all states except X, test on X.

**Metrics:**
- RMSE (m) -- overall depth error
- MAE (m) -- less sensitive to outliers
- R-squared -- variance explained
- Error by depth bin (0-2m, 2-5m, 5-10m, 10-20m, 20m+)
- Contour accuracy -- Hausdorff distance between predicted and true contour lines at standard intervals (2ft, 5ft, 10ft)

**Expected performance based on literature:**
- Clear shallow lakes (<10m): RMSE 0.5-1.5m
- Moderate depth lakes (10-20m): RMSE 1.5-3.0m
- Deep/turbid lakes (>20m): Optical methods fail; rely on morphometric estimation from Stage 1
- Lakes with ICESat-2 overpasses: RMSE 0.7-1.5m

---

## 6. Inference Pipeline: Generating Bathymetry at Scale

### 6.1 Processing Steps per Lake

```
1. LAKE IDENTIFICATION
   - Query NHDPlus HR for lake polygon
   - Extract lake ID, area, perimeter, elevation

2. SATELLITE IMAGE ACQUISITION (Google Earth Engine)
   - Pull Sentinel-2 L2A (atmospherically corrected) time series for lake bbox
   - Filter: cloud cover <20%, sun elevation >30 degrees
   - Create seasonal median composites (summer, fall) to minimize turbidity/ice
   - Compute band ratios: ln(B2)/ln(B3), ln(B1)/ln(B3)

3. FEATURE ENGINEERING
   - Distance-to-shore raster (from lake polygon)
   - Surrounding terrain DEM (30m SRTM or 10m 3DEP)
   - Water clarity estimate from green/blue ratio
   - Lake morphometric features for Stage 1

4. DEPTH PREDICTION
   - Stage 1: Predict Dmax from morphometric + spectral features
   - Stage 2: Run U-Net on multi-channel image patch
   - Clip predictions to [0, Dmax * 1.1]
   - Apply water mask (NIR threshold)

5. POST-PROCESSING
   - Gaussian smoothing (sigma=1 pixel) to remove artifacts
   - Enforce monotonic depth increase toward lake center
   - Fill small holes/artifacts with interpolation
   - Clip to lake polygon boundary

6. OUTPUT
   - GeoTIFF depth raster (10m resolution)
   - Contour lines at standard intervals
   - PMTiles for MapLibre serving
```

### 6.2 Scale Estimates

| Item | Count/Size |
|------|-----------|
| US + Canada lakes >1 hectare | ~300,000+ |
| Sentinel-2 tiles needed | ~5,000 (each covers 100x100km) |
| GEE processing (estimate) | ~500 compute-hours |
| U-Net inference per lake | ~0.1-5 seconds (depending on size) |
| Total inference time (GPU) | ~50-100 GPU-hours |
| Storage (depth rasters) | ~500 GB - 1 TB |
| Storage (vector contours) | ~50-100 GB |
| Storage (PMTiles) | ~10-20 GB |

---

## 7. Contour Generation and Vector Tile Pipeline

### 7.1 Raster to Contour Lines

Use GDAL for contour extraction from predicted depth rasters:

```
gdal_contour -a depth -i 2 -f GeoJSON lake_depth.tif lake_contours.geojson
```

Parameters:
- `-i 2` -- 2-foot contour interval (adjustable per use case)
- `-a depth` -- attribute name for depth value
- Smooth contours with `-snodata` to handle NaN areas

For production, use contour intervals based on lake depth:
- Lakes <10ft max: 1ft contours
- Lakes 10-30ft: 2ft contours
- Lakes 30-100ft: 5ft contours
- Lakes >100ft: 10ft contours

### 7.2 Vector Tile Generation

Convert contour GeoJSON to PMTiles using tippecanoe:

```
tippecanoe -o bathymetry.pmtiles \
  --layer=contours \
  --minimum-zoom=8 \
  --maximum-zoom=16 \
  --simplification=10 \
  --drop-densest-as-needed \
  --extend-zooms-if-still-dropping \
  lake_contours.geojson
```

Alternatively, for dynamic rendering: use **maplibre-contour** plugin to generate contours on-the-fly from raster DEM tiles in the browser. This avoids pre-generating massive contour tile archives.

### 7.3 MapLibre Integration

Two approaches for rendering bathymetry in MapLibre:

**Option A: Pre-generated vector contour tiles (PMTiles)**
- Pros: Fast rendering, works offline, precise control over styling
- Cons: Large storage, must regenerate when model improves
- Serve PMTiles from S3/R2 static storage
- Style contour lines with depth-dependent colors (light blue -> dark blue)

**Option B: Dynamic contour rendering from raster DEM tiles**
- Pros: Smaller storage (raster tiles compress well), infinite style flexibility
- Cons: More client-side computation, requires maplibre-contour plugin
- Encode depth rasters as Terrain-RGB tiles
- maplibre-contour generates contour lines client-side from DEM tiles

**Recommended: Option A for mobile app (pre-generated PMTiles for performance), Option B for web map (dynamic flexibility).**

### 7.4 Tile Serving Architecture

```
                   [S3/R2 Static Storage]
                          |
                   [PMTiles files]
                     /          \
            [Mobile App]     [Web Map]
            (pre-rendered    (maplibre-contour
             contour tiles)   dynamic rendering)
```

Each lake gets:
1. A depth raster PMTiles file (for hillshade/dynamic contours)
2. A contour vector PMTiles file (for pre-rendered contour lines)
3. Metadata JSON (Dmax, area, data quality score, source attribution)

---

## 8. Data Quality and Confidence Scoring

Not all predictions are equal. Assign a confidence score per lake:

| Factor | High Confidence | Low Confidence |
|--------|----------------|----------------|
| Water clarity | Secchi >3m | Secchi <1m |
| Lake size | 10-1000 ha | <1 ha or >10,000 ha |
| Max depth | <15m | >25m |
| Training data proximity | Surveyed lake in same ecoregion | No nearby training data |
| ICESat-2 overpasses | Has transect | No transect |
| Satellite coverage | Multiple clear images | Few/cloudy images |

Display confidence as a quality badge on each lake's bathymetry map:
- **Gold**: Survey-quality data or high-confidence prediction with ICESat-2 validation
- **Silver**: Good spectral signal, nearby training data, moderate depth
- **Bronze**: Morphometric estimation only (deep/turbid lakes)

---

## 9. Phased Implementation Roadmap

### Phase 0: Data Collection (2-3 weeks)
- Download Minnesota DNR bathymetric shapefiles/DEMs
- Set up Google Earth Engine access
- Pull LAGOS-NE dataset via R package, export to CSV
- Download GLOBathy from GEE community catalog
- Acquire NHDPlus HR lake polygons for target states
- Pull ICESat-2 ATL03 data for lakes with known bathymetry

### Phase 1: Baseline Model (3-4 weeks)
- Implement Stumpf log-ratio model as baseline
- Train Stage 1 Dmax estimator on LAGOS-NE + MN DNR
- Evaluate: how well does morphometry alone predict max depth?
- Process Sentinel-2 composites for Minnesota lakes via GEE

### Phase 2: Deep Learning Model (4-6 weeks)
- Pretrain U-Net on GLOBathy + Sentinel-2 (50K lakes)
- Fine-tune on MN DNR survey data (~4,500 lakes)
- Implement physics-informed loss function
- Add ICESat-2 semi-supervised training
- Cross-validate: leave-one-state-out

### Phase 3: Scale to US + Canada (2-3 weeks)
- Run inference on all NHDPlus lakes via GEE batch processing
- Generate depth rasters and contour lines
- Build PMTiles archive
- Quality scoring per lake

### Phase 4: Integration (1-2 weeks)
- Serve PMTiles from S3/R2
- Integrate into MapLibre mobile map
- Add depth contour styling and lake detail view
- Display confidence badges

### Phase 5: Continuous Improvement (ongoing)
- Ingest new state survey data as it becomes available
- Process new Sentinel-2 imagery seasonally
- Accept user-submitted sonar logs (like Navionics SonarChart)
- Retrain model quarterly

---

## 10. Key Technical Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Turbid water kills optical signal | No depth info for muddy lakes | Fall back to morphometric estimation (Stage 1 only), flag as low confidence |
| Depth limit ~20m for optical | Cannot map deep lakes fully | Hybrid: optical for shallow zones, morphometric interpolation for deep zones |
| Cloud cover limits Sentinel-2 | Fewer usable images | Multi-temporal composites, use Landsat as backup, target summer/fall windows |
| Transfer learning fails across regions | Model trained in MN doesn't generalize | Domain adaptation with domain aligner, ICESat-2 anchoring across regions |
| Minnesota DNR data format issues | Can't ingest training data cleanly | Budget time for data cleaning/conversion |
| GEE compute quotas | Processing bottleneck | Batch processing, use paid GEE if needed, or planetary-computer alternative |
| Submerged vegetation confuses signal | Overestimates shallow depth | Add NDVI-like vegetation index as input feature, train on lakes with known vegetation |

---

## 11. Competitive Advantage vs Navionics

| Feature | Navionics | OpenCatch Satellite Bathymetry |
|---------|-----------|-------------------------------|
| Coverage | Popular/navigable lakes only | ALL lakes including remote/small |
| Data source | Crowdsourced sonar (requires boats) | Satellite (no boat needed) |
| Update frequency | When boaters visit | Every 5 days (Sentinel-2 revisit) |
| Accuracy (surveyed lakes) | Very high (direct sonar) | Moderate (model-dependent) |
| Accuracy (unsurveyed lakes) | None | Moderate (satellite + morphometric) |
| Cost to user | $25-50/year subscription | Free (open data inputs) |
| River coverage | Limited | Full (from satellite) |
| Small ponds | None | Yes |

The strategy is NOT to compete head-to-head on accuracy for popular bass lakes (where Navionics sonar data is excellent), but to provide bathymetry coverage where none exists today, and to do so for free at continental scale.

---

## 12. Key References and Resources

### Papers
- Stumpf et al. (2003) -- Original log-ratio model for SDB
- Khojasteh et al. (2022) -- GLOBathy: Global Lakes Bathymetry Dataset
- Lumban-Gaol et al. (2024) -- Physics-guided deep neural networks for bathymetric mapping using Sentinel-2
- Agrafiotis et al. (2025) -- BathyFormer: Transformer-based bathymetry from multispectral satellite imagery
- Paganini et al. (2024) -- MagicBathyNet benchmark dataset
- Ma et al. (2020) -- Satellite-derived bathymetry using ICESat-2 and Sentinel-2
- Li et al. (2025) -- Domain-adaptive deep learning for generalized SDB

### GitHub Repositories
- pagraf/MagicBathyNet -- Benchmark dataset and deep learning baselines
- yustisiardhitasari/sdbcnn -- CNN for satellite-derived bathymetry
- Ocean-Technologies/satellite_bathymetry -- ML-based SDB on AWS
- onthegomap/maplibre-contour -- Dynamic contour rendering in MapLibre
- felt/tippecanoe -- Vector tile generation from GeoJSON
- protomaps/PMTiles -- Single-file tile archive format
- nasa/delta -- Deep learning framework for satellite imagery

### Data Portals
- Google Earth Engine: earthengine.google.com (Sentinel-2, Landsat, GLOBathy, HydroLAKES)
- USGS National Map: apps.nationalmap.gov (NHDPlus HR)
- NSIDC/OpenAltimetry: openaltimetry.org (ICESat-2)
- CHS NONNA: data.chs-shc.ca (Canadian bathymetry)
- Minnesota DNR: dnr.state.mn.us/lakefind (lake surveys)
- LAGOS: lagoslakes.org (lake attributes and morphometry)
