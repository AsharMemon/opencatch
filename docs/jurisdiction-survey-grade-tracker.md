# North America Survey-Grade Bathymetry Tracker

Updated: 2026-04-10

This is the working acquisition board for inland `survey-grade` bathymetry across all `50` U.S. states and all `13` Canadian provinces/territories.

It complements:

- [/Users/Ashar/Documents/fish/docs/us-canada-bathymetry-coverage-matrix.md](/Users/Ashar/Documents/fish/docs/us-canada-bathymetry-coverage-matrix.md)
- [/Users/Ashar/Documents/fish/docs/official-source-gap-audit-2026-04-09.md](/Users/Ashar/Documents/fish/docs/official-source-gap-audit-2026-04-09.md)
- [/Users/Ashar/Documents/fish/docs/systematic-survey-grade-source-audit-2026-04-09.md](/Users/Ashar/Documents/fish/docs/systematic-survey-grade-source-audit-2026-04-09.md)
- [/Users/Ashar/Documents/fish/docs/survey-grade-promotion-playbook.md](/Users/Ashar/Documents/fish/docs/survey-grade-promotion-playbook.md)

## Status Legend

- `Survey live`: real contour/survey-backed inland tiles are already live
- `Supporting live`: official or derived inland lane is live, but not yet survey-grade
- `Reservoir subset`: best official path is reservoir-by-reservoir federal/state surveys
- `PDF upgrade`: official depth maps exist, but conversion work is required
- `Project/index-supported`: official atlas, project, or index resources exist, but not yet as a direct statewide bathymetry dataset
- `Withdrawn official data`: official bathymetry once existed, but the published source was withdrawn or deprecated
- `Weak-source`: no strong clean public inland survey source found yet

## Current Totals

- `24` `Survey live`
- `14` `Supporting live`
- `11` `Reservoir subset`
- `10` `PDF upgrade`
- `3` `Project/index-supported`
- `1` `Withdrawn official data`
- `0` `Weak-source`

## United States

| State | Status | Best lead / current lane | Next move |
| --- | --- | --- | --- |
| Alabama | Survey live | ADCNR contour service | Maintain |
| Alaska | Survey live | Official live survey lane | Maintain |
| Arizona | Reservoir subset | USBR reservoir surveys | Build reservoir program |
| Arkansas | Survey live | Official live survey lane | Maintain |
| California | Reservoir subset | DWR bathymetry services + USBR reservoirs | Ingest DWR + USBR |
| Colorado | Reservoir subset | USBR reservoirs + CPW summaries | Build reservoir program |
| Connecticut | Survey live | Official live survey lane | Maintain |
| Delaware | Survey live | DNREC pond contours | Maintain |
| Florida | Survey live | Official live survey lane | Maintain / style |
| Georgia | PDF upgrade | Georgia DNR PFAs now have a verified partial official subset: `21` candidate PFAs with `7` verified PDF guides | Expand and digitize the verified PFA subset |
| Hawaii | Supporting live | Derived depth-band lake lane | Keep until stronger official source appears |
| Idaho | Reservoir subset | USBR reservoirs | Build reservoir program |
| Illinois | Survey live | Official live survey lane | Maintain |
| Indiana | Survey live | Official live survey lane | Maintain |
| Iowa | Supporting live | Derived DNR depth-band lane | Keep hunting real contours |
| Kansas | Survey live | Kansas Biological Survey contours | Maintain |
| Kentucky | PDF upgrade | KDFWR bulletins / FINs lake maps | Build PDF ingestor |
| Louisiana | PDF upgrade | LDWF inland waterbody-management and aquatic-vegetation plan trail is now captured into a seeded official inventory (`6` plan URLs) | Digitize the named lake plans and move browser-capable download to Vast if needed |
| Maine | Supporting live | IF&W survey index lane | Promote indexed official lake assets |
| Maryland | PDF upgrade | MGS reservoir bathymetry pages/data | Promote reservoir bathymetry |
| Massachusetts | Survey live | Official live survey lane | Maintain |
| Michigan | Survey live | Official live survey lane | Maintain |
| Minnesota | Survey live | Official live survey lane | Maintain |
| Mississippi | PDF upgrade | MDWFP lake-depth PDFs (`75` inventoried) | Download + extract statewide PDF batch |
| Missouri | Supporting live | Clearwater sounding lane | Expand beyond pilot |
| Montana | Survey live | Official live survey lane | Maintain |
| Nebraska | Survey live | Official live survey lane | Maintain |
| Nevada | Reservoir subset | USBR reservoirs | Build reservoir program |
| New Hampshire | Survey live | Official live survey lane | Maintain |
| New Jersey | Supporting live | DEP lake-plan PDF index | Expand beyond current set |
| New Mexico | Reservoir subset | USBR reservoirs | Build reservoir program |
| New York | Supporting live | DEC contour-page index lane | Find more automatable direct path |
| North Carolina | Reservoir subset | NC reservoir project/report lane is now captured into a verified official inventory (`3` PDFs) with local downloads | Promote reservoir reports and bathymetry-backed studies into richer geometry / calibration assets |
| North Dakota | Survey live | Official live survey lane | Maintain |
| Ohio | Survey live | Official live survey lane | Maintain |
| Oklahoma | Supporting live | OWRB max-depth point lane | Promote to richer geometry |
| Oregon | Reservoir subset | USBR reservoirs | Build reservoir program |
| Pennsylvania | Supporting live | PFBC lake-footprint lane | Pair with contour/depth assets |
| Rhode Island | Supporting live | DEM lake-management/project index | Expand project/PDF inventory |
| South Carolina | PDF upgrade | SCDNR lake brochures / maps | Build brochure/PDF ingestor |
| South Dakota | PDF upgrade | GFP fisheries bathymetry PDFs (`1649` inventoried) | Prioritize `Lake Maps` before `Lake Survey Report` long tail |
| Tennessee | Reservoir subset | TVA / USACE reservoir charts and monitoring path | Harvest official reservoir-by-reservoir maps and surveys |
| Texas | Survey live | TWDB contour lane | Scale statewide |
| Utah | Reservoir subset | USBR reservoirs | Build reservoir program |
| Vermont | Survey live | Official live survey lane | Maintain |
| Virginia | Supporting live | DWR waterbody index lane | Promote PDF/map subsets |
| Washington | Survey live | Official live survey lane | Maintain |
| West Virginia | Supporting live | WVDNR lake-map PDF index | Promote richer downloadable subsets |
| Wisconsin | Supporting live | Hypsography-derived depth bands | Keep hunting real contours |
| Wyoming | Reservoir subset | USBR reservoirs | Build reservoir program |

