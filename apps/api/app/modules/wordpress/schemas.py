"""Pydantic schemas for the wordpress module (docs/WORDPRESS.md).

Two conventions, both borrowed from ``cloudflare`` because they were right there:

* **The application password is write-only.** It goes in on create/update and never comes back
  out — not in a read model, not in the OpenAPI spec, not masked. ``password_configured`` is
  the only thing a client learns about it.
* **A refusal names its problem as a key, not a sentence.** ``last_error`` and the
  ``capability_errors`` *slugs* are stable machine strings the web resolves to
  ``wordpress.issue.*`` messages, so the API never picks a locale for someone else's screen
  (CLAUDE.md §8). The site's own text rides alongside as evidence, untranslated on purpose:
  it is a quote, and translating a quote is how a diagnosis stops matching the log line an
  admin is looking at.

Names are prefixed (``WordPressSiteRead``, not ``SiteRead``): a generic Pydantic name makes
FastAPI qualify *both* colliding modules' components in the generated client.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.wordpress.client import normalise_base_url


class WordPressSiteRead(BaseModel):
    """A connected WordPress. Never carries the application password."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    website_id: uuid.UUID
    base_url: str
    username: str
    active: bool
    status: str
    #: A stable key (``wordpress.issue.*``), never a sentence.
    last_error: str | None = None

    #: Observed at probe time — the keys of ``client.CAPABILITIES``. A **missing** key means
    #: "not probed", which is a different screen from ``False`` ("probed and refused").
    capabilities: dict[str, bool] = Field(default_factory=dict)
    #: Why a probe answered no, keyed the same way. Only ever holds keys whose capability is
    #: ``False`` — a ✗ with no explanation is the one state an admin cannot act on.
    capability_errors: dict[str, str] = Field(default_factory=dict)
    #: NULL means nobody has ever looked, which an empty ``capabilities`` cannot say on its own.
    capabilities_checked_at: datetime | None = None

    mcp_server_path: str | None = None
    rankmath_version: str | None = None
    #: Whether this Rank Math is new enough to have AI Visibility at all (≥ 1.0.273). Resolved
    #: server-side so the panel never re-implements a version comparison in two languages.
    rankmath_ai_visibility: bool = False

    last_verified_at: datetime | None = None
    #: Whether a password is stored at all. The password itself never leaves the server.
    password_configured: bool = True
    created_at: datetime
    updated_at: datetime


class WordPressSiteCreate(BaseModel):
    website_id: uuid.UUID
    #: Absolute site URL, subpath preserved. Normalised on the way in so ``https://klant.nl/``
    #: and ``https://klant.nl`` cannot become two credentials for one site.
    base_url: str = Field(min_length=1, max_length=500)
    username: str = Field(min_length=1, max_length=255)
    #: A WordPress **Application Password** (Gebruikers → Profiel → Toepassingswachtwoorden),
    #: never the account password. WordPress shows it space-separated in groups of four; both
    #: forms authenticate, so it is accepted as displayed.
    app_password: str = Field(min_length=8, max_length=512)
    active: bool = True

    @field_validator("base_url")
    @classmethod
    def _normalise(cls, value: str) -> str:
        normalised = normalise_base_url(value)
        if not normalised:
            raise ValueError("errors.invalid_url")
        return normalised


class WordPressSiteUpdate(BaseModel):
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    username: str | None = Field(default=None, min_length=1, max_length=255)
    #: Omit to keep the stored password; send a new one to rotate. Never send an empty string
    #: to clear it — a connected site without a credential is not a state this module has a use
    #: for, and "disconnect" is a DELETE.
    app_password: str | None = Field(default=None, min_length=8, max_length=512)
    active: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _normalise(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = normalise_base_url(value)
        if not normalised:
            raise ValueError("errors.invalid_url")
        return normalised


class WordPressVerifyResult(BaseModel):
    """What a verify learned.

    ``ok`` is **not** "every capability is true" — a site with no Rank Math is a perfectly good
    WordPress connection, and a probe that reported it as broken would be the health-check
    mistake this module exists to avoid. ``ok`` is "at least one probe got through", i.e. the
    credential is real; the capability map is where the nuance lives.
    """

    ok: bool
    status: str
    capabilities: dict[str, bool] = Field(default_factory=dict)
    capability_errors: dict[str, str] = Field(default_factory=dict)
    rankmath_version: str | None = None
    rankmath_ai_visibility: bool = False
    mcp_server_path: str | None = None
    #: How many Rank Math brands this site tracks, where AI Visibility answered. ``None`` where
    #: it did not — zero brands and no Rank Math are different sentences.
    brand_count: int | None = None
    #: A stable ``wordpress.issue.*`` key where the whole credential was refused.
    error: str | None = None


class WordPressBrand(BaseModel):
    """One tracked brand, as the marketing picker and the panel need it.

    A hand-written subset of Rank Math's row rather than a passthrough: the upstream shape is a
    third party's and carries fields (``created_at``, cache bookkeeping) that would become our
    contract the moment they appeared in the spec.
    """

    id: str
    name: str
    url: str = ""
    locale: str | None = None
    status: str = "active"
    score: float | None = None
    rank: float | None = None
    avg_sentiment: float | None = None
    mentions: float | None = None
    citations: float | None = None
    analysis_status: str | None = None
    last_analyzed: str | None = None


def brand_from_payload(row: Any) -> WordPressBrand | None:
    """One Rank Math overview row → :class:`WordPressBrand`, defensively.

    Every field is optional in practice: ``/overview`` omits ``description`` by design, and a
    brand mid-analysis carries nulls where numbers will be. A row with no id is unusable and is
    dropped rather than guessed at.
    """
    if not isinstance(row, dict):
        return None
    brand_id = row.get("id")
    if not isinstance(brand_id, str) or not brand_id:
        return None

    def num(key: str) -> float | None:
        value = row.get(key)
        if isinstance(value, bool) or value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def txt(key: str) -> str | None:
        value = row.get(key)
        return value if isinstance(value, str) and value else None

    return WordPressBrand(
        id=brand_id,
        name=txt("name") or brand_id,
        url=txt("url") or "",
        locale=txt("locale"),
        status=txt("status") or "active",
        score=num("score"),
        rank=num("rank"),
        avg_sentiment=num("avg_sentiment"),
        mentions=num("mentions"),
        citations=num("citations"),
        analysis_status=txt("analysis_status"),
        last_analyzed=txt("last_analyzed"),
    )
