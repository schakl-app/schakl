"""Validation and label resolution for parties (issue #88).

Two jobs, both tenant-scoped (Golden Rule 1):

* :meth:`PartyService.validate` — on write, coerce a :class:`PartyRef` into the ``(type, id)``
  pair to store, and **reject an id that is not this tenant's** (a contact, company or employee
  from another org), so a party can never cross the isolation boundary.
* :meth:`PartyService.resolve_many` — on read, turn stored pairs into display labels in a handful
  of batched queries, never one per row (docs/PERFORMANCE.md).

Company and contact existence is checked via RLS-scoped raw SQL against those tables by name
(the same convention ``contacts`` uses to reach ``companies``) rather than importing another
module's models (CLAUDE.md §6). Employees are ``memberships`` of this org.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import bindparam, select, text

from app.core.auth.models import User
from app.core.models import Membership, OrgSettings
from app.core.party.models import PartyType
from app.core.party.schemas import PartyReadRef, PartyRef
from app.core.tenancy import RequestContext
from app.errors import AppError

#: The stored form of one party column pair, plus the owning record's ``company_id`` — which is
#: what a ``company`` party with a NULL id resolves to ("the record's own company").
PartyInput = tuple[str | None, uuid.UUID | None, uuid.UUID | None]


class PartyService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    def _invalid(self) -> AppError:
        return AppError("invalid_party", "errors.invalid_party", status_code=400)

    # --- write --------------------------------------------------------------- #
    async def validate(self, ref: PartyRef | None) -> tuple[str | None, uuid.UUID | None]:
        """Coerce ``ref`` into the ``(party_type, party_id)`` pair to persist.

        ``None`` clears the party. An id belonging to another tenant — or a missing id where one
        is required — raises ``400 errors.invalid_party``.
        """
        if ref is None:
            return None, None
        ptype = PartyType(ref.type)

        if ptype is PartyType.AGENCY:
            # The agency is the org itself; an id here means the client meant something else
            # (a contact? another org?) — reject rather than guess.
            if ref.id is not None:
                raise self._invalid()
            return ptype.value, None
        if ptype is PartyType.COMPANY:
            if ref.id is not None:
                await self._ensure_in_tenant("companies", ref.id)
            return ptype.value, ref.id  # NULL ⇒ the record's own company
        if ptype is PartyType.EMPLOYEE:
            if ref.id is None:
                raise self._invalid()
            await self._ensure_membership(ref.id)
            return ptype.value, ref.id
        if ptype is PartyType.CONTACT:
            if ref.id is None:
                raise self._invalid()
            await self._ensure_in_tenant("contacts", ref.id)
            return ptype.value, ref.id
        raise self._invalid()

    async def _ensure_in_tenant(self, table: str, entity_id: uuid.UUID) -> None:
        # ``table`` is never client-supplied — only "companies"/"contacts" from the branches above.
        ok = await self.ctx.session.scalar(
            text(f"SELECT 1 FROM {table} WHERE id = :id AND org_id = :oid"),
            {"id": entity_id, "oid": self._org_id},
        )
        if not ok:
            raise self._invalid()

    async def _ensure_membership(self, user_id: uuid.UUID) -> None:
        ok = await self.ctx.session.scalar(
            select(Membership.id).where(
                Membership.org_id == self._org_id, Membership.user_id == user_id
            )
        )
        if not ok:
            raise self._invalid()

    # --- read ---------------------------------------------------------------- #
    async def resolve(
        self, party_type: str | None, party_id: uuid.UUID | None, company_id: uuid.UUID | None
    ) -> PartyReadRef | None:
        """Resolve a single stored pair to a labelled ref (convenience over ``resolve_many``)."""
        return (await self.resolve_many([(party_type, party_id, company_id)]))[0]

    async def resolve_many(self, items: Sequence[PartyInput]) -> list[PartyReadRef | None]:
        """Resolve stored pairs to labelled refs in batched queries (no N+1).

        ``None`` type ⇒ ``None`` result (the party was never set). A referenced row that has since
        been deleted resolves to an empty label but keeps its type, so the UI still knows *what*
        kind of thing is gone.
        """
        company_ids: set[uuid.UUID] = set()
        user_ids: set[uuid.UUID] = set()
        contact_ids: set[uuid.UUID] = set()
        want_agency = False

        for ptype, pid, company_id in items:
            if ptype is None:
                continue
            if ptype == PartyType.AGENCY.value:
                want_agency = True
            elif ptype == PartyType.COMPANY.value:
                target = pid or company_id
                if target is not None:
                    company_ids.add(target)
            elif ptype == PartyType.EMPLOYEE.value and pid is not None:
                user_ids.add(pid)
            elif ptype == PartyType.CONTACT.value and pid is not None:
                contact_ids.add(pid)

        company_names = await self._company_names(company_ids)
        user_names = await self._user_names(user_ids)
        contact_names = await self._contact_names(contact_ids)
        agency = await self._agency_label() if want_agency else ""

        results: list[PartyReadRef | None] = []
        for ptype, pid, company_id in items:
            if ptype is None:
                results.append(None)
                continue
            if ptype == PartyType.AGENCY.value:
                results.append(PartyReadRef(type=PartyType.AGENCY, label=agency))
            elif ptype == PartyType.COMPANY.value:
                target = pid or company_id
                results.append(
                    PartyReadRef(
                        type=PartyType.COMPANY,
                        id=pid,
                        label=company_names.get(target, "") if target else "",
                    )
                )
            elif ptype == PartyType.EMPLOYEE.value:
                results.append(
                    PartyReadRef(
                        type=PartyType.EMPLOYEE, id=pid, label=user_names.get(pid, "")
                    )
                )
            elif ptype == PartyType.CONTACT.value:
                results.append(
                    PartyReadRef(
                        type=PartyType.CONTACT, id=pid, label=contact_names.get(pid, "")
                    )
                )
            else:
                results.append(None)
        return results

    async def _agency_label(self) -> str:
        return (
            await self.ctx.session.scalar(
                select(OrgSettings.brand_name).where(OrgSettings.org_id == self._org_id)
            )
        ) or ""

    async def _company_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        stmt = text("SELECT id, name FROM companies WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        rows = (await self.ctx.session.execute(stmt, {"ids": list(ids)})).all()
        return {row[0]: row[1] for row in rows}

    async def _contact_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        stmt = text(
            "SELECT id, first_name, last_name FROM contacts WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        rows = (await self.ctx.session.execute(stmt, {"ids": list(ids)})).all()
        return {row[0]: " ".join(p for p in (row[1], row[2]) if p).strip() for row in rows}

    async def _user_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = (
            await self.ctx.session.execute(
                select(User.id, User.full_name, User.email)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.org_id == self._org_id, User.id.in_(ids))
            )
        ).all()
        return {row[0]: (row[1] or row[2]) for row in rows}
