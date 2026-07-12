"""Rule/run CRUD + dry-run (issue #27). Tenant-scoped like every service."""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.automation.actions import available_actions
from app.modules.automation.conditions import evaluate, validate
from app.modules.automation.engine import content_payload, entity_snapshot
from app.modules.automation.models import AutomationAction, AutomationRule, AutomationRun
from app.modules.automation.schemas import (
    ActionRead,
    ActionWrite,
    DryRunRequest,
    DryRunResult,
    RuleCreate,
    RuleRead,
    RuleUpdate,
    RunRead,
)
from app.modules.automation.triggers import TRIGGERS


class AutomationService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.rules = ctx.repo(AutomationRule)
        self.actions = ctx.repo(AutomationAction)
        self.runs = ctx.repo(AutomationRun)

    # ------------------------------------------------------------------ #
    # Validation — a stored rule must always be runnable
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_trigger(trigger_event: str) -> None:
        if trigger_event not in TRIGGERS:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"trigger_event": "errors.automation_unknown_trigger"},
            )

    @staticmethod
    def _validate_actions(actions: list[ActionWrite]) -> None:
        known = available_actions()
        for action in actions:
            if action.action_type not in known:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"actions": "errors.automation_unknown_action"},
                )
            if action.action_type == "webhook.post" and not str(
                action.config.get("url") or ""
            ).startswith(("http://", "https://")):
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"actions": "errors.automation_webhook_invalid_url"},
                )

    # ------------------------------------------------------------------ #
    # Rules
    # ------------------------------------------------------------------ #
    async def _read(self, rule: AutomationRule) -> RuleRead:
        actions = await self.actions.list(
            limit=50, order_by=AutomationAction.position.asc(), rule_id=rule.id
        )
        read = RuleRead.model_validate(rule)
        read.actions = [ActionRead.model_validate(a) for a in actions]
        return read

    async def list(self) -> list[RuleRead]:
        rules = await self.rules.list(
            limit=200, order_by=AutomationRule.position.asc()
        )
        return [await self._read(rule) for rule in rules]

    async def get(self, rule_id: uuid.UUID) -> RuleRead:
        return await self._read(await self.rules.get_or_404(rule_id))

    async def _replace_actions(
        self, rule: AutomationRule, actions: list[ActionWrite]
    ) -> None:
        existing = await self.actions.list(limit=50, rule_id=rule.id)
        for action in existing:
            await self.actions.delete(action)
        for index, action in enumerate(actions):
            await self.actions.create(
                rule_id=rule.id,
                action_type=action.action_type,
                config=action.config,
                position=index,
            )

    async def create(self, data: RuleCreate) -> RuleRead:
        self._validate_trigger(data.trigger_event)
        validate(data.conditions)
        self._validate_actions(data.actions)
        rule = await self.rules.create(
            name=data.name,
            trigger_event=data.trigger_event,
            conditions=data.conditions,
            enabled=data.enabled,
            position=data.position,
        )
        await self._replace_actions(rule, data.actions)
        return await self._read(rule)

    async def update(self, rule_id: uuid.UUID, data: RuleUpdate) -> RuleRead:
        rule = await self.rules.get_or_404(rule_id)
        values = data.model_dump(exclude_unset=True, exclude={"actions"})
        if "trigger_event" in values:
            self._validate_trigger(values["trigger_event"])
        if "conditions" in values:
            validate(values["conditions"])
        if data.actions is not None:
            self._validate_actions(data.actions)
        if values:
            rule = await self.rules.update(rule, **values)
        if data.actions is not None:
            await self._replace_actions(rule, data.actions)
        return await self._read(rule)

    async def delete(self, rule_id: uuid.UUID) -> None:
        rule = await self.rules.get_or_404(rule_id)
        await self.rules.delete(rule)

    # ------------------------------------------------------------------ #
    # Runs — the audit trail (read-only via the API)
    # ------------------------------------------------------------------ #
    async def list_runs(
        self, *, rule_id: uuid.UUID | None, limit: int, offset: int
    ) -> tuple[list[RunRead], int]:
        stmt = self.runs.scoped_select().order_by(AutomationRun.created_at.desc())
        if rule_id is not None:
            stmt = stmt.where(AutomationRun.rule_id == rule_id)
        total = await self.runs.count(**({"rule_id": rule_id} if rule_id else {}))
        rows = (
            (await self.ctx.session.execute(stmt.limit(limit).offset(offset)))
            .scalars()
            .all()
        )
        return [RunRead.model_validate(row) for row in rows], total

    # ------------------------------------------------------------------ #
    # Dry-run — evaluate, never execute (issue #27's preview requirement)
    # ------------------------------------------------------------------ #
    async def dry_run(self, data: DryRunRequest) -> DryRunResult:
        self._validate_trigger(data.trigger_event)
        validate(data.conditions)
        self._validate_actions(data.actions)
        spec = TRIGGERS[data.trigger_event]
        snapshot = await entity_snapshot(self.ctx, spec, data.entity_id)
        merged = {**snapshot, **content_payload(data.payload)}
        matched = evaluate(data.conditions, merged) if data.conditions else True
        return DryRunResult(
            matched=matched,
            would_fire=[a.action_type for a in data.actions] if matched else [],
            snapshot_found=bool(snapshot),
        )
