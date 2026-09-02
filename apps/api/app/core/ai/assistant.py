"""The contextual assistant's tool loop (#127).

One conversation turn = a bounded loop of model calls and tool executions, streamed as
neutral dict events the router turns into SSE. Every tool runs under the caller's
``RequestContext`` — ``ctx.can`` filtered the offered tools, and each handler goes through
the module's own tenant-scoped service, so the assistant can never answer beyond the
caller's role or tenant.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.core.ai import prompts
from app.core.ai.apitools import (
    Forwarding,
    api_tools,
    describe_call,
    modules_summary,
    usable,
)
from app.core.ai.providers import ChatMessage
from app.core.ai.schemas import AssistantRequest
from app.core.ai.service import AIService
from app.core.ai.tools import available_tools, result_text, run_tool, tool_defs
from app.core.timezone import org_today

#: A typical answer costs 1–3 tool invocations (#127); the ceiling is a runaway guard. Eight
#: rather than five since the catalog pair (``apitools``): a question the curated tools do not
#: cover is ``api.find`` → ``api.get`` → maybe a second read → the write the user asked for.
MAX_TOOL_ROUNDS = 8


async def run_assistant(
    service: AIService,
    payload: AssistantRequest,
    *,
    forwarding: Forwarding | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield ``{"event": ..., "data": ...}`` frames for one assistant turn.

    Events: ``text`` (a delta), ``tool`` (a status line: "zoekt in uren…" — for an API call it
    also names the operation, its method and module), ``sources`` (deduped chips, once at the
    end), ``done``.

    ``forwarding`` is the request the turn arrived on (``apitools.forwarding_from``); with it the
    model reaches the whole read surface and the stated writes, without it only the curated
    lookups — which is what an in-process caller with no HTTP request gets.
    """
    ctx = service.ctx
    specs = available_tools(ctx)
    surface: str | None = None
    if forwarding is not None:
        specs = [*specs, *api_tools(ctx, forwarding)]
        surface = prompts.assistant_surface(
            modules=modules_summary(ctx, forwarding.app),
            writes=[op.name for op in usable(ctx, forwarding.app) if op.write],
        )
    by_name = {spec.name: spec for spec in specs}

    context_line = None
    if payload.context is not None:
        label = f' "{payload.context.label}"' if payload.context.label else ""
        context_line = (
            f"The user is currently looking at the {payload.context.entity_type}"
            f"{label} with id {payload.context.entity_id}. When the question says "
            '"this client/project/task" or gives no other subject, it means that record.'
        )
    system = prompts.assistant_system(
        locale=service.locale(),
        brand=ctx.org.name,
        today=await org_today(ctx.session, ctx.org.id),
        context_line=context_line,
        surface=surface,
    )

    history: list[ChatMessage] = [
        ChatMessage(role=m.role, content=m.content) for m in payload.messages
    ]
    sources: dict[tuple[str, str], dict[str, str]] = {}

    offer = tool_defs(specs) or None
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        # The final round must answer with what it has: tools stay on the request (a
        # history holding tool blocks is invalid without them) but new calls are forbidden.
        text_parts: list[str] = []
        calls = []
        async for event in service.stream(
            "assistant",
            system=system,
            messages=history,
            tools=offer,
            disable_tools=round_no >= MAX_TOOL_ROUNDS,
            override_budget=payload.override_budget,
        ):
            if event.kind == "text":
                text_parts.append(event.text)
                yield {"event": "text", "data": {"text": event.text}}
            elif event.kind == "tool_call" and event.tool_call is not None:
                calls.append(event.tool_call)
        if not calls:
            break
        history.append(
            ChatMessage(role="assistant", content="".join(text_parts), tool_calls=tuple(calls))
        )
        for call in calls:
            spec = by_name.get(call.name)
            detail = (
                describe_call(forwarding.app, call.name, call.input)
                if forwarding is not None
                else {"name": call.name}
            )
            yield {"event": "tool", "data": detail}
            if spec is None:
                content = '{"error": "unknown tool"}'
            else:
                result = await run_tool(ctx, spec, call.input)
                for source in result.sources:
                    sources[(source.type, source.id)] = source.as_dict()
                content = result_text(result)
            history.append(ChatMessage(role="tool", content=content, tool_call_id=call.id))

    if sources:
        yield {"event": "sources", "data": {"sources": list(sources.values())}}
    yield {"event": "done", "data": {}}
