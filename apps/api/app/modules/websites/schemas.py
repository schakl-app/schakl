"""Pydantic schemas for the websites module (issue #94, CLAUDE.md §9).

A website is named by its **address** — host plus path — and the API answers that name once
(``host`` / ``label`` / ``url`` on :class:`WebsiteRead`) rather than leaving every screen to
compose ``root ? name : "www." + name`` for itself, which is how eight copies of that expression
came to exist and none of them knew about the path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.party.schemas import PartyReadRef, PartyRef
from app.core.webaddress import normalize_path


def _normalize_path(value: object) -> object:
    """``normalize_path`` as a ``before`` validator: non-strings pass through for Pydantic to
    reject, and a refusal speaks the envelope's language (``errors.invalid_website_path``)."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    return normalize_path(value)


class WebsiteBase(BaseModel):
    root: bool = True
    #: Where under the host the site lives: ``""`` (the root) or ``/briellaerd``. Normalised on
    #: the way in, so ``briellaerd/`` and ``/briellaerd`` are one address.
    path: str = Field(default="", max_length=500)
    #: The client this site belongs to where it is not the domain's — a client's dev install on
    #: the agency's own domain. ``null`` follows the domain, and an override that merely
    #: restates the domain's client is stored as ``null`` too.
    company_override_id: uuid.UUID | None = None
    technical_owner: PartyRef | None = None
    hosting_id: uuid.UUID | None = None
    uptime_enabled: bool = False
    custom: dict[str, Any] = Field(default_factory=dict)

    _normalize_path = field_validator("path", mode="before")(_normalize_path)


class WebsiteCreate(WebsiteBase):
    domain_id: uuid.UUID


class WebsiteUpdate(BaseModel):
    root: bool | None = None
    path: str | None = Field(default=None, max_length=500)
    #: Absent leaves the client alone; an explicit ``null`` makes the site follow its domain
    #: again (§18's pair).
    company_override_id: uuid.UUID | None = None
    technical_owner: PartyRef | None = None
    hosting_id: uuid.UUID | None = None
    uptime_enabled: bool | None = None
    custom: dict[str, Any] | None = None

    _normalize_path = field_validator("path", mode="before")(_normalize_path)


class AvailableDomain(BaseModel):
    """A domain a website may be created on — the create picker's option, and nothing more.

    Deliberately not a ``DomainRead`` subset: a picker that borrows another module's read schema
    inherits every field somebody adds to it, and this one crosses a module boundary already
    (§6 — a bare-table bridge, not an import).

    ``taken`` names the addresses already recorded on the domain (``breik.dev``,
    ``breik.dev/briellaerd``), so the form can show the constraint working (#305) instead of
    letting the save discover it: a domain with several sites is the ordinary case now, and a
    picker that hid every claimed domain would hide exactly the ones a dev install goes on.
    """

    id: uuid.UUID
    name: str
    company_id: uuid.UUID
    taken: list[str] = Field(default_factory=list)


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    domain_id: uuid.UUID
    domain_name: str = ""
    root: bool
    path: str = ""
    #: The hostname the site answers on (``breik.dev`` / ``www.klant.nl``), the label every
    #: screen prints (``breik.dev/briellaerd``) and the address to open (``https://…``) — all
    #: resolved here, none stored, empty under ``meta=false``.
    host: str = ""
    label: str = ""
    url: str = ""
    #: The client as **resolved**: the override where the site names one, else the parent
    #: domain's. ``company_override_id`` beside it says which of the two it was, which is what
    #: the edit form needs to draw "follows the domain" rather than a frozen copy of it.
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    company_override_id: uuid.UUID | None = None
    #: The parent domain's client — what the site follows when it names no client of its own,
    #: so a form can say "volgt het domein (Nova)" beside an override rather than lose it.
    domain_company_id: uuid.UUID | None = None
    technical_owner: PartyReadRef | None = None
    hosting_id: uuid.UUID | None = None
    hosting_name: str | None = None
    uptime_enabled: bool = False
    #: What the monitor last reported — ``up`` / ``down`` / ``pending`` / ``maintenance``,
    #: the monitor's own vocabulary (#356). ``None`` means *nothing has looked*: either no
    #: monitor is attached, or one is and has never reported. It is deliberately **not** the
    #: same value as ``uptime_enabled``, which is a tick in a box and says nothing about
    #: health — a green pill drawn from that flag showed a site that had been down for two
    #: hours exactly as it showed one that was up.
    #:
    #: Resolved through :mod:`app.core.monitoring`, so this module never learns what a monitor
    #: is (§6); `None` is also the honest answer on an instance with no uptime module at all.
    uptime_status: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
