"""REST endpoints for wordpress under ``/api/v1/wordpress`` (docs/WORDPRESS.md, §6, §9).

Deny-by-default: every route declares a permission (§15). The split that matters is that
``site.manage`` gates the credential and ``site.read`` gates the *facts about* it — an agency
can let every account manager see that a client's site is connected and has Rank Math, without
letting anyone rotate a WordPress administrator password.

``/brands`` is the one read that dials out, and it declares ``site.read`` rather than
``site.manage``: choosing which Rank Math brand to attach to a client is marketing work, not
credential work. It stays out of ``marketing`` because the call needs this module's own client
and §6 forbids reaching across for it — marketing asks through ``app/core/wordpress.py``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.entitlements import license_write_gate
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.wordpress.schemas import (
    WordPressAbility,
    WordPressAbilityResult,
    WordPressAbilityRun,
    WordPressBrand,
    WordPressContentCreate,
    WordPressContentList,
    WordPressContentRead,
    WordPressContentWrite,
    WordPressFormCreate,
    WordPressFormRead,
    WordPressFormRow,
    WordPressFormWrite,
    WordPressMediaList,
    WordPressMediaRow,
    WordPressRestCall,
    WordPressRestResult,
    WordPressSiteCreate,
    WordPressSiteRead,
    WordPressSiteSummary,
    WordPressSiteUpdate,
    WordPressVerifyResult,
)
from app.integrations.wordpress.service import WordPressService
from app.integrations.wordpress.surface import WordPressSurfaceService

# The licence gate is mounted here rather than per route: past expiry + grace the module goes
# read-only, so the panel keeps showing what was last observed while connecting, rotating and
# disconnecting turn 402 (epic #140). `license_write_gate` reads the method, so every GET below
# — including `/brands`, which is a read that happens to travel — survives an expired licence.
router = APIRouter(
    prefix="/wordpress",
    tags=["wordpress"],
    dependencies=[license_write_gate("wordpress")],
)


@router.get(
    "/sites",
    response_model=list[WordPressSiteRead],
    dependencies=[require_permission("wordpress.site.read")],
)
async def list_sites(
    website_id: uuid.UUID | None = Query(None),
    company_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[WordPressSiteRead]:
    """Every connected site, or a website's, or a **client's** — the question an agent asks
    first, since a client is how it was told about the site."""
    return await WordPressService(ctx).list(website_id=website_id, company_id=company_id)


@router.post(
    "/sites",
    response_model=WordPressSiteRead,
    status_code=201,
    dependencies=[require_permission("wordpress.site.manage")],
)
async def connect_site(
    payload: WordPressSiteCreate,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteRead:
    return await WordPressService(ctx).create(payload)


@router.get(
    "/sites/by-website/{website_id}",
    response_model=WordPressSiteRead | None,
    dependencies=[require_permission("wordpress.site.read")],
)
async def site_for_website(
    website_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteRead | None:
    """The one credential a website has, or ``null``.

    Literal segment, so declared before ``/sites/{site_id}``. ``null`` rather than a 404
    because most websites have no WordPress connected and that is the panel's ordinary empty
    state, not an error worth logging once per page view.
    """
    return await WordPressService(ctx).for_website(website_id)


@router.get(
    "/sites/{site_id}",
    response_model=WordPressSiteRead,
    dependencies=[require_permission("wordpress.site.read")],
)
async def get_site(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteRead:
    return await WordPressService(ctx).get(site_id)


@router.patch(
    "/sites/{site_id}",
    response_model=WordPressSiteRead,
    dependencies=[require_permission("wordpress.site.manage")],
)
async def update_site(
    site_id: uuid.UUID,
    payload: WordPressSiteUpdate,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteRead:
    return await WordPressService(ctx).update(site_id, payload)


@router.delete(
    "/sites/{site_id}",
    status_code=204,
    dependencies=[require_permission("wordpress.site.manage")],
)
async def disconnect_site(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await WordPressService(ctx).delete(site_id)


@router.post(
    "/sites/{site_id}/verify",
    response_model=WordPressVerifyResult,
    dependencies=[require_permission("wordpress.site.manage")],
)
async def verify_site(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> WordPressVerifyResult:
    """Probe the site and store what was observed.

    Answers 200 for a credential that was refused: the per-capability answer *is* the response,
    and an exception is the one shape that cannot carry it. ``ok`` says whether anything got
    through at all.
    """
    return await WordPressService(ctx).verify(site_id)


@router.get(
    "/sites/{site_id}/brands",
    response_model=list[WordPressBrand],
    dependencies=[require_permission("wordpress.site.read")],
)
async def list_brands(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[WordPressBrand]:
    """The Rank Math brands this site tracks — the marketing link picker's options."""
    return await WordPressService(ctx).brands(site_id)


