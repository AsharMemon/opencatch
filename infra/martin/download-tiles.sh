#!/bin/bash
# Download all bathymetry PMTiles from B2 for Martin tile server
# Run this before starting docker-compose

set -e

TILE_DIR="$(dirname "$0")/tiles"
mkdir -p "$TILE_DIR"

echo "Downloading bathymetry PMTiles to $TILE_DIR..."

# Download from Vast.ai instance (or B2 if configured)
# These files total ~640 MB
TILES=(
  al_contours.pmtiles    # Alabama ADCNR lake contours
  ak_contours.pmtiles    # 1.9 MB — Alaska ADF&G bathymetry polygons
  ar_contours.pmtiles    # 2.8 MB — Arkansas Nimrod contours
  bc_contours.pmtiles    # ~1-5 MB — British Columbia bathymetric polygons
  mo_contours.pmtiles    # ~1-3 MB — Missouri Clearwater soundings
  mn_contours.pmtiles    # 152 MB — Minnesota (2,000 lakes)
  on_contours.pmtiles    # 278 MB — Ontario (11,000+ lakes)
  qc_contours.pmtiles    # 200+ MB — Quebec bathymetry lines
  mi_contours.pmtiles    #  34 MB — Michigan (2,000+ lakes)
  nh_contours.pmtiles    #  33 MB — New Hampshire
  ct_contours.pmtiles    #  ~15 MB — Connecticut
  de_contours.pmtiles    # Delaware DNREC public ponds bathymetry
  nj_contours.pmtiles    # New Jersey DEP lake-plan index
  ny_contours.pmtiles    # New York DEC contour-map index
  ri_contours.pmtiles    # Rhode Island DEM lake-management index
  nd_contours.pmtiles    #  ~10-20 MB — North Dakota
  sd_contours.pmtiles    # South Dakota digitized GFP lake maps
  fl_contours.pmtiles    #  29 MB — Florida
  hi_contours.pmtiles    # 0.1 MB — Hawaii HydroLAKES fallback
  in_contours.pmtiles    #  ~20-40 MB — Indiana
  il_contours.pmtiles    #  10-20 MB — Illinois
  ks_contours.pmtiles    # Kansas Biological Survey bathymetry contours
  lagos_contours.pmtiles #  35 MB — LAGOS nationwide (8,687 lakes)
  ab_contours.pmtiles    #  25 MB — Alberta
  mt_contours.pmtiles    #  16 MB — Montana
  wa_contours.pmtiles    #  13 MB — Washington
  ma_contours.pmtiles    #  12 MB — Massachusetts
  oh_contours.pmtiles    # 9.4 MB — Ohio
  ok_contours.pmtiles    # Oklahoma OWRB statewide max-depth points
  pa_contours.pmtiles    # Pennsylvania PFBC statewide lake footprints
  va_contours.pmtiles    # Virginia DWR statewide waterbody index
  ne_contours.pmtiles    # 9.2 MB — Nebraska
  tx_contours.pmtiles    # 1.2 MB — Texas TWDB pilot (Bardwell)
  vt_contours.pmtiles    # 2.5 MB — Vermont
  wv_contours.pmtiles    # West Virginia DNR lake-map index
  ia_contours.pmtiles    # 1.9 MB — Iowa
  wi_contours.pmtiles    # 3.4 MB — Wisconsin max-depth summaries
  nb_contours.pmtiles    # 2.2 MB — New Brunswick soundings
  ga_pfa_contours.pmtiles # Georgia PFAs digitized bathymetry surveys
  ky_kdfwr_contours.pmtiles # Kentucky KDFWR official contour KMLs
  ms_contours.pmtiles    # Mississippi digitized bathymetry surveys
  nl_contours.pmtiles    # coarse HydroLAKES fallback for Newfoundland & Labrador
  nt_contours.pmtiles    # coarse HydroLAKES fallback for Northwest Territories
  nu_contours.pmtiles    # major-lakes HydroLAKES fallback for Nunavut
  ns_contours.pmtiles    # ~10-20 MB — Nova Scotia survey coverage
  pe_contours.pmtiles    # 0.1 MB — Prince Edward Island HydroLAKES fallback
  me_contours.pmtiles    # 0.2 MB — Maine survey index
  mb_contours.pmtiles    # 1.6 MB — Manitoba survey index
  sk_contours.pmtiles    # 1.5 MB — Saskatchewan survey index
  yt_contours.pmtiles    # 10.0 MB — Yukon HydroLAKES fallback
  noaa_marine_navigation.pmtiles  # NOAA maintained channels / shipping lanes / maritime boundaries
  usace_river_navigation.pmtiles  # USACE IENC-derived river navigation overlays
  na_river_network.pmtiles        # North America packaged river-network completeness overlay
)

# Use b2 CLI if available, otherwise try curl with auth
if command -v b2 &>/dev/null; then
  for tile in "${TILES[@]}"; do
    name="${tile%% *}"  # Remove comment
    if [ -f "$TILE_DIR/$name" ]; then
      echo "  ✓ $name (already exists)"
    else
      echo "  ↓ Downloading $name..."
      b2 download-file-by-name castline-data "production/tiles/$name" "$TILE_DIR/$name"
    fi
  done
else
  echo "b2 CLI not found. Please install: pip install b2"
  echo "Or manually copy PMTiles from Vast.ai:"
  echo "  scp -P 16546 root@ssh3.vast.ai:/data/production_contours/*.pmtiles $TILE_DIR/"
  exit 1
fi

echo ""
echo "Done! $(ls "$TILE_DIR"/*.pmtiles 2>/dev/null | wc -l) tilesets downloaded."
echo "Total size: $(du -sh "$TILE_DIR" | cut -f1)"
echo ""
echo "Start Martin with: docker-compose up martin"
