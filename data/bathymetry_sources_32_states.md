# Lake Bathymetry Data Sources — 32 US States

Generated: 2026-03-29

## CROSS-STATE RESOURCES (Apply to ALL states)

### 1. USGS Inland Bathymetric Survey Inventory v4
- **Coverage**: All CONUS, Alaska, Puerto Rico — hundreds of lakes
- **Format**: Shapefile (227 MB), Geodatabase (59 MB)
- **ScienceBase**: https://www.sciencebase.gov/catalog/item/5fce600bd34e30b912396ad0
- **ArcGIS REST**: https://partnerships.nationalmap.gov/arcgis/rest/services/USGS_Inland_Bathymetry/MapServer/0/query
- **DOI**: https://doi.org/10.5066/P9PDX9X3
```bash
# Download the full shapefile inventory
curl -L -o USGS_InlandBathy_v4.zip "https://www.sciencebase.gov/catalog/file/get/5fce600bd34e30b912396ad0?name=USGS_InlandBathyResearch_Inventory_v4.zip"

# Query by state (e.g., Wisconsin) via ArcGIS REST
curl -o usgs_bathy_WI.geojson "https://partnerships.nationalmap.gov/arcgis/rest/services/USGS_Inland_Bathymetry/MapServer/0/query?where=STATE%3D%27WI%27&outFields=*&f=geojson"
```

### 2. GLOBathy — Global Lakes Bathymetry Dataset
- **Coverage**: 1.4M+ waterbodies worldwide
- **Paper**: https://www.nature.com/articles/s41597-022-01132-9
- **Data**: https://doi.org/10.6084/m9.figshare.c.5243309
- **Format**: GeoTIFF rasters per HydroLAKES ID
```bash
# Download GLOBathy (full dataset ~20GB)
# Individual lake rasters by HydroLAKES ID
```

### 3. Lake Map Database (lakemaps.org)
- **Coverage**: Global catalog of bathymetric surveys
- **URL**: http://lakemaps.org/
- **Note**: Reference/index only, links to primary sources

---

## STATE-BY-STATE FINDINGS

---

### TIER 1: STRONG GIS DATA AVAILABLE

---

#### NEW JERSEY (NJ) ★★★★★
**NJDEP Lake Bathymetry** — Best dataset found
- **Portal**: https://gisdata-njdep.opendata.arcgis.com/datasets/997dcf3ccdca422492c79e2dd690cc48
- **Contours**: https://gisdata-njdep.opendata.arcgis.com/datasets/njdep::bathymetric-contours-of-new-jersey/about
- **Fish & Wildlife Maps**: https://dep.nj.gov/njfw/fishing/freshwater/lake-survey-maps/ (PDF contour maps of most NJ lakes)
- **Format**: Shapefile, GeoJSON via ArcGIS Hub
```bash
# Download lake bathymetry shapefile
curl -L -o NJ_lake_bathymetry.zip "https://gisdata-njdep.opendata.arcgis.com/api/download/v1/items/997dcf3ccdca422492c79e2dd690cc48/shapefile?layers=0"

# Download bathymetric contours
curl -L -o NJ_bathy_contours.zip "https://gisdata-njdep.opendata.arcgis.com/api/download/v1/items/bathymetric-contours-of-new-jersey/shapefile?layers=0"
```

---

#### KANSAS (KS) ★★★★★
**KDWP Bathymetric Lake Maps** — 50+ lakes with KMZ + PDF
- **PDF Maps**: https://ksoutdoors.gov/Fishing/Where-to-Fish-in-Kansas/Bathymetric-Lake-Maps/PDF-Version-of-Maps
- **KMZ (GIS)**: https://ksoutdoors.gov/Fishing/Where-to-Fish-in-Kansas/Bathymetric-Lake-Maps/KMZ-Google-Earth-Version-of-the-Maps
- **Lakes**: ~50 lakes with bathymetric KMZ files
- **Format**: KMZ (Google Earth), PDF
- **KGS Topo Maps**: https://www.kgs.ku.edu/Hydro/lake_maps.html (major reservoirs)
- **KARS ArcGIS**: https://kars.ku.edu/pages/kansas-lakes-and-reservoirs
```bash
# Download individual KMZ files (example — need to scrape page for all URLs)
# KMZ files downloadable from the KDWP page linked above
# KGS has topo maps for: Milford, Glen Elder, Kirwin, Cedar Bluff, Clinton, Perry reservoirs
```

