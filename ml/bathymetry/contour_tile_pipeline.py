#!/usr/bin/env python3
"""
OpenCatch — Contour Tile Pipeline

Converts GeoJSON contour data (filled depth-band polygons + depth labels)
into efficient vector tiles for mobile map display in the OpenCatch app.

Output formats:
    1. PMTiles (preferred) — single file, static hosting on B2/S3/CDN
    2. MBTiles — SQLite-based, good for offline/embedded use
    3. Directory of .pbf tiles — traditional z/x/y structure

Uses tippecanoe (Mapbox/Felt) for tile generation with a Python fallback
for environments where tippecanoe is unavailable.

The 202.7 MB Minnesota GeoJSON needs to be tileable for mobile performance;
this pipeline handles geometry simplification, zoom-dependent visibility,
and tile-size quality checks.

Usage:
    python contour_tile_pipeline.py \\
        --input /data/production_contours/all_lakes/mn_complete.geojson \\
        --output /data/tiles/mn_contours.pmtiles \\
        --format pmtiles \\
        --min-zoom 5 --max-zoom 16 \\
        --upload-b2

Requirements:
    pip install geopandas shapely tqdm
    brew install tippecanoe  # macOS
    # or: apt-get install tippecanoe  # Linux
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("contour_tiles")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Papercut blue palette — shallow (light) -> deep (dark)
PAPERCUT_BLUES = [
    "#E8F4FD", "#B8DCF0", "#7BB8DE", "#4A98C9",
    "#2574A9", "#1A5276", "#0E3D5C", "#071E2E",
]

# MN bounding box (lat/lon)
MN_BOUNDS = [-97.5, 43.0, -89.0, 49.5]
MN_CENTER = [-94.0, 46.0]

# Tile size quality thresholds
MAX_TILE_SIZE_KB = 500  # warn if individual tile > 500 KB
MAX_FEATURES_PER_TILE = 10_000

# B2 config
B2_BUCKET = "opencatch-data"
B2_TILES_PREFIX = "tiles/contours"


@dataclass
class TileStats:
    """Tile generation statistics."""
    total_features: int = 0
    output_size_mb: float = 0.0
    tile_count: int = 0
    min_zoom: int = 0
    max_zoom: int = 0
    generation_time_s: float = 0.0
    oversized_tiles: int = 0
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class ContourTilePipeline:
    """
    Convert bathymetry contour GeoJSON to vector tiles for mobile map display.

    Output formats:
    1. PMTiles (preferred) - single file, static hosting on B2/S3/CDN
    2. MBTiles - SQLite-based, good for offline/embedded
    3. Directory of .pbf tiles - traditional z/x/y structure

    Uses tippecanoe (Mapbox/Felt) for tile generation.
    """

    def __init__(
        self,
        min_zoom: int = 5,
        max_zoom: int = 16,
        bounds: Optional[List[float]] = None,
    ):
        self.min_zoom = min_zoom
        self.max_zoom = max_zoom
        self.bounds = bounds or MN_BOUNDS
        self._has_tippecanoe = self._check_tippecanoe()
        self._has_pmtiles_cli = self._check_pmtiles_cli()

    # ------------------------------------------------------------------
    # Dependency checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_tippecanoe() -> bool:
        """Check if tippecanoe is installed."""
        try:
            result = subprocess.run(
                ["tippecanoe", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            version = result.stderr.strip() or result.stdout.strip()
            log.info(f"tippecanoe found: {version}")
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.warning(
                "tippecanoe not found. Install it for best performance:\n"
                "  macOS:  brew install tippecanoe\n"
                "  Linux:  sudo apt-get install tippecanoe\n"
                "  Build:  https://github.com/felt/tippecanoe"
            )
            return False

    @staticmethod
    def _check_pmtiles_cli() -> bool:
        """Check if pmtiles CLI is available (for mbtiles -> pmtiles conversion)."""
        try:
            subprocess.run(
                ["pmtiles", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    # ------------------------------------------------------------------
    # GeoJSON preparation
    # ------------------------------------------------------------------

    def prepare_geojson(
        self,
        contour_path: Path,
        labels_path: Optional[Path],
        output_path: Path,
    ) -> Path:
        """Prepare GeoJSON for tiling.

        - Simplify geometries based on zoom level
        - Merge small polygons
        - Add zoom-dependent visibility properties
        - Split into layers: contour_fills, contour_lines, depth_labels

        Returns the path to the prepared GeoJSON.
        """
        import geopandas as gpd
        from shapely.validation import make_valid

        log.info(f"Preparing GeoJSON: {contour_path}")
        t0 = time.time()

        gdf = gpd.read_file(contour_path)
        log.info(f"  Loaded {len(gdf)} features")

        if len(gdf) == 0:
            raise ValueError(f"No features found in {contour_path}")

        # Validate geometries
        invalid = ~gdf.geometry.is_valid
        if invalid.any():
            log.info(f"  Fixing {invalid.sum()} invalid geometries")
            gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].apply(make_valid)

        geom_types = gdf.geometry.geom_type.fillna("")
        frames = []

        polygon_mask = geom_types.str.contains("Polygon")
        if polygon_mask.any():
            fills = gdf.loc[polygon_mask].copy()
            fills["_layer"] = fills.get("_layer", "contour_fills")

            fills_proj = fills.to_crs(epsg=3857)
            fills["_area_m2"] = fills_proj.geometry.area
            fills["_minzoom"] = 5
            fills.loc[fills["_area_m2"] < 500, "_minzoom"] = 14
            fills.loc[(fills["_area_m2"] >= 500) & (fills["_area_m2"] < 5000), "_minzoom"] = 12
            fills.loc[(fills["_area_m2"] >= 5000) & (fills["_area_m2"] < 50000), "_minzoom"] = 9
            fills.loc[fills["_area_m2"] >= 50000, "_minzoom"] = 5
            fills.loc[fills["_area_m2"] > 100_000, "geometry"] = (
                fills.loc[fills["_area_m2"] > 100_000, "geometry"].simplify(0.00005)
            )
            fills = fills.drop(columns=[c for c in ["_area_m2"] if c in fills.columns])
            frames.append(fills)

            lines_gdf = fills.copy()
            lines_gdf["geometry"] = lines_gdf.geometry.boundary
            lines_gdf["_layer"] = "contour_lines"
            frames.append(lines_gdf)

        line_mask = geom_types.str.contains("LineString")
        if line_mask.any():
            lines = gdf.loc[line_mask].copy()
            lines["_layer"] = lines.get("_layer", "contour_lines")
            lines_proj = lines.to_crs(epsg=3857)
            lines["_length_m"] = lines_proj.geometry.length
            lines["_minzoom"] = 5
            lines.loc[lines["_length_m"] < 50, "_minzoom"] = 14
            lines.loc[(lines["_length_m"] >= 50) & (lines["_length_m"] < 250), "_minzoom"] = 12
            lines.loc[(lines["_length_m"] >= 250) & (lines["_length_m"] < 1000), "_minzoom"] = 9
            lines = lines.drop(columns=[c for c in ["_length_m"] if c in lines.columns])
            frames.append(lines)

        point_mask = geom_types.str.contains("Point")
        if point_mask.any():
            points = gdf.loc[point_mask].copy()
            points["_layer"] = points.get("_layer", "depth_labels")
            if "minzoom" in points.columns:
                points["_minzoom"] = points["minzoom"]
            else:
                points["_minzoom"] = 10
            frames.append(points)

        if not frames:
            raise ValueError(f"Unsupported geometry types in {contour_path}: {sorted(set(geom_types))}")

        combined = gpd.GeoDataFrame(
            __import__("pandas").concat(frames, ignore_index=True),
            crs=gdf.crs,
        )

        # Add label points if provided
        if labels_path and labels_path.exists():
            labels_gdf = gpd.read_file(labels_path)
            labels_gdf["_layer"] = "depth_labels"
            labels_gdf["_minzoom"] = labels_gdf.get("minzoom", 10)
            combined = gpd.GeoDataFrame(
                __import__("pandas").concat([combined, labels_gdf], ignore_index=True),
                crs=gdf.crs,
            )
            log.info(f"  Added {len(labels_gdf)} depth labels")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_file(output_path, driver="GeoJSON")
        elapsed = time.time() - t0
        log.info(
            f"  Prepared {len(combined)} features -> {output_path} "
            f"({output_path.stat().st_size / 1e6:.1f} MB, {elapsed:.1f}s)"
        )
        return output_path

    # ------------------------------------------------------------------
    # Tile generation — tippecanoe
    # ------------------------------------------------------------------

    def generate_tiles(
        self,
        geojson_path: Path,
        output_path: Path,
        fmt: str = "pmtiles",
    ) -> TileStats:
        """Generate vector tiles using tippecanoe.

        Args:
            geojson_path: Input GeoJSON (prepared or raw).
            output_path: Destination file (.pmtiles, .mbtiles, or directory).
            fmt: One of "pmtiles", "mbtiles", "directory".

        Returns:
            TileStats with generation metadata.
        """
        if self._has_tippecanoe:
            return self._generate_tippecanoe(geojson_path, output_path, fmt)

        log.warning("tippecanoe unavailable — falling back to Python tiling")
        return self._generate_python_fallback(geojson_path, output_path, fmt)

    def _generate_tippecanoe(
        self,
        geojson_path: Path,
        output_path: Path,
        fmt: str,
    ) -> TileStats:
        """Run tippecanoe to create vector tiles."""
        t0 = time.time()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # tippecanoe natively outputs pmtiles or mbtiles
        actual_fmt = fmt
        if fmt == "directory":
            # tippecanoe can output directory with -e
            pass

        cmd = [
            "tippecanoe",
            f"--minimum-zoom={self.min_zoom}",
            f"--maximum-zoom={self.max_zoom}",
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            "--simplification=10",
            "--detect-shared-borders",
            "--no-tile-compression",  # let CDN handle compression
            "--force",
            "--layer=contours",
            f"--name=OpenCatch Contours",
            f"--description=Bathymetry contour tiles for OpenCatch",
            f"--attribution=OpenCatch",
        ]

        # Layer splitting: use tippecanoe-layer property if present
        cmd.append("--read-parallel")

        if fmt == "directory":
            cmd.extend(["-e", str(output_path)])
        elif fmt == "pmtiles":
            cmd.extend(["-o", str(output_path)])
            if not str(output_path).endswith(".pmtiles"):
                log.warning("Output path does not end with .pmtiles")
        else:
            cmd.extend(["-o", str(output_path)])

        cmd.append(str(geojson_path))

        log.info(f"Running tippecanoe: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600,
        )

        if result.returncode != 0:
            log.error(f"tippecanoe failed:\n{result.stderr}")
            raise RuntimeError(f"tippecanoe failed with exit code {result.returncode}")

        elapsed = time.time() - t0
        stats = TileStats(generation_time_s=elapsed, min_zoom=self.min_zoom, max_zoom=self.max_zoom)

        if output_path.exists():
            if output_path.is_file():
                stats.output_size_mb = output_path.stat().st_size / 1e6
            else:
                total = sum(f.stat().st_size for f in output_path.rglob("*") if f.is_file())
                stats.output_size_mb = total / 1e6

        log.info(
            f"Tiles generated: {stats.output_size_mb:.1f} MB in {elapsed:.1f}s "
            f"(zoom {self.min_zoom}-{self.max_zoom})"
        )

        # Quality checks
        stats = self._check_tile_quality(output_path, fmt, stats)
        return stats

    def _generate_python_fallback(
        self,
        geojson_path: Path,
        output_path: Path,
        fmt: str,
    ) -> TileStats:
        """Minimal Python-based tiling fallback (limited quality).

        Produces a single-tile-per-zoom approach using geojson-vt logic.
        For production, install tippecanoe.
        """
        log.warning(
            "Python fallback produces lower-quality tiles than tippecanoe. "
            "Install tippecanoe for production use."
        )
        t0 = time.time()

        try:
            from vt2pbf import vt2pbf  # type: ignore
            import geojson_vt  # type: ignore
        except ImportError:
            log.error(
                "Python fallback requires: pip install geojson-vt vt2pbf\n"
                "Or install tippecanoe for production tiling."
            )
            raise RuntimeError("Neither tippecanoe nor Python tiling libs available")

        with open(geojson_path) as f:
            data = json.load(f)

        tile_index = geojson_vt.geojsonvt(
            data,
            {
                "maxZoom": self.max_zoom,
                "indexMaxZoom": self.max_zoom,
                "indexMaxPoints": 0,
            },
        )

        output_path.mkdir(parents=True, exist_ok=True)
        tile_count = 0

        for z in range(self.min_zoom, self.max_zoom + 1):
            n = 1 << z
            for x in range(n):
                for y in range(n):
                    tile = tile_index.get_tile(z, x, y)
                    if tile and tile.get("features"):
                        tile_dir = output_path / str(z) / str(x)
                        tile_dir.mkdir(parents=True, exist_ok=True)
                        pbf = vt2pbf(tile)
                        (tile_dir / f"{y}.pbf").write_bytes(pbf)
                        tile_count += 1

            log.info(f"  Zoom {z}: generated tiles")

        elapsed = time.time() - t0
        total_bytes = sum(f.stat().st_size for f in output_path.rglob("*.pbf"))
        stats = TileStats(
            tile_count=tile_count,
            output_size_mb=total_bytes / 1e6,
            min_zoom=self.min_zoom,
            max_zoom=self.max_zoom,
            generation_time_s=elapsed,
        )
        log.info(f"Python fallback: {tile_count} tiles, {stats.output_size_mb:.1f} MB in {elapsed:.1f}s")
        return stats

    # ------------------------------------------------------------------
    # Quality checks
    # ------------------------------------------------------------------

    def _check_tile_quality(self, output_path: Path, fmt: str, stats: TileStats) -> TileStats:
        """Check tile sizes and feature counts for quality issues."""
        if fmt == "directory" and output_path.is_dir():
            for pbf in output_path.rglob("*.pbf"):
                size_kb = pbf.stat().st_size / 1024
                if size_kb > MAX_TILE_SIZE_KB:
                    stats.oversized_tiles += 1
                stats.tile_count += 1

            if stats.oversized_tiles:
                msg = f"{stats.oversized_tiles} tiles exceed {MAX_TILE_SIZE_KB} KB — may cause slow rendering"
                stats.warnings.append(msg)
                log.warning(msg)
        elif fmt in ("pmtiles", "mbtiles") and output_path.is_file():
            # For packaged formats, check overall size as heuristic
            size_mb = output_path.stat().st_size / 1e6
            if size_mb > 500:
                msg = f"Tile archive is {size_mb:.0f} MB — consider reducing max-zoom or simplification"
                stats.warnings.append(msg)
                log.warning(msg)

        return stats

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_to_b2(self, tiles_path: Path, bucket: str = B2_BUCKET) -> str:
        """Upload tiles to Backblaze B2 for CDN serving.

        Returns the public URL for the uploaded tiles.
        """
        from b2sdk.v2 import B2Api, InMemoryAccountInfo

        info = InMemoryAccountInfo()
        api = B2Api(info)

        key_id = os.environ.get("B2_KEY_ID", "")
        app_key = os.environ.get("B2_APP_KEY", "")
        if not key_id or not app_key:
            raise RuntimeError("Set B2_KEY_ID and B2_APP_KEY environment variables")

        api.authorize_account("production", key_id, app_key)
        b2_bucket = api.get_bucket_by_name(bucket)

        b2_key = f"{B2_TILES_PREFIX}/{tiles_path.name}"
        size_mb = tiles_path.stat().st_size / 1e6
        log.info(f"Uploading {tiles_path.name} ({size_mb:.1f} MB) to b2://{bucket}/{b2_key}")

        b2_bucket.upload_local_file(
            local_file=str(tiles_path),
            file_name=b2_key,
            content_type="application/octet-stream",
        )

        url = f"https://f004.backblazeb2.com/file/{bucket}/{b2_key}"
        log.info(f"Uploaded: {url}")
        return url

    # ------------------------------------------------------------------
    # Style + metadata generation
    # ------------------------------------------------------------------

    def generate_style(self, tiles_url: str, output_path: Path) -> Path:
        """Generate MapLibre style JSON for the contour tiles.

        Includes filled polygon layers with PAPERCUT_BLUES colors,
        contour line layers, depth label layers, and a draft safety overlay.
        """
        style: Dict[str, Any] = {
            "version": 8,
            "name": "OpenCatch Bathymetry Contours",
            "sources": {
                "contours": {
                    "type": "vector",
                    "url": tiles_url if tiles_url.endswith(".json") else f"pmtiles://{tiles_url}",
                },
            },
            "layers": [
                # Filled depth-band polygons
                {
                    "id": "contour-fills",
                    "type": "fill",
                    "source": "contours",
                    "source-layer": "contour_fills",
                    "paint": {
                        "fill-color": [
                            "match", ["get", "band_index"],
                            *[v for i, c in enumerate(PAPERCUT_BLUES) for v in (i, c)],
                            PAPERCUT_BLUES[-1],
                        ],
                        "fill-opacity": 0.75,
                    },
                },
                # Contour lines at depth transitions
                {
                    "id": "contour-lines",
                    "type": "line",
                    "source": "contours",
                    "source-layer": "contour_lines",
                    "paint": {
                        "line-color": "#1A5276",
                        "line-width": 0.5,
                        "line-opacity": 0.6,
                    },
                },
                # Depth labels
                {
                    "id": "depth-labels",
                    "type": "symbol",
                    "source": "contours",
                    "source-layer": "depth_labels",
                    "minzoom": 10,
                    "layout": {
                        "text-field": ["concat", ["get", "depth_ft"], " ft"],
                        "text-size": 11,
                        "text-font": ["Open Sans Regular"],
                        "text-allow-overlap": False,
                    },
                    "paint": {
                        "text-color": "#0E3D5C",
                        "text-halo-color": "#FFFFFF",
                        "text-halo-width": 1.5,
                    },
                },
                # Draft / safety overlay (togglable in app)
                {
                    "id": "draft-safety-overlay",
                    "type": "fill",
                    "source": "contours",
                    "source-layer": "contour_fills",
                    "filter": ["==", ["get", "is_draft"], True],
                    "paint": {
                        "fill-pattern": "hatching",
                        "fill-opacity": 0.15,
                    },
                    "layout": {"visibility": "none"},
                },
            ],
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(style, f, indent=2)
        log.info(f"Style JSON written: {output_path}")
        return output_path

    def generate_tilejson(self, tiles_url: str, output_path: Path) -> Path:
        """Generate TileJSON 3.0 metadata for the app."""
        tilejson: Dict[str, Any] = {
            "tilejson": "3.0.0",
            "name": "OpenCatch Bathymetry Contours",
            "description": "Filled depth-band contour polygons for inland lakes",
            "version": "1.0.0",
            "scheme": "xyz",
            "tiles": [f"{tiles_url}/{{z}}/{{x}}/{{y}}.pbf"],
            "minzoom": self.min_zoom,
            "maxzoom": self.max_zoom,
            "bounds": self.bounds,
            "center": [*MN_CENTER, 10],
            "vector_layers": [
                {
                    "id": "contour_fills",
                    "description": "Filled depth-band polygons",
                    "fields": {
                        "depth_min_m": "Number",
                        "depth_max_m": "Number",
                        "depth_min_ft": "Number",
                        "depth_max_ft": "Number",
                        "band_index": "Number",
                        "color": "String",
                        "lake_id": "String",
                        "is_draft": "Boolean",
                    },
                },
                {
                    "id": "contour_lines",
                    "description": "Contour lines at depth transitions",
                    "fields": {
                        "depth_m": "Number",
                        "depth_ft": "Number",
                    },
                },
                {
                    "id": "depth_labels",
                    "description": "Depth label points",
                    "fields": {
                        "depth_m": "Number",
                        "depth_ft": "Number",
                        "label": "String",
                    },
                },
            ],
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(tilejson, f, indent=2)
        log.info(f"TileJSON written: {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run(
        self,
        input_path: Path,
        output_path: Path,
        fmt: str = "pmtiles",
        labels_path: Optional[Path] = None,
        upload_b2: bool = False,
        generate_metadata: bool = True,
    ) -> TileStats:
        """Run the full contour tile pipeline.

        1. Prepare GeoJSON (simplify, add layers)
        2. Generate vector tiles
        3. Optionally upload to B2
        4. Generate style + TileJSON metadata
        """
        log.info("=" * 60)
        log.info("OpenCatch Contour Tile Pipeline")
        log.info(f"  Input:  {input_path}")
        log.info(f"  Output: {output_path}")
        log.info(f"  Format: {fmt}")
        log.info(f"  Zoom:   {self.min_zoom}-{self.max_zoom}")
        log.info("=" * 60)

        # Step 1 — Prepare
        with tempfile.TemporaryDirectory(prefix="contour_tiles_") as tmpdir:
            prepared = Path(tmpdir) / "prepared.geojson"
            self.prepare_geojson(input_path, labels_path, prepared)

            # Step 2 — Generate tiles
            stats = self.generate_tiles(prepared, output_path, fmt)

        # Step 3 — Upload
        tiles_url = ""
        if upload_b2:
            if output_path.is_file():
                tiles_url = self.upload_to_b2(output_path)
            else:
                log.warning("B2 upload only supports single-file formats (pmtiles/mbtiles)")

        # Step 4 — Metadata
        if generate_metadata:
            meta_dir = output_path.parent if output_path.is_file() else output_path
            cdn_url = tiles_url or f"https://cdn.opencatch.com/tiles/{output_path.stem}"

            self.generate_style(cdn_url, meta_dir / "contour_style.json")
            self.generate_tilejson(cdn_url, meta_dir / "contour_tilejson.json")

        # Summary
        log.info("=" * 60)
        log.info("Pipeline complete")
        log.info(f"  Output size:  {stats.output_size_mb:.1f} MB")
        log.info(f"  Time:         {stats.generation_time_s:.1f}s")
        if stats.warnings:
            for w in stats.warnings:
                log.warning(f"  Warning: {w}")
        log.info("=" * 60)

        return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch — Convert contour GeoJSON to vector tiles",
    )
    parser.add_argument(
        "--input", "-i", required=True, type=Path,
        help="Input GeoJSON file (contour fills)",
    )
    parser.add_argument(
        "--labels", type=Path, default=None,
        help="Optional GeoJSON file with depth label points",
    )
    parser.add_argument(
        "--output", "-o", required=True, type=Path,
        help="Output path (.pmtiles, .mbtiles, or directory)",
    )
    parser.add_argument(
        "--format", "-f", dest="fmt", default="pmtiles",
        choices=["pmtiles", "mbtiles", "directory"],
        help="Output tile format (default: pmtiles)",
    )
    parser.add_argument("--min-zoom", type=int, default=5, help="Minimum zoom level (default: 5)")
    parser.add_argument("--max-zoom", type=int, default=16, help="Maximum zoom level (default: 16)")
    parser.add_argument(
        "--bounds", type=float, nargs=4, default=None,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Bounding box (default: Minnesota)",
    )
    parser.add_argument("--upload-b2", action="store_true", help="Upload tiles to Backblaze B2")
    parser.add_argument("--no-metadata", action="store_true", help="Skip style/TileJSON generation")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.input.exists():
        log.error(f"Input file not found: {args.input}")
        raise SystemExit(1)

    pipeline = ContourTilePipeline(
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
        bounds=args.bounds,
    )

    stats = pipeline.run(
        input_path=args.input,
        output_path=args.output,
        fmt=args.fmt,
        labels_path=args.labels,
        upload_b2=args.upload_b2,
        generate_metadata=not args.no_metadata,
    )

    # Exit with warning code if quality issues detected
    if stats.warnings:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
