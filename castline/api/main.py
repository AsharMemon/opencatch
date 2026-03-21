"""CASTLINE FastAPI application — fishing conditions prediction API."""
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from castline.api.config import settings
from castline.api.middleware.rate_limit import RateLimitMiddleware
from castline.api.routers import auth, conditions, locations, species, reports, tiles, predictions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: preload ML model + connect Redis. Shutdown: cleanup."""
    # Connect Redis
    app.state.redis = aioredis.from_url(
        settings.redis_url, decode_responses=True
    )

    # Preload ML predictor — try V14 two-model ensemble first, fall back to V13/legacy
    app.state.predictor = None
    try:
        from castline.models.inference_v14 import V14Predictor
        from pathlib import Path

        model_dir = Path(settings.model_dir)
        # V14 artifacts live alongside V13 in castline/models/
        alt_model_dir = Path(__file__).resolve().parents[1] / "models"

        # Auto-detect latest version (V15 > V14)
        for try_dir in (model_dir, alt_model_dir):
            for v in ("v15", "v14"):
                if (try_dir / f"cpue_{v}_seen_catboost.cbm").exists():
                    app.state.predictor = V14Predictor.load(try_dir, version=v)
                    print(f"[CASTLINE] {v.upper()} two-model predictor loaded from {try_dir}")
                    break
            if app.state.predictor is not None:
                break

        if app.state.predictor is None and (alt_model_dir / "cpue_v13_catboost.cbm").exists():
            # Load V13 stacked ensemble via V14Predictor-compatible wrapper
            from castline.api.services.model_loader import load_v13_as_predictor
            app.state.predictor = load_v13_as_predictor(alt_model_dir)
            print(f"[CASTLINE] V13 stacked ensemble loaded from {alt_model_dir}")
        else:
            print(f"[CASTLINE] WARNING: No model artifacts found at {model_dir} or {alt_model_dir}")
    except Exception as e:
        print(f"[CASTLINE] WARNING: Could not load ML model: {e}")
        import traceback; traceback.print_exc()

    yield

    # Shutdown
    await app.state.redis.aclose()
    # Close DB pool if created by auth endpoints
    db_pool = getattr(app.state, "db_pool", None)
    if db_pool:
        await db_pool.close()


app = FastAPI(
    title="CASTLINE API",
    description="Real-time fishing conditions prediction",
    version="2.0.0",
    lifespan=lifespan,
)

# Middleware — order matters: last added = outermost (runs first)
# Rate limiting (outermost — runs before everything else)
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_rpm)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, prefix=settings.api_prefix, tags=["auth"])
app.include_router(locations.router, prefix=settings.api_prefix, tags=["locations"])
app.include_router(conditions.router, prefix=settings.api_prefix, tags=["conditions"])
app.include_router(species.router, prefix=settings.api_prefix, tags=["species"])
app.include_router(reports.router, prefix=settings.api_prefix, tags=["reports"])
app.include_router(tiles.router, prefix=settings.api_prefix, tags=["tiles"])
app.include_router(predictions.router, prefix=settings.api_prefix, tags=["predictions"])


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model_loaded": app.state.predictor is not None,
        "environment": settings.environment,
    }
