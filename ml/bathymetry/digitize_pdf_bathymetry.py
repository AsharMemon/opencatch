"""
Digitize lake bathymetry from scanned PDF contour maps into GIS vector data.

Many US states (NY 400+ lakes, BC 2,600+ maps, KS, MS, SD, WV, WY) only publish
lake depth maps as PDFs. This tool extracts depth contours and depth-band polygons
programmatically, producing GeoJSON with depth attributes.

Pipeline:
    1. Convert PDF pages to high-res images (300 DPI) via pymupdf/fitz
    2. Detect depth bands by blue color intensity (darker = deeper)
    3. OCR depth labels using easyocr (FULL mode only)
    4. Georeference using lake boundary polygon from HydroLAKES
    5. Output as GeoJSON with depth attributes

Two modes:
    FULL   — OCR + contour detection + georeferencing (best quality)
    SIMPLE — Color intensity bands only (faster, no OCR needed)

Dependencies:
    pip install pymupdf opencv-python easyocr shapely geopandas pyproj

Usage:
    python digitize_pdf_bathymetry.py \\
        --input /data/pdf_maps/ny/ \\
        --lake-polygons /data/shorelines/hydrolakes_na.parquet \\
        --max-depths /data/cross_region/lagos_depth.csv \\
        --output /data/digitized/ny/ \\
        --mode simple
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import fitz  # pymupdf
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.affinity import affine_transform
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# HSV range for blue tones typically used in bathymetric maps
BLUE_HUE_LOW = 85
BLUE_HUE_HIGH = 135
MIN_SATURATION = 30
MIN_CONTOUR_AREA_PX = 200  # ignore tiny noise blobs
DEFAULT_DPI = 300
DEFAULT_NUM_BANDS = 6
OCR_CONFIDENCE_THRESHOLD = 0.3


@dataclass
class QualityReport:
    """Quality metrics for a single digitized lake."""

    lake_id: str
    pdf_file: str
    mode: str
    num_bands: int = 0
    num_contours: int = 0
    ocr_labels_found: int = 0
    ocr_mean_confidence: float = 0.0
    georef_rmse_m: float = float("nan")
    max_depth_source: str = "unknown"
    max_depth_m: float = float("nan")
    image_width_px: int = 0
    image_height_px: int = 0
    blue_pixel_fraction: float = 0.0
    score: float = 0.0

    def compute_score(self) -> float:
        """Composite quality score in [0, 1]."""
        s = 0.0
        # Band count: 4-8 is ideal
        if 4 <= self.num_bands <= 10:
            s += 0.25
        elif self.num_bands > 0:
            s += 0.10
        # Blue coverage: expect 10-80% of image
        if 0.10 <= self.blue_pixel_fraction <= 0.80:
            s += 0.20
        # OCR quality (FULL mode only)
        if self.ocr_labels_found > 0 and self.ocr_mean_confidence > 0.5:
            s += 0.25
        elif self.mode == "simple":
            s += 0.15  # no OCR penalty in simple mode
        # Georef accuracy
        if not np.isnan(self.georef_rmse_m) and self.georef_rmse_m < 50:
            s += 0.15
        elif not np.isnan(self.georef_rmse_m) and self.georef_rmse_m < 200:
            s += 0.08
        # Max depth available
        if not np.isnan(self.max_depth_m):
            s += 0.15
        self.score = round(s, 3)
        return self.score


# ---------------------------------------------------------------------------
# PDF to image conversion
# ---------------------------------------------------------------------------


def pdf_to_image(pdf_path: str, dpi: int = DEFAULT_DPI, page: int = 0) -> np.ndarray:
    """Render a PDF page to a BGR numpy array at the given DPI."""
    doc = fitz.open(pdf_path)
    if page >= len(doc):
        raise ValueError(f"Page {page} does not exist in {pdf_path} ({len(doc)} pages)")
    pg = doc[page]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = pg.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
    # fitz gives RGB, opencv wants BGR
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    doc.close()
    return img_bgr


def select_map_page(pdf_path: str, dpi: int = DEFAULT_DPI) -> int:
    """Heuristic: pick the PDF page with the most blue pixels (the map)."""
    doc = fitz.open(pdf_path)
    best_page, best_blue = 0, 0.0
    for i in range(len(doc)):
        img = pdf_to_image(pdf_path, dpi=150, page=i)
        blue_frac = _blue_pixel_fraction(img)
        if blue_frac > best_blue:
            best_blue = blue_frac
            best_page = i
    doc.close()
    return best_page


def _blue_pixel_fraction(img_bgr: np.ndarray) -> float:
    """Fraction of pixels that fall in the blue hue range."""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([BLUE_HUE_LOW, MIN_SATURATION, 30]),
        np.array([BLUE_HUE_HIGH, 255, 255]),
    )
    return float(np.count_nonzero(mask)) / (img_bgr.shape[0] * img_bgr.shape[1])


# ---------------------------------------------------------------------------
# Color-based depth band extraction (SIMPLE mode core)
# ---------------------------------------------------------------------------


def extract_blue_mask(img_bgr: np.ndarray) -> np.ndarray:
    """Binary mask of all blue-toned pixels."""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([BLUE_HUE_LOW, MIN_SATURATION, 30]),
        np.array([BLUE_HUE_HIGH, 255, 255]),
    )
    # Clean up noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    return mask


def segment_depth_bands(
    img_bgr: np.ndarray,
    num_bands: int = DEFAULT_NUM_BANDS,
) -> list[tuple[np.ndarray, float]]:
    """
    Segment blue regions into depth bands by color intensity.

    Returns list of (binary_mask, relative_depth) tuples, where
    relative_depth is in [0, 1] (0 = shallowest, 1 = deepest).
    Darker blue = deeper water.
    """
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    # Blue mask
    blue_mask = extract_blue_mask(img_bgr)

    # Within blue pixels, compute "depth intensity" from saturation and inverse value.
    # Higher saturation + lower value = darker blue = deeper.
    depth_intensity = np.zeros_like(h, dtype=np.float32)
    blue_pixels = blue_mask > 0
    if not np.any(blue_pixels):
        return []

    sat_norm = s[blue_pixels].astype(np.float32) / 255.0
    val_inv = 1.0 - (v[blue_pixels].astype(np.float32) / 255.0)
    depth_intensity[blue_pixels] = 0.6 * sat_norm + 0.4 * val_inv

    # Quantize into bands
    di_blue = depth_intensity[blue_pixels]
    if len(di_blue) == 0:
        return []

    pmin, pmax = np.percentile(di_blue, [2, 98])
    if pmax - pmin < 0.01:
        pmax = pmin + 0.01

    thresholds = np.linspace(pmin, pmax, num_bands + 1)
    bands = []
    for i in range(num_bands):
        lo, hi = thresholds[i], thresholds[i + 1]
        band_mask = np.zeros_like(blue_mask)
        in_band = blue_pixels & (depth_intensity >= lo)
        if i < num_bands - 1:
            in_band = in_band & (depth_intensity < hi)
        band_mask[in_band] = 255

        # Clean small fragments
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        band_mask = cv2.morphologyEx(band_mask, cv2.MORPH_OPEN, kernel)

        if cv2.countNonZero(band_mask) < MIN_CONTOUR_AREA_PX:
            continue

        rel_depth = (i + 0.5) / num_bands  # center of band
        bands.append((band_mask, rel_depth))

    return bands


def masks_to_polygons(
    masks: list[tuple[np.ndarray, float]],
    simplify_tolerance: float = 2.0,
) -> list[tuple[Polygon | MultiPolygon, float]]:
    """Convert binary masks to shapely polygons in pixel coordinates."""
    results = []
    for mask, rel_depth in masks:
        contours, hierarchy = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        polys = []
        for cnt in contours:
            if cv2.contourArea(cnt) < MIN_CONTOUR_AREA_PX:
                continue
            coords = cnt.squeeze()
            if len(coords.shape) != 2 or coords.shape[0] < 4:
                continue
            # Close the ring
            coords_closed = np.vstack([coords, coords[0]])
            poly = Polygon(coords_closed)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            poly = poly.simplify(simplify_tolerance, preserve_topology=True)
            polys.append(poly)

        if polys:
            merged = unary_union(polys)
            results.append((merged, rel_depth))

    return results


# ---------------------------------------------------------------------------
# OCR depth label extraction (FULL mode)
# ---------------------------------------------------------------------------


def extract_depth_labels(img_bgr: np.ndarray) -> list[dict]:
    """
    Use easyocr to find numeric depth labels on the map.

    Returns list of {text, value_ft, value_m, bbox_center, confidence}.
    """
    try:
        import easyocr
    except ImportError:
        log.warning("easyocr not installed — skipping OCR. Install with: pip install easyocr")
        return []

    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    results = reader.readtext(img_bgr)

    labels = []
    for bbox, text, conf in results:
        if conf < OCR_CONFIDENCE_THRESHOLD:
            continue
        # Try to parse as a number (depth value)
        cleaned = text.strip().replace("'", "").replace('"', "").replace(",", ".")
        # Remove trailing units
        for suffix in ["ft", "m", "feet", "meters", "f", "metre"]:
            if cleaned.lower().endswith(suffix):
                cleaned = cleaned[: -len(suffix)].strip()
                break
        try:
            value = float(cleaned)
        except ValueError:
            continue

        if value <= 0 or value > 500:
            continue  # implausible depth

        # Bbox center in pixel coords
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        cx, cy = np.mean(xs), np.mean(ys)

        labels.append(
            {
                "text": text,
                "value_ft": value,
                "value_m": value * 0.3048,
                "bbox_center": (float(cx), float(cy)),
                "confidence": float(conf),
            }
        )

    log.info(f"OCR found {len(labels)} depth labels")
    return labels


# ---------------------------------------------------------------------------
# Contour line detection (FULL mode)
# ---------------------------------------------------------------------------


def detect_contour_lines(img_bgr: np.ndarray) -> list[np.ndarray]:
    """
    Detect thin contour lines on the map via edge detection.

    Returns list of contour arrays in pixel coordinates.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    # Adaptive threshold to isolate thin lines
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, 30, 100)

    # Focus on blue-region edges
    blue_mask = extract_blue_mask(img_bgr)
    # Dilate blue mask slightly so edges near boundaries are kept
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    blue_dilated = cv2.dilate(blue_mask, kernel, iterations=2)
    edges = cv2.bitwise_and(edges, blue_dilated)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    # Filter: keep only contours that look like depth lines (elongated, reasonable size)
    filtered = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        perimeter = cv2.arcLength(cnt, closed=False)
        if perimeter < 30:
            continue
        if area > 0 and perimeter > 0:
            circularity = 4 * np.pi * area / (perimeter * perimeter)
            # Contour lines are typically non-circular
            if circularity > 0.7:
                continue
        filtered.append(cnt)

    log.info(f"Detected {len(filtered)} contour line candidates")
    return filtered


