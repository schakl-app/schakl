"""Pydantic schemas for the automation module (issue #27)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Rules + actions
# --------------------------------------------------------------------------- #
class ActionWrite(BaseModel):
    action_type: str = Field(min_length=1, max_length=50)
    config: dict[str, Any] = Field(default_factory=dict)


class ActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_type: str
    config: dict[str, Any]
    position: int


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trigger_event: str = Field(min_length=1, max_length=50)
    conditions: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    position: int = 0
    actions: list[ActionWrite] = Field(default_factory=list, max_length=20)


class RuleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    trigger_event: str | None = Field(None, min_length=1, max_length=50)
    conditions: dict[str, Any] | None = None
    enabled: bool | None = None
    position: int | None = None
    # None = leave the action list untouched; a list replaces it wholesale (template pattern).
    actions: list[ActionWrite] | None = Field(None, max_length=20)


class RuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    trigger_event: str
    conditions: dict[str, Any]
    enabled: bool
    position: int
    created_at: datetime
    updated_at: datetime
    actions: list[ActionRead] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: uuid.UUID | None
    rule_name: str
    trigger_event: str
    entity_type: str
    entity_id: uuid.UUID
    status: str
    depth: int
    payload: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    steps: list[Any]
    created_at: datetime


# --------------------------------------------------------------------------- #
# Dry-run (issue #27: preview before enabling)
# --------------------------------------------------------------------------- #
class DryRunRequest(BaseModel):
    """A draft rule body + a sample entity: what *would* happen? Nothing executes."""

    trigger_event: str = Field(min_length=1, max_length=50)
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: list[ActionWrite] = Field(default_factory=list, max_length=20)
    entity_id: uuid.UUID
    # Optional sample event payload, merged over the entity snapshot like a real trigger.
    payload: dict[str, Any] = Field(default_factory=dict)


class DryRunResult(BaseModel):
    matched: bool
    #: The action types that would fire, in order — empty when conditions don't match.
    would_fire: list[str]
    #: Whether the sample entity was found (a missing row evaluates payload-only, honestly).
    snapshot_found: bool


# --------------------------------------------------------------------------- #
# Catalog — what the rule editor may offer
# --------------------------------------------------------------------------- #
class TriggerInfo(BaseModel):
    event: str
    entity_type: str


class CatalogRead(BaseModel):
    triggers: list[TriggerInfo]
    actions: list[str]
