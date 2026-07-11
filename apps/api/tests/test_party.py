"""Core party value type: validation, tenant isolation and label resolution (issue #88)."""

from __future__ import annotations

import uuid

import pytest

from app.core.party import PartyService, PartyType
from app.core.party.schemas import PartyRef
from app.core.roles import Role
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.modules.companies.models import Company
from app.modules.contacts.models import Contact
from tests.conftest import make_tenant


async def _ctx(session, tenant) -> RequestContext:
    return RequestContext(
        user=tenant.user, org=tenant.org, role=Role.OWNER, session=session
    )


async def test_party_validate_and_resolve(client_for) -> None:
    a = await make_tenant("party-a")
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        company = Company(org_id=a.org.id, name="Alpha Co")
        contact = Contact(org_id=a.org.id, first_name="Ada", last_name="Lovelace")
        session.add_all([company, contact])
        await session.commit()

    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        party = PartyService(await _ctx(session, a))

        # agency needs no id and resolves to the tenant brand.
        assert await party.validate(PartyRef(type=PartyType.AGENCY)) == ("agency", None)
        ref = await party.resolve("agency", None, None)
        assert ref.type == PartyType.AGENCY
        assert ref.label == "Party-A"

        # company / contact ids of this tenant validate and resolve to their names.
        assert await party.validate(
            PartyRef(type=PartyType.CONTACT, id=contact.id)
        ) == ("contact", contact.id)
        resolved = await party.resolve("contact", contact.id, None)
        assert resolved.label == "Ada Lovelace"

        # employee requires an id.
        with pytest.raises(AppError):
            await party.validate(PartyRef(type=PartyType.EMPLOYEE))


async def test_party_rejects_cross_tenant_id(client_for) -> None:
    a = await make_tenant("party-iso-a")
    b = await make_tenant("party-iso-b")
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        b_contact = Contact(org_id=b.org.id, first_name="Bob")
        session.add(b_contact)
        await session.commit()
        b_contact_id = b_contact.id

    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        party = PartyService(await _ctx(session, a))
        # A cannot reference B's contact, nor a random id.
        with pytest.raises(AppError):
            await party.validate(PartyRef(type=PartyType.CONTACT, id=b_contact_id))
        with pytest.raises(AppError):
            await party.validate(PartyRef(type=PartyType.COMPANY, id=uuid.uuid4()))
