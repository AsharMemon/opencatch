"""Application configuration via environment variables."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://castline:castline@localhost:5432/castline"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # ML model
    model_dir: str = "castline/models"

    # Martin tile server (internal)
    martin_url: str = "http://martin:3000"

    # App
    environment: str = "development"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["*"]

    # JWT auth
    jwt_secret: str = "castline-dev-secret-change-in-production"

    # Rate limiting
    rate_limit_rpm: int = 100  # requests per minute per user/IP

    # Cache TTLs (seconds)
    cache_conditions_ttl: int = 3600       # 1 hour
    cache_forecast_ttl: int = 21600        # 6 hours
    cache_species_ttl: int = 86400         # 24 hours
    cache_usgs_ttl: int = 900              # 15 min
    cache_usace_ttl: int = 1800            # 30 min
    cache_weather_ttl: int = 3600          # 1 hour
    cache_geocode_ttl: int = 604800       # 7 days

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
