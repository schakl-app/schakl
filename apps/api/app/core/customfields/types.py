"""Custom-field data types (CLAUDE.md §13).

The v1 type set a tenant can choose from when defining an attribute on any customizable entity.
``config_json`` per-type rules (min/max, regex, options, default, help text) are interpreted by
the validation service; this enum just names the types.
"""

from __future__ import annotations

from enum import StrEnum


class CustomFieldType(StrEnum):
    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    EMAIL = "email"
    URL = "url"
    PHONE = "phone"

    @property
    def has_options(self) -> bool:
        """Types whose valid values come from a defined option list."""
        return self in {CustomFieldType.SELECT, CustomFieldType.MULTI_SELECT}


CUSTOM_FIELD_TYPE_VALUES: tuple[str, ...] = tuple(t.value for t in CustomFieldType)
