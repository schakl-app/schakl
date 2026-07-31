"""Canonical tenant host (#291).

An org with a verified custom domain has **two** valid origins: the operator-controlled
``<slug>.<base_domain>`` host and the customer-owned custom domain. The policy, in one place:

- The **custom domain is canonical only while it is live** — verified *and*, where Cloudflare
  for SaaS manages its certificate, hostname status ``active`` + SSL status ``active`` + DNS
  still pointing at the SaaS target (``app.core.cloud.domain_health`` keeps that state fresh).
- The **slug host is never removed**: it keeps resolving (``app.core.tenancy.resolve_org``)
  whatever the custom domain's health, so the operator always has a recovery path when
  customer DNS or certificate renewal breaks. While the custom domain is live, browser
  navigation to the slug host is *redirected* to the canonical host by the web app; API and
  MCP calls are deliberately never redirected (dual-origin is supported there — a blind 307
  would break non-idempotent requests and cookie-less clients).
- Every **generated absolute link** — e-mails, OAuth callbacks, calendar webhooks, the
  provisioning API's ``url`` — goes through :func:`org_base_url`, so a link never points at
  a domain whose edge cannot serve it.

Sessions are host-only cookies: moving between the two origins means signing in again. That
is by design — an arbitrary customer domain must never be able to share the base domain's
cookie scope (docs/CLOUD.md, "Canonical host").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids a models import cycle
    from app.core.models import Org


def slug_host(org: Org) -> str:
    """The operator-controlled address this org always answers on."""
    return f"{org.slug}.{settings.base_domain}"


def custom_domain_live(org: Org) -> bool:
    """Whether the org's custom domain is verified **and** actually serving.

    Three postures:

    - No verified custom domain → ``False``.
    - Verified, no Cloudflare custom hostname (``cf_hostname_id`` is NULL — every self-host
      box and the Traefik/Let's Encrypt cloud posture) → ``True``: the router and its
      certificate follow the verification directly, there is no state to poll.
    - Verified with a Cloudflare custom hostname → live only when the last reconciliation
      saw hostname ``active`` + SSL ``active`` and DNS not moved away. A row whose state was
      **never captured** (verified before #291, ``domain_checked_at`` NULL) stays live — an
      upgrade must not silently demote a working domain; the first sweep records the truth.
    """
    if not (org.custom_domain and org.custom_domain_verified_at):
        return False
    if not org.cf_hostname_id:
        return True
    if org.domain_checked_at is None:
        return True
    return (
        org.cf_hostname_status == "active"
        and org.cf_ssl_status == "active"
        and org.domain_dns_ok is not False
    )


def canonical_host(org: Org) -> str:
    """The one host generated links and browser navigation should land on."""
    if custom_domain_live(org):
        return org.custom_domain  # type: ignore[return-value] — live implies present
    return slug_host(org)


def org_base_url(org: Org) -> str:
    """The address users reach this org on: its live custom domain, else the slug host."""
    return f"https://{canonical_host(org)}"
