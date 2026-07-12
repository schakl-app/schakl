"""REST endpoints for the AI core and its features under ``/api/v1/ai`` (#126–#130).

The whole router is mounted behind ``license_write_gate(AI_SKU)`` (issue #137): every
generation is a POST, so an uncovered instance keeps its stored settings and usage readable
while generation stops — the licensed-module semantics, applied to a core surface.

Streaming endpoints resolve configuration and budget *before* the stream starts, so a
misconfigured tenant gets the ordinary 409 envelope; a mid-stream failure becomes an
``error`` SSE event (headers are long gone by then).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.ai.assistant import run_assistant
from app.core.ai.features import (
    ReportsService,
    gather_company_facts,
    parse_time_entry,
    reconstruct_day,
    stream_digest,
    stream_report,
    stream_writing_assist,
)
from app.core.ai.schemas import (
    AIModelsRequest,
    AIModelsResult,
    AISettingsRead,
    AISettingsWrite,
    AITestResult,
    AIUsageSummary,
    AssistantRequest,
    DigestRequest,
    ReportCreate,
    ReportGenerateRequest,
    ReportRead,
    ReportUpdate,
    TimeParseRequest,
    TimeParseResult,
    TimeReconstructRequest,
    TimeReconstructResult,
    WritingAssistRequest,
)
from app.core.ai.service import AIService, AISettingsService
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse_body(frames: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Serialise event frames, converting mid-stream failures into an ``error`` event."""
    try:
        async for frame in frames:
            yield _sse(frame["event"], frame["data"])
    except AppError as exc:
        yield _sse("error", {"code": exc.code, "message": exc.message_key})
    except Exception:
        logger.exception("AI stream failed")
        yield _sse("error", {"code": "ai_provider_error", "message": "errors.ai_provider_error"})


