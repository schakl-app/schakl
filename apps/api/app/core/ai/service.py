"""The AI core service (#126): settings admin, feature gating, budget, metering, tool loop.

No feature ever talks to a provider SDK directly — everything goes through here, so the
tenant's provider choice, key, per-feature toggles and budget apply everywhere at once.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import providers
from app.core.ai.models import AI_FEATURES, AI_PROVIDERS, AISettings, AIUsage
from app.core.ai.providers import (
    AIEvent,
    AIProviderError,
    ChatMessage,
    ProviderConfig,
    ToolDef,
)
from app.core.ai.schemas import (
    AIFeatureConfig,
    AIModelsRequest,
    AIModelsResult,
    AISettingsRead,
    AISettingsWrite,
    AITestResult,
    AIUsageFeature,
    AIUsageSummary,
)
from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext
from app.errors import AppError

logger = logging.getLogger(__name__)

#: ``/meta/me`` reads the enabled features on every SSR render; a per-org TTL cache keeps
#: that free of a query in steady state. Invalidated explicitly on every settings write.
_FEATURES_TTL_SECONDS = 30.0
_features_cache: dict[uuid.UUID, tuple[float, list[str]]] = {}


def invalidate_features_cache(org_id: uuid.UUID) -> None:
    _features_cache.pop(org_id, None)


def _feature_config(row: AISettings, feature: str) -> AIFeatureConfig:
    raw = (row.features or {}).get(feature) or {}
    return AIFeatureConfig(
        enabled=bool(raw.get("enabled", True)), model=raw.get("model") or None
    )


async def get_row(session: AsyncSession, org_id: uuid.UUID) -> AISettings | None:
    return await session.scalar(select(AISettings).where(AISettings.org_id == org_id))


async def enabled_features(session: AsyncSession, org_id: uuid.UUID) -> list[str]:
    """The feature keys usable for this org — no provider configured means none at all
    ("off means invisible", #126). Cached per org; see ``_FEATURES_TTL_SECONDS``."""
    now = time.monotonic()
    cached = _features_cache.get(org_id)
    if cached is not None and now - cached[0] < _FEATURES_TTL_SECONDS:
        return cached[1]
    row = await get_row(session, org_id)
    features = (
        [f for f in AI_FEATURES if _feature_config(row, f).enabled] if row is not None else []
    )
    _features_cache[org_id] = (now, features)
    return features


class AIService:
    """What features call: resolves the tenant's provider config, enforces the budget,
    runs the model, and meters usage. One instance per request context."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ #
    # Gating
    # ------------------------------------------------------------------ #
    async def config_for(self, feature: str) -> ProviderConfig:
        """The provider config for one feature, or the standard errors when the tenant has
        not configured a provider / has the feature off."""
        row = await get_row(self.ctx.session, self.ctx.org.id)
        if row is None:
            raise AppError("ai_not_configured", "errors.ai_not_configured", status_code=409)
        config = _feature_config(row, feature)
        if feature in AI_FEATURES and not config.enabled:
            raise AppError(
                "ai_feature_disabled", "errors.ai_feature_disabled", status_code=409
            )
        try:
            api_key = decrypt(row.api_key_enc)
        except ValueError as exc:  # rotated encryption key — configuration is gone
            raise AppError(
                "ai_not_configured", "errors.ai_not_configured", status_code=409
            ) from exc
        return ProviderConfig(
            provider=row.provider,
            api_key=api_key,
            model=config.model or row.default_model,
            base_url=row.base_url,
        )

    async def ensure_budget(self, *, override: bool = False) -> None:
        """The monthly soft cap (#126): interactive use over 100 % sits behind an explicit
        acknowledgement (the "budget bereikt" notice); non-interactive callers never pass
        ``override`` and hard-stop."""
        row = await get_row(self.ctx.session, self.ctx.org.id)
        if row is None or row.monthly_token_budget is None:
            return
        spent = await self._month_tokens()
        if spent >= row.monthly_token_budget and not override:
            raise AppError("ai_budget_reached", "errors.ai_budget_reached", status_code=409)

    async def _month_tokens(self) -> int:
        start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total = await self.ctx.session.scalar(
            select(func.coalesce(func.sum(AIUsage.tokens_in + AIUsage.tokens_out), 0)).where(
                AIUsage.org_id == self.ctx.org.id, AIUsage.created_at >= start
            )
        )
        return int(total or 0)

    def locale(self) -> str:
        return self.ctx.user.locale or "nl"

    async def house_style(self) -> str | None:
        row = await get_row(self.ctx.session, self.ctx.org.id)
        return row.house_style if row is not None else None

    # ------------------------------------------------------------------ #
    # Model calls
    # ------------------------------------------------------------------ #
    async def record_usage(
        self, feature: str, model: str, tokens_in: int, tokens_out: int
    ) -> None:
        """Counts and labels only — never content (#126)."""
        self.ctx.session.add(
            AIUsage(
                org_id=self.ctx.org.id,
                user_id=self.ctx.user.id,
                feature=feature,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        )
        await self.ctx.session.flush()

    async def stream(
        self,
        feature: str,
        *,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef] | None = None,
        force_tool: str | None = None,
        disable_tools: bool = False,
        override_budget: bool = False,
        max_tokens: int = providers.MAX_TOKENS,
    ) -> AsyncIterator[AIEvent]:
        """One metered model turn; usage is recorded when the provider reports it.

        A provider refusal/failure becomes the standard 502 envelope: the verbatim message
        goes to the log (it may quote our request, never tenant secrets), the client gets
        the i18n key — except on the settings page's test button, which bypasses this on
        purpose to show the provider's own words."""
        config = await self.config_for(feature)
        await self.ensure_budget(override=override_budget)
        try:
            async for event in providers.stream_chat(
                config,
                system=system,
                messages=messages,
                tools=tools,
                force_tool=force_tool,
                disable_tools=disable_tools,
                max_tokens=max_tokens,
            ):
                if event.kind == "done":
                    await self.record_usage(
                        feature, config.model, event.tokens_in, event.tokens_out
                    )
                yield event
        except AIProviderError as exc:
            logger.warning("AI provider error (%s/%s): %s", config.provider, feature, exc)
            raise AppError(
                "ai_provider_error", "errors.ai_provider_error", status_code=502
            ) from exc

    async def complete(
        self,
        feature: str,
        *,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolDef] | None = None,
        force_tool: str | None = None,
        disable_tools: bool = False,
        override_budget: bool = False,
        max_tokens: int = providers.MAX_TOKENS,
    ) -> tuple[str, list[providers.ToolCall]]:
        text_parts: list[str] = []
        calls: list[providers.ToolCall] = []
        async for event in self.stream(
            feature,
            system=system,
            messages=messages,
            tools=tools,
            force_tool=force_tool,
            disable_tools=disable_tools,
            override_budget=override_budget,
            max_tokens=max_tokens,
        ):
            if event.kind == "text":
                text_parts.append(event.text)
            elif event.kind == "tool_call" and event.tool_call is not None:
                calls.append(event.tool_call)
        return "".join(text_parts), calls


class AISettingsService:
    """The Instellingen → AI admin surface: one settings row per org, key write-only."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    def _read(self, row: AISettings) -> AISettingsRead:
        return AISettingsRead(
            provider=row.provider,  # type: ignore[arg-type]
            base_url=row.base_url,
            default_model=row.default_model,
            has_key=bool(row.api_key_enc),
            features={f: _feature_config(row, f) for f in AI_FEATURES},
            house_style=row.house_style,
            monthly_token_budget=row.monthly_token_budget,
        )

    async def get(self) -> AISettingsRead | None:
        self.ctx.require("ai.settings.manage")
        row = await get_row(self.ctx.session, self.ctx.org.id)
        return self._read(row) if row else None

    async def save(self, data: AISettingsWrite) -> AISettingsRead:
        self.ctx.require("ai.settings.manage")
        if data.provider not in AI_PROVIDERS:  # pragma: no cover - Literal already guards
            raise AppError("validation", "errors.validation", status_code=422)
        row = await get_row(self.ctx.session, self.ctx.org.id)

        api_key = (data.api_key or "").strip()
        # An empty key on an update means "keep what is stored" — the form never sees it back.
        if not api_key:
            if row is None:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"api_key": "errors.required"},
                )
            api_key_enc = row.api_key_enc
        else:
            api_key_enc = encrypt(api_key)

        default_model = (data.default_model or "").strip() or providers.DEFAULT_MODELS.get(
            data.provider, ""
        )
        if not default_model:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"default_model": "errors.required"},
            )
        base_url = (data.base_url or "").strip() or None
        if data.provider == "openai_compatible" and not base_url:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"base_url": "errors.required"},
            )

        features = {
            f: data.features[f].model_dump() for f in AI_FEATURES if f in data.features
        }
        values = {
            "provider": data.provider,
            "api_key_enc": api_key_enc,
            "base_url": base_url,
            "default_model": default_model,
            "features": features,
            "house_style": (data.house_style or "").strip() or None,
            "monthly_token_budget": data.monthly_token_budget,
        }
        if row is None:
            row = AISettings(org_id=self.ctx.org.id, **values)
            self.ctx.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await self.ctx.session.flush()
        invalidate_features_cache(self.ctx.org.id)
        return self._read(row)

    async def delete(self) -> None:
        """Remove the configuration — every AI affordance is simply gone again."""
        self.ctx.require("ai.settings.manage")
        row = await get_row(self.ctx.session, self.ctx.org.id)
        if row is not None:
            await self.ctx.session.delete(row)
            await self.ctx.session.flush()
        invalidate_features_cache(self.ctx.org.id)

    async def test(self) -> AITestResult:
        """Round-trip a tiny completion through the *stored* settings and report the
        provider's failure verbatim (#126) — the email test-button pattern."""
        self.ctx.require("ai.settings.manage")
        row = await get_row(self.ctx.session, self.ctx.org.id)
        if row is None:
            return AITestResult(ok=False, error="not configured")
        try:
            config = ProviderConfig(
                provider=row.provider,
                api_key=decrypt(row.api_key_enc),
                model=row.default_model,
                base_url=row.base_url,
            )
            text, _, done = await providers.complete_chat(
                config,
                system="You are a connection test. Reply with the single word: ok",
                messages=[ChatMessage(role="user", content="ping")],
                max_tokens=32,
            )
        except (AIProviderError, ValueError, OSError) as exc:
            return AITestResult(ok=False, error=str(exc))
        service = AIService(self.ctx)
        await service.record_usage("test", row.default_model, done.tokens_in, done.tokens_out)
        return AITestResult(ok=bool(text.strip()), model=row.default_model)

    async def list_models(self, payload: AIModelsRequest) -> AIModelsResult:
        """The provider's live model list, for the settings picker (#126): fetched, so it
        never rots. Empty inputs fall back to the stored row — a typed-but-unsaved key
        works during first setup, and the stored key is used without ever playing it back."""
        self.ctx.require("ai.settings.manage")
        row = await get_row(self.ctx.session, self.ctx.org.id)
        provider = payload.provider or (row.provider if row else None)
        if provider is None:
            return AIModelsResult(error="no provider configured")
        api_key = (payload.api_key or "").strip()
        if not api_key:
            # Only reuse the stored key for the provider it belongs to — a key typed for
            # one provider must never be sent to another.
            if row is None or row.provider != provider:
                return AIModelsResult(error="no API key")
            try:
                api_key = decrypt(row.api_key_enc)
            except ValueError:
                return AIModelsResult(error="no API key")
        base_url = (payload.base_url or "").strip() or (
            row.base_url if row and row.provider == provider else None
        )
        config = ProviderConfig(provider=provider, api_key=api_key, model="", base_url=base_url)
        try:
            return AIModelsResult(models=await providers.list_models(config))
        except (AIProviderError, ValueError, OSError) as exc:
            return AIModelsResult(error=str(exc))

    async def usage(self) -> AIUsageSummary:
        """This month's metering grouped by feature — the settings-page meter."""
        self.ctx.require("ai.settings.manage")
        start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = (
            await self.ctx.session.execute(
                select(
                    AIUsage.feature,
                    func.coalesce(func.sum(AIUsage.tokens_in), 0),
                    func.coalesce(func.sum(AIUsage.tokens_out), 0),
                    func.count(),
                )
                .where(AIUsage.org_id == self.ctx.org.id, AIUsage.created_at >= start)
                .group_by(AIUsage.feature)
                .order_by(AIUsage.feature)
            )
        ).all()
        settings_row = await get_row(self.ctx.session, self.ctx.org.id)
        return AIUsageSummary(
            month=start.strftime("%Y-%m"),
            tokens_total=sum(int(r[1]) + int(r[2]) for r in rows),
            budget=settings_row.monthly_token_budget if settings_row else None,
            features=[
                AIUsageFeature(
                    feature=r[0], tokens_in=int(r[1]), tokens_out=int(r[2]), requests=int(r[3])
                )
                for r in rows
            ],
        )