## Canada

| Province / Territory | Status | Best lead / current lane | Next move |
| --- | --- | --- | --- |
| Alberta | Survey live | Official live survey lane | Maintain |
| British Columbia | Survey live | Official live survey lane | Maintain |
| Manitoba | PDF upgrade | Official report/PDF bathymetry for named lakes | Harvest report/PDF bathymetry |
| New Brunswick | PDF upgrade | Official lake-depth interactive map + PDFs (`233` inventoried) | Download + convert PDF batch, then merge with existing NB bathy GeoJSON |
| Newfoundland & Labrador | Project/index-supported | Official water-resources atlas/report/project lane now indexed into `152` structured links, but not a direct province-wide inland bathymetry dataset | Promote atlas/project/report leads into richer structured geometry/index layers |
| Northwest Territories | Supporting live | HydroLAKES fallback + official inland-water service leads | Inspect GNWT service layers |
| Nova Scotia | PDF upgrade | Lake Inventory program with `1091` inventoried PDFs | Download region batches + promote into contour geometry |
| Nunavut | Project/index-supported | Official procurement and coastal-resource inventory trail confirms real Nunavut bathymetry projects, but not a territory-wide inland dataset | Build a Nunavut project index instead of treating the territory as fully blank |
| Ontario | Survey live | Official live survey lane | Maintain |
| Prince Edward Island | Project/index-supported | Official angling/fishing-location and GIS/project lanes are real, even though a direct inland bathymetry dataset has not surfaced | Build a PEI waterbody/project index and keep fallback for geometry |
| Quebec | Survey live | Official live survey lane | Maintain |
| Saskatchewan | Supporting live | Official bathymetric viewer + index | Promote viewer/index into geometry |
| Yukon | Withdrawn official data | Official Yukon bathymetry record existed but was withdrawn for poor accuracy; e-chart product still exists for boating context | Keep fallback plus e-chart path unless a newer inland bathymetry program appears |

## Priority Order

### Immediate highest ROI

1. Mississippi
2. South Dakota
3. New Brunswick
4. Nova Scotia
5. Saskatchewan
6. Maryland

### Current execution batch status

1. `Mississippi`
   - official inventory captured: `75` MDWFP lake-depth PDFs
   - inventory file: [/Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv)
2. `South Dakota`
   - official inventory captured: `3863` total GFP fisheries reports, `1649` bathymetry-relevant PDFs
   - inventory files:
     - [/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_fisheries_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_fisheries_inventory.csv)
     - [/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv)
3. `New Brunswick`
   - official inventory captured: `233` lake-depth PDFs
   - inventory file: [/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv)
4. `Nova Scotia`
   - official inventory captured: `1091` Lake Inventory PDFs
   - inventory file: [/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv)
