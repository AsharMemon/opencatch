# Marine / River Acquisition Plan

Updated: 2026-04-09

## Official Source Priority

1. `NOAA ENC Direct to GIS`
2. `USACE IENC`
3. `CHS NONNA`
4. `NHDPlus HR + NHN` for river-network completeness where surveyed chart depth is absent

## Current Product State

- `River Network` overlay in the app is now live as the official river-completeness backbone:
  - U.S.: official `USGS HydroCached` hydrography raster
  - Canada: official NRCan `hydro_network_en` WMS
- `USACE IENC` remains a separate overlay on top for surveyed / maintained navigable corridors.
- `river_depth.py` remains a separate depth-estimation fallback where hydrography exists but chart bathymetry does not.
- A packaged continent-wide PMTiles replacement is now scaffolded in:
  - `/Users/Ashar/Documents/fish/ml/bathymetry/build_na_river_network.py`

## Scripts

### NOAA coastal / Great Lakes official sources

```bash
python3 ml/bathymetry/fetch_noaa_enc.py \
  --output /Users/Ashar/Documents/fish/data/bathymetry/noaa_enc \
  --download-themes
```

This acquires the official NOAA theme layers that matter most for navigation:

- Coastal Maintained Channels
- Shipping Lanes and Regulations
- U.S. Maritime Limits & Boundaries

It also writes scale-band service metadata for:

- overview
- general
- coastal
- approach
- harbor
- berthing

### USACE inland navigation corridors

```bash
python3 ml/bathymetry/fetch_usace_ienc.py \
  --output /Users/Ashar/Documents/fish/data/bathymetry/usace_ienc
```

Optional first-batch downloads:

```bash
python3 ml/bathymetry/fetch_usace_ienc.py \
  --output /Users/Ashar/Documents/fish/data/bathymetry/usace_ienc \
  --download-first 3
```

### Canada marine / coastal

```bash
python3 ml/bathymetry/fetch_chs_nonna.py \
  --output /Users/Ashar/Documents/fish/data/bathymetry/chs_nonna \
  --download-nonna10
```

This writes:

- WMS capabilities
- WMTS capabilities
- WCS capabilities
- NONNA10 ZIP download
- a parsed manifest of layers / coverages

### U.S. all-river official hydrography backbone

```bash
python3 ml/bathymetry/fetch_usgs_hydrography.py \
  --output /Users/Ashar/Documents/fish/data/bathymetry/usgs_hydrography
```

This validates and records:

- `NHDPlus_HR` MapServer
- `3DHP_all` FeatureServer
- flowline layer metadata
- sample query URLs for coverage/integration

### Canada all-river official hydrography backbone

```bash
python3 ml/bathymetry/fetch_nrcan_hydrography.py \
  --output /Users/Ashar/Documents/fish/data/bathymetry/nrcan_hydrography
```

This validates and records:

- `NHN` official GeoBase distribution path
- NRCan hydrography/web-service references
- head-checks for the official Canadian hydrography backbone
- `11` NHN regional GDB directories plus sample package names

### Packaged North America river overlay

```bash
/Users/Ashar/Documents/fish/.venv/bin/python \
  /Users/Ashar/Documents/fish/ml/bathymetry/build_na_river_network.py \
  --output-root /Users/Ashar/Documents/fish/data/bathymetry/river_network \
  --pmtiles-output /Users/Ashar/Documents/fish/infra/martin/tiles/na_river_network.pmtiles
```

This script is designed to:

- read local U.S. flowline parquet exported from the official NHDPlus HR pipeline
- ingest Canadian NHN package zips / FileGDBs one package at a time
- normalize both into a common `river_network` schema
- build a future `na_river_network.pmtiles` layer for Martin / MapLibre

Because the local machine is disk-constrained, the intended large run should happen
after additional space is freed or on remote infrastructure.

## Product Integration Intent

- `NOAA ENC + CUDEM + GEBCO` should cover U.S. coasts and Great Lakes
- `USACE IENC` should cover major U.S. navigable rivers and inland channels
- `NHDPlus_HR + 3DHP_all` should provide U.S. river-network completeness where chart bathymetry is absent
- `CHS NONNA` should cover Canadian marine waters and major navigable corridors where available
- `NHN` should provide Canadian river-network completeness where chart bathymetry is absent
- `river_depth.py` remains the depth-estimation fallback where we have hydrography but no surveyed chart bathymetry

## Honest Caveat

This is the acquisition and indexing layer, not the final styling/rendering layer.
The next operational step after download is always:

1. normalize
2. tile if needed
3. register in Martin
4. wire to the app overlay / router