---

#### OKLAHOMA (OK) ★★★★★
**OWRB Bathymetric Mapping Program** — Comprehensive reservoir coverage
- **Main Page**: https://oklahoma.gov/owrb/data-and-maps/bathymetric-mapping.html
- **GIS Data**: https://oklahoma.gov/owrb/data-and-maps/gis-data.html
- **Format**: Shapefiles, Geodatabases, KMZ via ESRI ArcGIS Server
- **Grand Lake**: https://www.sciencebase.gov/catalog/item/5e4c001fe4b0ff554f6c6531 (USGS 2019 survey)
```bash
# USGS Grand Lake O' the Cherokees bathymetry
curl -L -o OK_GrandLake_bathy.zip "https://www.sciencebase.gov/catalog/file/get/5e4c001fe4b0ff554f6c6531"
```

---

#### MISSOURI (MO) ★★★★★
**USGS Missouri Lake Bathymetry Surveys** — 35+ water-supply lakes (2019-2023)
- **NW Missouri 2019-2020**: https://www.sciencebase.gov/catalog/item/5f4fbbc882ce4c3d123486d8 (12 lakes)
- **NC/WC Missouri 2020**: https://www.usgs.gov/data/bathymetric-and-supporting-data-various-water-supply-lakes-north-central-and-west-central (10 lakes)
- **NE Missouri 2021**: https://pubs.usgs.gov/publication/sir20235108
- **Missouri 2022-23**: https://pubs.usgs.gov/publication/sir20245114 (13 lakes)
- **Format**: ESRI Shapefile
```bash
# Download NW Missouri bathymetry (12 lakes)
curl -L -o MO_NW_bathy.zip "https://www.sciencebase.gov/catalog/file/get/5f4fbbc882ce4c3d123486d8"
```

---

#### MARYLAND (MD) ★★★★
**Maryland Geological Survey Reservoir Bathymetry**
- **Index**: https://www2.mgs.md.gov/coastal_geology/bathy_index.html
- **Data Page**: http://www.mgs.md.gov/publications/data_pages/reservoir_bathymetry.html
- **Loch Raven**: http://www.mgs.md.gov/coastal_geology/lochraven.html
- **Format**: CSV (comma-delimited depth data), PDF maps
- **Reservoirs**: Multiple water supply reservoirs (Loch Raven, Rocky Gorge/T. Howard Duckett, etc.)
```bash
# Need to check data page for direct download links — CSV flat files available
```

---

#### CALIFORNIA (CA) ★★★★
**CA DWR Bathymetry Index + SingleBeam Data**
- **Bathymetry Index (polygons)**: https://data.ca.gov/dataset/i06-bathymetry-index
- **SingleBeam REST (83 layers!)**: https://gis.water.ca.gov/arcgis/rest/services/Elevation/i06_SingleBeam_Bathymetry/MapServer
- **DWR ArcGIS Hub**: https://atlas-dwr.opendata.arcgis.com/
- **Format**: Shapefile, GeoJSON, CSV, KML
```bash
# Download bathymetry index shapefile
curl -L -o CA_bathy_index.zip "https://gis.data.cnra.ca.gov/api/download/v1/items/96d031dcc7844be8825fd75f02437cc5/shapefile?layers=0"

# Download bathymetry index GeoJSON
curl -L -o CA_bathy_index.geojson "https://gis.data.cnra.ca.gov/api/download/v1/items/96d031dcc7844be8825fd75f02437cc5/geojson?layers=0"

# Query individual SingleBeam layers (83 layers, each a different survey)
curl -o CA_singlebeam_layer0.geojson "https://gis.water.ca.gov/arcgis/rest/services/Elevation/i06_SingleBeam_Bathymetry/MapServer/0/query?where=1%3D1&outFields=*&f=geojson"
```

---

#### ARKANSAS (AR) ★★★★
**USGS + AGFC Combined**
- **AGFC Lake Maps**: https://www.agfc.com/resources/maps/arkansas-lake-maps/
- **Blue Mountain Lake GDB**: https://data.usgs.gov/datacatalog/data/USGS:5a3bff48e4b0d05ee8b744e7 (contours + DEM)
- **Beaver Lake 2018**: https://www.usgs.gov/data/bathymetric-and-supporting-data-beaver-lake-near-rogers-arkansas-2018
- **Dierks Lake DEM**: https://data.usgs.gov/datacatalog/data/USGS:5bd8bd82e4b0b3fc5cea242f (GeoTIFF)
- **Format**: File GDB (4ft contours, 3ft DEM), GeoTIFF
```bash
# Dierks Lake DEM
curl -L -o AR_DierksLake_bathy.zip "https://www.sciencebase.gov/catalog/file/get/5bd8bd82e4b0b3fc5cea242f"
```

