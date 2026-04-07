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
  mn_contours.pmtiles    # 152 MB — Minnesota (2,000 lakes)
  on_contours.pmtiles    # 278 MB — Ontario (11,000+ lakes)
  mi_contours.pmtiles    #  34 MB — Michigan (2,000+ lakes)
  nh_contours.pmtiles    #  33 MB — New Hampshire
  fl_contours.pmtiles    #  29 MB — Florida
  lagos_contours.pmtiles #  35 MB — LAGOS nationwide (8,687 lakes)
  ab_contours.pmtiles    #  25 MB — Alberta
  mt_contours.pmtiles    #  16 MB — Montana
  wa_contours.pmtiles    #  13 MB — Washington
  ma_contours.pmtiles    #  12 MB — Massachusetts
  ne_contours.pmtiles    # 9.2 MB — Nebraska
  vt_contours.pmtiles    # 2.5 MB — Vermont
  ia_contours.pmtiles    # 1.9 MB — Iowa
  mb_contours.pmtiles    # 7.6 KB — Manitoba
  sk_contours.pmtiles    #  28 KB — Saskatchewan
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
