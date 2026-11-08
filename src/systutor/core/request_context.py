from __future__ import annotations

from uuid import uuid4

from starlette.datastructures import MutableHeaders, State
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from systutor.core.logging import get_logger

logger = get_logger(__name__)


class RequestContextMiddleware:
    """Middleware ASGI puro.

    Evita el overhead y los bloqueos de ``BaseHTTPMiddleware`` cuando hay
    endpoints sync ejecutándose en el threadpool de AnyIO.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = request.headers.get("x-request-id", str(uuid4()))
        correlation_id = request.headers.get("x-correlation-id", request_id)

        state = scope.get("state")
        if not isinstance(state, State):
            new_state = State()
            if isinstance(state, dict):
                for key, value in state.items():
                    setattr(new_state, key, value)
            scope["state"] = new_state
            state = new_state
        state.request_id = request_id
        state.correlation_id = correlation_id

        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["x-request-id"] = request_id
                headers["x-correlation-id"] = correlation_id
            await send(message)

        await self.app(scope, receive, send_wrapper)

        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
            },
        )
