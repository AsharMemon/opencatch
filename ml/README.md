# OpenCatch ML Pipeline

Deep learning and data pipelines for OpenCatch's mapping and prediction features.

## Directory Structure

```
ml/
├── bathymetry/          # Satellite-derived bathymetry prediction
│   └── predict_depth.py # U-Net model for depth from Sentinel-2 imagery
├── waterbodies/         # Water body detection and classification
│   └── (TBD)            # DeepWaterMap-style segmentation
├── data_pipeline/       # Data acquisition scripts
│   └── fetch_nhdplus.py # NHDPlus + NHN water body download
└── README.md
```

## Quick Start (Vast.ai)

```bash
# 1. Install dependencies
pip install torch torchvision rasterio geopandas shapely pystac-client \
  planetary-computer odc-stac scikit-learn tqdm requests

# 2. Fetch water body data for US
python data_pipeline/fetch_nhdplus.py --country us --output /data/waterbodies

# 3. Train bathymetry model (needs S2 + survey data pairs)
python bathymetry/predict_depth.py --data-dir /data/training --epochs 50

# 4. Generate contours for unsurveyed lakes
python bathymetry/predict_depth.py --predict --input /data/s2_tiles --output /data/contours
```

## Data Sources

### Training Data (Known Bathymetry)
- **Ontario**: 11,000+ lake bathymetry maps (data.ontario.ca)
- **BC**: 2,600+ lake depth maps (FIDQ)
- **Alberta**: 169 lake shapefiles (AGS)
- **US States**: Many have survey data (state DNR websites)
- **NOAA**: Great Lakes + coastal bathymetry

### Satellite Imagery
- **Sentinel-2**: 10m resolution, free via Copernicus/Planetary Computer
- **Landsat 8/9**: 30m resolution, free via USGS EarthExplorer

### Water Body Outlines
- **US**: NHDPlus (USGS) — every stream, lake, pond
- **Canada**: NHN (NRCan) — ~2 million lakes

## Architecture

### Bathymetry Prediction
1. **Physics baseline**: Stumpf log-ratio (blue/green bands)
2. **ML model**: U-Net trained on lakes with survey data
3. **Transfer learning**: Fine-tune on regional subsets
4. **Output**: Depth raster → contour lines → vector tiles

### Water Body Detection
1. **Source**: JRC Global Surface Water + NHDPlus/NHN
2. **Enhancement**: Sentinel-2 segmentation for unmapped features
3. **Name matching**: GNIS + OSM + web scraping
4. **Access points**: OSM Overpass API + satellite parking lot detection
