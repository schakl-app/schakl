"""Trash routes — five per opted-in entity, never one generic set (CLAUDE.md §15).

Built at mount time from the :class:`~app.core.trash.spec.TrashableSpec`s on the enabled
modules, the bulk router's shape and for the bulk router's reason: a concrete route per entity
declares **that entity's own delete permission** in its ``dependencies``, so deny-by-default
stays enumerable — the introspection lint sees a real ``require_permission`` and the behavioural
sweep needs no exemption entry. A generic ``/trash/{entity_type}/…`` would have to be exempted
and re-checked inside the service, the un-enumerable shape the two-layer rule exists to prevent.

It also makes every verb a *named* MCP tool for free (``trash_restore_company``), which a chat
client can actually offer, where ``restore(entity_type=…)`` is a tool that has to be explained.

The reads carry no licence gate and the two writes carry the module's own (issue #137): an
expired licence must not stop anyone seeing what is in the trash, and restoring a row into a
module that may not be written to is a write.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from app.config import settings
from app.core.entitlements.service import license_write_gate
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.core.trash.schemas import TrashItem, TrashPage, TrashPreview
from app.core.trash.service import TrashService
from app.core.trash.spec import TrashableSpec


def _list_endpoint(spec: TrashableSpec) -> Any:
    async def trash_list(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        ctx: RequestContext = Depends(require_context),
    ) -> TrashPage:
        return await TrashService(ctx).list(spec.entity_type, limit=limit, offset=offset)

    trash_list.__name__ = f"trash_list_{spec.entity_type}"
    trash_list.__doc__ = (
        f"The {spec.entity_type} records in the trash: who deleted each, when, when it will be "
        "purged, and what hangs off it. A record with anything in `blocking` is kept past the "
        "retention window until somebody restores it."
    )
    return trash_list


def _get_endpoint(spec: TrashableSpec) -> Any:
    async def trash_get(
        entity_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
    ) -> TrashItem:
        return await TrashService(ctx).get(spec.entity_type, entity_id)

    trash_get.__name__ = f"trash_get_{spec.entity_type}"
    trash_get.__doc__ = (
        f"One trashed {spec.entity_type} record. 404 for a live record: to this surface a row "
        "that is not in the trash does not exist."
    )
    return trash_get


def _preview_endpoint(spec: TrashableSpec) -> Any:
    async def trash_preview(
        entity_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
    ) -> TrashPreview:
        return await TrashService(ctx).preview(spec.entity_type, entity_id)

    trash_preview.__name__ = f"trash_preview_{spec.entity_type}"
    trash_preview.__doc__ = (
        f"What deleting a live {spec.entity_type} record would do: `blocking` lists the records "
        "that stop it (issued invoices, domains, agreements, projects, hours — these outlive a "
        "client, so a client holding any is archived rather than deleted), `taken_along` what "
        "hides with it and goes when it is purged. Read this before calling DELETE."
    )
    return trash_preview


def _restore_endpoint(spec: TrashableSpec) -> Any:
    async def trash_restore(
        entity_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
    ) -> Response:
        await TrashService(ctx).restore(spec.entity_type, entity_id)
        return Response(status_code=204)

    trash_restore.__name__ = f"trash_restore_{spec.entity_type}"
    trash_restore.__doc__ = (
        f"Bring a trashed {spec.entity_type} record back exactly as it was, with everything that "
        "hid with it."
    )
    return trash_restore


def _purge_endpoint(spec: TrashableSpec) -> Any:
    async def trash_purge(
        entity_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
    ) -> Response:
        await TrashService(ctx).purge(spec.entity_type, entity_id)
        return Response(status_code=204)

    trash_purge.__name__ = f"trash_purge_{spec.entity_type}"
    trash_purge.__doc__ = (
        f"Delete a trashed {spec.entity_type} record for good. Irreversible; refused (409) while "
        "anything in `blocking` still hangs off it. The nightly sweep does this by itself after "
        "the retention window."
    )
    return trash_purge


def build_trash_router() -> APIRouter:
    """Mount ``/trash/<entity>`` (+ ``/{id}``, ``/{id}/preview``, ``/{id}/restore``) per spec.

    Imported lazily by ``create_app`` after module loading, like the bulk and impex routers.
    """
    from app.registry import registry

    router = APIRouter(prefix="/trash", tags=["trash"])
    for module in registry.enabled(settings.enabled_modules):
        gate = [license_write_gate(module.sku)] if module.sku else []
        for spec in module.trash:
            permission = require_permission(spec.delete_permission)
            base = f"/{spec.entity_type}"
            router.add_api_route(
                base,
                _list_endpoint(spec),
                methods=["GET"],
                name=f"trash_list_{spec.entity_type}",
                dependencies=[permission],
                response_model=TrashPage,
            )
            router.add_api_route(
                base + "/{entity_id}",
                _get_endpoint(spec),
                methods=["GET"],
                name=f"trash_get_{spec.entity_type}",
                dependencies=[permission],
                response_model=TrashItem,
            )
            router.add_api_route(
                base + "/{entity_id}/preview",
                _preview_endpoint(spec),
                methods=["GET"],
                name=f"trash_preview_{spec.entity_type}",
                dependencies=[permission],
                response_model=TrashPreview,
            )
            router.add_api_route(
                base + "/{entity_id}/restore",
                _restore_endpoint(spec),
                methods=["POST"],
                name=f"trash_restore_{spec.entity_type}",
                dependencies=[*gate, permission],
                status_code=204,
            )
            router.add_api_route(
                base + "/{entity_id}",
                _purge_endpoint(spec),
                methods=["DELETE"],
                name=f"trash_purge_{spec.entity_type}",
                dependencies=[*gate, permission],
                status_code=204,
            )
    return router
