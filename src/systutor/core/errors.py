from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError


class AppError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "app_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class AuthenticationError(AppError):
    def __init__(self, message: str = "Credenciales invalidas") -> None:
        super().__init__(message, status_code=401, code="authentication_error")


class AuthorizationError(AppError):
    def __init__(self, message: str = "Permiso insuficiente") -> None:
        super().__init__(message, status_code=403, code="authorization_error")


class NotFoundError(AppError):
    def __init__(self, message: str = "Recurso no encontrado") -> None:
        super().__init__(message, status_code=404, code="not_found")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                }
            },
        )

    @app.exception_handler(SQLAlchemyTimeoutError)
    async def handle_db_pool_timeout(_: Request, exc: SQLAlchemyTimeoutError) -> JSONResponse:
        # El pool está saturado: es una sobrecarga temporal, no un error interno.
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "1"},
            content={
                "error": {
                    "code": "database_busy",
                    "message": "Base de datos ocupada. Reintenta en unos segundos.",
                }
            },
        )
