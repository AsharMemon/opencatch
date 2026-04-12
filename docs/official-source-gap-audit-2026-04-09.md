# Official Source Gap Audit — 2026-04-09

This is the current official-source audit for pushing North America toward:

1. `100%` inland coverage with some live lane everywhere
2. the highest possible share of `survey-grade` inland coverage
3. full U.S./Canada ocean/coastal coverage
4. continent-scale river coverage

## Honest Bottom Line

- We already have `100%` inland baseline coverage across the U.S. + Canada.
- We do **not** yet have `100%` survey-grade coverage.
- The remaining survey-grade gap is bigger than the old coarse-fallback-only list:
  - `24` jurisdictions are already `survey live`
  - the remaining `39` jurisdictions still rely on supporting lanes, PDF/index lanes, reservoir subsets, or coarse fallback
- Reaching literal `100% survey-grade` with public data alone is unlikely, because some jurisdictions only expose:
  - PDFs / scanned maps
  - project-by-project reservoir surveys
  - indices / footprints rather than contour geometry
  - no obvious public lake bathymetry at all
- The realistic path is:
  - ingest every official vector / ArcGIS / geodatabase survey source we can find
  - promote PDF/index lanes where feasible
  - use federal reservoir / navigation sources where states do not publish lake surveys
  - keep ML/coarse fallback only where no stronger source exists

## Current Inland Survey-Grade Gaps

From [us-canada-bathymetry-coverage-matrix.md](/Users/Ashar/Documents/fish/docs/us-canada-bathymetry-coverage-matrix.md), the remaining `non-survey-live` jurisdictions are:

- `30` U.S. states:
  - Arizona, California, Colorado, Georgia, Hawaii, Idaho, Iowa, Kentucky, Louisiana, Maine, Maryland, Mississippi, Missouri, Nevada, New Jersey, New Mexico, New York, North Carolina, Oklahoma, Oregon, Pennsylvania, Rhode Island, South Carolina, South Dakota, Tennessee, Utah, Virginia, West Virginia, Wisconsin, Wyoming
- `9` Canadian jurisdictions:
  - Manitoba, New Brunswick, Newfoundland & Labrador, Nova Scotia, Northwest Territories, Nunavut, Prince Edward Island, Saskatchewan, Yukon

This is now a `quality-upgrade` problem, not a `coverage` problem.

## Immediate Official Inland Wins

These are the strongest non-survey-live leads found in the latest sweep.

### Mississippi

