# Satellite Bathymetry Preprocessing Guide
## Achieving Sub-0.5m Lake Bathymetry Through Rigorous Preprocessing

**Created:** 2026-03-23
**Purpose:** Comprehensive preprocessing pipeline for the OpenCatch satellite bathymetry system.
Literature suggests proper preprocessing alone can reduce RMSE by 30-50%.

---

## Table of Contents
1. [Pipeline Overview](#pipeline-overview)
2. [Step 1: Cloud & Shadow Masking](#step-1-cloud--shadow-masking)
3. [Step 2: Atmospheric Correction](#step-2-atmospheric-correction)
4. [Step 3: Sun Glint Removal](#step-3-sun-glint-removal)
5. [Step 4: Adjacency Effect Correction](#step-4-adjacency-effect-correction)
6. [Step 5: Water Surface Detection & Edge Handling](#step-5-water-surface-detection--edge-handling)
7. [Step 6: Temporal Compositing](#step-6-temporal-compositing)
8. [Step 7: Water Column Correction](#step-7-water-column-correction)
9. [Step 8: DEM Preprocessing](#step-8-dem-preprocessing)
10. [Step 9: Elevation Data Preprocessing (ICESat-2/SWOT)](#step-9-elevation-data-preprocessing)
11. [Step 10: A-E Curve Construction & Smoothing](#step-10-a-e-curve-construction--smoothing)
12. [Step 11: Vertical Datum Harmonization](#step-11-vertical-datum-harmonization)
13. [Step 12: Data Quality Filtering & Validation](#step-12-data-quality-filtering--validation)
14. [Pre-Processed Datasets](#pre-processed-datasets)
15. [Priority Summary](#priority-summary)
16. [Implementation Roadmap](#implementation-roadmap)

---

## Pipeline Overview

The preprocessing pipeline must be executed in order. Each step depends on prior steps.

```
Raw Satellite Imagery (Sentinel-2 L1C)
    |
    v
[1] Cloud & Shadow Masking .............. (CRITICAL)
    |
    v
[2] Atmospheric Correction (ACOLITE) .... (CRITICAL)
    |
    v
[3] Sun Glint Removal ................... (CRITICAL)
    |
    v
[4] Adjacency Effect Correction ......... (CRITICAL for small lakes)
    |
    v
[5] Water Surface Detection & Edges ..... (IMPORTANT)
    |
    v
[6] Temporal Compositing ................ (CRITICAL)
    |
    v
[7] Water Column Correction ............. (IMPORTANT for spectral SDB)
    |
    v
[Rrs-clean imagery ready for spectral bathymetry models]


DEM Data (SRTM/Copernicus/FABDEM)      ICESat-2 / SWOT Altimetry
    |                                        |
    v                                        v
[8] DEM Preprocessing .................. [9] Altimetry Preprocessing
    |                                        |
    v                                        v
[11] Vertical Datum Harmonization <------+---+
    |
    v
[10] A-E Curve Construction
    |
    v
[12] Data Quality Filtering & Validation
    |
    v
[FINAL: Bathymetry-ready dataset]
```

---

## Step 1: Cloud & Shadow Masking

### What & Why
Cloud and cloud shadow pixels contaminate spectral reflectance, making depth estimation impossible. The Sentinel-2 Scene Classification Layer (SCL) is known to confuse water with cloud shadow (both are dark), causing systematic errors in water body analysis.

### Accuracy Impact
- **Without masking:** Depth errors of 50-100% in affected pixels
- **With proper masking:** Eliminates ~15-30% of unusable pixels that would otherwise corrupt models
- **SCL misclassification rate for water:** ~10-20% of water pixels flagged as cloud shadow

### Implementation

**Primary approach: s2cloudless + custom water mask**

```python
# s2cloudless provides cloud probability (0-100%) per pixel
# Use threshold of 40-50% for cloud masking (tunable)
# Then fix water/shadow confusion:

# 1. Get s2cloudless probability
cloud_prob = ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY')

# 2. Get SCL for initial water detection
scl = image.select('SCL')
water_scl = scl.eq(6)  # SCL class 6 = water

# 3. Exclude water pixels from shadow detection
# SCL classes: 3=cloud_shadow, 6=water
# If pixel is dark AND within known water body boundary -> water, not shadow
shadow_scl = scl.eq(3)
false_shadow = shadow_scl.And(known_water_mask)  # from JRC/HydroLAKES
corrected_shadow = shadow_scl.And(false_shadow.Not())

# 4. Final clean mask
clean_mask = cloud_prob.lt(50).And(corrected_shadow.Not())
```

**Fallback: Fmask 4.0**
- Fmask cloud shadow accuracy is ~70%, but better than SCL for some cases
- Available in Google Earth Engine as `LANDSAT/*/C02/T1_L2` QA bands

### Tools
| Tool | Type | Notes |
|------|------|-------|
| **s2cloudless** | Python/GEE | Best cloud detection for S2; ~90% accuracy |
| **Fmask 4.0** | Python (`pyfmask`) | Good for Landsat; ~70% shadow accuracy |
| **Sen2Cor SCL** | Built into S2 L2A | Free but poor water/shadow distinction |
| **JRC Global Surface Water** | GEE dataset | Use occurrence band as water prior |
| **HydroLAKES** | Shapefile | Known lake boundaries for shadow filtering |

### Priority: CRITICAL
Every downstream step fails if cloud/shadow pixels remain.

---

## Step 2: Atmospheric Correction

### What & Why
Converts Top-of-Atmosphere (TOA) radiance to surface reflectance (Rrs), removing atmospheric scattering and absorption. This is THE most impactful preprocessing step for spectral bathymetry. Standard Sen2Cor is designed for land; it performs poorly over water bodies.

### Accuracy Impact
- **Sen2Cor vs ACOLITE for bathymetry:** ACOLITE reduces depth RMSE by 20-40%
- **Sen2Cor:** Poor agreement with in-situ depths, especially 5-15m range
- **ACOLITE DSF:** Best correlation with survey soundings in 5-15m depth range
- **Uncorrected TOA vs corrected Rrs:** Depth errors up to 3-5m without correction

### Implementation: ACOLITE (Recommended)

```python
# ACOLITE is a Python package from RBINS (Royal Belgian Institute)
# pip install acolite (or clone from github.com/acolite/acolite)

import acolite as ac

# Settings for inland water processing
settings = {
    'inputfile': '/path/to/S2_L1C.SAFE',
    'output': '/path/to/output/',
    'l2w_parameters': ['Rrs_*', 'rhow_*'],  # Remote sensing reflectance
    'atmospheric_correction': 'dark_spectrum',  # DSF method
    'dsf_aot_estimate': 'fixed',  # or 'tiled' for large scenes
    'dsf_residual_glint_correction': True,  # Built-in glint handling
    'l2w_mask': True,
    'l2w_mask_wave': 1600,  # SWIR masking for non-water
    'l2w_mask_threshold': 0.0215,
    'geometry_per_band': True,  # Better for inland water
}

ac.acolite.acolite_run(settings=settings)
```

**ACOLITE DSF (Dark Spectrum Fitting) method:**
- Estimates aerosol optical thickness from dark pixels in the scene
- Works for both clear and turbid/productive waters
- Handles sun glint as part of the correction (via `dsf_residual_glint_correction`)
- Outputs Rrs (sr^-1) at 10m resolution across all VIS/NIR bands

### Alternative: iCOR

```
# iCOR is available as SNAP plugin (free)
# Strengths: handles land AND water in same scene
# Includes SIMEC adjacency correction over water
# MODTRAN5-based atmospheric model
# Good for transitional water/land scenes

# In SNAP: Optical > Thematic Water Processing > iCOR
# Or via command line: gpt iCOR -Ssource=input.dim
```

### Comparison Matrix

| Processor | Water Suitability | Bathymetry Performance | Adjacency | Ease of Use |
|-----------|-------------------|----------------------|-----------|-------------|
| **ACOLITE DSF** | Excellent | Best (RMSE -20-40%) | No | Python script |
| **iCOR** | Good | Good | Yes (SIMEC) | SNAP plugin |
| **Sen2Cor** | Poor (land-focused) | Poor | No | Built-in L2A |
| **C2RCC** | Good (Case-2 waters) | Moderate | No | SNAP plugin |
| **Polymer** | Good | Good | No | Python/Fortran |

### Priority: CRITICAL
Single largest accuracy improvement. Switch from Sen2Cor/L2A to ACOLITE immediately.

---

## Step 3: Sun Glint Removal

### What & Why
Specular reflection of sunlight off the water surface adds spurious radiance to all bands, biasing depth estimates. Uncorrected glint can cause depth errors of up to 30%. The Hedley et al. (2005) method is the standard, with newer improvements for shallow water.

### Accuracy Impact
- **Uncorrected glint:** Up to 30% depth error in affected scenes
- **Hedley correction:** Reduces glint error to <5% in most cases
- **Noise De-correlation method (ND-SGC):** Better than Hedley for very shallow water

### Implementation

**Hedley et al. (2005) method:**

```python
import numpy as np

def hedley_glint_correction(image_bands, nir_band, deep_water_roi):
    """
    Correct sun glint using Hedley et al. (2005) method.

    For each visible band:
    1. Select deep water pixels (where NIR should be ~0)
    2. Regress visible band vs NIR band over deep water
    3. Subtract scaled NIR from visible band

    Args:
        image_bands: dict of {band_name: 2D array} for visible bands
        nir_band: 2D array of NIR (B8A or B8)
        deep_water_roi: boolean mask of optically deep water pixels
    """
    corrected = {}
    nir_deep = nir_band[deep_water_roi]
    min_nir = np.percentile(nir_deep, 5)  # Use 5th percentile as min

    for band_name, band_data in image_bands.items():
        vis_deep = band_data[deep_water_roi]

        # Linear regression: vis = slope * nir + intercept
        slope = np.cov(vis_deep, nir_deep)[0, 1] / np.var(nir_deep)

        # Correct: subtract excess NIR-correlated signal
        corrected[band_name] = band_data - slope * (nir_band - min_nir)

    return corrected
```

**Limitations of Hedley method:**
- Requires optically deep water pixels in the scene (NIR ~ 0)
- Fails in very shallow water where NIR is not fully absorbed
- For very shallow areas (<1m), use ND-SGC method instead

**ACOLITE integration:** If using ACOLITE with `dsf_residual_glint_correction=True`, basic glint correction is already applied. Still recommend Hedley as a second pass for residual glint.

### Tools
| Tool | Notes |
|------|-------|
| **ACOLITE** | Built-in residual glint correction |
| **Custom Python** | Hedley implementation (above) |
| **teongu/lyzenga1978** | GitHub repo with water column + glint correction |
| **CoastSat** | Includes glint handling for shoreline applications |

### Priority: CRITICAL
Especially important for scenes with solar zenith angle < 30 degrees or calm water surfaces.

---

## Step 4: Adjacency Effect Correction

### What & Why
Light scattered from bright adjacent land pixels contaminates the signal from darker water pixels. This is CRITICAL for small lakes where most water pixels are near shore. For Sentinel-2 at 10m resolution, the effect extends 100-300m from shoreline, meaning small lakes (<500m across) are almost entirely affected.

### Accuracy Impact
- **Within 200m of shoreline:** 16.7% RMSE reduction with adjacency correction
- **Symmetric signed percentage bias:** 32.4% reduction
- **Median symmetric accuracy:** 36.8% improvement
- **For lakes <1km across:** Nearly all pixels affected; correction is essential

### Implementation

**Option 1: iCOR with SIMEC (recommended for simplicity)**
```
# iCOR includes SIMEC (SIMilarity Environment Correction) for water
# Available as SNAP plugin - processes adjacency automatically
# Determines horizontal range of adjacency effect adaptively
```

**Option 2: Standalone Python correction**
```python
# Based on Warren et al. (2024) - sensor-generic adjacency correction
# Open-source Python tool
# 1. Calculate atmospheric Point Spread Function (PSF)
# 2. Convolve input imagery with PSF
# 3. Correct each pixel to the TOA reflectance it would have
#    if surrounded by identical pixels

# pip install ... (check github for sensor-generic adjacency tool)

# Three empirical methods for horizontal range:
# - Fixed range (simple, ~300m)
# - SIMEC (uses spectral similarity)
# - AWP-Inland Water (Adaptive Window by Proportion)
```

**Option 3: Buffer exclusion (simplest)**
```python
# Exclude pixels within N meters of shoreline
# Lose data but avoid contaminated measurements
# Recommended: buffer 1-2 pixels (10-20m) for Sentinel-2

from shapely.geometry import shape
from shapely.ops import unary_union

def create_water_buffer(lake_polygon, buffer_distance=-20):
    """Negative buffer = inward erosion from shoreline"""
    return lake_polygon.buffer(buffer_distance)
```

### Tools
| Tool | Method | Complexity |
|------|--------|------------|
| **iCOR (SNAP)** | SIMEC | Low (plugin) |
| **Warren et al. tool** | PSF-based | Medium (Python) |
| **Buffer exclusion** | Simple masking | Very low |
| **GAAC** | Genetic algorithm | High (research) |

### Priority: CRITICAL for small lakes (<1km), IMPORTANT for large lakes

---

## Step 5: Water Surface Detection & Edge Handling

### What & Why
Accurate water boundary delineation is essential for both spectral SDB and the A-E curve method. Mixed pixels at the water-land boundary contain both water and land reflectance, corrupting depth estimates near shore. Sub-pixel methods can resolve boundaries at 2m from 10m Sentinel-2 data.

### Accuracy Impact
- **Standard pixel-level detection:** +/-5m boundary uncertainty at 10m resolution
- **Sub-pixel methods (DE_MRF):** 2m resolution from 10m input
- **Impact on A-E curves:** Poor boundary = noisy area estimates = poor bathymetry

### Implementation

**Water index calculation:**
```python
import numpy as np

def calculate_water_indices(green, nir, swir):
    """Calculate multiple water indices for robust detection."""
    # NDWI (McFeeters, 1996)
    ndwi = (green - nir) / (green + nir + 1e-10)

    # MNDWI (Xu, 2006) - better for built-up areas
    mndwi = (green - swir) / (green + swir + 1e-10)

    # AWEI (Feyisa et al., 2014) - best for shadow distinction
    # awei_nsh = 4 * (green - swir1) - (0.25 * nir + 2.75 * swir2)

    return ndwi, mndwi
```

**Sub-pixel shoreline extraction:**
```python
def subpixel_shoreline(water_index, threshold=0.0):
    """
    Marching squares algorithm for sub-pixel water edge.
    Linearly interpolates water index values between
    neighboring pixels to find exact waterline position.
    """
    from skimage import measure
    contours = measure.find_contours(water_index, threshold)
    return contours  # Sub-pixel coordinate arrays
```

**Mixed pixel handling:**
```python
def handle_mixed_pixels(image, water_mask, buffer_pixels=1):
    """
    Flag and handle mixed pixels at water-land boundary.
    Options:
    1. Exclude (safest)
    2. Spectral unmixing (complex but preserves data)
    3. Smoothing (9x9 window) for bathymetry output
    """
    from scipy.ndimage import binary_dilation, binary_erosion

    # Identify edge pixels
    dilated = binary_dilation(water_mask, iterations=buffer_pixels)
    eroded = binary_erosion(water_mask, iterations=buffer_pixels)
    edge_pixels = dilated & ~eroded

    # Option 1: Exclude edge pixels
    clean_water = water_mask & ~edge_pixels

    return clean_water, edge_pixels
```

### Tools
| Tool | Purpose |
|------|---------|
| **JRC Global Surface Water** | Water occurrence (0-100%) per pixel, 1984-2021 |
| **HydroLAKES** | Pre-defined lake polygons (1.4M+ lakes) |
| **skimage.measure.find_contours** | Sub-pixel contour extraction |
| **CoastSat** | Sub-pixel shoreline mapping toolkit |
| **Shoreliner** | Sub-pixel waterline extraction pipeline |

### Priority: IMPORTANT
Directly affects A-E curve quality and near-shore bathymetry accuracy.

---

## Step 6: Temporal Compositing

### What & Why
Single satellite images are noisy due to atmospheric variability, cloud remnants, turbidity changes, and sun angle differences. Compositing multiple images reduces noise and addresses variable water clarity. This is one of the highest-impact steps for improving SDB accuracy.

### Accuracy Impact
- **Single image vs. multi-temporal composite:** 20-40% RMSE reduction
- **Median composite:** Best for removing outliers (clouds, turbidity spikes)
- **Maximum reflectance composite:** Reveals periods of minimum turbidity
- **Composite of 10-20 images:** Optimal balance of coverage and quality

### Implementation

**Strategy 1: Median composite (general purpose)**
```python
import numpy as np

def create_median_composite(image_stack, cloud_masks):
    """
    Pixel-wise median across cloud-free temporal stack.

    Args:
        image_stack: (n_images, n_bands, height, width) array
        cloud_masks: (n_images, height, width) boolean masks
    """
    # Mask cloudy pixels as NaN
    masked_stack = image_stack.copy()
    for i in range(len(cloud_masks)):
        masked_stack[i, :, cloud_masks[i]] = np.nan

    # Pixel-wise median (ignoring NaN)
    composite = np.nanmedian(masked_stack, axis=0)
    count = np.sum(~np.isnan(masked_stack[:, 0, :, :]), axis=0)

    return composite, count
```

**Strategy 2: Maximum reflectance composite (for turbid waters)**
```python
def create_max_composite(image_stack, cloud_masks, window_months=3):
    """
    Pixel-wise maximum reflectance = minimum turbidity.
    Use 2-3 month window around target date.
    Maximum values correspond to clearest water conditions.
    """
    masked_stack = image_stack.copy()
    for i in range(len(cloud_masks)):
        masked_stack[i, :, cloud_masks[i]] = np.nan

    composite = np.nanmax(masked_stack, axis=0)
    return composite
```

**Strategy 3: Best-pixel composite (advanced)**
```python
def best_pixel_composite(image_stack, cloud_masks, quality_scores):
    """
    Select best pixel from temporal stack based on quality metric.
    Quality = f(cloud_distance, sun_angle, turbidity_proxy, NDWI)
    """
    best_idx = np.argmax(quality_scores, axis=0)
    composite = np.take_along_axis(
        image_stack,
        best_idx[np.newaxis, np.newaxis, :, :],
        axis=0
    )[0]
    return composite
```

### Recommended Parameters
- **Temporal window:** 2-4 months centered on target period
- **Minimum images:** 5 (absolute minimum), 10-20 (optimal)
- **Season:** Prefer summer (less cloud, better sun angle, less ice)
- **Filter criteria:** Cloud cover <30%, sun zenith <60 degrees

### Priority: CRITICAL
Among the easiest high-impact improvements. Implement immediately.

---

## Step 7: Water Column Correction (Spectral SDB)

### What & Why
For spectral SDB methods (log-ratio, Lyzenga), the water column attenuates bottom reflectance differently per band. Water column correction separates depth signal from bottom type, producing depth-invariant indices. Without it, bright sandy bottoms appear shallower than dark muddy bottoms at the same depth.

### Accuracy Impact
- **Without correction:** Bottom type causes 1-3m depth bias
- **With BRI/DII correction:** Classification accuracy improves from ~40% to ~85%
- **Lyzenga method RMSE:** 0.49-0.96m for shallow water bathymetry

### Implementation

**Lyzenga (1978/2006) depth-invariant index:**
```python
import numpy as np

def lyzenga_depth_invariant(band_i, band_j, deep_water_roi):
    """
    Calculate depth-invariant index (DII) from two bands.

    DII = ln(Li) - (ki/kj) * ln(Lj)

    where ki/kj is the ratio of attenuation coefficients,
    estimated from the covariance of log-transformed bands
    over areas of variable depth but uniform bottom.
    """
    # Log-transform (after subtracting deep water)
    Lw_i = np.mean(band_i[deep_water_roi])
    Lw_j = np.mean(band_j[deep_water_roi])

    Xi = np.log(band_i - Lw_i + 1e-10)
    Xj = np.log(band_j - Lw_j + 1e-10)

    # Estimate attenuation ratio from covariance
    var_i = np.var(Xi[deep_water_roi])
    var_j = np.var(Xj[deep_water_roi])
    cov_ij = np.cov(Xi[deep_water_roi].ravel(),
                     Xj[deep_water_roi].ravel())[0, 1]

    a = (var_i - var_j)
    ki_kj = (a + np.sqrt(a**2 + 4 * cov_ij**2)) / (2 * cov_ij)

    # Depth invariant index
    dii = Xi - ki_kj * Xj

    return dii, ki_kj
```

**Bottom reflectance normalization (4SM method):**
```python
# 4SM = Self-Calibrated Algebraic Ratio Method
# Models brightest bottom substrate at null depth
# Uses bare land pixels as spectral model of shallow bottoms
# Separates depth from bottom albedo algebraically
# Reference: Morel & Maritorena (2001), Sagawa et al. (2010)
```

### Tools
| Tool | Method | Notes |
|------|--------|-------|
| **teongu/lyzenga1978** | Lyzenga DII | Python, GitHub |
| **4SM** | Algebraic ratio | Self-calibrating |
| **Custom implementation** | Band ratio | Simple, tune per lake |

### Priority: IMPORTANT for spectral SDB models, NOT NEEDED for A-E curve method

---

## Step 8: DEM Preprocessing

### What & Why
DEMs are used for (a) shore slope estimation to predict underwater slopes, (b) A-E curve construction, and (c) morphometric features. SRTM has voids over water, artifacts near shorelines, and tree height bias. Choosing the right DEM and preprocessing it correctly is essential.

### DEM Comparison

| DEM | Resolution | Vertical Accuracy | Lake Suitability | Notes |
|-----|-----------|-------------------|------------------|-------|
| **FABDEM** | 30m | Best overall (lowest error) | Excellent | ML-corrected SRTM, tree/building removal |
| **Copernicus DEM** | 30m | Second best | Very Good | TanDEM-X based, newer data |
| **MERIT DEM** | 90m | Good (58% <2m error) | Good | Multi-error removed SRTM |
| **SRTM v3** | 30m | Moderate (39% <2m) | Fair | Void-filled, legacy |
| **NASADEM** | 30m | Moderate | Fair | Reprocessed SRTM |

**Recommendation: FABDEM for shore slopes, Copernicus DEM for lake level reference**

### Implementation

**1. Void filling near water bodies:**
```python
import rasterio
import numpy as np
from scipy.interpolate import griddata

def fill_dem_voids(dem_path, water_mask):
    """
    Fill DEM voids (especially over water) using TIN interpolation
    from shoreline elevations.
    """
    with rasterio.open(dem_path) as src:
        dem = src.read(1)
        transform = src.transform

    # Identify void pixels (typically coded as -32768 or 0)
    voids = (dem <= -1000) | (dem == 0)
    water_voids = voids & water_mask

    # Get valid shoreline pixels (water edge with valid elevation)
    from scipy.ndimage import binary_dilation
    shore_zone = binary_dilation(water_mask, iterations=3) & ~water_mask
    valid_shore = shore_zone & ~voids

    # Interpolate from shoreline into lake
    coords_valid = np.argwhere(valid_shore)
    values_valid = dem[valid_shore]
    coords_fill = np.argwhere(water_voids)

    if len(coords_valid) > 0 and len(coords_fill) > 0:
        filled = griddata(coords_valid, values_valid,
                         coords_fill, method='linear')
        dem[water_voids] = filled

    return dem
```

**2. Artifact removal:**
```python
def remove_dem_artifacts(dem, max_slope_threshold=45):
    """
    Remove spikes and pits using maximum slope approach.
    Based on Yamazaki et al. (MERIT DEM methodology).

    SRTM artifacts near water: spikes from radar layover,
    pits from specular reflection (water = radar mirror).
    """
    from scipy.ndimage import generic_filter

    def max_slope_filter(window):
        center = window[4]  # 3x3 window center
        neighbors = np.delete(window, 4)
        slopes = np.abs(center - neighbors)
        return np.max(slopes)

    slope_map = generic_filter(dem, max_slope_filter, size=3)
    artifacts = slope_map > max_slope_threshold

    # Replace artifacts with local median
    from scipy.ndimage import median_filter
    dem_smooth = median_filter(dem, size=5)
    dem_clean = np.where(artifacts, dem_smooth, dem)

    return dem_clean
```

**3. Shore slope estimation:**
```python
def estimate_shore_slopes(dem, lake_polygon, buffer_m=200):
    """
    Extract slopes around lake shoreline from DEM.
    Shore slopes predict underwater slopes (same geophysical processes).

    Returns: slope per shoreline segment (degrees)
    """
    import richdem as rd

    # Calculate slope from DEM
    slope_map = rd.TerrainAttribute(rd.LoadGDAL(dem_path), attrib='slope_degrees')

    # Extract slopes within buffer of shoreline
    shore_buffer = lake_polygon.boundary.buffer(buffer_m)
    # ... mask and extract mean/median slope per segment

    return shore_slopes
```

### Tools
| Tool | Purpose |
|------|---------|
| **FABDEM** | Best bare-earth DEM (download from fathom.global) |
| **richdem** | Python terrain analysis (slopes, curvature) |
| **lakemorpho** | R package for lake morphometry metrics |
| **rasterio** | Python raster I/O |
| **GDAL** | DEM reprojection, void detection |

### Priority: CRITICAL for A-E curve method

---

## Step 9: Elevation Data Preprocessing (ICESat-2 / SWOT)

### What & Why
ICESat-2 photon-counting LiDAR provides sparse but accurate elevation points. However, raw photon data is extremely noisy, especially during daytime (solar background noise). SWOT provides wide-swath water surface elevation but requires denoising. Proper signal extraction is essential for calibrating bathymetry models.

### Accuracy Impact
- **Raw ATL03 photons:** Signal-to-noise ratio can be 1:10 in daytime
- **After density filtering:** 90%+ noise removal, sub-meter depth accuracy
- **SWOT after outlier removal:** Average bias 0.08m, precision 0.22m

### ICESat-2 Preprocessing

**1. Photon classification and noise filtering:**
```python
import h5py
import numpy as np

def filter_icesat2_photons(atl03_file, lake_polygon):
    """
    Multi-stage filtering of ICESat-2 ATL03 photons for lake bathymetry.
    """
    with h5py.File(atl03_file, 'r') as f:
        # For each beam (gt1l, gt1r, gt2l, gt2r, gt3l, gt3r)
        for beam in ['gt1l', 'gt1r', 'gt2l', 'gt2r', 'gt3l', 'gt3r']:
            lat = f[f'{beam}/heights/lat_ph'][:]
            lon = f[f'{beam}/heights/lon_ph'][:]
            h = f[f'{beam}/heights/h_ph'][:]
            conf = f[f'{beam}/heights/signal_conf_ph'][:]
            # Column indices: 0=land, 1=ocean, 2=sea_ice, 3=land_ice, 4=inland_water

            # Step 1: Spatial filter - only photons within lake
            in_lake = point_in_polygon(lat, lon, lake_polygon)

            # Step 2: Confidence filter
            # For inland water (column 4), keep medium+ confidence
            water_conf = conf[:, 4]  # inland water confidence
            high_conf = water_conf >= 3  # 3=medium, 4=high

            # Step 3: ATL08 classification filter
            # Use ATL08 labels: ground, canopy, noise
            # For water: look for "ground" classified photons
            # (ATL08 classifies water surface returns as ground)

            # Step 4: Density-based denoising (DBSCAN-like)
            valid = in_lake & high_conf
            photons = np.column_stack([lat[valid], lon[valid], h[valid]])
            clean_photons = density_filter(photons)

    return clean_photons

def density_filter(photons, search_radius=10, min_count=3):
    """
    Density-based photon filtering.
    Signal photons cluster together; noise is randomly distributed.

    Uses adaptive elliptical neighborhood:
    - Along-track: ~10m
    - Cross-track: narrow (single beam)
    - Vertical: ~1m
    """
    from sklearn.neighbors import BallTree

    tree = BallTree(photons[:, :2])  # spatial coordinates
    counts = tree.query_radius(photons[:, :2],
                                r=search_radius,
                                count_only=True)
    signal_mask = counts >= min_count
    return photons[signal_mask]
```

**2. Surface vs. bottom separation:**
```python
def separate_surface_bottom(photons_h, along_track):
    """
    Separate water surface returns from bottom returns.

    Water surface: cluster of photons at consistent elevation
    Bottom: photons below surface, depth = surface_h - bottom_h

    Uses: histogram analysis, local density peaks
    """
    from scipy.signal import find_peaks

    # Bin photons by elevation
    hist, bin_edges = np.histogram(photons_h, bins=100)

    # Find peaks (surface = highest density peak near expected level)
    peaks, properties = find_peaks(hist, height=5, distance=10)

    # Surface = highest elevation peak
    # Bottom = lower elevation peak(s)
    surface_elev = bin_edges[peaks[-1]]  # highest peak
    bottom_mask = photons_h < (surface_elev - 0.5)  # below surface

    depths = surface_elev - photons_h[bottom_mask]
    return depths, surface_elev
```

### SWOT Preprocessing

```python
def preprocess_swot_lake(swot_lakeSP_file):
    """
    Preprocess SWOT L2_HR_LakeSP product.

    Key steps:
    1. Quality flag filtering (ice_clim_f, dark_frac, partial_f)
    2. Outlier removal using IQR method
    3. Geoid correction (EGM2008 -> local datum)
    """
    import netCDF4 as nc

    ds = nc.Dataset(swot_lakeSP_file)
    wse = ds['wse'][:]           # Water surface elevation
    wse_u = ds['wse_u'][:]       # Uncertainty
    quality = ds['quality_f'][:] # Quality flag

    # Step 1: Keep only good quality (quality_f == 0)
    good = quality == 0

    # Step 2: IQR outlier removal
    q25, q75 = np.percentile(wse[good], [25, 75])
    iqr = q75 - q25
    valid = good & (wse > q25 - 1.5*iqr) & (wse < q75 + 1.5*iqr)

    # Step 3: Use uncertainty to weight observations
    weights = 1.0 / (wse_u[valid]**2)

    return wse[valid], weights
```

### Atmospheric Delay Correction (ICESat-2)

```
ICESat-2 ATL03 already includes atmospheric corrections:
- Tropospheric delay (dry + wet): corrected using MERRA-2 model
- Already applied in h_ph (photon heights)

For SWOT:
- Tropospheric corrections included in WSE product
- Additional correction needed for small lakes: wet troposphere
  model correction may be inaccurate over small water bodies
  (radiometer footprint = 20-40 km, much larger than lake)
- Recommendation: use ECMWF ERA5 model-based correction instead
```

### Tools
| Tool | Purpose |
|------|---------|
| **SlideRule** | Cloud-native ICESat-2 processing (slideruleearth.io) |
| **icepyx** | Python ICESat-2 data access |
| **ATL13** | Pre-processed inland water product (NSIDC) |
| **h5py** | HDF5 reading |
| **Hydroweb** | Pre-processed lake levels from altimetry |

### Priority: CRITICAL for calibration data

---

## Step 10: A-E Curve Construction & Smoothing

### What & Why
Area-Elevation (A-E) curves (hypsometric curves) are the backbone of our bathymetry method. They relate lake surface area at different water levels to elevation, allowing depth estimation. Noisy A-E curves produce noisy bathymetry. Smoothing and outlier detection are essential.

### Accuracy Impact
- **Unsmoothed A-E curve:** Noisy, physically implausible depth estimates
- **Smoothed + validated:** Consistent, monotonic depth profiles
- **Co-registration errors:** 1 pixel shift = 0.5-2m depth error in A-E method

### Implementation

**1. A-E curve construction:**
```python
def build_ae_curve(water_areas, water_levels):
    """
    Build Area-Elevation curve from paired satellite observations.

    Args:
        water_areas: list of (date, area_km2) from Landsat/S2
        water_levels: list of (date, elevation_m) from altimetry

    Returns:
        Sorted (elevation, area) pairs defining the hypsometric curve
    """
    # Match observations by date (within +/- 5 days)
    paired = match_temporal(water_areas, water_levels, max_days=5)

    # Sort by elevation
    paired.sort(key=lambda x: x[1])  # sort by elevation

    # Basic quality check: area should increase with elevation
    # (as water rises, lake gets bigger)
    return paired
```

**2. Smoothing and outlier detection:**
```python
def smooth_ae_curve(elevations, areas, method='isotonic'):
    """
    Smooth A-E curve while enforcing physical constraints:
    - Area must be monotonically non-decreasing with elevation
    - No negative areas
    - Smooth transitions (no sudden jumps)
    """
    if method == 'isotonic':
        from sklearn.isotonic import IsotonicRegression
        # Isotonic regression enforces monotonicity
        ir = IsotonicRegression(increasing=True)
        areas_smooth = ir.fit_transform(elevations, areas)

    elif method == 'lowess':
        from statsmodels.nonparametric.smoothers_lowess import lowess
        result = lowess(areas, elevations, frac=0.3)
        areas_smooth = result[:, 1]
        # Enforce monotonicity post-hoc
        areas_smooth = np.maximum.accumulate(areas_smooth)

    elif method == 'spline':
        from scipy.interpolate import UnivariateSpline
        spl = UnivariateSpline(elevations, areas, s=len(elevations)*0.1)
        areas_smooth = spl(elevations)
        areas_smooth = np.maximum.accumulate(np.maximum(areas_smooth, 0))

    return areas_smooth

def detect_ae_outliers(elevations, areas, threshold=3.0):
    """
    Detect outliers in A-E curve using residual analysis.
    Outliers often caused by:
    - Cloud contamination (underestimates area)
    - Ice/snow confusion (overestimates area)
    - Altimetry errors (wrong elevation)
    """
    from scipy.interpolate import UnivariateSpline

    # Fit smooth spline
    spl = UnivariateSpline(elevations, areas, s=len(elevations)*0.5)
    residuals = areas - spl(elevations)

    # Z-score outlier detection
    z_scores = (residuals - np.mean(residuals)) / np.std(residuals)
    outliers = np.abs(z_scores) > threshold

    return outliers
```

**3. Multi-temporal co-registration:**
```python
def coregister_water_extents(images, reference_image):
    """
    Ensure consistent spatial alignment across multi-temporal images.
    Misalignment of even 1 pixel (10m) causes area errors.

    Steps:
    1. Use phase correlation for sub-pixel shift detection
    2. Apply affine transformation
    3. Validate with stable land features
    """
    from skimage.registration import phase_cross_correlation

    for img in images:
        shift, error, diffphase = phase_cross_correlation(
            reference_image, img, upsample_factor=10
        )
        # Apply sub-pixel shift
        from scipy.ndimage import shift as ndshift
        corrected = ndshift(img, shift)
        yield corrected
```

### Key Constraints
- **Do NOT extrapolate** the A-E curve beyond observed elevation range
- Prefer isotonic regression for monotonicity enforcement
- Need minimum 10-15 paired observations for reliable curve
- Seasonal bias: summer observations preferred (less cloud, no ice)

### Priority: CRITICAL for A-E curve bathymetry method

---

## Step 11: Vertical Datum Harmonization

### What & Why
Different data sources use different vertical references: ICESat-2 uses WGS84 ellipsoid, SRTM uses EGM96 geoid, Copernicus DEM uses EGM2008, SWOT uses its own reference. Mixing these without harmonization causes systematic depth errors.

### Accuracy Impact
- **EGM96 vs EGM2008 difference:** Up to 1-2m in some regions
- **Ellipsoid vs geoid:** 10-100m offset (must be corrected)
- **Local geoid errors over lakes:** Average 18.3cm RMS (EGM2008)
- **Unharmonized mix:** Systematic bias of 0.5-2m in depth estimates

### Implementation

```python
import pyproj

def harmonize_vertical_datum(heights, source_datum, target_datum='EGM2008',
                              lats=None, lons=None):
    """
    Convert heights between vertical datums.

    Common conversions:
    - WGS84 ellipsoid -> EGM2008 geoid (for ICESat-2 -> DEM comparison)
    - EGM96 -> EGM2008 (for SRTM -> Copernicus comparison)
    - Local gauge datum -> EGM2008
    """
    if source_datum == 'WGS84' and target_datum == 'EGM2008':
        # Subtract geoid undulation to go from ellipsoid to geoid height
        # N = geoid height at location
        # h_orthometric = h_ellipsoidal - N

        # Using pyproj transformer
        transformer = pyproj.Transformer.from_crs(
            "EPSG:4979",  # WGS84 3D (ellipsoidal height)
            "EPSG:3855",  # EGM2008 geoid height
            always_xy=True
        )
        # Note: pyproj handles geoid undulation internally
        x, y, z = transformer.transform(lons, lats, heights)
        return z

    elif source_datum == 'EGM96' and target_datum == 'EGM2008':
        # Difference is typically small (< 1m) but matters for sub-meter work
        # Use geoid grids from NGA
        # EGM2008 - EGM96 difference grid available from:
        # https://earth-info.nga.mil/
        pass

    return heights

def validate_datum_consistency(dem_elevation, altimetry_elevation,
                                lake_boundary, tolerance_m=0.5):
    """
    Validate vertical datum consistency by comparing
    DEM shore elevation with altimetry water level.

    If difference > tolerance at known shore points,
    there is a datum mismatch.
    """
    # Extract DEM elevation at lake shore
    shore_dem_elev = extract_at_boundary(dem_elevation, lake_boundary)

    # Compare with altimetry-derived water level at same time
    offset = np.median(shore_dem_elev) - altimetry_elevation

    if abs(offset) > tolerance_m:
        print(f"WARNING: Datum offset detected: {offset:.2f}m")
        print(f"Apply correction of {-offset:.2f}m to {'DEM' if offset > 0 else 'altimetry'}")

    return offset
```

### Recommended Reference Datum
**Use EGM2008 geoid height as the common reference for all datasets.**

| Source | Native Datum | Conversion Needed |
|--------|-------------|-------------------|
| ICESat-2 ATL03 | WGS84 ellipsoid | Subtract EGM2008 geoid undulation |
| ICESat-2 ATL13 | EGM2008 | None (already in target datum) |
| SWOT LakeSP | EGM2008 | None |
| SRTM | EGM96 | Apply EGM96-to-EGM2008 correction |
| Copernicus DEM | EGM2008 | None |
| FABDEM | EGM96 | Apply EGM96-to-EGM2008 correction |
| USGS gauge data | NAVD88 | Convert via GEOID18 model |

### Priority: CRITICAL
Mixing datums silently corrupts all depth estimates.

---

## Step 12: Data Quality Filtering & Validation

### What & Why
Final quality control step to catch remaining issues before model training. Validates physical plausibility, cross-checks data sources, and flags suspect observations.

### Implementation

```python
def final_quality_filter(depths, areas, elevations, lake_id):
    """
    Final quality checks on assembled bathymetry dataset.
    """
    flags = np.zeros(len(depths), dtype=bool)

    # 1. Physical plausibility
    flags |= depths < 0  # No negative depths
    flags |= depths > 500  # Implausibly deep (>500m)

    # 2. Area-depth consistency
    # Deeper points should generally be farther from shore
    # (not always true but extreme violations are suspect)

    # 3. Temporal consistency
    # Same location should have consistent depth across time
    # (bathymetry doesn't change, only water level does)

    # 4. Cross-validation between sources
    # If ICESat-2 depth disagrees with A-E derived depth by >2m, flag

    # 5. Minimum observation count
    # Need at least 3 independent observations per depth bin

    print(f"Lake {lake_id}: {np.sum(flags)}/{len(depths)} points flagged "
          f"({100*np.sum(flags)/len(depths):.1f}%)")

    return ~flags  # Return valid mask

def cross_validate_sources(icesat2_depths, ae_depths, spectral_depths,
                           tolerance_m=2.0):
    """
    Cross-validate depths from different methods.
    Consistent results across methods = high confidence.
    """
    # Find co-located points
    # Compare pairwise
    agreement = {}
    if icesat2_depths is not None and ae_depths is not None:
        diff = np.abs(icesat2_depths - ae_depths)
        agreement['icesat2_vs_ae'] = np.median(diff)

    if icesat2_depths is not None and spectral_depths is not None:
        diff = np.abs(icesat2_depths - spectral_depths)
        agreement['icesat2_vs_spectral'] = np.median(diff)

    return agreement
```

### Priority: IMPORTANT
Catches systematic errors that propagate through training.

---

## Pre-Processed Datasets

Use these to skip some preprocessing steps:

### Ready-to-Use Datasets

| Dataset | Contents | Preprocessing Done | Access |
|---------|----------|-------------------|--------|
| **ESA CCI Lakes** | Water-leaving reflectance, water level, extent | Full atmospheric correction, quality filtering | climatedataguide.ucar.edu |
| **JRC Global Surface Water** | Water occurrence, seasonality, transitions (1984-2021) | Cloud masking, water classification | GEE: `JRC/GSW1_4/GlobalSurfaceWater` |
| **GLORIA** | 7,572 hyperspectral Rrs measurements, 450 water bodies | In-situ calibrated reflectance | nature.com/articles/s41597-023-01973-y |
| **ReaLSAT** | 681K lake/reservoir area time series (1984-2015) | Landsat-derived, validated | nature.com/articles/s41597-022-01449-5 |
| **Hydroweb/DAHITI** | Lake levels from satellite altimetry | Multi-mission harmonized, quality filtered | hydroweb.theia-land.fr |
| **HydroLAKES** | 1.4M+ lake polygons with morphometry | Validated boundaries | hydrosheds.org |
| **Sentinel-2 L2A** | Surface reflectance (Sen2Cor corrected) | Basic atmospheric correction (land-focused) | Copernicus Hub |
| **ICESat-2 ATL13 v7** | Inland water surface heights + shallow bathymetry | Photon classified, quality filtered | nsidc.org |
| **SWOT LakeSP** | Lake water surface elevation | Denoised, quality flagged | podaac.jpl.nasa.gov |
| **GloBAthy** | Global lake bathymetry estimates | Pre-computed from satellite data | figshare |

### Recommendation
- Use **JRC Global Surface Water** occurrence maps as water priors (Steps 1, 5)
- Use **HydroLAKES** for lake boundaries and morphometric features
- Use **ATL13** instead of raw ATL03 when possible (pre-filtered)
- Use **Hydroweb/DAHITI** for lake level time series (pre-harmonized)
- Still run **ACOLITE** on raw S2 L1C (better than L2A for water)

---

## Priority Summary

### CRITICAL (implement immediately, largest accuracy impact)

| # | Step | Expected RMSE Improvement | Effort |
|---|------|--------------------------|--------|
| 2 | **ACOLITE atmospheric correction** | 20-40% reduction | Medium (Python setup) |
| 6 | **Temporal compositing** | 20-40% reduction | Low (GEE/Python) |
| 1 | **Cloud/shadow masking (s2cloudless)** | Eliminates corrupt data | Low (GEE) |
| 3 | **Sun glint removal** | Up to 30% error reduction | Low (Python) |
| 11 | **Vertical datum harmonization** | 0.5-2m systematic bias fix | Low (pyproj) |
| 9 | **ICESat-2 photon filtering** | Clean calibration data | Medium (Python) |
| 10 | **A-E curve smoothing** | Physically consistent depths | Low (Python) |
| 8 | **DEM preprocessing (FABDEM)** | Better shore slopes | Low (data swap) |

### IMPORTANT (implement next, meaningful improvement)

| # | Step | Expected RMSE Improvement | Effort |
|---|------|--------------------------|--------|
| 4 | **Adjacency effect correction** | ~17% RMSE reduction near shore | Medium |
| 5 | **Water surface detection + edges** | Better A-E curves | Medium |
| 7 | **Water column correction** | 1-3m bias reduction (spectral SDB) | Medium |
| 12 | **Cross-validation filtering** | Removes systematic errors | Low |

### NICE-TO-HAVE (diminishing returns, implement last)

| # | Step | Notes |
|---|------|-------|
| - | Sub-pixel shoreline extraction | Marginal over standard methods |
| - | Bottom reflectance normalization | Only for spectral SDB on clear lakes |
| - | SWOT preprocessing | SWOT still maturing; ATL13 better for now |

---

## Implementation Roadmap

### Phase 1: Quick Wins (1-2 days)
1. Switch from Sen2Cor L2A to ACOLITE DSF for atmospheric correction
2. Implement temporal median compositing (10-20 images per lake)
3. Add s2cloudless cloud masking (replace/augment SCL)
4. Add Hedley sun glint correction post-ACOLITE
5. Switch from SRTM to FABDEM for DEM source

### Phase 2: Core Pipeline (3-5 days)
6. Implement vertical datum harmonization (all sources to EGM2008)
7. Build ICESat-2 ATL03 photon density filtering pipeline
8. Add isotonic regression for A-E curve smoothing
9. Implement mixed pixel exclusion at lake boundaries
10. Add JRC water occurrence maps as prior masks

### Phase 3: Refinement (5-7 days)
11. Implement adjacency effect correction (iCOR or standalone)
12. Add Lyzenga water column correction for spectral SDB
13. Build cross-validation between ICESat-2, A-E, and spectral depths
14. Implement multi-temporal co-registration for A-E curves
15. Add SWOT data integration with proper filtering

### Expected Cumulative Impact
- **After Phase 1:** ~30-40% RMSE reduction (from ~2m to ~1.2-1.4m)
- **After Phase 2:** ~45-55% RMSE reduction (down to ~0.9-1.1m)
- **After Phase 3:** ~55-65% RMSE reduction (target ~0.7-0.9m)

Combined with improved ML models, sub-0.5m should be achievable for lakes
with good data coverage (multiple ICESat-2 passes, clear water, >10 temporal
images).

---

## References

Key papers and tools referenced throughout this guide:

- Hedley et al. (2005) - Sun glint removal for shallow water mapping
- Lyzenga (1978, 2006) - Water column correction and depth estimation
- Vanhellemont & Ruddick - ACOLITE DSF atmospheric correction
- Warren et al. (2024) - Sensor-generic adjacency effect correction
- Yamazaki et al. (2017) - MERIT DEM multi-error removal
- Hawker et al. (2022) - FABDEM forest and building removed DEM
- Pekel et al. (2016) - JRC Global Surface Water dataset
- Weekley et al. (2021) - Lake level tracking with hypsometric relationships
- Martinsen et al. (2023) - Predicting lake bathymetry from surrounding topography
- Lu et al. (2025) - ICESat-2 Density-Dimension Algorithm for bathymetry

Tool repositories:
- ACOLITE: github.com/acolite/acolite
- iCOR: Available as SNAP plugin (vito.be)
- SlideRule: slideruleearth.io (cloud-native ICESat-2 processing)
- icepyx: github.com/icesat2py/icepyx
- CoastSat: github.com/kvos/CoastSat
- lyzenga1978: github.com/teongu/lyzenga1978
- lakemorpho: CRAN R package
