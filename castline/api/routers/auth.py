"""Authentication endpoints: register, login, token refresh, profile."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from castline.api.services.auth import (
    _hash_password,
    _verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field("", max_length=64)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class UserProfileResponse(BaseModel):
    user_id: int
    email: str
    display_name: str
    created_at: Optional[str] = None


# ── User storage (PostgreSQL via raw asyncpg) ─────────────────

async def _get_db_pool(request: Request):
    """Get or create the asyncpg connection pool from app state."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        import asyncpg
        from castline.api.config import settings
        # Convert SQLAlchemy URL to asyncpg format
        db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10)
        request.app.state.db_pool = pool
    return pool


async def _ensure_users_table(pool):
    """Create the users table if it doesn't exist."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(256) UNIQUE NOT NULL,
                password_hash VARCHAR(256) NOT NULL,
                display_name VARCHAR(64) NOT NULL DEFAULT '',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)


# ── Endpoints ──────────────────────────────────────────────────

@router.post("/auth/register", response_model=TokenResponse, status_code=201)
async def register(request: Request, body: RegisterRequest):
    """Register a new user account.

    Returns access and refresh tokens on success.
    """
    pool = await _get_db_pool(request)
    await _ensure_users_table(pool)

    password_hash = _hash_password(body.password)

    async with pool.acquire() as conn:
        # Check if email already exists
        existing = await conn.fetchrow(
            "SELECT id FROM users WHERE email = $1", body.email
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

        # Insert new user
        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, display_name)
               VALUES ($1, $2, $3) RETURNING id""",
            body.email,
            password_hash,
            body.display_name or body.email.split("@")[0],
        )
        user_id = row["id"]

    logger.info("New user registered: %s (id=%d)", body.email, user_id)

    access_token = create_access_token(user_id, body.email)
    refresh_token = create_refresh_token(user_id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=60 * 60 * 24,  # 24 hours
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(request: Request, body: LoginRequest):
    """Authenticate with email and password.

    Returns access and refresh tokens on success.
    """
    pool = await _get_db_pool(request)
    await _ensure_users_table(pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, password_hash, is_active FROM users WHERE email = $1",
            body.email,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    if not _verify_password(body.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    user_id = row["id"]
    email = row["email"]

    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=60 * 60 * 24,
    )


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_tokens(request: Request, body: RefreshRequest):
    """Exchange a refresh token for new access + refresh tokens."""
    payload = decode_token(body.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — expected refresh token",
        )

    user_id = int(payload["sub"])

    # Verify user still exists and is active
    pool = await _get_db_pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email, is_active FROM users WHERE id = $1", user_id
        )

    if row is None or not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )

    access_token = create_access_token(user_id, row["email"])
    refresh_token = create_refresh_token(user_id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=60 * 60 * 24,
    )


@router.get("/auth/me", response_model=UserProfileResponse)
async def get_profile(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Get the authenticated user's profile."""
    pool = await _get_db_pool(request)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, display_name, created_at FROM users WHERE id = $1",
            user["user_id"],
        )

    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    return UserProfileResponse(
        user_id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
    )