# ---------------------------------------------------------------------------
# Georeferencing
# ---------------------------------------------------------------------------


def georeference_polygons(
    pixel_polygons: list[tuple[Polygon | MultiPolygon, float]],
    img_shape: tuple[int, int],
    lake_boundary: Polygon | MultiPolygon,
) -> list[tuple[Polygon | MultiPolygon, float]]:
    """
    Transform pixel-coordinate polygons to geographic coordinates using
    the lake boundary as reference.

    Strategy: fit the bounding box of all pixel polygons to the bounding
    box of the lake boundary polygon (affine transform).
    """
    if not pixel_polygons:
        return []

    # Bounding box of all pixel polygons
    all_geoms = [p for p, _ in pixel_polygons]
    combined = unary_union(all_geoms)
    px_minx, px_miny, px_maxx, px_maxy = combined.bounds

    # Lake boundary bbox in geographic coords
    lk_minx, lk_miny, lk_maxx, lk_maxy = lake_boundary.bounds

    # Scale factors
    px_w = px_maxx - px_minx
    px_h = px_maxy - px_miny
    if px_w < 1 or px_h < 1:
        log.warning("Pixel polygon extent too small for georeferencing")
        return pixel_polygons

    lk_w = lk_maxx - lk_minx
    lk_h = lk_maxy - lk_miny
    sx = lk_w / px_w
    sy = lk_h / px_h

    # Affine: translate to origin, scale, translate to lake coords.
    # Note: pixel Y is inverted (top=0), geographic Y increases upward.
    georef = []
    for poly, depth in pixel_polygons:
        transformed = affine_transform(
            poly,
            [sx, 0, 0, -sy, lk_minx - sx * px_minx, lk_maxy + sy * px_miny],
        )
        # Clip to lake boundary
        clipped = transformed.intersection(lake_boundary)
        if clipped.is_empty:
            continue
        georef.append((clipped, depth))

    return georef


