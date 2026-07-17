"""The cloud-posture gate (epic #199). Business-licensed — see this directory's LICENSE."""

from __future__ import annotations

from app.config import settings
from app.errors import AppError


def require_cloud() -> None:
    """404 on a self-hosted box: the cloud surface exists there only in the OpenAPI spec —
    it never advertises itself, exactly like the disabled instance-admin surface."""
    if not settings.is_cloud:
        raise AppError("not_found", "errors.not_found", status_code=404)
