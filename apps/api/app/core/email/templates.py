"""Tenant-customisable email templates (#161 tier 2).

A tenant may override the subject and HTML body of any **customisable kind** — core's reset
and invite mails, plus whatever the enabled modules contribute (the invoice, quote and
reminder mails; :mod:`app.core.email.kinds`) — per locale, in Instellingen -> E-mail. A
missing override falls back to the built-in catalog text (tier 1), so *blank means default*
everywhere. Which ``{markers}`` a body may use is the **kind's** property, not this module's:
an invoice mail interpolates a number and an amount, a reset mail a link. They substitute with
the single-brace convention the rest of the API uses (:mod:`app.i18n`).

Safety: the HTML is sanitised with an email-safe allow-list on **write** and again on **send**
(after variable substitution, so a value smuggling markup — a user's display name, a client's
company name — is caught too). The plaintext part is always the catalog-rendered body, so every
mail keeps its working link or its full summary even when a tenant's HTML omits one.
"""

from __future__ import annotations

import html as html_lib
import re

import nh3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email.kinds import email_kind, require_email_kind
from app.core.email.models import OrgEmailTemplate
from app.core.email.senders import OutgoingEmail
from app.i18n import available_locales, translate

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
#: ``cid:`` is allowed because an inline image is a MIME part of *this* message
#: (``EmailAttachment.inline``, epic #269's payment QR), not a network fetch: it cannot report
#: an open back to anyone the way a remote ``<img>`` can, and stripping it would silently
#: remove the QR from a tenant-authored invoice body. ``data:`` stays out — most clients block
#: it — and so does everything else, ``javascript:`` first among them.
_URL_SCHEMES: set[str] = {"http", "https", "mailto", "cid"}


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
    return translate(require_email_kind(kind).subject_key, locale)


def default_body_html(kind: str, locale: str) -> str:
    """A starter HTML rendering of the catalog plaintext body, for the editor placeholder.

    Paragraphs on blank lines, ``<br>`` on single newlines, and the bare ``{link}`` made
    clickable. Variables stay as ``{...}`` markers — they resolve when the mail is sent.
    """
    body = translate(require_email_kind(kind).body_key, locale)
    paragraphs: list[str] = []
    for block in body.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        lines = [html_lib.escape(line) for line in block.split("\n")]
        paragraphs.append("<p>" + "<br>".join(lines) + "</p>")
    html = "\n".join(paragraphs)
    return html.replace("{link}", '<a href="{link}">{link}</a>')


def branded_default_html(kind: str, locale: str, values: dict[str, str], primary_color: str) -> str:
    """The built-in (tier 1) body as a real HTML fragment (#236): the catalog paragraphs with
    the bare ``{link}`` line rendered as a CTA button in the org's primary color.

    Values are escaped before substitution and the whole fragment passes
    :func:`sanitize_email_html` afterwards, so a value smuggling markup (a display name) is
    caught exactly like in the tier-2 path. The chrome (logo, card, footer) is not built
    here — it rides the send seam (:mod:`app.core.email.branding`).

    A kind with no ``button_key`` simply renders its paragraphs: the button is an affordance of
    the body, not of the layer.

    **A variable that resolves to nothing takes its line with it.** ``{link}`` is optional for
    some kinds — an invoice mail offers a pay button only while there is something to pay and
    something to pay it with (epic #269) — and the two naive renderings are both wrong in front
    of a client: an empty ``<p></p>`` opening a gap in the middle of the mail, or worse, a
    perfectly styled CTA whose ``href`` is the empty string, which navigates to the mail client's
    own idea of nowhere. So a line that renders blank is dropped, a paragraph left with no lines
    is dropped, and the button needs a URL before it is drawn at all.
    """
    from app.core.email.branding import button_html

    spec = require_email_kind(kind)
    body = translate(spec.body_key, locale)
    label = translate(spec.button_key, locale) if spec.button_key else ""
    link = values.get("link", "")
    # ``image`` is the one value that is **markup**, not text: an inline ``<img src="cid:…">``
    # (with its anchor) built by the composer, which is the only layer that knows the message's
    # attachments. It therefore skips the escaping every other value gets — and must skip the
    # substitution pass too, or a `{` in a URL would be re-read as a marker.
    image = values.get("image", "")
    escaped = {
        key: html_lib.escape(str(value)) for key, value in values.items() if key != "image"
    }
    blocks: list[str] = []
    for block in body.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        lines: list[str] = []
        button = False
        picture = False
        for line in block.split("\n"):
            if spec.button_key and line.strip() == "{link}":
                # The URL-on-its-own-line becomes the button, not a wall of href text.
                button = True
                continue
            if line.strip() == "{image}":
                # …and an image marker on its own line becomes the image. Same shape as the
                # button, and for the same reason: a block element cannot live inside a <p>.
                picture = True
                continue
            rendered = render_variables(html_lib.escape(line), escaped)
            # A line that was nothing but an unresolved variable is not a line.
            if rendered.strip():
                lines.append(rendered)
        if lines:
            blocks.append('<p style="margin:0 0 16px 0;">' + "<br>\n".join(lines) + "</p>")
        if button and link:
            blocks.append(button_html(label, link, primary_color))
        if picture and image:
            blocks.append(image)
    return sanitize_email_html("\n".join(blocks))


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


