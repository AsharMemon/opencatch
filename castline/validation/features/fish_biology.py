"""Fish biology feature engineering for bass catch-rate prediction.

Computes location-independent features derived from fisheries science
that explain bass behavior from environmental conditions alone. These
features are critical for leave-one-location-out (LOO) generalization
because they encode *why* fish respond to conditions, not *where*.

Primary drivers modeled:
1. Spawn cycle (water temperature phase transitions)
2. Metabolic rate (Q10 temperature dependence)
3. Dissolved oxygen comfort (estimated or measured)
4. Barometric pressure response (frontal passage effects)
5. Photoperiod / light conditions
6. Moon phase / solunar feeding windows
7. Seasonal pattern quality
8. Wind-induced mixing
9. Multi-factor stability index

Literature references are cited inline. Key sources:
- Cooke et al. (2003) "Activity and energetics of free-ranging bass"
- Suski & Ridgway (2009) "Temperature physiology of Micropterus"
- Niimi & Beamish (1974) "Q10 in freshwater fish metabolism"
- Schramm et al. (1998) "Seasonal patterns in bass tournament catch"
- Knight (1936) "Moon Up — Moon Down" (Solunar Theory)
- USGS Water Resources dissolved oxygen saturation tables
"""
from __future__ import annotations

import math

NaN = float("nan")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _isnan(v: float) -> bool:
    """Fast NaN check without importing numpy."""
    return v != v


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def _gaussian(x: float, mu: float, sigma: float) -> float:
    """Unnormalized Gaussian for smooth scoring."""
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


# ---------------------------------------------------------------------------
# 1. Spawn timing features
# ---------------------------------------------------------------------------

def _spawn_phase_features(water_temp_c: float) -> dict[str, float]:
    """Compute bass spawn-cycle features from water temperature.

    Largemouth bass (Micropterus salmoides) spawn phases by water temp:
      Pre-spawn:  12-17 C  (54-62 F) — aggressive feeding, staging
      Spawn:      17-22 C  (62-72 F) — bedding, nest guarding
      Post-spawn: 22-26 C  (72-78 F) — recovery, lethargic
      Summer:     >26 C    (>78 F)   — normal summer pattern

    Refs: Kramer & Smith (1962); Heidinger (1975); Philipp et al. (1997)
    """
    if _isnan(water_temp_c):
        return {
            "spawn_phase": NaN,
            "spawn_progress": NaN,
            "prespawn_intensity": NaN,
            "spawn_bedding_prob": NaN,
            "postspawn_lethargy": NaN,
        }

    t = water_temp_c

    # spawn_phase: continuous 0-1 encoding of the full spawn cycle
    # 0.0 = deep winter (< 10 C), 0.25 = peak pre-spawn (~15 C),
    # 0.50 = peak spawn (~19 C), 0.75 = post-spawn (~24 C), 1.0 = summer
    if t < 7:
        phase = 0.0
    elif t < 12:
        phase = 0.10 * (t - 7) / 5.0  # winter -> early pre-spawn
    elif t < 15.5:
        phase = 0.10 + 0.15 * (t - 12) / 3.5  # early -> peak pre-spawn
    elif t < 17:
        phase = 0.25 + 0.10 * (t - 15.5) / 1.5  # pre-spawn -> spawn onset
    elif t < 22.2:
        phase = 0.35 + 0.25 * (t - 17) / 5.2  # spawn
    elif t < 26:
        phase = 0.60 + 0.20 * (t - 22.2) / 3.8  # post-spawn
    else:
        phase = min(1.0, 0.80 + 0.20 * (t - 26) / 4.0)  # summer

    # spawn_progress: linear 0-1 from 10 C (first movement) to 24 C (spawn done)
    spawn_progress = _clamp((t - 10.0) / 14.0)

    # prespawn_intensity: peaks at 14-16 C (58-61 F) — the classic "pre-spawn bite"
    # This is the single most productive fishing period of the year.
    # Ref: Schramm et al. (1998), tournament data shows median weight peaks
    prespawn_intensity = _gaussian(t, mu=15.0, sigma=2.0)

    # spawn_bedding_prob: probability fish are actively on beds (17-22 C)
    # Gaussian centered at 19.5 C with tight window
    spawn_bedding_prob = _gaussian(t, mu=19.5, sigma=2.5)

    # postspawn_lethargy: fish are exhausted after spawning (22-27 C)
    # This depresses catch rates. Peaks around 24 C.
    postspawn_lethargy = _gaussian(t, mu=24.0, sigma=2.0)

    return {
        "spawn_phase": phase,
        "spawn_progress": spawn_progress,
        "prespawn_intensity": prespawn_intensity,
        "spawn_bedding_prob": spawn_bedding_prob,
        "postspawn_lethargy": postspawn_lethargy,
    }


