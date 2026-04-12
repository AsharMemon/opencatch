# Survey-Grade Gap Source Map

As of April 11, 2026, `24 / 63` U.S. states + Canadian provinces/territories are already `survey live`.

That leaves `39` jurisdictions that still need promotion into richer survey-grade inland geometry. This file is the practical source map for those remaining jurisdictions: where the best current official lead is, and what is blocking promotion.

## Summary

- `10` PDF-promotion jurisdictions
- `11` reservoir-subset jurisdictions
- `14` supporting-lane upgrades
- `3` project/index-supported upgrades
- `1` withdrawn/blocked fallback jurisdiction

## PDF Promotion

These already have, or are expected to have, official map/PDF assets with lake-scale contour content.

| Jurisdiction | Best Current Source | Where To Find It | Main Limitation |
| --- | --- | --- | --- |
| Georgia | Georgia DNR Public Fishing Areas subset | Georgia Outdoor Map / PFA guides / official PFA KMZ | Partial subset only; name alias cleanup still needed for some lakes |
| Kentucky | KDFWR lake maps / FINs / fisheries bulletins | Kentucky Fish & Wildlife lake-map / FINs bulletin ecosystem | No clean automated statewide inventory yet |
| Louisiana | LDWF inland waterbody plans | LDWF named lake-management / aquatic vegetation plan pages | Direct scripted + Playwright fetches still hit `403`; needs manual/browser-assisted retrieval |
| Maryland | Maryland Geological Survey reservoir bathymetry | MGS reservoir bathymetry pages and lake-by-lake map/data assets | Inventory still needs to be harvested lake-by-lake |
| Mississippi | MDWFP Lake Depth Maps | MDWFP official lake-depth PDF maps | Good source, but still needs full digitize/package/publish |
| South Carolina | SCDNR brochures / lake maps | SCDNR lake brochure/map pages | No built statewide brochure/PDF ingestor yet |
| South Dakota | GFP fisheries lake maps and survey reports | South Dakota GFP lake maps / survey PDFs | Huge inventory; needed state-native polygons to georeference cleanly |
| Manitoba | named-lake PDF/report bathymetry | Manitoba official lake reports / bathymetry publications | Inventory still needs harvesting |
| New Brunswick | official lake-depth PDFs + lake map service | New Brunswick interactive bathymetry map + inventoried PDFs | Packaging was blocked by wrapper bug; now rerunning/packaging |
| Nova Scotia | Lake Inventory PDFs | Nova Scotia Lake Inventory program | High-volume PDF program; grayscale contour parsing was required |

## Reservoir Subset

These are not “statewide every-lake” sources. They are official reservoir/survey programs that can still produce survey-grade geometry for important lakes.

| Jurisdiction | Best Current Source | Where To Find It | Main Limitation |
| --- | --- | --- | --- |
| Arizona | USBR reservoir surveys | USBR reservoir sedimentation/bathymetry reports | Reservoir-only coverage, not full statewide lake coverage |
| California | California DWR + USBR reservoirs | California DWR bathymetry services + USBR | Split source strategy needed; inland reservoirs only from USBR/DWR, not all lakes |
| Colorado | USBR + CPW summaries | USBR reservoir surveys; CPW fishery survey summaries | CPW side is mostly supporting context, not contour geometry |
| Idaho | USBR reservoirs | USBR reservoir survey program | Reservoir-only coverage |
| Nevada | USBR reservoirs | USBR reservoir survey program | Reservoir-only coverage; NDOW pages are mostly supporting context |
| New Mexico | USBR reservoirs | USBR reservoir survey program | Reservoir-only coverage |
| North Carolina | reservoir study/report lane | NCDEQ / project reports like Jordan Lake | Reports/calibration assets, not direct contour geometry everywhere |
| Oregon | USBR reservoirs | USBR reservoir survey program | Reservoir-only coverage |
| Tennessee | TVA / USACE reservoir charts | TVA lake / reservoir charts, USACE reservoir path | Reservoir-by-reservoir harvesting still needed |
| Utah | USBR reservoirs | USBR reservoir survey program | Reservoir-only coverage |
| Wyoming | USBR reservoirs | USBR reservoir survey program | Reservoir-only coverage |

