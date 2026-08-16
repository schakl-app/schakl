"""The write surface: what it takes to change somebody else's live advertising account.

Business-licensed — see LICENSE.

Every method here is the same six steps, in this order, and the order is the design:

1. **resolve the account** through the tenant-scoped repository — 404, never 403 (§15);
2. **the kill switch** (``writes_enabled``), which is a second condition and not the permission:
   the permission decides *who*, this decides *whether*, and an owner who has just watched an
   agent do something surprising needs one switch rather than eight role grants;
3. **the policy**, resolved once (:mod:`~app.integrations.google_ads.policy`);
4. **build the operations**, and let the policy refuse what it must — see the split below;
5. **one ``:mutate``** inside ``open_client``, with the pooled database connection released;
6. **record**: a decision row per applied operation, and one activity line.

## A call-level refusal raises; a row-level one is reported

CLAUDE.md §18's rule, and it lands here exactly: *a bad shared value is the caller's; a bad row is
the row's*. A daily budget over the policy's ceiling **is** the call — there is one budget and
refusing it is the whole answer, so it is a 422 naming the field and the limit (#305: show the
constraint working, rather than removing the control). A protected term inside a batch of twelve
exclusions is one row: refusing all twelve because the guard did its job on one of them punishes
the caller for something that worked. So it is **skipped and reported**, with the protected term
it would have blocked named in the report.

## Partial failure is a property of the route, not of the batch size

The batch routes (keywords, negatives) always send ``partialFailure: true``; the single-resource
ones never do. Deciding it from the runtime operation count instead would mean an agent excluding
one term gets a raised error and an agent excluding two gets a per-row report — the same tool
answering in two shapes depending on how much work it was given, which is the sort of thing that
is discovered in production by an agent's retry loop.

## Nothing raises after Google has been changed

``ctx.release_db()`` commits on entry, and anything written *after* the client block is rolled
back by ``require_context`` if an exception escapes it. A mutation that Google applied and whose
decision row was rolled back is the worst state available here: the account changed and nothing
says so. So once the mutate has returned, every remaining problem becomes a warning on the
outcome and the outcome is **returned**. The same shape ``GoogleAdsService.verify`` uses, for the
same reason.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.activity import ActivityService
from app.core.googleads import AdsClient, partial_failures
from app.errors import AppError
from app.integrations.google_ads import mutations as ops
from app.integrations.google_ads import policy as policy_rules
from app.integrations.google_ads.decisions import GoogleAdsDecisionService, GoogleAdsPolicyService
from app.integrations.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsDecisionKind,
    GoogleAdsDecisionSubject,
)
from app.integrations.google_ads.service import GoogleAdsService

_ENTITY = "google_ads_account"


@dataclass
class Recordable:
    """What to write to the decisions log if operation ``index`` was applied."""

    subject_type: str
    subject: str
    decision: str
    scope: str = "account"
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class MutationOutcome:
    """One mutate, described so a caller never has to guess what happened.

    ``requested`` and ``applied`` are separate numbers on purpose. They differ when the policy
    skipped a row, when Google refused one inside a partial-failure batch, and — always — when
    ``validate_only`` is set, where ``applied`` is zero because nothing was.
    """

    resource: str
    validate_only: bool
    requested: int = 0
    applied: int = 0
    #: One per operation sent: ``{index, ok, resource_name, error_code, message}``.
    results: list[dict[str, Any]] = field(default_factory=list)
    #: One per operation the **policy** refused before Google saw it:
    #: ``{subject, reason, blocks, limit}``. Never merged into ``results`` — "we did not ask" and
    #: "Google said no" are different sentences and only one of them is fixable in Google.
    skipped: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class GoogleAdsWriteService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.accounts = GoogleAdsService(ctx)
        self.policies = GoogleAdsPolicyService(ctx)
        self.decisions = GoogleAdsDecisionService(ctx)
        self.activity = ActivityService(ctx)

    # --- the shared spine -------------------------------------------------------------------- #

    async def _prepare(
        self, account_id: uuid.UUID, permission: str
    ) -> tuple[GoogleAdsAccount, policy_rules.AdsPolicy]:
        """Everything that must be true before a single byte goes to Google.

        Both DB reads happen here rather than lazily inside the client block, because the pooled
        connection is checked back in for the duration of that block and a query inside it would
        re-check one out with no RLS GUC bound — failing **closed** (zero rows) rather than
        erroring, which is the most confusing failure this codebase has.
        """
        self.ctx.require(permission)
        account = await self.accounts.get_account(account_id)
        await self.accounts.require_writes_enabled()
        policy = await self.policies.resolve(account_id)
        return account, policy

    async def _mutate(
        self,
        account: GoogleAdsAccount,
        resource: str,
        operations: list[dict[str, Any]],
        *,
        validate_only: bool,
        partial_failure: bool,
        tool: str,
    ) -> MutationOutcome:
        """One ``:mutate``, with the pooled database connection released for its duration."""
        ops.check_resource(resource)
        if not operations:
            return MutationOutcome(resource=resource, validate_only=validate_only)
        if len(operations) > ops.MAX_OPERATIONS:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"operations": "errors.google_ads_too_many_operations"},
            )
        async with self.accounts.open_client(account_id=account.id, tool=tool) as (client, _a):
            payload = await client.mutate(
                account.customer_id,
                resource,
                operations,
                validate_only=validate_only,
                partial_failure=partial_failure,
                context=tool,
            )
        return self._read_outcome(resource, payload, operations, validate_only)

    def _read_outcome(
        self,
        resource: str,
        payload: dict[str, Any],
        operations: list[dict[str, Any]],
        validate_only: bool,
    ) -> MutationOutcome:
        """Google's answer, per operation.

        Two shapes are folded into one here. Without partial failure a 200 means every operation
        landed. With it, ``results`` still carries one slot per operation and the refused ones are
        **empty objects** — so the only link from a slot to its reason is the index inside
        ``partialFailureError``, which is a bare ``google.rpc.Status`` that the ordinary error
        classifier never sees (it walks ``payload["error"]``, and this response is a 200).
        """
        failures = {
            failure.index: failure.error
            for failure in partial_failures(payload)
            if failure.index is not None
        }
        unattributed = [f.error for f in partial_failures(payload) if f.index is None]
        results: list[dict[str, Any]] = []
        raw = payload.get("results") or []
        applied = 0
        for index in range(len(operations)):
            error = failures.get(index)
            slot = raw[index] if index < len(raw) and isinstance(raw[index], dict) else {}
            resource_name = str(slot.get("resourceName") or "") or None
            ok = error is None and (validate_only or resource_name is not None)
            if ok and not validate_only:
                applied += 1
            results.append(
                {
                    "index": index,
                    "ok": ok,
                    "resource_name": resource_name,
                    "error_code": error.error_code if error else None,
                    # Google's own sentence, already scrubbed of credentials by the classifier.
                    # It is provider text, so it never becomes an i18n key (§9) — it lives beside
                    # the row where somebody can act on it.
                    "message": str(error) if error else None,
                }
            )
        warnings: list[str] = []
        if unattributed:
            # Google refused something and gave no path back to which operation. Saying so is the
            # only honest answer; pinning it on operation 0 would be a confident wrong one.
            warnings.append("google_ads.warning.unattributed_failure")
        if validate_only:
            warnings.append("google_ads.warning.validated_not_applied")
        return MutationOutcome(
            resource=resource,
            validate_only=validate_only,
            requested=len(operations),
            applied=applied,
            results=results,
            warnings=warnings,
        )

    async def _record(
        self,
        account: GoogleAdsAccount,
        outcome: MutationOutcome,
        recordables: list[Recordable | None],
        *,
        action: str,
    ) -> None:
        """A decision row per applied operation, and one activity line for the act.

        Nothing is recorded for a ``validate_only`` run: nothing happened, and a log that says
        otherwise is worse than no log. Nothing is recorded for a refused operation either — the
        decision is what was *done*.
        """
        if outcome.validate_only or not outcome.applied:
            return
        for result in outcome.results:
            if not result.get("ok"):
                continue
            index = int(result["index"])
            item = recordables[index] if index < len(recordables) else None
            if item is None:
                continue
            await self.decisions.record(
                account.id,
                subject_type=item.subject_type,
                subject=item.subject,
                decision=item.decision,
                scope=item.scope,
                reason=item.reason,
                applied=True,
                source="write",
                payload={**item.payload, "resource_name": result.get("resource_name")},
            )
        await self.activity.record(
            _ENTITY,
            account.id,
            action,
            {
                "resource": outcome.resource,
                "applied": outcome.applied,
                "requested": outcome.requested,
                "skipped": len(outcome.skipped),
            },
        )

    # --- budgets ------------------------------------------------------------------------------ #

    async def create_budget(
        self, account_id: uuid.UUID, *, name: str, amount: float, shared: bool, validate_only: bool
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """A new daily budget.

        The relative ceiling cannot bound a create — there is no previous amount to be a
        percentage of — so an account whose policy sets no ``max_daily_budget`` bounds this by the
        permission alone. Said in the docstring because that docstring is the tool description an
        agent reads before spending somebody's money.
        """
        account, policy = await self._prepare(account_id, "google_ads.budget.write")
        _refuse(policy_rules.budget_refusal(policy, amount=amount, previous=None))
        outcome = await self._mutate(
            account,
            "campaignBudgets",
            [ops.operation_create(ops.budget_create(name=name, amount=amount, shared=shared))],
            validate_only=validate_only,
            partial_failure=False,
            tool="budget_create",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.BUDGET.value,
                    name,
                    GoogleAdsDecisionKind.CREATED.value,
                    payload={"amount": amount, "shared": shared},
                )
            ],
            action="google_ads.budget_created",
        )
        return outcome, account

    async def update_budget(
        self,
        account_id: uuid.UUID,
        budget_id: str,
        *,
        amount: float | None,
        name: str | None,
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Change a daily budget — the one write where the money is already spent when it is wrong.

        The current amount is read first, because the policy's relative ceiling is a claim about a
        *change* and cannot be checked without it. That read also answers a second question worth
        surfacing: how many campaigns use this budget. A budget shared by six campaigns moves all
        six, and an agency asked to "raise the budget on the brand campaign" does not expect that —
        so it is warned about rather than refused, since editing a shared budget is a legitimate
        act somebody may well have meant.
        """
        account, policy = await self._prepare(account_id, "google_ads.budget.write")

        async def _read(client: AdsClient) -> dict[str, Any]:
            row = await client.search_one(
                account.customer_id,
                "SELECT campaign_budget.id, campaign_budget.name, "
                "campaign_budget.amount_micros, campaign_budget.reference_count "
                f"FROM campaign_budget WHERE campaign_budget.id = {int(budget_id)}",
                context="budget_current",
            )
            return (row or {}).get("campaignBudget", {}) or {}

        # The policy check needs Google's answer, so the read runs inside the client block and the
        # refusal is raised after it — before anything is mutated, which is the only ordering
        # requirement that matters.
        current = await self._peek(account, _read, tool="budget_update")
        previous = _micros(current.get("amountMicros"))
        if not current:
            raise AppError("not_found", "errors.not_found", status_code=404)
        if amount is not None:
            _refuse(policy_rules.budget_refusal(policy, amount=amount, previous=previous))

        fields: dict[str, Any] = {}
        if amount is not None:
            fields["amountMicros"] = ops.to_micros(amount)
        if name is not None:
            fields["name"] = name
        if not fields:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"amount": "errors.google_ads_nothing_to_change"},
            )
        outcome = await self._mutate(
            account,
            "campaignBudgets",
            [ops.operation_update(ops.budget_rn(account.customer_id, budget_id), fields)],
            validate_only=validate_only,
            partial_failure=False,
            tool="budget_update",
        )
        if int(current.get("referenceCount") or 0) > 1:
            outcome.warnings.append("google_ads.warning.shared_budget")
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.BUDGET.value,
                    str(current.get("name") or budget_id),
                    GoogleAdsDecisionKind.BUDGET_CHANGED.value,
                    payload={
                        "from": previous,
                        "to": amount,
                        "campaigns_affected": int(current.get("referenceCount") or 0),
                    },
                )
            ],
            action="google_ads.budget_changed",
        )
        return outcome, account

    # --- campaigns ----------------------------------------------------------------------------- #

    async def create_campaign(
        self,
        account_id: uuid.UUID,
        *,
        name: str,
        budget_id: str,
        channel: str,
        target_content_network: bool,
        validate_only: bool,
        eu_political_advertising: bool = False,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """A new campaign, **paused**, on a budget that already exists.

        It takes a ``budget_id`` rather than an amount, and that is the four-way permission split
        holding: creating the budget is somebody's decision, made with ``budget.write``, and a
        campaign route that could conjure one would make ``campaign.write`` a budget key with
        extra steps. It is also what keeps this atomic — two mutates cannot be one transaction,
        so a campaign create that failed after its budget succeeded would leave an orphan nobody
        goes looking for.
        """
        account, _policy = await self._prepare(account_id, "google_ads.campaign.write")
        outcome = await self._mutate(
            account,
            "campaigns",
            [
                ops.operation_create(
                    ops.campaign_create(
                        name=name,
                        budget_resource=ops.budget_rn(account.customer_id, budget_id),
                        channel=channel,
                        target_content_network=target_content_network,
                        eu_political_advertising=eu_political_advertising,
                    )
                )
            ],
            validate_only=validate_only,
            partial_failure=False,
            tool="campaign_create",
        )
        outcome.warnings.append("google_ads.warning.created_paused")
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.CAMPAIGN.value,
                    name,
                    GoogleAdsDecisionKind.CREATED.value,
                    payload={"budget_id": budget_id, "channel": channel},
                )
            ],
            action="google_ads.campaign_created",
        )
        return outcome, account

    async def update_campaign(
        self,
        account_id: uuid.UUID,
        campaign_id: str,
        *,
        status: str | None,
        name: str | None,
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Pause, resume, remove or rename a campaign.

        It deliberately does **not** move a campaign onto a different budget. That field lives on
        the campaign, so it looks like a campaign edit, but its effect is "this campaign now
        spends up to a different number" — a budget act reachable with ``campaign.write``, which
        is precisely the escalation the split exists to prevent. Google's own interface does it in
        two clicks for whoever holds both.
        """
        account, _policy = await self._prepare(account_id, "google_ads.campaign.write")
        fields = _status_and_name(status, name)
        outcome = await self._mutate(
            account,
            "campaigns",
            [ops.operation_update(ops.campaign_rn(account.customer_id, campaign_id), fields)],
            validate_only=validate_only,
            partial_failure=False,
            tool="campaign_update",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.CAMPAIGN.value,
                    name or campaign_id,
                    _status_decision(status),
                    scope=f"campaign:{campaign_id}",
                    payload={"status": status, "name": name},
                )
            ],
            action="google_ads.campaign_changed",
        )
        return outcome, account

    # --- ad groups ------------------------------------------------------------------------------ #

    async def create_ad_group(
        self,
        account_id: uuid.UUID,
        *,
        name: str,
        campaign_id: str,
        cpc_bid: float | None,
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """A new ad group, paused, inside an existing campaign."""
        account, policy = await self._prepare(account_id, "google_ads.campaign.write")
        _refuse(policy_rules.cpc_refusal(policy, amount=cpc_bid))
        outcome = await self._mutate(
            account,
            "adGroups",
            [
                ops.operation_create(
                    ops.ad_group_create(
                        name=name,
                        campaign_resource=ops.campaign_rn(account.customer_id, campaign_id),
                        cpc_bid=cpc_bid,
                    )
                )
            ],
            validate_only=validate_only,
            partial_failure=False,
            tool="ad_group_create",
        )
        outcome.warnings.append("google_ads.warning.created_paused")
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.AD_GROUP.value,
                    name,
                    GoogleAdsDecisionKind.CREATED.value,
                    scope=f"campaign:{campaign_id}",
                    payload={"cpc_bid": cpc_bid},
                )
            ],
            action="google_ads.ad_group_created",
        )
        return outcome, account

    async def update_ad_group(
        self,
        account_id: uuid.UUID,
        ad_group_id: str,
        *,
        status: str | None,
        name: str | None,
        cpc_bid: float | None,
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Pause, resume, remove, rename or re-bid an ad group."""
        account, policy = await self._prepare(account_id, "google_ads.campaign.write")
        _refuse(policy_rules.cpc_refusal(policy, amount=cpc_bid))
        fields = _status_and_name(status, name)
        if cpc_bid is not None:
            fields["cpcBidMicros"] = ops.to_micros(cpc_bid)
        outcome = await self._mutate(
            account,
            "adGroups",
            [ops.operation_update(ops.ad_group_rn(account.customer_id, ad_group_id), fields)],
            validate_only=validate_only,
            partial_failure=False,
            tool="ad_group_update",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.AD_GROUP.value,
                    name or ad_group_id,
                    (
                        GoogleAdsDecisionKind.BID_CHANGED.value
                        if cpc_bid is not None
                        else _status_decision(status)
                    ),
                    scope=f"ad_group:{ad_group_id}",
                    payload={"status": status, "name": name, "cpc_bid": cpc_bid},
                )
            ],
            action="google_ads.ad_group_changed",
        )
        return outcome, account

    # --- keywords ------------------------------------------------------------------------------- #

    async def add_keywords(
        self,
        account_id: uuid.UUID,
        *,
        ad_group_id: str,
        keywords: list[Any],
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Add positive keywords to one ad group.

        A keyword whose bid is above the policy's ceiling is **skipped and reported**, not raised:
        the other eleven in the batch are fine, and the report names the limit so the caller can
        resend one row rather than re-derive the whole list.
        """
        account, policy = await self._prepare(account_id, "google_ads.keyword.write")
        parent = ops.ad_group_rn(account.customer_id, ad_group_id)
        operations: list[dict[str, Any]] = []
        recordables: list[Recordable | None] = []
        skipped: list[dict[str, Any]] = []
        for item in keywords:
            refusal = policy_rules.cpc_refusal(policy, amount=item.cpc_bid)
            if refusal is not None:
                skipped.append(
                    {"subject": item.text, "reason": refusal.key, "limit": refusal.limit}
                )
                continue
            operations.append(
                ops.operation_create(
                    ops.keyword_create(
                        ad_group_resource=parent,
                        text=item.text,
                        match_type=item.match_type,
                        cpc_bid=item.cpc_bid,
                    )
                )
            )
            recordables.append(
                Recordable(
                    GoogleAdsDecisionSubject.KEYWORD.value,
                    item.text,
                    GoogleAdsDecisionKind.ADDED.value,
                    scope=f"ad_group:{ad_group_id}",
                    payload={"match_type": item.match_type, "cpc_bid": item.cpc_bid},
                )
            )
        outcome = await self._mutate(
            account,
            "adGroupCriteria",
            operations,
            validate_only=validate_only,
            partial_failure=True,
            tool="keywords_add",
        )
        outcome.skipped = skipped
        await self._record(account, outcome, recordables, action="google_ads.keywords_added")
        return outcome, account

    async def update_keyword(
        self,
        account_id: uuid.UUID,
        *,
        ad_group_id: str,
        criterion_id: str,
        status: str | None,
        cpc_bid: float | None,
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Pause, resume, remove or re-bid one keyword.

        Its **text and match type cannot be changed** — Google marks ``keyword`` and ``negative``
        immutable and answers ``CANT_UPDATE_NEGATIVE`` to the attempt. Correcting a keyword is
        remove-then-add, which is two decisions and reads as two lines in the log, because that is
        what it is.
        """
        account, policy = await self._prepare(account_id, "google_ads.keyword.write")
        _refuse(policy_rules.cpc_refusal(policy, amount=cpc_bid))
        fields: dict[str, Any] = {}
        if status:
            fields["status"] = status.strip().upper()
        if cpc_bid is not None:
            fields["cpcBidMicros"] = ops.to_micros(cpc_bid)
        if not fields:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"status": "errors.google_ads_nothing_to_change"},
            )
        outcome = await self._mutate(
            account,
            "adGroupCriteria",
            [
                ops.operation_update(
                    ops.ad_group_criterion_rn(account.customer_id, ad_group_id, criterion_id),
                    fields,
                )
            ],
            validate_only=validate_only,
            partial_failure=False,
            tool="keyword_update",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.KEYWORD.value,
                    criterion_id,
                    (
                        GoogleAdsDecisionKind.BID_CHANGED.value
                        if cpc_bid is not None
                        else _status_decision(status)
                    ),
                    scope=f"ad_group:{ad_group_id}",
                    payload={"status": status, "cpc_bid": cpc_bid},
                )
            ],
            action="google_ads.keyword_changed",
        )
        return outcome, account

    async def remove_keywords(
        self,
        account_id: uuid.UUID,
        *,
        ad_group_id: str,
        criterion_ids: list[str],
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        account, _policy = await self._prepare(account_id, "google_ads.keyword.write")
        operations = [
            ops.operation_remove(
                ops.ad_group_criterion_rn(account.customer_id, ad_group_id, criterion_id)
            )
            for criterion_id in criterion_ids
        ]
        outcome = await self._mutate(
            account,
            "adGroupCriteria",
            operations,
            validate_only=validate_only,
            partial_failure=True,
            tool="keywords_remove",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.KEYWORD.value,
                    criterion_id,
                    GoogleAdsDecisionKind.REMOVED.value,
                    scope=f"ad_group:{ad_group_id}",
                )
                for criterion_id in criterion_ids
            ],
            action="google_ads.keywords_removed",
        )
        return outcome, account

    # --- negatives ------------------------------------------------------------------------------ #

    async def add_negatives(
        self,
        account_id: uuid.UUID,
        *,
        level: str,
        parent_id: str,
        terms: list[Any],
        keep: list[Any],
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Exclude terms — and record the ones deliberately **not** excluded.

        The ``keep`` half writes nothing to Google and is the reason this list stops growing back.
        A review pass decides both halves at once: excluding eight of a hundred search terms is
        also a decision about the other ninety-two, and a log holding only the exclusions makes
        the same ninety-two candidates again next month, and the month after, until the account
        manager stops reading the list. It rides ``negative.write`` rather than
        ``policy.manage`` because a key that may exclude a term may certainly record that it chose
        not to — strictly the weaker act under the stronger key, which is the safe direction.

        A term the policy protects is skipped, and the report names the protected term it would
        have blocked. Blocking is computed the way Google matches — under the proposed negative's
        own match type — so an EXACT exclusion on "beugel kosten" is allowed while a BROAD one is
        not, and the guard keeps the credibility it needs to stay switched on.
        """
        account, policy = await self._prepare(account_id, "google_ads.negative.write")
        resource, parent_field, parent = _negative_target(level, account.customer_id, parent_id)
        scope = f"{level}:{parent_id}" if level != "shared_set" else f"shared_set:{parent_id}"

        operations: list[dict[str, Any]] = []
        recordables: list[Recordable | None] = []
        skipped: list[dict[str, Any]] = []
        for item in terms:
            blocked = policy_rules.protected_hit(policy, item.text, item.match_type)
            if blocked is not None:
                skipped.append(
                    {
                        "subject": item.text,
                        "reason": "errors.google_ads_protected_term",
                        "blocks": blocked,
                    }
                )
                continue
            if level == "shared_set":
                resource_body = ops.shared_negative_create(
                    shared_set_resource=parent, text=item.text, match_type=item.match_type
                )
            else:
                resource_body = ops.negative_keyword_create(
                    parent_field=parent_field,
                    parent_resource=parent,
                    text=item.text,
                    match_type=item.match_type,
                )
            operations.append(ops.operation_create(resource_body))
            recordables.append(
                Recordable(
                    GoogleAdsDecisionSubject.SEARCH_TERM.value,
                    item.text,
                    GoogleAdsDecisionKind.EXCLUDED.value,
                    scope=scope,
                    reason=item.reason or "",
                    payload={"match_type": item.match_type, "level": level},
                )
            )
        outcome = await self._mutate(
            account,
            resource,
            operations,
            validate_only=validate_only,
            partial_failure=True,
            tool="negatives_add",
        )
        outcome.skipped = skipped
        await self._record(account, outcome, recordables, action="google_ads.negatives_added")

        # The kept half. Recorded even on a `validate_only` run, because it *is* the decision —
        # there was never anything to validate against Google, and withholding it would mean a dry
        # run silently discarded the only half of the review that has no other home.
        for item in keep:
            recorded = await self.decisions.record(
                account.id,
                subject_type=GoogleAdsDecisionSubject.SEARCH_TERM.value,
                subject=item.text,
                decision=GoogleAdsDecisionKind.KEPT.value,
                scope=scope,
                reason=item.reason or "",
                applied=False,
                source="manual",
                expires_on=item.expires_on,
            )
            if recorded is None:
                outcome.warnings.append("google_ads.warning.decision_already_stood")
        return outcome, account

    async def remove_negatives(
        self,
        account_id: uuid.UUID,
        *,
        level: str,
        parent_id: str,
        criterion_ids: list[str],
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Take exclusions back off. The mirror of ``add_negatives``, and just as auditable."""
        account, _policy = await self._prepare(account_id, "google_ads.negative.write")
        resource, _field, _parent = _negative_target(level, account.customer_id, parent_id)
        if level == "shared_set":
            names = [
                ops.shared_criterion_rn(account.customer_id, parent_id, criterion_id)
                for criterion_id in criterion_ids
            ]
        elif level == "campaign":
            names = [
                ops.campaign_criterion_rn(account.customer_id, parent_id, criterion_id)
                for criterion_id in criterion_ids
            ]
        else:
            names = [
                ops.ad_group_criterion_rn(account.customer_id, parent_id, criterion_id)
                for criterion_id in criterion_ids
            ]
        outcome = await self._mutate(
            account,
            resource,
            [ops.operation_remove(name) for name in names],
            validate_only=validate_only,
            partial_failure=True,
            tool="negatives_remove",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.SEARCH_TERM.value,
                    criterion_id,
                    GoogleAdsDecisionKind.KEPT.value,
                    scope=f"{level}:{parent_id}",
                    reason="",
                    payload={"level": level, "undo": True},
                )
                for criterion_id in criterion_ids
            ],
            action="google_ads.negatives_removed",
        )
        return outcome, account

    async def create_negative_list(
        self,
        account_id: uuid.UUID,
        *,
        name: str,
        campaign_ids: list[str],
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """A shared negative-keyword list, optionally attached to campaigns.

        Two mutates, and they cannot be one transaction. The failure that leaves is benign and
        worth stating rather than engineering around: a list created but not attached blocks
        nothing, is visible in Google's own interface, and re-running attaches it. The reverse
        order — attach first — is not available, and a list that silently blocked traffic on a
        campaign nobody meant to touch would not be benign at all.
        """
        account, _policy = await self._prepare(account_id, "google_ads.negative.write")
        outcome = await self._mutate(
            account,
            "sharedSets",
            [ops.operation_create(ops.shared_set_create(name=name))],
            validate_only=validate_only,
            partial_failure=False,
            tool="negative_list_create",
        )
        created = next(
            (r.get("resource_name") for r in outcome.results if r.get("ok")), None
        )
        if created and campaign_ids and not validate_only:
            attach = await self._mutate(
                account,
                "campaignSharedSets",
                [
                    ops.operation_create(
                        ops.campaign_shared_set_create(
                            campaign_resource=ops.campaign_rn(account.customer_id, campaign_id),
                            shared_set_resource=created,
                        )
                    )
                    for campaign_id in campaign_ids
                ],
                validate_only=False,
                partial_failure=True,
                tool="negative_list_attach",
            )
            outcome.results.extend(attach.results)
            outcome.requested += attach.requested
            outcome.applied += attach.applied
            outcome.warnings.extend(attach.warnings)
            if attach.applied < attach.requested:
                outcome.warnings.append("google_ads.warning.list_not_fully_attached")
        await self._record(
            account,
            MutationOutcome(
                resource="sharedSets",
                validate_only=outcome.validate_only,
                requested=1,
                applied=1 if created else 0,
                results=[{"index": 0, "ok": bool(created), "resource_name": created}],
            ),
            [
                Recordable(
                    GoogleAdsDecisionSubject.SEARCH_TERM.value,
                    name,
                    GoogleAdsDecisionKind.CREATED.value,
                    scope="account",
                    payload={"shared_set": created, "campaigns": campaign_ids},
                )
            ],
            action="google_ads.negative_list_created",
        )
        return outcome, account

    # --- ads ------------------------------------------------------------------------------------ #

    async def create_ad(
        self,
        account_id: uuid.UUID,
        *,
        ad_group_id: str,
        headlines: list[str],
        descriptions: list[str],
        final_urls: list[str],
        path1: str | None,
        path2: str | None,
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """A responsive search ad, created **paused**.

        Two checks run before Google sees it, and both are here rather than left to the API
        because Google reports them against an operation index and an index is not something
        anybody can fix. The lengths and counts are Google's own limits (#289's rule: a check the
        row report cannot name is a check the preview does not have). The banned phrases are the
        tenant's, and they are *checked* rather than merely requested of whatever wrote the copy —
        #300's rule, and the reason a banned phrase is a policy field instead of a line in a
        prompt.
        """
        account, policy = await self._prepare(account_id, "google_ads.campaign.write")
        fields = ops.validate_ad_copy(headlines, descriptions, final_urls)
        if fields:
            raise AppError("validation", "errors.validation", status_code=422, fields=fields)
        # Checked per part, so the field named is the field to edit: `headlines` was hardcoded,
        # which sent whoever wrote a banned word in a description to the wrong box.
        banned = policy_rules.banned_hit(policy, *headlines)
        culprit = "headlines"
        if banned is None:
            banned = policy_rules.banned_hit(policy, *descriptions)
            culprit = "descriptions"
        if banned is not None:
            raise AppError(
                "google_ads_banned_phrase",
                "errors.google_ads_banned_phrase",
                status_code=422,
                fields={culprit: "errors.google_ads_banned_phrase"},
                # Which phrase, for the reason §10a gives about protected terms: naming what was
                # found invites a fix, while a bare refusal invites an argument with the software.
                details={"blocks": banned},
            )
        outcome = await self._mutate(
            account,
            "adGroupAds",
            [
                ops.operation_create(
                    ops.responsive_search_ad_create(
                        ad_group_resource=ops.ad_group_rn(account.customer_id, ad_group_id),
                        headlines=headlines,
                        descriptions=descriptions,
                        final_urls=final_urls,
                        path1=path1,
                        path2=path2,
                    )
                )
            ],
            validate_only=validate_only,
            partial_failure=False,
            tool="ad_create",
        )
        outcome.warnings.append("google_ads.warning.created_paused")
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.AD.value,
                    headlines[0] if headlines else ad_group_id,
                    GoogleAdsDecisionKind.CREATED.value,
                    scope=f"ad_group:{ad_group_id}",
                    payload={"headlines": len(headlines), "descriptions": len(descriptions)},
                )
            ],
            action="google_ads.ad_created",
        )
        return outcome, account

    async def update_ad(
        self,
        account_id: uuid.UUID,
        *,
        ad_group_id: str,
        ad_id: str,
        status: str,
        validate_only: bool,
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Pause, resume or remove one ad.

        Status only. An ad's creative is **immutable** at Google — changing a headline means
        creating a new ad and removing the old one, which is deliberate on their side (an ad's
        performance history belongs to its text) and is two acts here for the same reason.
        """
        account, _policy = await self._prepare(account_id, "google_ads.campaign.write")
        outcome = await self._mutate(
            account,
            "adGroupAds",
            [
                ops.operation_update(
                    ops.ad_group_ad_rn(account.customer_id, ad_group_id, ad_id),
                    # Through the shared guard, not a bare upper(): this route documented
                    # removal through `status` too, and REMOVED is refused by Google.
                    _status_and_name(status, None),
                )
            ],
            validate_only=validate_only,
            partial_failure=False,
            tool="ad_update",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.AD.value,
                    ad_id,
                    _status_decision(status),
                    scope=f"ad_group:{ad_group_id}",
                    payload={"status": status},
                )
            ],
            action="google_ads.ad_changed",
        )
        return outcome, account

    # --- removals ------------------------------------------------------------------------------- #
    #
    # Removal is a `remove` operation and **never** ``status: "REMOVED"``. That enum is
    # output-only: Google answers ``requestError.INVALID_ENUM_VALUE``, *"Enum value 'REMOVED'
    # cannot be used"*, so every route that documented removal through the status field could
    # never perform one. Keywords and negatives were the only two resources that could be
    # removed, because they were the only two with a route that built the right operation.
    #
    # Removing a campaign does not cascade its children to REMOVED, and afterwards they cannot
    # be removed at all (``contextError.OPERATION_NOT_PERMITTED_FOR_REMOVED_RESOURCE``). So a
    # caller who wants a clean tree removes the ad, then the ad group, then the campaign — and
    # `_record` keeps each of those as its own line, because they are three decisions.

    async def remove_budget(
        self, account_id: uuid.UUID, budget_id: str, *, validate_only: bool
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Delete a daily budget. Google refuses while a campaign still uses it."""
        account, _policy = await self._prepare(account_id, "google_ads.budget.write")
        outcome = await self._mutate(
            account,
            "campaignBudgets",
            [ops.operation_remove(ops.budget_rn(account.customer_id, budget_id))],
            validate_only=validate_only,
            partial_failure=False,
            tool="budget_remove",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.BUDGET.value,
                    budget_id,
                    GoogleAdsDecisionKind.REMOVED.value,
                )
            ],
            action="google_ads.budget_removed",
        )
        return outcome, account

    async def remove_campaign(
        self, account_id: uuid.UUID, campaign_id: str, *, validate_only: bool
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Delete a campaign. Permanent at Google — it can be recreated, never restored."""
        account, _policy = await self._prepare(account_id, "google_ads.campaign.write")
        outcome = await self._mutate(
            account,
            "campaigns",
            [ops.operation_remove(ops.campaign_rn(account.customer_id, campaign_id))],
            validate_only=validate_only,
            partial_failure=False,
            tool="campaign_remove",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.CAMPAIGN.value,
                    campaign_id,
                    GoogleAdsDecisionKind.REMOVED.value,
                    scope=f"campaign:{campaign_id}",
                )
            ],
            action="google_ads.campaign_removed",
        )
        return outcome, account

    async def remove_ad_group(
        self, account_id: uuid.UUID, ad_group_id: str, *, validate_only: bool
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Delete an ad group, with its keywords and ads."""
        account, _policy = await self._prepare(account_id, "google_ads.campaign.write")
        outcome = await self._mutate(
            account,
            "adGroups",
            [ops.operation_remove(ops.ad_group_rn(account.customer_id, ad_group_id))],
            validate_only=validate_only,
            partial_failure=False,
            tool="ad_group_remove",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.AD_GROUP.value,
                    ad_group_id,
                    GoogleAdsDecisionKind.REMOVED.value,
                    scope=f"ad_group:{ad_group_id}",
                )
            ],
            action="google_ads.ad_group_removed",
        )
        return outcome, account

    async def remove_ad(
        self, account_id: uuid.UUID, *, ad_group_id: str, ad_id: str, validate_only: bool
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Delete one ad from its ad group."""
        account, _policy = await self._prepare(account_id, "google_ads.campaign.write")
        outcome = await self._mutate(
            account,
            "adGroupAds",
            [ops.operation_remove(ops.ad_group_ad_rn(account.customer_id, ad_group_id, ad_id))],
            validate_only=validate_only,
            partial_failure=False,
            tool="ad_remove",
        )
        await self._record(
            account,
            outcome,
            [
                Recordable(
                    GoogleAdsDecisionSubject.AD.value,
                    ad_id,
                    GoogleAdsDecisionKind.REMOVED.value,
                    scope=f"ad_group:{ad_group_id}",
                )
            ],
            action="google_ads.ad_removed",
        )
        return outcome, account

    async def remove_negative_list(
        self, account_id: uuid.UUID, shared_set_id: str, *, validate_only: bool
    ) -> tuple[MutationOutcome, GoogleAdsAccount]:
        """Delete a shared negative-keyword list, and with it every campaign's use of it."""
        account, _policy = await self._prepare(account_id, "google_ads.negative.write")
        outcome = await self._mutate(
            account,
            "sharedSets",
            [ops.operation_remove(ops.shared_set_rn(account.customer_id, shared_set_id))],
            validate_only=validate_only,
            partial_failure=False,
            tool="negative_list_remove",
        )
        await self._record(
            account,
            outcome,
            [
                # The same subject `create_negative_list` files under, deliberately: the log's
                # "newest row about a subject wins" rule only holds while a create and its
                # removal are about the same subject.
                Recordable(
                    GoogleAdsDecisionSubject.SEARCH_TERM.value,
                    shared_set_id,
                    GoogleAdsDecisionKind.REMOVED.value,
                    scope="account",
                )
            ],
            action="google_ads.negative_list_removed",
        )
        return outcome, account

    # --- internals ------------------------------------------------------------------------------ #

    async def _peek(self, account: GoogleAdsAccount, read: Any, *, tool: str) -> Any:
        """One read against Google, with the connection released — nothing is mutated."""
        async with self.accounts.open_client(account_id=account.id, tool=tool) as (client, _a):
            return await read(client)


def _refuse(refusal: policy_rules.PolicyRefusal | None) -> None:
    """Turn a call-level policy refusal into the error envelope, naming the field and the limit.

    The limit is in the payload rather than only in the message because #305's lesson is that a
    constraint nobody can see working reads as a broken control: "refused" invites an argument
    with the software, "refused, the ceiling is € 80 and you asked for € 800" invites a decision.
    """
    if refusal is None:
        return
    raise AppError(
        refusal.key.removeprefix("errors."),
        refusal.key,
        status_code=422,
        fields={refusal.field: refusal.key},
        # `fields` values are i18n keys and a key cannot hold a number, so the figures ride in
        # `details` — the same two facts `skipped[]` already reports per row (`limit`, `blocks`).
        # Without them the docstring above was describing a payload that was never sent.
        details={
            key: value
            for key, value in (("limit", refusal.limit), ("blocks", refusal.subject))
            if value is not None
        }
        or None,
    )


#: The only two an update may set. ``REMOVED`` is output-only at Google — sending it answers
#: ``requestError.INVALID_ENUM_VALUE`` — so it is refused here, where the message can name the
#: route that does work, rather than 200 operations later as somebody else's enum complaint.
_SETTABLE_STATUSES = frozenset({"ENABLED", "PAUSED"})


def _status_and_name(status: str | None, name: str | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if status:
        wanted = status.strip().upper()
        if wanted not in _SETTABLE_STATUSES:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={
                    "status": (
                        "errors.google_ads_status_removed"
                        if wanted == "REMOVED"
                        else "errors.google_ads_status_invalid"
                    )
                },
            )
        fields["status"] = wanted
    if name:
        fields["name"] = name
    if not fields:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"status": "errors.google_ads_nothing_to_change"},
        )
    return fields


