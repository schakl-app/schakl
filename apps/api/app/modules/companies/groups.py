"""Company groups — the company data horizon's admin surface (issue #191).

Groups scope **data** (which companies a membership sees); roles scope **capability** (§15).
This file holds the whole feature: the per-request scope resolver (registered onto the core
seam in the package ``__init__``), the service, and the REST surface under
``/api/v1/companies/groups`` — included into the companies router *before* its
``/{company_id}`` routes, so the literal path always wins the match.

Everything is gated on one declared permission, ``companies.group.manage`` — group CRUD,
company assignment and per-member visibility assignment are one administrative capability.
Every mutation lands on the activity trail (§16).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.activity import ActivityService
from app.core.models import Membership
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.modules.companies.models import (
    Company,
    CompanyGroup,
    CompanyGroupMember,
    MembershipCompanyGroup,
)

_ENTITY_TYPE = "company_group"


# --------------------------------------------------------------------------- #
# The per-request resolver (core seam, app/core/scope.py)
# --------------------------------------------------------------------------- #
async def resolve_membership_company_scope(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> frozenset[uuid.UUID] | None:
    """One indexed query: no assignment rows → ``None`` (sees all); rows → the union of the
    assigned groups' companies — possibly the **empty** set (assigned to an empty group means
    seeing nothing, not everything)."""
    rows = (
        await session.execute(
            select(MembershipCompanyGroup.group_id, CompanyGroupMember.company_id)
            .outerjoin(
                CompanyGroupMember,
                CompanyGroupMember.group_id == MembershipCompanyGroup.group_id,
            )
            .where(
                MembershipCompanyGroup.org_id == org_id,
                MembershipCompanyGroup.membership_id == membership_id,
            )
        )
    ).all()
    if not rows:
        return None
    return frozenset(company_id for _, company_id in rows if company_id is not None)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    position: int | None = None


class GroupCompanies(BaseModel):
    company_ids: list[uuid.UUID]


class GroupMemberships(BaseModel):
    membership_ids: list[uuid.UUID]


class GroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    position: int
    company_ids: list[uuid.UUID] = Field(default_factory=list)
    membership_ids: list[uuid.UUID] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class CompanyGroupService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(CompanyGroup)
        self.activity = ActivityService(ctx)

    async def list(self) -> list[GroupRead]:
        groups = await self.repo.list(order_by=CompanyGroup.position.asc(), limit=500)
        # Two grouped queries for the assignments — never one per group (docs/PERFORMANCE.md).
        companies = (
            await self.ctx.session.execute(
                select(CompanyGroupMember.group_id, CompanyGroupMember.company_id).where(
                    CompanyGroupMember.org_id == self.ctx.org.id
                )
            )
        ).all()
        members = (
            await self.ctx.session.execute(
                select(
                    MembershipCompanyGroup.group_id, MembershipCompanyGroup.membership_id
                ).where(MembershipCompanyGroup.org_id == self.ctx.org.id)
            )
        ).all()
        by_group_companies: dict[uuid.UUID, list[uuid.UUID]] = {}
        for group_id, company_id in companies:
            by_group_companies.setdefault(group_id, []).append(company_id)
        by_group_members: dict[uuid.UUID, list[uuid.UUID]] = {}
        for group_id, membership_id in members:
            by_group_members.setdefault(group_id, []).append(membership_id)
        return [
            GroupRead(
                id=g.id,
                name=g.name,
                position=g.position,
                company_ids=by_group_companies.get(g.id, []),
                membership_ids=by_group_members.get(g.id, []),
            )
            for g in groups
        ]

    async def create(self, data: GroupCreate) -> CompanyGroup:
        group = await self.repo.create(name=data.name)
        await self.activity.record(_ENTITY_TYPE, group.id, "created", {"name": group.name})
        return group

    async def update(self, group_id: uuid.UUID, data: GroupUpdate) -> CompanyGroup:
        group = await self.repo.get_or_404(group_id)
        changes: dict[str, dict[str, object]] = {}
        values: dict[str, object] = {}
        if data.name is not None and data.name != group.name:
            changes["name"] = {"from": group.name, "to": data.name}
            values["name"] = data.name
        if data.position is not None and data.position != group.position:
            values["position"] = data.position
        if values:
            group = await self.repo.update(group, **values)
        if changes:
            await self.activity.record(_ENTITY_TYPE, group.id, "updated", {"changes": changes})
        return group

    async def delete(self, group_id: uuid.UUID) -> None:
        group = await self.repo.get_or_404(group_id)
        name = group.name
        # Assignments go with the group (FK CASCADE): visibility widens, never breaks (#191).
        await self.repo.delete(group)
        await self.activity.record(_ENTITY_TYPE, group_id, "deleted", {"name": name})

    async def set_companies(self, group_id: uuid.UUID, company_ids: list[uuid.UUID]) -> None:
        group = await self.repo.get_or_404(group_id)
        # Validate every id inside the tenant — a stray/cross-tenant id silently drops.
        valid = set(
            (
                await self.ctx.session.execute(
                    select(Company.id).where(
                        Company.org_id == self.ctx.org.id, Company.id.in_(company_ids or [])
                    )
                )
            ).scalars()
        )
        wanted = [cid for cid in dict.fromkeys(company_ids) if cid in valid]
        link_repo = self.ctx.repo(CompanyGroupMember)
        existing = await link_repo.list(group_id=group_id, limit=10_000)
        existing_ids = {row.company_id for row in existing}
        for row in existing:
            if row.company_id not in wanted:
                await link_repo.delete(row)
        for cid in wanted:
            if cid not in existing_ids:
                await link_repo.create(group_id=group_id, company_id=cid)
        await self.activity.record(
            _ENTITY_TYPE,
            group.id,
            "companies_changed",
            {"name": group.name, "count": len(wanted)},
        )

    async def set_memberships(
        self, group_id: uuid.UUID, membership_ids: list[uuid.UUID]
    ) -> None:
        group = await self.repo.get_or_404(group_id)
        valid = set(
            (
                await self.ctx.session.execute(
                    select(Membership.id).where(
                        Membership.org_id == self.ctx.org.id,
                        Membership.id.in_(membership_ids or []),
                    )
                )
            ).scalars()
        )
        wanted = [mid for mid in dict.fromkeys(membership_ids) if mid in valid]
        link_repo = self.ctx.repo(MembershipCompanyGroup)
        existing = await link_repo.list(group_id=group_id, limit=10_000)
        existing_ids = {row.membership_id for row in existing}
        for row in existing:
            if row.membership_id not in wanted:
                await link_repo.delete(row)
        for mid in wanted:
            if mid not in existing_ids:
                await link_repo.create(group_id=group_id, membership_id=mid)
        await self.activity.record(
            _ENTITY_TYPE,
            group.id,
            "memberships_changed",
            {"name": group.name, "count": len(wanted)},
        )


# --------------------------------------------------------------------------- #
# Router — included before /companies/{company_id}, so the literal path wins
# --------------------------------------------------------------------------- #
groups_router = APIRouter(prefix="/groups", tags=["company-groups"])

_MANAGE = "companies.group.manage"


@groups_router.get(
    "", response_model=list[GroupRead], dependencies=[require_permission(_MANAGE)]
)
async def list_groups(ctx: RequestContext = Depends(require_context)) -> list[GroupRead]:
    return await CompanyGroupService(ctx).list()


@groups_router.post(
    "", response_model=GroupRead, status_code=201, dependencies=[require_permission(_MANAGE)]
)
async def create_group(
    data: GroupCreate, ctx: RequestContext = Depends(require_context)
) -> GroupRead:
    try:
        group = await CompanyGroupService(ctx).create(data)
    except Exception as exc:  # translate the unique-name violation to a field error
        from sqlalchemy.exc import IntegrityError

        if isinstance(exc, IntegrityError):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"name": "errors.duplicate"},
            ) from exc
        raise
    return GroupRead(id=group.id, name=group.name, position=group.position)


@groups_router.patch(
    "/{group_id}", response_model=GroupRead, dependencies=[require_permission(_MANAGE)]
)
async def update_group(
    group_id: uuid.UUID, data: GroupUpdate, ctx: RequestContext = Depends(require_context)
) -> GroupRead:
    group = await CompanyGroupService(ctx).update(group_id, data)
    return GroupRead(id=group.id, name=group.name, position=group.position)


@groups_router.delete(
    "/{group_id}", status_code=204, dependencies=[require_permission(_MANAGE)]
)
async def delete_group(
    group_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await CompanyGroupService(ctx).delete(group_id)


@groups_router.put(
    "/{group_id}/companies", status_code=204, dependencies=[require_permission(_MANAGE)]
)
async def set_group_companies(
    group_id: uuid.UUID, data: GroupCompanies, ctx: RequestContext = Depends(require_context)
) -> None:
    await CompanyGroupService(ctx).set_companies(group_id, data.company_ids)


@groups_router.put(
    "/{group_id}/memberships", status_code=204, dependencies=[require_permission(_MANAGE)]
)
async def set_group_memberships(
    group_id: uuid.UUID, data: GroupMemberships, ctx: RequestContext = Depends(require_context)
) -> None:
    await CompanyGroupService(ctx).set_memberships(group_id, data.membership_ids)
