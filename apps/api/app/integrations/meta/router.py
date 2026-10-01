"""REST endpoints for meta under ``/api/v1/meta-business``. Business-licensed — see LICENSE.

Deny-by-default: every route declares one of the four ``meta.*`` permissions (§15), with one
stated exception — the address Meta itself fetches Instagram media from, which no session can
reach by definition and which is a capability rather than a route (see ``media.py``).

**These routes are the MCP surface** (CLAUDE.md §12). The handlers are therefore named for
what an agent would ask for — ``schedule_social_post``, not ``schedule`` — and the prefix is
``/meta-business`` rather than ``/meta``: core already serves ``/api/v1/meta`` (``/meta/me``,
``/meta/tenant``), and a section is derived from the prefix, so the shorter one would have
put core's routes, one of them unauthenticated, in this integration's tool list.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import func, select

from app.config import settings
from app.core import directory
from app.core.hosts import org_base_url
from app.core.metagraph import KIND_AD_ACCOUNT, KIND_INSTAGRAM, KIND_PAGE
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, request_hostname, require_context, resolve_org
from app.core.timezone import org_today
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.integrations.meta import media as media_lib
from app.integrations.meta import reads
from app.integrations.meta.checks import asset_issues, has_errors
from app.integrations.meta.models import (
    WORKING_POST_STATUSES,
    MetaAsset,
    MetaCredential,
    MetaSettings,
    Scheduler,
)
from app.integrations.meta.posts import PostService, PostView, parse_statuses
from app.integrations.meta.publisher import post_title
from app.integrations.meta.schemas import (
    MetaAssetKind,
    MetaAssetPage,
    MetaAssetRead,
    MetaAssetUpdate,
    MetaCredentialCreate,
    MetaCredentialRead,
    MetaCredentialUpdate,
    MetaDiscoveryRead,
    MetaInsightsRead,
    MetaPublishedPost,
    MetaSettingsRead,
    MetaSettingsWrite,
    MetaStatusRead,
    SocialPostCounts,
    SocialPostCreate,
    SocialPostImage,
    SocialPostIssue,
    SocialPostMediaRead,
    SocialPostPage,
    SocialPostRead,
    SocialPostSchedule,
    SocialPostTargetRead,
    SocialPostUpdate,
)
from app.integrations.meta.service import (
    RECOMMENDED_SCOPES,
    REFRESH_WINDOW,
    MetaService,
    capabilities,
    days_left,
    missing_scopes,
)

router = APIRouter(prefix="/meta-business", tags=["meta"])

_MANAGE = "meta.settings.manage"
_READ = "meta.asset.read"
_WRITE = "meta.post.write"
_PUBLISH = "meta.post.publish"


# --- shapes ---------------------------------------------------------------------------------- #
def _settings(row: MetaSettings | None, ctx: RequestContext) -> MetaSettingsRead:
    return MetaSettingsRead(
        app_id=row.app_id if row else None,
        app_secret_configured=bool(row and row.app_secret_encrypted),
        writes_enabled=row.writes_enabled if row else True,
        facebook_scheduler=row.facebook_scheduler if row else Scheduler.SCHAKL.value,
        api_version=settings.meta_api_version,
        recommended_scopes=list(RECOMMENDED_SCOPES),
        media_url_prefix=f"{org_base_url(ctx.org)}/api/v1/meta-business/media/",
    )


def _credential(
    row: MetaCredential, app_id: str | None, counts: dict[uuid.UUID, tuple[int, int]]
) -> MetaCredentialRead:
    scopes = [str(s) for s in (row.scopes or [])]
    total, linked = counts.get(row.id, (0, 0))
    due = None
    if row.expires_at is not None and row.active and row.status != "expired":
        due = max(row.expires_at - REFRESH_WINDOW, datetime.now(UTC))
    return MetaCredentialRead(
        id=row.id,
        label=row.label,
        business_id=row.business_id,
        business_name=row.business_name,
        subject_id=row.subject_id,
        subject_name=row.subject_name,
        token_kind=row.token_kind,
        app_matches=(row.token_app_id == app_id) if row.token_app_id and app_id else None,
        scopes=scopes,
        # Only a token whose scopes were actually read has any missing: "we never looked"
        # must not print as a list of nine things to fix.
        missing_scopes=missing_scopes(scopes) if scopes else [],
        capabilities=capabilities(scopes) if scopes else {},
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        days_left=days_left(row.expires_at),
        data_access_expires_at=row.data_access_expires_at,
        refreshed_at=row.refreshed_at,
        refresh_attempted_at=row.refresh_attempted_at,
        refresh_error=row.refresh_error,
        refresh_due_at=due,
        active=row.active,
        status=row.status,
        last_error=row.last_error,
        last_verified_at=row.last_verified_at,
        last_discovered_at=row.last_discovered_at,
        asset_count=total,
        linked_asset_count=linked,
    )


def _meta_url(row: MetaAsset) -> str | None:
    if row.kind == KIND_PAGE:
        return f"https://www.facebook.com/{row.external_id}"
    if row.kind == KIND_INSTAGRAM and row.username:
        return f"https://www.instagram.com/{row.username}/"
    if row.kind == KIND_AD_ACCOUNT:
        return f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={row.external_id}"
    return None


async def _asset_reads(ctx: RequestContext, rows: list[MetaAsset]) -> list[MetaAssetRead]:
    """A page of assets with everything a row prints, in a fixed number of queries."""
    if not rows:
        return []
    names = await directory.labels_for(ctx, "company", (row.company_id for row in rows))
    credential_ids = {row.credential_id for row in rows if row.credential_id}
    labels: dict[uuid.UUID, str] = {}
    if credential_ids:
        found = await ctx.session.execute(
            select(MetaCredential.id, MetaCredential.label).where(
                MetaCredential.org_id == ctx.org.id, MetaCredential.id.in_(credential_ids)
            )
        )
        labels = {row[0]: row[1] for row in found}
    page_ids = {row.linked_page_id for row in rows if row.linked_page_id}
    pages: dict[str, tuple[uuid.UUID, str]] = {}
    if page_ids:
        found = await ctx.session.execute(
            select(MetaAsset.external_id, MetaAsset.id, MetaAsset.name).where(
                MetaAsset.org_id == ctx.org.id,
                MetaAsset.kind == KIND_PAGE,
                MetaAsset.external_id.in_(page_ids),
            )
        )
        pages = {row[0]: (row[1], row[2]) for row in found}
    checks = await PostService(ctx).check_assets(rows)
    out = []
    for row in rows:
        blocked = None
        if row.kind != KIND_AD_ACCOUNT:
            problems = asset_issues(checks[row.id])
            blocked = problems[0].code if problems else None
        page = pages.get(row.linked_page_id) if row.linked_page_id else None
        out.append(
            MetaAssetRead(
                id=row.id,
                kind=row.kind,  # type: ignore[arg-type]
                meta_id=row.external_id,
                name=row.name,
                username=row.username,
                picture_url=row.picture_url,
                linked_page_id=page[0] if page else None,
                linked_page_name=page[1] if page else None,
                relation=row.relation,
                company_id=row.company_id,
                company_name=names.get(row.company_id) if row.company_id else None,
                credential_id=row.credential_id,
                credential_label=labels.get(row.credential_id) if row.credential_id else None,
                active=row.active,
                tasks=[str(t) for t in (row.tasks or [])],
                can_publish=row.kind != KIND_AD_ACCOUNT and blocked is None,
                blocked_by=blocked,
                currency=row.currency,
                timezone=row.timezone,
                account_status=row.account_status,
                dsa_beneficiary=row.dsa_beneficiary,
                dsa_payor=row.dsa_payor,
                status=row.status,
                last_error=row.last_error,
                observed_at=row.observed_at,
                last_verified_at=row.last_verified_at,
                meta_url=_meta_url(row),
            )
        )
    return out


async def _post_reads(ctx: RequestContext, views: list[PostView]) -> list[SocialPostRead]:
    names = await directory.labels_for(ctx, "company", (v.post.company_id for v in views))
    out = []
    for view in views:
        post = view.post
        targets = []
        for target in view.targets:
            asset = view.assets.get(target.asset_id)
            targets.append(
                SocialPostTargetRead(
                    id=target.id,
                    asset_id=target.asset_id,
                    asset_name=asset.name if asset else "",
                    asset_username=asset.username if asset else None,
                    asset_picture_url=asset.picture_url if asset else None,
                    channel=target.channel,
                    body_override=target.body_override,
                    status=target.status,
                    scheduler=target.scheduler,  # type: ignore[arg-type]
                    meta_post_id=target.external_id,
                    permalink=target.permalink,
                    attempts=target.attempts,
                    published_at=target.published_at,
                    last_error=target.last_error,
                    last_error_code=target.last_error_code,
                )
            )
        out.append(
            SocialPostRead(
                id=post.id,
                company_id=post.company_id,
                company_name=names.get(post.company_id) if post.company_id else None,
                format=post.format,  # type: ignore[arg-type]
                title=post_title(post),
                body=post.body,
                link=post.link,
                media=[SocialPostMediaRead(**item.as_json()) for item in view.media],
                notes=post.notes,
                scheduled_at=post.scheduled_at,
                status=post.status,  # type: ignore[arg-type]
                published_at=post.published_at,
                created_by_user_id=post.created_by_user_id,
                created_by_name=post.created_by_name,
                approved_by_user_id=post.approved_by_user_id,
                approved_by_name=post.approved_by_name,
                approved_at=post.approved_at,
                created_at=post.created_at,
                updated_at=post.updated_at,
                targets=targets,
                issues=[SocialPostIssue(**issue.as_json()) for issue in view.issues],
                ready=bool(view.targets) and not has_errors(view.issues),
            )
        )
    return out


async def _one(ctx: RequestContext, service: PostService, post_id: uuid.UUID) -> SocialPostRead:
    post = await service.get(post_id)
    await ctx.session.refresh(post)
    return (await _post_reads(ctx, [await service.view(post)]))[0]


# --- settings -------------------------------------------------------------------------------- #
@router.get(
    "/settings",
    response_model=MetaSettingsRead,
    dependencies=[require_permission(_MANAGE)],
)
async def get_meta_settings(ctx: RequestContext = Depends(require_context)) -> MetaSettingsRead:
    """The org's Meta app, the write switch, and who schedules a Facebook post."""
    return _settings(await MetaService(ctx).settings_row(), ctx)