def _status_decision(status: str | None) -> str:
    return {
        "PAUSED": GoogleAdsDecisionKind.PAUSED.value,
        "ENABLED": GoogleAdsDecisionKind.ENABLED.value,
        "REMOVED": GoogleAdsDecisionKind.REMOVED.value,
    }.get((status or "").strip().upper(), GoogleAdsDecisionKind.ADDED.value)


def _negative_target(level: str, customer_id: str, parent_id: str) -> tuple[str, str, str]:
    """``(mutate collection, parent field, parent resource name)`` for one exclusion level.

    Three resources for one question, which is Google's model rather than ours: an exclusion on an
    ad group, on a campaign, and on a shared list are three different tables. The read surface
    already answers all three as one list; the write surface has to pick.
    """
    if level == "ad_group":
        return "adGroupCriteria", "adGroup", ops.ad_group_rn(customer_id, parent_id)
    if level == "campaign":
        return "campaignCriteria", "campaign", ops.campaign_rn(customer_id, parent_id)
    if level == "shared_set":
        return "sharedCriteria", "sharedSet", ops.shared_set_rn(customer_id, parent_id)
    raise AppError(
        "validation",
        "errors.validation",
        status_code=422,
        fields={"level": "errors.google_ads_negative_level_invalid"},
    )


def _micros(raw: Any) -> float | None:
    """Google's int64-as-a-string back into money, or ``None`` when it said nothing.

    ``None`` rather than ``0.0``: a budget of zero and an unknown budget are different facts, and
    the relative ceiling is a claim about a change that cannot be made against an unknown.
    """
    if raw in (None, ""):
        return None
    try:
        return int(raw) / 1_000_000
    except (TypeError, ValueError):
        return None
