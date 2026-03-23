#!/usr/bin/env python3
"""
OpenCatch — Physics-Based Radiative Transfer Features for SDB

Advanced features from the satellite-derived bathymetry literature that encode
the physics of light propagation in water. These go beyond simple band ratios
to model the actual radiative transfer processes.

Features computed:
  1. Kd (diffuse attenuation coefficient) — estimated from blue/green ratio
  2. Secchi depth proxy — from band ratio empirical models
  3. Chlorophyll-a proxy — from blue/green/red OC4-style algorithm
  4. CDOM absorption — from blue/red ratio (Brezonik et al., 2015)
  5. Turbidity index — from red band magnitude + NIR
  6. Bottom reflectance estimate — subtract water column contribution
  7. Water type classification — Jerlov types from spectral shape
  8. Optical depth — -ln(R_bottom / R_surface) per band

References:
  - Lee et al. (1999): Semi-analytical model for shallow water reflectance
  - Stumpf et al. (2003): Log-ratio bathymetry
  - Lyzenga (1978, 1985): Multi-band depth-invariant indices
  - Morel & Prieur (1977): Water type classification
  - Pope & Fry (1997): Pure water absorption coefficients
  - Brezonik et al. (2015): Landsat-based water quality
  - Maritorena et al. (1994): Two-flow shallow water model

Usage:
    from physics_features import PhysicsFeatureExtractor
    extractor = PhysicsFeatureExtractor()

    # Single point (reflectances, already deglinted)
    features = extractor.compute_features(blue=0.03, green=0.04, red=0.01, nir=0.005)

    # Batch (DataFrame with reflectance columns)
    df = extractor.compute_features_df(df)

Requirements:
    numpy, pandas
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("physics_features")

EPS = 1e-8

# ── Physical Constants ───────────────────────────────────────────────

# Pure water absorption coefficients a_w (m^-1) — Pope & Fry (1997)
AW = {
    "blue":     0.0196,   # 490 nm (B2)
    "green":    0.0640,   # 560 nm (B3)
    "red":      0.3490,   # 665 nm (B4)
    "rededge1": 0.630,    # 705 nm (B5)
    "nir":      2.38,     # 842 nm (B8)
}

# Pure water backscattering coefficients bb_w (m^-1) — Morel (1974)
BBW = {
    "blue":     0.00153,
    "green":    0.00098,
    "red":      0.00046,
    "rededge1": 0.00035,
    "nir":      0.00018,
}

# Sentinel-2 band central wavelengths (nm)
WAVELENGTHS = {
    "blue": 490, "green": 560, "red": 665,
    "rededge1": 705, "rededge2": 740, "rededge3": 783,
    "nir": 842, "nir08": 865,
}

# Reference wavelength for spectral slope calculations
LAMBDA_REF = 440  # nm

# Typical bottom albedo for different substrates
BOTTOM_ALBEDO = {
    "sand":    {"blue": 0.20, "green": 0.25, "red": 0.20},
    "mud":     {"blue": 0.05, "green": 0.07, "red": 0.06},
    "veg":     {"blue": 0.02, "green": 0.08, "red": 0.03},
    "average": {"blue": 0.10, "green": 0.13, "red": 0.10},
}


class PhysicsFeatureExtractor:
    """
    Extracts physics-based features from Sentinel-2 water reflectances.

    All input reflectances should be BOA (bottom-of-atmosphere), deglinted,
    and quality-filtered. Values typically in range [0, 0.15] for water.
    """

    def __init__(
        self,
        deep_water_ref: Optional[dict] = None,
        bottom_type: str = "average",
    ):
        """
        Args:
            deep_water_ref: Reference deep water reflectance for each band.
                If None, uses reasonable defaults for clear inland lakes.
            bottom_type: Assumed bottom substrate for reflectance correction.
        """
        # Deep water reflectance (optically deep, no bottom signal)
        self.deep_water = deep_water_ref or {
            "blue":  0.015,
            "green": 0.010,
            "red":   0.003,
            "nir":   0.001,
        }
        self.bottom_albedo = BOTTOM_ALBEDO.get(bottom_type, BOTTOM_ALBEDO["average"])

    # ── 1. Diffuse Attenuation Coefficient (Kd) ─────────────────────

    def estimate_kd(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """
        Estimate Kd (diffuse attenuation coefficient) per band.

        Method 1: Empirical from Lee et al. (2005) Kd490 algorithm
            Kd(490) = a + bb/cos(theta_w) ≈ f(R_rs blue/green)

        Method 2: Semi-analytical from IOPs:
            Kd(λ) ≈ a(λ) + bb(λ)  (simplified for nadir)
            a(λ) = a_w(λ) + a_ph(λ) + a_cdom(λ)

        We use the empirical approach for robustness.
        """
        blue = np.asarray(blue, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        red = np.asarray(red, dtype=np.float64)

        # Lee et al. (2005) empirical Kd(490) from blue/green ratio
        bg_ratio = np.log10(np.clip(blue / (green + EPS), 0.1, 10.0))
        # Coefficients from Lee et al. (2005) for inland waters
        kd_490 = 10.0 ** (-0.8515 - 1.8263 * bg_ratio
                          - 0.09 * bg_ratio ** 2 + 0.4659 * bg_ratio ** 3)
        kd_490 = np.clip(kd_490, 0.01, 20.0)

        # Extrapolate to other wavelengths using spectral shape
        # Kd(λ) ≈ Kd(490) * (a_w(λ) + bb_w(λ)) / (a_w(490) + bb_w(490))
        kd_ref = AW["blue"] + BBW["blue"]
        kd_green = kd_490 * (AW["green"] + BBW["green"]) / kd_ref
        kd_red = kd_490 * (AW["red"] + BBW["red"]) / kd_ref

        return {
            "kd_blue":  kd_490,
            "kd_green": kd_green,
            "kd_red":   kd_red,
            "kd_490":   kd_490,
        }

    # ── 2. Secchi Depth Proxy ────────────────────────────────────────

    def estimate_secchi_depth(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
    ) -> np.ndarray:
        """
        Estimate Secchi disk depth from reflectance ratios.

        Uses the Lee et al. (2015) Zsd model:
            Zsd = 1 / (2.5 * Kd(490)) * ln(|0.14 - R_rs| / 0.013)

        Simplified: Zsd ∝ 1/Kd(490) — transparency inversely proportional
        to attenuation.

        Also incorporates the empirical Doron et al. (2007):
            ln(Zsd) = a0 + a1*ln(Rrs_blue/Rrs_green) + a2*ln(Rrs_blue/Rrs_red)
        """
        blue = np.asarray(blue, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        red = np.asarray(red, dtype=np.float64)

        # Method 1: Kd-based
        kd = self.estimate_kd(blue, green, red)
        zsd_kd = 1.0 / (2.5 * kd["kd_490"] + EPS)

        # Method 2: Empirical band-ratio
        bg = np.log(blue / (green + EPS) + EPS)
        br = np.log(blue / (red + EPS) + EPS)
        ln_zsd = 0.82 + 0.98 * bg + 0.65 * br  # Empirical coefficients
        zsd_ratio = np.exp(np.clip(ln_zsd, -2, 5))

        # Average both estimates
        secchi = 0.5 * (np.clip(zsd_kd, 0.05, 30.0) + np.clip(zsd_ratio, 0.05, 30.0))

        return secchi

    # ── 3. Chlorophyll-a Proxy ───────────────────────────────────────

    def estimate_chlorophyll(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        rededge1: Optional[np.ndarray] = None,
    ) -> dict[str, np.ndarray]:
        """
        Estimate chlorophyll-a concentration from band ratios.

        Uses OC4-style algorithm (O'Reilly et al., 2019):
            log10(Chl) = a0 + a1*R + a2*R² + a3*R³ + a4*R⁴
            where R = max(log10(Rrs_blue/Rrs_green))

        Also: Red-edge CI (Chlorophyll Index) if rededge1 available:
            CI = Rrs(705) - [Rrs(665) + (705-665)/(740-665) * (Rrs(740) - Rrs(665))]
            (Gitelson et al., 2011)
        """
        blue = np.asarray(blue, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        red = np.asarray(red, dtype=np.float64)

        # OC4 (simplified for S2 band configuration)
        R = np.log10(np.clip(blue / (green + EPS), 0.1, 10.0))

        # OC4 polynomial coefficients (O'Reilly et al., 2019 — ocean color)
        a = [0.3272, -2.9940, 2.7218, -1.2259, -0.5683]
        log_chl = a[0] + a[1] * R + a[2] * R ** 2 + a[3] * R ** 3 + a[4] * R ** 4
        chl_oc4 = 10.0 ** np.clip(log_chl, -2, 3)

        # 2-band ratio (Gilerson et al., 2010) — better for turbid inland waters
        gr_ratio = red / (green + EPS)
        chl_2band = 35.0 * (gr_ratio ** 1.65)  # Empirical for inland

        result = {
            "chl_oc4": chl_oc4,
            "chl_2band": chl_2band,
            "log_chl": np.log10(chl_oc4 + EPS),
        }

        # Red-edge chlorophyll index
        if rededge1 is not None:
            rededge1 = np.asarray(rededge1, dtype=np.float64)
            # 2-band NIR/red-edge (Mishra & Mishra, 2012)
            ci_re = rededge1 / (red + EPS) - 1.0
            chl_re = 14.039 + 86.115 * ci_re + 194.325 * ci_re ** 2
            result["chl_rededge"] = np.clip(chl_re, 0, 500)
            result["ci_rededge"] = ci_re

        return result

    # ── 4. CDOM Absorption ───────────────────────────────────────────

    def estimate_cdom(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """
        Estimate CDOM (Colored Dissolved Organic Matter) absorption.

        CDOM absorbs strongly in blue, decaying exponentially with wavelength:
            a_cdom(λ) = a_cdom(440) * exp(-S * (λ - 440))

        Empirical from Brezonik et al. (2015):
            a_cdom(440) = f(Rrs_blue / Rrs_red, Rrs_green / Rrs_red)

        Also compute spectral slope S (0.01-0.02 typical for freshwater).
        """
        blue = np.asarray(blue, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        red = np.asarray(red, dtype=np.float64)

        # Ratio-based CDOM proxy
        br_ratio = blue / (red + EPS)
        gr_ratio = green / (red + EPS)

        # Brezonik et al. (2015) — Landsat-based, adapted for S2
        # ln(a_cdom_440) = c0 + c1 * ln(Rrs_blue/Rrs_red) + c2 * ln(Rrs_green/Rrs_red)
        ln_acdom = -1.78 + 0.89 * np.log(br_ratio + EPS) + 0.58 * np.log(gr_ratio + EPS)
        a_cdom_440 = np.exp(np.clip(ln_acdom, -5, 5))

        # Spectral slope estimation
        # S ≈ -ln(a_cdom(490)/a_cdom(665)) / (665-490)
        # Approximate from blue/red reflectance difference
        with np.errstate(divide="ignore", invalid="ignore"):
            s_cdom = np.abs(np.log(blue + EPS) - np.log(red + EPS)) / (
                WAVELENGTHS["red"] - WAVELENGTHS["blue"]
            )
        s_cdom = np.clip(s_cdom, 0.005, 0.05)

        return {
            "a_cdom_440": a_cdom_440,
            "log_a_cdom": np.log(a_cdom_440 + EPS),
            "s_cdom": s_cdom,
            "cdom_index": np.log(br_ratio + EPS),  # Simple proxy
        }

    # ── 5. Turbidity Index ───────────────────────────────────────────

    def estimate_turbidity(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        nir: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """
        Estimate turbidity (suspended sediment concentration) indices.

        Methods:
        1. Red band magnitude: direct proxy for suspended particles
        2. NDTI (Normalized Difference Turbidity Index): (red-green)/(red+green)
        3. NIR-based: NIR over water = particles or floating vegetation
        4. Dogliotti et al. (2015): switching algorithm for low/high turbidity
        """
        red = np.asarray(red, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        blue = np.asarray(blue, dtype=np.float64)
        nir = np.asarray(nir, dtype=np.float64)

        # NDTI — Normalized Difference Turbidity Index
        ndti = (red - green) / (red + green + EPS)

        # Red band proxy (Nechad et al., 2010)
        # TSM ≈ A * rho_red / (1 - rho_red / C) + B
        A_red = 355.85   # calibration constant
        C_red = 0.1728   # asymptotic reflectance
        red_safe = np.clip(red, 0, C_red - 0.001)
        tsm_red = A_red * red_safe / (1 - red_safe / C_red)

        # NIR turbidity proxy
        nir_turb = nir * 100  # Scale for numerical convenience

        # Dogliotti switching: red for low turb, NIR for high turb
        # Blend at transition zone (red reflectance ~0.05-0.07)
        w = np.clip((red - 0.05) / 0.02, 0, 1)  # Weight toward NIR
        turb_blend = (1 - w) * tsm_red + w * (nir * 2000)

        # Turbidity categories
        turb_class = np.where(
            red < 0.01, 0,   # Clear
            np.where(red < 0.03, 1,   # Moderate
                     np.where(red < 0.07, 2,   # Turbid
                              3))  # Very turbid
        )

        return {
            "turb_ndti": ndti,
            "turb_tsm_red": np.clip(tsm_red, 0, 2000),
            "turb_nir": nir_turb,
            "turb_blend": np.clip(turb_blend, 0, 5000),
            "turb_class": turb_class.astype(np.float32),
            "log_turb": np.log1p(np.clip(tsm_red, 0, 2000)),
        }

    # ── 6. Bottom Reflectance Estimate ───────────────────────────────

    def estimate_bottom_reflectance(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        depth_estimate: Optional[np.ndarray] = None,
    ) -> dict[str, np.ndarray]:
        """
        Estimate bottom reflectance by removing water column contribution.

        Based on the Maritorena et al. (1994) two-flow model:
            R(λ) = R_bottom(λ) * exp(-2*Kd(λ)*z) + R_deep(λ) * (1 - exp(-2*Kd(λ)*z))

        Rearranging:
            R_bottom(λ) = [R(λ) - R_deep(λ)*(1 - exp(-2*Kd*z))] / exp(-2*Kd*z)

        If depth is unknown, uses optical depth to estimate bottom contribution
        relative to water column.
        """
        blue = np.asarray(blue, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        red = np.asarray(red, dtype=np.float64)

        kd = self.estimate_kd(blue, green, red)

        result = {}

        for band_name, band_data, kd_key in [
            ("blue", blue, "kd_blue"),
            ("green", green, "kd_green"),
            ("red", red, "kd_red"),
        ]:
            r_deep = self.deep_water[band_name]
            kd_val = kd[kd_key]

            if depth_estimate is not None:
                z = np.asarray(depth_estimate, dtype=np.float64)
            else:
                # Estimate optical depth from reflectance excess over deep water
                r_excess = np.clip(band_data - r_deep, EPS, None)
                r_bottom_ref = self.bottom_albedo[band_name]
                # z ≈ -ln(r_excess / r_bottom_ref) / (2 * Kd)
                z = -np.log(r_excess / (r_bottom_ref + EPS) + EPS) / (2 * kd_val + EPS)
                z = np.clip(z, 0, 50)

            # Two-flow inversion
            transmission = np.exp(-2 * kd_val * z)
            transmission = np.clip(transmission, EPS, 1.0)

            r_bottom = (band_data - r_deep * (1 - transmission)) / transmission
            r_bottom = np.clip(r_bottom, 0, 1)

            result[f"r_bottom_{band_name}"] = r_bottom
            result[f"transmission_{band_name}"] = transmission

        # Bottom brightness (mean of all bands)
        result["r_bottom_mean"] = (
            result["r_bottom_blue"] + result["r_bottom_green"] + result["r_bottom_red"]
        ) / 3.0

        # Bottom color index (helps distinguish substrates)
        result["bottom_bg_ratio"] = result["r_bottom_blue"] / (
            result["r_bottom_green"] + EPS
        )
        result["bottom_gr_ratio"] = result["r_bottom_green"] / (
            result["r_bottom_red"] + EPS
        )

        return result

    # ── 7. Water Type Classification ─────────────────────────────────

    def classify_water_type(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        nir: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """
        Classify water into Jerlov-like optical types based on spectral shape.

        Types (simplified from Jerlov 1976):
            Type I:   Very clear (oceanic) — blue dominant
            Type II:  Clear (oligotrophic lake) — blue > green
            Type III: Moderate (mesotrophic) — green ≈ blue
            Type 1C:  Turbid (eutrophic) — green > blue, red elevated
            Type 3C:  Very turbid — red dominant

        Also computes continuous water clarity features.
        """
        blue = np.asarray(blue, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        red = np.asarray(red, dtype=np.float64)
        nir = np.asarray(nir, dtype=np.float64)

        # Spectral shape features
        bg_ratio = blue / (green + EPS)
        gr_ratio = green / (red + EPS)
        br_ratio = blue / (red + EPS)
        total = blue + green + red + EPS

        # Fractional contributions
        f_blue = blue / total
        f_green = green / total
        f_red = red / total

        # Spectral slope (blue -> red)
        with np.errstate(divide="ignore", invalid="ignore"):
            spectral_slope = (np.log(blue + EPS) - np.log(red + EPS)) / (
                WAVELENGTHS["red"] - WAVELENGTHS["blue"]
            )

        # Classify into types
        # Type I: bg_ratio > 2.0, red very low
        # Type II: bg_ratio > 1.2, moderate red
        # Type III: bg_ratio 0.8-1.2
        # Type 1C: bg_ratio < 0.8, red/nir elevated
        # Type 3C: bg_ratio < 0.5, red dominant
        jerlov_type = np.where(
            bg_ratio > 2.0, 1,
            np.where(bg_ratio > 1.2, 2,
                     np.where(bg_ratio > 0.8, 3,
                              np.where(bg_ratio > 0.5, 4,
                                       5)))
        ).astype(np.float32)

        # One-hot encoded (soft) water types for ML
        clear_score = np.clip((bg_ratio - 1.0) / 1.5, 0, 1)
        moderate_score = 1.0 - np.abs(bg_ratio - 1.0)
        moderate_score = np.clip(moderate_score, 0, 1)
        turbid_score = np.clip((1.0 - bg_ratio) / 0.5, 0, 1)
        tannin_score = np.clip(
            (np.log(blue + EPS) - np.log(red + EPS)) * (-1) * (1 - red / (red + 0.02)),
            0, 1,
        )

        return {
            "water_type": jerlov_type,
            "clear_score": clear_score,
            "moderate_score": moderate_score,
            "turbid_score": turbid_score,
            "tannin_score": tannin_score,
            "spectral_slope": spectral_slope,
            "f_blue": f_blue,
            "f_green": f_green,
            "f_red": f_red,
            "bg_ratio": bg_ratio,
            "gr_ratio": gr_ratio,
            "br_ratio": br_ratio,
        }

    # ── 8. Optical Depth ─────────────────────────────────────────────

    def compute_optical_depth(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """
        Compute optical depth per band: tau = -ln(R / R_deep) for each band.

        Optical depth relates to physical depth via Beer-Lambert:
            R(z) = R_bottom * exp(-2*Kd*z) + R_deep*(1 - exp(-2*Kd*z))

        For shallow water where bottom signal dominates:
            tau ≈ -ln((R - R_deep) / R_bottom) ≈ 2*Kd*z

        This linearizes the depth-reflectance relationship for ML.
        """
        blue = np.asarray(blue, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        red = np.asarray(red, dtype=np.float64)

        result = {}

        for band_name, band_data in [("blue", blue), ("green", green), ("red", red)]:
            r_deep = self.deep_water[band_name]
            r_bottom = self.bottom_albedo[band_name]

            # Excess reflectance over deep water = bottom signal
            r_excess = np.clip(band_data - r_deep, EPS, None)

            # Optical depth
            tau = -np.log(np.clip(r_excess / (r_bottom + EPS), EPS, 1.0))
            tau = np.clip(tau, 0, 15)  # Physical limit

            result[f"optical_depth_{band_name}"] = tau

        # Multi-band optical depth features
        result["optical_depth_mean"] = (
            result["optical_depth_blue"]
            + result["optical_depth_green"]
            + result["optical_depth_red"]
        ) / 3.0

        # Ratio of optical depths (sensitive to water properties, not depth)
        result["od_ratio_bg"] = result["optical_depth_blue"] / (
            result["optical_depth_green"] + EPS
        )
        result["od_ratio_br"] = result["optical_depth_blue"] / (
            result["optical_depth_red"] + EPS
        )

        return result

    # ── Combined Feature Extraction ──────────────────────────────────

    def compute_features(
        self,
        blue: float,
        green: float,
        red: float,
        nir: float = 0.001,
        rededge1: Optional[float] = None,
        depth_estimate: Optional[float] = None,
    ) -> dict[str, float]:
        """
        Compute ALL physics features for a single point.

        Args:
            blue, green, red, nir: BOA reflectance values (already deglinted).
            rededge1: Red-edge band reflectance (optional, for chl-a).
            depth_estimate: Known/estimated depth (m) for bottom correction.

        Returns:
            Dict of feature_name -> value.
        """
        b = np.array([blue])
        g = np.array([green])
        r = np.array([red])
        n = np.array([nir])

        features = {}

        # Kd
        kd = self.estimate_kd(b, g, r)
        for k, v in kd.items():
            features[k] = float(v[0])

        # Secchi
        features["secchi_depth_m"] = float(self.estimate_secchi_depth(b, g, r)[0])

        # Chlorophyll
        re1 = np.array([rededge1]) if rededge1 is not None else None
        chl = self.estimate_chlorophyll(b, g, r, re1)
        for k, v in chl.items():
            features[k] = float(v[0]) if isinstance(v, np.ndarray) else float(v)

        # CDOM
        cdom = self.estimate_cdom(b, g, r)
        for k, v in cdom.items():
            features[k] = float(v[0])

        # Turbidity
        turb = self.estimate_turbidity(b, g, r, n)
        for k, v in turb.items():
            features[k] = float(v[0])

        # Bottom reflectance
        de = np.array([depth_estimate]) if depth_estimate is not None else None
        bottom = self.estimate_bottom_reflectance(b, g, r, de)
        for k, v in bottom.items():
            features[k] = float(v[0])

        # Water type
        wtype = self.classify_water_type(b, g, r, n)
        for k, v in wtype.items():
            features[k] = float(v[0])

        # Optical depth
        od = self.compute_optical_depth(b, g, r)
        for k, v in od.items():
            features[k] = float(v[0])

        return features

    def compute_features_df(
        self,
        df: pd.DataFrame,
        prefix: str = "",
    ) -> pd.DataFrame:
        """
        Compute ALL physics features for a DataFrame of points.

        Expects columns: blue, green, red, nir (reflectance).
        Optional: rededge1, depth_m (for bottom correction).

        Returns original DataFrame with new feature columns added.
        """
        result = df.copy()

        blue = df["blue"].values
        green = df["green"].values
        red = df["red"].values
        nir = df.get("nir", pd.Series(0.001, index=df.index)).values

        rededge1 = df["rededge1"].values if "rededge1" in df.columns else None
        depth_est = df["depth_m"].values if "depth_m" in df.columns else None

        # 1. Kd
        kd = self.estimate_kd(blue, green, red)
        for k, v in kd.items():
            result[f"{prefix}{k}"] = v

        # 2. Secchi depth
        result[f"{prefix}secchi_depth_m"] = self.estimate_secchi_depth(blue, green, red)

        # 3. Chlorophyll
        chl = self.estimate_chlorophyll(blue, green, red, rededge1)
        for k, v in chl.items():
            result[f"{prefix}{k}"] = v

        # 4. CDOM
        cdom = self.estimate_cdom(blue, green, red)
        for k, v in cdom.items():
            result[f"{prefix}{k}"] = v

        # 5. Turbidity
        turb = self.estimate_turbidity(blue, green, red, nir)
        for k, v in turb.items():
            result[f"{prefix}{k}"] = v

        # 6. Bottom reflectance
        bottom = self.estimate_bottom_reflectance(blue, green, red, depth_est)
        for k, v in bottom.items():
            result[f"{prefix}{k}"] = v

        # 7. Water type
        wtype = self.classify_water_type(blue, green, red, nir)
        for k, v in wtype.items():
            result[f"{prefix}{k}"] = v

        # 8. Optical depth
        od = self.compute_optical_depth(blue, green, red)
        for k, v in od.items():
            result[f"{prefix}{k}"] = v

        n_new = sum(1 for c in result.columns if c not in df.columns)
        log.info(f"Added {n_new} physics features to DataFrame ({len(result):,} rows)")

        return result

    def get_feature_names(self) -> list[str]:
        """Return list of all physics feature names this extractor produces."""
        # Compute on a dummy point to get all feature names
        dummy = self.compute_features(blue=0.03, green=0.04, red=0.01, nir=0.002)
        return sorted(dummy.keys())


# ── CLI for batch processing ─────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Compute physics-based SDB features")
    parser.add_argument("--input", type=str, required=True, help="Input parquet/csv")
    parser.add_argument("--output", type=str, required=True, help="Output parquet")
    parser.add_argument(
        "--prefix", type=str, default="phys_", help="Column prefix for new features"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    # Load
    path = args.input
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    log.info(f"Loaded {len(df):,} rows from {path}")

    # Extract features
    extractor = PhysicsFeatureExtractor()
    df_out = extractor.compute_features_df(df, prefix=args.prefix)

    # Save
    df_out.to_parquet(args.output, index=False)
    log.info(f"Saved {len(df_out):,} rows with physics features to {args.output}")

    # Print feature summary
    new_cols = [c for c in df_out.columns if c not in df.columns]
    log.info(f"\nNew physics features ({len(new_cols)}):")
    for col in sorted(new_cols):
        vals = df_out[col].dropna()
        if len(vals) > 0:
            log.info(f"  {col}: mean={vals.mean():.4f}, std={vals.std():.4f}, "
                     f"range=[{vals.min():.4f}, {vals.max():.4f}]")


if __name__ == "__main__":
    main()
