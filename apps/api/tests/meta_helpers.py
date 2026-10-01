"""Shared set-up for the Meta suites: a tenant whose Meta is configured, linked and ready."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import Tenant, add_membership, auth_cookie, make_tenant
from tests.meta_fake import (
    AD_ACCOUNT_CLIENT,
    APP_ID,
    APP_SECRET,
    BUSINESS_ID,
    IG_CLIENT,
    PAGE_CLIENT,
    PAGE_OWN,
    SYSTEM_TOKEN,
)

BASE = "/api/v1/meta-business"


@dataclass
class Ready:
    tenant: Tenant
    headers: dict[str, str]
    credential_id: str
    company_id: str
    page_id: str
    instagram_id: str
    ad_account_id: str
    own_page_id: str


async def company(org_id: uuid.UUID, name: str = "Nova Fietsen") -> str:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = await session.execute(
            text(
                "INSERT INTO companies (id, org_id, name, status, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org, :name, 'active', now(), now()) RETURNING id"
            ),
            {"org": str(org_id), "name": name},
        )
        company_id = row.scalar_one()
        await session.commit()
    return str(company_id)


async def member(tenant: Tenant, email: str, role: str = "member") -> dict[str, str]:
    """A second account in the tenant, holding ``role``'s default permissions."""
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(), email=email, hashed_password="x", is_active=True, is_verified=True
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, tenant.org.id)
        await add_membership(session, tenant.org.id, user.id, role)
        await session.commit()
        detached = User(id=user.id, email=email, hashed_password="", is_active=True)
    return await auth_cookie(detached, tenant.org.id)


async def configure(c: Any, headers: dict[str, str]) -> None:
    res = await c.put(
        f"{BASE}/settings", json={"app_id": APP_ID, "app_secret": APP_SECRET}, headers=headers
    )
    assert res.status_code == 200, res.text


async def add_credential(c: Any, headers: dict[str, str], **body: Any) -> dict:
    payload = {"label": "breik. portfolio", "token": SYSTEM_TOKEN, "business_id": BUSINESS_ID}
    payload.update(body)
    res = await c.post(f"{BASE}/credentials", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def ready(client_for: Any, slug: str) -> Ready:
    """A tenant with the app configured, a credential verified, assets discovered and the
    client's Page, Instagram account and ad account linked to one client."""
    t = await make_tenant(slug)
    headers = await auth_cookie(t.user)
    company_id = await company(t.org.id)
    # Switched on for the tenant, as Instellingen → Integraties would: what a hub composes
    # and what a nav draws are both read off this list.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            text(
                "UPDATE org_settings SET enabled_modules = enabled_modules || "
                "ARRAY['meta', 'meta_ads']::varchar[] WHERE org_id = :org"
            ),
            {"org": str(t.org.id)},
        )
        await session.commit()
    async with client_for(t.host) as c:
        await configure(c, headers)
        credential = await add_credential(c, headers)
        res = await c.post(f"{BASE}/credentials/{credential['id']}/discover", headers=headers)
        assert res.status_code == 200, res.text
        assets = (
            await c.get(f"{BASE}/assets", params={"active_only": False}, headers=headers)
        ).json()["items"]
        by_meta = {asset["meta_id"]: asset for asset in assets}
        for meta_id in (PAGE_CLIENT, IG_CLIENT, AD_ACCOUNT_CLIENT):
            res = await c.patch(
                f"{BASE}/assets/{by_meta[meta_id]['id']}",
                json={"company_id": company_id},
                headers=headers,
            )
            assert res.status_code == 200, res.text
        res = await c.patch(
            f"{BASE}/assets/{by_meta[PAGE_OWN]['id']}", json={"active": True}, headers=headers
        )
        assert res.status_code == 200, res.text
    return Ready(
        tenant=t,
        headers=headers,
        credential_id=credential["id"],
        company_id=company_id,
        page_id=by_meta[PAGE_CLIENT]["id"],
        instagram_id=by_meta[IG_CLIENT]["id"],
        ad_account_id=by_meta[AD_ACCOUNT_CLIENT]["id"],
        own_page_id=by_meta[PAGE_OWN]["id"],
    )


def image(width: int = 1080, height: int = 1080, fmt: str = "PNG") -> bytes:
    """A real image of a given shape — the checks measure it, so it has to be one."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 60, 40)).save(out, fmt)
    return out.getvalue()


async def upload(
    c: Any,
    headers: dict[str, str],
    post_id: str,
    *,
    data: bytes | None = None,
    filename: str = "foto.png",
    content_type: str = "image/png",
) -> str:
    res = await c.post(
        "/api/v1/files",
        params={"entity_type": "meta_post", "entity_id": post_id},
        files={"file": (filename, data or image(), content_type)},
        headers=headers,
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


async def system_publisher(tenant: Tenant):
    """A publisher acting as the worker does, on a session of its own.

    Returns ``(publisher, session)``; the caller commits and closes.
    """
    from app.core.jobs import system_context
    from app.core.models import Org
    from app.integrations.meta.publisher import Publisher

    session = async_session_maker()
    org = await session.scalar(select(Org).where(Org.id == tenant.org.id))
    await set_current_org(session, tenant.org.id)
    return Publisher(system_context(org, session)), session


async def run_worker(tenant: Tenant, **kwargs: Any) -> int:
    """One tick of the publishing sweep, as the worker runs it."""
    publisher, session = await system_publisher(tenant)
    try:
        touched = await publisher.run_due(**kwargs)
        await session.commit()
        return touched
    finally:
        await session.close()