def compute_georef_rmse(
    pixel_polygons: list[tuple[Polygon | MultiPolygon, float]],
    georef_polygons: list[tuple[Polygon | MultiPolygon, float]],
    lake_boundary: Polygon | MultiPolygon,
) -> float:
    """Estimate georeferencing error as fraction of lake extent (in meters approx)."""
    if not georef_polygons:
        return float("nan")
    geo_union = unary_union([p for p, _ in georef_polygons])
    lake_area = lake_boundary.area
    geo_area = geo_union.area
    if lake_area == 0:
        return float("nan")
    # Area ratio as proxy — perfect georef would cover a fraction of the lake
    ratio = geo_area / lake_area
    # Convert to approximate meters (rough, assuming ~0.00001 deg ~ 1m)
    extent_m = np.sqrt(lake_area) * 111_000
    rmse_est = abs(1.0 - ratio) * extent_m * 0.1
    return float(round(rmse_est, 2))


# ---------------------------------------------------------------------------
# Lake polygon lookup
# ---------------------------------------------------------------------------


def load_lake_polygons(path: str) -> gpd.GeoDataFrame:
    """Load lake boundary polygons from parquet, gpkg, or shapefile."""
    p = Path(path)
    if p.suffix == ".parquet":
        return gpd.read_parquet(path)
    elif p.suffix in (".gpkg", ".shp", ".geojson"):
        return gpd.read_file(path)
    else:
        raise ValueError(f"Unsupported lake polygon format: {p.suffix}")


