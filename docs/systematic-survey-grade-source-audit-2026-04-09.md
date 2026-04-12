# Systematic Survey-Grade Source Audit — 2026-04-09

This is the systematic source sweep for closing the remaining gap between:

- `100% inland coverage` with some lane everywhere
- `near-100% survey-grade` wherever official data actually exists

It is built on top of:

- [/Users/Ashar/Documents/fish/docs/us-canada-bathymetry-coverage-matrix.md](/Users/Ashar/Documents/fish/docs/us-canada-bathymetry-coverage-matrix.md)
- [/Users/Ashar/Documents/fish/docs/official-source-gap-audit-2026-04-09.md](/Users/Ashar/Documents/fish/docs/official-source-gap-audit-2026-04-09.md)
- [/Users/Ashar/Documents/fish/docs/survey-grade-promotion-playbook.md](/Users/Ashar/Documents/fish/docs/survey-grade-promotion-playbook.md)

## Status Legend

- `Supporting lane already live`: a non-survey official lane is already live, but there is a clearer upgrade path
- `Vector/GIS survey source`: strong official source, usually best next ingest target
- `Official PDF / brochure maps`: real official depth maps exist, but conversion work is needed
- `Federal reservoir survey subset`: official survey-grade data exists, but only for some reservoirs/lakes
- `Index / metadata only`: source proves bathymetry exists, but not yet exposed as clean geometry
- `No clean public source found yet`: no strong public source found in this pass

## U.S. Non-Survey Jurisdictions — Current Best Read

