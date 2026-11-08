from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from systutor.core.config import Settings
from systutor.core.database import async_db_session_scope
from systutor.core.lifecycle import ensure_async_session_factory, ensure_session_factory
from systutor.kernel.events.bus import EventBus
from systutor.kernel.plugins.runtime import PluginManifestRegistry


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_plugin_registry(request: Request) -> PluginManifestRegistry:
    return request.app.state.plugin_registry


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


async def get_db_session(request: Request) -> AsyncGenerator[Session, None]:
    """Sesión sync con cleanup en el event loop.

    Si el cleanup de una dependencia sync se ejecuta en el threadpool, una
    saturación de threads puede encolarlo detrás de requests que esperan una
    conexión y producir un deadlock del pool. Al ser async, el cierre corre en
    el event loop y siempre devuelve la conexión.
    """
    session_factory = ensure_session_factory(request.app)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


async def get_async_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = ensure_async_session_factory(request.app)
    async for session in async_db_session_scope(session_factory):
        yield session
