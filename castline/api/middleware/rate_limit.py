"""Redis-backed rate limiting middleware for the CASTLINE API.

Uses a sliding window counter in Redis. Rate limits are per-user (JWT sub)
or per-IP for unauthenticated requests.

Default: 100 requests per minute per identity.
"""

import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths exempt from rate limiting
_EXEMPT_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter backed by Redis."""

    def __init__(self, app, requests_per_minute: int = 100):
        super().__init__(app)
        self.rpm = requests_per_minute
        self.window = 60  # seconds

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate limiting for health checks, docs, etc.
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            # No Redis connection — pass through without rate limiting
            return await call_next(request)

        # Determine the rate-limit identity
        identity = self._extract_identity(request)
        key = f"rl:{identity}"

        try:
            now = time.time()
            window_start = now - self.window

            # Use a Redis pipeline for atomic sliding window operations
            pipe = redis.pipeline()
            # Remove expired entries
            pipe.zremrangebyscore(key, 0, window_start)
            # Add current request
            pipe.zadd(key, {str(now): now})
            # Count requests in window
            pipe.zcard(key)
            # Set TTL on the key
            pipe.expire(key, self.window + 1)
            results = await pipe.execute()

            request_count = results[2]

            # Build response with rate limit headers
            response = None
            if request_count > self.rpm:
                retry_after = int(self.window - (now - window_start))
                response = JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "retry_after_seconds": max(1, retry_after),
                    },
                )
                logger.warning("Rate limit exceeded for %s (%d/%d)", identity, request_count, self.rpm)
            else:
                response = await call_next(request)

            # Add standard rate limit headers
            response.headers["X-RateLimit-Limit"] = str(self.rpm)
            response.headers["X-RateLimit-Remaining"] = str(max(0, self.rpm - request_count))
            response.headers["X-RateLimit-Reset"] = str(int(now + self.window))

            return response

        except Exception as exc:
            # Redis errors should not block requests — fail open
            logger.warning("Rate limiter error (failing open): %s", exc)
            return await call_next(request)

    def _extract_identity(self, request: Request) -> str:
        """Extract a rate-limit identity from the request.

        Priority:
        1. JWT user ID (from Authorization header)
        2. Client IP address
        """
        # Try to extract user ID from JWT without full validation
        # (validation happens in the endpoint dependency)
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                import jwt as pyjwt
                from castline.api.config import settings
                token = auth_header[7:]
                payload = pyjwt.decode(
                    token, settings.jwt_secret, algorithms=["HS256"],
                    options={"verify_exp": False},  # Don't reject expired for rate limiting
                )
                user_id = payload.get("sub")
                if user_id:
                    return f"user:{user_id}"
            except Exception:
                pass

        # Fall back to client IP
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
        return f"ip:{ip}"
