"""REST endpoints for companies under ``/api/v1/companies`` (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from sqlalchemy import select

from app.config import settings
from app.core.models import OrgSettings
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.modules.companies.groups import groups_router
from app.modules.companies.models import Company
from app.modules.companies.schemas import (
    ClientNumberBackfillResult,
    CompanyCreate,
    CompanyNumberingRead,
    CompanyNumberingWrite,
    CompanyRead,
    CompanyUpdate,
)
from app.modules.companies.service import CompanyService, CompanySettingsService
from app.schemas import Page, PanelData, SummaryData

router = APIRouter(prefix="/companies", tags=["companies"])
# The horizon admin surface (#191) registers *first*: `/companies/groups/...` must match its
# literal routes, never fall into `/companies/{company_id}` below.
router.include_router(groups_router)


async def _enabled_modules(ctx: RequestContext) -> list[str]:
    org_settings = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == ctx.org.id)
    )
    if org_settings and org_settings.enabled_modules:
        return list(org_settings.enabled_modules)
    return list(settings.enabled_modules)


# --------------------------------------------------------------------------- #
# Settings — client numbering
#
# Above ``/{company_id}`` for the same reason ``groups_router`` is: "settings" would otherwise
# be parsed as a company id and 422 on every request.
# --------------------------------------------------------------------------- #
@router.get(
    "/settings",
    response_model=CompanyNumberingRead,
    dependencies=[require_permission("companies.settings.manage")],
)
async def get_company_settings(
    ctx: RequestContext = Depends(require_context),
) -> CompanyNumberingRead:
    """How this organisation numbers its clients (klantnummer format + sequence)."""
    return CompanyNumberingRead.model_validate(await CompanySettingsService(ctx).row())


@router.put(
    "/settings",
    response_model=CompanyNumberingRead,
    dependencies=[require_permission("companies.settings.manage")],
)
async def update_company_settings(
    payload: CompanyNumberingWrite,
    ctx: RequestContext = Depends(require_context),
) -> CompanyNumberingRead:
    return CompanyNumberingRead.model_validate(await CompanySettingsService(ctx).save(payload))


@router.post(
    "/settings/backfill-client-numbers",
    response_model=ClientNumberBackfillResult,
    dependencies=[require_permission("companies.settings.manage")],
)
async def backfill_client_numbers(
    ctx: RequestContext = Depends(require_context),
) -> ClientNumberBackfillResult:
    """Number every client that has no number yet, oldest first.

    Only fills blanks — an existing number is never rewritten, so this is safe to run twice.
    """
    return ClientNumberBackfillResult(
        numbered=await CompanySettingsService(ctx).backfill_client_numbers()
    )


@router.get(
    "",
    response_model=Page[CompanyRead],
    dependencies=[require_permission("companies.company.read")],
)
async def list_companies(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200),
    status: str | None = Query(
        None,
        max_length=200,
        description=(
            "Lifecycle status; comma-separate for several ('lead,onboarding,active'). "
            "Absent means every status, the archived ones included — the screen picks its own "
            "default, this endpoint does not."
        ),
    ),
    mine: bool = Query(False, description="Only clients I'm assigned to (primary or not)"),
    sort: str | None = Query(
        None,
        description=(
            "name | client_number | status | created_at | updated_at, '-' desc. Default: name"
        ),
    ),
    hours: bool = Query(
        False, description="Include the budget roll-up; costs three grouped queries"
    ),
    count: bool = Query(True, description="Compute total; set false for name-only lookups"),
    ctx: RequestContext = Depends(require_context),
) -> Page[CompanyRead]:
    items, total = await CompanyService(ctx).list(
        limit=limit, offset=offset, q=q, status=status, mine=mine, sort=sort,
        # A client never pays for — or reads — the budget roll-up (#449, the projects rule
        # one module over): the hours the agency spends against its own budgets are what it
        # agreed with itself, and the web draws no column for what the API withholds.
        hours=hours and not ctx.is_portal, count=count,
    )
    return Page(
        items=[CompanyRead.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=CompanyRead,
    status_code=201,
    dependencies=[require_permission("companies.company.write")],
)
async def create_company(
    payload: CompanyCreate,
    ctx: RequestContext = Depends(require_context),
) -> CompanyRead:
    company = await CompanyService(ctx).create(payload)
    return CompanyRead.model_validate(company)


@router.get(
    "/{company_id}",
    response_model=CompanyRead,
    dependencies=[require_permission("companies.company.read")],
)
async def get_company(
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> CompanyRead:
    company = await CompanyService(ctx).get(company_id)
    return CompanyRead.model_validate(company)


@router.patch(
    "/{company_id}",
    response_model=CompanyRead,
    dependencies=[require_permission("companies.company.write")],
)
async def update_company(
    company_id: uuid.UUID,
    payload: CompanyUpdate,
    ctx: RequestContext = Depends(require_context),
) -> CompanyRead:
    company = await CompanyService(ctx).update(company_id, payload)
    return CompanyRead.model_validate(company)


@router.delete(
    "/{company_id}",
    status_code=204,
    dependencies=[require_permission("companies.company.delete")],
)
async def delete_company(
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await CompanyService(ctx).delete(company_id)


@router.get(
    "/{company_id}/panels",
    response_model=list[PanelData],
    dependencies=[require_permission("companies.company.read")],
)
async def company_panels(
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[PanelData]:
    """Compose the detail-view panels contributed by every enabled module (the hub).

    Only the panels **this caller may read** (#365): the composition used to declare
    ``companies.company.read`` once and then call thirteen providers, so a member holding
    exactly that key received the client's tasks, hours, domains and full change history.
    ``panels_for`` takes ``ctx.can`` and the provider is never called.
    """
    # Import here to avoid a module→registry import cycle at load time.
    from app.registry import registry

    # Ensure the company exists / is in-tenant before composing panels.
    await CompanyService(ctx).get(company_id)

    enabled = await _enabled_modules(ctx)
    panels: list[PanelData] = []
    for spec in registry.panels_for("company", enabled, ctx.can, ctx.is_portal):
        data = await spec.provider(ctx, company_id)
        panels.append(
            PanelData(
                key=spec.key,
                title_key=spec.title_key,
                position=spec.position,
                data=data,
                prominence=spec.prominence,
                size=spec.size,
                # The module reads its own payload (#364); a panel with no predicate is never
                # called empty, because "said nothing" and "said there is nothing" differ.
                empty=bool(spec.empty_when(data)) if spec.empty_when else False,
            )
        )
    return panels


@router.get(
    "/{company_id}/summary",
    response_model=list[SummaryData],
    dependencies=[require_permission("companies.company.read")],
)
async def company_summary(
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[SummaryData]:
    """The client's vital signs (#364) — openstaand, uren, open taken, laatste contact, verlenging.

    Every one of these was already derivable from a panel the reader had to scroll to and add up
    by eye. Same seam as the panels one level up: the module owns the number and where it opens,
    core owns the strip, and this page gains no per-module code.
    """
    from app.registry import registry

    await CompanyService(ctx).get(company_id)

    enabled = await _enabled_modules(ctx)
    tiles: list[SummaryData] = []
    for spec in registry.summaries_for("company", enabled, ctx.can):
        for tile in await spec.provider(ctx, company_id):
            tiles.append(
                SummaryData(
                    key=tile.key,
                    label_key=tile.label_key,
                    value=tile.value,
                    format=tile.format,
                    currency=tile.currency,
                    tone=tile.tone,
                    hint_key=tile.hint_key,
                    hint_params=tile.hint_params,
                    href=tile.href,
                    position=tile.position or spec.position,
                )
            )
    return sorted(tiles, key=lambda t: (t.position, t.key))


# --------------------------------------------------------------------------- #
# Per-client logo (#196) — a StoredFile hung off the company, served tenant- and
# horizon-scoped. Never the anonymous branding path: a client's logo is client data.
# --------------------------------------------------------------------------- #
@router.post(
    "/{company_id}/logo",
    response_model=CompanyRead,
    dependencies=[require_permission("companies.company.write")],
)
async def upload_company_logo(
    company_id: uuid.UUID,
    file: UploadFile,
    ctx: RequestContext = Depends(require_context),
) -> CompanyRead:
    """Upload or replace the client's logo. Images only, bounded by the instance caps."""
    from app.core.activity import ActivityService
    from app.core.storage.models import StoredFile
    from app.core.storage.service import drop_file, write_file

    service = CompanyService(ctx)
    company = await service.get(company_id)  # tenant + horizon scoped: 404 outside

    content_type = file.content_type or "application/octet-stream"
    if not content_type.startswith("image/"):
        raise AppError(
            "validation",
            "errors.upload_type",
            status_code=422,
            fields={"file": "errors.upload_type"},
        )
    # The type allow-list, the size ceiling and the de-duplicated write all live in the
    # storage core; the *permission* is this route's own (a company writer sets a client's
    # logo without holding `files.file.write`), which is why this is not `FileService`.
    stored = await write_file(
        ctx,
        filename=file.filename or "logo",
        content_type=content_type,
        stream=file.file,
        entity_type="company_logo",
        entity_id=company.id,
    )
    previous_id = company.logo_file_id
    company = await ctx.repo(Company).update(company, logo_file_id=stored.id)
    if previous_id is not None:
        old = await ctx.repo(StoredFile).get(previous_id)
        if old is not None:
            # Never `storage_for(...).delete(key)` by hand here: replacing a logo with the
            # *same* image would delete the bytes the new row now shares.
            await drop_file(ctx, old)
    await ActivityService(ctx).record(
        "company", company.id, "logo_uploaded", {"filename": stored.filename}
    )
    return CompanyRead.model_validate(company)


@router.get(
    "/{company_id}/logo",
    dependencies=[require_permission("companies.company.read")],
)
async def serve_company_logo(
    company_id: uuid.UUID,
    request: Request,
    ctx: RequestContext = Depends(require_context),
):
    """The logo bytes — behind the same tenant + horizon check as the company itself, so a
    portal login only ever sees logos of companies in their horizon (#191/#193)."""
    from app.core.storage.models import StoredFile
    from app.core.storage.router import _file_response

    company = await CompanyService(ctx).get(company_id)
    if company.logo_file_id is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    stored = await ctx.repo(StoredFile).get_or_404(company.logo_file_id)
    return await _file_response(stored, request, ctx=ctx)


@router.delete(
    "/{company_id}/logo",
    response_model=CompanyRead,
    dependencies=[require_permission("companies.company.write")],
)
async def remove_company_logo(
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> CompanyRead:
    from app.core.activity import ActivityService
    from app.core.storage.models import StoredFile
    from app.core.storage.service import drop_file

    service = CompanyService(ctx)
    company = await service.get(company_id)
    if company.logo_file_id is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    stored = await ctx.repo(StoredFile).get(company.logo_file_id)
    company = await ctx.repo(Company).update(company, logo_file_id=None)
    if stored is not None:
        await drop_file(ctx, stored)
    await ActivityService(ctx).record("company", company.id, "logo_removed", {})
    return CompanyRead.model_validate(company)
