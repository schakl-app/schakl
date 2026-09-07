"""A custom field that applies to *some* rows of its entity type (CLAUDE.md §13).

A tenant's "Website" field belongs on a hosting agreement and on nothing else, and a definition
on the ``subscription`` entity type reaches every agreement — so a field meant for hosting was
asked for on every SEO retainer too. A definition may now carry a **scope** in its
``config_json`` (the ``print_on_document`` precedent, ``format.py``: a flag beside the definition,
no migration)::

    {"scope": {"subscription_type_id": ["<uuid>", …], "subscription_template_id": ["<uuid>", …]}}

and applies to a row iff it has no scope, or for **any** dimension the row's value is in that
list — "attached to" semantics: a field attached to type *Hosting* and to preset *Hosting Pro*
shows on an agreement that has either. The scope decides what a form draws, whether ``required``
binds, and whether a flagged value rides the document; a value stored on a row the definition no
longer applies to is **kept**, so switching a type away and back loses nothing.

**Core names no module.** What the dimensions *are* — which attribute of the row is judged, what
the options are called, where they come from — is the owning module's knowledge, so it registers a
:class:`CustomFieldScopeSpec` per dimension here (the ``core/busy.py`` / ``core/tagmanager.py``
shape) and core only composes: a spec's ``key`` **is the entity attribute name** the row is judged
on, which is what lets the same pure rule serve the write path (``row_scope`` from the request),
the document path (``row_scope`` from the ORM row) and the import (``row_scope`` from the resolved
cells) without any of them knowing what a subscription type is. An entity type with no specs has
no scoping at all, and every caller that passes ``row_scope=None`` gets the old answer: everything
applies.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.customfields.models import CustomFieldDefinition
from app.errors import AppError

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a core → tenancy import cycle
    from app.core.tenancy import RequestContext

#: The ``config_json`` key a definition's scope lives under.
SCOPE = "scope"

#: What a caller hands the rule: the row's value per dimension key (``str``/UUID/``None``).
RowScope = Mapping[str, Any]


@dataclass(frozen=True)
class ScopeOption:
    """One thing a definition may be attached to, as the settings screen lists it."""

    value: str
    label_i18n: dict[str, str] = field(default_factory=dict)
    #: A deactivated type is still listed — muted — so relabelling a field that was scoped to
    #: it does not 422 on a value the tenant did not touch.
    active: bool = True


ScopeOptionsProvider = Callable[["RequestContext"], Awaitable[list[ScopeOption]]]


@dataclass(frozen=True)
class CustomFieldScopeSpec:
    """One dimension a module offers for narrowing its entity's definitions.

    ``key`` is the **entity attribute** the row is judged on (``subscription_type_id``) — the
    contract that keeps the rule pure: whoever has the row, the request or the resolved import
    cells reads that attribute and hands it over. ``label_key`` names the dimension on the
    settings screen through i18n. ``options`` answers with the tenant's current choices and is
    expected to gate on the module's own read permission (§15's "each provider remembers" is a
    hope; the provider checking itself is the rule).
    """

    key: str
    label_key: str
    options: ScopeOptionsProvider


_specs: dict[str, list[CustomFieldScopeSpec]] = {}


def register_scopes(entity_type: str, specs: Sequence[CustomFieldScopeSpec]) -> None:
    """Called once by the owning module at import time. Idempotent per key, so a re-import
    (the test suite reloads modules) never doubles a dimension."""
    current = _specs.setdefault(entity_type, [])
    for spec in specs:
        if all(s.key != spec.key for s in current):
            current.append(spec)


def scopes_for(entity_type: str) -> list[CustomFieldScopeSpec]:
    """The dimensions registered for ``entity_type`` — ``[]`` when it has none."""
    return list(_specs.get(entity_type, []))


# --- the pure rule ------------------------------------------------------------ #


def _normalise(raw: Any) -> dict[str, list[str]]:
    """``config_json.scope`` as ``{key: [values]}``, junk-tolerant: a stored config is tenant
    data that survived every release, so a wrong shape reads as *unscoped* rather than raising
    on a page that only wanted to draw a form."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, values in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        cleaned = list(dict.fromkeys(str(v) for v in values if v is not None and str(v)))
        if cleaned:
            out[key] = cleaned
    return out


def definition_scope(definition: CustomFieldDefinition) -> dict[str, list[str]]:
    """The definition's scope, normalised; ``{}`` for an unscoped one."""
    return _normalise((definition.config_json or {}).get(SCOPE))


def is_scoped(definition: CustomFieldDefinition) -> bool:
    return bool(definition_scope(definition))


def applies(definition: CustomFieldDefinition, row_scope: RowScope | None) -> bool:
    """Whether ``definition`` applies to a row whose dimension values are ``row_scope``.

    ``row_scope=None`` means the caller has no row to judge — the old contract, everything
    applies. A ``None`` value in a dimension never matches: a row with no type is not "of"
    any type.
    """
    scope = definition_scope(definition)
    if not scope or row_scope is None:
        return True
    for key, values in scope.items():
        current = row_scope.get(key)
        if current is not None and str(current) in values:
            return True
    return False


def applicable(
    definitions: Iterable[CustomFieldDefinition], row_scope: RowScope | None
) -> list[CustomFieldDefinition]:
    return [d for d in definitions if applies(d, row_scope)]


def row_scope_from(
    entity_type: str, values: Mapping[str, Any] | None = None, entity: Any | None = None
) -> dict[str, Any] | None:
    """The row's dimension values for the rule, read off resolved ``values`` first and the
    ``entity`` otherwise — the import's shape, where a create has only cells and an update has
    the row behind them. ``None`` when the entity type has no dimensions, so a caller with no
    specs to consult keeps the everything-applies contract.
    """
    specs = scopes_for(entity_type)
    if not specs:
        return None
    out: dict[str, Any] = {}
    for spec in specs:
        if values is not None and spec.key in values:
            out[spec.key] = values[spec.key]
        elif entity is not None:
            out[spec.key] = getattr(entity, spec.key, None)
        else:
            out[spec.key] = None
    return out


# --- validating what a tenant saves ------------------------------------------- #


async def validate_scope(
    ctx: RequestContext, entity_type: str, config_json: dict[str, Any] | None
) -> dict[str, Any]:
    """``config_json`` with its scope normalised, or a 422 naming ``scope``.

    An unknown dimension, or a value the module's provider does not list *for this tenant*, is
    refused — the second half is what keeps another org's ids out of a definition, since the
    provider answers through its own tenant-scoped service. Providers are only asked when a
    scope is actually present, so saving an ordinary definition costs no extra read. An empty
    scope is dropped from the config rather than stored as ``{}``.
    """
    config = dict(config_json or {})
    scope = _normalise(config.get(SCOPE))
    if not scope:
        config.pop(SCOPE, None)
        return config
    specs = {spec.key: spec for spec in scopes_for(entity_type)}
    invalid = any(key not in specs for key in scope)
    if not invalid:
        for key, values in scope.items():
            options = {opt.value for opt in await specs[key].options(ctx)}
            if any(value not in options for value in values):
                invalid = True
                break
    if invalid:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={SCOPE: "customfields.errors.invalid_scope"},
        )
    config[SCOPE] = scope
    return config