# ---------------------------------------------------------------------------
# 2. Metabolic rate features
# ---------------------------------------------------------------------------

def _metabolic_features(water_temp_c: float) -> dict[str, float]:
    """Compute metabolic rate and feeding activity indices.

    Fish are ectotherms: metabolic rate scales with temperature via the
    Arrhenius/Q10 relationship. For largemouth bass:
      Q10 ~ 2.3 (Niimi & Beamish, 1974)
      Optimal activity: 24-28 C (75-82 F)
      Metabolic peak: ~27 C (80 F)
      Upper avoidance: >33 C (91 F) — thermal stress
      Lower threshold: ~7 C (45 F) — near torpor

    The metabolic_rate_index reflects how active and hungry fish are.
    The feeding_window_score reflects optimal foraging temperature range.
    """
    if _isnan(water_temp_c):
        return {
            "metabolic_rate_index": NaN,
            "feeding_window_score": NaN,
            "thermal_stress_index": NaN,
            "activity_level": NaN,
        }

    t = water_temp_c

    # Q10-based metabolic rate (normalized to reference temp 25 C)
    # M(T) = M(25) * Q10^((T - 25) / 10)
    # Ref: Niimi & Beamish (1974), Cooke et al. (2001)
    q10 = 2.3
    reference_temp = 25.0
    raw_rate = q10 ** ((t - reference_temp) / 10.0)

    # Normalize: at 25 C this is 1.0, at 15 C ~0.43, at 5 C ~0.19
    # Cap at upper thermal stress limit where metabolism breaks down
    if t > 33:
        # Above critical thermal maximum, metabolic efficiency collapses
        # Ref: Coutant (1975), thermal tolerance of centrarchids
        stress_penalty = max(0.1, 1.0 - (t - 33) / 7.0)
        raw_rate *= stress_penalty

    metabolic_rate_index = _clamp(raw_rate / 1.5, 0.0, 1.0)  # normalize ~0-1

    # Feeding window score: optimal foraging range is 18-28 C
    # Bass feed most efficiently when metabolic demand is high but not stressed
    # Ref: Suski & Ridgway (2009)
    feeding_center = 23.0  # C — ideal feeding temperature
    feeding_sigma = 6.0
    feeding_base = _gaussian(t, mu=feeding_center, sigma=feeding_sigma)

    # Suppress at extremes
    if t < 7:
        feeding_base *= max(0.05, t / 7.0)
    elif t > 32:
        feeding_base *= max(0.1, 1.0 - (t - 32) / 8.0)

    feeding_window_score = _clamp(feeding_base)

    # Thermal stress index: how far from optimal comfort range (18-28 C)
    if 18 <= t <= 28:
        thermal_stress = 0.0
    elif t < 18:
        thermal_stress = _clamp((18 - t) / 18.0)  # increases toward 0 C
    else:
        thermal_stress = _clamp((t - 28) / 12.0)  # increases toward 40 C

    # Activity level: composite of metabolic rate and feeding potential
    # Low at extremes, high in optimal range
    activity_level = _clamp(0.6 * metabolic_rate_index + 0.4 * feeding_window_score)

    return {
        "metabolic_rate_index": metabolic_rate_index,
        "feeding_window_score": feeding_window_score,
        "thermal_stress_index": thermal_stress,
        "activity_level": activity_level,
    }


# ---------------------------------------------------------------------------
# 3. Dissolved oxygen features
# ---------------------------------------------------------------------------

