"""Tenant-managed **provider catalog** (issue #89, part of #87).

One generic catalog of the outside services a tenant works with — ``providers(org_id, kind, name,
config, active, position)`` where ``kind ∈ {email, dns, registrar, hosting}``. A tenant adds e.g.
"Microsoft Exchange" / "Google Workspace" (email), "Cloudflare" (dns/hosting) or an OXXA registrar
once, under Instellingen, and every entity that references a provider (a domain's registrar / DNS /
email provider, a hosting's provider) points at a catalog row by ``provider_id``.

Core, not per-module (like custom fields §13 and roles §15): the same catalog is reused by several
modules, so it lives here and they depend on it. ``config`` is an open JSONB blob so per-provider
structured bits (API endpoints, integration hooks) can be added later with no schema change.

Managing the catalog needs ``settings.providers.manage``; merely *reading* it — to populate a
picker on a domain or hosting form — needs ``settings.providers.read`` (held by all staff).
"""

from __future__ import annotations

from app.core.providers.models import Provider, ProviderKind
from app.core.providers.schemas import ProviderCreate, ProviderRead, ProviderUpdate
from app.core.providers.service import ProviderService

__all__ = [
    "Provider",
    "ProviderCreate",
    "ProviderKind",
    "ProviderRead",
    "ProviderService",
    "ProviderUpdate",
]
