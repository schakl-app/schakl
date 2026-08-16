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

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import providers
from app.core.ai.models import (
    AI_FEATURES,
    AI_PROVIDERS,
    SPEECH_FEATURES,
    AISettings,
    AIUsage,
)
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
from app.core.ai.transcribe import DEFAULT_SPEECH_MODEL, can_transcribe
from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.i18n import resolve_locale, translate

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


#: Reported alongside the real feature keys when the org can actually transcribe (#246). It is
#: deliberately *not* in ``AI_FEATURES``: there is nothing to toggle — either a speech provider
#: is configured or there is no such capability — and adding it there would grow the settings
#: form, the web's ``AIFeature`` union and the per-feature model override for a non-choice.
SPEECH_CAPABILITY = "speech"


async def enabled_features(session: AsyncSession, org_id: uuid.UUID) -> list[str]:
    """The feature keys usable for this org — no provider configured means none at all
    ("off means invisible", #126). Cached per org; see ``_FEATURES_TTL_SECONDS``.

    ``speech`` rides along as a capability rather than a toggle: without it the web app would
    draw a microphone on every Anthropic-configured org and 409 on the first click, which is
    the opposite of "off means invisible".

    It needs a host as well as a provider, and the host is now a *set* (``SPEECH_FEATURES``,
    #382). Written as one name it coupled the task microphone to the time quick-add's toggle:
    an org that wanted dictated tasks and no AI time entries got no microphone anywhere, with
    nothing on any screen able to say why.
    """
    now = time.monotonic()
    cached = _features_cache.get(org_id)
    if cached is not None and now - cached[0] < _FEATURES_TTL_SECONDS:
        return cached[1]
    row = await get_row(session, org_id)
    features = (
        [f for f in AI_FEATURES if _feature_config(row, f).enabled] if row is not None else []
    )
    if row is not None and any(f in features for f in SPEECH_FEATURES) and _speech_ready(row):
        features.append(SPEECH_CAPABILITY)
    _features_cache[org_id] = (now, features)
    return features


def _speech_ready(row: AISettings) -> bool:
    """Can this org transcribe at all? Its own speech credential, or a chat provider that
    happens to have a speech endpoint — which Anthropic, the default, does not."""
    provider = row.speech_provider or row.provider
    if not can_transcribe(provider):
        return False
    return bool(row.speech_api_key_enc if row.speech_provider else row.api_key_enc)


