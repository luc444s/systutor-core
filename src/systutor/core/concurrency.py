from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Liveness probes must never be rejected by the limiter: if they queue
# behind business requests, health checks time out and load balancers
# mark a still-healthy process as dead.
_EXEMPT_SUFFIXES = ("/system/health", "/system/health/live")


class ConcurrencyLimitMiddleware:
    """ASGI middleware that applies backpressure instead of queueing.

    Without a limit, every request is accepted and waits in line:
    ``latency ~= concurrency / throughput`` (e.g. 2000 / 68 ~= 29 s).
    This middleware admits at most ``limit`` HTTP requests at once per
    process and rejects the rest *immediately* with 503 + ``Retry-After``,
    so clients retry instead of hanging for tens of seconds.

    The limit is per process (per uvicorn worker). Total admitted
    concurrency is ``limit * workers``; size it to ``threadpool_tokens``
    so the limiter fires before requests pile up in the AnyIO threadpool
    or the SQLAlchemy connection pool. A limit of 0 disables the limiter.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: int,
        retry_after_seconds: int = 1,
        exempt_suffixes: tuple[str, ...] = _EXEMPT_SUFFIXES,
    ) -> None:
        self.app = app
        self.limit = max(0, limit)
        self.retry_after_seconds = max(0, retry_after_seconds)
        self.exempt_suffixes = exempt_suffixes
        self._in_flight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.limit <= 0:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.endswith(self.exempt_suffixes):
            await self.app(scope, receive, send)
            return

        # No await between the check and the increment: on a single event
        # loop this is atomic, so _in_flight cannot be raced past the limit.
        if self._in_flight >= self.limit:
            await self._send_overloaded(scope, send)
            return

        self._in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self._in_flight -= 1

    async def _send_overloaded(self, scope: Scope, send: Send) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "overloaded",
                    "message": "Server busy. Retry in a few seconds.",
                }
            }
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"retry-after", str(self.retry_after_seconds).encode("ascii")),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": headers,
            }
        )
        message: Message = {"type": "http.response.body", "body": body}
        await send(message)
