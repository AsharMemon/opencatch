# US / Canada Bathymetry Coverage Matrix

Updated: 2026-04-09

## How To Read This

- `Survey live`: real survey / contour-backed tiles are live in the app
- `Coarse live`: a live fallback exists, but it is not survey-grade
- `Raw acquired`: source files are on disk and ready for normalization / tiling
- `Source identified`: official data path is known, but we have not ingested it yet
- `No live fallback`: the app does not currently have a meaningful inland contour lane there

## Current Honest Summary

- Live survey-backed inland contour jurisdictions: `24`
  - US: `AK, AL, AR, CT, DE, FL, IL, IN, KS, MA, MI, MN, MT, ND, NE, NH, OH, TX, VT, WA`
  - Canada: `AB, BC, ON, QC`
- Live state/province-specific supporting inland lanes: `21`
  - `IA` derived depth-band polygons from lake summaries
  - `WI` derived depth-band polygons from max-depth lake summaries
  - `NB` bathymetry sounding points
  - `NL` HydroLAKES coarse lake summaries
  - `NS` surveyed-lake coverage footprints
  - `NT` HydroLAKES coarse lake points
  - `NU` HydroLAKES major-lakes coarse points
  - `HI` derived depth-band polygons from HydroLAKES lake summaries
  - `PE` HydroLAKES coarse lake points
  - `ME` survey-index coverage points
  - `MB` survey-index coverage points
  - `RI` official lake-management index lane
  - `SK` survey-index coverage points
  - `NY` official contour-page index lane
  - `NJ` official lake-plan PDF index lane
  - `OK` official statewide depth-point lane from OWRB
  - `PA` official statewide lake-footprint coverage lane from PFBC
  - `VA` official statewide waterbody-index lane from DWR
  - `MO` Clearwater Lake sounding points
  - `WV` official lake-map PDF index lane
  - `YT` HydroLAKES coarse lake points
- Raw-acquired expansion jurisdictions now on disk beyond those live lanes: `0`
- Live coarse national fallback for lakes: `LAGOS-US` across the contiguous US
- No live inland fallback today: `none` for the U.S. + Canada inland baseline
- Ocean/coastal official lanes are now live via `NOAA ENC` / `CHS NONNA`, but they are not yet normalized into a full papercut-style survey-grade presentation everywhere
- Rivers now have an official `USACE IENC` live lane for major navigable corridors, but not true survey-grade depth for every river reach

## United States