class AIService:
    """What features call: resolves the tenant's provider config, enforces the budget,
    runs the model, and meters usage. One instance per request context."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        #: The org's settings row, read at most once per request. A multi-round tool loop used
        #: to re-read it (and re-sum the month's usage) on every round, so a single parse spent
        #: a dozen DB round trips re-answering a question whose answer cannot change mid-request.
        self._row: AISettings | None = None
        self._row_loaded = False
        #: Metering accumulated across a multi-round loop, written once by ``flush_usage``.
        self.pending_tokens_in = 0
        self.pending_tokens_out = 0
        self.pending_model: str | None = None
        #: Why the last :meth:`complete` stopped. Kept on the instance rather than added to the
        #: return tuple: every caller wants the text and the calls, and only the ones that can
        #: *do* something about a cut-off answer ask (``truncated`` below). Five callers stay
        #: unchanged, and the one question they could not previously ask becomes askable.
        self.last_stop_reason: str | None = None

    async def _settings(self) -> AISettings | None:
        if not self._row_loaded:
            self._row = await get_row(self.ctx.session, self.ctx.org.id)
            self._row_loaded = True
        return self._row

    # ------------------------------------------------------------------ #
    # Gating
    # ------------------------------------------------------------------ #
    async def config_for(self, feature: str) -> ProviderConfig:
        """The provider config for one feature, or the standard errors when the tenant has
        not configured a provider / has the feature off."""
        row = await self._settings()
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

    async def speech_config(self) -> ProviderConfig:
        """The provider config for transcription (#246).

        Its own credential when the tenant set one, otherwise the chat provider — which only
        resolves for a provider that can actually transcribe. Anthropic cannot, and is the
        default, so this raises the ordinary "not configured" 409 there rather than pretending;
        the web surface asks ``enabled_features`` first and simply does not draw a microphone.
        """
        row = await self._settings()
        if row is None:
            raise AppError("ai_not_configured", "errors.ai_not_configured", status_code=409)
        if not _feature_config(row, "time_assist").enabled:
            raise AppError(
                "ai_feature_disabled", "errors.ai_feature_disabled", status_code=409
            )
        provider = row.speech_provider or row.provider
        if not can_transcribe(provider):
            raise AppError(
                "ai_speech_not_configured",
                "errors.ai_speech_not_configured",
                status_code=409,
            )
        encrypted = row.speech_api_key_enc if row.speech_provider else row.api_key_enc
        try:
            api_key = decrypt(encrypted) if encrypted else ""
        except ValueError as exc:
            raise AppError(
                "ai_speech_not_configured",
                "errors.ai_speech_not_configured",
                status_code=409,
            ) from exc
        if not api_key:
            raise AppError(
                "ai_speech_not_configured",
                "errors.ai_speech_not_configured",
                status_code=409,
            )
        base_url = row.speech_base_url if row.speech_provider else row.base_url
        return ProviderConfig(
            provider=provider,
            api_key=api_key,
            model=row.speech_model or DEFAULT_SPEECH_MODEL,
            base_url=base_url,
        )

    async def ensure_audio_budget(self, *, override: bool = False) -> None:
        """The monthly transcription cap, in seconds — its own unit, its own budget."""
        row = await self._settings()
        if row is None or row.monthly_audio_seconds_budget is None:
            return
        start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = await self.ctx.session.scalar(
            select(func.coalesce(func.sum(AIUsage.audio_seconds), 0)).where(
                AIUsage.org_id == self.ctx.org.id, AIUsage.created_at >= start
            )
        )
        if int(spent or 0) >= row.monthly_audio_seconds_budget and not override:
            raise AppError("ai_budget_reached", "errors.ai_budget_reached", status_code=409)

    async def ensure_budget(self, *, override: bool = False) -> None:
        """The monthly soft cap (#126): interactive use over 100 % sits behind an explicit
        acknowledgement (the "budget bereikt" notice); non-interactive callers never pass
        ``override`` and hard-stop."""
        row = await self._settings()
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
        row = await self._settings()
        return row.house_style if row is not None else None

    # ------------------------------------------------------------------ #
    # Model calls
    # ------------------------------------------------------------------ #
    async def record_usage(
        self,
        feature: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        audio_seconds: int = 0,
    ) -> None:
        """Counts and labels only — never content (#126).

        The meter is org-scoped; the person on it is a label. A model call made by the *system*
        — the scheduled report run (#300), driven by an ``events.SystemContext`` whose ``user``
        is ``None`` — therefore books its tokens against nobody, exactly as the activity trail
        records a NULL actor (§16, ``ActivityService.record``). Two shapes of "no person" and
        both must land on ``NULL``: a ``SystemContext`` has no user at all, and
        ``jobs.system_context`` carries a placeholder ``User`` that exists in no ``users`` row —
        safe to resolve against, never safe to store, because ``ai_usage.user_id`` has a FK.

        Reading ``ctx.user.id`` unconditionally is why no scheduled report could ever finish:
        the run gathered, snapshotted and wrote its prose, then died in the ``finally`` that
        meters it — losing a completed document to the bookkeeping about it.
        """
        actor = None if getattr(self.ctx, "is_system", False) else self.ctx.user
        self.ctx.session.add(
            AIUsage(
                org_id=self.ctx.org.id,
                user_id=actor.id if actor else None,
                feature=feature,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                audio_seconds=audio_seconds,
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
        config: ProviderConfig | None = None,
    ) -> tuple[str, list[providers.ToolCall]]:
        """One non-streaming model turn, with the DB connection handed back while it runs.

        A request is one transaction pinning one pooled connection (``app/db.py``). A model
        call takes seconds — a multi-round tool loop, tens of seconds — and holding the
        connection across it is the pool-drain that reads as *the whole site* freezing, not
        just this feature (``docs/PERFORMANCE.md``). ``release_db()`` is the sanctioned seam
        and this is the right place for it: ``complete`` drains the stream itself and runs no
        caller code inside the block, so nothing touches the session while it is unbound. Tool
        handlers run *between* rounds, back on a real connection.

        Gating happens before the block (it needs the session), and usage is accumulated and
        recorded by the caller after it — a write inside would commit at the block's entry.
        ``AIService.stream`` is deliberately left alone: its callers meter per round.

        ``config`` lets a caller that has already gated (the router's ``_preflight``, or an
        earlier round of the same loop) pass the resolved config in and skip re-gating. The
        budget is a monthly figure and a request cannot cross it mid-loop, so re-summing the
        month per round bought nothing.
        """
        if config is None:
            config = await self.config_for(feature)
            await self.ensure_budget(override=override_budget)
        text_parts: list[str] = []
        calls: list[providers.ToolCall] = []
        tokens_in = tokens_out = 0
        stop_reason: str | None = None
        self.last_stop_reason = None
        try:
            async with self.ctx.release_db():
                async for event in providers.stream_chat(
                    config,
                    system=system,
                    messages=messages,
                    tools=tools,
                    force_tool=force_tool,
                    disable_tools=disable_tools,
                    max_tokens=max_tokens,
                ):
                    if event.kind == "text":
                        text_parts.append(event.text)
                    elif event.kind == "tool_call" and event.tool_call is not None:
                        calls.append(event.tool_call)
                    elif event.kind == "done":
                        tokens_in, tokens_out = event.tokens_in, event.tokens_out
                        stop_reason = event.stop_reason
        except AIProviderError as exc:
            logger.warning("AI provider error (%s/%s): %s", config.provider, feature, exc)
            raise AppError(
                "ai_provider_error", "errors.ai_provider_error", status_code=502
            ) from exc
        self.pending_tokens_in += tokens_in
        self.pending_tokens_out += tokens_out
        self.pending_model = config.model
        self.last_stop_reason = stop_reason
        if self.truncated:
            # Logged here rather than per caller, because every one of them is exposed to it and
            # none of them could see it: a cut-off answer is a *short* answer everywhere
            # downstream — a half-written report paragraph, a plan with no fields.
            logger.warning(
                "AI answer truncated (%s/%s, max_tokens=%s, out=%s): the answer is incomplete",
                config.model,
                feature,
                max_tokens,
                tokens_out,
            )
        return "".join(text_parts), calls

    @property
    def truncated(self) -> bool:
        """Did the last :meth:`complete` stop because it ran out of room, rather than finish?"""
        return self.last_stop_reason in providers.TRUNCATED_STOP_REASONS

    async def flush_usage(self, feature: str) -> None:
        """Write the accumulated metering for a multi-round feature as **one** row.

        Counts are what the meter sums, so one row per request and one per round total the
        same; the row count is not itself reported. Call from a ``finally`` — a loop that
        failed halfway still spent the tokens it spent.
        """
        if self.pending_model is None:
            return
        await self.record_usage(
            feature, self.pending_model, self.pending_tokens_in, self.pending_tokens_out
        )
        self.pending_tokens_in = self.pending_tokens_out = 0
        self.pending_model = None


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
            speech_provider=row.speech_provider,  # type: ignore[arg-type]
            speech_base_url=row.speech_base_url,
            speech_model=row.speech_model,
            has_speech_key=bool(row.speech_api_key_enc),
            monthly_audio_seconds_budget=row.monthly_audio_seconds_budget,
            # Resolved here so no client has to know which providers can transcribe.
            speech_available=_speech_ready(row),
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
        # Speech is optional and independent: no speech provider means "reuse the chat one",
        # which resolves only for a provider that can transcribe.
        speech_key = (data.speech_api_key or "").strip()
        speech_base_url = (data.speech_base_url or "").strip() or None
        if data.speech_provider is None:
            speech_key_enc = None  # clearing the provider clears its credential with it
        elif speech_key:
            speech_key_enc = encrypt(speech_key)
        else:
            speech_key_enc = row.speech_api_key_enc if row is not None else None
        if data.speech_provider == "openai_compatible" and not speech_base_url:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"speech_base_url": "errors.required"},
            )
        if data.speech_provider is not None and not speech_key_enc:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"speech_api_key": "errors.required"},
            )

        values = {
            "provider": data.provider,
            "api_key_enc": api_key_enc,
            "base_url": base_url,
            "default_model": default_model,
            "features": features,
            "house_style": (data.house_style or "").strip() or None,
            "monthly_token_budget": data.monthly_token_budget,
            "speech_provider": data.speech_provider,
            "speech_api_key_enc": speech_key_enc,
            "speech_base_url": speech_base_url if data.speech_provider else None,
            "speech_model": ((data.speech_model or "").strip() or None)
            if data.speech_provider
            else None,
            "monthly_audio_seconds_budget": data.monthly_audio_seconds_budget,
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
        provider's failure verbatim (#126) — the email test-button pattern.

        Success means *the key authenticated and a completion came back* — not that the
        model chatted. A reasoning model can spend the whole budget thinking and emit no
        visible text, which used to read as ``ok=False, error=None`` → "Test mislukt: ?"
        (#158). Usage is only metered for a test reported as passed."""
        self.ctx.require("ai.settings.manage")
        row = await get_row(self.ctx.session, self.ctx.org.id)
        if row is None:
            locale = resolve_locale(self.ctx.user.locale)
            return AITestResult(ok=False, error=translate("settings.ai.not_configured", locale))
        try:
            config = ProviderConfig(
                provider=row.provider,
                api_key=decrypt(row.api_key_enc),
                model=row.default_model,
                base_url=row.base_url,
            )
            _, _, done = await providers.complete_chat(
                config,
                system="You are a connection test. Reply with the single word: ok",
                messages=[ChatMessage(role="user", content="ping")],
                max_tokens=256,
            )
        except (AIProviderError, ValueError, OSError, httpx.HTTPError) as exc:
            # httpx errors are not OSError subclasses — without the explicit catch a
            # DNS/timeout failure became a 500 instead of a readable test result.
            return AITestResult(ok=False, error=str(exc) or exc.__class__.__name__)
        service = AIService(self.ctx)
        await service.record_usage("test", row.default_model, done.tokens_in, done.tokens_out)
        return AITestResult(ok=True, model=row.default_model)

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
