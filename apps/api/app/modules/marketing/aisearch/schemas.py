"""Payloads for the AI Search overview (docs/SERANKING.md)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Engine = Literal["all", "ai-overview", "ai-mode", "chatgpt", "perplexity", "gemini"]
Scope = Literal["base_domain", "domain", "url"]


class AiSearchSettingsRead(BaseModel):
    """Settings with every inheritance already applied — what a read will actually ask."""

    enabled: bool
    engines: list[Engine]
    #: SE Ranking's ``source``: the alpha-2 country whose prompt database is read.
    source: str
    scope: Scope
    target: str = ""
    brand: str = ""
    #: Units one client costs per month at these settings (800 per engine choice).
    monthly_units: int = 0


class AiSearchOrgSettingsWrite(BaseModel):
    """The house defaults. A field left out keeps what is stored."""

    enabled: bool | None = None
    engines: list[Engine] | None = None
    source: str | None = Field(default=None, min_length=2, max_length=2)
    scope: Scope | None = None


class AiSearchCompanySettingsWrite(BaseModel):
    """One client's diff over the house defaults, **posted whole**.

    Unlike the org form, ``null`` here is a value: it is how the editor says "volg de
    standaard" for that field, so the stored diff is rebuilt from exactly what was posted and
    a field the form leaves blank stops overriding.
    """

    enabled: bool | None = None
    engines: list[Engine] | None = None
    source: str | None = Field(default=None, max_length=2)
    scope: Scope | None = None
    target: str | None = Field(default=None, max_length=512)
    brand: str | None = Field(default=None, max_length=255)


class AiSearchMetric(BaseModel):
    """One headline figure for the month, against the month before."""

    key: Literal[
        "brand_presence", "link_presence", "average_position", "ai_opportunity_traffic"
    ]
    current: float | None = None
    previous: float | None = None
    change_absolute: float | None = None
    change_percent: float | None = None
    #: Where the number went — ``up`` / ``down`` / ``flat``.
    direction: str | None = None
    #: What that means — ``good`` / ``bad`` / ``neutral``. A position that fell is good.
    verdict: str | None = None


class AiSearchPoint(BaseModel):
    month: str
    value: float


class AiSearchEngineBlock(BaseModel):
    """One engine choice's answer. ``all`` is SE Ranking's own cross-engine aggregate."""

    engine: Engine
    #: ``ok`` · ``fetching`` (another request is reading it right now) · ``denied`` (the key
    #: lacks Data API access) · ``insufficient`` (the plan's units are spent) · ``failed`` ·
    #: ``missing`` (nothing stored and this reader may not fetch).
    status: str
    #: The month that was asked about — always the last complete one.
    period_month: date
    #: The month the figures cover, by SE Ranking's own series. Earlier than ``period_month``
    #: while the vendor has not published it, or when an older stored month is shown instead.
    data_month: date | None = None
    #: The month the figures are compared with: the one before ``data_month``.
    compare_month: date | None = None
    metrics: list[AiSearchMetric] = Field(default_factory=list)
    series: dict[str, list[AiSearchPoint]] = Field(default_factory=dict)
    fetched_at: datetime | None = None
    #: The figures were re-read from the monthly series because SE Ranking's newest point was
    #: the month still running; brand presence and opportunity traffic then have no comparison.
    realigned: bool = False
    #: SE Ranking holds no AI answers for this target in this country database — a state the
    #: screen says in words, never four empty tiles (``metrics`` is then empty).
    no_data: bool = False


class AiSearchOverview(BaseModel):
    company_id: uuid.UUID
    #: ``off`` · ``no_key`` · ``no_target`` · ``ready``.
    state: str
    period_month: date
    settings: AiSearchSettingsRead
    #: Where the target came from: ``setting`` · ``seranking`` (the linked project's own
    #: domain) · ``website`` (the client's first website).
    target_origin: str | None = None
    #: The brand whose mentions were counted, and how it was arrived at: ``setting`` ·
    #: ``discovered`` (SE Ranking's own attribution, read once) · ``vendor`` (left to SE
    #: Ranking, because its lookup named nothing).
    brand: str = ""
    brand_origin: str = "vendor"
    #: False when the discovered brand shares nothing with the client's name or domain — a
    #: hint to type the right one, never a refusal.
    brand_fits: bool = True
    discovered_brands: list[str] = Field(default_factory=list)
    engines: list[AiSearchEngineBlock] = Field(default_factory=list)
    can_manage: bool = False
    # --- managers only (withheld from a reader who cannot act on it, and from a client) ---- #
    #: This client's own stored diff, ``None`` when everything is inherited.
    own: dict | None = None
    house: AiSearchSettingsRead | None = None
    #: What SE Ranking said was left on the plan, when this request had reason to ask.
    units_left: int | None = None
    #: A refusal (``denied`` / ``insufficient`` / ``failed``) this request met while re-reading
    #: a month that was already stored — the stored figures were kept, and this says why they
    #: are not newer. Only on the response to the request that met it.
    notice: str | None = None


class SeRankingCheck(BaseModel):
    """Which of SE Ranking's two APIs the stored key(s) reach (docs/SERANKING.md §2)."""

    configured: bool
    #: ``ok`` · ``denied`` · ``failed`` · ``not_configured``.
    project_api: str
    data_api: str
    #: Which key answered for the Data API: the agency's separate one, or the shared key.
    data_api_key: Literal["own", "shared"] = "shared"
    subscription_status: str = ""
    units_limit: int | None = None
    units_left: int | None = None
    expires_at: str = ""
    #: How many clients the house setting would read each month, and what that costs.
    enabled_clients: int = 0
    monthly_units: int = 0


class BrandLookupRequest(BaseModel):
    """What to look the brand up *for*. Both optional: left out, the client's stored (or
    derived) target and country are used — but the editor sends what is in its boxes, so a
    manager correcting a domain looks up the domain they typed, not the one being replaced."""

    target: str | None = Field(default=None, max_length=512)
    source: str | None = Field(default=None, max_length=2)


class BrandLookup(BaseModel):
    target: str
    brands: list[str]
    units: int
