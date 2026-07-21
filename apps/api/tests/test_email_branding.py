"""Branded HTML e-mail: the chrome and the tier-1 default templates (#236).

Unit-level: color/logo validation, text promotion, idempotent wrapping. End-to-end: the
default reset mail leaves as branded multipart HTML with a CTA button in the org's primary
color, the plaintext link intact, and the signature riding *inside* the chrome — while a
tenant override (#161 tier 2) still takes precedence over the default body.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import update

from app.core.auth.emails import send_password_email
from app.core.email.branding import (
    DEFAULT_PRIMARY,
    EmailBrand,
    apply_branding,
    brand_from,
    button_html,
    paragraphs_html,
    render_branded_email,
)
from app.core.email.senders import OutgoingEmail
from app.core.email.templates import build_email_content
from app.core.models import OrgSettings
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant
from tests.test_email_templates import _BREVO, _Req

_BRAND = EmailBrand(
    brand_name="Agency & Co",
    logo_url="https://cdn.example/logo.png",
    show_brand_name=True,
    primary_color="#123abc",
    base_url="https://agency.example",
)


# --------------------------------------------------------------------------- #
# Unit: fragments and chrome
# --------------------------------------------------------------------------- #
def test_paragraphs_html_escapes_and_splits() -> None:
    html = paragraphs_html("Hi <b>there</b>\nline two\n\nsecond paragraph")
    assert html.count("<p") == 2
    assert "&lt;b&gt;there&lt;/b&gt;" in html and "<b>" not in html
    assert "line two" in html and "<br>" in html


def test_button_html_escapes_label_and_validates_color() -> None:
    html = button_html('Click "here" <now>', "https://x.example/a?b=1&c=2", "red; evil:1")
    assert "&lt;now&gt;" in html and "<now>" not in html
    assert 'href="https://x.example/a?b=1&amp;c=2"' in html
    # A non-hex color never reaches a style attribute; the model default steps in.
    assert "evil" not in html and DEFAULT_PRIMARY in html


def test_chrome_carries_logo_brand_and_footer() -> None:
    html = render_branded_email(_BRAND, "<p>body</p>", preheader="first line\nsecond")
    assert html.lstrip().startswith("<!doctype")
    assert '<img src="https://cdn.example/logo.png"' in html
    # The brand name is escaped wherever it appears (header, title, footer).
    assert "Agency &amp; Co" in html and "Agency & Co<" not in html
    assert "<p>body</p>" in html
    assert "first line" in html and "second" not in html  # preheader = first text line


def test_chrome_hides_name_next_to_logo_when_configured() -> None:
    brand = EmailBrand(
        brand_name="Quiet",
        logo_url="https://cdn.example/logo.png",
        show_brand_name=False,
        primary_color="#123abc",
        base_url="https://q.example",
    )
    html = render_branded_email(brand, "<p>x</p>")
    header = html.split("border-radius:8px")[0]
    assert '<img src="https://cdn.example/logo.png"' in header
    assert ">Quiet</p>" not in header  # no name beside the logo…
    assert ">Quiet<" in html  # …but the footer still says who sent it


def test_apply_branding_promotes_text_and_is_idempotent() -> None:
    message = OutgoingEmail(to="a@b.example", subject="s", text="plain\n\nwords")
    branded = apply_branding(_BRAND, message)
    assert branded.html is not None and branded.html.lstrip().startswith("<!doctype")
    assert "plain" in branded.html and "words" in branded.html
    assert branded.text == "plain\n\nwords"  # the plaintext part is untouched
    again = apply_branding(_BRAND, branded)
    assert again.html == branded.html  # a full document is never wrapped twice
    assert apply_branding(None, message) is message  # no brand → send as-is, never block


def test_brand_from_absolutises_and_refuses_unsafe_logo() -> None:
    org = SimpleNamespace(
        name="Org", slug="org", custom_domain=None, custom_domain_verified_at=None
    )
    rel = brand_from(org, SimpleNamespace(
        brand_name="B", logo_url="/uploads/logo.png", show_brand_name=True, primary_color="#fff"
    ))
    assert rel.logo_url == f"{rel.base_url}/uploads/logo.png"
    evil = brand_from(org, SimpleNamespace(
        brand_name="B", logo_url="javascript:alert(1)", show_brand_name=True, primary_color="zzz"
    ))
    assert evil.logo_url is None
    assert evil.primary_color == DEFAULT_PRIMARY
    bare = brand_from(org, None)
    assert bare.brand_name == "Org"


# --------------------------------------------------------------------------- #
# Unit: the tier-1 default body is real HTML with a button
# --------------------------------------------------------------------------- #
def test_default_content_html_has_button_not_bare_link() -> None:
    values = {
        "name": "Ada <script>x()</script>",
        "brand": "Agency",
        "link": "https://a.example/reset-password?token=t1",
    }
    subject, text, html = build_email_content(
        "reset", "nl", None, None, values, primary_color="#123abc"
    )
    assert html is not None
    assert 'href="https://a.example/reset-password?token=t1"' in html
    assert "#123abc" in html  # the CTA carries the org's primary color
    # The URL is the button's target, not a wall of visible href text.
    assert ">https://a.example" not in html
    # Smuggled markup is escaped to harmless text, never a live tag.
    assert "<script" not in html and "&lt;script&gt;" in html
    assert "https://a.example/reset-password?token=t1" in text  # plaintext keeps the link


def test_without_brand_color_default_stays_plaintext_only() -> None:
    subject, text, html = build_email_content(
        "reset", "nl", None, None, {"name": "A", "brand": "B", "link": "https://x"}
    )
    assert html is None  # the pre-#236 contract for callers that pass no branding


# --------------------------------------------------------------------------- #
# End to end: the default reset mail is branded multipart
# --------------------------------------------------------------------------- #
async def test_default_reset_mail_sends_branded_html(client_for, monkeypatch) -> None:
    t = await make_tenant("brandmail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        ).status_code == 200
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            update(OrgSettings)
            .where(OrgSettings.org_id == t.org.id)
            .values(logo_url="/uploads/logo.png", primary_color="#00aa55")
        )
        await session.commit()

    captured: dict = {}

    async def _capture(provider, config, sender, message):  # noqa: ANN001, ARG001
        captured["message"] = message
        return True, None

    monkeypatch.setattr("app.core.email.service.send_email", _capture)

    async with async_session_maker() as session:
        sent, error = await send_password_email(
            session, t.user, "tok-brand1", _Req(t.host), kind="reset"
        )
    assert sent is True, error
    message = captured["message"]
    # Branded multipart out of the box: full HTML document, logo absolutised onto the org's
    # own host, CTA button in the org's primary color — and the plaintext link untouched.
    assert message.html is not None and message.html.lstrip().startswith("<!doctype")
    assert 'src="https://brandmail.' in message.html and "/uploads/logo.png" in message.html
    assert "#00aa55" in message.html
    assert "reset-password?token=tok-brand1" in message.html
    assert "reset-password?token=tok-brand1" in message.text
    assert "Brandmail" in message.html  # the tenant's brand, never the product's


async def test_signature_rides_inside_the_chrome(client_for, monkeypatch) -> None:
    t = await make_tenant("brandsig")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.put(
                "/api/v1/settings/email",
                json={**_BREVO, "signature_html": "<p>Groeten, het team</p>"},
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
            session, t.user, "tok-sig1", _Req(t.host), kind="invite"
        )
    assert sent is True, error
    html = captured["message"].html
    assert html is not None and html.count("<!doctype") == 1
    # The signature sits inside the card, before the document closes.
    assert "Groeten, het team" in html
    assert html.index("Groeten, het team") < html.index("</body>")
