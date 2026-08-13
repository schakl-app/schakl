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

import datetime as dt
import inspect
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response

from app.config import settings
from app.core.impex.schemas import (
    ImpexColumnsResponse,
    ImpexEntityInfo,
    ImpexInspectReport,
    ImportReport,
)
from app.core.impex.service import ImpexService
from app.core.impex.spec import ImpexDescriptor, ImpexExtension
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

#: The core filter vocabulary. A descriptor names a subset; these mirror the query params the
#: entity's own list endpoint takes, so the export filters exactly like the list it exports.
FILTER_PARAMS: dict[str, tuple[Any, Any]] = {
    "q": (str | None, Query(None, max_length=200, description="Search, as on the list")),
    # Wide enough for a comma-separated set, which the companies list takes (#329): the export
    # is handed whatever the screen resolved its filter to, so a cap that fits one status would
    # 422 the download of the very list the user is looking at.
    "status": (str | None, Query(None, max_length=200, description="Status, as on the list")),
    "mine": (bool, Query(False, description="Only rows assigned to me")),
    "company_id": (uuid.UUID | None, Query(None)),
    "project_id": (uuid.UUID | None, Query(None)),
    "user_id": (uuid.UUID | None, Query(None)),
    "date_from": (dt.date | None, Query(None, description="Rows on/after this day")),
    "date_to": (dt.date | None, Query(None, description="Rows on/before this day")),
    "hosting_id": (uuid.UUID | None, Query(None)),
    "registrar_provider_id": (uuid.UUID | None, Query(None)),
    "dns_provider_id": (uuid.UUID | None, Query(None)),
    # Three-state, and that is the point: absent is "every row", and `false` is a filter in its
    # own right ("what am I *not* invoicing", "what is *not* monitored"). A plain `bool` would
    # make those two indistinguishable and quietly export the lot.
    "invoiceable": (bool | None, Query(None)),
    "uptime_enabled": (bool | None, Query(None)),
    "sort": (str | None, Query(None, max_length=50, description="List sort key, '-' desc")),
}


def _export_endpoint(descriptor: ImpexDescriptor) -> Any:
    async def export_csv(**kwargs: Any) -> Response:
        ctx: RequestContext = kwargs.pop("ctx")
        # Only `None` is "not asked for". Dropping `False` too — which this did — is right for a
        # plain flag like `mine` (whose absent state *is* False) and wrong for every tri-state
        # one: `invoiceable=false` means "list what I do not bill", and discarding it exported
        # the whole register under a filename that says otherwise. A `fetch_page` that reads a
        # flag with `filters.get(...)` is unaffected, since False is what it inferred anyway.
        filters = {key: value for key, value in kwargs.items() if value is not None}
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


async def _source_bytes(file: UploadFile | None, text: str | None) -> tuple[bytes, bool]:
    """The uploaded bytes and whether they were **pasted** — the two differ in their cap.

    A file may be 5 MiB; a paste may not, because Starlette caps a non-file multipart part at
    1 MiB and truncates rather than erroring. Enforcing a larger paste limit here would be a
    check running on bytes that were already cut.
    """
    if file is not None and file.filename:
        return await file.read(), False
    if text and text.strip():
        return text.encode("utf-8"), True
    raise AppError("no_source", "impex.errors.no_source")


def _columns_endpoint(descriptor: ImpexDescriptor) -> Any:
    async def impex_columns(
        ctx: RequestContext = Depends(require_context),
    ) -> ImpexColumnsResponse:
        return await ImpexService(ctx).columns_for(descriptor)

    impex_columns.__name__ = f"impex_columns_{descriptor.entity_type}"
    impex_columns.__doc__ = (
        f"Every column a {descriptor.entity_type} import can write into: the entity's own, "
        "those contributed by other modules, and this organisation's custom fields — with "
        "the labels, types and aliases a mapping UI needs."
    )
    return impex_columns