def _dissolved_oxygen_features(
    water_temp_c: float,
    dissolved_oxygen_mgL: float,
) -> dict[str, float]:
    """Compute dissolved oxygen comfort features.

    Bass require >3 mg/L to survive and prefer 7-9 mg/L.
    DO < 5 mg/L causes behavioral avoidance (fish move to find better water).

    If measured DO is unavailable, estimate from water temperature using
    the Benson & Krause (1984) saturation equation at sea level:
      DO_sat = 14.62 - 0.3898*T + 0.006969*T^2 - 0.00005896*T^3
    where T is in Celsius.

    Ref: Benson & Krause (1984), USGS Water Resources tables
    Ref: Coutant (1985), DO thresholds for centrarchid fishes
    """
    # Estimate saturated DO from temperature
    if _isnan(water_temp_c):
        return {
            "do_estimated_mgL": NaN,
            "do_comfort_index": NaN,
            "do_stress_flag": NaN,
        }

    t = water_temp_c
    # Benson & Krause (1984) saturation formula (fresh water, 1 atm)
    do_saturated = 14.62 - 0.3898 * t + 0.006969 * t**2 - 0.00005896 * t**3
    do_saturated = max(0.0, do_saturated)

    # Use measured DO if available, otherwise estimate at ~90% saturation
    # (typical for well-mixed surface waters)
    if not _isnan(dissolved_oxygen_mgL) and dissolved_oxygen_mgL > 0:
        do_actual = dissolved_oxygen_mgL
    else:
        do_actual = do_saturated * 0.90  # typical surface water

    # Comfort index: peaks at 7-9 mg/L, drops at low and very high DO
    # Ref: Coutant (1985), bass prefer 7-9 mg/L
    if do_actual >= 7.0:
        # Comfortable range — slight decline at very high supersaturation
        comfort = _clamp(1.0 - max(0, do_actual - 12.0) / 8.0)
    elif do_actual >= 5.0:
        # Marginal — fish are present but may be stressed
        comfort = 0.5 + 0.5 * (do_actual - 5.0) / 2.0
    elif do_actual >= 3.0:
        # Stressful — avoidance behavior, fish concentrate at DO refuges
        comfort = 0.2 + 0.3 * (do_actual - 3.0) / 2.0
    else:
        # Lethal range — fish will die or flee
        comfort = max(0.0, 0.2 * do_actual / 3.0)

    # Binary-ish stress flag (smooth)
    do_stress = _clamp(1.0 - (do_actual - 3.0) / 4.0) if do_actual < 7.0 else 0.0

    return {
        "do_estimated_mgL": do_actual,
        "do_comfort_index": comfort,
        "do_stress_flag": max(0.0, do_stress),
    }


# ---------------------------------------------------------------------------
# 4. Barometric pressure features
# ---------------------------------------------------------------------------

def _pressure_features(
    pressure_mb: float,
    pressure_delta_6h: float,
    pressure_delta_24h: float,
) -> dict[str, float]:
    """Compute barometric pressure influence on bass feeding.

    Well-documented in angling literature and some scientific studies:
    - Falling pressure (pre-frontal): triggers aggressive feeding
    - Stable high pressure: consistent, good conditions
    - Rising pressure (post-frontal): suppresses feeding 24-48h
    - Rapid drops (>3 mb/6h): strongest feeding trigger

    Mechanism: bass detect pressure changes via swim bladder.
    Falling pressure = expanding swim bladder = discomfort = feed to adjust.

    Ref: Kraft & Peters (1988), barometric pressure and largemouth bass catch
    Ref: Schramm et al. (1998), tournament catch vs. weather variables
    """
    result: dict[str, float] = {}

    if _isnan(pressure_mb) and _isnan(pressure_delta_6h):
        return {
            "pressure_phase": NaN,
            "pressure_fishing_quality": NaN,
            "pressure_change_rate": NaN,
            "prefrontal_bite_index": NaN,
            "postfrontal_suppression": NaN,
        }

    # Pressure phase: categorical encoded as continuous
    # -1 = rapidly falling, 0 = stable, +1 = rapidly rising
    delta = pressure_delta_6h if not _isnan(pressure_delta_6h) else 0.0
    # Normalize: typical range is -6 to +6 mb/6h, extremes beyond that
    pressure_phase = _clamp(delta / 6.0, -1.0, 1.0)

    # Short-term rate of change (mb per hour equivalent)
    pressure_change_rate = delta / 6.0 if not _isnan(pressure_delta_6h) else NaN

    # Fishing quality score
    # Falling pressure = best, stable high = good, rising = worst
    if delta < -3.0:
        quality = 0.95  # Rapid drop — best pre-frontal bite
    elif delta < -1.5:
        quality = 0.85  # Moderate drop — very good
    elif delta < -0.5:
        quality = 0.75  # Slow drop — good
    elif delta <= 0.5:
        # Stable — quality depends on absolute pressure
        if not _isnan(pressure_mb):
            if pressure_mb > 1022:
                quality = 0.70  # Stable high — good
            elif pressure_mb > 1013:
                quality = 0.55  # Stable normal — average
            else:
                quality = 0.40  # Stable low — below average
        else:
            quality = 0.55
    elif delta <= 1.5:
        quality = 0.30  # Slow rise — post-frontal beginning
    elif delta <= 3.0:
        quality = 0.20  # Moderate rise — post-frontal suppression
    else:
        quality = 0.10  # Rapid rise — severe post-frontal lockjaw

    # Consider 24h trend for sustained frontal effects
    if not _isnan(pressure_delta_24h):
        if pressure_delta_24h > 4.0 and delta > 0:
            # Sustained rising over 24h — deep post-frontal. Further suppress.
            quality *= 0.8
        elif pressure_delta_24h < -4.0 and delta < 0:
            # Sustained falling — strong approaching front. Boost slightly.
            quality = min(1.0, quality * 1.1)

    # Pre-frontal bite index: how strong is the pre-frontal feeding trigger?
    # Peaks when pressure is falling rapidly
    prefrontal = _clamp(-delta / 4.0) if delta < 0 else 0.0

    # Post-frontal suppression: how suppressed are fish?
    # Peaks 12-24h after frontal passage (rising pressure)
    postfrontal = _clamp(delta / 4.0) if delta > 0 else 0.0
    # Amplify if 24h delta confirms sustained rise
    if not _isnan(pressure_delta_24h) and pressure_delta_24h > 2.0:
        postfrontal = min(1.0, postfrontal * 1.3)

    return {
        "pressure_phase": pressure_phase,
        "pressure_fishing_quality": _clamp(quality),
        "pressure_change_rate": pressure_change_rate,
        "prefrontal_bite_index": prefrontal,
        "postfrontal_suppression": postfrontal,
    }


