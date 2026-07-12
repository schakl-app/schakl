"""API schemas for the party value type (issue #88).

``PartyRef`` is what a client sends (a type + optional id); ``PartyReadRef`` is what it gets back
— the same pair plus a resolved, locale-agnostic display ``label`` (a company name, a person's
name, or the agency's brand), so a client can render "who's responsible" without a second lookup.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.core.party.models import PartyType


class PartyRef(BaseModel):
    """A responsible party as submitted by a client.

    ``id`` is optional: ``agency`` forbids it, ``company`` treats NULL as *the record's own
    company*, and ``employee`` / ``contact`` require it (validated in the service).
    """

    type: PartyType
    id: uuid.UUID | None = None


class PartyReadRef(BaseModel):
    """A responsible party as returned to a client, with its resolved display label."""

    type: PartyType
    id: uuid.UUID | None = None
    label: str = ""
