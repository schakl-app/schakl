"""Pydantic shapes for the AI core (#126) and its features (#127–#130)."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.core.ai.models import AI_FEATURES, AI_PROVIDERS

Provider = Literal["anthropic", "openai", "openai_compatible"]

_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class AIFeatureConfig(BaseModel):
    enabled: bool = True
    #: Optional per-feature model override; None = the org's default model.
    model: str | None = Field(default=None, max_length=255)


class AISettingsWrite(BaseModel):
    provider: Provider
    #: Write-only. Empty on an update means "keep the stored key".
    api_key: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=1024)
    default_model: str | None = Field(default=None, max_length=255)
    features: dict[str, AIFeatureConfig] = Field(default_factory=dict)
    house_style: str | None = Field(default=None, max_length=4000)
    monthly_token_budget: int | None = Field(default=None, ge=1)


class AISettingsRead(BaseModel):
    provider: Provider
    base_url: str | None
    default_model: str
    has_key: bool
    features: dict[str, AIFeatureConfig]
    house_style: str | None
    monthly_token_budget: int | None


class AITestResult(BaseModel):
    """Round-trip result of the settings page's test button; ``error`` is the provider's
    failure verbatim — the one place raw provider text reaches the UI on purpose."""

    ok: bool
    model: str | None = None
    error: str | None = None


class AIUsageFeature(BaseModel):
    feature: str
    tokens_in: int
    tokens_out: int
    requests: int


class AIUsageSummary(BaseModel):
    """This calendar month's metering, for the settings-page meter (#126)."""

    month: str
    tokens_total: int
    budget: int | None
    features: list[AIUsageFeature]


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #
WritingAction = Literal[
    "improve", "shorten", "expand", "fix", "tone_business", "tone_informal",
    "translate", "draft",
]


class WritingAssistRequest(BaseModel):
    action: WritingAction
    text: str = Field(min_length=1, max_length=40_000)
    #: Naming context only — never the record graph (#128 scope discipline).
    entity_type: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=255)
    #: For ``translate``: the language to translate into (nl or en).
    target_locale: str | None = Field(default=None, max_length=8)
    override_budget: bool = False


class AssistantContext(BaseModel):
    entity_type: str = Field(max_length=40)
    entity_id: uuid.UUID
    label: str | None = Field(default=None, max_length=255)


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=40_000)


class AssistantRequest(BaseModel):
    messages: list[AssistantMessage] = Field(min_length=1, max_length=40)
    context: AssistantContext | None = None
    override_budget: bool = False


class TimeParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    override_budget: bool = False


class TimeParseResult(BaseModel):
    """A *draft* entry: prefills the form, never creates anything (#129)."""

    date: dt.date | None = None
    start: str | None = None
    end: str | None = None
    duration_minutes: int | None = None
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    description: str | None = None


class TimeReconstructRequest(BaseModel):
    date: dt.date
    override_budget: bool = False


class TimeSuggestion(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    minutes: int | None = None
    description: str = ""
    label: str = ""


class TimeReconstructResult(BaseModel):
    short: bool
    scheduled_minutes: int
    logged_minutes: int
    leave_minutes: int
    suggestions: list[TimeSuggestion] = Field(default_factory=list)


class DigestRequest(BaseModel):
    override_budget: bool = False


class ReportGenerateRequest(BaseModel):
    company_id: uuid.UUID
    period: str = Field(pattern=_PERIOD.pattern)
    language: str = Field(default="nl", max_length=8)
    override_budget: bool = False


class ReportCreate(BaseModel):
    company_id: uuid.UUID
    period: str = Field(pattern=_PERIOD.pattern)
    language: str = Field(default="nl", max_length=8)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=200_000)


class ReportUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=200_000)


class ReportRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    period: str
    language: str
    title: str
    content: str
    created_by_name: str | None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


__all__ = [
    "AI_FEATURES",
    "AI_PROVIDERS",
    "AIFeatureConfig",
    "AISettingsRead",
    "AISettingsWrite",
    "AITestResult",
    "AIUsageFeature",
    "AIUsageSummary",
    "AssistantContext",
    "AssistantMessage",
    "AssistantRequest",
    "DigestRequest",
    "ReportCreate",
    "ReportGenerateRequest",
    "ReportRead",
    "ReportUpdate",
    "TimeParseRequest",
    "TimeParseResult",
    "TimeReconstructRequest",
    "TimeReconstructResult",
    "TimeSuggestion",
    "WritingAssistRequest",
]