---

### TIER 2: PARTIAL GIS DATA / DOWNLOADABLE PDFs

---

#### WISCONSIN (WI) ★★★
**Multiple Sources — Largest lake count but fragmented**
- **UMN/DRUM Hypsography**: https://doi.org/10.13020/f3xa-5h34 (750+ MN+WI lakes, CSV area-at-depth)
- **Wiscartography**: https://wiscartography.com/digital-data (160 lakes, shapefile + GDB, paid per-lake)
- **WI DNR Open Data**: https://data-wi-dnr.opendata.arcgis.com/
- **24K Hydro GDB**: https://data-wi-dnr.opendata.arcgis.com/datasets/cb1c7f75d14f42ee819a46894fd2e771 (lake outlines, NOT depth)
- **DNR Lake Maps (PDF)**: https://dnr.wisconsin.gov/topic/Fishing/questions/lakemaps
```bash
# Download UMN Wisconsin hypsography (free, ~80KB)
curl -L -o WI_Hypsography.zip "https://conservancy.umn.edu/bitstreams/555df6b2-5924-45b5-82fa-eb0e3d7a1505/download"

# Note: Wiscartography is commercial ($) per-lake downloads
# DNR lake depth maps are PDF only via web viewer
```

---

#### NEW YORK (NY) ★★★
**DEC Lake Contour Maps — 400+ lakes, PDF + KMZ index**
- **DEC Contour Maps**: https://www.dec.ny.gov/outdoor/9920.html
- **KMZ Index (Andy Arthur)**: https://andyarthur.org/kml-maps-waterbodies-with-dec-fishing-contour-maps.html
  - Download KMZ: https://andyarthur.org/data/kml_17189.kmz
- **GIS Inventory**: http://gis.ny.gov/gisdata/inventories/member.cfm?OrganizationID=529
- **USGS NYC Reservoirs**: https://data.usgs.gov/datacatalog/data/USGS:5f7c85a082ce1d74e7db5363
- **Format**: Individual PDFs from DEC, KMZ index, USGS GIS data for reservoirs
```bash
# Download KMZ index of all DEC contour maps
curl -L -o NY_DEC_contourmaps_index.kmz "https://andyarthur.org/data/kml_17189.kmz"

# Individual PDFs e.g. Lake George South:
curl -L -o NY_LakeGeorge_South.pdf "https://extapps.dec.ny.gov/docs/fish_marine_pdf/lkgeosomap.pdf"
```

---

#### COLORADO (CO) ★★★
**CPW Map Library + USGS Surveys**
- **CPW Map Library (900+ maps)**: https://cpw.state.co.us/maps-and-gis
- **CPW Spatial Data Hub**: https://geodata-cpw.hub.arcgis.com/
- **USGS Clear Creek Reservoir**: https://pubs.usgs.gov/publication/sim3375 (multibeam)
- **CDSS GIS**: https://cdss.colorado.gov/gis-data/gis-data-by-category
```bash
# CPW map search — search via web portal, download individual maps
# USGS Clear Creek Reservoir bathymetry
curl -L -o CO_ClearCreek_bathy.pdf "https://pubs.usgs.gov/sim/3375/sim3375.pdf"
```

---

#### OREGON (OR) ★★★
**Atlas of Oregon Lakes**
- **Main Site**: https://oregonlakesatlas.org/ (requires JavaScript)
- **Bathymetry Page**: https://oregonlakesatlas.org/bathymetry
- **ArcGIS Item**: https://www.arcgis.com/home/item.html?id=7ed7eeb4c1594abeb8dc977678e47366
- **Oregon GEOHub**: https://geohub.oregon.gov/
- **Note**: Requires manual browser access, JavaScript-dependent

---

