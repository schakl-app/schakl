"""Bulk routes — one update + one delete route **per opted-in entity** (CLAUDE.md §15).

Built at mount time from the registered :class:`~app.core.bulk.spec.BulkDescriptor`s, *after*
``main.py`` has imported the enabled modules. Registering a concrete route pair per entity —
rather than one generic ``/{entity_type}/…`` route — is the same deliberate decision impex
made: each route then declares **that entity's own** write/delete permission in its
``dependencies``, so deny-by-default stays enumerable (the introspection lint sees a real
``require_permission``) and the behavioural sweep needs no exemption entry. A generic route
would have to be exempted and re-checked inside the service, which is exactly the
un-enumerable shape the two-layer rule exists to prevent.

**No new capability gates this.** The two precedents in the tree disagree on purpose and both
write down why, so the choice is not a coin flip: impex earns ``impex.export`` because taking
the client list out of the building in one file is a *different act* from opening one record,
while ``interactions`` bulk review carries the plain review permission because approving forty
emails you may each approve is *the same act, repeated*. A bulk edit is the second kind. It
writes rows the caller can already write, one at a time, through the very same service — and
inventing ``bulk.edit`` would only add a switch that can be off while the thing it guards is
still reachable fifty clicks at a time.

**A licensed module's bulk write is gated like its own routes.** ``domains``, ``projects``,
``subscriptions`` and ``websites`` carry an sku, and their routers are mounted behind
``license_write_gate`` (issue #137) — so a bulk route mounted without one would be the single
place an uncovered instance could still write those modules. Reads never block; there are no
reads here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.config import settings
from app.core.bulk.schemas import BulkActionResult, BulkDeleteRequest, BulkUpdateRequest
from app.core.bulk.service import BulkService
from app.core.bulk.spec import BulkDescriptor, check_descriptor
from app.core.entitlements.service import license_write_gate
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context


def _update_endpoint(descriptor: BulkDescriptor) -> Any:
    async def bulk_update(
        payload: BulkUpdateRequest,
        ctx: RequestContext = Depends(require_context),
    ) -> BulkActionResult:
        return BulkActionResult.model_validate(await BulkService(ctx).update(descriptor, payload))

    editable = ", ".join(f"`{key}`" for key in descriptor.columns)
    bulk_update.__name__ = f"bulk_update_{descriptor.entity_type}"
    bulk_update.__doc__ = (
        f"Set fields on a selection of {descriptor.entity_type} records: {editable}. "
        "Keys are the entity's own stable column keys (the ones its CSV export uses). An "
        "absent key leaves every row's own value alone; an explicit `null` clears it where "
        "the field allows that. Rows are independent — an ineligible one is reported in "
        "`failed`, never rolled back over the rest."
    )
    return bulk_update


def _delete_endpoint(descriptor: BulkDescriptor) -> Any:
    async def bulk_delete(
        payload: BulkDeleteRequest,
        ctx: RequestContext = Depends(require_context),
    ) -> BulkActionResult:
        return BulkActionResult.model_validate(await BulkService(ctx).delete(descriptor, payload))

    bulk_delete.__name__ = f"bulk_delete_{descriptor.entity_type}"
    bulk_delete.__doc__ = (
        f"Delete a selection of {descriptor.entity_type} records. Permanent, and per row: "
        "the rows the batch could do are done, and the rest come back in `failed`."
    )
    return bulk_delete


def build_bulk_router() -> APIRouter:
    """Mount ``/bulk/<entity>/update`` (+ ``/delete``) for every registered descriptor.

    Imported lazily by ``create_app`` after module loading — descriptors live on the
    ``ModuleDescriptor``s of the enabled modules, so disabling a module removes its routes.
    """
    from app.registry import registry

    router = APIRouter(prefix="/bulk", tags=["bulk"])
    for module in registry.enabled(settings.enabled_modules):
        gate = [license_write_gate(module.sku)] if module.sku else []
        for descriptor in module.bulk:
            check_descriptor(descriptor)
            if descriptor.editable and descriptor.write_permission:
                router.add_api_route(
                    f"/{descriptor.entity_type}/update",
                    _update_endpoint(descriptor),
                    methods=["POST"],
                    name=f"bulk_update_{descriptor.entity_type}",
                    dependencies=[*gate, require_permission(descriptor.write_permission)],
                    response_model=BulkActionResult,
                )
            if descriptor.delete_permission is not None:
                router.add_api_route(
                    f"/{descriptor.entity_type}/delete",
                    _delete_endpoint(descriptor),
                    methods=["POST"],
                    name=f"bulk_delete_{descriptor.entity_type}",
                    # POST, not DELETE: a body of ids is what this takes, and a DELETE with a
                    # body is a thing plenty of clients and proxies quietly drop.
                    dependencies=[*gate, require_permission(descriptor.delete_permission)],
                    response_model=BulkActionResult,
                )
    return router