# ---------------------------------------------------------------------------
# 5. Photoperiod / light features
# ---------------------------------------------------------------------------

def _photoperiod_features(
    day_of_year: int,
    latitude: float,
    cloud_cover_pct: float,
) -> dict[str, float]:
    """Compute photoperiod and light-condition features.

    Day length is the primary trigger for seasonal fish behavior:
    - Increasing photoperiod triggers pre-spawn migration
    - Rate of change of day length matters more than absolute length
    - Cloud cover affects light penetration (overcast = better topwater)

    Day length approximation using the CBM model:
      declination = 23.45 * sin(2*pi*(284 + doy)/365)
      hour_angle = arccos(-tan(lat)*tan(decl))
      day_length = 2 * hour_angle / 15

    Ref: Forsythe et al. (1995), day length model for ecological applications
    Ref: Lusk et al. (1978), photoperiod and bass reproduction
    """
    if _isnan(latitude) or day_of_year < 1 or day_of_year > 366:
        return {
            "day_length_hours": NaN,
            "photoperiod_change_rate": NaN,
            "light_penetration_index": NaN,
            "photoperiod_spawn_trigger": NaN,
        }

    lat_rad = math.radians(latitude)

    def _day_length(doy: int) -> float:
        """Forsythe et al. (1995) day length model."""
        # Solar declination
        decl = 23.45 * math.sin(math.radians(360 / 365 * (284 + doy)))
        decl_rad = math.radians(decl)

        # Hour angle at sunrise/sunset
        cos_ha = -math.tan(lat_rad) * math.tan(decl_rad)
        cos_ha = _clamp(cos_ha, -1.0, 1.0)  # handle arctic/antarctic edge cases
        hour_angle = math.degrees(math.acos(cos_ha))

        return 2.0 * hour_angle / 15.0  # convert to hours

    day_length = _day_length(day_of_year)

    # Rate of change: difference from previous day (hours/day)
    # This is the actual biological trigger — fish respond to changing day length
    prev_day = max(1, day_of_year - 1)
    next_day = min(365, day_of_year + 1)
    photoperiod_change = (_day_length(next_day) - _day_length(prev_day)) / 2.0

    # Light penetration index: combines cloud cover and day length
    # Overcast = lower penetration = better for certain fishing (topwater, shallow)
    cloud = cloud_cover_pct if not _isnan(cloud_cover_pct) else 50.0
    light_penetration = (1.0 - cloud / 100.0) * (day_length / 16.0)
    light_penetration = _clamp(light_penetration)

    # Photoperiod spawn trigger: peaks when day length is increasing through
    # 12-14 hours — the critical window that initiates spawning behavior
    # Ref: Lusk et al. (1978)
    spawn_trigger = 0.0
    if photoperiod_change > 0 and 11.5 <= day_length <= 14.5:
        # Increasing day length in the spawn-trigger window
        # Peak at ~13h day length with positive change rate
        length_factor = _gaussian(day_length, mu=13.0, sigma=1.5)
        rate_factor = min(1.0, photoperiod_change / 0.04)  # max change ~3.5 min/day
        spawn_trigger = length_factor * rate_factor

    return {
        "day_length_hours": day_length,
        "photoperiod_change_rate": photoperiod_change,
        "light_penetration_index": light_penetration,
        "photoperiod_spawn_trigger": spawn_trigger,
    }


# ---------------------------------------------------------------------------
# 6. Moon / solunar features
# ---------------------------------------------------------------------------