def _tidy(text: str) -> str:
    """Close the hole an optional variable leaves in the plaintext body.

    A catalog body puts ``{link}`` in a paragraph of its own so the HTML half can turn it into
    a button. When it resolves to nothing — no provider connected, nothing left to collect —
    the plaintext is left with a paragraph break, a blank line and another paragraph break in
    the middle of the letter. Trailing spaces go, runs of blank lines collapse to one, and the
    whole thing is stripped: the same mail, minus the gap.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


def build_email_content(
    kind: str,
    locale: str,
    subject_override: str | None,
    body_html_override: str | None,
    values: dict[str, str],
    *,
    primary_color: str | None = None,
) -> tuple[str, str, str | None]:
    """Return ``(subject, text, html)`` for one customisable mail.

    ``text`` is always the catalog-rendered plaintext body (so a working link — or the amount
    and due date an invoice mail promises — survives even a custom HTML that omits it).
    ``subject`` / ``html`` use the tenant override when it is non-blank, substituting variables
    and sanitising the HTML afterwards. Without an override, ``html`` is the branded built-in
    default (#236) when a ``primary_color`` is given — never ``None`` on the normal send path,
    so the mail leaves as styled multipart out of the box.
    """
    spec = require_email_kind(kind)
    subject = translate(spec.subject_key, locale, **values)
    # ``image`` is markup, and **an image has no plaintext form**: substituted into the text
    # part it would put a raw ``<table><img src="cid:…">`` in the middle of the letter every
    # client that shows plaintext would then display verbatim. It resolves to nothing here and
    # its line goes with it (:func:`_tidy`); the URL it links to is already in the body as
    # ``{link}``, so the plaintext reader loses no way in.
    text = _tidy(translate(spec.body_key, locale, **{**values, "image": ""}))
    html: str | None = None
    if subject_override and subject_override.strip():
        subject = _strip_tags(render_variables(subject_override, values))
    if body_html_override and body_html_override.strip():
        html = sanitize_email_html(render_variables(body_html_override, values))
    elif primary_color is not None:
        html = branded_default_html(kind, locale, values, primary_color)
    return subject, text, html


def is_supported_kind(kind: str) -> bool:
    return email_kind(kind) is not None


def is_supported_locale(locale: str) -> bool:
    return locale in available_locales()


# --------------------------------------------------------------------------- #
# Org-wide e-mail signature (owner request)
# --------------------------------------------------------------------------- #
def signature_plaintext(signature_html: str) -> str:
    """The signature's words for the plaintext part: tags dropped, line breaks kept."""
    text = re.sub(r"<br\s*/?>", "\n", signature_html, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(text).strip()


def apply_signature(message: OutgoingEmail, signature_html: str | None) -> OutgoingEmail:
    """Append the org's HTML signature to an outgoing message, automatically.

    Rides the one send seam (``send_org_email``), so every org mail — auth, notification,
    invoice — carries it without per-caller code. Sanitised again at send time (the same
    double-sanitise rule the templates follow); the plaintext part gets the classic
    ``-- `` delimiter with the signature's words. A text-only message is promoted to
    text+HTML so the signature can render as authored.
    """
    if not signature_html or not signature_html.strip():
        return message
    sig_html = sanitize_email_html(signature_html)
    if not sig_html.strip():
        return message
    body_html = message.html
    if body_html is None:
        escaped = html_lib.escape(message.text).replace("\n", "<br>\n")
        body_html = f"<p>{escaped}</p>"
    plain = signature_plaintext(sig_html)
    return OutgoingEmail(
        to=message.to,
        subject=message.subject,
        text=message.text + (f"\n\n-- \n{plain}" if plain else ""),
        html=f"{body_html}<br>{sig_html}",
        attachments=message.attachments,
    )
