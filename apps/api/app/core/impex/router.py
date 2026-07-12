"""Impex routes — one export + one import route **per opted-in entity** (issue #77).

Built at mount time from the registered :class:`ImpexDescriptor`s, *after* ``main.py`` has
imported the enabled modules. Registering a concrete route pair per entity — rather than one
generic ``/{entity_type}/…`` route — is a deliberate §15 decision: each route then declares
**that entity's own** read/write permission in its decorator, so deny-by-default stays
enumerable (the introspection lint sees a real ``require_permission``) and the behavioural
sweep needs no exemption entry. A generic route would have to be exempted and re-checked
inside the service, which is exactly the un-enumerable shape the two-layer rule exists to
prevent.

Each export route's query parameters are generated from the descriptor's declared filters
(``__signature__`` is how FastAPI reads them), so the OpenAPI spec — and the typed web client —
only ever offer the filters that entity's list actually supports. A filter the list cannot
apply must not appear to work: an "exported subset" that silently wasn't filtered is worse
than a 422.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response

from app.config import settings
from app.core.impex.schemas import ImportReport
from app.core.impex.service import ImpexService
from app.core.impex.spec import ImpexDescriptor
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

#: The core filter vocabulary. A descriptor names a subset; these mirror the query params the
#: entity's own list endpoint takes, so the export filters exactly like the list it exports.
FILTER_PARAMS: dict[str, tuple[Any, Any]] = {
    "q": (str | None, Query(None, max_length=200, description="Search, as on the list")),
    "status": (str | None, Query(None, max_length=50)),
    "mine": (bool, Query(False, description="Only rows assigned to me")),
    "company_id": (uuid.UUID | None, Query(None)),
    "sort": (str | None, Query(None, max_length=50, description="List sort key, '-' desc")),
}


def _export_endpoint(descriptor: ImpexDescriptor) -> Any:
    async def export_csv(**kwargs: Any) -> Response:
        ctx: RequestContext = kwargs.pop("ctx")
        filters = {key: value for key, value in kwargs.items() if value not in (None, False)}
        return await ImpexService(ctx).export_csv(descriptor, filters)

    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=FILTER_PARAMS[name][1],
            annotation=FILTER_PARAMS[name][0],
        )
        for name in descriptor.filters
    ]
    parameters.append(
        inspect.Parameter(
            "ctx",
            inspect.Parameter.KEYWORD_ONLY,
            default=Depends(require_context),
            annotation=RequestContext,
        )
    )
    # FastAPI builds the dependency tree from ``inspect.signature``, which honours this.
    export_csv.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    export_csv.__name__ = f"export_{descriptor.entity_type}_csv"
    export_csv.__doc__ = (
        f"Export the current filtered {descriptor.entity_type} list as CSV (UTF-8, BOM). "
        "Headers are stable column keys plus the tenant's custom-field keys — the file "
        "re-imports into the same organisation unchanged."
    )
    return export_csv


def _import_endpoint(descriptor: ImpexDescriptor) -> Any:
    async def import_csv(
        file: UploadFile = File(..., description="CSV file; headers are the export's keys"),
        dry_run: bool = Query(
            True,
            description="Validate and report creates/updates/errors without writing anything. "
            "`false` applies the file all-or-nothing in one transaction.",
        ),
        ctx: RequestContext = Depends(require_context),
    ) -> ImportReport:
        raw = await file.read()
        return await ImpexService(ctx).import_csv(descriptor, raw, dry_run=dry_run)

    import_csv.__name__ = f"import_{descriptor.entity_type}_csv"
    import_csv.__doc__ = (
        f"Import {descriptor.entity_type} rows from CSV, upserting on "
        f"`{descriptor.natural_key}` (max 2000 data rows per request)."
    )
    return import_csv


def build_impex_router() -> APIRouter:
    """Mount `/impex/<entity>/export` + `/impex/<entity>/import` for every registered descriptor.

    Imported lazily by ``create_app`` after module loading — descriptors live on the
    ``ModuleDescriptor``s of the enabled modules.
    """
    from app.registry import registry

    router = APIRouter(prefix="/impex", tags=["impex"])
    for module in registry.enabled(settings.enabled_modules):
        for descriptor in module.impex:
            router.add_api_route(
                f"/{descriptor.entity_type}/export",
                _export_endpoint(descriptor),
                methods=["GET"],
                name=f"impex_export_{descriptor.entity_type}",
                dependencies=[require_permission(descriptor.read_permission)],
                response_class=Response,
                responses={200: {"content": {"text/csv": {}}, "description": "CSV file"}},
            )
            router.add_api_route(
                f"/{descriptor.entity_type}/import",
                _import_endpoint(descriptor),
                methods=["POST"],
                name=f"impex_import_{descriptor.entity_type}",
                dependencies=[require_permission(descriptor.write_permission)],
                response_model=ImportReport,
            )
    return router
