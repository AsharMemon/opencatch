"""Pydantic request/response schemas for the CASTLINE API."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Conditions ──────────────────────────────────────────────────

class ConditionsRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class WeatherData(BaseModel):
    temp_f: Optional[float] = None
    feels_like_f: Optional[float] = None
    wind_mph: Optional[float] = None
    wind_direction: Optional[str] = None
    pressure_mb: Optional[float] = None
    pressure_trend: Optional[str] = None
    humidity_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    precip_in: Optional[float] = None
    conditions: Optional[str] = None


class WaterData(BaseModel):
    temp_f: Optional[float] = None
    level_ft: Optional[float] = None
    level_trend: Optional[str] = None
    discharge_cfs: Optional[float] = None
    clarity: Optional[str] = None
    source: Optional[str] = None


class SpeciesActivity(BaseModel):
    species: str
    probability: float
    activity_level: str  # "high", "moderate", "low"
    optimal_temp_f: Optional[float] = None
    notes: Optional[str] = None


class ConditionsResponse(BaseModel):
    score: float = Field(..., ge=0, le=100)
    confidence: str  # "high", "medium", "low"
    label: str  # "Excellent", "Good", "Fair", "Poor"
    summary: str
    location_name: str
    weather: WeatherData
    water: WaterData
    species: list[SpeciesActivity]
    top_factors: list[str]  # e.g. ["Stable pressure", "Post-frontal", "Peak feed month"]
    updated_at: datetime


# ── Forecast ────────────────────────────────────────────────────

class ForecastPoint(BaseModel):
    time: datetime
    score: float
    label: str
    weather_summary: Optional[str] = None


class ForecastResponse(BaseModel):
    location_name: str
    lat: float
    lon: float
    forecast: list[ForecastPoint]
    best_window: Optional[str] = None  # e.g. "Tomorrow 6-9 AM"


# ── Species ─────────────────────────────────────────────────────

class SpeciesListResponse(BaseModel):
    lat: float
    lon: float
    species: list[SpeciesActivity]
    dominant_group: str  # "bass", "panfish", "predator", etc.


# ── Catch Reports ──────────────────────────────────────────────

class CatchReportCreate(BaseModel):
    user_id: str
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    trip_start: datetime
    trip_end: datetime
    effort_hours: float = Field(..., gt=0, le=48)
    species: str = "largemouth_bass"
    catch_count: int = Field(0, ge=0)
    kept_count: int = Field(0, ge=0)
    largest_weight_lb: Optional[float] = None
    rating: int = Field(..., ge=1, le=5)
    conditions_snapshot: Optional[dict] = None
    reported_at: Optional[datetime] = None
    notes: Optional[str] = None


class CatchReportResponse(BaseModel):
    id: int
    status: str = "created"
    resolved_predictions: Optional[list[dict]] = None


# ── Predictions (v2 4-layer system) ──────────────────────────

class PredictRequest(BaseModel):
    location: str
    date: str  # YYYY-MM-DD
    usgs_site_id: Optional[str] = ""
    user_id: Optional[str] = ""


class BatchPredictRequest(BaseModel):
    predictions: list[PredictRequest]


class LayerBreakdown(BaseModel):
    label: str
    description: str
    contribution: int
    data_quality: str  # "live", "estimated", "modeled", "historical"


class PredictionBreakdown(BaseModel):
    fishing_score: int
    layers: dict[str, LayerBreakdown]
    predicted_weight_lb: float


class PredictionIntervalSchema(BaseModel):
    lower_bound: float
    upper_bound: float
    interval_width: float
    margin: float  # half-width for "+/- X" display
    coverage_target: float  # e.g. 0.90
    method: str  # "conformal" or "geo_conformal"


class PredictResponse(BaseModel):
    fishing_score: int = Field(..., ge=0, le=100)
    confidence: float
    model_version: str
    breakdown: PredictionBreakdown
    conditions: dict
    explanation: str
    prediction_id: Optional[int] = None
    prediction_interval: Optional[PredictionIntervalSchema] = None


class ForecastV2Response(BaseModel):
    location: str
    start_date: str
    forecast: list[dict]


class BestFishingResponse(BaseModel):
    date: str
    top_locations: list[dict]
    total_evaluated: int


# ── Location Embeddings ───────────────────────────────────────

class LocationEmbeddingResponse(BaseModel):
    name: str
    lat: float
    lon: float
    geoclip_embedding: list[float] = Field(..., description="512-dim GeoCLIP location embedding")
    satclip_embedding: Optional[list[float]] = Field(None, description="256-dim SatCLIP location embedding (if available)")
