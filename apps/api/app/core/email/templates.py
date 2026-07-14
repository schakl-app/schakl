"""Tenant-customisable auth email templates (#161 tier 2).

A tenant may override the subject and HTML body of the **reset** and **invite** mails, per
locale, in Instellingen -> E-mail. A missing override falls back to the built-in catalog text
(tier 1), so *blank means default* everywhere. Three variables are available — ``{brand}``,
``{name}``, ``{link}`` — the same ones tier 1's catalog strings already use, substituted with the
single-brace convention the rest of the API uses (:mod:`app.i18n`).

Safety: the HTML is sanitised with an email-safe allow-list on **write** and again on **send**
(after variable substitution, so a value smuggling markup — a user's display name — is caught
too). The plaintext part is always the catalog-rendered body, so every mail keeps a working
reset link even when a tenant's HTML omits one.
"""

from __future__ import annotations

import html as html_lib
import re

import nh3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email.models import EMAIL_TEMPLATE_KINDS, OrgEmailTemplate
from app.i18n import available_locales, translate

#: The variables a tenant may use in a template; shown in the editor.
TEMPLATE_VARIABLES: tuple[str, ...] = ("brand", "name", "link")

_VAR_RE = re.compile(r"\{(\w+)\}")

#: An email-safe allow-list. Templates are authored by ``settings.email.manage`` holders (org
#: admins), so this is defence-in-depth, not an untrusted-input boundary; still, no ``script`` /
#: event handlers / exotic schemes ever survive.
_EMAIL_TAGS: set[str] = {
    "a", "p", "br", "hr", "strong", "b", "em", "i", "u", "s", "small",
    "ul", "ol", "li", "blockquote", "pre", "code",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "span", "div", "img",
    "table", "thead", "tbody", "tr", "td", "th",
}
_STYLE_TAGS: set[str] = {
    "a", "p", "div", "span", "blockquote", "li", "td", "th", "table", "img",
    "h1", "h2", "h3", "h4", "h5", "h6",
}
_EMAIL_ATTRS: dict[str, set[str]] = {tag: {"style"} for tag in _STYLE_TAGS}
_EMAIL_ATTRS["a"] |= {"href", "title"}
_EMAIL_ATTRS["img"] = {"src", "alt", "width", "height", "style"}
_EMAIL_ATTRS["td"] |= {"align", "valign", "width", "colspan", "rowspan"}
_EMAIL_ATTRS["th"] |= {"align", "valign", "width", "colspan", "rowspan"}
_EMAIL_ATTRS["table"] |= {"width", "cellpadding", "cellspacing", "border", "align"}
_STYLE_PROPERTIES: set[str] = {
    "color", "background-color", "background", "font-size", "font-weight", "font-style",
    "font-family", "text-align", "text-decoration", "padding", "margin", "border",
    "border-radius", "line-height", "width", "max-width", "height", "display",
    "vertical-align",
}
_URL_SCHEMES: set[str] = {"http", "https", "mailto"}


def render_variables(text: str, values: dict[str, str]) -> str:
    """Substitute ``{name}``-style markers; unknown markers are left untouched (like i18n)."""
    return _VAR_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)


def sanitize_email_html(html: str) -> str:
    """Strip everything outside the email allow-list (script, handlers, unsafe schemes/CSS)."""
    return nh3.clean(
        html,
        tags=_EMAIL_TAGS,
        attributes=_EMAIL_ATTRS,
        filter_style_properties=_STYLE_PROPERTIES,
        url_schemes=_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


def _strip_tags(text: str) -> str:
    """A subject is plaintext: remove any markup a tenant pasted in."""
    return re.sub(r"<[^>]+>", "", text).strip()


def default_subject(kind: str, locale: str) -> str:
    """The built-in subject template (raw, with ``{brand}`` visible) for the editor placeholder."""
    return translate(f"auth.email.{kind}_subject", locale)


def default_body_html(kind: str, locale: str) -> str:
    """A starter HTML rendering of the catalog plaintext body, for the editor placeholder.

    Paragraphs on blank lines, ``<br>`` on single newlines, and the bare ``{link}`` made
    clickable. Variables stay as ``{...}`` markers — they resolve when the mail is sent.
    """
    body = translate(f"auth.email.{kind}_body", locale)
    paragraphs: list[str] = []
    for block in body.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        lines = [html_lib.escape(line) for line in block.split("\n")]
        paragraphs.append("<p>" + "<br>".join(lines) + "</p>")
    html = "\n".join(paragraphs)
    return html.replace("{link}", '<a href="{link}">{link}</a>')


async def resolve_template(
    session: AsyncSession, org_id, kind: str, locale: str  # noqa: ANN001
) -> OrgEmailTemplate | None:
    """The tenant override for ``(kind, locale)``, or ``None`` to use the built-in default."""
    return await session.scalar(
        select(OrgEmailTemplate).where(
            OrgEmailTemplate.org_id == org_id,
            OrgEmailTemplate.kind == kind,
            OrgEmailTemplate.locale == locale,
        )
    )


def build_email_content(
    kind: str,
    locale: str,
    subject_override: str | None,
    body_html_override: str | None,
    values: dict[str, str],
) -> tuple[str, str, str | None]:
    """Return ``(subject, text, html)`` for an auth mail.

    ``text`` is always the catalog-rendered plaintext body (so a working link survives even a
    linkless custom HTML). ``subject`` / ``html`` use the tenant override when it is non-blank,
    substituting variables and sanitising the HTML afterwards.
    """
    subject = translate(f"auth.email.{kind}_subject", locale, **values)
    text = translate(f"auth.email.{kind}_body", locale, **values)
    html: str | None = None
    if subject_override and subject_override.strip():
        subject = _strip_tags(render_variables(subject_override, values))
    if body_html_override and body_html_override.strip():
        html = sanitize_email_html(render_variables(body_html_override, values))
    return subject, text, html


def is_supported_kind(kind: str) -> bool:
    return kind in EMAIL_TEMPLATE_KINDS


def is_supported_locale(locale: str) -> bool:
    return locale in available_locales()
