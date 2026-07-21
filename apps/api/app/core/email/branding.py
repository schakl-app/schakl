"""Branded HTML chrome for outgoing org e-mail (#236).

Every mail the platform sends — auth, notifications, invoicing, test sends — leaves as a
multipart message whose HTML part is wrapped in the tenant's branding (CLAUDE.md §7): logo,
brand name and primary color from ``org_settings``, no rebuild and nothing hardcoded. The
chrome is applied at the send seam (:func:`app.core.email.service.send_org_email`) or, for
the invoicing paths that bypass it, via :func:`apply_branding` directly.

Two layers, one rule each (docs/EMAIL.md):

- **Content** is a sanitised *fragment* (paragraphs, a button, a table) built per mail and
  run through :func:`app.core.email.templates.sanitize_email_html` whenever any value in it
  is not our own literal.
- **Chrome** is the outer document built here — table layout, inline styles only, 600 px —
  and is never sanitised (it needs ``<html>``/``<body>``), so everything interpolated into
  it is escaped or validated: colors against a hex pattern, the logo URL escaped and forced
  http(s), the brand name HTML-escaped.

The plaintext part is untouched by all of this: every mail keeps a working text alternative.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email.senders import OutgoingEmail

logger = logging.getLogger("schakl.email")

#: ``org_settings.primary_color`` model default — the fallback when a stored value is unusable.
DEFAULT_PRIMARY = "#4f46e5"

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")

#: Safe everywhere, no quoting needed inside style attributes (docs/EMAIL.md).
FONT_STACK = "Arial,Helvetica,sans-serif"

_TEXT = "#1f2937"
_MUTED = "#6b7280"
_PAGE_BG = "#f2f4f6"
_CARD_BG = "#ffffff"


@dataclass(frozen=True)
class EmailBrand:
    """The branding a mail renders with, resolved once per send."""

    brand_name: str
    logo_url: str | None  # absolute http(s) URL, ready for <img src>, or None
    show_brand_name: bool
    primary_color: str  # validated hex
    base_url: str  # the org's own address (deep links, relative-logo resolution)


def _safe_color(value: str | None, fallback: str = DEFAULT_PRIMARY) -> str:
    """A color goes into an unsanitised style attribute — only a hex literal may pass."""
    if value and _HEX_RE.match(value.strip()):
        return value.strip()
    return fallback


def _safe_logo(logo_url: str | None, base_url: str) -> str | None:
    """Absolutise a relative logo path; refuse any scheme but http(s)."""
    if not logo_url or not logo_url.strip():
        return None
    url = logo_url.strip()
    if url.startswith("/"):
        url = base_url.rstrip("/") + url
    if not url.lower().startswith(("http://", "https://")):
        return None
    return url


def brand_from(org, org_settings) -> EmailBrand:  # noqa: ANN001
    """Build an :class:`EmailBrand` from rows a caller already holds (no extra queries)."""
    from app.core.auth.sso import org_base_url

    base_url = org_base_url(org)
    brand_name = (getattr(org_settings, "brand_name", None) or "") or org.name
    return EmailBrand(
        brand_name=brand_name,
        logo_url=_safe_logo(getattr(org_settings, "logo_url", None), base_url),
        show_brand_name=bool(getattr(org_settings, "show_brand_name", True)),
        primary_color=_safe_color(getattr(org_settings, "primary_color", None)),
        base_url=base_url,
    )


async def load_brand(session: AsyncSession, org) -> EmailBrand:  # noqa: ANN001
    """Resolve the org's branding for a mail. RLS-scoped like every read on this path."""
    from app.core.models import OrgSettings

    org_settings = await session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == org.id)
    )
    return brand_from(org, org_settings)


async def load_brand_by_id(session: AsyncSession, org_id) -> EmailBrand | None:  # noqa: ANN001
    """Best-effort brand for the send seam, which holds only the id. ``None`` — send bare
    rather than block the mail (the same rule ``_org_brand`` follows)."""
    from app.core.models import Org

    try:
        org = await session.get(Org, org_id)
        if org is None:
            return None
        return await load_brand(session, org)
    except Exception:  # noqa: BLE001 — a branding hiccup must never block a mail
        logger.warning("email branding could not be resolved for org %s", org_id, exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# Fragment helpers (content layer — email-client-safe, inline styles only)
# --------------------------------------------------------------------------- #
def paragraphs_html(text: str) -> str:
    """Escape plaintext into ``<p>`` blocks: blank line = paragraph, newline = ``<br>``."""
    paragraphs: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        lines = [html_lib.escape(line) for line in block.split("\n")]
        paragraphs.append(
            '<p style="margin:0 0 16px 0;">' + "<br>\n".join(lines) + "</p>"
        )
    return "\n".join(paragraphs)


def button_html(label: str, url: str, color: str) -> str:
    """A bulletproof CTA button: a padded table cell, no images, no VML required."""
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="margin:24px 0;">'
        "<tr>"
        f'<td style="background-color:{_safe_color(color)};border-radius:6px;">'
        f'<a href="{html_lib.escape(url, quote=True)}"'
        f' style="display:inline-block;padding:12px 24px;font-family:{FONT_STACK};'
        'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;">'
        f"{html_lib.escape(label)}</a>"
        "</td></tr></table>"
    )