# ------------------------------------------------------------------ the site as a surface
#
# Everything below reaches into the client's WordPress with the stored credential and is what
# `/mcp/wordpress` serves (docs/WORDPRESS.md §7). A site is a parameter, never a tool: forty
# connected sites are forty rows above and zero routes here.


@router.get(
    "/sites/{site_id}/summary",
    response_model=WordPressSiteSummary,
    dependencies=[require_permission("wordpress.site.read")],
)
async def site_summary(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteSummary:
    """What the site is: name, WordPress and PHP version, post types, plugin namespaces.

    The read to make first: it says whether the site has forms, abilities, several
    languages, and which post types it answers for.
    """
    return await WordPressSurfaceService(ctx).summary(site_id)


@router.get(
    "/sites/{site_id}/content",
    response_model=WordPressContentList,
    dependencies=[require_permission("wordpress.content.read")],
)
async def list_site_content(
    site_id: uuid.UUID,
    type: str = Query("page", max_length=40),
    search: str | None = Query(None, max_length=200),
    status: str | None = Query(None, max_length=60),
    lang: str | None = Query(None, max_length=10),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> WordPressContentList:
    """Pages, posts or any custom post type, drafts included, newest change first.

    ``type`` is a post type slug from the summary; ``lang`` a WPML language on a multilingual
    site. ``total`` is the site's own count for the filter.
    """
    return await WordPressSurfaceService(ctx).list_content(
        site_id,
        type_slug=type,
        search=search,
        status=status,
        lang=lang,
        page=page,
        per_page=per_page,
    )


@router.post(
    "/sites/{site_id}/content",
    response_model=WordPressContentRead,
    status_code=201,
    dependencies=[require_permission("wordpress.content.write")],
)
async def create_site_content(
    site_id: uuid.UUID,
    payload: WordPressContentCreate,
    ctx: RequestContext = Depends(require_context),
) -> WordPressContentRead:
    """A new page or post — a draft unless ``status`` says otherwise, and a live status needs
    ``wordpress.content.publish``."""
    return await WordPressSurfaceService(ctx).create_content(site_id, payload)


@router.get(
    "/sites/{site_id}/content/{type}/{wp_id}",
    response_model=WordPressContentRead,
    dependencies=[require_permission("wordpress.content.read")],
)
async def get_site_content(
    site_id: uuid.UUID,
    type: str,
    wp_id: int,
    ctx: RequestContext = Depends(require_context),
) -> WordPressContentRead:
    """One record whole: raw content, rendered HTML, the ACF fields, the meta."""
    return await WordPressSurfaceService(ctx).get_content(site_id, type, wp_id)


@router.patch(
    "/sites/{site_id}/content/{type}/{wp_id}",
    response_model=WordPressContentRead,
    dependencies=[require_permission("wordpress.content.write")],
)
async def update_site_content(
    site_id: uuid.UUID,
    type: str,
    wp_id: int,
    payload: WordPressContentWrite,
    ctx: RequestContext = Depends(require_context),
) -> WordPressContentRead:
    """Change a record. Absent fields are left alone. Editing anything a visitor can see, or
    setting a live status, needs ``wordpress.content.publish``."""
    return await WordPressSurfaceService(ctx).update_content(site_id, type, wp_id, payload)


@router.get(
    "/sites/{site_id}/media",
    response_model=WordPressMediaList,
    dependencies=[require_permission("wordpress.content.read")],
)
async def list_site_media(
    site_id: uuid.UUID,
    search: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> WordPressMediaList:
    """The media library — what an ACF image id resolves to."""
    return await WordPressSurfaceService(ctx).list_media(
        site_id, search=search, page=page, per_page=per_page
    )


@router.get(
    "/sites/{site_id}/media/{wp_id}",
    response_model=WordPressMediaRow,
    dependencies=[require_permission("wordpress.content.read")],
)
async def get_site_media(
    site_id: uuid.UUID,
    wp_id: int,
    ctx: RequestContext = Depends(require_context),
) -> WordPressMediaRow:
    return await WordPressSurfaceService(ctx).get_media(site_id, wp_id)


@router.get(
    "/sites/{site_id}/forms",
    response_model=list[WordPressFormRow],
    dependencies=[require_permission("wordpress.forms.read")],
)
async def list_site_forms(
    site_id: uuid.UUID,
    search: str | None = Query(None, max_length=200),
    ctx: RequestContext = Depends(require_context),
) -> list[WordPressFormRow]:
    """The site's Contact Form 7 forms."""
    return await WordPressSurfaceService(ctx).list_forms(site_id, search=search)


@router.post(
    "/sites/{site_id}/forms",
    response_model=WordPressFormRead,
    status_code=201,
    dependencies=[require_permission("wordpress.forms.write")],
)
async def create_site_form(
    site_id: uuid.UUID,
    payload: WordPressFormCreate,
    ctx: RequestContext = Depends(require_context),
) -> WordPressFormRead:
    return await WordPressSurfaceService(ctx).create_form(site_id, payload)


@router.get(
    "/sites/{site_id}/forms/{wp_id}",
    response_model=WordPressFormRead,
    dependencies=[require_permission("wordpress.forms.read")],
)
async def get_site_form(
    site_id: uuid.UUID,
    wp_id: int,
    ctx: RequestContext = Depends(require_context),
) -> WordPressFormRead:
    """One form whole: template, field names, both mails, messages, extra settings."""
    return await WordPressSurfaceService(ctx).get_form(site_id, wp_id)


@router.patch(
    "/sites/{site_id}/forms/{wp_id}",
    response_model=WordPressFormRead,
    dependencies=[require_permission("wordpress.forms.write")],
)
async def update_site_form(
    site_id: uuid.UUID,
    wp_id: int,
    payload: WordPressFormWrite,
    ctx: RequestContext = Depends(require_context),
) -> WordPressFormRead:
    """Change a form. Live the moment it saves; absent fields are left alone."""
    return await WordPressSurfaceService(ctx).update_form(site_id, wp_id, payload)


@router.get(
    "/sites/{site_id}/abilities",
    response_model=list[WordPressAbility],
    dependencies=[require_permission("wordpress.ability.read")],
)
async def list_site_abilities(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[WordPressAbility]:
    """Every ability the site registers for REST, with its input schema and whether it is
    read-only — what ``run`` may be asked for."""
    return await WordPressSurfaceService(ctx).abilities(site_id)


@router.post(
    "/sites/{site_id}/abilities/run",
    response_model=WordPressAbilityResult,
    dependencies=[require_permission("wordpress.ability.read")],
)
async def run_site_ability(
    site_id: uuid.UUID,
    payload: WordPressAbilityRun,
    ctx: RequestContext = Depends(require_context),
) -> WordPressAbilityResult:
    """Run one ability by name. A read-only ability runs on ``wordpress.ability.read``; any
    other needs ``wordpress.ability.run`` — decided by the ability's own annotation."""
    return await WordPressSurfaceService(ctx).run_ability(site_id, payload)


@router.post(
    "/sites/{site_id}/rest",
    response_model=WordPressRestResult,
    dependencies=[require_permission("wordpress.rest.read")],
)
async def call_site_rest(
    site_id: uuid.UUID,
    payload: WordPressRestCall,
    ctx: RequestContext = Depends(require_context),
) -> WordPressRestResult:
    """Any call to the site's REST API, under the stored credential — the escape hatch for a
    plugin namespace no curated route above knows (read them off ``summary.namespaces``).

    A ``GET`` runs on ``wordpress.rest.read``. Any other verb needs ``wordpress.rest.write``
    and is refused outright on the site-takeover routes (users, plugins, themes, settings),
    which are changed in the site's own admin. Every write is a trail line; every answer is
    capped and says so when it was cut.
    """
    return await WordPressSurfaceService(ctx).rest_call(site_id, payload)
