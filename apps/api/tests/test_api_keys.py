"""API keys and service accounts (#20): scoped, expiring, capped by the owner, tenant-isolated."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant


def _expiry(days: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def test_personal_key_authenticates_and_is_scope_capped(client_for) -> None:
    t = await make_tenant("apikey-basic")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/api-keys",
            json={"name": "ci", "scopes": ["leave.request.read:any"], "expires_at": _expiry()},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        secret = body["secret"]
        assert secret.startswith("schakl_")
        assert body["redacted"].endswith("****")
        listed = (await c.get("/api/v1/api-keys", headers=headers)).json()
        assert "secret" not in listed[0]  # the secret is never returned again

        # The key authenticates a request within its scope…
        key_headers = {"X-API-Key": secret}
        ok = await c.get("/api/v1/leave/types", headers=key_headers)
        assert ok.status_code == 200, ok.text
        # …but not one outside it (members.member.read isn't in the key's scopes).
        denied = await c.get("/api/v1/members", headers=key_headers)
        assert denied.status_code == 403

        # Bearer form works too.
        ok2 = await c.get("/api/v1/leave/types", headers={"Authorization": f"Bearer {secret}"})
        assert ok2.status_code == 200


async def test_expiry_is_optional_and_bounded(client_for) -> None:
    t = await make_tenant("apikey-expiry")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        past = await c.post(
            "/api/v1/api-keys",
            json={"name": "x", "scopes": ["leave.request.read:any"], "expires_at": _expiry(-1)},
            headers=headers,
        )
        assert past.status_code == 422
        too_far = await c.post(
            "/api/v1/api-keys",
            json={"name": "x", "scopes": ["leave.request.read:any"], "expires_at": _expiry(400)},
            headers=headers,
        )
        assert too_far.status_code == 422

        # No expiry at all = a key that never expires (an explicit owner choice).
        unlimited = await c.post(
            "/api/v1/api-keys",
            json={"name": "forever", "scopes": ["leave.request.read:any"]},
            headers=headers,
        )
        assert unlimited.status_code == 201, unlimited.text
        assert unlimited.json()["expires_at"] is None
        ok = await c.get(
            "/api/v1/leave/types", headers={"X-API-Key": unlimited.json()["secret"]}
        )
        assert ok.status_code == 200


async def test_revoked_key_is_401_not_403(client_for) -> None:
    t = await make_tenant("apikey-revoke")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/api-keys",
            json={"name": "r", "scopes": ["leave.request.read:any"], "expires_at": _expiry()},
            headers=headers,
        )
        secret = created.json()["secret"]
        key_id = created.json()["id"]
        live = await c.get("/api/v1/leave/types", headers={"X-API-Key": secret})
        assert live.status_code == 200

        revoked = await c.post(f"/api/v1/api-keys/{key_id}/revoke", headers=headers)
        assert revoked.status_code == 200
        # A revoked key is unauthorized, never forbidden — the response never confirms it exists.
        res = await c.get("/api/v1/leave/types", headers={"X-API-Key": secret})
        assert res.status_code == 401


async def test_member_key_cannot_exceed_own_grants(client_for) -> None:
    t = await make_tenant("apikey-cap")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)

        # A member holds leave.request.read only at :own — they cannot mint an :any key.
        over = await c.post(
            "/api/v1/api-keys",
            json={"name": "greedy", "scopes": ["leave.request.read:any"], "expires_at": _expiry()},
            headers=mh,
        )
        assert over.status_code == 403

        # …but they can mint the :own key they do hold.
        ok = await c.post(
            "/api/v1/api-keys",
            json={"name": "fine", "scopes": ["leave.request.read:own"], "expires_at": _expiry()},
            headers=mh,
        )
        assert ok.status_code == 201, ok.text


async def test_key_from_another_org_is_rejected(client_for) -> None:
    a = await make_tenant("apikey-org-a")
    b = await make_tenant("apikey-org-b")
    ah = await auth_cookie(a.user)
    async with client_for(a.host) as ca:
        secret = (
            await ca.post(
                "/api/v1/api-keys",
                json={"name": "k", "scopes": ["leave.request.read:any"], "expires_at": _expiry()},
                headers=ah,
            )
        ).json()["secret"]
    # The same key presented on org B's host resolves to no key there → 401.
    async with client_for(b.host) as cb:
        res = await cb.get("/api/v1/leave/types", headers={"X-API-Key": secret})
        assert res.status_code == 401


async def test_service_account_key_authenticates(client_for) -> None:
    t = await make_tenant("apikey-svc")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await c.post(
            "/api/v1/service-accounts", json={"name": "n8n"}, headers=owner
        )
        assert account.status_code == 201, account.text
        aid = account.json()["id"]
        key = await c.post(
            f"/api/v1/service-accounts/{aid}/keys",
            json={"name": "bot", "scopes": ["leave.request.read:any"], "expires_at": _expiry()},
            headers=owner,
        )
        assert key.status_code == 201, key.text
        secret = key.json()["secret"]
        assert key.json()["principal_type"] == "service_account"

        res = await c.get("/api/v1/leave/types", headers={"X-API-Key": secret})
        assert res.status_code == 200

        # A plain member may not manage service accounts.
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)
        create = await c.post("/api/v1/service-accounts", json={"name": "x"}, headers=mh)
        assert create.status_code == 403
        assert (await c.get("/api/v1/service-accounts", headers=mh)).status_code == 403


async def test_api_keys_are_tenant_isolated(client_for) -> None:
    a = await make_tenant("apikey-iso-a")
    b = await make_tenant("apikey-iso-b")
    ah = await auth_cookie(a.user)
    bh = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        await ca.post(
            "/api/v1/api-keys",
            json={"name": "k", "scopes": ["leave.request.read:any"], "expires_at": _expiry()},
            headers=ah,
        )
        assert len((await ca.get("/api/v1/api-keys", headers=ah)).json()) == 1
    async with client_for(b.host) as cb:
        # B's owner sees none of A's keys.
        assert (await cb.get("/api/v1/api-keys", headers=bh)).json() == []