def _moon_features(moon_phase: float) -> dict[str, float]:
    """Compute moon-phase fishing features.

    Moon phase effects on bass fishing:
    - New moon & full moon: strongest solunar influence
    - Full moon: more night feeding -> potentially less day feeding
    - New moon: concentrated daytime feeding (no nighttime light)
    - Quarter moons: weakest solunar effect

    The solunar effect is controversial in science but consistently
    shows up in large tournament datasets.

    Ref: Knight (1936), Solunar Theory
    Ref: Grover & Olla (1983), lunar periodicity in fish behavior
    Ref: Hanson et al. (2008), lunar influence on centrarchid activity

    moon_phase input: 0.0 = new moon, 0.5 = full moon, 1.0 = next new moon
    """
    if _isnan(moon_phase):
        return {
            "moon_phase_score": NaN,
            "solunar_period_quality": NaN,
            "moon_night_feeding_adj": NaN,
            "moon_phase_sin": NaN,
            "moon_phase_cos": NaN,
        }

    phase = moon_phase % 1.0  # ensure 0-1 range

    # Solunar quality: peaks at new (0) and full (0.5) moon
    # cos(4*pi*phase) peaks at 0.0 and 0.5
    solunar_raw = (math.cos(4.0 * math.pi * phase) + 1.0) / 2.0

    # Moon phase score: overall fishing quality from moon
    # New moon is slightly better than full for daytime fishing
    # because fish haven't fed as much at night
    if phase < 0.1 or phase > 0.9:
        # Near new moon — best for daytime
        moon_score = 0.85 + 0.15 * solunar_raw
    elif 0.4 < phase < 0.6:
        # Near full moon — good solunar but night feeding offsets
        moon_score = 0.65 + 0.15 * solunar_raw
    else:
        # Quarter moons — weakest solunar
        moon_score = 0.40 + 0.20 * solunar_raw

    # Night feeding adjustment: full moon = more night feeding = less day hunger
    # 0 = no night feeding offset, 1 = maximal night feeding (full moon)
    # This acts as a mild suppressor for daytime catch rates
    # Illumination peaks at full moon (phase=0.5)
    illumination = (1.0 - math.cos(2.0 * math.pi * phase)) / 2.0
    night_feeding_adj = illumination * 0.15  # max 15% suppression

    # Cyclical encoding for ML models (avoids discontinuity at 0/1)
    moon_sin = math.sin(2.0 * math.pi * phase)
    moon_cos = math.cos(2.0 * math.pi * phase)

    return {
        "moon_phase_score": _clamp(moon_score),
        "solunar_period_quality": solunar_raw,
        "moon_night_feeding_adj": night_feeding_adj,
        "moon_phase_sin": moon_sin,
        "moon_phase_cos": moon_cos,
    }


# ---------------------------------------------------------------------------
# 7. Seasonal pattern features
# ---------------------------------------------------------------------------

def _seasonal_features(
    day_of_year: int,
    water_temp_c: float,
    latitude: float,
) -> dict[str, float]:
    """Compute seasonal fishing pattern features.

    Bass fishing quality follows a seasonal pattern driven by forage
    availability, spawn cycle, and thermal stratification:

      Spring (pre-spawn/spawn): HIGH — peak catch rates
      Post-spawn transition:    LOW — recovery period
      Summer:                   MODERATE — deep structure, early/late bite
      Fall turnover:            HIGH — shad migration triggers feeding frenzy
      Winter:                   LOW — deep, slow, minimal movement

    Ref: Schramm et al. (1998), seasonal catch rate patterns in bass tournaments
    Ref: Sammons & Bettoli (2000), seasonal distribution of largemouth bass
    """
    if day_of_year < 1 or day_of_year > 366:
        return {
            "season_quality_index": NaN,
            "seasonal_pattern_phase": NaN,
            "fall_feed_intensity": NaN,
            "winter_dormancy": NaN,
        }

    # Adjust day-of-year by latitude to account for earlier seasons in the south
    # Southern US (lat ~30) has spring ~40 days earlier than northern US (lat ~45)
    lat_shift = (latitude - 37.5) * 2.67  # days per degree latitude
    adjusted_doy = (day_of_year - lat_shift) % 365

    # Seasonal quality curve — empirical from tournament data
    # Dual peaks: spring (day ~100) and fall (day ~280)
    spring_peak = _gaussian(adjusted_doy, mu=100, sigma=30)
    fall_peak = _gaussian(adjusted_doy, mu=280, sigma=25)
    # Summer moderate
    summer_base = _gaussian(adjusted_doy, mu=190, sigma=50) * 0.55
    # Winter low
    winter_trough = 0.15

    season_quality = max(
        winter_trough,
        spring_peak * 0.95,
        fall_peak * 0.90,
        summer_base,
    )

    # Seasonal phase as continuous 0-1: winter=0, spring=0.25, summer=0.5, fall=0.75
    seasonal_phase = (adjusted_doy % 365) / 365.0

    # Fall feeding frenzy: peaks when water temp drops through 18-22 C in autumn
    # Bass gorge on shad before winter. Strongest signal in DOY 250-320.
    fall_feed = 0.0
    if 240 <= adjusted_doy <= 330 and not _isnan(water_temp_c):
        if 14 <= water_temp_c <= 22:
            doy_factor = _gaussian(adjusted_doy, mu=285, sigma=25)
            temp_factor = _gaussian(water_temp_c, mu=18.0, sigma=3.0)
            fall_feed = doy_factor * temp_factor
    elif 240 <= adjusted_doy <= 330 and _isnan(water_temp_c):
        fall_feed = _gaussian(adjusted_doy, mu=285, sigma=25) * 0.5

    # Winter dormancy: how dormant are fish?
    # Peaks in deep winter (DOY 0-60 and 330-365) and at cold temps
    dormancy_doy = max(
        _gaussian(adjusted_doy, mu=15, sigma=30),
        _gaussian(adjusted_doy, mu=350, sigma=30),
    )
    if not _isnan(water_temp_c):
        dormancy_temp = _clamp(1.0 - (water_temp_c - 4.0) / 8.0) if water_temp_c < 12 else 0.0
        winter_dormancy = max(dormancy_doy * 0.5, dormancy_temp)
    else:
        winter_dormancy = dormancy_doy * 0.6

    return {
        "season_quality_index": _clamp(season_quality),
        "seasonal_pattern_phase": seasonal_phase,
        "fall_feed_intensity": _clamp(fall_feed),
        "winter_dormancy": _clamp(winter_dormancy),
    }