def find_lake_polygon(
    lake_name: str,
    lake_gdf: gpd.GeoDataFrame,
) -> Optional[Polygon | MultiPolygon]:
    """
    Fuzzy-match a lake name to the polygon dataset.

    Tries exact match first, then case-insensitive substring match.
    """
    name_cols = [c for c in lake_gdf.columns if "name" in c.lower() or "lake" in c.lower()]
    if not name_cols:
        log.warning("No name column found in lake polygons")
        return None

    name_col = name_cols[0]
    clean_name = lake_name.lower().strip()

    # Exact match
    exact = lake_gdf[lake_gdf[name_col].str.lower().str.strip() == clean_name]
    if len(exact) > 0:
        return exact.iloc[0].geometry

    # Substring match
    substr = lake_gdf[lake_gdf[name_col].str.lower().str.contains(clean_name, na=False)]
    if len(substr) > 0:
        # Pick largest by area
        areas = substr.geometry.area
        return substr.iloc[areas.argmax()].geometry

    return None


def lake_name_from_pdf(pdf_path: str) -> str:
    """Extract a lake name from the PDF filename."""
    stem = Path(pdf_path).stem
    # Common patterns: "LakeName_bathymetry", "lakename-contour-map", etc.
    name = stem.replace("_", " ").replace("-", " ")
    # Remove common suffixes
    for suffix in ["bathymetry", "contour", "map", "depth", "bathy", "survey", "lake"]:
        name = name.lower().replace(suffix, "").strip()
    return name.title().strip()


# ---------------------------------------------------------------------------
# Max depth lookup
# ---------------------------------------------------------------------------