5. `Georgia`
   - official partial subset captured: `21` candidate PFAs, `7` verified PDF guides
   - inventory file: [/Users/Ashar/Documents/fish/data/bathymetry/ga/ga_pfa_pdf_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/ga/ga_pfa_pdf_inventory.csv)
   - downloaded PDF manifest: [/Users/Ashar/Documents/fish/data/bathymetry/ga/pdfs/download_manifest.json](/Users/Ashar/Documents/fish/data/bathymetry/ga/pdfs/download_manifest.json)
6. `Newfoundland & Labrador`
   - official project/index-supported lane captured: `152` structured atlas/report/project links
   - inventory file: [/Users/Ashar/Documents/fish/data/bathymetry/nl/nl_water_resources_index.csv](/Users/Ashar/Documents/fish/data/bathymetry/nl/nl_water_resources_index.csv)
7. `Louisiana`
   - official LDWF plan inventory captured: `6` seeded official plan URLs
   - inventory file: [/Users/Ashar/Documents/fish/data/bathymetry/la/la_ldwf_plan_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/la/la_ldwf_plan_inventory.csv)
   - planned manifest: [/Users/Ashar/Documents/fish/data/bathymetry/la/pdfs/download_manifest.json](/Users/Ashar/Documents/fish/data/bathymetry/la/pdfs/download_manifest.json)
   - manual retrieval queue: [/Users/Ashar/Documents/fish/data/bathymetry/la/manual_queue/la_ldwf_manual_retrieval_queue.csv](/Users/Ashar/Documents/fish/data/bathymetry/la/manual_queue/la_ldwf_manual_retrieval_queue.csv)
   - search-index hits: [/Users/Ashar/Documents/fish/data/bathymetry/la/manual_queue/la_ldwf_search_index_hits.csv](/Users/Ashar/Documents/fish/data/bathymetry/la/manual_queue/la_ldwf_search_index_hits.csv)
8. `North Carolina`
   - official reservoir report inventory captured: `3` verified PDFs
   - inventory file: [/Users/Ashar/Documents/fish/data/bathymetry/nc/nc_reservoir_report_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/nc/nc_reservoir_report_inventory.csv)
   - downloaded PDF manifest: [/Users/Ashar/Documents/fish/data/bathymetry/nc/pdfs/download_manifest.json](/Users/Ashar/Documents/fish/data/bathymetry/nc/pdfs/download_manifest.json)
   - extracted calibration signals: [/Users/Ashar/Documents/fish/data/bathymetry/nc/extracted/nc_reservoir_report_signals.csv](/Users/Ashar/Documents/fish/data/bathymetry/nc/extracted/nc_reservoir_report_signals.csv)
   - calibration metadata: [/Users/Ashar/Documents/fish/data/bathymetry/nc/calibration/nc_reservoir_calibration_metadata.csv](/Users/Ashar/Documents/fish/data/bathymetry/nc/calibration/nc_reservoir_calibration_metadata.csv)
9. `Prince Edward Island`
   - direct official publications captured: `2` verified PDFs
   - inventory file: [/Users/Ashar/Documents/fish/data/bathymetry/pe/pei_publication_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/pe/pei_publication_inventory.csv)
   - downloaded PDF manifest: [/Users/Ashar/Documents/fish/data/bathymetry/pe/pdfs/download_manifest.json](/Users/Ashar/Documents/fish/data/bathymetry/pe/pdfs/download_manifest.json)

### Reusable federal program

1. Arizona
2. California
3. Colorado
4. Idaho
5. Nevada
6. New Mexico
7. Oregon
8. Utah
9. Wyoming

### True weak-source jurisdictions

None after the April 10 deep official-source sweep. The remaining low-confidence jurisdictions now all have a more specific official path:

1. Louisiana -> `PDF upgrade`
2. North Carolina -> `Reservoir subset`
3. Nunavut -> `Project/index-supported`
4. Prince Edward Island -> `Project/index-supported`

### Project/index-supported jurisdictions

1. Newfoundland & Labrador
2. Nunavut
3. Prince Edward Island

### Withdrawn official data jurisdictions

1. Yukon

## Practical Interpretation

- We already have `100%` inland baseline coverage.
- The real work now is promoting non-survey jurisdictions into stronger official lanes.
- The fastest wins are no longer generic ML improvements; they are:
  - official PDF map ingestion
  - federal reservoir survey reuse
  - promotion of existing official index/viewer lanes into real geometry
- The promotion workflow is now codified in:
  - [/Users/Ashar/Documents/fish/docs/survey-grade-promotion-playbook.md](/Users/Ashar/Documents/fish/docs/survey-grade-promotion-playbook.md)
