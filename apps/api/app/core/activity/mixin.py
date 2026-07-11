"""``AuditableMixin`` — opt an entity into the core activity trail (issue #67, CLAUDE.md §6/§9).

Inheriting the mixin and setting ``__entity_type__`` registers the entity as auditable, so the
core activity panel is offered on its detail page and the feed accepts its type. It adds **no
column**: the trail lives in the single core ``activity_log`` table, not on the record.

``__entity_type__`` is the same attribute ``CustomizableMixin`` reads, so an entity that is both
customizable and auditable declares it once.
"""

from __future__ import annotations

from typing import Any

from app.core.activity.registry import register_auditable


class AuditableMixin:
    #: Each auditable model sets this to its stable entity-type slug (e.g. "company").
    __entity_type__: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        entity_type = getattr(cls, "__entity_type__", None)
        if entity_type:
            register_auditable(entity_type)