#### IDAHO (ID) ★★★
**IDFG Lake Surveys + USGS**
- **IDFG Lake/Stream Survey**: https://idfg.idaho.gov/data/fisheries/lss
- **IDFG Open Data (ArcGIS)**: https://data-idfggis.opendata.arcgis.com/
- **USGS Lucky Peak**: https://www.sciencebase.gov/catalog/item/60ef5ee4d34e93b366704f70
- **USGS Coeur d'Alene**: https://hub.arcgis.com/documents/fe84410fca514d29a644e12674e7b2d1
- **Format**: Contour shapefiles, DEM (from USGS)
```bash
# USGS Lucky Peak Lake bathymetry
curl -L -o ID_LuckyPeak_bathy.zip "https://www.sciencebase.gov/catalog/file/get/60ef5ee4d34e93b366704f70"
```

---

#### KENTUCKY (KY) ★★★
**USGS Surveys + KDFWR Fish Attractors**
- **USGS Buckhorn Lake 2023**: https://catalog.data.gov/dataset/bathymetry-of-buckhorn-lake-kentucky-may-2023
- **KyGovMaps Open Data**: https://opengisdata.ky.gov/search?categories=geoscientific+information
- **KY Water Maps**: https://www.watermaps.ky.gov/
- **KDFWR Fish Attractors (GPX)**: https://fw.ky.gov/Fish/Pages/fish_attractor_lakes.aspx
- **Format**: XYZ point data, GPX
```bash
# USGS Buckhorn Lake bathymetry
curl -L -o KY_BuckhornLake_bathy.zip "https://www.sciencebase.gov/catalog/file/get/bathymetry-of-buckhorn-lake-kentucky-may-2023"
```

---

#### RHODE ISLAND (RI) ★★★
**RI DEM Lake Bathymetry Maps**
- **Pond Bathymetry PDF**: https://dem.ri.gov/sites/g/files/xkgbur861/files/maps/mapfile/pondbath.pdf
- **DEM Data Portal**: https://dem.ri.gov/online-services/data-maps
- **RIGIS**: https://www.rigis.org/data/topo
- **ArcGIS Elevation+Bathy**: https://www.arcgis.com/home/item.html?id=56094827920e41b98ac07b887e3075a3
```bash
# Download RI pond bathymetry map compilation (PDF)
curl -L -o RI_pond_bathymetry.pdf "https://dem.ri.gov/sites/g/files/xkgbur861/files/maps/mapfile/pondbath.pdf"
```

---

#### MISSISSIPPI (MS) ★★★
**MDWFP Lake Depth Maps** — 30+ managed lakes
- **Main Page**: https://www.mdwfp.com/fishing-boating/lake-depth-maps
- **Format**: PDF depth maps
- **Coverage**: ~30 MDWFP-managed lakes (Eagle Lake, Lake Monroe, Lake Perry, etc.)
```bash
# Download individual PDF depth maps
curl -L -o MS_EagleLake.pdf "https://www.mdwfp.com/sites/default/files/2024-05/Eagle%20Lake.pdf"
curl -L -o MS_LakeMonroe.pdf "https://www.mdwfp.com/sites/default/files/2024-05/lake-monroe-depth-map.pdf"
```

---

#### UTAH (UT) ★★★
**Utah Open Water Data + ArcGIS**
- **Open Water Data**: https://dwre-utahdnr.opendata.arcgis.com/
- **Utah Lake Bathymetry (ArcGIS)**: https://www.arcgis.com/home/item.html?id=c949158144ae403899de5134401110f4
- **USGS Great Salt Lake**: https://pubs.usgs.gov/sim/2005/2894/PDF/SIM2894.pdf
- **DWR GIS**: https://water.utah.gov/gis-maps/
```bash
# Great Salt Lake bathymetry map
curl -L -o UT_GreatSaltLake_bathy.pdf "https://pubs.usgs.gov/sim/2005/2894/PDF/SIM2894.pdf"
```

---

### TIER 3: LIMITED DATA / PDF ONLY / REQUIRES CONTACT

---

#### ALABAMA (AL) ★★
- **Alabama Open Data (ArcGIS)**: https://data-algeohub.opendata.arcgis.com/
- **USGS Lake Tuscaloosa surveys**: https://pubs.usgs.gov/sim/3176/pdf/sim3176.pdf
- **No statewide lake bathymetry GIS dataset found**

#### ARIZONA (AZ) ★★
- **AZGFD GIS**: http://gis.azgfd.gov/
- **AZ DWR Open Data**: https://gisdata2016-11-18t150447874z-azwater.opendata.arcgis.com/
- **Navionics has Apache Lake bathymetry** (commercial)
- **No statewide lake bathymetry GIS dataset found**

#### DELAWARE (DE) ★
- **DNREC Open Data**: https://dnrec.delaware.gov/dnrec-open-data/
- **Very few inland lakes; no state bathymetry dataset found**

