"""The four e-mail transports (#17): Brevo / SendGrid / SMTP2GO official APIs + plain SMTP.

Every sender takes the decrypted provider config and returns ``(ok, error)`` where ``error``
carries the provider's own message — that is what the test-send button shows the admin. No
sender raises for a delivery failure; exceptions mean a programming error, not a bounced mail.

The named services deliberately use their HTTP APIs, not their SMTP relays: a JSON error body
beats a 4xx SMTP dialogue for diagnosability, and outbound 443 works where 587 is blocked.
Plain SMTP uses the stdlib client on a worker thread — no extra dependency for the one
transport that is inherently synchronous.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage

import httpx

_TIMEOUT = 15.0


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str | None = None


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


def _mime(sender: Sender, message: OutgoingEmail) -> MimeMessage:
    mime = MimeMessage()
    mime["From"] = f"{sender.from_name} <{sender.from_email}>"
    mime["To"] = message.to
    mime["Subject"] = message.subject
    if sender.reply_to:
        mime["Reply-To"] = sender.reply_to
    mime.set_content(message.text)
    if message.html:
        mime.add_alternative(message.html, subtype="html")
    return mime


def _send_smtp_sync(
    config: dict, sender: Sender, message: OutgoingEmail
) -> tuple[bool, str | None]:
    host = str(config.get("host") or "")
    port = int(config.get("port") or 587)
    security = str(config.get("security") or "starttls")
    username = str(config.get("username") or "")
    password = str(config.get("password") or "")
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
    return await _post_json(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": str(config.get("api_key") or "")},
        payload=payload,
        ok_statuses=(200, 201, 202),
        error_path=("message",),
    )


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
    return await _post_json(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {config.get('api_key') or ''}"},
        payload=payload,
        ok_statuses=(200, 202),
        error_path=("errors", 0, "message"),
    )


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