def load_max_depths(path: str) -> pd.DataFrame:
    """Load max depth CSV (expects columns: lake_name or lake_id, max_depth_m)."""
    df = pd.read_csv(path)
    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def lookup_max_depth(
    lake_name: str,
    max_depths_df: Optional[pd.DataFrame],
) -> Optional[float]:
    """Look up the maximum depth for a lake."""
    if max_depths_df is None:
        return None

    name_col = None
    for c in max_depths_df.columns:
        if "name" in c or "lake" in c:
            name_col = c
            break
    if name_col is None:
        return None

    depth_col = None
    for c in max_depths_df.columns:
        if "depth" in c and "max" in c:
            depth_col = c
            break
    if depth_col is None:
        for c in max_depths_df.columns:
            if "depth" in c:
                depth_col = c
                break
    if depth_col is None:
        return None

    clean = lake_name.lower().strip()
    match = max_depths_df[max_depths_df[name_col].astype(str).str.lower().str.strip() == clean]
    if len(match) > 0:
        val = match.iloc[0][depth_col]
        if pd.notna(val):
            return float(val)
    return None


# ---------------------------------------------------------------------------
# GeoJSON output
# ---------------------------------------------------------------------------


def build_geojson(
    georef_bands: list[tuple[Polygon | MultiPolygon, float]],
    max_depth_m: Optional[float],
    lake_name: str,
    mode: str,
    quality: QualityReport,
) -> dict:
    """Build a GeoJSON FeatureCollection from georeferenced depth bands."""
    features = []
    for geom, rel_depth in georef_bands:
        abs_depth = round(rel_depth * max_depth_m, 2) if max_depth_m else None
        props = {
            "lake_name": lake_name,
            "relative_depth": round(rel_depth, 4),
            "depth_m": abs_depth,
            "depth_ft": round(abs_depth * 3.28084, 2) if abs_depth else None,
            "mode": mode,
            "quality_score": quality.score,
        }
        feat = {
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": props,
        }
        features.append(feat)

    return {
        "type": "FeatureCollection",
        "properties": {
            "lake_name": lake_name,
            "mode": mode,
            "max_depth_m": max_depth_m,
            "quality_score": quality.score,
            "num_bands": len(georef_bands),
        },
        "features": features,
    }


# ---------------------------------------------------------------------------
# Single-lake processing
# ---------------------------------------------------------------------------


