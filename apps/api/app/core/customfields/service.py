"""Custom-fields service (CLAUDE.md §13) — definitions CRUD + dynamic validation.

On every write to a customizable entity, ``validate`` loads the tenant's active definitions for
that ``entity_type``, coerces and checks the submitted ``custom`` object (types, required, select
options, per-type rules), and returns a cleaned dict. Failures raise the standard error envelope
with per-field **i18n message keys** (never user-facing English). All DB access is tenant-scoped
via the request context's repository (Golden Rule 1).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from pydantic import AnyUrl, EmailStr, TypeAdapter, ValidationError

from app.core.customfields.models import CustomFieldDefinition
from app.core.customfields.schemas import (
    CustomFieldDefinitionCreate,
    CustomFieldDefinitionUpdate,
)
from app.core.customfields.types import CustomFieldType
from app.core.richtext import sanitize_markdown
from app.core.tenancy import RequestContext
from app.errors import AppError

_email_adapter: TypeAdapter[str] = TypeAdapter(EmailStr)
_url_adapter: TypeAdapter[AnyUrl] = TypeAdapter(AnyUrl)


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "") or value == []


class CustomFieldsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(CustomFieldDefinition)

    # --- definitions CRUD ---------------------------------------------------- #
    async def definitions(
        self, entity_type: str, *, include_inactive: bool = False
    ) -> Sequence[CustomFieldDefinition]:
        items = await self.repo.list(limit=500, offset=0, order_by=CustomFieldDefinition.position)
        defs = [d for d in items if d.entity_type == entity_type]
        if not include_inactive:
            defs = [d for d in defs if d.active]
        return sorted(defs, key=lambda d: (d.position, d.key))

    async def get_definition(self, definition_id: uuid.UUID) -> CustomFieldDefinition:
        return await self.repo.get_or_404(definition_id)

    async def create_definition(
        self, data: CustomFieldDefinitionCreate
    ) -> CustomFieldDefinition:
        self.ctx.require("settings.customfields.write")
        existing = await self.definitions(data.entity_type, include_inactive=True)
        if any(d.key == data.key for d in existing):
            raise AppError(
                "conflict", "errors.conflict", status_code=409, fields={"key": "errors.conflict"}
            )
        return await self.repo.create(**data.model_dump(mode="json"))

    async def update_definition(
        self, definition_id: uuid.UUID, data: CustomFieldDefinitionUpdate
    ) -> CustomFieldDefinition:
        self.ctx.require("settings.customfields.write")
        definition = await self.repo.get_or_404(definition_id)
        return await self.repo.update(
            definition, **data.model_dump(mode="json", exclude_unset=True)
        )

    async def delete_definition(self, definition_id: uuid.UUID) -> None:
        self.ctx.require("settings.customfields.write")
        definition = await self.repo.get_or_404(definition_id)
        await self.repo.delete(definition)

    # --- dynamic validation -------------------------------------------------- #
    async def validate(self, entity_type: str, custom: dict[str, Any]) -> dict[str, Any]:
        """Validate/coerce ``custom`` for ``entity_type`` against the tenant's definitions.

        Treats ``custom`` as the complete value set (the UI submits all active fields), so
        ``required`` is enforced here on every write. Returns the cleaned dict; raises
        ``AppError`` (422) with per-field i18n keys on any failure.
        """
        defs = await self.definitions(entity_type, include_inactive=False)
        by_key = {d.key: d for d in defs}

        errors: dict[str, str] = {}
        cleaned: dict[str, Any] = {}

        # Unknown keys (not an active definition) are rejected for data hygiene.
        for key in custom:
            if key not in by_key:
                errors[key] = "customfields.errors.unknown_field"

        for definition in defs:
            raw = custom.get(definition.key)
            if _is_empty(raw):
                if definition.required:
                    errors[definition.key] = "errors.required"
                continue
            try:
                cleaned[definition.key] = self._coerce(definition, raw)
            except _FieldError as exc:
                errors[definition.key] = exc.message_key

        if errors:
            raise AppError(
                "validation", "errors.validation", status_code=422, fields=errors
            )
        return cleaned

    def _coerce(self, definition: CustomFieldDefinition, raw: Any) -> Any:
        dt = CustomFieldType(definition.data_type)
        config = definition.config_json or {}

        if dt in (CustomFieldType.TEXT, CustomFieldType.LONG_TEXT, CustomFieldType.PHONE):
            value = str(raw)
            self._check_text_rules(value, config)
            # LONG_TEXT is markdown, authored through the shared editor (issue #66); strip raw HTML
            # on write like every other rich-text field. TEXT/PHONE stay single-line plain text.
            if dt is CustomFieldType.LONG_TEXT:
                value = sanitize_markdown(value) or ""
            return value

        if dt is CustomFieldType.EMAIL:
            try:
                return _email_adapter.validate_python(str(raw))
            except ValidationError as exc:
                raise _FieldError("errors.invalid_email") from exc

        if dt is CustomFieldType.URL:
            try:
                _url_adapter.validate_python(str(raw))
            except ValidationError as exc:
                raise _FieldError("errors.invalid_url") from exc
            return str(raw)

        if dt is CustomFieldType.NUMBER:
            return self._coerce_number(raw, config)

        if dt is CustomFieldType.BOOLEAN:
            return self._coerce_boolean(raw)

        if dt is CustomFieldType.DATE:
            return self._coerce_date(raw)

        if dt is CustomFieldType.DATETIME:
            return self._coerce_datetime(raw)

        if dt is CustomFieldType.SELECT:
            return self._coerce_select(definition, raw)

        if dt is CustomFieldType.MULTI_SELECT:
            return self._coerce_multi_select(definition, raw)

        raise _FieldError("customfields.errors.invalid_type")

    @staticmethod
    def _check_text_rules(value: str, config: dict[str, Any]) -> None:
        import re

        max_len = config.get("max")
        if isinstance(max_len, int) and len(value) > max_len:
            raise _FieldError("customfields.errors.too_long")
        pattern = config.get("regex")
        if isinstance(pattern, str) and pattern and re.fullmatch(pattern, value) is None:
            raise _FieldError("customfields.errors.pattern_mismatch")

    @staticmethod
    def _coerce_number(raw: Any, config: dict[str, Any]) -> float | int:
        if isinstance(raw, bool):
            raise _FieldError("customfields.errors.invalid_number")
        try:
            num: float | int = float(raw)
        except (TypeError, ValueError) as exc:
            raise _FieldError("customfields.errors.invalid_number") from exc
        if isinstance(num, float) and num.is_integer():
            num = int(num)
        lo, hi = config.get("min"), config.get("max")
        if isinstance(lo, (int, float)) and num < lo:
            raise _FieldError("customfields.errors.out_of_range")
        if isinstance(hi, (int, float)) and num > hi:
            raise _FieldError("customfields.errors.out_of_range")
        return num

    @staticmethod
    def _coerce_boolean(raw: Any) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.lower() in {"true", "false", "1", "0", "yes", "no"}:
            return raw.lower() in {"true", "1", "yes"}
        raise _FieldError("customfields.errors.invalid_boolean")

    @staticmethod
    def _coerce_date(raw: Any) -> str:
        try:
            return date.fromisoformat(str(raw)).isoformat()
        except ValueError as exc:
            raise _FieldError("customfields.errors.invalid_date") from exc

    @staticmethod
    def _coerce_datetime(raw: Any) -> str:
        try:
            return datetime.fromisoformat(str(raw)).isoformat()
        except ValueError as exc:
            raise _FieldError("customfields.errors.invalid_datetime") from exc

    @staticmethod
    def _option_values(definition: CustomFieldDefinition) -> set[str]:
        return {str(o.get("value")) for o in (definition.options_json or [])}

    def _coerce_select(self, definition: CustomFieldDefinition, raw: Any) -> str:
        value = str(raw)
        if value not in self._option_values(definition):
            raise _FieldError("customfields.errors.invalid_option")
        return value

    def _coerce_multi_select(self, definition: CustomFieldDefinition, raw: Any) -> list[str]:
        if not isinstance(raw, list):
            raise _FieldError("customfields.errors.invalid_option")
        allowed = self._option_values(definition)
        values = [str(v) for v in raw]
        if any(v not in allowed for v in values):
            raise _FieldError("customfields.errors.invalid_option")
        return values


class _FieldError(Exception):
    """Internal: a single custom-field coercion failure carrying its i18n message key."""

    def __init__(self, message_key: str) -> None:
        super().__init__(message_key)
        self.message_key = message_key