@router.put(
    "/settings",
    response_model=MetaSettingsRead,
    dependencies=[require_permission(_MANAGE)],
)
async def save_meta_settings(
    payload: MetaSettingsWrite, ctx: RequestContext = Depends(require_context)
) -> MetaSettingsRead:
    """Save the app and the posture. The app secret is write-only: send it to set it, send
    ``null`` to clear it, leave it out to keep it."""
    row = await MetaService(ctx).save_settings(
        app_id=payload.app_id,
        app_secret=payload.app_secret,
        writes_enabled=payload.writes_enabled,
        facebook_scheduler=payload.facebook_scheduler,
        app_id_set="app_id" in payload.model_fields_set,
        app_secret_set="app_secret" in payload.model_fields_set,
    )
    return _settings(row, ctx)


@router.get(
    "/status",
    response_model=MetaStatusRead,
    dependencies=[require_permission(_READ)],
)
async def get_meta_status(ctx: RequestContext = Depends(require_context)) -> MetaStatusRead:
    """Whether Meta is connected, how many channels are linked, and who schedules — the
    answer to "why is this planner empty", for anyone who may read the channels."""
    return MetaStatusRead(**await MetaService(ctx).status())


# --- credentials ----------------------------------------------------------------------------- #
async def _credential_counts(ctx: RequestContext) -> dict[uuid.UUID, tuple[int, int]]:
    rows = await ctx.session.execute(
        select(
            MetaAsset.credential_id,
            func.count(),
            func.count().filter(MetaAsset.active.is_(True)),
        )
        .where(MetaAsset.org_id == ctx.org.id, MetaAsset.credential_id.isnot(None))
        .group_by(MetaAsset.credential_id)
    )
    return {row[0]: (int(row[1]), int(row[2])) for row in rows}