# ---------------------------------------------------------------------------
# 8. Wind mixing features
# ---------------------------------------------------------------------------

def _wind_features(
    wind_speed_kph: float,
    water_temp_c: float,
    air_temp_c: float,
) -> dict[str, float]:
    """Compute wind-related fishing features.

    Wind effects on bass fishing:
    - Moderate wind (10-25 kph): breaks up surface, concentrates baitfish,
      reduces fish wariness — improves catch rates
    - Wind creates current that pushes plankton/baitfish to windblown banks
    - Wind mixes surface water, can improve DO in warm conditions
    - Surface chop reduces light penetration — triggers feeding
    - Excessive wind (>35 kph): makes fishing difficult

    Also estimates effective surface temp mixing from wind chill on water.

    Ref: Jones & Hoyer (1982), wind effects on fish distribution in lakes
    """
    if _isnan(wind_speed_kph):
        return {
            "wind_mixing_index": NaN,
            "wind_fishing_quality": NaN,
        }

    w = wind_speed_kph

    # Wind mixing index: how much is wind mixing the water column?
    # Proportional to wind speed cubed (Langmuir circulation threshold)
    # Ref: Imberger & Patterson (1990), physical limnology
    if w < 5:
        mixing = 0.05
    elif w < 15:
        mixing = 0.05 + 0.35 * (w - 5) / 10.0
    elif w < 30:
        mixing = 0.40 + 0.45 * (w - 15) / 15.0
    else:
        mixing = min(1.0, 0.85 + 0.15 * (w - 30) / 20.0)

    # Wind fishing quality: optimal at moderate speeds
    # Too calm = fish wary, too rough = can't fish effectively
    wind_quality = _gaussian(w, mu=18.0, sigma=10.0)

    # Bonus: warm wind over cool water can improve conditions
    if not _isnan(air_temp_c) and not _isnan(water_temp_c):
        if air_temp_c > water_temp_c + 3:
            # Warm wind — can warm shallows, activate baitfish
            wind_quality = min(1.0, wind_quality + 0.05)
        elif air_temp_c < water_temp_c - 5:
            # Cold wind — can chill surface, push fish deeper
            wind_quality *= 0.9

    return {
        "wind_mixing_index": _clamp(mixing),
        "wind_fishing_quality": _clamp(wind_quality),
    }


# ---------------------------------------------------------------------------
# 9. Conditions stability index
# ---------------------------------------------------------------------------

