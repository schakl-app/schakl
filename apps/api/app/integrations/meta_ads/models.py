"""The ads posture, the guardrails, and what was decided. Business-licensed — see LICENSE.

Three tables and **no mirror of Meta's objects.** A campaign, an ad set and an ad live at Meta
and are read from Meta; a copy here would be a second opinion about what is running and what it
costs. The ad *account* is a ``meta_assets`` row owned by the ``meta`` integration — this one
requires that one and holds nothing about credentials at all.

:class:`MetaAdsSettings` is the kill switch for every call that changes something in an ad
account, separate from publishing's: an owner who watched an agent do something surprising to
a budget wants to stop *that* without stopping Saturday's posts.

:class:`MetaAdsPolicy` is the guardrail, in layers that must not fuse: a row whose
``asset_id IS NULL`` is the agency's own house policy, a row naming an account is that
account's diff over it, and the built-in (``policy.BUILT_IN``) sits under both.

:class:`MetaAdsDecision` is the one kind of fact that exists nowhere else: *who* changed this
budget, *why*, through which key — and what it was before. Meta records that a budget is
€50; it records nowhere that an agency raised it from €30 on a Tuesday because the client
asked for it on the phone.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    column,
    false,
    select,
    table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class MetaAdsSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    __tablename__ = "meta_ads_settings"
    __entity_type__ = "meta_ads_settings"
    __activity_read_permission__ = "meta_ads.policy.manage"

    __table_args__ = (UniqueConstraint("org_id", name="uq_meta_ads_settings_org"),)

    writes_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


def _asset_horizon_clause(asset_column, *, house_visible: bool = False):
    """The company horizon for a table whose client link is its **ad account's** (#285)."""

    def clause(scope):
        assets = table("meta_assets", column("id"), column("company_id"))
        joined = asset_column.in_(
            select(assets.c.id).where(
                (assets.c.company_id.is_(None)) | (assets.c.company_id.in_(scope))
            )
        )
        return (asset_column.is_(None)) | joined if house_visible else joined

    return clause


class MetaAdsPolicy(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One layer of the guardrails: the house's, or one ad account's diff over it.

    ``NULL`` on any limit means *inherit* — the house value for an account row, the built-in
    for the house row — never "no limit". A limit is removed by stating a larger one.
    """

    __tablename__ = "meta_ads_policies"
    __entity_type__ = "meta_ads_policy"
    __activity_read_permission__ = "meta_ads.policy.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "asset_id", name="uq_meta_ads_policies_asset"),
        # NULLs are distinct inside a unique constraint, so the one above alone permits two
        # house policies. The partial index is what makes "the house policy" one row.
        Index(
            "uq_meta_ads_policies_house",
            "org_id",
            unique=True,
            postgresql_where=text("asset_id IS NULL"),
        ),
    )

    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meta_assets.id", ondelete="CASCADE"), nullable=True
    )
    #: In the account's own currency, in **minor units** (cents) — the unit Meta takes, so a
    #: ceiling and the budget it is compared with are never a factor of a hundred apart.
    max_daily_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_lifetime_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: How much one change may raise a budget by, as a fraction: ``1.0`` is "may at most
    #: double". The one limit with a built-in, because it needs no local knowledge.
    max_budget_increase: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: Words an ad's text may not contain. Checked before the creative is made.
    banned_phrases: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: The two names the EU's Digital Services Act has every ad set targeting the EU state:
    #: who the ad is for, and who pays for it. Decided here where the account's own defaults
    #: at Meta are empty or wrong.
    dsa_beneficiary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dsa_payor: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Tenant prose: what this account is for and how it should be run. When it ever feeds a
    #: model it travels as a labelled field, never concatenated into a prompt (#300).
    steering: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    @declared_attr.directive
    def __company_horizon_clause__(cls):  # noqa: N805
        return _asset_horizon_clause(cls.asset_id, house_visible=True)

    @classmethod
    def __portal_horizon_clause__(cls, scope):  # noqa: ANN001, ANN206
        return false()


class DecisionSubject(StrEnum):
    CAMPAIGN = "campaign"
    AD_SET = "ad_set"
    AD = "ad"
    CREATIVE = "creative"
    IMAGE = "image"


class DecisionKind(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    BUDGET_CHANGED = "budget_changed"
    #: ``PAUSED`` → ``ACTIVE``: the act that spends.
    ACTIVATED = "activated"
    PAUSED = "paused"
    ARCHIVED = "archived"
    #: A judgement with no act behind it — "looked at this campaign, left it running" — which
    #: leaves no trace at Meta and is therefore the entry only this table can hold (#318).
    KEPT = "kept"


class MetaAdsDecision(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only: what was changed or decided, by whom, and why.

    Not :class:`AuditableMixin` — this table *is* the trail for the acts it records, with its
    own snapshotted actor (§16). There is deliberately no unique index: a duplicate history
    row is a duplicate history row, where a unique index would cost a 500 on an ordinary
    second call (``google_ads_decisions``' reasoning).
    """

    __tablename__ = "meta_ads_decisions"
    __entity_type__ = "meta_ads_decision"

    __table_args__ = (
        Index("ix_meta_ads_decisions_recent", "org_id", "asset_id", "created_at"),
        Index("ix_meta_ads_decisions_subject", "org_id", "asset_id", "subject_id", "created_at"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meta_assets.id", ondelete="CASCADE"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Meta's id of the thing the decision is about.
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: Its name when the decision was taken. Snapshotted: a campaign renamed next month must
    #: not rename what was decided about it this month.
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    #: Whether Meta was changed. A ``KEPT`` decision never is; a ``validate_only`` run records
    #: nothing at all.
    applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: What was sent and what it replaced — the amounts, the status, the fields.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: Set when the decision was taken through an impersonated session (#296).
    impersonator_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    @declared_attr.directive
    def __company_horizon_clause__(cls):  # noqa: N805
        return _asset_horizon_clause(cls.asset_id)

    @classmethod
    def __portal_horizon_clause__(cls, scope):  # noqa: ANN001, ANN206
        return false()
