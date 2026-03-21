"""CASTLINE API service layer — external data clients and feature collection."""

from castline.api.services.geocoding import reverse_geocode
from castline.api.services.usgs import get_nearest_usgs_site, get_usgs_conditions
from castline.api.services.weather import get_current_weather, get_weather_forecast
from castline.api.services.features import collect_realtime_features

__all__ = [
    "collect_realtime_features",
    "get_current_weather",
    "get_nearest_usgs_site",
    "get_usgs_conditions",
    "get_weather_forecast",
    "reverse_geocode",
]
