"""Stable public error contract: {code, message, details?, correlation_id}.

Every error response also carries the legacy `detail` field so existing
frontend calls keep working. Messages are operator-safe: exception internals,
key pools and remote payloads never reach the client.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .funpay import ChallengeException, FunPayError, ResponseFormatError, UnauthorizedException
from .inventory import InventoryConflict

logger = logging.getLogger("uvicorn.error")

STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
}
DEFAULT_CODE = "http_error"


class ApiError(Exception):
    """Typed error carrying the public contract fields."""

    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def correlation(request: Request) -> str:
    """One stable identifier per request, reused by handlers and logs."""
    ident = getattr(request.state, "correlation_id", None)
    if not ident:
        ident = uuid.uuid4().hex[:12]
        request.state.correlation_id = ident
    return ident


def body(
    code: str,
    message: str,
    correlation_id: str,
    details=None,
    *,
    status: int,
    legacy_detail=None,
) -> JSONResponse:
    """Build the contract response. `detail` keeps legacy clients working."""
    payload = {"code": code, "message": message, "correlation_id": correlation_id}
    if details is not None:
        payload["details"] = details
    # Legacy FastAPI shape: string messages and validation item lists.
    payload["detail"] = legacy_detail if legacy_detail is not None else message
    response = JSONResponse(status_code=status, content=payload)
    # ServerErrorMiddleware (500) bypasses the http middleware, so the header
    # must be attached here for every error response.
    if correlation_id:
        response.headers["X-Correlation-ID"] = correlation_id
    return response


async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
    return body(
        error.code,
        error.message,
        correlation(request),
        details=error.details,
        status=error.status,
    )


async def http_error_handler(request: Request, error: StarletteHTTPException) -> JSONResponse:
    message = error.detail if isinstance(error.detail, str) else "Запрос отклонён"
    return body(
        STATUS_CODES.get(error.status_code, DEFAULT_CODE),
        message,
        correlation(request),
        status=error.status_code,
    )


async def validation_error_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    # Exclude input and context: validation responses must not echo tokens or key pools.
    details = [
        {"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in error.errors()
    ]
    return body(
        "validation_failed",
        "Некорректные данные запроса",
        correlation(request),
        details=details,
        status=422,
        legacy_detail=details,
    )


async def remote_error_handler(request: Request, error: FunPayError) -> JSONResponse:
    code = "remote_error"
    if isinstance(error, UnauthorizedException):
        code = "funpay_auth_expired"
    elif isinstance(error, ChallengeException):
        code = "funpay_challenge_required"
    elif isinstance(error, ResponseFormatError):
        code = "remote_unconfirmed"
    # 502, not 401: a FunPay session expiry must not drop the app session.
    return body(code, str(error), correlation(request), status=502)


async def inventory_conflict_handler(request: Request, error: InventoryConflict) -> JSONResponse:
    return body("conflict", str(error), correlation(request), status=409)


async def integrity_error_handler(request: Request, _error: IntegrityError) -> JSONResponse:
    return body(
        "conflict",
        "Запись уже существует или связанный объект удалён",
        correlation(request),
        status=409,
    )


async def internal_error_handler(request: Request, error: Exception) -> JSONResponse:
    ident = correlation(request)
    # Only the class name and correlation id are logged; message may contain secrets.
    logger.error(
        "Unhandled %s during request %s %s: correlation_id=%s",
        type(error).__name__,
        request.method,
        request.url.path,
        ident,
        exc_info=False,
    )
    return body("internal", "Внутренняя ошибка. Повторите позже.", ident, status=500)


def middleware_header(request: Request, response_response) -> None:
    """Attach the correlation id to successful responses as well."""
    ident = getattr(request.state, "correlation_id", None)
    if ident:
        response_response.headers["X-Correlation-ID"] = ident


__all__ = [
    "ApiError",
    "STATUS_CODES",
    "api_error_handler",
    "body",
    "correlation",
    "http_error_handler",
    "integrity_error_handler",
    "internal_error_handler",
    "inventory_conflict_handler",
    "middleware_header",
    "remote_error_handler",
    "validation_error_handler",
]