def link_html(label: str, url: str, color: str) -> str:
    """An inline branded link for list-style content (digest items)."""
    return (
        f'<a href="{html_lib.escape(url, quote=True)}"'
        f' style="color:{_safe_color(color)};text-decoration:underline;">'
        f"{html_lib.escape(label)}</a>"
    )


# --------------------------------------------------------------------------- #
# Chrome (outer document — built from validated/escaped values, never sanitised)
# --------------------------------------------------------------------------- #
def _header_html(brand: EmailBrand) -> str:
    name = html_lib.escape(brand.brand_name)
    parts: list[str] = []
    if brand.logo_url:
        parts.append(
            f'<img src="{html_lib.escape(brand.logo_url, quote=True)}" alt="{name}"'
            ' height="40" style="height:40px;max-width:220px;border:0;display:block;">'
        )
    if brand.show_brand_name or not brand.logo_url:
        margin = "margin:8px 0 0 0;" if brand.logo_url else "margin:0;"
        parts.append(
            f'<p style="{margin}font-family:{FONT_STACK};font-size:18px;'
            f'font-weight:700;color:{brand.primary_color};">{name}</p>'
        )
    return "\n".join(parts)


def render_branded_email(
    brand: EmailBrand, content_html: str, *, preheader: str | None = None
) -> str:
    """Wrap a content fragment in the tenant's chrome: full document, tables, 600 px."""
    pre = ""
    if preheader and preheader.strip():
        snippet = html_lib.escape(preheader.strip().splitlines()[0][:120])
        if snippet:
            pre = (
                '<div style="display:none;max-height:0;overflow:hidden;">'
                f"{snippet}</div>"
            )
    title = html_lib.escape(brand.brand_name)
    return (
        "<!doctype html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light">\n'
        f"<title>{title}</title>\n"
        "</head>\n"
        f'<body style="margin:0;padding:0;background-color:{_PAGE_BG};">\n'
        f"{pre}"
        '<table width="100%" cellpadding="0" cellspacing="0" border="0"'
        f' style="background-color:{_PAGE_BG};">\n'
        '<tr><td align="center" style="padding:24px 12px;">\n'
        '<table width="600" cellpadding="0" cellspacing="0" border="0"'
        ' style="width:600px;max-width:100%;">\n'
        f'<tr><td align="left" style="padding:0 8px 16px 8px;">{_header_html(brand)}</td></tr>\n'
        '<tr><td align="left" style="'
        f"background-color:{_CARD_BG};border-radius:8px;padding:32px;"
        f'font-family:{FONT_STACK};font-size:15px;line-height:1.6;color:{_TEXT};">\n'
        f"{content_html}\n"
        "</td></tr>\n"
        '<tr><td align="left" style="'
        f'padding:16px 8px 0 8px;font-family:{FONT_STACK};font-size:12px;color:{_MUTED};">'
        f"{title}</td></tr>\n"
        "</table>\n"
        "</td></tr>\n"
        "</table>\n"
        "</body>\n"
        "</html>"
    )


def apply_branding(brand: EmailBrand | None, message: OutgoingEmail) -> OutgoingEmail:
    """Give an outgoing mail its branded HTML part.

    A text-only message is promoted (escaped paragraphs) first, so even the humblest mail
    leaves as multipart; an existing HTML *fragment* — a template body, a digest, the
    signature ride — is wrapped as-is. Idempotent by construction: a message whose HTML is
    already a full document (starts with a doctype) is left alone.
    """
    if brand is None:
        return message
    body_html = message.html
    if body_html is not None and body_html.lstrip()[:9].lower() == "<!doctype":
        return message
    if body_html is None:
        body_html = paragraphs_html(message.text)
    return replace(
        message,
        html=render_branded_email(brand, body_html, preheader=message.text),
    )
