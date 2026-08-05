"""Tenant-customisable email templates (#161 tier 2).

Covers the editor surface (customise / reset-to-default / defaults present), HTML sanitisation on
write, tenant isolation, and — end to end — that a saved template's subject and HTML actually
reach the sent message with its variables substituted and its script stripped, while the
plaintext part keeps the working reset link.

Plus the registry the kinds now come from (:mod:`app.core.email.kinds`): a module contributes
its own mails, the editor offers only those of the modules this org runs, and the keys are
unique and namespaced — the two things that are invisible until a stored override starts
resolving to the wrong mail.
"""

from __future__ import annotations

from app.core.auth.emails import send_password_email
from app.core.email.kinds import all_email_kinds, email_kinds_for, validate_email_kinds
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

    def __init__(self, host: str, forwarded_host: str | None = None) -> None:
        self.headers = {"host": host}
        if forwarded_host is not None:
            self.headers["x-forwarded-host"] = forwarded_host


async def test_list_returns_every_slot_with_defaults(client_for) -> None:
    t = await make_tenant("emailtpl-list")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        body = (await c.get("/api/v1/settings/email/templates", headers=headers)).json()
        assert set(body["locales"]) == {"en", "nl"}
        keys = [k["key"] for k in body["kinds"]]
        # Core's auth pair, plus the three client-facing mails invoicing contributes.
        assert keys[:2] == ["invite", "reset"]
        assert set(keys) >= {"invoicing.invoice", "invoicing.quote", "invoicing.reminder"}
        # Variables are per kind: an invoice mail's markers are not a reset mail's.
        by_key = {k["key"]: k for k in body["kinds"]}
        assert by_key["reset"]["variables"] == ["brand", "name", "link"]
        assert "outstanding" in by_key["invoicing.reminder"]["variables"]
        assert "link" not in by_key["invoicing.invoice"]["variables"]
        assert by_key["invoicing.invoice"]["module"] == "invoicing"
        # Every kind x every locale, each with a built-in default and no override yet.
        assert len(body["templates"]) == len(keys) * 2
        for item in body["templates"]:
            assert item["kind"] in set(keys)
            assert item["subject"] is None and item["body_html"] is None
            assert item["default_subject"] and item["default_body_html"]


async def test_kinds_follow_the_org_modules(client_for) -> None:
    """A mail belongs to the module that sends it: switching invoicing off takes its three
    templates off the editor, and off the write path — a stale form must not be able to store
    an override for a mail this org no longer sends."""
    t = await make_tenant("emailtpl-modules")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        switched = await c.patch(
            "/api/v1/meta/tenant",
            json={"enabled_modules": ["companies", "contacts", "tasks"]},
            headers=headers,
        )
        assert switched.status_code == 200, switched.text
        body = (await c.get("/api/v1/settings/email/templates", headers=headers)).json()
        assert [k["key"] for k in body["kinds"]] == ["invite", "reset"]
        refused = await c.put(
            "/api/v1/settings/email/templates",
            json={"kind": "invoicing.invoice", "locale": "nl", "subject": "x"},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text


def test_kind_keys_are_unique_and_namespaced() -> None:
    """The mount-time guard: a key is stored data, so a collision or a bare module key is a
    build break rather than one module silently reading another's overrides."""
    validate_email_kinds()
    keys = [kind.key for kind in all_email_kinds()]
    assert len(keys) == len(set(keys))
    for kind in all_email_kinds():
        if kind.module is not None:
            assert kind.key.startswith(f"{kind.module}.")
    # Core's kinds are the ones that stay bare — they shipped rows under those names.
    assert [k.key for k in email_kinds_for([])] == ["invite", "reset"]


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


async def test_password_email_resolves_org_behind_ssr_proxy(client_for, monkeypatch) -> None:
    """The SSR web app calls the API on its internal service address: ``Host`` is the service
    name and the tenant hostname rides ``X-Forwarded-Host`` — the ``require_context`` rule.
    The reset/invite mail must resolve the org the same way; resolving on the raw ``Host``
    finds no org and silently drops every forgot-password and invite mail, while the
    test-mail path (which runs under ``require_context``) keeps working."""
    t = await make_tenant("emailtpl-fwdhost")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        ).status_code == 200

    captured: dict = {}

    async def _capture(provider, config, sender, message):  # noqa: ANN001, ARG001
        captured["message"] = message
        return True, None

    monkeypatch.setattr("app.core.email.service.send_email", _capture)

    async with async_session_maker() as session:
        sent, error = await send_password_email(
            session,
            t.user,
            "tok-fwd456",
            _Req("api:8000", forwarded_host=t.host),
            kind="invite",
        )
    assert sent is True, error
    assert "reset-password?token=tok-fwd456" in captured["message"].text
    # The link lands on the org's own address, not the internal service name.
    assert "api:8000" not in captured["message"].text
