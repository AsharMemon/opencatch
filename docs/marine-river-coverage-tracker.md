# Marine / River Coverage Tracker

Updated: 2026-04-09

This tracker answers one narrower question than the inland lake matrix:

`Do we have an official or credible fallback lane for all oceans, coasts, Great Lakes, and rivers in the U.S. + Canada?`

## Current Honest Summary

- `Ocean / coastal`: source-complete enough to claim full baseline coverage
  - U.S. marine / Great Lakes: `NOAA ENC + ocean_contours`
  - Canadian marine: `CHS NONNA + ocean_contours`
- `Rivers`: not yet survey-grade everywhere, but the path to full baseline coverage is now clear
  - U.S. navigable corridors: `USACE IENC` live
  - U.S. all-river completeness: `USGS HydroCached` live in-app, `USGS NHDPlus_HR / 3DHP` validated packaging path
  - Canada navigable corridors: `CHS` where covered
  - Canada all-river completeness: `NHN WMS` live in-app, `NHN` package ingest validated for packaging

Validated manifests now on disk:

- [/Users/Ashar/Documents/fish/data/bathymetry/usgs_hydrography/manifest.json](/Users/Ashar/Documents/fish/data/bathymetry/usgs_hydrography/manifest.json)
- [/Users/Ashar/Documents/fish/data/bathymetry/nrcan_hydrography/manifest.json](/Users/Ashar/Documents/fish/data/bathymetry/nrcan_hydrography/manifest.json)
- [/Users/Ashar/Documents/fish/data/bathymetry/nrcan_hydrography/directory_manifest.json](/Users/Ashar/Documents/fish/data/bathymetry/nrcan_hydrography/directory_manifest.json)

Important Canadian ingest detail:

- `NHN` is not just a paper source. The FTP layout exposes `11` regional GDB directories with downloadable package zips, so Canadian all-river completeness is now an executable batch-ingest problem rather than a research placeholder.

## Status Legend

- `Official live`: source already live in product/runtime
- `Official validated`: official source path verified and documented, but not yet fully tiled/wired
- `Fallback live`: non-survey fallback already available

## Coverage Board

| Domain | Region | Status | Current source stack | What remains |
| --- | --- | --- | --- | --- |
| Ocean / coastal | U.S. + Great Lakes | Official live | `NOAA ENC`, `ocean_contours`, NOAA tides/currents | Normalize more official NOAA chart objects into the papercut presentation |
| Ocean / coastal | Canada | Official live | `CHS NONNA`, `ocean_contours`, CHS tides/water levels | Keep attribution/disclaimer path explicit and improve styling parity |
| River navigation corridors | U.S. | Official live | `USACE IENC`, `NOAA ENC` for tidal corridors | Expand from selected pilot charts to broader official chart coverage |
| River navigation corridors | Canada | Official validated | `CHS` navigable-corridor bathymetry where available | Broaden corridor-specific use where CHS covers inland navigation |
| River network completeness | U.S. | Official live | `USGS HydroCached`, `USGS NHDPlus_HR`, `USGS 3DHP_all`, `river_depth.py` fallback | Promote from service-backed rendering into packaged vector PMTiles |
| River network completeness | Canada | Official live | `NHN WMS`, `NHN` regional package bundles, fallback modeling where needed | Promote from service-backed rendering into packaged vector PMTiles |

## Operational Interpretation

- We can now honestly say:
  - all oceans and coasts have an official baseline lane
  - major U.S. navigable rivers have an official chart lane
  - all rivers have a validated official hydrography backbone path, but not all are tiled/rendered yet
- The remaining gap is not “no source exists.”
- The remaining gap is:
  - `normalize / tile / wire` the official hydrography backbones into a packaged North America river PMTiles layer

## Immediate Build Queue

1. Keep the live `North America river network` app overlay on official services:
   - `USGS HydroCached` in the U.S.
   - `NHN WMS` in Canada
2. Build and publish a packaged `na_river_network.pmtiles` layer from:
   - local `NHDPlus HR` flowline parquet exports
   - `NHN` package zips / FileGDBs
3. Keep `USACE IENC` as the survey/navigation overlay for U.S. major rivers.
4. Keep `river_depth.py` as the depth fallback where hydrography exists but chart bathymetry does not.