| Jurisdiction | Best official lead found | Classification | Practical next move |
| --- | --- | --- | --- |
| Arizona | USBR Theodore Roosevelt Lake sedimentation survey data / GIS | Federal reservoir survey subset | Ingest USBR reservoir surveys first, then look for AZGFD lake-specific PDFs |
| California | USBR reservoir surveys; California DWR bathymetry services; California Seafloor / CSMP for coast | Federal reservoir survey subset | Treat inland and coastal separately; ingest USBR + DWR inland reservoir surveys |
| Colorado | USBR reservoir surveys; CPW fishery survey summaries | Federal reservoir survey subset | Use USBR for survey-grade reservoirs, keep CPW as supporting context |
| Georgia | GA Outdoor Map / DNR PFAs now have an executable partial official subset: `21` candidate PFAs with `7` verified fishing-guide PDFs | Official PDF / partial subset | Digitize and expand the verified Georgia PFA subset |
| Hawaii | Existing HydroLAKES-derived depth-band lane is the practical inland baseline; no stronger statewide inland bathymetry source found | Supporting lane already live | Keep the current lane unless a stronger official inland source appears |
| Idaho | USBR reservoir surveys (Anderson Ranch, Arrowrock, Lake Lowell, Palisades, etc.) | Federal reservoir survey subset | Build Idaho reservoir lane from USBR first |
| Iowa | Existing Iowa DNR lake-summary polygons are already live, but no clean public contour geometry surfaced in this pass | Supporting lane already live | Keep searching for official contour geometry while maintaining the current derived bands |
| Kentucky | KDFWR FINs lake maps, Fish Boat KY, fisheries bulletin PDFs with contour maps | Official PDF / brochure maps | Build a Kentucky PDF map ingestor instead of waiting for a vector service |
| Louisiana | LDWF inland waterbody-management and aquatic-vegetation plan trail is now captured into a seeded official inventory of `6` named plan URLs, but direct scripted and Playwright browser fetches still hit `403` | Official PDF / lake-plan subset | Use search-index or manual-browser-assisted retrieval, then digitize named inland lake plans |
| Maine | Official IF&W lake-region map/index lane is already live, but not yet promoted into contour geometry | Supporting lane already live | Continue promoting the indexed official lake assets into downloadable map/geometry subsets |
| Maryland | Maryland Geological Survey reservoir bathymetry pages (for example Liberty Reservoir) | Official PDF / brochure maps | Ingest MGS reservoir maps and data lake-by-lake |
| Mississippi | MDWFP Lake Depth Maps page with `75` inventoried official PDF depth maps | Official PDF / brochure maps | Official inventory captured; download + extract statewide PDF batch |
| Missouri | Official depth-point/sounding lane is already live for Clearwater Lake, but not statewide | Supporting lane already live | Expand from the current lake pilot into a broader Missouri official-lake program |
| Nevada | USBR reservoir survey subset (for example Lahontan); NDOW waterbody pages give summary maps but not clear bathymetric geometry | Federal reservoir survey subset | Build Nevada from USBR reservoirs first |
| New Jersey | Official lake-plan PDF index lane is already live, but the broader NJDEP fish-survey page remains protected | Supporting lane already live | Expand beyond the current three lake-plan PDFs or find a less protected official survey path |
| New Mexico | USBR reservoir surveys (Heron, Navajo, Avalon, etc.) | Federal reservoir survey subset | Build NM reservoir lane from USBR first |
| New York | Official DEC contour-page index lane is already live statewide, but still not a direct contour dataset | Supporting lane already live | Keep enriching the index and look for a more automatable direct PDF/geometry path |
| North Carolina | NCDEQ reservoir assessment reports plus Jordan Lake bathymetry/model work are now captured into a verified official inventory of `3` downloadable PDFs | Federal / reservoir subset path | Promote the reservoir reports and bathymetry-backed studies into richer geometry / calibration assets |
| Oklahoma | Official OWRB statewide depth-point lane is already live, but not yet contour-rich | Supporting lane already live | Promote from max-depth points into richer contour geometry where official assets exist |
| Oregon | USBR reservoir survey subset (Keene Creek, Ochoco, Prineville, Thief Valley, etc.) | Federal reservoir survey subset | Build Oregon reservoir lane from USBR first |
| Pennsylvania | Official PFBC lake-footprint lane is already live, but it still needs contour/depth geometry | Supporting lane already live | Pair PFBC lake footprints with richer official contour/PDF assets where possible |
| Rhode Island | Official DEM lake-management/project index lane is already live for a small set of lakes | Supporting lane already live | Expand the official project/PDF inventory beyond the current waterbodies |
| South Carolina | SCDNR lake brochures with lake-specific depth maps / “hot spot” maps | Official PDF / brochure maps | High-priority brochure/PDF ingestion target |
| South Dakota | GFP statewide fisheries reports expose `1649` bathymetry-relevant PDFs (`Lake Maps` + `Lake Survey Report`) | Official PDF / brochure maps | Official inventory captured; prioritize `Lake Maps` batch before the longer survey-report tail |
| Tennessee | Tennessee state monitoring docs explicitly point to TVA and USACE reservoir expertise/data; TWRA/TVA reservoir map path appears real even if not one clean statewide contour service | Federal / reservoir subset path | Promote Tennessee out of weak-source and harvest reservoir-by-reservoir official maps/surveys |
| Utah | USBR Hyrum Reservoir bathymetric survey and other federal reservoir assets | Federal reservoir survey subset | Build Utah reservoir lane from USBR first |
| Virginia | Official DWR statewide waterbody-index lane is already live, with page coordinates and map/report links | Supporting lane already live | Promote directly downloadable contour/PDF subsets into richer geometry |
| West Virginia | Official WVDNR lake-map PDF index lane is already live | Supporting lane already live | Improve geocode match coverage and promote richer downloadable subsets when available |
| Wisconsin | Existing hypsography-based depth-band lane is already live, but real official contour geometry still has not surfaced cleanly | Supporting lane already live | Keep searching for richer contour geometry to replace max-depth-derived bands |
| Wyoming | USBR reservoir surveys (Fontenelle, Bighorn, Pathfinder/Seminoe, Keyhole, etc.) | Federal reservoir survey subset | Build Wyoming reservoir lane from USBR first |

## Canadian Supporting Lanes — Upgrade Potential