async def _credential_read(ctx: RequestContext, row: MetaCredential) -> MetaCredentialRead:
    await ctx.session.flush()
    await ctx.session.refresh(row)
    settings_row = await MetaService(ctx).settings_row()
    return _credential(
        row, settings_row.app_id if settings_row else None, await _credential_counts(ctx)
    )


@router.get(
    "/credentials",
    response_model=list[MetaCredentialRead],
    dependencies=[require_permission(_MANAGE)],
)
async def list_meta_credentials(
    ctx: RequestContext = Depends(require_context),
) -> list[MetaCredentialRead]:
    """The system-user tokens this org holds, with each one's clock: when it expires, when it
    was last refreshed, and what the last refresh said."""
    service = MetaService(ctx)
    rows = await service.list_credentials()
    settings_row = await service.settings_row()
    counts = await _credential_counts(ctx)
    return [_credential(row, settings_row.app_id if settings_row else None, counts) for row in rows]


@router.post(
    "/credentials",
    response_model=MetaCredentialRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_MANAGE)],
)
async def add_meta_credential(
    payload: MetaCredentialCreate, ctx: RequestContext = Depends(require_context)
) -> MetaCredentialRead:
    """Store a system-user token and ask Meta at once what it is.

    Never fails for a token that answered badly: the outcome is on the row (``status``,
    ``last_error``, ``missing_scopes``), where the screen draws it beside the token that has
    the problem.
    """
    row = await MetaService(ctx).create_credential(
        label=payload.label, token=payload.token, business_id=payload.business_id
    )
    return await _credential_read(ctx, row)


