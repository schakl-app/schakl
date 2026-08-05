"""The four e-mail transports (#17): Brevo / SendGrid / SMTP2GO official APIs + plain SMTP.

Every sender takes the decrypted provider config and returns ``(ok, error)`` where ``error``
carries the provider's own message — that is what the test-send button shows the admin. No
sender raises for a delivery failure; exceptions mean a programming error, not a bounced mail.

The named services deliberately use their HTTP APIs, not their SMTP relays: a JSON error body
beats a 4xx SMTP dialogue for diagnosability, and outbound 443 works where 587 is blocked.
Plain SMTP uses the stdlib client on a worker thread — no extra dependency for the one
transport that is inherently synchronous.

**Inline images** (epic #269: the invoice mail wants its payment QR *in* the body) are the one
capability the four transports genuinely disagree about, and each expresses it in its own
vocabulary — a nested MIME part, a field on the attachment object, a separate top-level array,
or not at all. What they agree on is stated once, in :class:`EmailAttachment`: the content id
**is the filename**. Ask :func:`supports_inline_images` before composing an ``<img>``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage as MimeMessage

import httpx

from app.core.net_guard import SsrfBlocked, assert_host_public_sync

_TIMEOUT = 15.0

logger = logging.getLogger("schakl.email")


@dataclass
class EmailAttachment:
    """A file riding an outgoing mail (issue #207: the sent invoice carries its PDF).

    ``inline=True`` makes it **body content** rather than a paperclip: still a real MIME part,
    but one carrying a Content-ID and ``Content-Disposition: inline``, which the HTML body
    references as ``cid:<filename>`` (epic #269's payment QR).

    **The content id is the filename, and that is a contract, not a shortcut.** SMTP2GO's
    ``inlines`` array has no id field at all — its documentation says to write
    ``<img src="cid:filename"/>`` — so there the filename *is* the cid and nothing else can be.
    SMTP and SendGrid both let us choose one freely, so the only way a single composed HTML
    fragment can travel unchanged over all three is to make the two that have a choice agree
    with the one that does not. A filename is therefore an **identity**: keep it short, ASCII
    and unique within the message (``invoice-qr.png``), and never let two inline parts share one.

    Not every transport can do this at all — ask :func:`supports_inline_images` *before* you
    compose the ``<img>``. A ``cid:`` that never arrives is a broken-image box in the middle of
    an invoice, which is strictly worse than the fallback you would have drawn instead.
    """

    filename: str
    content: bytes
    mimetype: str = "application/octet-stream"
    inline: bool = False


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str | None = None
    attachments: list[EmailAttachment] = field(default_factory=list)


@dataclass
class Sender:
    """The resolved from/reply-to identity, shared by every provider."""

    from_email: str
    from_name: str
    reply_to: str | None = None


async def send_email(
    provider: str, config: dict, sender: Sender, message: OutgoingEmail
) -> tuple[bool, str | None]:
    """Dispatch to the configured transport. Returns ``(ok, provider's own error)``."""
    if provider == "brevo":
        return await _send_brevo(config, sender, message)
    if provider == "sendgrid":
        return await _send_sendgrid(config, sender, message)
    if provider == "smtp2go":
        return await _send_smtp2go(config, sender, message)
    if provider == "smtp":
        return await asyncio.to_thread(_send_smtp_sync, config, sender, message)
    return False, f"unknown provider '{provider}'"


#: Which transports can carry a ``cid:`` body image, each verified against that provider's own
#: current documentation. Brevo is the odd one out: its attachment object is ``{url, content,
#: name}`` and it documents no Content-ID mechanism whatsoever.
_INLINE_CAPABLE: frozenset[str] = frozenset({"smtp", "sendgrid", "smtp2go"})


def supports_inline_images(provider: str) -> bool:
    """May a mail sent over ``provider`` reference an image as ``cid:<filename>`` in its body?

    **Ask before composing, not after sending.** The composer is the only layer that can pick
    a fallback — a plain "bekijk en betaal" link where the QR would have gone — and it can only
    pick one if it knows first. Discovering the failure afterwards is not an option worth
    having: the mail is already in the client's inbox with a broken-image box in it.

    Anything not in the table answers ``False``, which fails closed. That deliberately includes
    ``"instance"``: the operator-provided transport (epic #199) is a *settings* choice, not a
    transport — :func:`send_email` rejects the name too — so resolve it to the real provider
    through :mod:`app.core.email.service` and ask about that.
    """
    return provider in _INLINE_CAPABLE


#: Which situations we have already told the log cannot carry a body image. **Once per process,
#: not once per mail**: a nightly invoice run over a Brevo org would otherwise write the same
#: line a thousand times, and what is being reported — *this transport has no cid mechanism* —
#: is a property of the configuration, not of any one message. It is worth saying at all
#: because it means a composer skipped :func:`supports_inline_images`, which is one bug to fix,
#: not a thousand incidents.
_INLINE_DROP_LOGGED: set[str] = set()


def _log_inline_dropped(situation: str, count: int) -> None:
    if situation in _INLINE_DROP_LOGGED:
        return
    _INLINE_DROP_LOGGED.add(situation)
    logger.info(
        "%s cannot carry inline images; dropped %d body image(s). "
        "Call supports_inline_images() before composing a cid: reference.",
        situation,
        count,
    )


def _mime(sender: Sender, message: OutgoingEmail) -> MimeMessage:
    """Build the MIME tree for the SMTP transport.

    The **nesting** is the whole point here, and getting it wrong is invisible until a mail
    client draws the message: an image attached at the top level is a paperclip that no
    ``cid:`` resolves to, however impeccable its Content-ID. The tree is therefore::

        multipart/mixed                      (only when there are ordinary attachments)
        ├── multipart/alternative
        │   ├── text/plain
        │   └── multipart/related            (only when there are inline images)
        │       ├── text/html
        │       └── image/png  Content-ID: <invoice-qr.png>, disposition inline
        └── application/pdf                  the paperclips

    ``EmailMessage.add_related`` on the **html part** is what performs the inner wrap: the
    stdlib moves the existing ``text/html`` content down into the new ``multipart/related``
    for us. Which is why the part is looked up once, before the first call, and reused after —
    its object identity survives both that wrap and the ``multipart/mixed`` one, but its
    *content type* does not, so a second lookup would find nothing.

    ``cid=`` takes the angle brackets (RFC 2392: the header is ``<id>``, the URL is ``cid:id``)
    and ``disposition="inline"`` is passed explicitly — ``set_content`` turns any part with a
    ``filename`` into an attachment otherwise, and we want both the name and the disposition.
    """
    mime = MimeMessage()
    mime["From"] = f"{sender.from_name} <{sender.from_email}>"
    mime["To"] = message.to
    mime["Subject"] = message.subject
    if sender.reply_to:
        mime["Reply-To"] = sender.reply_to
    mime.set_content(message.text)
    if message.html:
        mime.add_alternative(message.html, subtype="html")
    html_part = next(
        (part for part in mime.iter_parts() if part.get_content_type() == "text/html"), None
    )
    for attachment in message.attachments:
        maintype, _, subtype = attachment.mimetype.partition("/")
        maintype, subtype = maintype or "application", subtype or "octet-stream"
        if attachment.inline:
            if html_part is None:
                # Nothing to be inline *in*: with no HTML body there is no <img> referencing
                # this part, so attaching it anyway would produce exactly the stray paperclip
                # the flag exists to avoid. Same judgement as Brevo's, one layer down.
                _log_inline_dropped("smtp (text-only message)", 1)
                continue
            html_part.add_related(
                attachment.content,
                maintype=maintype,
                subtype=subtype,
                cid=f"<{attachment.filename}>",
                filename=attachment.filename,
                disposition="inline",
            )
            continue
        mime.add_attachment(
            attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename,
        )
    return mime


def _send_smtp_sync(
    config: dict, sender: Sender, message: OutgoingEmail
) -> tuple[bool, str | None]:
    host = str(config.get("host") or "")
    port = int(config.get("port") or 587)
    security = str(config.get("security") or "starttls")
    username = str(config.get("username") or "")
    password = str(config.get("password") or "")
    # SSRF (audit F24): an email-settings admin must not point the relay at an internal host to
    # probe the network. Refuse a non-public SMTP host unless the operator opted the instance into
    # private targets (an internal MTA is a legitimate but deliberate choice).
    try:
        assert_host_public_sync(host)
    except SsrfBlocked as exc:
        return False, f"blocked: {exc}"
    try:
        if security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, timeout=_TIMEOUT, context=ssl.create_default_context()
            )
        else:
            client = smtplib.SMTP(host, port, timeout=_TIMEOUT)
        with client:
            if security == "starttls":
                client.starttls(context=ssl.create_default_context())
            if username:
                client.login(username, password)
            client.send_message(_mime(sender, message))
        return True, None
    except (smtplib.SMTPException, OSError) as exc:
        return False, str(exc)


async def _send_brevo(
    config: dict, sender: Sender, message: OutgoingEmail
) -> tuple[bool, str | None]:
    payload: dict = {
        "sender": {"email": sender.from_email, "name": sender.from_name},
        "to": [{"email": message.to}],
        "subject": message.subject,
        "textContent": message.text,
    }
    if message.html:
        payload["htmlContent"] = message.html
    if sender.reply_to:
        payload["replyTo"] = {"email": sender.reply_to}
    # Brevo's attachment object is {url, content, name}: no Content-ID, no disposition, nothing
    # a `cid:` can resolve to — and the community consistently reports the header dropped in
    # transit even when smuggled. So an inline part is **dropped**, never downgraded to an
    # ordinary attachment: a bare QR image paperclipped to the bottom of an invoice mail, beside
    # a broken-image box where it should have been, is worse than the plain link the composer
    # would have drawn had it asked supports_inline_images() first.
    ordinary = [a for a in message.attachments if not a.inline]
    if len(ordinary) != len(message.attachments):
        _log_inline_dropped("brevo", len(message.attachments) - len(ordinary))
    if ordinary:
        payload["attachment"] = [
            {"name": a.filename, "content": base64.b64encode(a.content).decode()}
            for a in ordinary
        ]
    return await _post_json(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": str(config.get("api_key") or "")},
        payload=payload,
        ok_statuses=(200, 201, 202),
        error_path=("message",),
    )


def _sendgrid_part(a: EmailAttachment) -> dict:
    """One `attachments` entry. Inline and ordinary differ by two fields, not by array —
    ``content_id`` is meaningful to SendGrid *only* alongside ``disposition: inline``, so the
    two are always written together and never separately."""
    part = {
        "content": base64.b64encode(a.content).decode(),
        "filename": a.filename,
        "type": a.mimetype,
        "disposition": "inline" if a.inline else "attachment",
    }
    if a.inline:
        part["content_id"] = a.filename
    return part


async def _send_sendgrid(
    config: dict, sender: Sender, message: OutgoingEmail
) -> tuple[bool, str | None]:
    content = [{"type": "text/plain", "value": message.text}]
    if message.html:
        content.append({"type": "text/html", "value": message.html})
    payload: dict = {
        "personalizations": [{"to": [{"email": message.to}]}],
        "from": {"email": sender.from_email, "name": sender.from_name},
        "subject": message.subject,
        "content": content,
    }
    if sender.reply_to:
        payload["reply_to"] = {"email": sender.reply_to}
    if message.attachments:
        # SendGrid keeps body images in the *same* array, distinguished by two fields. From its
        # reference: "The content_id is used when the disposition is set to inline and the
        # attachment is an image, allowing the file to be displayed within the body of the
        # email." The HTML then references `cid:<content_id>` — and the content id is the
        # filename, for the reason EmailAttachment states.
        payload["attachments"] = [_sendgrid_part(a) for a in message.attachments]
    return await _post_json(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {config.get('api_key') or ''}"},
        payload=payload,
        ok_statuses=(200, 202),
        error_path=("errors", 0, "message"),
    )


def _smtp2go_part(a: EmailAttachment) -> dict:
    """One entry, in the shape SMTP2GO's ``attachments`` *and* ``inlines`` arrays both take.

    The two arrays are the same object; which array it lands in is the entire difference. That
    is worth stating, because it is what makes the mistake so easy: an inline image put in
    ``attachments`` is accepted, delivered, and shown as a paperclip.
    """
    return {
        "filename": a.filename,
        "fileblob": base64.b64encode(a.content).decode(),
        "mimetype": a.mimetype,
    }


async def _send_smtp2go(
    config: dict, sender: Sender, message: OutgoingEmail
) -> tuple[bool, str | None]:
    payload: dict = {
        "sender": f"{sender.from_name} <{sender.from_email}>",
        "to": [message.to],
        "subject": message.subject,
        "text_body": message.text,
    }
    if message.html:
        payload["html_body"] = message.html
    ordinary = [a for a in message.attachments if not a.inline]
    inline = [a for a in message.attachments if a.inline]
    if ordinary:
        payload["attachments"] = [_smtp2go_part(a) for a in ordinary]
    if inline:
        # A separate top-level array, per SMTP2GO's own reference: "An array of images to be
        # inlined into the email. Use an image in content as <img src="cid:filename"/>". Note
        # what that sentence settles — the transport offers no id field, so the **filename is
        # the cid**, which is where EmailAttachment's rule comes from in the first place.
        payload["inlines"] = [_smtp2go_part(a) for a in inline]
    ok, error = await _post_json(
        "https://api.smtp2go.com/v3/email/send",
        headers={"X-Smtp2go-Api-Key": str(config.get("api_key") or "")},
        payload=payload,
        ok_statuses=(200,),
        error_path=("data", "error"),
    )
    return ok, error


def _dig(data: object, path: tuple) -> object | None:
    for step in path:
        if isinstance(step, int) and isinstance(data, list) and len(data) > step:
            data = data[step]
        elif isinstance(data, dict):
            data = data.get(step)
        else:
            return None
    return data


async def _post_json(
    url: str, *, headers: dict, payload: dict, ok_statuses: tuple, error_path: tuple
) -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        return False, str(exc)
    if response.status_code in ok_statuses:
        # SMTP2GO answers 200 even for per-recipient failures; its error rides in the body.
        try:
            body = response.json()
        except ValueError:
            return True, None
        error = _dig(body, error_path)
        if error:
            return False, str(error)
        return True, None
    try:
        error = _dig(response.json(), error_path)
    except ValueError:
        error = None
    return False, str(error) if error else f"HTTP {response.status_code}"
