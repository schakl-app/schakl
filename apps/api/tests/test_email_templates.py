"""Tenant-customisable auth email templates (#161 tier 2).

Covers the editor surface (customise / reset-to-default / defaults present), HTML sanitisation on
write, tenant isolation, and — end to end — that a saved template's subject and HTML actually
reach the sent message with its variables substituted and its script stripped, while the
plaintext part keeps the working reset link.
"""

from __future__ import annotations

from app.core.auth.emails import send_password_email
from app.db import async_session_maker
from tests.conftest import auth_cookie, make_tenant

_BREVO = {
    "provider": "brevo",
    "from_email": "noreply@agency-example.nl",
    "from_name": "Agency",
    "api_key": "xkeysib-secret-123",
}


class _Req:
    """The minimal shape send_password_email reads off the request."""

    def __init__(self, host: str) -> None:
        self.headers = {"host": host}


async def test_list_returns_every_slot_with_defaults(client_for) -> None:
    t = await make_tenant("emailtpl-list")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        body = (await c.get("/api/v1/settings/email/templates", headers=headers)).json()
        assert set(body["locales"]) == {"en", "nl"}
        assert body["variables"] == ["brand", "name", "link"]
        # 2 kinds x 2 locales, each with a non-empty built-in default and no override yet.
        assert len(body["templates"]) == 4
        for item in body["templates"]:
            assert item["kind"] in {"reset", "invite"}
            assert item["subject"] is None and item["body_html"] is None
            assert item["default_subject"] and item["default_body_html"]


async def test_save_customise_then_reset_to_default(client_for) -> None:
    t = await make_tenant("emailtpl-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        saved = await c.put(
            "/api/v1/settings/email/templates",
            json={
                "kind": "invite",
                "locale": "nl",
                "subject": "Welkom bij {brand}",
                "body_html": "<p>Hoi {name}</p>",
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["subject"] == "Welkom bij {brand}"

        listed = (await c.get("/api/v1/settings/email/templates", headers=headers)).json()
        nl_invite = next(
            i for i in listed["templates"] if i["kind"] == "invite" and i["locale"] == "nl"
        )
        assert nl_invite["subject"] == "Welkom bij {brand}"
        assert nl_invite["body_html"] == "<p>Hoi {name}</p>"

        # Blank both fields resets to the built-in default (the override row is deleted).
        reset = await c.put(
            "/api/v1/settings/email/templates",
            json={"kind": "invite", "locale": "nl", "subject": "", "body_html": ""},
            headers=headers,
        )
        assert reset.status_code == 200
        listed = (await c.get("/api/v1/settings/email/templates", headers=headers)).json()
        nl_invite = next(
            i for i in listed["templates"] if i["kind"] == "invite" and i["locale"] == "nl"
        )
        assert nl_invite["subject"] is None and nl_invite["body_html"] is None


async def test_html_is_sanitised_on_write(client_for) -> None:
    t = await make_tenant("emailtpl-xss")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        saved = await c.put(
            "/api/v1/settings/email/templates",
            json={
                "kind": "reset",
                "locale": "en",
                "subject": "Reset",
                "body_html": (
                    '<p>Hi</p><script>steal()</script>'
                    '<a href="{link}" onclick="x()">go</a>'
                ),
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        stored = saved.json()["body_html"]
        assert "<script" not in stored and "steal()" not in stored
        assert "onclick" not in stored
        assert '<a href="{link}"' in stored  # the safe parts (and the variable) survive


async def test_invalid_kind_or_locale_rejected(client_for) -> None:
    t = await make_tenant("emailtpl-val")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        bad_locale = await c.put(
            "/api/v1/settings/email/templates",
            json={"kind": "reset", "locale": "de", "subject": "x"},
            headers=headers,
        )
        assert bad_locale.status_code == 422, bad_locale.text


async def test_member_cannot_manage_templates(client_for) -> None:
    from tests.test_notification_channels import _member

    t = await make_tenant("emailtpl-rbac")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "m@emailtpl-rbac-example.nl")
        member_headers = await auth_cookie(member)
        assert (
            await c.get("/api/v1/settings/email/templates", headers=member_headers)
        ).status_code == 403
        assert (
            await c.put(
                "/api/v1/settings/email/templates",
                json={"kind": "reset", "locale": "nl", "subject": "x"},
                headers=member_headers,
            )
        ).status_code == 403


async def test_tenant_isolation(client_for) -> None:
    a = await make_tenant("emailtpl-iso-a")
    b = await make_tenant("emailtpl-iso-b")
    async with client_for(a.host) as ca:
        ha = await auth_cookie(a.user)
        await ca.put(
            "/api/v1/settings/email/templates",
            json={"kind": "reset", "locale": "nl", "subject": "Alleen A"},
            headers=ha,
        )
    async with client_for(b.host) as cb:
        hb = await auth_cookie(b.user)
        listed = (await cb.get("/api/v1/settings/email/templates", headers=hb)).json()
        nl_reset = next(
            i for i in listed["templates"] if i["kind"] == "reset" and i["locale"] == "nl"
        )
        assert nl_reset["subject"] is None  # org B never sees org A's override


async def test_saved_template_overrides_sent_mail(client_for, monkeypatch) -> None:
    t = await make_tenant("emailtpl-send")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A transport so the send does not short-circuit as not-configured.
        assert (
            await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        ).status_code == 200
        # Customise the reset mail (nl): a variable in the subject, and a script that must go.
        assert (
            await c.put(
                "/api/v1/settings/email/templates",
                json={
                    "kind": "reset",
                    "locale": "nl",
                    "subject": "Herstel je wachtwoord bij {brand}",
                    "body_html": '<p>Hoi {name}</p><a href="{link}">reset</a>'
                    "<script>evil()</script>",
                },
                headers=headers,
            )
        ).status_code == 200

    captured: dict = {}

    async def _capture(provider, config, sender, message):  # noqa: ANN001, ARG001
        captured["message"] = message
        return True, None

    monkeypatch.setattr("app.core.email.service.send_email", _capture)

    async with async_session_maker() as session:
        sent, error = await send_password_email(
            session, t.user, "tok-abc123", _Req(t.host), kind="reset"
        )
    assert sent is True, error
    message = captured["message"]
    # Subject: the tenant override with {brand} substituted (brand = the org's name).
    assert message.subject == "Herstel je wachtwoord bij Emailtpl-Send"
    # HTML: the override, variables substituted, script stripped.
    assert message.html is not None
    assert "evil()" not in message.html and "<script" not in message.html
    assert "reset-password?token=tok-abc123" in message.html
    # Plaintext part always keeps the working link (the catalog body), even with custom HTML.
    assert "reset-password?token=tok-abc123" in message.text