@router.patch(
    "/credentials/{credential_id}",
    response_model=MetaCredentialRead,
    dependencies=[require_permission(_MANAGE)],
)
async def update_meta_credential(
    credential_id: uuid.UUID,
    payload: MetaCredentialUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MetaCredentialRead:
    service = MetaService(ctx)
    row = await service.get_credential(credential_id)
    await service.update_credential(
        row,
        label=payload.label,
        token=payload.token,
        business_id=payload.business_id,
        active=payload.active,
    )
    return await _credential_read(ctx, row)


@router.delete(
    "/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission(_MANAGE)],
)
async def remove_meta_credential(
    credential_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Forget the token here. **Nothing is revoked at Meta** — that is done in Business
    Settings, where the token was made. The assets stay, dormant, with their clients."""
    await MetaService(ctx).delete_credential(credential_id)


@router.post(
    "/credentials/{credential_id}/verify",
    response_model=MetaCredentialRead,
    dependencies=[require_permission(_MANAGE)],
)
async def verify_meta_credential(
    credential_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MetaCredentialRead:
    """Ask Meta what this token is — its scopes, its expiry, whose it is — and record it."""
    return await _credential_read(ctx, await MetaService(ctx).verify(credential_id))


@router.post(
    "/credentials/{credential_id}/refresh",
    response_model=MetaCredentialRead,
    dependencies=[require_permission(_MANAGE)],
)
async def refresh_meta_credential(
    credential_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MetaCredentialRead:
    """Exchange an expiring token for another sixty days, now.

    The nightly job does this by itself once a token has twenty days left. It needs the app
    secret and a token that is still alive: one already past its expiry cannot be refreshed
    and has to be generated again in Business Settings.
    """
    return await _credential_read(ctx, await MetaService(ctx).refresh(credential_id, force=True))


@router.post(
    "/credentials/{credential_id}/discover",
    response_model=MetaDiscoveryRead,
    dependencies=[require_permission(_MANAGE)],
)
async def discover_meta_assets(
    credential_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MetaDiscoveryRead:
    """Read every Page, Instagram account and ad account this token reaches. Links nothing:
    a new asset arrives switched off, and a person says whose it is."""
    result = await MetaService(ctx).discover(credential_id)
    return MetaDiscoveryRead(found=result.found, created=result.created, warnings=result.warnings)


# --- assets ---------------------------------------------------------------------------------- #
@router.get(
    "/assets",
    response_model=MetaAssetPage,
    dependencies=[require_permission(_READ)],
)
async def list_meta_assets(
    kind: MetaAssetKind | None = Query(default=None),
    company_id: uuid.UUID | None = Query(default=None),
    active_only: bool = Query(
        default=True,
        description="Only the assets that are linked and worked on. False lists every "
        "asset a token has found, which is what the linking screen needs.",
    ),
    unlinked_only: bool = Query(
        default=False, description="Only what a token has found and nobody has linked yet."
    ),
    q: str | None = Query(
        default=None, max_length=200, description="A name, an @handle, or Meta's own id."
    ),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    count: bool = Query(default=True),
    ctx: RequestContext = Depends(require_context),
) -> MetaAssetPage:
    """The Pages, Instagram accounts and ad accounts this org works on — **start here**.

    Every other tool takes one of these ``id`` values. ``can_publish`` says whether a post
    can go to a channel as things stand, and ``blocked_by`` says why not.
    """
    service = MetaService(ctx)
    filters = {
        "kind": kind,
        "company_id": company_id,
        "active_only": active_only and not unlinked_only,
        "unlinked_only": unlinked_only,
        "q": q,
    }
    rows = await service.list_assets(**filters, limit=limit, offset=offset)
    total = await service.count_assets(**filters) if count else len(rows)
    return MetaAssetPage(
        items=await _asset_reads(ctx, rows), total=total, limit=limit, offset=offset
    )


@router.get(
    "/assets/{asset_id}",
    response_model=MetaAssetRead,
    dependencies=[require_permission(_READ)],
)
async def get_meta_asset(
    asset_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MetaAssetRead:
    return (await _asset_reads(ctx, [await MetaService(ctx).get_asset(asset_id)]))[0]


@router.patch(
    "/assets/{asset_id}",
    response_model=MetaAssetRead,
    dependencies=[require_permission(_MANAGE)],
)
async def update_meta_asset(
    asset_id: uuid.UUID,
    payload: MetaAssetUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAssetRead:
    """Say whose asset this is, and whether it is worked on here. Naming a client switches
    the asset on; ``company_id: null`` makes it the agency's own."""
    service = MetaService(ctx)
    row = await service.get_asset(asset_id)
    await service.update_asset(
        row,
        company_id=payload.company_id,
        active=payload.active,
        company_id_set="company_id" in payload.model_fields_set,
    )
    await ctx.session.refresh(row)
    return (await _asset_reads(ctx, [row]))[0]


@router.get(
    "/assets/{asset_id}/published",
    response_model=list[MetaPublishedPost],
    dependencies=[require_permission(_READ)],
)
async def list_meta_published_posts(
    asset_id: uuid.UUID,
    limit: int = Query(default=25, ge=1, le=reads.MAX_PUBLISHED),
    ctx: RequestContext = Depends(require_context),
) -> list[MetaPublishedPost]:
    """What is live on this channel right now, newest first — whoever posted it.

    Read from Meta on every call. It includes what the client posted themselves, which the
    planned posts here never will.
    """
    service = MetaService(ctx)
    asset = await service.get_asset(asset_id)
    if asset.kind == KIND_AD_ACCOUNT:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"asset_id": "errors.meta_asset_not_a_channel"},
        )
    async with service.open_client(asset_id, tool="published") as (client, row):
        caller = await service.page_client(client, row) if row.kind == KIND_PAGE else client
        rows = await reads.published(caller, row, limit=limit)
    return [MetaPublishedPost(**entry) for entry in rows]