def _stability_features(
    pressure_delta_6h: float,
    pressure_delta_24h: float,
    wind_speed_kph: float,
    water_temp_c: float,
    air_temp_c: float,
) -> dict[str, float]:
    """Compute environmental stability index.

    Stable conditions = predictable fish behavior = more consistent catch.
    Unstable conditions (fronts, variable wind) = erratic fish behavior.

    Components:
    - Pressure stability (small delta = stable)
    - Temperature consistency (water-air delta small = stable weather)
    - Wind consistency (moderate and steady vs. gusty)

    Ref: Colvin (1991), environmental stability and sportfish catch rates
    """
    components = 0
    stability_sum = 0.0

    # Pressure stability
    if not _isnan(pressure_delta_6h):
        # Perfect stability = delta near 0
        p_stab = _gaussian(pressure_delta_6h, mu=0.0, sigma=2.0)
        stability_sum += p_stab
        components += 1

    if not _isnan(pressure_delta_24h):
        p24_stab = _gaussian(pressure_delta_24h, mu=0.0, sigma=4.0)
        stability_sum += p24_stab
        components += 1

    # Temperature consistency: small air-water differential = stable weather
    if not _isnan(water_temp_c) and not _isnan(air_temp_c):
        temp_diff = abs(air_temp_c - water_temp_c)
        t_stab = _gaussian(temp_diff, mu=0.0, sigma=8.0)
        stability_sum += t_stab
        components += 1

    # Wind stability: moderate wind is more stable than calm or very windy
    if not _isnan(wind_speed_kph):
        # Calm or extreme wind both suggest instability or difficult conditions
        w_stab = _gaussian(wind_speed_kph, mu=12.0, sigma=10.0)
        stability_sum += w_stab
        components += 1

    if components == 0:
        return {"conditions_stability_index": NaN}

    return {
        "conditions_stability_index": _clamp(stability_sum / components),
    }


# ---------------------------------------------------------------------------
# 10. Composite interaction features
# ---------------------------------------------------------------------------

def _interaction_features(
    sub_features: dict[str, float],
    water_temp_c: float,
    pressure_delta_6h: float,
) -> dict[str, float]:
    """Compute interaction features that combine multiple biological signals.

    These capture non-linear interactions that matter for prediction:
    - Temperature x pressure: pre-spawn + falling pressure = explosive fishing
    - Activity x conditions: high metabolic rate + stable conditions = catchable
    - Spawn x solunar: spawn phase modulates solunar importance
    """
    result: dict[str, float] = {}

    # Pre-spawn + falling pressure interaction
    # The best fishing in bass angling: fish staging in pre-spawn positions
    # combined with falling barometric pressure triggering aggressive feeding
    prespawn = sub_features.get("prespawn_intensity", NaN)
    prefrontal = sub_features.get("prefrontal_bite_index", NaN)
    if not _isnan(prespawn) and not _isnan(prefrontal):
        result["prespawn_prefrontal_combo"] = prespawn * prefrontal
    else:
        result["prespawn_prefrontal_combo"] = NaN

    # Activity x stability: high activity fish in stable conditions = good fishing
    activity = sub_features.get("activity_level", NaN)
    stability = sub_features.get("conditions_stability_index", NaN)
    if not _isnan(activity) and not _isnan(stability):
        result["active_stable_combo"] = activity * stability
    else:
        result["active_stable_combo"] = NaN

    # Thermal-metabolic sweet spot: temperature in the optimal range AND
    # good DO conditions — the ideal fishing scenario
    feeding = sub_features.get("feeding_window_score", NaN)
    do_comfort = sub_features.get("do_comfort_index", NaN)
    if not _isnan(feeding) and not _isnan(do_comfort):
        result["optimal_physiology_combo"] = feeding * do_comfort
    else:
        result["optimal_physiology_combo"] = NaN

    # Seasonal x moon interaction: solunar matters more in some seasons
    season_q = sub_features.get("season_quality_index", NaN)
    solunar_q = sub_features.get("solunar_period_quality", NaN)
    if not _isnan(season_q) and not _isnan(solunar_q):
        result["season_solunar_combo"] = season_q * solunar_q
    else:
        result["season_solunar_combo"] = NaN

    # Overall catch potential: weighted composite of all major factors
    # This is the "master score" — a single feature summarizing all biology
    weights = {
        "prespawn_intensity": 0.20,
        "feeding_window_score": 0.15,
        "pressure_fishing_quality": 0.15,
        "do_comfort_index": 0.10,
        "season_quality_index": 0.10,
        "wind_fishing_quality": 0.10,
        "moon_phase_score": 0.05,
        "conditions_stability_index": 0.10,
        "activity_level": 0.05,
    }
    weighted_sum = 0.0
    weight_total = 0.0
    for feat, w in weights.items():
        val = sub_features.get(feat, NaN)
        if not _isnan(val):
            weighted_sum += val * w
            weight_total += w

    if weight_total > 0:
        result["catch_potential_index"] = _clamp(weighted_sum / weight_total)
    else:
        result["catch_potential_index"] = NaN

    return result


# ===========================================================================
# Public API
# ===========================================================================

