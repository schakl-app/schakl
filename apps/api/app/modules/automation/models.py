"""Automation storage (issue #27, stage 1) — rules, their actions, and the run log.

Three org-scoped tables, RLS-forced like every domain table:

* ``automation_rules`` — one per tenant-authored rule: a trigger event, a declarative JSONB
  condition tree (never user-supplied code) and an enabled flag.
* ``automation_actions`` — the ordered actions a rule performs. ``config`` is per-type
  (a task template id, a webhook url + ``confirm`` flag, …); ``action_type`` is a key from
  the action registry, never free text that gets executed.
* ``automation_runs`` — the audit trail (issue #27: "a silent automation is worse than
  none"). One row per rule firing, written **in the emitter's transaction** and executed by
  the ARQ worker. ``dedup_key`` (unique per org) is what makes a re-emitted event idempotent;
  ``depth`` is the loop guard an action-caused event increments. ``rule_name`` is snapshotted
  and ``rule_id`` is ``SET NULL`` on delete — the trail outlives the rule it describes, like
  every audit surface here (§16). ``entity_id`` carries no FK for the same reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: Run lifecycle. ``skipped`` records a match that was deliberately not executed (depth cap),
#: so loop protection is visible in the log rather than silent.
RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_SKIPPED = "skipped"
RUN_STATUSES: tuple[str, ...] = (RUN_PENDING, RUN_RUNNING, RUN_SUCCEEDED, RUN_FAILED, RUN_SKIPPED)


class AutomationRule(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "automation_rules"
    __table_args__ = (
        # The bus handler's hot path: one indexed probe per emitted event.
        Index("ix_automation_rules_trigger", "org_id", "trigger_event", "enabled"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(50), nullable=False)
    # Declarative predicate tree: {"all": [...]} / {"any": [...]} / {field, op, value}.
    conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AutomationAction(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "automation_actions"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AutomationRun(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        Index("ix_automation_runs_rule", "org_id", "rule_id", "created_at"),
        # Idempotency: a retried/re-emitted event maps to the same key and never double-fires.
        Index("uq_automation_runs_dedup", "org_id", "dedup_key", unique=True),
        # The requeue sweep's probe (stale pending runs whose enqueue was lost).
        Index(
            "ix_automation_runs_pending",
            "org_id",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    # SET NULL + name snapshot: deleting a rule must not delete its history (§16).
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Polymorphic, cross-module → deliberately NO FK; the row survives the entity's deletion.
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default=RUN_PENDING, server_default=RUN_PENDING
    )
    # Loop protection: how many automation hops caused this event (0 = a human/cron did).
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The trigger payload, snapshotted (routing keys stripped) — the worker executes later and
    # the webhook action forwards it; an event payload cannot be reconstructed after the fact.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # An i18n key (``errors.*``) where the engine speaks, raw data (an HTTP status, an
    # exception string) where the outside world does; the UI translates the former.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-step results: [{action_type, status, result?, error?}], in execution order.
    steps: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    dedup_key: Mapped[str] = mapped_column(String(80), nullable=False)