def process_single_pdf(
    pdf_path: str,
    lake_gdf: Optional[gpd.GeoDataFrame] = None,
    max_depths_df: Optional[pd.DataFrame] = None,
    mode: str = "simple",
    num_bands: int = DEFAULT_NUM_BANDS,
    dpi: int = DEFAULT_DPI,
    output_path: Optional[str] = None,
) -> tuple[dict, QualityReport]:
    """
    Process a single PDF bathymetric map.

    Args:
        pdf_path: Path to the PDF file.
        lake_gdf: GeoDataFrame with lake boundary polygons.
        max_depths_df: DataFrame with max depth values.
        mode: "simple" or "full".
        num_bands: Number of depth bands to extract.
        dpi: Rendering resolution.
        output_path: Where to write GeoJSON output.

    Returns:
        (geojson_dict, quality_report)
    """
    lake_name = lake_name_from_pdf(pdf_path)
    log.info(f"Processing: {lake_name} ({pdf_path}) mode={mode}")

    quality = QualityReport(
        lake_id=lake_name.lower().replace(" ", "_"),
        pdf_file=os.path.basename(pdf_path),
        mode=mode,
    )

    # Step 1: PDF to image
    page_idx = select_map_page(pdf_path, dpi=150) if mode == "full" else 0
    img = pdf_to_image(pdf_path, dpi=dpi, page=page_idx)
    quality.image_height_px = img.shape[0]
    quality.image_width_px = img.shape[1]
    quality.blue_pixel_fraction = round(_blue_pixel_fraction(img), 4)

    if quality.blue_pixel_fraction < 0.02:
        log.warning(f"Very low blue content ({quality.blue_pixel_fraction:.1%}) — may not be a bathymetric map")

    # Step 2: Extract depth bands
    bands = segment_depth_bands(img, num_bands=num_bands)
    quality.num_bands = len(bands)
    log.info(f"Extracted {len(bands)} depth bands")

    # Step 2b: Convert masks to polygons
    pixel_polys = masks_to_polygons(bands)

    # Step 3: OCR (FULL mode)
    ocr_labels = []
    if mode == "full":
        ocr_labels = extract_depth_labels(img)
        quality.ocr_labels_found = len(ocr_labels)
        if ocr_labels:
            quality.ocr_mean_confidence = round(
                np.mean([lb["confidence"] for lb in ocr_labels]), 3
            )
        # Also detect contour lines
        contour_lines = detect_contour_lines(img)
        quality.num_contours = len(contour_lines)

    # Step 4: Max depth lookup
    max_depth = lookup_max_depth(lake_name, max_depths_df)
    if max_depth is not None:
        quality.max_depth_m = max_depth
        quality.max_depth_source = "lagos_lookup"
    elif ocr_labels:
        # Use max OCR label as fallback
        max_label = max(ocr_labels, key=lambda x: x["value_m"])
        max_depth = max_label["value_m"]
        quality.max_depth_m = max_depth
        quality.max_depth_source = "ocr"

    # Step 5: Georeference
    lake_poly = None
    if lake_gdf is not None:
        lake_poly = find_lake_polygon(lake_name, lake_gdf)
        if lake_poly is None:
            log.warning(f"No lake polygon found for '{lake_name}' — output in pixel coords")

    if lake_poly is not None:
        georef_polys = georeference_polygons(pixel_polys, img.shape[:2], lake_poly)
        quality.georef_rmse_m = compute_georef_rmse(pixel_polys, georef_polys, lake_poly)
    else:
        georef_polys = pixel_polys

    # Build output
    quality.compute_score()
    geojson = build_geojson(georef_polys, max_depth, lake_name, mode, quality)

    # Write output
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(geojson, f, indent=2)
        log.info(f"Wrote {output_path} (quality={quality.score:.2f})")

    return geojson, quality


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


