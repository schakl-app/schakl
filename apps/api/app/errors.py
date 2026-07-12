"""Consistent error envelope (CLAUDE.md §9).

Shape: ``{ "error": { "code", "message", "fields"? } }`` where ``message`` (and every
per-field message) is an **i18n key** the client translates — never user-facing English.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# HTTP status → default i18n message key.
_STATUS_MESSAGE_KEYS: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "errors.validation",
    status.HTTP_401_UNAUTHORIZED: "errors.unauthorized",
    status.HTTP_403_FORBIDDEN: "errors.forbidden",
    status.HTTP_404_NOT_FOUND: "errors.not_found",
    status.HTTP_409_CONFLICT: "errors.conflict",
    422: "errors.validation",
}

# Pydantic error "type" → per-field i18n message key.
_FIELD_MESSAGE_KEYS: dict[str, str] = {
    "missing": "errors.required",
    "value_error.missing": "errors.required",
    "url_parsing": "errors.invalid_url",
    "url_type": "errors.invalid_url",
}


class AppError(Exception):
    """Raise inside services/routers to return the standard envelope.

    ``message_key`` and any ``fields`` values must be i18n keys.
    """

    def __init__(
        self,
        code: str,
        message_key: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        fields: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message_key)
        self.code = code
        self.message_key = message_key
        self.status_code = status_code
        self.fields = fields


def _envelope(
    code: str, message: str, fields: dict[str, str] | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return {"error": error}


def _field_key(err: dict[str, Any]) -> str:
    error_type = err.get("type", "")
    # Pydantic v2's ``EmailStr`` failures collapse to the generic "value_error" type (no
    # more Pydantic-v1-style dotted subtypes), so email format is the one case matched by
    # message text rather than `type`.
    if error_type == "value_error":
        message = str(err.get("msg", ""))
        if "valid email address" in message:
            return "errors.invalid_email"
        # A validator that raises ``ValueError("errors.some_key")`` already speaks the
        # envelope's language. Honour it, rather than flattening every rule a model knows how
        # to explain (a break outside the working day, overlapping breaks) into
        # "Some fields are invalid." Pydantic prefixes the message with "Value error, ".
        key = message.removeprefix("Value error, ").strip()
        if key.startswith("errors.") and " " not in key:
            return key
    return _FIELD_MESSAGE_KEYS.get(error_type, "errors.validation")


async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message_key, exc.fields),
    )


async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields: dict[str, str] = {}
    for err in exc.errors():
        # loc is like ("body", "email"); use the last string segment as the field name.
        loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
        field = loc[-1] if loc else "_"
        fields[field] = _field_key(err)
    return JSONResponse(
        status_code=422,
        content=_envelope("validation", "errors.validation", fields),
    )


async def _http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = _STATUS_MESSAGE_KEYS.get(exc.status_code, "errors.server")
    # If the raiser passed an i18n key as detail, honor it.
    code = str(exc.status_code)
    if isinstance(exc.detail, str) and exc.detail and "." in exc.detail:
        message = exc.detail
    elif isinstance(exc.detail, dict):
        # FastAPI Users' invalid-password shape: {"code": …, "reason": …} — the manager puts
        # our i18n key in the reason (#161), so the envelope can carry it to the form.
        reason = exc.detail.get("reason")
        if isinstance(reason, str) and "." in reason:
            message = reason
    return JSONResponse(status_code=exc.status_code, content=_envelope(code, message))


async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope("server_error", "errors.server"),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