def _inspect_endpoint(descriptor: ImpexDescriptor) -> Any:
    async def inspect_source(
        file: UploadFile | None = File(None, description="CSV, TSV or .xlsx file"),
        text: str | None = Form(None, description="A pasted table instead of a file"),
        sheet: str | None = Form(None, description="Worksheet to read (.xlsx only)"),
        has_header: bool = Form(True, description="Is the first row a header?"),
        ctx: RequestContext = Depends(require_context),
    ) -> ImpexInspectReport:
        raw, pasted = await _source_bytes(file, text)
        return await ImpexService(ctx).inspect(
            descriptor, raw, sheet=sheet, pasted=pasted, has_header=has_header
        )

    inspect_source.__name__ = f"impex_inspect_{descriptor.entity_type}"
    inspect_source.__doc__ = (
        "Read an uploaded file and report what it is — format, worksheets, encoding, row "
        "count — plus each of its columns with sample cells and the suggested target "
        "column. Writes nothing and reads no records; returns a fingerprint the import "
        "repeats so a mapping cannot be applied to a different file."
    )
    return inspect_source


def _import_endpoint(descriptor: ImpexDescriptor) -> Any:
    async def import_csv(
        file: UploadFile | None = File(
            None, description="CSV, TSV or .xlsx file; headers are the export's keys"
        ),
        text: str | None = Form(
            None,
            description="A pasted table instead of a file — tab, comma or semicolon "
            "separated, first line the header. Max 1 MiB.",
        ),
        sheet: str | None = Form(
            None, description="Which worksheet to read (.xlsx only; default the first)."
        ),
        has_header: bool = Form(True, description="Is the first row a header?"),
        mapping: str | None = Form(
            None,
            description="JSON object mapping a file column **index** to a target column key, "
            'e.g. `{"0": "name", "3": "city"}`. Unmapped columns are skipped. Omit the field '
            "entirely to use the file's own header row as the mapping, where every header "
            "must be an exact column key.",
        ),
        match_key: str | None = Form(
            None,
            description="Force the upsert to match on this column (must be one of the "
            "entity's natural keys). Default: the first natural key each row fills.",
        ),
        fingerprint: str | None = Form(
            None,
            description="The fingerprint from `/inspect`. Supplied and mismatched is a 409 — "
            "a mapping is positional and must not be applied to a different file.",
        ),
        dry_run: bool = Query(
            True,
            description="Validate and report creates/updates/errors without writing anything. "
            "`false` applies the file all-or-nothing in one transaction.",
        ),
        ctx: RequestContext = Depends(require_context),
    ) -> ImportReport:
        raw, pasted = await _source_bytes(file, text)
        return await ImpexService(ctx).import_csv(
            descriptor,
            raw,
            dry_run=dry_run,
            sheet=sheet,
            pasted=pasted,
            has_header=has_header,
            mapping=mapping,
            match_key=match_key,
            fingerprint=fingerprint,
        )

    import_csv.__name__ = f"import_{descriptor.entity_type}_csv"
    upsert = (
        "upserting on the first of "
        + ", ".join(f"`{key}`" for key in descriptor.natural_keys)
        + " each row fills"
        if descriptor.natural_keys
        else "create-only (no natural key)"
    )
    import_csv.__doc__ = (
        f"Import {descriptor.entity_type} rows from a spreadsheet, {upsert} "
        "(max 2000 data rows per request). Accepts a CSV/TSV/Excel upload or a pasted "
        "block; the format is read from the content, not the filename."
    )
    return import_csv


def _check_extensions(
    descriptor: ImpexDescriptor, extensions: list[ImpexExtension]
) -> None:
    """Fail at import time on a contribution that cannot work — never at request time.

    A key collision or a required contributed column is a **code** bug in whichever module
    declared it, and a code bug should stop the app coming up rather than surface as one
    tenant's confusing import report. Three rules, in the order they bite:

    * no contributed key may shadow one of the host's own — two columns under one header
      cannot round-trip, and the host's must win;
    * no two contributors may claim one key — neither has priority, so there is no correct
      resolution to pick;
    * no contributed column may be ``required`` — contacts has no standing to make
      ``contact_email`` mandatory on every company import.
    """
    claimed = {column.key: "the entity itself" for column in descriptor.columns}
    for extension in extensions:
        for column in extension.columns:
            owner = claimed.get(column.key)
            if owner is not None:
                raise RuntimeError(
                    f"impex: {extension.module!r} contributes column {column.key!r} to "
                    f"{descriptor.entity_type!r}, which {owner} already defines"
                )
            if column.required:
                raise RuntimeError(
                    f"impex: contributed column {column.key!r} "
                    f"({extension.module!r} → {descriptor.entity_type!r}) is required; a "
                    "contributing module may not make a host's import mandatory"
                )
            claimed[column.key] = f"module {extension.module!r}"