def _stream_response(frames: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    return StreamingResponse(
        _sse_body(frames),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


async def _preflight(service: AIService, feature: str, *, override_budget: bool) -> None:
    """Raise the 409s (not configured / feature off / budget) before headers go out."""
    await service.config_for(feature)
    await service.ensure_budget(override=override_budget)


# --------------------------------------------------------------------------- #
# Settings (Instellingen → AI, #126)
# --------------------------------------------------------------------------- #
@router.get(
    "/settings",
    response_model=AISettingsRead | None,
    dependencies=[require_permission("ai.settings.manage")],
)
async def get_ai_settings(
    ctx: RequestContext = Depends(require_context),
) -> AISettingsRead | None:
    return await AISettingsService(ctx).get()


@router.put(
    "/settings",
    response_model=AISettingsRead,
    dependencies=[require_permission("ai.settings.manage")],
)
async def save_ai_settings(
    payload: AISettingsWrite, ctx: RequestContext = Depends(require_context)
) -> AISettingsRead:
    return await AISettingsService(ctx).save(payload)


@router.delete(
    "/settings",
    status_code=204,
    dependencies=[require_permission("ai.settings.manage")],
)
async def delete_ai_settings(ctx: RequestContext = Depends(require_context)) -> None:
    await AISettingsService(ctx).delete()


@router.post(
    "/settings/test",
    response_model=AITestResult,
    dependencies=[require_permission("ai.settings.manage")],
)
async def test_ai_settings(ctx: RequestContext = Depends(require_context)) -> AITestResult:
    return await AISettingsService(ctx).test()


@router.post(
    "/settings/models",
    response_model=AIModelsResult,
    dependencies=[require_permission("ai.settings.manage")],
)
async def list_ai_models(
    payload: AIModelsRequest, ctx: RequestContext = Depends(require_context)
) -> AIModelsResult:
    """Live model listing for the settings picker — a settings-page helper like the test
    button, so a provider failure comes back as data, never a 500."""
    return await AISettingsService(ctx).list_models(payload)


@router.get(
    "/usage",
    response_model=AIUsageSummary,
    dependencies=[require_permission("ai.settings.manage")],
)
async def ai_usage(ctx: RequestContext = Depends(require_context)) -> AIUsageSummary:
    return await AISettingsService(ctx).usage()


# --------------------------------------------------------------------------- #
# Writing assist (#128)
# --------------------------------------------------------------------------- #
@router.post("/assist/write", dependencies=[require_permission("ai.use")])
async def writing_assist(
    payload: WritingAssistRequest, ctx: RequestContext = Depends(require_context)
) -> StreamingResponse:
    service = AIService(ctx)
    await _preflight(service, "writing_assist", override_budget=payload.override_budget)
    return _stream_response(stream_writing_assist(service, payload))


# --------------------------------------------------------------------------- #
# Contextual assistant (#127)
# --------------------------------------------------------------------------- #
@router.post("/assistant", dependencies=[require_permission("ai.use")])
async def assistant(
    payload: AssistantRequest, ctx: RequestContext = Depends(require_context)
) -> StreamingResponse:
    service = AIService(ctx)
    await _preflight(service, "assistant", override_budget=payload.override_budget)
    return _stream_response(run_assistant(service, payload))


# --------------------------------------------------------------------------- #
# Time assist (#129)
# --------------------------------------------------------------------------- #
@router.post(
    "/time/parse",
    response_model=TimeParseResult,
    dependencies=[require_permission("ai.use")],
)
async def time_parse(
    payload: TimeParseRequest, ctx: RequestContext = Depends(require_context)
) -> TimeParseResult:
    service = AIService(ctx)
    await _preflight(service, "time_assist", override_budget=payload.override_budget)
    return await parse_time_entry(service, payload)


@router.post(
    "/time/reconstruct",
    response_model=TimeReconstructResult,
    dependencies=[require_permission("ai.use")],
)
async def time_reconstruct(
    payload: TimeReconstructRequest, ctx: RequestContext = Depends(require_context)
) -> TimeReconstructResult:
    service = AIService(ctx)
    await _preflight(service, "time_assist", override_budget=payload.override_budget)
    return await reconstruct_day(service, payload)


# --------------------------------------------------------------------------- #
# Client digest + report drafts (#130)
# --------------------------------------------------------------------------- #
@router.post("/companies/{company_id}/digest", dependencies=[require_permission("ai.use")])
async def company_digest(
    company_id: uuid.UUID,
    payload: DigestRequest,
    ctx: RequestContext = Depends(require_context),
) -> StreamingResponse:
    service = AIService(ctx)
    await _preflight(service, "reporting", override_budget=payload.override_budget)
    # Facts are assembled up front so a company outside the caller's tenant/permissions is
    # an ordinary 404 envelope, never a half-open stream.
    facts, sources = await gather_company_facts(service, company_id)
    return _stream_response(
        stream_digest(
            service, company_id, facts, sources, override_budget=payload.override_budget
        )
    )


@router.post("/reports/generate", dependencies=[require_permission("ai.use")])
async def generate_report(
    payload: ReportGenerateRequest, ctx: RequestContext = Depends(require_context)
) -> StreamingResponse:
    service = AIService(ctx)
    await _preflight(service, "reporting", override_budget=payload.override_budget)
    return _stream_response(stream_report(service, payload))


@router.get(
    "/reports",
    response_model=list[ReportRead],
    dependencies=[require_permission("ai.use")],
)
async def list_reports(
    company_id: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(require_context),
) -> list[ReportRead]:
    return await ReportsService(ctx).list(company_id)


@router.post(
    "/reports",
    response_model=ReportRead,
    status_code=201,
    dependencies=[require_permission("ai.use")],
)
async def create_report(
    payload: ReportCreate, ctx: RequestContext = Depends(require_context)
) -> ReportRead:
    return await ReportsService(ctx).create(payload)


@router.get(
    "/reports/{report_id}",
    response_model=ReportRead,
    dependencies=[require_permission("ai.use")],
)
async def get_report(
    report_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> ReportRead:
    return await ReportsService(ctx).get(report_id)


@router.put(
    "/reports/{report_id}",
    response_model=ReportRead,
    dependencies=[require_permission("ai.use")],
)
async def update_report(
    report_id: uuid.UUID,
    payload: ReportUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ReportRead:
    return await ReportsService(ctx).update(report_id, payload)


@router.delete(
    "/reports/{report_id}",
    status_code=204,
    dependencies=[require_permission("ai.use")],
)
async def delete_report(
    report_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await ReportsService(ctx).delete(report_id)