#### GEORGIA (GA) ★★
- **GA DNR contact**: Jan.McKinnon@dnr.ga.gov for DEM data
- **No public download portal for lake bathymetry found**
- **Lakes/ponds outline**: https://opendata.atlantaregional.com/datasets/d6ab69cb899a4faf9db98464168b4b72

#### HAWAII (HI) ★
- **HI GIS**: https://planning.hawaii.gov/gis/download-gis-data-expanded/
- **Ocean bathymetry available (SOEST/HMRG)**, but very few inland lakes
- **Not relevant for inland fishing bathymetry**

#### LOUISIANA (LA) ★★
- **LDWF WMA GIS**: https://www.wlf.louisiana.gov/page/wma-gis-data-download
- **NOAA coastal**: https://www.ngdc.noaa.gov/mgg/bathymetry/maps/area3.html
- **No statewide inland lake bathymetry dataset found**

#### NEVADA (NV) ★★
- **NDWR Hub**: https://data-ndwr.hub.arcgis.com/
- **USGS Walker Lake**: https://www.usgs.gov/publications/bathymetry-walker-lake-west-central-nevada
- **Library of Congress historical maps**: Weber, Wild Horse, Lake Tahoe reservoirs
- **Limited inland lake data**

#### NEW MEXICO (NM) ★
- **NM RGIS**: https://rgis.unm.edu/
- **OSE GIS**: https://www.ose.nm.gov/GIS/maps.php
- **No specific lake bathymetry dataset found**

#### NORTH CAROLINA (NC) ★★
- **NC OneMap**: https://www.nconemap.gov/
- **USGS coastal surveys only in ScienceBase**
- **No statewide inland lake bathymetry GIS dataset found**
- **Recommend checking USGS Inland Bathy Inventory for NC-specific surveys**

#### PENNSYLVANIA (PA) ★★
- **PFBC ArcGIS**: https://pfbc.maps.arcgis.com/apps/webappviewer/index.html?id=3292981a1fcf415e9ce4a4a7a3ce98e2
- **PASDA (lake outlines, no depth)**: https://www.pasda.psu.edu/uci/DataSummary.aspx?dataset=1103
- **No statewide lake bathymetry shapefile found**

#### SOUTH CAROLINA (SC) ★★
- **SCDNR Open Data**: https://data-scdnr.opendata.arcgis.com/
- **USGS Table Rock/N. Saluda**: https://pubs.usgs.gov/sim/3289/
- **USGS Lake Bowen**: https://pubs.usgs.gov/sim/3076/pdf/sim3076.pdf
- **Contact**: gis@dnr.sc.gov
```bash
# USGS Lake Bowen bathymetry
curl -L -o SC_LakeBowen_bathy.pdf "https://pubs.usgs.gov/sim/3076/pdf/sim3076.pdf"
```

#### SOUTH DAKOTA (SD) ★★
- **SD DANR Measured Lakes**: https://apps.sd.gov/NR65LakeInfo/public.aspx
- **SD Geological Survey Digital Data**: https://www.sdgs.usd.edu/digitaldata/index.html
- **UMN/DRUM Hypsography**: https://doi.org/10.13020/f3xa-5h34 (includes some SD lakes)
- **USGS Lake Sharpe**: https://pubs.usgs.gov/sim/3307/
```bash
# UMN South Dakota hypsography
curl -L -o SD_Hypsography.zip "https://conservancy.umn.edu/bitstreams/555df6b2-5924-45b5-82fa-eb0e3d7a1505/download"
# (same download as WI — includes SD folder)
```

#### TENNESSEE (TN) ★★
- **TWRA Reservoir Maps**: https://www.tn.gov/twra/fishing/reservoirs/reservoir-fish-attractor-maps.html
- **TWRA GIS**: https://www.tn.gov/twra/gis-maps.html
- **TVA reservoir maps**: Contact TVA Map Sales, Chattanooga, TN
- **TN Open Data**: https://tn-tnmap.opendata.arcgis.com/
- **No statewide bathymetry GIS dataset found; TVA likely has data for major reservoirs**

#### VIRGINIA (VA) ★★
- **DWR GIS Hub**: https://dwr-hub-gis-data-dgif-virginia.hub.arcgis.com/
- **DWR GIS Download**: https://dwr.virginia.gov/gis/data/download/ (requires agreement)
- **Format**: ESRI GDB, Shapefiles
- **No confirmed lake bathymetry layer; need to explore after accepting terms**

