"""The Ads policy and the decisions log, as services. Business-licensed — see LICENSE.

Two services and one rule between them: :class:`GoogleAdsPolicyService` answers *what the rules
are*, :class:`GoogleAdsDecisionService` answers *what has already been settled*, and neither ever
reaches Google. Both are read by the write surface before a mutation and by the curated MCP tools
before a proposal — which is the point of Phase 4: the tools stop being a data pipe the moment
they can say "we decided about this in March, and here is why".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, or_

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.timezone import org_today
from app.errors import AppError
from app.integrations.google_ads import policy as policy_rules
from app.integrations.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsDecision,
    GoogleAdsPolicy,
)

_POLICY_ENTITY = "google_ads_policy"

#: What an edit of a policy records. The prose is deliberately absent: an activity row saying
#: "steering changed" is the fact worth attributing, and copying two paragraphs of a tenant's own
#: writing into a second table on every save is storage, not an audit trail.
_TRACKED = (
    "protected_terms",
    "banned_phrases",
    "always_exclude",
    "max_daily_budget",
    "max_budget_increase_pct",
    "max_cpc",
    "waste_min_cost",
    "waste_min_clicks",
)

#: Editable fields, as an allow-list rather than a sweep over the model's columns. A sweep picks
#: up whatever the next migration adds — including ``account_id``, which would let a save move a
#: policy onto somebody else's account.
WRITABLE = (
    *_TRACKED,
    "steering",
    "ad_copy_rules",
)


class GoogleAdsPolicyService:
    """The house policy and each account's, and the one function that folds them."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.activity = ActivityService(ctx)

    def _select(self) -> Select:
        return self.ctx.repo(GoogleAdsPolicy).scoped_select()

    async def rows_for(self, account_id: uuid.UUID | None) -> tuple[Any, Any]:
        """``(account row, house row)`` in **one** statement.

        One statement rather than two because this runs on every write and on every tool call
        that proposes something, and docs/PERFORMANCE.md's rule about a read that is one query at
        three rows and one-per-row at three hundred applies just as much to a read that is two
        queries every time instead of one.
        """
        stmt = self._select().where(
            or_(
                GoogleAdsPolicy.account_id.is_(None),
                GoogleAdsPolicy.account_id == account_id,
            )
            if account_id is not None
            else GoogleAdsPolicy.account_id.is_(None)
        )
        rows = list((await self.ctx.session.scalars(stmt)).all())
        own = next((r for r in rows if r.account_id is not None), None)
        house = next((r for r in rows if r.account_id is None), None)
        return own, house

    async def resolve(self, account_id: uuid.UUID | None) -> policy_rules.AdsPolicy:
        """The effective policy: built-in, then the house row, then the account's."""
        own, house = await self.rows_for(account_id)
        return policy_rules.resolve(own, house)

    async def get(self, account_id: uuid.UUID | None) -> GoogleAdsPolicy | None:
        own, house = await self.rows_for(account_id)
        return house if account_id is None else own

    async def save(
        self, account_id: uuid.UUID | None, values: dict[str, Any]
    ) -> GoogleAdsPolicy:
        """Upsert one policy row. ``account_id=None`` edits the agency's house policy.

        ``values`` carries only the keys the caller actually set (CLAUDE.md §18): an absent key
        leaves the stored value alone, and an explicit ``None`` on a scalar clears it back to
        *inherit*, which for an account row means the house value and for the house row means the
        built-in. Both are real states and a payload alone cannot tell them apart, which is why
        the router passes ``model_fields_set`` rather than the model.
        """
        self.ctx.require("google_ads.policy.manage")
        if account_id is not None:
            # 404 through the account's own scoped fetch, so a policy cannot be written for an
            # account outside this caller's tenant or company horizon — and the caller is never
            # told the difference between "no such account" and "not yours" (§15).
            await self._account(account_id)
        row = await self.get(account_id)
        created = row is None
        if row is None:
            row = GoogleAdsPolicy(org_id=self.ctx.org.id, account_id=account_id)
            self.ctx.session.add(row)
        before = snapshot(row, _TRACKED)
        for key, value in values.items():
            if key not in WRITABLE:
                continue
            if key in {"protected_terms", "banned_phrases", "always_exclude"}:
                setattr(row, key, _clean_terms(value))
            elif key in {"steering", "ad_copy_rules"}:
                setattr(row, key, str(value or "").strip())
            else:
                setattr(row, key, value)
        await self.ctx.session.flush()
        if created:
            await self.activity.record_created(
                _POLICY_ENTITY, row.id, {"account_id": str(account_id or "")}
            )
        else:
            await self.activity.record_update(
                _POLICY_ENTITY, row.id, before, snapshot(row, _TRACKED)
            )
        return row

    async def _account(self, account_id: uuid.UUID) -> GoogleAdsAccount:
        row = await self.ctx.session.scalar(
            self.ctx.repo(GoogleAdsAccount)
            .scoped_select()
            .where(GoogleAdsAccount.id == account_id)
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row


def _clean_terms(raw: Any) -> list[str]:
    """A term list as it is stored: normalised, deduplicated, capped.

    Normalised **on the way in** as well as on the way out, because this column is written by an
    agent holding ``policy.manage`` and read on every mutation. A list that is only cleaned when
    read is a list whose stored form nobody ever sees, and the first thing that breaks is a
    tenant staring at "Beugel " in the box asking why it does not match.
    """
    terms, _ = policy_rules._terms(raw or ())
    return list(terms)


@dataclass(frozen=True)
class StandingDecision:
    """The decision currently in force about one subject."""

    subject: str
    subject_key: str
    scope: str
    decision: str
    reason: str
    decided_on: Any
    decided_by: str


class GoogleAdsDecisionService:
    """Append-only, latest-wins, and the reason a proposal is not made twice."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    def _select(self) -> Select:
        return self.ctx.repo(GoogleAdsDecision).scoped_select()

    async def _standing_filter(self, stmt: Select) -> Select:
        """Only decisions that still stand: not withdrawn, not expired.

        Expiry is resolved against the **org's** calendar rather than UTC (§8): a decision set to
        lapse on the 31st lapses at the tenant's midnight, and a cron reasoning in UTC would let
        it stand or drop it an hour early depending on the season.
        """
        today = await org_today(self.ctx.session, self.ctx.org.id)
        return stmt.where(
            GoogleAdsDecision.withdrawn_at.is_(None),
            or_(
                GoogleAdsDecision.expires_on.is_(None),
                GoogleAdsDecision.expires_on >= today,
            ),
        )

    async def standing(
        self, account_id: uuid.UUID, *, subject_type: str | None = None
    ) -> dict[str, StandingDecision]:
        """Every subject with a decision in force, keyed by ``subject_key``.

        Latest wins, resolved here rather than in SQL: the set is small (an account's decisions
        are counted in hundreds, not millions), and a `DISTINCT ON` would put the tie-breaking
        rule somewhere no test could read it. Newest **last** in the iteration, so the assignment
        overwrites — the same shape as reading a log forward.
        """
        stmt = (await self._standing_filter(self._select())).where(
            GoogleAdsDecision.account_id == account_id
        )
        if subject_type:
            stmt = stmt.where(GoogleAdsDecision.subject_type == subject_type)
        stmt = stmt.order_by(GoogleAdsDecision.created_at.asc())
        out: dict[str, StandingDecision] = {}
        for row in (await self.ctx.session.scalars(stmt)).all():
            out[_key(row.subject_key, row.scope)] = StandingDecision(
                subject=row.subject,
                subject_key=row.subject_key,
                scope=row.scope,
                decision=row.decision,
                reason=row.reason,
                decided_on=row.created_at,
                decided_by=row.decided_by_name,
            )
        return out

    async def page(
        self,
        account_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        subject_type: str | None = None,
        decision: str | None = None,
        include_withdrawn: bool = False,
        count: bool = True,
    ) -> tuple[list[GoogleAdsDecision], int | None]:
        """One page of the log, newest first, with an optional total.

        ``count=False`` skips the count query for a caller that does not draw one
        (docs/PERFORMANCE.md), and the total rides ``scoped_count_select`` rather than a
        hand-built ``select(count())`` — #285's second failure mode is a count that says "2" over
        a list of one.
        """
        stmt = self._select().where(GoogleAdsDecision.account_id == account_id)
        if subject_type:
            stmt = stmt.where(GoogleAdsDecision.subject_type == subject_type)
        if decision:
            stmt = stmt.where(GoogleAdsDecision.decision == decision)
        if not include_withdrawn:
            stmt = stmt.where(GoogleAdsDecision.withdrawn_at.is_(None))
        total: int | None = None
        if count:
            counter = self.ctx.repo(GoogleAdsDecision).scoped_count_select()
            counter = counter.where(GoogleAdsDecision.account_id == account_id)
            if subject_type:
                counter = counter.where(GoogleAdsDecision.subject_type == subject_type)
            if decision:
                counter = counter.where(GoogleAdsDecision.decision == decision)
            if not include_withdrawn:
                counter = counter.where(GoogleAdsDecision.withdrawn_at.is_(None))
            total = int(await self.ctx.session.scalar(counter) or 0)
        stmt = stmt.order_by(GoogleAdsDecision.created_at.desc()).limit(limit).offset(offset)
        return list((await self.ctx.session.scalars(stmt)).all()), total

    async def record(
        self,
        account_id: uuid.UUID,
        *,
        subject_type: str,
        subject: str,
        decision: str,
        scope: str = "account",
        reason: str = "",
        applied: bool = False,
        source: str = "manual",
        payload: dict[str, Any] | None = None,
        expires_on: Any = None,
    ) -> GoogleAdsDecision | None:
        """Append one decision, unless the same one already stands.

        The pre-check is a courtesy, not a guarantee, and that is the deliberate inversion of the
        payments rule (CLAUDE.md §10). There, an idempotency guarantee in application code loses
        a race the database would have won — and it matters, because a duplicate
        ``InvoicePayment`` is money counted twice. Here the loser of that race writes a second
        history row saying what the first one said. A unique index would instead turn an
        agent's ordinary second call into a 500, and cost the ability to record "excluded in
        March, un-excluded in June, excluded again in September", which is a true sequence.

        Returns ``None`` when nothing was appended, so a caller can report "already decided"
        rather than claiming it wrote something.
        """
        key = policy_rules.normalise(subject)[:255]
        if not key:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"subject": "errors.google_ads_decision_subject_required"},
            )
        current = (await self.standing(account_id, subject_type=subject_type)).get(
            _key(key, scope)
        )
        if current is not None and current.decision == decision:
            return None
        actor, impersonator = _actor(self.ctx)
        row = GoogleAdsDecision(
            org_id=self.ctx.org.id,
            account_id=account_id,
            subject_type=subject_type,
            subject=str(subject)[:255],
            subject_key=key,
            scope=scope[:64] or "account",
            decision=decision,
            reason=str(reason or "").strip(),
            applied=bool(applied),
            source=source,
            payload=payload or {},
            decided_by_user_id=getattr(getattr(self.ctx, "user", None), "id", None),
            decided_by_name=actor,
            impersonator_name=impersonator,
            expires_on=expires_on,
        )
        self.ctx.session.add(row)
        await self.ctx.session.flush()
        return row

    async def withdraw(self, account_id: uuid.UUID, decision_id: uuid.UUID) -> GoogleAdsDecision:
        """Unsay a decision without erasing it.

        A delete would take the reason with it, and "we decided this and then changed our minds"
        is the sentence the log exists to be able to make.
        """
        self.ctx.require("google_ads.policy.manage")
        row = await self.ctx.session.scalar(
            self._select().where(
                GoogleAdsDecision.id == decision_id,
                GoogleAdsDecision.account_id == account_id,
            )
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        if row.withdrawn_at is None:
            actor, _impersonator = _actor(self.ctx)
            row.withdrawn_at = datetime.now(UTC)
            row.withdrawn_by_name = actor
            await self.ctx.session.flush()
        return row


def _key(subject_key: str, scope: str) -> str:
    """A decision's identity: the subject **and** where it applies.

    Both, because keeping a term in the brand campaign and excluding it in the generic one is an
    ordinary arrangement rather than a contradiction, and a lookup keyed on the subject alone
    would make the second decision overwrite the first.
    """
    return f"{scope or 'account'}\x1f{subject_key}"


def _name(user: Any) -> str:
    return (getattr(user, "full_name", "") or getattr(user, "email", "") or "").strip()


def _actor(ctx: Any) -> tuple[str, str | None]:
    """``(who, through whom)`` — snapshotted, never joined live (§16).

    A write made while impersonating runs *as* the target, so an actor alone would record the
    client's name for something the agency did (#296). The impersonator is the fact worth having.

    ``getattr`` throughout because a cron tick's ``SystemContext`` carries neither field, and a
    decision recorded by the nightly run legitimately has no human behind it.
    """
    user = None if getattr(ctx, "is_system", False) else getattr(ctx, "user", None)
    impersonator = getattr(ctx, "impersonated_by", None)
    return _name(user), (_name(impersonator) or None if impersonator else None)
