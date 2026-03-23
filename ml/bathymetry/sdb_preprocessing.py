#!/usr/bin/env python3
"""
OpenCatch — Satellite-Derived Bathymetry (SDB) Preprocessing Pipeline

Transforms raw Sentinel-2 L2A imagery into physics-based features for
bathymetry estimation. Current approach (raw DN values -> ML) yields 4.65m RMSE.
Literature shows proper preprocessing is critical for sub-1m accuracy.

Pipeline stages:
  1. DN -> reflectance conversion (DN / 10000)
  2. Sun glint removal (Hedley et al. 2005)
  3. Quality filtering (clouds, saturation, land, turbidity)
  4. Water column correction (Beer-Lambert Kd estimation)
  5. Physics-based feature engineering (Stumpf, Lyzenga, indices)
  6. Temporal match scoring (ICESat-2 <-> S2 date proximity)

References:
  - Stumpf et al. (2003): log-ratio bathymetry
  - Lyzenga (1978, 1985): multi-band log-linear depth
  - Hedley et al. (2005): NIR-based sun glint removal
  - Beer-Lambert law: exponential light attenuation in water

Usage:
    from sdb_preprocessing import SDBPreprocessor
    proc = SDBPreprocessor()
    features = proc.process_point(bands_dict, scl_value, s2_date, icesat2_date)

    # Or batch processing
    df = proc.process_dataframe(df_with_raw_bands)
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

log = logging.getLogger("sdb_preprocess")


# ── Constants ────────────────────────────────────────────────────────

# Sentinel-2 L2A scale factor: DN / 10000 = BOA reflectance
S2_SCALE_FACTOR = 10000.0

# Sentinel-2 Scene Classification Layer (SCL) classes
SCL_NO_DATA = 0
SCL_SATURATED_DEFECTIVE = 1
SCL_DARK_AREA = 2
SCL_CLOUD_SHADOW = 3
SCL_VEGETATION = 4
SCL_BARE_SOIL = 5
SCL_WATER = 6
SCL_CLOUD_LOW = 7
SCL_CLOUD_MEDIUM = 8
SCL_CLOUD_HIGH = 9
SCL_THIN_CIRRUS = 10
SCL_SNOW_ICE = 11

# Bad SCL classes for water remote sensing
SCL_BAD = {
    SCL_NO_DATA, SCL_SATURATED_DEFECTIVE,
    SCL_CLOUD_SHADOW, SCL_CLOUD_LOW, SCL_CLOUD_MEDIUM,
    SCL_CLOUD_HIGH, SCL_THIN_CIRRUS, SCL_SNOW_ICE,
}

# Approximate diffuse attenuation coefficients (Kd, m^-1) for pure water
# From Pope & Fry (1997), Smith & Baker (1981)
KD_PURE_WATER = {
    "blue":  0.0196,   # 490 nm
    "green": 0.0640,   # 560 nm
    "red":   0.3490,   # 665 nm
}

# Sentinel-2 band central wavelengths (nm)
S2_WAVELENGTHS = {
    "blue": 490, "green": 560, "red": 665,
    "rededge1": 705, "rededge2": 740, "rededge3": 783,
    "nir": 842, "nir08": 865,
    "swir16": 1610, "swir22": 2190,
}

# Numerical stability
EPS = 1e-8


class SDBPreprocessor:
    """
    Full SDB preprocessing pipeline.

    Converts raw Sentinel-2 digital numbers into physics-based features
    suitable for bathymetry model training. Handles atmospheric effects,
    sun glint, water column correction, and quality filtering.
    """

    def __init__(
        self,
        nir_deep_threshold: float = 0.02,
        saturation_threshold: float = 0.30,
        ndvi_land_threshold: float = 0.20,
        turbidity_rb_threshold: float = 2.0,
        max_sun_zenith: float = 60.0,
        cloud_buffer_pixels: int = 2,
        min_nir_glint_samples: int = 50,
    ):
        """
        Args:
            nir_deep_threshold: NIR reflectance threshold for "deep water" pixels
                used in glint regression. Deep water NIR should be ~0.
            saturation_threshold: Max reflectance in any visible band before
                flagging as saturated.
            ndvi_land_threshold: NDVI above this = land/vegetation, not water.
            turbidity_rb_threshold: Red/Blue ratio above this = extremely turbid.
            max_sun_zenith: Sun zenith angle above this = extreme view geometry.
            cloud_buffer_pixels: Number of pixels to buffer around clouds.
            min_nir_glint_samples: Minimum deep water pixels needed for glint
                regression. If fewer, skip deglinting.
        """
        self.nir_deep_threshold = nir_deep_threshold
        self.saturation_threshold = saturation_threshold
        self.ndvi_land_threshold = ndvi_land_threshold
        self.turbidity_rb_threshold = turbidity_rb_threshold
        self.max_sun_zenith = max_sun_zenith
        self.cloud_buffer_pixels = cloud_buffer_pixels
        self.min_nir_glint_samples = min_nir_glint_samples

        # Glint regression slopes (computed per-scene; cached)
        self._glint_slopes = {}
        self._glint_min_nir = None

    # ── Stage 1: DN -> Reflectance ──────────────────────────────────

    @staticmethod
    def dn_to_reflectance(dn_value: float) -> float:
        """
        Convert Sentinel-2 L2A Digital Number to BOA reflectance.

        S2 L2A products store reflectance as uint16 scaled by 10000.
        e.g., DN=2016 -> reflectance=0.2016

        This is CRITICAL: without this, spectral ratios are meaningless
        because they operate on raw integer scales instead of physical units.
        """
        return dn_value / S2_SCALE_FACTOR

    @staticmethod
    def dn_to_reflectance_array(dn_array: np.ndarray) -> np.ndarray:
        """Vectorized DN -> reflectance for arrays."""
        return dn_array.astype(np.float64) / S2_SCALE_FACTOR

    # ── Stage 2: Sun Glint Removal (Hedley et al. 2005) ────────────

    def estimate_glint_slopes(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        nir: np.ndarray,
    ) -> dict[str, float]:
        """
        Estimate sun glint regression slopes from deep water pixels.

        Hedley et al. (2005) method:
        - Over deep water, NIR should be ~0 (fully absorbed).
        - Any NIR signal over water = sun glint reflected off the surface.
        - For each visible band, regress against NIR over deep water pixels.
        - The slope gives the glint contribution per unit of NIR.

        Args:
            blue, green, red, nir: 1D arrays of reflectance values
                (all pixels in the scene/tile that are water).

        Returns:
            Dict with slopes for each band, e.g. {"blue": 0.8, "green": 0.7, ...}
        """
        # Identify deep water pixels: low NIR = no bottom signal
        deep_mask = nir < self.nir_deep_threshold

        n_deep = np.sum(deep_mask)
        if n_deep < self.min_nir_glint_samples:
            log.debug(
                f"Only {n_deep} deep water pixels (need {self.min_nir_glint_samples}). "
                f"Skipping deglinting."
            )
            return {}

        nir_deep = nir[deep_mask]
        self._glint_min_nir = np.percentile(nir_deep, 5)

        slopes = {}
        for band_name, band_data in [("blue", blue), ("green", green), ("red", red)]:
            band_deep = band_data[deep_mask]

            # Linear regression: band = intercept + slope * NIR
            # We only need the slope
            nir_centered = nir_deep - nir_deep.mean()
            band_centered = band_deep - band_deep.mean()

            nir_var = np.sum(nir_centered ** 2)
            if nir_var < EPS:
                slopes[band_name] = 0.0
                continue

            slope = np.sum(nir_centered * band_centered) / nir_var
            slopes[band_name] = max(slope, 0.0)  # Slope must be non-negative

        self._glint_slopes = slopes
        log.debug(f"Glint slopes: {slopes}, min_NIR={self._glint_min_nir:.4f}")
        return slopes

    def remove_sun_glint(
        self,
        band_value: float,
        nir_value: float,
        band_name: str,
    ) -> float:
        """
        Remove sun glint contribution from a single pixel.

        R_corrected = R_band - slope * (NIR - min_NIR)

        The min_NIR term accounts for the ambient NIR level over the
        deepest water, which represents the residual atmospheric path
        radiance rather than glint.

        Args:
            band_value: Reflectance value for this band
            nir_value: NIR reflectance at the same pixel
            band_name: "blue", "green", or "red"

        Returns:
            Deglinted reflectance value
        """
        if band_name not in self._glint_slopes or self._glint_min_nir is None:
            return band_value

        slope = self._glint_slopes[band_name]
        min_nir = self._glint_min_nir

        glint_contribution = slope * (nir_value - min_nir)
        corrected = band_value - glint_contribution

        # Reflectance cannot be negative
        return max(corrected, EPS)

    def remove_sun_glint_array(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        nir: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Vectorized sun glint removal for arrays.

        Returns:
            (blue_corrected, green_corrected, red_corrected)
        """
        if not self._glint_slopes or self._glint_min_nir is None:
            return blue, green, red

        min_nir = self._glint_min_nir
        nir_excess = nir - min_nir

        blue_c = blue - self._glint_slopes.get("blue", 0) * nir_excess
        green_c = green - self._glint_slopes.get("green", 0) * nir_excess
        red_c = red - self._glint_slopes.get("red", 0) * nir_excess

        return (
            np.clip(blue_c, EPS, None),
            np.clip(green_c, EPS, None),
            np.clip(red_c, EPS, None),
        )

    # ── Stage 3: Quality Filtering ─────────────────────────────────

    def compute_quality_mask(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        nir: np.ndarray,
        scl: Optional[np.ndarray] = None,
        sun_zenith: Optional[float] = None,
    ) -> np.ndarray:
        """
        Compute a boolean quality mask. True = good pixel, False = bad.

        Filters:
        1. SCL-based: cloud, cloud shadow, snow, saturated, vegetation
        2. Saturation: reflectance > 0.3 in any visible band
        3. Land/vegetation: NDVI > 0.2
        4. Extreme turbidity: red/blue > 2.0
        5. Sun zenith angle > 60 degrees

        Args:
            blue, green, red, nir: Reflectance arrays (already scaled)
            scl: Scene Classification Layer values (optional)
            sun_zenith: Sun zenith angle in degrees (optional, per-scene)

        Returns:
            Boolean mask, same shape as input arrays
        """
        n = len(blue)
        mask = np.ones(n, dtype=bool)

        # 1. SCL filtering (cloud, shadow, snow, saturated)
        if scl is not None:
            scl_good = np.array([s not in SCL_BAD for s in scl])
            mask &= scl_good
            n_scl_bad = np.sum(~scl_good)
            if n_scl_bad > 0:
                log.debug(f"SCL filtered: {n_scl_bad} pixels")

        # 2. Saturation check
        saturated = (
            (blue > self.saturation_threshold) |
            (green > self.saturation_threshold) |
            (red > self.saturation_threshold)
        )
        mask &= ~saturated

        # 3. Land/vegetation (NDVI > threshold)
        ndvi = (nir - red) / (nir + red + EPS)
        land = ndvi > self.ndvi_land_threshold
        mask &= ~land

        # 4. Extreme turbidity (red >> blue)
        turbid = (red / (blue + EPS)) > self.turbidity_rb_threshold
        mask &= ~turbid

        # 5. Sun zenith angle
        if sun_zenith is not None and sun_zenith > self.max_sun_zenith:
            log.debug(f"Sun zenith {sun_zenith:.1f} > {self.max_sun_zenith}. "
                      f"Entire scene flagged.")
            mask[:] = False

        # 6. Negative or zero reflectance (invalid data)
        invalid = (blue <= 0) | (green <= 0) | (red <= 0)
        mask &= ~invalid

        n_good = np.sum(mask)
        log.debug(f"Quality mask: {n_good}/{n} pixels passed ({100*n_good/max(n,1):.1f}%)")
        return mask

    def compute_quality_flag_single(
        self,
        blue: float,
        green: float,
        red: float,
        nir: float,
        scl: Optional[int] = None,
        sun_zenith: Optional[float] = None,
    ) -> bool:
        """Single-pixel quality check. Returns True if pixel is good."""
        if scl is not None and scl in SCL_BAD:
            return False
        if blue > self.saturation_threshold or green > self.saturation_threshold \
           or red > self.saturation_threshold:
            return False
        ndvi = (nir - red) / (nir + red + EPS)
        if ndvi > self.ndvi_land_threshold:
            return False
        if (red / (blue + EPS)) > self.turbidity_rb_threshold:
            return False
        if sun_zenith is not None and sun_zenith > self.max_sun_zenith:
            return False
        if blue <= 0 or green <= 0 or red <= 0:
            return False
        return True

    # ── Stage 4: Water Column Correction ───────────────────────────

    @staticmethod
    def estimate_kd(
        shallow_reflectance: np.ndarray,
        deep_reflectance: np.ndarray,
        depth_shallow: float = 1.0,
        depth_deep: float = 10.0,
    ) -> float:
        """
        Estimate diffuse attenuation coefficient (Kd) from reflectance ratio
        at two known depths using Beer-Lambert law.

        Beer-Lambert: R(z) = R(0) * exp(-2 * Kd * z)
            Factor 2 accounts for two-way path (down + up)

        Kd = ln(R_shallow / R_deep) / (2 * (z_deep - z_shallow))

        Args:
            shallow_reflectance: Mean reflectance over shallow water
            deep_reflectance: Mean reflectance over deep water
            depth_shallow: Approximate depth of shallow region (m)
            depth_deep: Approximate depth of deep region (m)

        Returns:
            Estimated Kd in m^-1
        """
        r_shallow = np.mean(shallow_reflectance)
        r_deep = np.mean(deep_reflectance)

        if r_deep < EPS or r_shallow < EPS:
            return 0.1  # Default reasonable Kd

        ratio = r_shallow / r_deep
        if ratio <= 1.0:
            return 0.1  # Shallow should be brighter; fallback

        dz = depth_deep - depth_shallow
        if dz <= 0:
            return 0.1

        kd = np.log(ratio) / (2.0 * dz)

        # Clamp to physically reasonable range
        return float(np.clip(kd, 0.01, 5.0))

    @staticmethod
    def water_column_correction(
        reflectance: float,
        depth: float,
        kd: float,
    ) -> float:
        """
        Correct reflectance for water column attenuation.

        Removes depth-dependent signal loss to recover bottom reflectance:
            R_bottom = R_measured * exp(2 * Kd * z)

        This normalizes reflectance as if all measurements were at the surface,
        making the signal depth-independent (isolates bottom type from depth).

        Args:
            reflectance: Measured water-leaving reflectance
            depth: Estimated water depth (m)
            kd: Diffuse attenuation coefficient (m^-1)

        Returns:
            Depth-corrected (bottom) reflectance
        """
        if depth <= 0 or kd <= 0:
            return reflectance

        # Clamp correction factor to avoid numerical explosion
        correction = np.exp(2.0 * kd * min(depth, 30.0))
        correction = min(correction, 1000.0)  # Safety cap

        return reflectance * correction

    @staticmethod
    def lyzenga_depth_invariant_index(
        band1: float,
        band2: float,
        ki_ratio: float,
    ) -> float:
        """
        Lyzenga (1978, 1985) depth-invariant bottom index.

        DII = ln(band1) - (ki/kj) * ln(band2)

        This index is sensitive to bottom type but independent of depth,
        useful for separating sand/mud/vegetation substrates.

        Args:
            band1: Reflectance of band i (e.g. blue)
            band2: Reflectance of band j (e.g. green)
            ki_ratio: Ratio of attenuation coefficients ki/kj

        Returns:
            Depth-invariant index
        """
        return np.log(max(band1, EPS)) - ki_ratio * np.log(max(band2, EPS))

    # ── Stage 5: Physics-Based Feature Engineering ─────────────────

    @staticmethod
    def compute_sdb_features(
        blue: float,
        green: float,
        red: float,
        nir: float,
        swir1: Optional[float] = None,
        swir2: Optional[float] = None,
        rededge1: Optional[float] = None,
        rededge2: Optional[float] = None,
        rededge3: Optional[float] = None,
        nir08: Optional[float] = None,
        kd_blue: float = 0.0196,
        kd_green: float = 0.0640,
        kd_red: float = 0.3490,
    ) -> dict[str, float]:
        """
        Compute physics-based features for bathymetry estimation.

        Returns ~25+ features with physical meaning for depth estimation,
        replacing raw band values that have no inherent depth relationship.

        Args:
            blue, green, red, nir: Core reflectance values (already deglinted)
            swir1, swir2: SWIR bands (optional)
            rededge1/2/3, nir08: Red edge and narrow NIR (optional)
            kd_blue, kd_green, kd_red: Diffuse attenuation coefficients

        Returns:
            Dict of feature_name -> value
        """
        features = {}

        # ── Core reflectance (log-space for linearity with depth) ──
        features["blue"] = blue
        features["green"] = green
        features["red"] = red
        features["nir"] = nir

        features["log_blue"] = np.log(max(blue, EPS))
        features["log_green"] = np.log(max(green, EPS))
        features["log_red"] = np.log(max(red, EPS))
        features["log_nir"] = np.log(max(nir, EPS))

        # ── Stumpf log-ratio (2003) ──
        # depth ~ n0 + n1 * ln(1000*blue) / ln(1000*green)
        # Scaling by 1000 ensures positive log values for typical reflectance
        stumpf_blue = np.log(max(blue * 1000, EPS))
        stumpf_green = np.log(max(green * 1000, EPS))
        features["stumpf_ratio"] = stumpf_blue / max(stumpf_green, EPS)

        # ── Lyzenga depth-invariant indices (1978, 1985) ──
        # DII = ln(Ri) - (Ki/Kj) * ln(Rj)
        # Removes depth dependency, isolates bottom type
        kb_kg = kd_blue / max(kd_green, EPS)
        kb_kr = kd_blue / max(kd_red, EPS)
        kg_kr = kd_green / max(kd_red, EPS)

        features["lyzenga_bg"] = np.log(max(blue, EPS)) - kb_kg * np.log(max(green, EPS))
        features["lyzenga_br"] = np.log(max(blue, EPS)) - kb_kr * np.log(max(red, EPS))
        features["lyzenga_gr"] = np.log(max(green, EPS)) - kg_kr * np.log(max(red, EPS))

        # ── Water indices ──
        # NDWI (McFeeters 1996): water/non-water discrimination
        features["ndwi"] = (green - nir) / (green + nir + EPS)

        # MNDWI (Xu 2006): modified NDWI, better for urban/built-up areas
        if swir1 is not None:
            features["mndwi"] = (green - swir1) / (green + swir1 + EPS)
        else:
            features["mndwi"] = np.nan

        # NDVI: vegetation detection (should be ~0 over open water)
        features["ndvi"] = (nir - red) / (nir + red + EPS)

        # ── Band ratios (depth-sensitive) ──
        features["blue_green_ratio"] = blue / max(green, EPS)
        features["blue_red_ratio"] = blue / max(red, EPS)
        features["green_red_ratio"] = green / max(red, EPS)

        # ── Turbidity & water quality proxies ──
        # Turbidity increases red+green relative to blue
        features["turbidity_index"] = (red + green) / max(blue, EPS)

        # CDOM (Coloured Dissolved Organic Matter) absorbs blue light
        features["cdom_proxy"] = blue / max(red, EPS)

        # NDTI (Normalized Difference Turbidity Index)
        features["ndti"] = (red - green) / (red + green + EPS)

        # ── Relative band depth (normalized by max) ──
        band_max = max(blue, green, red, EPS)
        features["rel_blue"] = blue / band_max
        features["rel_green"] = green / band_max
        features["rel_red"] = red / band_max

        # ── Second-order features (capture non-linear interactions) ──
        features["blue_x_green"] = blue * green
        features["blue_x_red"] = blue * red
        features["green_x_red"] = green * red
        features["blue_sq"] = blue ** 2
        features["green_sq"] = green ** 2

        # ── Band differences ──
        features["blue_minus_green"] = blue - green
        features["green_minus_red"] = green - red
        features["red_minus_nir"] = red - nir

        # ── Optional red-edge and SWIR features ──
        if rededge1 is not None:
            features["rededge1"] = rededge1
            features["cdom_rededge"] = blue / max(rededge1, EPS)

        if rededge2 is not None:
            features["rededge2"] = rededge2

        if rededge3 is not None:
            features["rededge3"] = rededge3

        if nir08 is not None:
            features["nir08"] = nir08

        if swir1 is not None:
            features["swir16"] = swir1

        if swir2 is not None:
            features["swir22"] = swir2

        # ── FAI (Floating Algae Index) ──
        if swir1 is not None:
            fai_lambda_factor = (833 - 665) / (1610 - 665)
            features["fai"] = nir - red - (swir1 - red) * fai_lambda_factor
        else:
            features["fai"] = np.nan

        return features

    @staticmethod
    def compute_sdb_features_array(
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        nir: np.ndarray,
        swir1: Optional[np.ndarray] = None,
        swir2: Optional[np.ndarray] = None,
        rededge1: Optional[np.ndarray] = None,
        rededge2: Optional[np.ndarray] = None,
        rededge3: Optional[np.ndarray] = None,
        nir08: Optional[np.ndarray] = None,
        kd_blue: float = 0.0196,
        kd_green: float = 0.0640,
        kd_red: float = 0.3490,
    ) -> dict[str, np.ndarray]:
        """
        Vectorized version of compute_sdb_features for numpy arrays.
        Same features, but operates on entire arrays at once.
        """
        features = {}

        # Core reflectance
        features["blue"] = blue
        features["green"] = green
        features["red"] = red
        features["nir"] = nir

        features["log_blue"] = np.log(np.clip(blue, EPS, None))
        features["log_green"] = np.log(np.clip(green, EPS, None))
        features["log_red"] = np.log(np.clip(red, EPS, None))
        features["log_nir"] = np.log(np.clip(nir, EPS, None))

        # Stumpf log-ratio
        stumpf_blue = np.log(np.clip(blue * 1000, EPS, None))
        stumpf_green = np.log(np.clip(green * 1000, EPS, None))
        features["stumpf_ratio"] = stumpf_blue / np.clip(stumpf_green, EPS, None)

        # Lyzenga depth-invariant indices
        kb_kg = kd_blue / max(kd_green, EPS)
        kb_kr = kd_blue / max(kd_red, EPS)
        kg_kr = kd_green / max(kd_red, EPS)

        log_b = np.log(np.clip(blue, EPS, None))
        log_g = np.log(np.clip(green, EPS, None))
        log_r = np.log(np.clip(red, EPS, None))

        features["lyzenga_bg"] = log_b - kb_kg * log_g
        features["lyzenga_br"] = log_b - kb_kr * log_r
        features["lyzenga_gr"] = log_g - kg_kr * log_r

        # Water indices
        features["ndwi"] = (green - nir) / (green + nir + EPS)
        features["ndvi"] = (nir - red) / (nir + red + EPS)

        if swir1 is not None:
            features["mndwi"] = (green - swir1) / (green + swir1 + EPS)
        else:
            features["mndwi"] = np.full_like(blue, np.nan)

        # Band ratios
        features["blue_green_ratio"] = blue / np.clip(green, EPS, None)
        features["blue_red_ratio"] = blue / np.clip(red, EPS, None)
        features["green_red_ratio"] = green / np.clip(red, EPS, None)

        # Turbidity & water quality
        features["turbidity_index"] = (red + green) / np.clip(blue, EPS, None)
        features["cdom_proxy"] = blue / np.clip(red, EPS, None)
        features["ndti"] = (red - green) / (red + green + EPS)

        # Relative band depth
        band_max = np.maximum(np.maximum(blue, green), np.maximum(red, EPS))
        features["rel_blue"] = blue / band_max
        features["rel_green"] = green / band_max
        features["rel_red"] = red / band_max

        # Second-order
        features["blue_x_green"] = blue * green
        features["blue_x_red"] = blue * red
        features["green_x_red"] = green * red
        features["blue_sq"] = blue ** 2
        features["green_sq"] = green ** 2

        # Band differences
        features["blue_minus_green"] = blue - green
        features["green_minus_red"] = green - red
        features["red_minus_nir"] = red - nir

        # Optional bands
        if rededge1 is not None:
            features["rededge1"] = rededge1
            features["cdom_rededge"] = blue / np.clip(rededge1, EPS, None)
        if rededge2 is not None:
            features["rededge2"] = rededge2
        if rededge3 is not None:
            features["rededge3"] = rededge3
        if nir08 is not None:
            features["nir08"] = nir08
        if swir1 is not None:
            features["swir16"] = swir1
        if swir2 is not None:
            features["swir22"] = swir2

        # FAI
        if swir1 is not None:
            fai_lambda_factor = (833 - 665) / (1610 - 665)
            features["fai"] = nir - red - (swir1 - red) * fai_lambda_factor
        else:
            features["fai"] = np.full_like(blue, np.nan)

        return features

    # ── Stage 6: Temporal Match Scoring ────────────────────────────

    @staticmethod
    def temporal_match_score(
        s2_date: datetime,
        icesat2_date: datetime,
    ) -> float:
        """
        Compute a quality score based on temporal proximity between
        the Sentinel-2 acquisition and ICESat-2 overpass.

        Closer dates = more reliable depth-reflectance pairing because
        water conditions (turbidity, algae, water level) change over time.

        Scoring:
            Same day:   1.0
            +/- 7 days: 0.8
            +/- 30 days: 0.5
            +/- 90 days: 0.2
            > 90 days:  0.05

        Args:
            s2_date: Sentinel-2 scene acquisition date
            icesat2_date: ICESat-2 overpass date

        Returns:
            Score in [0.05, 1.0]
        """
        delta_days = abs((s2_date - icesat2_date).days)

        if delta_days == 0:
            return 1.0
        elif delta_days <= 7:
            # Linear interpolation: 1.0 at day 0, 0.8 at day 7
            return 1.0 - 0.2 * (delta_days / 7.0)
        elif delta_days <= 30:
            # 0.8 at day 7, 0.5 at day 30
            return 0.8 - 0.3 * ((delta_days - 7) / 23.0)
        elif delta_days <= 90:
            # 0.5 at day 30, 0.2 at day 90
            return 0.5 - 0.3 * ((delta_days - 30) / 60.0)
        else:
            return 0.05

    @staticmethod
    def temporal_match_score_days(delta_days: int) -> float:
        """Simplified scoring from integer day difference."""
        delta_days = abs(delta_days)
        if delta_days == 0:
            return 1.0
        elif delta_days <= 7:
            return 1.0 - 0.2 * (delta_days / 7.0)
        elif delta_days <= 30:
            return 0.8 - 0.3 * ((delta_days - 7) / 23.0)
        elif delta_days <= 90:
            return 0.5 - 0.3 * ((delta_days - 30) / 60.0)
        else:
            return 0.05

    # ── Full Pipeline: Single Point ────────────────────────────────

    def process_point(
        self,
        bands: dict[str, float],
        scl: Optional[int] = None,
        sun_zenith: Optional[float] = None,
        s2_date: Optional[datetime] = None,
        icesat2_date: Optional[datetime] = None,
        is_raw_dn: bool = True,
    ) -> Optional[dict[str, float]]:
        """
        Full preprocessing pipeline for a single point.

        Steps:
        1. DN -> reflectance (if raw DNs)
        2. Quality check (returns None if bad)
        3. Sun glint removal (uses pre-computed slopes)
        4. Physics-based feature computation
        5. Temporal match scoring

        Args:
            bands: Dict of band values, e.g. {"blue": 2016, "green": 1500, ...}
            scl: Scene Classification Layer value
            sun_zenith: Sun zenith angle (degrees)
            s2_date: Sentinel-2 acquisition date
            icesat2_date: ICESat-2 overpass date
            is_raw_dn: True if values are raw DNs (need /10000 conversion)

        Returns:
            Dict of preprocessed features, or None if pixel fails quality checks.
        """
        # Stage 1: DN -> reflectance
        if is_raw_dn:
            bands = {k: self.dn_to_reflectance(v) for k, v in bands.items()}

        blue = bands.get("blue", 0.0)
        green = bands.get("green", 0.0)
        red = bands.get("red", 0.0)
        nir = bands.get("nir", 0.0)

        # Stage 3: Quality check
        if not self.compute_quality_flag_single(blue, green, red, nir, scl, sun_zenith):
            return None

        # Stage 2: Sun glint removal (if slopes have been estimated)
        blue = self.remove_sun_glint(blue, nir, "blue")
        green = self.remove_sun_glint(green, nir, "green")
        red = self.remove_sun_glint(red, nir, "red")

        # Stage 5: Physics features
        features = self.compute_sdb_features(
            blue=blue,
            green=green,
            red=red,
            nir=nir,
            swir1=bands.get("swir16"),
            swir2=bands.get("swir22"),
            rededge1=bands.get("rededge1"),
            rededge2=bands.get("rededge2"),
            rededge3=bands.get("rededge3"),
            nir08=bands.get("nir08"),
        )

        # Stage 6: Temporal match
        if s2_date is not None and icesat2_date is not None:
            features["temporal_match_score"] = self.temporal_match_score(s2_date, icesat2_date)
        else:
            features["temporal_match_score"] = np.nan

        return features

    # ── Full Pipeline: DataFrame (batch) ───────────────────────────

    def process_dataframe(
        self,
        df,
        is_raw_dn: bool = True,
        s2_date_col: str = "s2_date",
        icesat2_date_col: str = "icesat2_date",
        scl_col: str = "scl",
        sun_zenith_col: str = "sun_zenith",
    ):
        """
        Batch preprocessing pipeline for a pandas DataFrame.

        Applies all preprocessing stages to every row, returning a new
        DataFrame with physics-based features replacing raw bands.

        Args:
            df: DataFrame with at least blue, green, red, nir columns
            is_raw_dn: Whether values are raw digital numbers
            s2_date_col: Column name for S2 dates
            icesat2_date_col: Column name for ICESat-2 dates
            scl_col: Column name for SCL values
            sun_zenith_col: Column name for sun zenith angle

        Returns:
            DataFrame with preprocessed features and quality mask
        """
        import pandas as pd

        log.info(f"Processing {len(df):,} points through SDB pipeline...")
        df = df.copy()

        # Stage 1: DN -> reflectance
        band_cols = ["blue", "green", "red", "nir", "nir08",
                     "rededge1", "rededge2", "rededge3", "swir16", "swir22"]

        if is_raw_dn:
            for col in band_cols:
                if col in df.columns:
                    df[col] = df[col].astype(np.float64) / S2_SCALE_FACTOR
            log.info("  DN -> reflectance conversion applied")

        blue = df["blue"].values
        green = df["green"].values
        red = df["red"].values
        nir = df["nir"].values

        # Stage 2: Estimate glint slopes from the full dataset, then deglint
        self.estimate_glint_slopes(blue, green, red, nir)
        blue, green, red = self.remove_sun_glint_array(blue, green, red, nir)
        df["blue"] = blue
        df["green"] = green
        df["red"] = red
        log.info("  Sun glint removal applied")

        # Stage 3: Quality mask
        scl = df[scl_col].values if scl_col in df.columns else None
        sun_zenith = df[sun_zenith_col].values[0] if sun_zenith_col in df.columns else None

        mask = self.compute_quality_mask(blue, green, red, nir, scl, sun_zenith)
        n_before = len(df)
        df = df[mask].reset_index(drop=True)
        log.info(f"  Quality filter: {n_before} -> {len(df)} ({len(df)/max(n_before,1)*100:.1f}%)")

        if len(df) == 0:
            log.warning("  All points filtered out!")
            return pd.DataFrame()

        # Stage 5: Physics features (vectorized)
        blue = df["blue"].values
        green = df["green"].values
        red = df["red"].values
        nir = df["nir"].values

        feat_dict = self.compute_sdb_features_array(
            blue=blue,
            green=green,
            red=red,
            nir=nir,
            swir1=df["swir16"].values if "swir16" in df.columns else None,
            swir2=df["swir22"].values if "swir22" in df.columns else None,
            rededge1=df["rededge1"].values if "rededge1" in df.columns else None,
            rededge2=df["rededge2"].values if "rededge2" in df.columns else None,
            rededge3=df["rededge3"].values if "rededge3" in df.columns else None,
            nir08=df["nir08"].values if "nir08" in df.columns else None,
        )

        for feat_name, feat_vals in feat_dict.items():
            df[feat_name] = feat_vals
        log.info(f"  Computed {len(feat_dict)} physics-based features")

        # Stage 6: Temporal match scoring
        if s2_date_col in df.columns and icesat2_date_col in df.columns:
            s2_dates = pd.to_datetime(df[s2_date_col])
            ice_dates = pd.to_datetime(df[icesat2_date_col])
            delta_days = (s2_dates - ice_dates).dt.days.abs()
            df["temporal_match_score"] = delta_days.apply(self.temporal_match_score_days)
            log.info(f"  Temporal scores: mean={df['temporal_match_score'].mean():.2f}")
        else:
            df["temporal_match_score"] = np.nan
            log.info("  No date columns found; temporal_match_score = NaN")

        # Clean up inf/nan in numeric cols (use NaN, not 0.0)
        for col in df.select_dtypes(include=[np.number]).columns:
            df[col] = df[col].replace([np.inf, -np.inf], np.nan)

        log.info(f"  Final: {len(df)} points, {len(df.columns)} columns")
        return df


# ── Feature list for downstream models ─────────────────────────────

# Core physics features (always computed)
CORE_SDB_FEATURES = [
    "blue", "green", "red", "nir",
    "log_blue", "log_green", "log_red", "log_nir",
    "stumpf_ratio",
    "lyzenga_bg", "lyzenga_br", "lyzenga_gr",
    "ndwi", "mndwi", "ndvi",
    "blue_green_ratio", "blue_red_ratio", "green_red_ratio",
    "turbidity_index", "cdom_proxy", "ndti",
    "rel_blue", "rel_green", "rel_red",
    "blue_x_green", "blue_x_red", "green_x_red",
    "blue_sq", "green_sq",
    "blue_minus_green", "green_minus_red", "red_minus_nir",
]

# Extended features (when all bands available)
EXTENDED_SDB_FEATURES = CORE_SDB_FEATURES + [
    "rededge1", "rededge2", "rededge3", "nir08",
    "swir16", "swir22",
    "cdom_rededge", "fai",
    "temporal_match_score",
]


def get_feature_list(df_columns: list[str]) -> list[str]:
    """Return the list of available SDB features given a DataFrame's columns."""
    return [f for f in EXTENDED_SDB_FEATURES if f in df_columns]