def batch_process(
    input_dir: str,
    lake_polygons_path: Optional[str] = None,
    max_depths_path: Optional[str] = None,
    output_dir: str = "./digitized",
    mode: str = "simple",
    num_bands: int = DEFAULT_NUM_BANDS,
    dpi: int = DEFAULT_DPI,
    min_quality: float = 0.0,
) -> pd.DataFrame:
    """
    Batch process a directory of PDF bathymetric maps.

    Args:
        input_dir: Directory containing PDF files.
        lake_polygons_path: Path to lake boundary polygons.
        max_depths_path: Path to max depth CSV.
        output_dir: Where to write GeoJSON outputs.
        mode: "simple" or "full".
        num_bands: Number of depth bands.
        dpi: Rendering resolution.
        min_quality: Minimum quality score to keep output.

    Returns:
        DataFrame with quality reports for all processed lakes.
    """
    pdf_files = sorted(Path(input_dir).glob("*.pdf"))
    if not pdf_files:
        # Also check for uppercase
        pdf_files = sorted(Path(input_dir).glob("*.PDF"))
    log.info(f"Found {len(pdf_files)} PDF files in {input_dir}")

    if not pdf_files:
        log.warning(f"No PDF files found in {input_dir}")
        return pd.DataFrame()

    # Load reference data
    lake_gdf = load_lake_polygons(lake_polygons_path) if lake_polygons_path else None
    max_depths_df = load_max_depths(max_depths_path) if max_depths_path else None

    os.makedirs(output_dir, exist_ok=True)
    reports = []

    for i, pdf_path in enumerate(pdf_files):
        log.info(f"[{i + 1}/{len(pdf_files)}] {pdf_path.name}")
        try:
            out_name = pdf_path.stem + ".geojson"
            out_path = os.path.join(output_dir, out_name)
            _, quality = process_single_pdf(
                str(pdf_path),
                lake_gdf=lake_gdf,
                max_depths_df=max_depths_df,
                mode=mode,
                num_bands=num_bands,
                dpi=dpi,
                output_path=out_path,
            )
            if quality.score < min_quality:
                log.info(
                    f"  Removing low-quality output ({quality.score:.2f} < {min_quality})"
                )
                os.remove(out_path)
            reports.append(quality.__dict__)
        except Exception as e:
            log.error(f"  Failed: {e}")
            reports.append(
                {
                    "lake_id": pdf_path.stem,
                    "pdf_file": pdf_path.name,
                    "mode": mode,
                    "score": 0.0,
                    "error": str(e),
                }
            )

    report_df = pd.DataFrame(reports)

    # Save summary report
    report_path = os.path.join(output_dir, "_quality_report.csv")
    report_df.to_csv(report_path, index=False)
    log.info(f"Quality report saved to {report_path}")

    # Summary stats
    if "score" in report_df.columns:
        scores = report_df["score"].dropna()
        log.info(
            f"Batch complete: {len(pdf_files)} files, "
            f"mean quality={scores.mean():.3f}, "
            f"median={scores.median():.3f}, "
            f"passed={int((scores >= min_quality).sum())}/{len(scores)}"
        )

    return report_df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Digitize lake bathymetry from scanned PDF contour maps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Batch process NY lake PDFs (simple mode)
  python digitize_pdf_bathymetry.py \\
      --input /data/pdf_maps/ny/ \\
      --lake-polygons /data/shorelines/hydrolakes_na.parquet \\
      --max-depths /data/cross_region/lagos_depth.csv \\
      --output /data/digitized/ny/ \\
      --mode simple

  # Single PDF with full OCR
  python digitize_pdf_bathymetry.py \\
      --input /data/pdf_maps/cayuga_lake.pdf \\
      --lake-polygons /data/shorelines/hydrolakes_na.parquet \\
      --output /data/digitized/cayuga.geojson \\
      --mode full

  # Quick test without georeferencing
  python digitize_pdf_bathymetry.py \\
      --input /data/pdf_maps/test.pdf \\
      --output /data/digitized/test.geojson \\
      --mode simple --num-bands 8
        """,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a single PDF or directory of PDFs",
    )
    parser.add_argument(
        "--lake-polygons",
        default=None,
        help="Lake boundary polygons (parquet, gpkg, shp, geojson)",
    )
    parser.add_argument(
        "--max-depths",
        default=None,
        help="CSV with lake max depths (lake_name, max_depth_m)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output GeoJSON file or directory",
    )
    parser.add_argument(
        "--mode",
        choices=["simple", "full"],
        default="simple",
        help="Processing mode: simple (color bands only) or full (OCR + contours)",
    )
    parser.add_argument(
        "--num-bands",
        type=int,
        default=DEFAULT_NUM_BANDS,
        help=f"Number of depth bands to extract (default: {DEFAULT_NUM_BANDS})",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"PDF rendering DPI (default: {DEFAULT_DPI})",
    )
    parser.add_argument(
        "--min-quality",
        type=float,
        default=0.0,
        help="Minimum quality score to keep output (0-1, default: 0)",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)

    if input_path.is_dir():
        # Batch mode
        batch_process(
            input_dir=str(input_path),
            lake_polygons_path=args.lake_polygons,
            max_depths_path=args.max_depths,
            output_dir=args.output,
            mode=args.mode,
            num_bands=args.num_bands,
            dpi=args.dpi,
            min_quality=args.min_quality,
        )
    elif input_path.is_file() and input_path.suffix.lower() == ".pdf":
        # Single file mode
        lake_gdf = load_lake_polygons(args.lake_polygons) if args.lake_polygons else None
        max_depths_df = load_max_depths(args.max_depths) if args.max_depths else None
        geojson, quality = process_single_pdf(
            str(input_path),
            lake_gdf=lake_gdf,
            max_depths_df=max_depths_df,
            mode=args.mode,
            num_bands=args.num_bands,
            dpi=args.dpi,
            output_path=args.output,
        )
        log.info(f"Quality score: {quality.score}")
    else:
        log.error(f"Input must be a PDF file or directory: {args.input}")
        sys.exit(1)


if __name__ == "__main__":
    main()