def compute_fish_biology_features(
    water_temp_c: float,
    air_temp_c: float,
    pressure_mb: float,
    pressure_delta_6h: float,
    pressure_delta_24h: float,
    wind_speed_kph: float,
    cloud_cover_pct: float,
    day_of_year: int,
    latitude: float,
    humidity_pct: float = NaN,
    precip_mm: float = 0.0,
    dissolved_oxygen_mgL: float = NaN,
    moon_phase: float = NaN,  # 0-1 (0=new, 0.5=full)
) -> dict[str, float]:
    """Compute all fish biology features from environmental conditions.

    Returns a flat dict of feature_name -> float value. Features that cannot
    be computed from available inputs are returned as NaN (never 0).

    Parameters
    ----------
    water_temp_c : Water temperature in Celsius (primary driver)
    air_temp_c : Air temperature in Celsius
    pressure_mb : Barometric pressure in millibars
    pressure_delta_6h : Pressure change over last 6 hours (mb)
    pressure_delta_24h : Pressure change over last 24 hours (mb)
    wind_speed_kph : Wind speed in km/h
    cloud_cover_pct : Cloud cover percentage 0-100
    day_of_year : Day of year (1-366)
    latitude : Latitude in decimal degrees
    humidity_pct : Relative humidity percentage (optional)
    precip_mm : Precipitation in mm (default 0)
    dissolved_oxygen_mgL : Measured dissolved oxygen in mg/L (optional)
    moon_phase : Moon phase 0-1 (0=new, 0.5=full) (optional)

    Returns
    -------
    dict[str, float] with ~35 biology-based features
    """
    features: dict[str, float] = {}

    # Estimate water temp from air temp if missing
    # Ref: Stefan & Preud'homme (1993), stream temp estimation
    effective_water_temp = water_temp_c
    if _isnan(effective_water_temp) and not _isnan(air_temp_c):
        # Empirical: water temp lags air by ~2-5 C in temperate climates
        effective_water_temp = air_temp_c * 0.663 + 7.16

    # 1. Spawn timing
    spawn = _spawn_phase_features(effective_water_temp)
    features.update(spawn)

    # 2. Metabolic rate
    metabolic = _metabolic_features(effective_water_temp)
    features.update(metabolic)

    # 3. Dissolved oxygen
    do_feats = _dissolved_oxygen_features(effective_water_temp, dissolved_oxygen_mgL)
    features.update(do_feats)

    # 4. Barometric pressure
    pressure = _pressure_features(pressure_mb, pressure_delta_6h, pressure_delta_24h)
    features.update(pressure)

    # 5. Photoperiod / light
    photo = _photoperiod_features(day_of_year, latitude, cloud_cover_pct)
    features.update(photo)

    # 6. Moon / solunar
    moon = _moon_features(moon_phase)
    features.update(moon)

    # 7. Seasonal patterns
    seasonal = _seasonal_features(day_of_year, effective_water_temp, latitude)
    features.update(seasonal)

    # 8. Wind
    wind = _wind_features(wind_speed_kph, effective_water_temp, air_temp_c)
    features.update(wind)

    # 9. Stability
    stability = _stability_features(
        pressure_delta_6h, pressure_delta_24h,
        wind_speed_kph, effective_water_temp, air_temp_c,
    )
    features.update(stability)

    # 10. Interaction features (computed from all above)
    interactions = _interaction_features(
        features, effective_water_temp, pressure_delta_6h,
    )
    features.update(interactions)

    # Precipitation feature: light rain can improve fishing (reduced visibility,
    # increased invertebrate activity), heavy rain suppresses it
    if not _isnan(precip_mm):
        if precip_mm <= 0:
            features["precip_fishing_effect"] = 0.5  # neutral
        elif precip_mm <= 3.0:
            features["precip_fishing_effect"] = 0.7  # light rain — slight positive
        elif precip_mm <= 10.0:
            features["precip_fishing_effect"] = 0.6  # moderate rain — mixed
        elif precip_mm <= 25.0:
            features["precip_fishing_effect"] = 0.35  # heavy rain — suppression
        else:
            features["precip_fishing_effect"] = 0.15  # deluge — poor fishing
    else:
        features["precip_fishing_effect"] = NaN

    # Humidity contributes to insect hatch potential (muggy + warm = bugs = baitfish)
    if not _isnan(humidity_pct) and not _isnan(air_temp_c):
        # Insect activity peaks in warm, humid conditions
        if air_temp_c > 15:
            insect_potential = (humidity_pct / 100.0) * _clamp((air_temp_c - 15) / 15.0)
        else:
            insect_potential = 0.0
        features["insect_hatch_potential"] = _clamp(insect_potential)
    else:
        features["insect_hatch_potential"] = NaN

    return features
