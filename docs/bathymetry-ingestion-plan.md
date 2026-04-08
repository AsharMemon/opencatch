# Bathymetry Ingestion Plan

Updated: 2026-04-07

## Goal

Expand real survey-backed contour coverage first, then use ML only as the fallback
layer for lakes/rivers/coasts with no authoritative depth source.

## Production Priority Order

1. Survey/contour-backed inland lakes
2. Official hydrographic coastal + inland navigation sources
3. Lake-scale priors and catalogs
4. OpenCatch ML fallback

## Immediate Open Data Sources To Ingest

### U.S. / Great Lakes / Coastal

- NOAA ENC Direct to GIS
- NOAA ENC display services
- USACE IENC (inland rivers + channels)

### Canada / Marine

- CHS NONNA (non-navigational open bathymetry)

### State / Provincial Inland Surveys

Tile-ready with lightweight normalization now:

- Alberta contour GeoJSON
- Florida contour GeoJSON
- Michigan contour GeoJSON
- Vermont contour GeoJSON

Catalog/summary only for now:

- Iowa summary polygons
- Saskatchewan survey PDF index
- Manitoba waterbody/survey index

Needs geospatial environment or format-specific parsing next:

- Ontario FGDB
- Quebec FGDB
- Washington FGDB
- Massachusetts shapefile + raster bundle
- Montana shapefile
- Wisconsin hypsography package

Confirmed readable via GDAL/OpenFileGDB or shapefile tooling:

- Ontario `BATHYMETRY_LINE` FGDB layer
- Quebec `isobathes_l` FGDB layer
- Washington `LakeBathymetryLine` FGDB layer
- Massachusetts `DFWBATHY_ARC` shapefile layer

## Strategy

### 1. Direct Rendering Lane

Use the raw survey geometry wherever possible, normalize it to the OpenCatch
schema, tile it, and serve it directly in the app.

### 2. Catalog / Index Lane

If a source is only a survey index or a summary polygon, keep it as a catalog
for routing/provenance and future parsing, but do not market it as contour-grade
bathymetry yet.

### 3. ML Fallback Lane

Only use the bathymetry model where no survey/contour source exists.

## Concrete Next Batch

1. Finish the current normalized/tiled survey batch: Alberta, Michigan, Vermont, Florida
2. Add the generated tiles to Martin and the mobile source registry
3. Convert Ontario/Quebec/Washington/Massachusetts raw vector layers to normalized GeoJSON
4. Add NOAA ENC / USACE IENC ingestion for coastal + river navigation coverage
5. Continue expanding survey-backed provinces/states before pushing more ML-only coverage

## Important Licensing Rule

Do not scrape or restyle commercial chart products just because they appear in
OpenCPN source lists. Use official public-domain/open-licensed raw hydrographic
data first, and license Canadian commercial chart products where needed.