| State | Live Now | Expansion Status | Next Move |
| --- | --- | --- | --- |
| Alabama | Survey live | Live official ADCNR contour service | Done; maintain |
| Alaska | Survey live | Live | Done; maintain |
| Arizona | LAGOS-US coarse fallback | No source work started yet | Find official inland source or fallback lane |
| Arkansas | Survey live | Live | Done; maintain |
| California | LAGOS-US coarse fallback | Strong official DWR bathymetry services plus USBR reservoir subset identified | Treat inland and coastal separately; ingest DWR bathymetry services and USBR reservoir surveys |
| Colorado | LAGOS-US coarse fallback | Source identified | Acquire reservoir / CPW style sources |
| Connecticut | Survey live | Live | Done; maintain |
| Delaware | Survey live | Live DNREC public ponds bathymetry contours | Done; maintain |
| Florida | Survey live | Live | Tune styling only |
| Georgia | LAGOS-US coarse fallback | Source identified | Acquire DNR bathymetry source |
| Hawaii | State-specific live derived depth-band fallback lane | Live HydroLAKES polygons now rebuilt into nested pseudo depth bands for clearer chart-style rendering | Upgrade only if a stronger official inland source appears |
| Idaho | LAGOS-US coarse fallback | Source identified | Acquire IDFG / reservoir data |
| Illinois | Survey live | Live | Done; maintain |
| Indiana | Survey live | Live | Done; maintain |
| Iowa | State-specific live derived depth-band lane | Live Iowa DNR lake-summary polygons now rebuilt into nested pseudo depth bands for clearer chart-style rendering | Keep searching for official contour geometry, but the current lane is much richer than the old flat summaries |
| Kansas | Survey live | Live Kansas Biological Survey contour service | Done; maintain |
| Kentucky | LAGOS-US coarse fallback | Source identified | Acquire KyGovMaps / KDFWR data |
| Louisiana | LAGOS-US coarse fallback | Source identified | Find official inland / reservoir source |
| Maine | State-specific live survey index lane | Live survey-index points | Promote the IF&W region index from index coverage into downloadable lake-specific depth assets |
| Maryland | LAGOS-US coarse fallback | Official Maryland Geological Survey reservoir bathymetry ZIP/PDF data identified | Promote MGS reservoir bathymetry files into a survey-backed Maryland reservoir lane |
| Massachusetts | Survey live | Live | Done; maintain |
| Michigan | Survey live | Live | Done; maintain |
| Minnesota | Survey live | Live | Done; maintain |
| Mississippi | LAGOS-US coarse fallback | Official MDWFP lake-depth PDF collection identified statewide | High-priority PDF extraction target; likely one of the biggest remaining wins |
| Missouri | State-specific live sounding lane | Live Clearwater Lake depth-point tile | Expand beyond Clearwater into a broader Missouri lake lane |
| Montana | Survey live | Live | Done; maintain |
| Nebraska | Survey live | Live | Local repo now matches live tile; maintain |
| Nevada | LAGOS-US coarse fallback | Source identified | Acquire official inland source |
| New Hampshire | Survey live | Live | Done; maintain |
| New Jersey | State-specific live official survey index lane | Live DEP lake-plan PDF index for Hopatcong, Musconetcong, and Union Lake with approximate public geocodes | Expand beyond the current 3 official lake-plan PDFs or find a less protected statewide survey path |
| New Mexico | LAGOS-US coarse fallback | Source identified | Acquire reservoir / state source |
| New York | State-specific live official contour-page index lane | Live DEC contour-page index built from `421` official landing pages with `387` public geocodes | Maintain the new statewide index lane and keep searching for a more automation-friendly direct PDF path |
| North Carolina | LAGOS-US coarse fallback | Source identified | Acquire NCWRC sources |
| North Dakota | Survey live | Live | Maintain and monitor the flaky ND GIS Hub source for refreshes |
| Ohio | Survey live | Live | Done; maintain |
| Oklahoma | State-specific live official depth-point lane | Live OWRB statewide lake inventory tile with coordinates and max depth values | Upgrade from max-depth points into richer contour geometry where official assets exist |
| Oregon | LAGOS-US coarse fallback | Source identified | Acquire Atlas of Oregon Lakes / state source |
| Pennsylvania | State-specific live official lake-footprint lane | Live PFBC statewide lake polygons | Pair the PFBC lake footprints with contour/depth assets or derived ML contours instead of relying only on LAGOS |
| Rhode Island | State-specific live official lake-management index lane | Live DEM lake-management/project index for `8` waterbodies, built from official DEM URLs plus public geocodes | Expand beyond the current `8` official waterbodies and keep searching for a direct automation-friendly DEM PDF path |
| South Carolina | LAGOS-US coarse fallback | Source identified | Acquire SCDNR source |
| South Dakota | LAGOS-US coarse fallback | Official GFP statewide fisheries-survey PDFs include contour maps for many lakes | Promote South Dakota from coarse fallback into an official PDF-derived lake lane |
| Tennessee | LAGOS-US coarse fallback | Source identified | Acquire TWRA source |
| Texas | Survey live (expanded pilot) | Live TWDB contour tile now covers Bardwell, Bridgeport, Cherokee, and Conroe, with a repeatable statewide shapefile inventory/fetch pipeline | Keep scaling the TWDB fetch/extract pipeline from the current multi-lake pilot into a broader statewide survey lane |
| Utah | LAGOS-US coarse fallback | Source identified | Acquire DWR source |
| Vermont | Survey live | Live | Done; maintain |
| Virginia | State-specific live official survey index lane | Live DWR statewide waterbody index with page coordinates plus map/report PDF links | Keep enriching the index and promote any directly downloadable contour/PDF subsets into richer geometry |
| Washington | Survey live | Live | Done; maintain |
| West Virginia | State-specific live official survey index lane | Live WVDNR lake-map PDF index (`36` geocoded lakes from the official map-links directory) | Keep improving geocode match coverage and promote any richer downloadable contour subsets |
| Wisconsin | State-specific live derived depth-band lake lane | Live hypsography polygons joined to official lake geometry and rebuilt into nested pseudo depth bands | Keep searching for richer contour geometry to upgrade beyond max-depth summaries |
| Wyoming | LAGOS-US coarse fallback | Source identified | Acquire WGFD / official source |

