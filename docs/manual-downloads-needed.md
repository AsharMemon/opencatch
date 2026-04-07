# Manual Downloads Needed

These state GIS servers blocked programmatic downloads. Visit each URL in a browser and download manually.

## US States

### Connecticut (7,495 contour features, 125 waterbodies)
- **URL**: https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer
- **Action**: Open in ArcGIS Online, export as shapefile/GeoJSON

### Ohio (2,809 contour features)
- **URL**: https://gis.ohiodnr.gov/MapViewer/?config=LakeContourMaps
- **Action**: Use the ODNR map viewer to export data, or contact ODNR GIS

### New Hampshire (9,285 lines + 7,351 polygons)
- **URL**: https://new-hampshire-geodata-portal-1-nhgranit.hub.arcgis.com/datasets/NHGRANIT::nh-bathymetry-lakes-polygons
- **Action**: Click "Download" on the ArcGIS Hub page, select shapefile
- **FTP alternative**: https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Elevation_and_Derived_Products/d-bathymetry/

### North Dakota (4,765 contour features)
- **URL**: https://gis.nd.gov/hubdata/datasets?q=lake+contour
- **Action**: Search for "lake contour" on ND GIS Hub, download shapefile

### Illinois (4,828 depth contour features, 44 lakes)
- **URL**: https://clearinghouse.isgs.illinois.edu/data/hydrology
- **Action**: Search for lake depth contours, download shapefile

### Indiana (164 lakes, 5-ft contours)
- **URL**: https://maps.indiana.edu/download/Hydrology/Water_Bodies_Lakes_Bathymetry.zip
- **Action**: Direct download (server may be intermittently available)

### Massachusetts (104+ waterbodies, ~970 MB)
- **URL**: https://www.mass.gov/info-details/massgis-data-bathymetry
- **Action**: Follow MassGIS download links for bathymetry shapefile

### Nebraska
- **URL**: https://nebraskamap-ne.hub.arcgis.com/search?q=lake+contour
- **Action**: Search on Nebraska Map portal

### Alaska (998 features, 52 lakes)
- **URL**: https://adfg.maps.arcgis.com/home/search.html?q=bathymetry
- **Action**: Search ADF&G ArcGIS portal for bathymetry layers

## Canadian Provinces

### British Columbia (2,600+ lakes — PDF maps with CSV index)
- **URL**: https://a100.gov.bc.ca/pub/fidq/viewBathymetricMaps.do
- **CSV index**: https://catalogue.data.gov.bc.ca/dataset/1427d389-cd21-4fe2-8ed9-282d9bdcb7e2
- **Action**: Download CSV index, then batch-download PDF maps. Contact FISH.Issues@gov.bc.ca for vector data.

### Nova Scotia, New Brunswick, PEI, NL, NWT, Yukon, Nunavut
- These provinces/territories may have limited or no public digital lake bathymetry.
- Check provincial open data portals individually.

## After Downloading

Save files to `~/Documents/fish/data/bathymetry/<state_code>/`
Then run: `python3 ml/bathymetry/download_all_states.py --skip-existing`
to integrate with existing data.