@router.get(
    "/assets/{asset_id}/insights",
    response_model=MetaInsightsRead,
    dependencies=[require_permission(_READ)],
)
async def meta_asset_insights(
    asset_id: uuid.UUID,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> MetaInsightsRead:
    """Views, reach and engagement for a Page or Instagram account over a span of days.

    Defaults to the last 28 whole days. A metric Meta did not answer for is listed under
    ``unavailable`` rather than reported as zero.
    """
    service = MetaService(ctx)
    today = await org_today(ctx.session, ctx.org.id)
    end = date_to or (today - timedelta(days=1))
    start = date_from or (end - timedelta(days=27))
    if start > end:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"date_from": "errors.validation"},
        )
    asset = await service.get_asset(asset_id)
    if asset.kind == KIND_AD_ACCOUNT:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"asset_id": "errors.meta_asset_not_a_channel"},
        )
    async with service.open_client(asset_id, tool="insights") as (client, row):
        caller = await service.page_client(client, row) if row.kind == KIND_PAGE else client
        result = await reads.insights(caller, row, date_from=start, date_to=end)
    return MetaInsightsRead(asset_id=asset_id, **result)


# --- posts ----------------------------------------------------------------------------------- #
@router.get(
    "/posts",
    response_model=SocialPostPage,
    dependencies=[require_permission(_READ)],
)
async def list_social_posts(
    status_: str | None = Query(
        default=None,
        alias="status",
        description="A comma-separated set of statuses, or 'working' for everything still "
        "going on. Absent means every status.",
    ),
    company_id: uuid.UUID | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    channel: str | None = Query(default=None, pattern="^(facebook|instagram)$"),
    q: str | None = Query(default=None, max_length=200),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    sort: str | None = Query(
        default=None,
        description="when | created | status, with a leading - for descending. Absent is "
        "soonest first.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    count: bool = Query(default=True),
    ctx: RequestContext = Depends(require_context),
) -> SocialPostPage:
    """Planned and published posts, with each channel's own outcome.

    The dates filter on when a post went out, or is due to. A page is a page: ``total`` is
    the number of posts the filters match, not the number returned.
    """
    service = PostService(ctx)
    rows, total = await service.list(
        statuses=parse_statuses(status_),
        company_id=company_id,
        asset_id=asset_id,
        channel=channel,
        q=q,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        limit=limit,
        offset=offset,
        count=count,
    )
    items = await _post_reads(ctx, await service.views(rows))
    return SocialPostPage(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/posts/counts",
    response_model=SocialPostCounts,
    dependencies=[require_permission(_READ)],
)
async def social_post_counts(
    company_id: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> SocialPostCounts:
    """How many posts stand in each status — what needs approval, what failed."""
    by_status = await PostService(ctx).counts(company_id=company_id)
    return SocialPostCounts(
        by_status=by_status,
        working=sum(by_status.get(s, 0) for s in WORKING_POST_STATUSES),
        total=sum(by_status.values()),
    )


@router.post(
    "/posts",
    response_model=SocialPostRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def create_social_post(
    payload: SocialPostCreate, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """Write a draft. **Nothing is published and nothing is scheduled.**

    ``asset_ids`` are the channels (from ``list_meta_assets``) and must all be one client's.
    Images are attached afterwards: upload each to ``/files`` with
    ``entity_type=meta_post`` and this post's id, then name the file ids in ``media`` on an
    update. The answer carries ``issues`` — what would stop this post being scheduled.
    """
    service = PostService(ctx)
    post = await service.create(
        asset_ids=payload.asset_ids,
        format=payload.format,
        body=payload.body,
        link=payload.link,
        notes=payload.notes,
        scheduled_at=payload.scheduled_at,
        overrides=payload.overrides,
    )
    return await _one(ctx, service, post.id)


@router.get(
    "/posts/{post_id}",
    response_model=SocialPostRead,
    dependencies=[require_permission(_READ)],
)
async def get_social_post(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    return await _one(ctx, PostService(ctx), post_id)


@router.patch(
    "/posts/{post_id}",
    response_model=SocialPostRead,
    dependencies=[require_permission(_WRITE)],
)
async def update_social_post(
    post_id: uuid.UUID,
    payload: SocialPostUpdate,
    ctx: RequestContext = Depends(require_context),
) -> SocialPostRead:
    """Change a post that has not gone out.

    A draft needs the drafting permission. A post that is already **scheduled** needs the
    publishing one, because the change is what will be published — and is refused if it
    would make the post unpublishable.
    """
    service = PostService(ctx)
    post = await service.get(post_id)
    values = payload.model_dump(exclude_unset=True)
    if "media" in values and values["media"] is not None:
        values["media"] = [item.model_dump() for item in payload.media or []]
    await service.update(post, values=values)
    return await _one(ctx, service, post_id)


@router.delete(
    "/posts/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission(_WRITE)],
)
async def delete_social_post(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Delete a post that never went out. One that reached a channel is a record of what was
    said in a client's name, and stays."""
    service = PostService(ctx)
    await service.delete(await service.get(post_id))


@router.post(
    "/posts/{post_id}/offer",
    response_model=SocialPostRead,
    dependencies=[require_permission(_WRITE)],
)
async def offer_social_post(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """Hand a draft to whoever may schedule it. Still published nowhere."""
    service = PostService(ctx)
    await service.offer(await service.get(post_id))
    return await _one(ctx, service, post_id)


@router.post(
    "/posts/{post_id}/recall",
    response_model=SocialPostRead,
    dependencies=[require_permission(_WRITE)],
)
async def recall_social_post(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """Take a post back from review to keep writing it."""
    service = PostService(ctx)
    await service.recall(await service.get(post_id))
    return await _one(ctx, service, post_id)


@router.post(
    "/posts/{post_id}/schedule",
    response_model=SocialPostRead,
    dependencies=[require_permission(_PUBLISH)],
)
async def schedule_social_post(
    post_id: uuid.UUID,
    payload: SocialPostSchedule,
    ctx: RequestContext = Depends(require_context),
) -> SocialPostRead:
    """Approve a post and give it a time. **It will be published at that time.**

    Refused with every reason named (``details.issues``) while the post has a problem. A
    naive ``scheduled_at`` is read on the org's own clock.
    """
    service = PostService(ctx)
    await service.schedule(await service.get(post_id), scheduled_at=payload.scheduled_at)
    return await _one(ctx, service, post_id)


@router.post(
    "/posts/{post_id}/publish",
    response_model=SocialPostRead,
    dependencies=[require_permission(_PUBLISH)],
)
async def publish_social_post_now(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """Publish a post to its channels **now**.

    Answers at once with the post ``scheduled`` for this moment; the worker publishes it
    within the minute. Read the post again for each channel's outcome.
    """
    service = PostService(ctx)
    await service.schedule(await service.get(post_id), now=True)
    return await _one(ctx, service, post_id)


@router.post(
    "/posts/{post_id}/unschedule",
    response_model=SocialPostRead,
    dependencies=[require_permission(_PUBLISH)],
)
async def unschedule_social_post(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """Take a scheduled post back to a draft. Nothing has gone out and nothing will."""
    service = PostService(ctx)
    await service.unschedule(await service.get(post_id))
    return await _one(ctx, service, post_id)


@router.post(
    "/posts/{post_id}/cancel",
    response_model=SocialPostRead,
    dependencies=[require_permission(_WRITE)],
)
async def cancel_social_post(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """Withdraw a post for good, keeping it as a record of what was planned. Cancelling one
    that is already scheduled needs the publishing permission."""
    service = PostService(ctx)
    await service.cancel(await service.get(post_id))
    return await _one(ctx, service, post_id)


@router.post(
    "/posts/{post_id}/retry",
    response_model=SocialPostRead,
    dependencies=[require_permission(_PUBLISH)],
)
async def retry_social_post(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """Try the channels that failed once more. The ones that went out are left alone."""
    service = PostService(ctx)
    await service.retry(await service.get(post_id))
    return await _one(ctx, service, post_id)


@router.post(
    "/posts/{post_id}/images",
    response_model=SocialPostRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def add_social_post_image(
    post_id: uuid.UUID, payload: SocialPostImage, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """Add a picture to a post: base64 in, the post with its new picture and its checks out.

    JPEG, PNG or WebP. Instagram takes ratios from 4:5 to 1.91:1, and the answer's ``issues``
    say so when this picture is outside them. To reorder or remove pictures, send the
    ``media`` list you want with the update.
    """
    service = PostService(ctx)
    post = await service.attach_image(
        await service.get(post_id),
        filename=payload.filename,
        content_type=payload.content_type,
        data=payload.data,
        alt=payload.alt,
    )
    return await _one(ctx, service, post.id)


@router.post(
    "/posts/{post_id}/duplicate",
    response_model=SocialPostRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def duplicate_social_post(
    post_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SocialPostRead:
    """A new draft with the same words and channels. The images are not copied."""
    service = PostService(ctx)
    copy = await service.duplicate(await service.get(post_id))
    return await _one(ctx, service, copy.id)


# --- the address Meta fetches from ------------------------------------------------------------ #
@router.get(
    "/media/{name}",
    include_in_schema=False,
    dependencies=[
        no_permission_required(
            "Instagram's API fetches media from a public URL, so no session can exist here; "
            "the path is a short-lived capability for one file of a delivery in flight, "
            "withdrawn when the delivery ends"
        )
    ],
)
async def serve_meta_media(name: str, request: Request) -> Response:
    """One file, to whoever holds its address — for as long as a delivery needs it.

    The org comes from the hostname and nothing else. Unknown, expired and withdrawn are one
    answer, so a stranger learns nothing from which it was. ``no-referrer`` and ``noindex``
    because a credential in a path must never travel in a ``Referer`` (#304).
    """
    token = name.rsplit(".", 1)[0]
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            raise AppError("unknown_host", "errors.unknown_host", status_code=404)
        await set_current_org(session, org.id)
        stored = await media_lib.file_for_token(session, org.id, token)
    if stored is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    raw = await media_lib.read_bytes(stored)
    media_type = stored.content_type
    if stored.content_type in media_lib.IMAGE_TYPES:
        raw = await asyncio.to_thread(media_lib.as_jpeg, raw)
        media_type = "image/jpeg"
    return Response(
        raw,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
