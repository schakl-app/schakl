"""Outbound webhook delivery for the ``webhook.post`` action (issues #27, #96).

Security first: the URL is tenant data typed by a rule author, and this code runs on the
tenant's own server — an unchecked POST is a free SSRF probe of the internal network. So:

* only ``http``/``https``;
* the hostname is resolved and **every** resolved address must be public — loopback,
  private (RFC 1918), link-local and otherwise non-global targets are refused unless the
  operator explicitly set ``SCHAKL_ALLOW_PRIVATE_NOTIFICATION_TARGETS`` (their n8n may
  legitimately live on the LAN);
* redirects are not followed (a public URL 302'ing to ``169.254.169.254`` is the classic
  bypass);
* a hard timeout, because the worker's slot is shared.

Confirmation semantics (#96): with ``confirm: true`` the webhook is a *handshake*, not
fire-and-forget — a non-2xx response **or** a JSON body ``{"ok": false}`` fails the step (and
so the run), which is how "wait for the flow to confirm it's monitored" is expressed. Without
``confirm`` the request only fails on transport errors; the response status is recorded but
not judged.

``_resolve_addrs`` and ``_send`` are deliberate seams: tests stub them (no live DNS or HTTP in
the suite), the guard logic between them runs for real.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings

TIMEOUT_SECONDS = 10.0


class WebhookError(Exception):
    """A delivery/validation failure; ``str()`` is what the run step records.

    The message is an ``errors.*`` i18n key where the *engine* refused (private target, bad
    URL), and raw transport data (an HTTP status, an exception string) where the outside
    world answered — the runs UI translates the former and prints the latter. ``result``
    carries whatever response detail existed (the HTTP status of a refused confirmation),
    so a failed step still says what came back.
    """

    def __init__(self, message: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.result = result


def _parse(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebhookError("errors.automation_webhook_invalid_url")
    return parsed.scheme, parsed.hostname


async def _resolve_addrs(host: str) -> list[str]:
    """Every address the hostname resolves to (test seam; literal IPs skip DNS)."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except OSError as exc:
        raise WebhookError(f"DNS: {exc}") from exc
    return sorted({info[4][0] for info in infos})


async def ensure_public_target(url: str) -> None:
    """Refuse a URL whose host resolves to any non-public address (unless allowed)."""
    _, host = _parse(url)
    if settings.allow_private_notification_targets:
        return
    for addr in await _resolve_addrs(host):
        ip = ipaddress.ip_address(addr)
        if not ip.is_global:
            raise WebhookError("errors.automation_webhook_private_target")


async def _send(url: str, body: dict[str, Any]) -> tuple[int, Any]:
    """POST and return ``(status_code, parsed json | None)`` (test seam)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT_SECONDS), follow_redirects=False
    ) as client:
        response = await client.post(url, json=body)
    try:
        parsed = response.json()
    except (json.JSONDecodeError, ValueError):
        parsed = None
    return response.status_code, parsed


async def post_webhook(url: str, body: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
    """Deliver ``body`` to ``url``; returns the step result or raises :class:`WebhookError`."""
    await ensure_public_target(url)
    try:
        status_code, parsed = await _send(url, body)
    except httpx.HTTPError as exc:
        raise WebhookError(f"{type(exc).__name__}: {exc}") from exc
    result: dict[str, Any] = {"status_code": status_code}
    if confirm:
        confirmed = 200 <= status_code < 300 and not (
            isinstance(parsed, dict) and parsed.get("ok") is False
        )
        result["confirmed"] = confirmed
        if not confirmed:
            raise WebhookError("errors.automation_webhook_not_confirmed", result=result)
    return result
