#!/usr/bin/env python3
"""
OpenCatch -- Reservoir Cross-Section Extraction & Terrain Extrapolation

THE KEY INNOVATION: Reservoirs are flooded valleys. The DEM terrain above
the waterline is the same geological formation that continues underwater.
We extract valley cross-sections perpendicular to the thalweg, fit parametric
shapes to the visible walls, and extrapolate below the waterline.

Process:
  1. Discretize the thalweg (river channel) into sample points
  2. Generate perpendicular transects at each point
  3. Sample DEM elevation along each transect
  4. Identify above-waterline valley walls (visible terrain)
  5. Fit parametric valley shapes (V, U, asymmetric, parabolic)
  6. Extrapolate below waterline, constrained by dam height & A-E curve
  7. Interpolate cross-sections into a full 3D depth raster

Usage:
    from reservoir.cross_section_extractor import ReservoirCrossSectionExtractor

    extractor = ReservoirCrossSectionExtractor(
        dem_path="/data/dem/srtm_tile.tif",
        reservoir_polygon=reservoir_geom,  # shapely Polygon
        thalweg_line=thalweg_geom,         # shapely LineString
        dam_location=(lon, lat),
        dam_height_m=170.0,
        water_surface_elevation_m=1075.0,
    )
    result = extractor.extract()

Requirements:
    pip install numpy scipy rasterio shapely
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("cross_section")


# -- Parametric valley shape models ------------------------------------------

def v_shape(x, x0, a, b):
    """V-shaped valley: z = a * |x - x0|^b, b > 1 for V, b < 1 for U."""
    return a * np.power(np.abs(x - x0) + 0.1, b)


def u_shape(x, x0, a, sigma):
    """U-shaped valley: Gaussian trough."""
    return a * (1.0 - np.exp(-((x - x0) ** 2) / (2.0 * sigma**2)))


def parabolic(x, x0, a, z_min):
    """Simple parabolic: z = a * (x - x0)^2 + z_min."""
    return a * (x - x0) ** 2 + z_min


def asymmetric_power(x, x0, a_left, b_left, a_right, b_right):
    """Asymmetric valley: different power law for left and right walls."""
    z = np.zeros_like(x, dtype=float)
    left = x <= x0
    right = x > x0
    z[left] = a_left * np.power(np.abs(x[left] - x0) + 0.1, b_left)
    z[right] = a_right * np.power(np.abs(x[right] - x0) + 0.1, b_right)
    return z


VALLEY_MODELS = {
    "v_shape": {"func": v_shape, "n_params": 3, "bounds": ([None, 0.001, 0.3], [None, 100, 3.0])},
    "u_shape": {"func": u_shape, "n_params": 3, "bounds": ([None, 0.1, 10], [None, 500, 5000])},
    "parabolic": {"func": parabolic, "n_params": 3, "bounds": ([None, 1e-6, None], [None, 1.0, None])},
}


@dataclass
class CrossSection:
    """A single cross-section perpendicular to the thalweg."""
    distance_along_thalweg_m: float  # Distance from dam
    center_x: float  # Thalweg point x (in DEM CRS)
    center_y: float  # Thalweg point y (in DEM CRS)
    azimuth_deg: float  # Transect orientation (perpendicular to flow)

    # Raw DEM profile
    offsets_m: np.ndarray = field(default_factory=lambda: np.array([]))  # Distance from thalweg (-left, +right)
    elevations_m: np.ndarray = field(default_factory=lambda: np.array([]))  # Elevation at each sample
    is_water: np.ndarray = field(default_factory=lambda: np.array([]))  # True where pixel is in reservoir

    # Fitted model
    best_model: str = ""  # Name of best-fit model
    model_params: dict = field(default_factory=dict)  # Fitted parameters
    fit_r2: float = 0.0  # Goodness of fit on visible walls
    fit_rmse: float = 0.0  # RMSE on visible walls

    # Extrapolated depth
    extrapolated_elevations_m: np.ndarray = field(default_factory=lambda: np.array([]))
    thalweg_elevation_m: float = 0.0  # Estimated minimum elevation at this transect
    confidence: float = 0.0  # 0-1, based on fit quality and wall visibility


@dataclass
class ExtractionResult:
    """Complete cross-section extraction result for one reservoir."""
    reservoir_id: str
    cross_sections: list  # List[CrossSection]
    thalweg_profile_distances_m: np.ndarray = field(default_factory=lambda: np.array([]))
    thalweg_profile_elevations_m: np.ndarray = field(default_factory=lambda: np.array([]))
    dam_height_m: float = 0.0
    water_surface_elevation_m: float = 0.0
    base_elevation_m: float = 0.0

    # Aggregate metrics
    mean_fit_r2: float = 0.0
    mean_confidence: float = 0.0
    n_valid_sections: int = 0


class ReservoirCrossSectionExtractor:
    """Extract and fit valley cross-sections for a single reservoir.

    Parameters
    ----------
    dem_path : str
        Path to DEM raster (GeoTIFF). Supports SRTM 30m, 3DEP 10m, FABDEM.
    reservoir_polygon : shapely.Polygon
        Reservoir water body polygon (from NHD, PLD, or Sentinel-2 water mask).
    thalweg_line : shapely.LineString
        Main channel flowline through the reservoir (from NHDPlus).
    dam_location : tuple (lon, lat)
        Dam location in the same CRS as the DEM.
    dam_height_m : float
        Dam height in meters (from NID).
    water_surface_elevation_m : float
        Current or reference water surface elevation (from SWOT or USACE).
    transect_spacing_m : float
        Spacing between cross-sections along thalweg (default 100m).
    transect_half_width_m : float
        Half-width of each transect (default: auto from reservoir width).
    sample_spacing_m : float
        Spacing between DEM samples along each transect (default 10m).
    """

    def __init__(
        self,
        dem_path: str,
        reservoir_polygon,
        thalweg_line,
        dam_location: Tuple[float, float],
        dam_height_m: float,
        water_surface_elevation_m: float,
        transect_spacing_m: float = 100.0,
        transect_half_width_m: Optional[float] = None,
        sample_spacing_m: float = 10.0,
    ):
        self.dem_path = dem_path
        self.reservoir_polygon = reservoir_polygon
        self.thalweg_line = thalweg_line
        self.dam_location = dam_location
        self.dam_height_m = dam_height_m
        self.wse = water_surface_elevation_m
        self.base_elevation_m = water_surface_elevation_m - dam_height_m
        self.transect_spacing_m = transect_spacing_m
        self.transect_half_width_m = transect_half_width_m
        self.sample_spacing_m = sample_spacing_m

        self._dem = None
        self._dem_transform = None
        self._dem_crs = None

    def _load_dem(self):
        """Load DEM raster using rasterio."""
        import rasterio

        with rasterio.open(self.dem_path) as src:
            self._dem = src.read(1).astype(np.float32)
            self._dem_transform = src.transform
            self._dem_crs = src.crs
            self._dem_nodata = src.nodata

        # Mask nodata
        if self._dem_nodata is not None:
            self._dem[self._dem == self._dem_nodata] = np.nan

        log.info(f"Loaded DEM: {self._dem.shape}, "
                 f"elev range: {np.nanmin(self._dem):.0f}-{np.nanmax(self._dem):.0f}m")

    def _sample_dem(self, x: float, y: float) -> float:
        """Sample DEM elevation at a coordinate (bilinear interpolation)."""
        from rasterio.transform import rowcol

        try:
            row, col = rowcol(self._dem_transform, x, y)
            if 0 <= row < self._dem.shape[0] and 0 <= col < self._dem.shape[1]:
                return float(self._dem[int(row), int(col)])
        except Exception:
            pass
        return np.nan

    def _point_in_reservoir(self, x: float, y: float) -> bool:
        """Check if a point is within the reservoir polygon."""
        from shapely.geometry import Point
        return self.reservoir_polygon.contains(Point(x, y))

    def _discretize_thalweg(self) -> list:
        """Sample points along thalweg at regular intervals.

        Returns list of (x, y, distance_from_start, azimuth_deg) tuples.
        """
        from shapely.geometry import Point

        # Estimate degrees per meter at this latitude
        dam_lat = self.dam_location[1]
        deg_per_m_x = 1.0 / (111_000 * np.cos(np.radians(dam_lat)))
        deg_per_m_y = 1.0 / 111_000

        total_length = self.thalweg_line.length  # in CRS units (degrees)
        total_length_m = total_length * 111_000  # approximate

        spacing_deg = self.transect_spacing_m * deg_per_m_y

        points = []
        n_points = max(3, int(total_length / spacing_deg))
        fractions = np.linspace(0, 1, n_points)

        for i, frac in enumerate(fractions):
            pt = self.thalweg_line.interpolate(frac, normalized=True)

            # Compute local azimuth from neighboring points
            frac_before = max(0, frac - 0.01)
            frac_after = min(1, frac + 0.01)
            pt_before = self.thalweg_line.interpolate(frac_before, normalized=True)
            pt_after = self.thalweg_line.interpolate(frac_after, normalized=True)

            dx = pt_after.x - pt_before.x
            dy = pt_after.y - pt_before.y
            flow_azimuth = np.degrees(np.arctan2(dx, dy))  # degrees from north

            # Perpendicular to flow
            transect_azimuth = flow_azimuth + 90.0

            distance_m = frac * total_length_m

            points.append((pt.x, pt.y, distance_m, transect_azimuth))

        log.info(f"Discretized thalweg into {len(points)} sample points "
                 f"(total ~{total_length_m:.0f}m)")
        return points

    def _extract_single_transect(
        self,
        center_x: float,
        center_y: float,
        azimuth_deg: float,
        half_width_m: float,
        distance_along_m: float,
    ) -> CrossSection:
        """Extract DEM profile along a single transect."""
        dam_lat = self.dam_location[1]
        deg_per_m_x = 1.0 / (111_000 * np.cos(np.radians(dam_lat)))
        deg_per_m_y = 1.0 / 111_000

        # Generate sample points along the transect
        n_samples = int(2 * half_width_m / self.sample_spacing_m) + 1
        offsets = np.linspace(-half_width_m, half_width_m, n_samples)

        azimuth_rad = np.radians(azimuth_deg)
        dx_per_m = np.sin(azimuth_rad) * deg_per_m_x
        dy_per_m = np.cos(azimuth_rad) * deg_per_m_y

        elevations = np.full(n_samples, np.nan)
        is_water = np.zeros(n_samples, dtype=bool)

        for i, offset in enumerate(offsets):
            px = center_x + offset * dx_per_m
            py = center_y + offset * dy_per_m
            elevations[i] = self._sample_dem(px, py)
            is_water[i] = self._point_in_reservoir(px, py)

        return CrossSection(
            distance_along_thalweg_m=distance_along_m,
            center_x=center_x,
            center_y=center_y,
            azimuth_deg=azimuth_deg,
            offsets_m=offsets,
            elevations_m=elevations,
            is_water=is_water,
        )

    def _fit_valley_shape(self, cs: CrossSection) -> CrossSection:
        """Fit parametric models to visible valley walls and extrapolate.

        Uses the above-waterline portions of the cross-section to fit V, U,
        parabolic, and asymmetric models. Selects the best fit and extrapolates
        below the waterline.
        """
        from scipy.optimize import curve_fit

        offsets = cs.offsets_m
        elevations = cs.elevations_m
        is_water = cs.is_water

        # Get visible (above-waterline) portions
        visible = ~is_water & np.isfinite(elevations)

        # Need points on both sides of the water for a good fit
        center_idx = len(offsets) // 2
        left_visible = visible[:center_idx].sum()
        right_visible = visible[center_idx:].sum()

        if left_visible < 3 or right_visible < 3:
            cs.confidence = 0.1
            cs.best_model = "insufficient_data"
            return cs

        x_vis = offsets[visible]
        z_vis = elevations[visible] - self.wse  # Relative to water surface

        # Only use points above water that show rising terrain
        rising = z_vis > 0
        if rising.sum() < 5:
            cs.confidence = 0.2
            cs.best_model = "flat_terrain"
            return cs

        x_fit = x_vis[rising]
        z_fit = z_vis[rising]

        # Estimate thalweg position (center of water)
        water_offsets = offsets[is_water]
        if len(water_offsets) > 0:
            x0_est = np.median(water_offsets)
        else:
            x0_est = 0.0

        best_r2 = -np.inf
        best_model = None
        best_params = {}
        best_func = None

        # Try each parametric model
        models_to_try = [
            ("v_shape", v_shape, [x0_est, 0.01, 1.0],
             ([x0_est - 500, 0.001, 0.3], [x0_est + 500, 100, 3.0])),
            ("u_shape", u_shape, [x0_est, 50.0, 200.0],
             ([x0_est - 500, 0.1, 10], [x0_est + 500, 500, 5000])),
            ("parabolic", parabolic, [x0_est, 0.001, 0.0],
             ([x0_est - 500, 1e-7, -100], [x0_est + 500, 1.0, 100])),
        ]

        for name, func, p0, bounds in models_to_try:
            try:
                popt, pcov = curve_fit(
                    func, x_fit, z_fit,
                    p0=p0, bounds=bounds,
                    maxfev=5000,
                )

                # Evaluate fit
                z_pred = func(x_fit, *popt)
                ss_res = np.sum((z_fit - z_pred) ** 2)
                ss_tot = np.sum((z_fit - np.mean(z_fit)) ** 2)
                r2 = 1.0 - ss_res / max(ss_tot, 1e-10)
                rmse = np.sqrt(np.mean((z_fit - z_pred) ** 2))

                if r2 > best_r2:
                    best_r2 = r2
                    best_model = name
                    best_params = {f"p{i}": float(p) for i, p in enumerate(popt)}
                    best_func = func
                    best_popt = popt
                    best_rmse = rmse

            except (RuntimeError, ValueError):
                continue

        if best_model is None:
            cs.confidence = 0.1
            cs.best_model = "fit_failed"
            return cs

        cs.best_model = best_model
        cs.model_params = best_params
        cs.fit_r2 = float(best_r2)
        cs.fit_rmse = float(best_rmse)

        # Extrapolate below waterline
        extrapolated = np.copy(elevations)
        water_mask = is_water & np.isfinite(elevations)

        # For water pixels, predict elevation from fitted model
        x_water = offsets[is_water]
        z_pred_water = best_func(x_water, *best_popt)

        # Convert from relative-to-WSE back to absolute
        z_pred_abs = self.wse - np.abs(z_pred_water)  # Below water surface

        # Constrain: can't go below dam base
        z_pred_abs = np.clip(z_pred_abs, self.base_elevation_m, self.wse)

        extrapolated[is_water] = z_pred_abs
        cs.extrapolated_elevations_m = extrapolated

        # Thalweg elevation at this transect
        thalweg_idx = np.argmin(extrapolated) if len(extrapolated) > 0 else 0
        cs.thalweg_elevation_m = float(np.nanmin(extrapolated))

        # Confidence scoring
        n_visible_wall = rising.sum()
        wall_coverage = n_visible_wall / max(len(offsets), 1)
        cs.confidence = np.clip(
            0.3 * best_r2 + 0.3 * wall_coverage + 0.2 * (1.0 - best_rmse / 50.0) + 0.2,
            0.0, 1.0,
        )

        return cs

    def _estimate_thalweg_profile(
        self,
        cross_sections: list,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Estimate the longitudinal thalweg profile (deepest channel).

        Uses the extrapolated minimum elevation at each cross-section,
        constrained to be monotonically decreasing toward the dam and
        bounded by dam base elevation.
        """
        distances = np.array([cs.distance_along_thalweg_m for cs in cross_sections])
        min_elevs = np.array([cs.thalweg_elevation_m for cs in cross_sections])

        # Replace invalid with interpolation
        valid = np.isfinite(min_elevs) & (min_elevs > 0)
        if valid.sum() < 2:
            log.warning("Too few valid thalweg points for profile estimation")
            return distances, np.full_like(distances, self.base_elevation_m)

        # Interpolate missing values
        from scipy.interpolate import interp1d
        interp = interp1d(
            distances[valid], min_elevs[valid],
            kind="linear", fill_value="extrapolate",
        )
        profile = interp(distances)

        # Constrain: bounded by [base_elevation, WSE]
        profile = np.clip(profile, self.base_elevation_m, self.wse)

        # Enforce: generally decreasing toward dam (distance=0 is upstream)
        # Allow local variations but overall trend should decrease
        # Use isotonic regression for monotonic smoothing
        try:
            from sklearn.isotonic import IsotonicRegression
            ir = IsotonicRegression(increasing=False)
            profile_smooth = ir.fit_transform(distances, profile)
            # Blend: 70% monotonic, 30% original (to preserve local features)
            profile = 0.7 * profile_smooth + 0.3 * profile
        except ImportError:
            # Fallback: simple moving average
            kernel_size = max(3, len(profile) // 10)
            if kernel_size % 2 == 0:
                kernel_size += 1
            profile = np.convolve(
                profile, np.ones(kernel_size) / kernel_size, mode="same"
            )

        profile = np.clip(profile, self.base_elevation_m, self.wse)
        return distances, profile

    def extract(self, reservoir_id: str = "unknown") -> ExtractionResult:
        """Run the full cross-section extraction pipeline.

        Returns ExtractionResult with all cross-sections, fitted models,
        thalweg profile, and aggregate metrics.
        """
        log.info(f"Extracting cross-sections for reservoir {reservoir_id}")
        log.info(f"  Dam height: {self.dam_height_m:.1f}m, WSE: {self.wse:.1f}m, "
                 f"Base elev: {self.base_elevation_m:.1f}m")

        # Load DEM
        self._load_dem()

        # Determine transect width
        if self.transect_half_width_m is None:
            # Auto: use reservoir polygon bounds
            bounds = self.reservoir_polygon.bounds  # (minx, miny, maxx, maxy)
            width_deg = bounds[2] - bounds[0]
            height_deg = bounds[3] - bounds[1]
            max_extent_m = max(width_deg, height_deg) * 111_000
            self.transect_half_width_m = max(500, min(max_extent_m, 5000))
            log.info(f"  Auto transect half-width: {self.transect_half_width_m:.0f}m")

        # Step 1: Discretize thalweg
        thalweg_points = self._discretize_thalweg()

        # Step 2+3: Extract cross-sections
        cross_sections = []
        for x, y, dist, azimuth in thalweg_points:
            cs = self._extract_single_transect(
                x, y, azimuth, self.transect_half_width_m, dist,
            )
            cross_sections.append(cs)

        log.info(f"  Extracted {len(cross_sections)} raw cross-sections")

        # Step 4+5: Fit valley shapes
        for i, cs in enumerate(cross_sections):
            cross_sections[i] = self._fit_valley_shape(cs)

        valid = [cs for cs in cross_sections if cs.fit_r2 > 0.3]
        log.info(f"  Fitted {len(valid)}/{len(cross_sections)} cross-sections "
                 f"with R² > 0.3")

        # Step 6: Estimate thalweg profile
        thalweg_dists, thalweg_elevs = self._estimate_thalweg_profile(cross_sections)

        # Update cross-section thalweg elevations from smoothed profile
        for i, cs in enumerate(cross_sections):
            if i < len(thalweg_elevs):
                cs.thalweg_elevation_m = float(thalweg_elevs[i])

        # Aggregate metrics
        fit_r2s = [cs.fit_r2 for cs in cross_sections if cs.fit_r2 > 0]
        confidences = [cs.confidence for cs in cross_sections]

        result = ExtractionResult(
            reservoir_id=reservoir_id,
            cross_sections=cross_sections,
            thalweg_profile_distances_m=thalweg_dists,
            thalweg_profile_elevations_m=thalweg_elevs,
            dam_height_m=self.dam_height_m,
            water_surface_elevation_m=self.wse,
            base_elevation_m=self.base_elevation_m,
            mean_fit_r2=float(np.mean(fit_r2s)) if fit_r2s else 0.0,
            mean_confidence=float(np.mean(confidences)) if confidences else 0.0,
            n_valid_sections=len(valid),
        )

        log.info(f"  Result: {result.n_valid_sections} valid sections, "
                 f"mean R²={result.mean_fit_r2:.3f}, "
                 f"mean confidence={result.mean_confidence:.3f}")

        # Model type distribution
        model_counts = {}
        for cs in cross_sections:
            model_counts[cs.best_model] = model_counts.get(cs.best_model, 0) + 1
        log.info(f"  Model types: {model_counts}")

        return result

    def result_to_ae_curve(
        self,
        result: ExtractionResult,
        n_elevation_steps: int = 100,
    ) -> pd.DataFrame:
        """Convert extraction result to an Area-Elevation curve.

        For each elevation between base and WSE, compute the total reservoir
        area by summing the widths of all cross-sections at that elevation.
        """
        import pandas as pd

        elevations = np.linspace(
            result.base_elevation_m,
            result.water_surface_elevation_m,
            n_elevation_steps,
        )

        areas = np.zeros_like(elevations)

        for cs in result.cross_sections:
            if len(cs.extrapolated_elevations_m) == 0:
                continue

            for j, elev in enumerate(elevations):
                # Width at this elevation = distance between left and right
                # points where extrapolated elevation equals this level
                extrap = cs.extrapolated_elevations_m
                above = extrap <= elev  # Points that are below this elevation (flooded)
                if above.any():
                    # Width is the span of flooded offsets
                    flooded_offsets = cs.offsets_m[above]
                    width = flooded_offsets.max() - flooded_offsets.min()
                    # Add width * transect spacing to get area contribution
                    areas[j] += width * self.transect_spacing_m

        # Convert to km2
        areas_km2 = areas / 1e6

        # Cumulative volume via trapezoidal integration
        dz = np.diff(elevations)
        avg_areas = (areas[:-1] + areas[1:]) / 2.0
        volumes = np.cumsum(avg_areas * dz)
        volumes = np.insert(volumes, 0, 0.0)
        volumes_m3 = volumes  # already in m3

        df = pd.DataFrame({
            "elevation_m": elevations,
            "area_m2": areas,
            "area_km2": areas_km2,
            "volume_m3": volumes_m3,
            "volume_acft": volumes_m3 / 1233.48184,
        })

        return df