#### WEST VIRGINIA (WV) ★★
- **WVDNR Lake Maps (Avenza)**: https://sites.google.com/wv.gov/dnrgis/lake-maps
- **WVDNR GIS**: https://wvdnr.gov/gis-mapping/
- **Format**: Avenza maps (PDF/GeoPDF), not standard GIS
- **WV GIS Tech Center**: https://services.wvgis.wvu.edu/ArcGIS/rest/services

#### WYOMING (WY) ★★
- **WGFD Open Data**: https://wyoming-wgfd.opendata.arcgis.com/
- **USGS Yellowstone Lake**: Available via USGS Submerged Lands
- **WY State Water Plan GIS**: https://waterplan.state.wy.us/gis/gis.html
- **Limited inland lake data beyond Yellowstone**

---

## PRIORITY DOWNLOAD QUEUE

### Immediate Downloads (curl-ready):

1. **USGS Inland Bathy Inventory v4** (ALL states) — 227MB shapefile
2. **CA DWR Bathymetry Index** — shapefile + 83-layer REST service
3. **NJ Lake Bathymetry** — shapefile from ArcGIS Hub
4. **MO USGS Lake Surveys** (35+ lakes) — shapefiles from ScienceBase
5. **WI+SD UMN Hypsography** (750+ lakes) — CSV from DRUM
6. **AR USGS surveys** (Blue Mountain, Beaver, Dierks) — GDB + GeoTIFF
7. **OK Grand Lake USGS** — shapefile
8. **ID Lucky Peak USGS** — contours + DEM
9. **NY DEC KMZ index** — links to 400+ contour PDFs
10. **KS KDWP KMZ files** — 50 lakes

### Manual Browser Required:
- OK OWRB bathymetric mapping (dynamic page)
- OR Atlas of Oregon Lakes (JavaScript required)
- CO CPW Map Library (search interface)
- VA DWR GIS Hub (requires agreement click)
- PA PFBC ArcGIS viewer (interactive only)
- WI DNR lake maps (web viewer)

### Contact Required:
- GA DNR: Jan.McKinnon@dnr.ga.gov for DEMs
- SC DNR: gis@dnr.sc.gov
- TN TVA: Map Sales Chattanooga for reservoir bathymetry

---

## SUMMARY TABLE

| State | Rating | Lakes | Format | Auto-Download? |
|-------|--------|-------|--------|----------------|
| NJ | ★★★★★ | Many | SHP/GeoJSON | YES |
| KS | ★★★★★ | ~50 | KMZ/PDF | YES |
| OK | ★★★★★ | Many | SHP/GDB | PARTIAL |
| MO | ★★★★★ | 35+ | SHP | YES |
| MD | ★★★★ | ~10 | CSV/PDF | YES |
| CA | ★★★★ | 83 layers | SHP/GeoJSON | YES |
| AR | ★★★★ | ~5 | GDB/GeoTIFF | YES |
| WI | ★★★ | 750+ | CSV hyps | YES |
| NY | ★★★ | 400+ | KMZ index/PDF | YES |
| CO | ★★★ | Several | PDF/GIS | PARTIAL |
| OR | ★★★ | Many | Web only | NO |
| ID | ★★★ | Several | SHP/DEM | YES |
| KY | ★★★ | Several | XYZ/GPX | PARTIAL |
| RI | ★★★ | Many | PDF | YES |
| MS | ★★★ | ~30 | PDF | YES |
| UT | ★★★ | Several | ArcGIS | PARTIAL |
| SD | ★★ | Some | CSV hyps | YES |
| AL | ★★ | Few | PDF | NO |
| AZ | ★★ | Few | — | NO |
| GA | ★★ | Unknown | DEM | CONTACT |
| LA | ★★ | Few | — | NO |
| NC | ★★ | Few | — | NO |
| NV | ★★ | Few | — | PARTIAL |
| PA | ★★ | Unknown | — | NO |
| SC | ★★ | Few | PDF | CONTACT |
| TN | ★★ | Many (TVA) | — | CONTACT |
| VA | ★★ | Unknown | SHP/GDB | PARTIAL |
| WV | ★★ | Some | GeoPDF | NO |
| WY | ★★ | Few | — | NO |
| DE | ★ | Very few | — | NO |
| HI | ★ | N/A | Ocean only | NO |
| NM | ★ | Few | — | NO |