| Jurisdiction | Best official lead found | Classification | Practical next move |
| --- | --- | --- | --- |
| Manitoba | Government reports contain real bathymetry for some lakes (for example Shoal Lakes / Oak Lake), but no clean unified public bathymetry service found in this pass | Official PDF / brochure maps | Harvest report/PDF bathymetry for named lakes where available |
| New Brunswick | Official interactive lake depth map with `233` inventoried downloadable PDF lake-depth maps | Official PDF / brochure maps | Official inventory captured; merge PDF conversion with existing NB bathy GeoJSON coverage |
| Nova Scotia | Official Lake Inventory program with `1091` inventoried bathymetric PDFs plus an official lake-survey point layer | Official PDF / brochure maps | Official inventory captured; promote region-by-region PDF batches into contour geometry |
| Saskatchewan | Official bathymetric index/map service plus official bathymetric map HTML5/PDF path | Index / metadata only | Promote from the viewer/index into direct PDF or geometry harvesting |
| Newfoundland & Labrador | Official water-resources atlas, hydrology reporting, and project/report mapping now indexed into `152` structured links, but not a direct inland bathymetry dataset | Project / index-supported | Promote atlas/project/report leads into a structured index-supported lane instead of treating NL as fully blank |
| Northwest Territories | Official inland-water / hydrographic-chart services appear to exist, but not yet as clean lake-bathymetry contours | Index / metadata only | Inspect GNWT map-service layers for promotable chart/point subsets |
| Nunavut | Official procurement and coastal-resource inventory trail confirms real bathymetry projects around Nunavut communities, even though there is no territory-wide inland dataset | Project / index-supported | Build a Nunavut project index instead of treating NU as fully blank |
| Prince Edward Island | Official angling/fishing-location and GIS/project resources are real enough to support an index/project lane, and `2` direct official PEI PDFs now form a small downloadable subset | Project / index-supported | Keep the PEI waterbody/project index, use the downloadable publications as supporting context, and keep fallback for geometry |
| Yukon | Official bathymetry record exists, but it was withdrawn for poor accuracy; official Yukon boating e-charts still exist for contextual water features | Withdrawn official data | Track Yukon separately from ordinary weak-source hunting; keep fallback unless a newer bathymetry program appears |

## What This Changes

The remaining gap is no longer one uniform problem. It has split into four very different acquisition programs:

1. `Vector/GIS survey services`
   - easiest ingest
   - highest automation
   - best path to survey-grade tiles fast

2. `Official PDF / brochure map jurisdictions`
   - Georgia partial PFA subset
   - Kentucky
   - Mississippi
   - South Carolina
   - South Dakota
   - Maryland
   - New Brunswick
   - Nova Scotia
   - parts of Manitoba
   - these are probably the highest-ROI next upgrades after easy ArcGIS/vector wins

3. `Federal reservoir survey subset jurisdictions`
   - Arizona
   - California
   - Colorado
   - Idaho
   - Nevada
   - New Mexico
   - Oregon
   - Utah
   - Wyoming
   - these can get materially better even without statewide state-agency contour services

4. `True weak-source jurisdictions`
   - none after the April 10 deep official sweep
   - the remaining hard jurisdictions are now better described as `PDF upgrade`, `reservoir subset`, or `project / index-supported`

5. `Project / index-supported jurisdictions`
   - Newfoundland & Labrador
   - Nunavut
   - Prince Edward Island

6. `Withdrawn official data jurisdictions`
   - Yukon

## Highest-ROI Next Build Queue

1. `Mississippi`
   - official statewide lake-depth PDFs already inventoried (`75`)
   - likely the biggest immediate inland survey-grade upgrade

2. `New Brunswick`
   - official interactive lake-depth map + PDFs already inventoried (`233`)
   - likely promotable from current sounding/index lane

3. `Nova Scotia`
   - official Lake Inventory program with `1091` inventoried bathymetric maps
   - likely one of the biggest Canadian quality upgrades still available

4. `South Dakota`
   - official GFP fisheries reports now inventoried as `1649` bathymetry-relevant PDFs
   - promising statewide PDF extraction target

## Execution Batch Captured

The highest-priority PDF jurisdictions now have concrete official inventories on disk:

- Mississippi:
  - [/Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv)
- South Dakota:
  - [/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_fisheries_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_fisheries_inventory.csv)
  - [/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv)
- New Brunswick:
  - [/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv)
- Nova Scotia:
  - [/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv)

That means the next work for these four jurisdictions is no longer discovery. It is bulk download, PDF extraction, and promotion into contour geometry.

5. `USBR reservoir states`
   - Arizona, California, Colorado, Idaho, Nevada, New Mexico, Oregon, Utah, Wyoming
   - treat as one reusable federal acquisition program, not nine separate hunts

## Honest Conclusion

Literal `100% survey-grade` across all U.S. states and Canadian provinces/territories is still unlikely from public data alone.

But this sweep shows we can get meaningfully closer by splitting the problem into:

- `official vector/GIS`
- `official PDFs`
- `federal reservoir surveys`
- `fallback only where truly necessary`

That is the path most likely to maximize real survey-backed coverage instead of just maximizing raw map count.