def build_impex_router() -> APIRouter:
    """Mount `/impex/<entity>/export` + `/impex/<entity>/import` for every registered descriptor.

    Imported lazily by ``create_app`` after module loading — descriptors live on the
    ``ModuleDescriptor``s of the enabled modules.
    """
    from app.registry import registry

    router = APIRouter(prefix="/impex", tags=["impex"])
    descriptors = [
        descriptor
        for module in registry.enabled(settings.enabled_modules)
        for descriptor in module.impex
    ]
    for descriptor in descriptors:
        _check_extensions(descriptor, registry.impex_extensions_for(
            descriptor.entity_type, settings.enabled_modules
        ))

    @router.get(
        "/entities",
        response_model=list[ImpexEntityInfo],
        dependencies=[
            no_permission_required(
                "the code-defined impex registry — which entity types support CSV, no tenant "
                "data; each entity's actual export/import route declares its own permission"
            )
        ],
    )
    async def list_impex_entities(
        ctx: RequestContext = Depends(require_context),
    ) -> list[ImpexEntityInfo]:
        """The entity types with CSV support, for the Instellingen → Import & export screen."""
        return [
            ImpexEntityInfo(
                entity_type=d.entity_type,
                read_permission=d.read_permission,
                write_permission=d.write_permission,
                importable=d.importable,
                filters=list(d.filters),
                natural_keys=list(d.natural_keys),
            )
            for d in descriptors
        ]

    for descriptor in descriptors:
        router.add_api_route(
            f"/{descriptor.entity_type}/export",
            _export_endpoint(descriptor),
            methods=["GET"],
            name=f"impex_export_{descriptor.entity_type}",
            # Two gates, both required: the entity's own read permission decides *what* may
            # leave, ``impex.export`` decides *who* may take it out in bulk. A client-portal
            # login holds `companies.company.read` (its own company) and must never be able to
            # download the whole client list.
            dependencies=[
                require_permission("impex.export"),
                require_permission(descriptor.read_permission),
            ],
            response_class=Response,
            responses={200: {"content": {"text/csv": {}}, "description": "CSV file"}},
        )
        router.add_api_route(
            f"/{descriptor.entity_type}/columns",
            _columns_endpoint(descriptor),
            methods=["GET"],
            name=f"impex_columns_{descriptor.entity_type}",
            # The catalog of an entity's columns is the entity's shape, so it rides the read
            # permission — with no bulk gate, because it exposes no rows: a caller who may see
            # a company may see that companies have a `city` column.
            dependencies=[require_permission(descriptor.read_permission)],
            response_model=ImpexColumnsResponse,
        )
        if descriptor.importable:
            router.add_api_route(
                f"/{descriptor.entity_type}/inspect",
                _inspect_endpoint(descriptor),
                methods=["POST"],
                name=f"impex_inspect_{descriptor.entity_type}",
                # The *write* gate, though it writes nothing: it is a step of an import, and a
                # route that accepts an arbitrary upload should sit behind the tighter of the
                # two permissions, not the looser one.
                dependencies=[
                    require_permission("impex.import"),
                    require_permission(descriptor.write_permission),
                ],
                response_model=ImpexInspectReport,
            )
            router.add_api_route(
                f"/{descriptor.entity_type}/import",
                _import_endpoint(descriptor),
                methods=["POST"],
                name=f"impex_import_{descriptor.entity_type}",
                dependencies=[
                    require_permission("impex.import"),
                    require_permission(descriptor.write_permission),
                ],
                response_model=ImportReport,
            )
    return router