## Canada

| Province / Territory | Live Now | Expansion Status | Next Move |
| --- | --- | --- | --- |
| Alberta | Survey live | Live | Done; maintain |
| British Columbia | Survey live | Live | Done; maintain |
| Manitoba | State-specific live survey-index lane | Live survey-index points plus official report-based bathymetry leads | Harvest report/PDF bathymetry for named lakes where official maps exist |
| New Brunswick | State-specific live sounding lane | Official lake-depth interactive map plus downloadable PDFs identified | Promote the current lane from points into PDF-derived contour geometry |
| Newfoundland & Labrador | State-specific live HydroLAKES fallback lane | Live coarse fallback | Upgrade from HydroLAKES if a stronger official source appears |
| Northwest Territories | State-specific live HydroLAKES fallback lane | Official inland-water / hydrographic-chart services identified, but not clean bathymetry contours yet | Inspect GNWT inland-water service layers for point/chart subsets before accepting fallback-only status |
| Nova Scotia | State-specific live survey-coverage lane | Official Lake Inventory program with `1000+` bathymetric maps identified | High-priority promotion from footprints into PDF-derived bathymetric contour geometry |
| Nunavut | State-specific live HydroLAKES fallback lane | Live coarse fallback | Upgrade beyond the current major-lakes fallback when a better source exists |
| Ontario | Survey live | Live | Done; maintain |
| Prince Edward Island | State-specific live HydroLAKES fallback lane | Live coarse fallback | Upgrade only if a stronger local source appears |
| Quebec | Survey live | Live | Done; maintain |
| Saskatchewan | State-specific live survey-index lane | Official HTML5 bathymetric map viewer plus survey index is confirmed | Promote the viewer/index lane into direct PDF or geometry harvesting |
| Yukon | State-specific live HydroLAKES fallback lane | Official Yukon bathymetry record exists, but the data were withdrawn for poor accuracy | Keep HydroLAKES fallback unless a newer Yukon bathymetry program appears |

## Priority Expansion Queue

### Batch 1 — Biggest Chart-Quality Upgrades

1. Mississippi / South Dakota — official statewide lake-depth PDFs with real contour content
2. New Brunswick / Nova Scotia — promote point/index/footprint lanes into richer contour geometry
3. Manitoba / Saskatchewan — convert survey-index / viewer lanes into direct contour geometry
4. Maryland / California — promote official reservoir / DWR bathymetry data into live survey-backed lanes
5. Texas — scale the TWDB multi-lake pilot into a broader statewide survey lane

### Batch 2 — Canadian Coverage Gaps With Existing Leads

1. New Brunswick / Nova Scotia — upgrade supporting lanes into richer contour geometry
2. Manitoba / Saskatchewan — turn survey index / viewer lanes into richer contour geometry
3. Northwest Territories — inspect official inland-water / hydrographic-chart service layers for promotable subsets
4. Newfoundland & Labrador / Prince Edward Island / Nunavut — keep hunting for official geometry while fallback remains live
5. British Columbia — revisit scanned / PDF bathymetry extraction quality

### Batch 3 — New Raw-Acquired U.S. Lanes To Promote

1. California — focus on DWR bathymetry services + USBR reservoir surveys instead of polygon-only layers
2. Maryland — promote MGS reservoir bathymetry ZIP/PDF data
3. Rhode Island — expand the official lake-management index beyond the current 8 waterbodies
4. Texas — expand the live TWDB multi-lake pilot deeper into the statewide lane

### Batch 3 — Reservoir / River / Coastal Expansion

1. NOAA ENC Direct to GIS
2. USACE IENC
3. CHS NONNA
4. State / provincial reservoir surveys

## Practical Meaning Right Now

- The U.S. + Canada inland baseline now has `some live lane everywhere`.
- Only a smaller subset of states / provinces / territories currently have the richer survey-style contour look, but Iowa, Wisconsin, and Hawaii now render with derived depth-band layers instead of flat summary fills.
- Texas now has a real TWDB multi-lake survey lane instead of a one-lake pilot.
- The biggest remaining gap is no longer `coverage`; it is `quality`.
- The next fastest way to improve the product is still: `upgrade coarse fallback lanes into stronger survey-backed or contour-rich lanes`, not `train a different model`.
