from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from systutor.core.config import Settings


class Base(DeclarativeBase):
    """Shared declarative base for the backend."""


def register_model_metadata() -> None:
    # Load all kernel models so FKs resolve
    # outside Alembic or pytest as well.
    import systutor.kernel.models  # noqa: F401


def _to_async_url(url: str) -> str:
    """Convierte URL sync a async (psycopg → asyncpg)."""
    return url.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _pool_options(settings: Settings, url: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "pool_pre_ping": settings.db_pool_pre_ping,
        "pool_timeout": settings.db_pool_timeout,
        "future": True,
    }
    if url.startswith("sqlite"):
        return options
    options.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_use_lifo=settings.db_pool_use_lifo,
    )
    return options


def build_engine(settings: Settings) -> Engine:
    register_model_metadata()
    connect_args: dict[str, Any] = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    elif settings.database_url.startswith("postgresql") and settings.db_statement_timeout_ms > 0:
        connect_args["options"] = f"-c statement_timeout={settings.db_statement_timeout_ms}"

    return create_engine(
        settings.database_url,
        connect_args=connect_args,
        **_pool_options(settings, settings.database_url),
    )


def build_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = build_engine(settings)
    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def _async_pool_options(settings: Settings, url: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "pool_pre_ping": settings.db_pool_pre_ping,
        "pool_timeout": settings.db_pool_timeout,
        "future": True,
    }
    if url.startswith("sqlite"):
        return options
    options.update(
        pool_size=settings.db_async_pool_size,
        max_overflow=settings.db_async_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_use_lifo=settings.db_pool_use_lifo,
    )
    return options


def build_async_engine(settings: Settings) -> AsyncEngine | None:
    """Build an async engine for PostgreSQL only. SQLite has no async support."""
    if not settings.database_url.startswith("postgresql"):
        return None
    register_model_metadata()
    async_url = _to_async_url(settings.database_url)
    connect_args: dict[str, Any] = {}
    if settings.db_statement_timeout_ms > 0:
        connect_args["server_settings"] = {
            "statement_timeout": str(settings.db_statement_timeout_ms),
        }
    return create_async_engine(
        async_url,
        connect_args=connect_args,
        **_async_pool_options(settings, async_url),
    )


def build_async_session_factory(
    settings: Settings,
) -> async_sessionmaker[AsyncSession] | None:
    engine = build_async_engine(settings)
    if engine is None:
        return None
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def db_session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


async def async_db_session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session


def check_database_connection(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        session.execute(text("SELECT 1"))
    return True