- Official statewide PDF lake-depth lane exists:
  - [MDWFP Lake Depth Maps](https://www.mdwfp.com/fishing-boating/lake-depth-maps/)
  - example official PDF: [Lake Natchez depth map](https://www.mdwfp.com/sites/default/files/2024-05/Lakenatchezdepthmap.pdf)
- Why it matters:
  - this looks like one of the strongest remaining statewide official lake-depth collections
  - high-priority PDF extraction target

### South Dakota

- Official statewide fisheries-survey PDFs include contour maps and sonar metadata:
  - example official PDF: [Lake Thompson](https://gfp.sd.gov/UserDocs/nav/Thompson.pdf)
- Why it matters:
  - the PDFs explicitly reference sonar survey dates and contour maps
  - this is a real official lake-depth lane, just not a clean vector service

## Official But Weaker / Harder Inland Leads

These look real, but they are not yet “clean statewide contour service” wins.

### Kentucky

- Official PDF-based lake contour map lane appears to exist through Kentucky Fish and Wildlife:
  - [Kentucky Fisheries Bulletin example PDF](https://fw.ky.gov/Fish/Documents/FishBulletin093.pdf)
- Likely outcome:
  - PDF extraction or lake-by-lake ingest, not a clean statewide vector contour source

### Maryland

- Official reservoir bathymetry pages/data exist through Maryland Geological Survey:
  - [Maryland Geological Survey bathymetry index](https://www.mgs.md.gov/coastal_geology/bathy_index.html)
- Likely outcome:
  - strong reservoir-by-reservoir ingest path with PDFs/data ZIPs, but not a statewide lake contour service

### Nova Scotia

- Official Lake Inventory program exists with `1000+` bathymetric maps:
  - [Nova Scotia Lake Inventory](https://novascotia.ca/fish/sportfishing/our-lakes/lake-inventory/)
- Likely outcome:
  - very strong official PDF/scanned-map promotion target
  - better than a generic fallback, but still needs conversion work

### New Brunswick

- Official lake-depth interactive map / PDFs exist:
  - province leads already identified alongside the current sounding-point lane
- Likely outcome:
  - upgrade from sounding/index coverage into richer contour geometry

### Saskatchewan

- Official lake-depth viewer exists:
  - [Saskatchewan bathymetric maps viewer](https://gisappl.saskatchewan.ca/Html5Ext/?viewer=bathy)
  - province map portal page: [Access Geographic Information for Saskatchewan](https://www.saskatchewan.ca/government/notarized-documents-legislation-maps/maps)
- Likely outcome:
  - promote from survey-index/viewer coverage into direct PDF or geometry harvesting

### Georgia

- Official outdoor mapping hub exists:
  - [Georgia DNR online services / Georgia Outdoor Map](https://gadnr.org/onlineServices)
- Likely outcome:
  - needs deeper layer-by-layer inspection to determine whether bathymetry/contours are actually exposed, or just general recreation layers

### Tennessee

- Tennessee should not stay in the `true weak-source` bucket.
- TVA remains the strongest official path:
  - [TVA lake information/maps entry point](https://www.tva.com/environment/lake-levels)
  - Tennessee state monitoring docs point to `TVA`, `USACE`, and `APGI` reservoir data/expertise
- Likely outcome:
  - reservoir-by-reservoir official assets, not one simple statewide contour feed

### California / Arizona / Colorado / Idaho / Nevada / New Mexico / Oregon / Utah / Wyoming

- These states remain mostly a `federal reservoir survey subset` problem.
- The strongest reusable path is:
  - `USBR` sedimentation / bathymetric reservoir surveys
  - plus any state-agency reservoir or water-resources bathymetry services
- California is slightly stronger than the rest because `California DWR` bathymetry services appear to exist in addition to `USBR`.

### North Carolina / South Carolina / Louisiana

- These states appear more likely to be solved via:
  - official wildlife / fisheries brochures
  - reservoir-by-reservoir PDFs
  - or weaker project-specific state sources
- They still do **not** look like clean statewide contour-service wins in this pass.

### Yukon / Northwest Territories / Nunavut / Prince Edward Island / Newfoundland & Labrador

- These remain the weakest Canadian inland bathymetry jurisdictions in public-source terms.
- Notes:
  - Yukon has an official bathymetry record, but it appears to have been withdrawn for poor accuracy
  - Northwest Territories has official inland-water / hydrographic services worth deeper inspection, but not a clear bathymetry contour lane yet
  - Newfoundland & Labrador, Nunavut, and Prince Edward Island still have no strong inland survey-grade source in this pass

## Ocean / Coastal Completion

### United States

The U.S. coastal path is straightforward and strong:

- [NOAA GIS Data & Services](https://nauticalcharts.noaa.gov/data/gis-data-and-services.html)
- [NOAA ENC Direct to GIS](https://nauticalcharts.noaa.gov/learn/encdirect/)
- [NOAA CO-OPS / Tides and Currents](https://tidesandcurrents.noaa.gov/)

Practical use:

- `NOAA ENC Direct to GIS` for chart objects, soundings, contours, aids to navigation
- `NOAA CO-OPS` for tides, currents, water levels, predictions
- NOAA coastal coverage can be pushed close to complete for U.S. coasts and Great Lakes using official sources alone

### Canada

The Canadian path is viable, but licensing/usage needs to stay explicit:

- [CHS NONNA](https://www.charts.gc.ca/data-gestion/nonna/index-eng.html)
- [CHS Public Website Clickwrap Agreement](https://www.charts.gc.ca/data-gestion/terms-resourcemaps-cartesressources-eng.html)
- [CHS FAQ / licensing context](https://charts.gc.ca/help-aide/faq-eng.html)

Key constraints:

- CHS public-website digital data can be used in derivative products for `non-navigational` purposes with attribution and disclaimer per the clickwrap terms.
- CHS data/products are **not** automatically okay for navigational carriage or for implying CHS endorsement.
- If we want closer-to-official chart-derived Canadian marine presentation at scale, we should stay inside:
  - `NONNA + public-website data + attribution/disclaimer` for non-navigational use, or
  - separate CHS licensing where required

## Rivers — Continent-Wide Path

### United States

The best official river-navigation stack is:

- [USACE IENC SHP downloads](https://ienccloud.us/ienc_shp.html)
- [NOAA GIS Data & Services](https://nauticalcharts.noaa.gov/data/gis-data-and-services.html)

Practical use:

- `USACE IENC` for inland navigable rivers/channels
- `NOAA ENC` for tidal rivers, estuaries, harbor approaches, and connected coastal navigation corridors
- pair with `NHDPlus HR / 3DHP` style hydrography geometry for river-network completeness where chart bathymetry is absent

Honest limitation:

- This gets us strong coverage for navigable U.S. rivers.
- It does **not** mean every creek/stream gets true survey-grade bathymetry.

### Canada

The Canadian river path is weaker than the U.S. one, but still workable:

- [National Hydro Network (GeoBase series)](https://open.canada.ca/data/en/dataset/87b08750-4180-4d31-9414-a9470eba9b42)
- [CHS NONNA](https://www.charts.gc.ca/data-gestion/nonna/index-eng.html)
- [CHS tides / water level information context](https://charts.gc.ca/help-aide/faq-eng.html)

Practical use:

- `NHN` for river geometry and network completeness
- `CHS` bathymetry / chart data where navigable river corridors are covered
- water-level / current products where available for major navigable systems

Honest limitation:

- There is no single obvious public “all Canadian rivers survey bathymetry” source comparable to U.S. inland navigation coverage.

## Immediate Build Queue

1. Promote `Mississippi` and `South Dakota` into live PDF-derived contour lanes.
2. Promote `New Brunswick`, `Nova Scotia`, and `Saskatchewan` from supporting/index lanes into richer contour geometry.
3. Promote `Maryland` reservoir bathymetry into a live survey-backed reservoir lane.
4. Build the reusable `USBR reservoir survey` program for the western non-survey states.
5. Keep hunting the true weak-source jurisdictions before spending more time on ML upgrades.
