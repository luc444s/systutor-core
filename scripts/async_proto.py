"""Minimal asyncpg prototype: same endpoints as the sync core API, but
every DB call is native async (no threadpool, no SQLAlchemy session).

Purpose: A/B benchmark vs the sync implementation on the same hardware
and database. Run:
    uvicorn scripts.async_proto:app --workers 4 --port 8125
Endpoints mirror the bench targets:
    GET /api/v1/system/health/live   (no DB, no auth)
    GET /api/v1/system/health/ready  (SELECT 1)
    GET /api/v1/core/permissions     (JWT + user lookup + list permissions)
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Header, HTTPException

DATABASE_URL = os.getenv(
    "SYSTUTOR_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/systutor_bench_final",
)
# asyncpg wants a plain postgres:// URL (strip SQLAlchemy driver suffix)
if DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = "postgresql://" + DATABASE_URL.split("://", 1)[1]


def _to_asyncpg_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.split("://", 1)[1]
    return url


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        _to_asyncpg_url(DATABASE_URL), min_size=10, max_size=20
    )
    yield
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/api/v1/system/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok", "service": "api", "version": "proto", "env": "test"}


@app.get("/api/v1/system/health/ready")
async def ready() -> dict[str, object]:
    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {
        "status": "ok",
        "service": "api",
        "version": "proto",
        "env": "test",
        "database_connected": True,
    }


async def _require_user(pool: asyncpg.Pool, authorization: str | None):
    """Mirror of get_current_user: JWT verify + user lookup + branch scope."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing credentials")
    token = authorization[7:]
    # Reuse the kernel JWT verification (HS256, same secret)
    from systutor.core.config import get_settings
    from systutor.kernel.auth.security import decode_access_token

    try:
        payload = decode_access_token(token, get_settings())
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    user_id = payload.get("sub")
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, email, tenant_id, branch_id, is_superadmin, is_active FROM users WHERE id = $1",
            user_id,
        )
        if user is None or not user["is_active"]:
            raise HTTPException(status_code=401, detail="User not available")
        if user["branch_id"] is not None:
            branch = await conn.fetchval(
                "SELECT id FROM branches WHERE id = $1 AND tenant_id = $2",
                user["branch_id"],
                user["tenant_id"],
            )
            if branch is None:
                raise HTTPException(status_code=401, detail="User branch not available")
    return user


@app.get("/api/v1/core/permissions")
async def list_permissions(authorization: str | None = Header(default=None)):
    pool: asyncpg.Pool = app.state.pool
    await _require_user(pool, authorization)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, description, created_at FROM permissions ORDER BY name ASC"
        )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