## Supporting-Lane Upgrades

These already have some live lane in OpenCatch, but it is not yet survey-grade contour geometry.

| Jurisdiction | Best Current Source | Where To Find It | Main Limitation |
| --- | --- | --- | --- |
| Hawaii | existing derived inland depth bands | current HydroLAKES-derived lane | No stronger statewide official inland source surfaced yet |
| Iowa | Iowa DNR depth-band / summary lane | Iowa DNR live lane already in repo | Statewide official contours still not surfaced cleanly |
| Maine | IF&W survey index | Maine IF&W regional lake-survey map/index assets | Indexed assets exist, but not yet promoted into downloadable contour geometry |
| Missouri | sounding/depth-point pilot | current Clearwater sounding lane | Only a pilot lake so far |
| New Jersey | DEP lake-plan PDF index | NJ DEP lake planning/report PDFs | Index exists, but downloadable contour geometry still sparse |
| New York | DEC contour-page index | NY DEC contour/map pages + USGS reservoir assets | Automatable direct path still weak |
| Oklahoma | OWRB max-depth point lane | OWRB point/source lane | Points and summaries, not rich contour geometry |
| Pennsylvania | PFBC lake-footprint lane | PFBC lake polygons and lake assets | Needs actual contour/depth assets paired to footprints |
| Rhode Island | DEM project/index lane | RI DEM lake-management/project material | Project/index only so far |
| Virginia | DWR waterbody index lane | Virginia DWR waterbody pages + map/report links | Needs map/PDF subset promotion |
| West Virginia | WVDNR lake-map PDFs | WVDNR lake map PDF index | Needs richer downloadable subsets promoted |
| Wisconsin | hypsography-derived bands | UMN/DRUM area-at-depth package + existing derived lane | Good surrogate, but not true official contour geometry |
| Northwest Territories | HydroLAKES fallback + GNWT leads | GNWT inland-water service leads | Official geometry path still needs inspection/harvest |
| Saskatchewan | official viewer/index | Saskatchewan bathymetric viewer + index | Viewer/index exists, but geometry extraction still needs implementation |

## Project / Index-Supported Upgrades

These have real official project/report lanes, but not yet a direct province/territory-wide inland bathymetry dataset.

| Jurisdiction | Best Current Source | Where To Find It | Main Limitation |
| --- | --- | --- | --- |
| Newfoundland & Labrador | water-resources atlas/report/project lane | NL water-resources reports / atlas / indexed project links | Structured leads exist, but not direct bathymetry geometry |
| Nunavut | bathymetry project/procurement trail | Nunavut coastal-resource inventory / project trail | Project evidence exists, but not a territory-wide inland dataset |
| Prince Edward Island | waterbody/project lane | PEI angling/fishing-location + GIS/project resources | Useful project/index path, but direct inland contour data has not surfaced |

## Hold Fallback

| Jurisdiction | Best Current Source | Where To Find It | Main Limitation |
| --- | --- | --- | --- |
| Yukon | withdrawn official data + e-chart context | Yukon withdrawn bathymetry record + Yukon e-chart path | Official bathymetry dataset was withdrawn for poor accuracy |

## Highest-ROI Promotion Order Right Now

1. Mississippi
2. New Brunswick
3. Nova Scotia
4. Saskatchewan
5. Maryland
6. South Dakota

## Important Reality Check

- “Remaining `33`” is stale. The current actionable non-survey set is `39`.
- Some of these can absolutely be promoted to survey-grade geometry.
- Some reservoir-subset and project/index jurisdictions will only become “survey-grade” for a subset of lakes, not all inland waters, unless a stronger official dataset appears.
- `Georgia`, `South Dakota`, `New Brunswick`, and `Nova Scotia` are already in active promotion workflows.
