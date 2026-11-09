from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from systutor.core.concurrency import ConcurrencyLimitMiddleware


def _slow_app(*, limit: int) -> FastAPI:
    app = FastAPI()

    @app.get("/slow")
    async def slow() -> dict[str, str]:
        await asyncio.sleep(0.2)
        return {"status": "ok"}

    @app.get("/api/v1/system/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(ConcurrencyLimitMiddleware, limit=limit, retry_after_seconds=1)
    return app


def test_excess_requests_get_fast_503_instead_of_queueing() -> None:
    async def run() -> None:
        app = _slow_app(limit=2)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(*[client.get("/slow") for _ in range(5)])

        ok = [r for r in responses if r.status_code == 200]
        rejected = [r for r in responses if r.status_code == 503]
        assert len(ok) == 2
        assert len(rejected) == 3
        for response in rejected:
            assert response.headers["retry-after"] == "1"
            assert response.json()["error"]["code"] == "overloaded"

    asyncio.run(run())


def test_liveness_probe_bypasses_the_limit() -> None:
    async def run() -> None:
        app = _slow_app(limit=1)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            slow_task = asyncio.create_task(client.get("/slow"))
            await asyncio.sleep(0.05)
            live = await client.get("/api/v1/system/health/live")
            slow = await slow_task

        assert live.status_code == 200
        assert slow.status_code == 200

    asyncio.run(run())


def test_zero_limit_disables_the_limiter() -> None:
    async def run() -> None:
        app = _slow_app(limit=0)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(*[client.get("/slow") for _ in range(4)])
        assert all(r.status_code == 200 for r in responses)

    asyncio.run(run())


def test_app_health_ok_with_default_limit(client, test_settings) -> None:
    assert test_settings.concurrency_limit > 0
    response = client.get("/api/v1/system/health/live")
    assert response.status_code == 200
