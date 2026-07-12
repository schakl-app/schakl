"""Provider adapters (#126): Anthropic, OpenAI, and OpenAI-compatible, over raw httpx.

No provider SDKs on purpose — the email and notification cores hit provider HTTP APIs the
same way, and two small adapters beat two heavyweight dependencies for the four calls we
make. Everything is normalised to one neutral event stream so ``AIService`` and every
feature see a single shape regardless of provider.

Model choice is a tenant setting with a per-provider default and a free-text override —
never a hardcoded list that rots (#126).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_BASE_URL = "https://api.openai.com/v1"

#: Sensible starting points; the settings form pre-fills them and the tenant can type
#: anything newer. ``openai_compatible`` has no default — the server defines the models.
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5",
}

#: Ceiling per response. Our outputs are short (a digest, a rewritten paragraph); a runaway
#: completion should stop well before it becomes a bill.
MAX_TOKENS = 8192

_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


class AIProviderError(Exception):
    """The provider refused or failed; ``str(exc)`` carries its own message verbatim."""


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    api_key: str
    model: str
    base_url: str | None = None


@dataclass(frozen=True)
class ToolDef:
    """A tool as offered to the model — name, description, JSON schema for the input."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    """Neutral conversation turn; adapters map it to their wire format.

    ``role`` is ``user`` | ``assistant`` | ``tool`` (a tool result, carrying
    ``tool_call_id``). An assistant turn may carry ``tool_calls`` next to its text.
    """

    role: str
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class AIEvent:
    """One normalised stream event.

    kind ∈ ``text`` (a delta in ``text``), ``tool_call`` (a completed call in ``tool_call``),
    ``done`` (``stop_reason`` + final token counts).
    """

    kind: str
    text: str = ""
    tool_call: ToolCall | None = None
    stop_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class _Usage:
    tokens_in: int = 0
    tokens_out: int = 0


async def stream_chat(
    config: ProviderConfig,
    *,
    system: str,
    messages: list[ChatMessage],
    tools: list[ToolDef] | None = None,
    force_tool: str | None = None,
    max_tokens: int = MAX_TOKENS,
) -> AsyncIterator[AIEvent]:
    """Stream one model turn as normalised :class:`AIEvent`s.

    ``force_tool`` makes the named tool the only acceptable answer (structured output —
    the time quick-add parse). Raises :class:`AIProviderError` with the provider's own
    message on any non-2xx or malformed stream.
    """
    if config.provider == "anthropic":
        iterator = _anthropic_stream(
            config, system=system, messages=messages, tools=tools,
            force_tool=force_tool, max_tokens=max_tokens,
        )
    elif config.provider in ("openai", "openai_compatible"):
        iterator = _openai_stream(
            config, system=system, messages=messages, tools=tools,
            force_tool=force_tool, max_tokens=max_tokens,
        )
    else:  # pragma: no cover - settings validation prevents this
        raise AIProviderError(f"unknown provider {config.provider!r}")
    async for event in iterator:
        yield event


async def complete_chat(
    config: ProviderConfig,
    *,
    system: str,
    messages: list[ChatMessage],
    tools: list[ToolDef] | None = None,
    force_tool: str | None = None,
    max_tokens: int = MAX_TOKENS,
) -> tuple[str, list[ToolCall], AIEvent]:
    """Non-streaming convenience over :func:`stream_chat`.

    Returns ``(text, tool_calls, done_event)`` — one code path for both shapes.
    """
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    done = AIEvent(kind="done")
    async for event in stream_chat(
        config, system=system, messages=messages, tools=tools,
        force_tool=force_tool, max_tokens=max_tokens,
    ):
        if event.kind == "text":
            text_parts.append(event.text)
        elif event.kind == "tool_call" and event.tool_call is not None:
            calls.append(event.tool_call)
        elif event.kind == "done":
            done = event
    return "".join(text_parts), calls, done


async def _sse_data_lines(response: httpx.Response) -> AsyncIterator[str]:
    """Yield the ``data:`` payloads of an SSE byte stream, event by event."""
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip("\r")
            if line.startswith("data:"):
                yield line[5:].strip()


async def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body = (await response.aread()).decode("utf-8", "replace")
    try:
        payload = json.loads(body)
        error = payload.get("error") or {}
        message = error.get("message") or body
    except ValueError:
        message = body
    raise AIProviderError(f"HTTP {response.status_code}: {message[:500]}")


async def list_models(config: ProviderConfig) -> list[str]:
    """The model ids the configured provider currently serves — fetched live, so the
    settings picker never carries a hardcoded list that rots (#126). Every provider type
    exposes one: Anthropic ``GET /v1/models``, OpenAI ``GET /models``, and the
    OpenAI-compatible servers (Ollama, vLLM, Mistral, Azure) implement the same shape."""
    if config.provider == "anthropic":
        base = (config.base_url or ANTHROPIC_BASE_URL).rstrip("/")
        url = f"{base}/v1/models?limit=100"
        headers = {"x-api-key": config.api_key, "anthropic-version": ANTHROPIC_VERSION}
    else:
        base = (config.base_url or OPENAI_BASE_URL).rstrip("/")
        url = f"{base}/models"
        headers = {"authorization": f"Bearer {config.api_key}"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(url, headers=headers)
        await _raise_for_status(response)
        payload = response.json()
    models = [
        str(row["id"])
        for row in payload.get("data") or []
        if isinstance(row, dict) and row.get("id")
    ]
    # Anthropic returns newest first — keep that; the OpenAI list is a grab bag, so sort it.
    if config.provider != "anthropic":
        models.sort()
    return models[:200]


# --------------------------------------------------------------------------- #
# Anthropic Messages API
# --------------------------------------------------------------------------- #
def _anthropic_messages(messages: list[ChatMessage]) -> list[dict]:
    """Map the neutral history to Anthropic blocks; consecutive tool results merge into
    one user turn (the API requires all results of a parallel call in a single message)."""
    wire: list[dict] = []
    for msg in messages:
        if msg.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": msg.content,
            }
            if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list):
                wire[-1]["content"].append(block)
            else:
                wire.append({"role": "user", "content": [block]})
        elif msg.role == "assistant" and msg.tool_calls:
            blocks: list[dict] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            blocks.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                for c in msg.tool_calls
            )
            wire.append({"role": "assistant", "content": blocks})
        else:
            wire.append({"role": msg.role, "content": msg.content})
    return wire


async def _anthropic_stream(
    config: ProviderConfig,
    *,
    system: str,
    messages: list[ChatMessage],
    tools: list[ToolDef] | None,
    force_tool: str | None,
    max_tokens: int,
) -> AsyncIterator[AIEvent]:
    body: dict[str, Any] = {
        "model": config.model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": _anthropic_messages(messages),
        "stream": True,
    }
    if tools:
        body["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        if force_tool:
            body["tool_choice"] = {"type": "tool", "name": force_tool}
    base = (config.base_url or ANTHROPIC_BASE_URL).rstrip("/")
    headers = {
        "x-api-key": config.api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    usage = _Usage()
    stop_reason: str | None = None
    # One partial tool-use block at a time, keyed by stream index.
    open_tools: dict[int, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream(
            "POST", f"{base}/v1/messages", headers=headers, json=body
        ) as response:
            await _raise_for_status(response)
            async for data in _sse_data_lines(response):
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                etype = event.get("type")
                if etype == "message_start":
                    usage.tokens_in = (
                        event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                    )
                elif etype == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        open_tools[event["index"]] = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "json": "",
                        }
                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield AIEvent(kind="text", text=delta.get("text", ""))
                    elif delta.get("type") == "input_json_delta":
                        partial = open_tools.get(event.get("index"))
                        if partial is not None:
                            partial["json"] += delta.get("partial_json", "")
                elif etype == "content_block_stop":
                    partial = open_tools.pop(event.get("index"), None)
                    if partial is not None:
                        try:
                            args = json.loads(partial["json"]) if partial["json"] else {}
                        except ValueError:
                            args = {}
                        yield AIEvent(
                            kind="tool_call",
                            tool_call=ToolCall(partial["id"], partial["name"], args),
                        )
                elif etype == "message_delta":
                    stop_reason = event.get("delta", {}).get("stop_reason") or stop_reason
                    usage.tokens_out = event.get("usage", {}).get(
                        "output_tokens", usage.tokens_out
                    )
                elif etype == "error":
                    error = event.get("error", {})
                    raise AIProviderError(error.get("message", "provider error"))
    yield AIEvent(
        kind="done",
        stop_reason=stop_reason,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
    )


# --------------------------------------------------------------------------- #
# OpenAI chat-completions API (and any server speaking it)
# --------------------------------------------------------------------------- #
def _openai_messages(system: str, messages: list[ChatMessage]) -> list[dict]:
    wire: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        if msg.role == "tool":
            wire.append(
                {"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content}
            )
        elif msg.role == "assistant" and msg.tool_calls:
            wire.append(
                {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.input)},
                        }
                        for c in msg.tool_calls
                    ],
                }
            )
        else:
            wire.append({"role": msg.role, "content": msg.content})
    return wire


@dataclass
class _OpenAIToolPartial:
    id: str = ""
    name: str = ""
    arguments: str = ""


async def _openai_stream(
    config: ProviderConfig,
    *,
    system: str,
    messages: list[ChatMessage],
    tools: list[ToolDef] | None,
    force_tool: str | None,
    max_tokens: int,
) -> AsyncIterator[AIEvent]:
    body: dict[str, Any] = {
        "model": config.model,
        "messages": _openai_messages(system, messages),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    # Newer OpenAI models reject ``max_tokens``; compatible servers largely still expect it.
    if config.provider == "openai":
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
        if force_tool:
            body["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
    base = (config.base_url or OPENAI_BASE_URL).rstrip("/")
    headers = {
        "authorization": f"Bearer {config.api_key}",
        "content-type": "application/json",
    }
    usage = _Usage()
    stop_reason: str | None = None
    partials: dict[int, _OpenAIToolPartial] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream(
            "POST", f"{base}/chat/completions", headers=headers, json=body
        ) as response:
            await _raise_for_status(response)
            async for data in _sse_data_lines(response):
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    usage.tokens_in = chunk["usage"].get("prompt_tokens", usage.tokens_in)
                    usage.tokens_out = chunk["usage"].get(
                        "completion_tokens", usage.tokens_out
                    )
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield AIEvent(kind="text", text=delta["content"])
                for call in delta.get("tool_calls") or []:
                    partial = partials.setdefault(call.get("index", 0), _OpenAIToolPartial())
                    if call.get("id"):
                        partial.id = call["id"]
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        partial.name += fn["name"]
                    if fn.get("arguments"):
                        partial.arguments += fn["arguments"]
                if choice.get("finish_reason"):
                    stop_reason = choice["finish_reason"]
    for partial in partials.values():
        try:
            args = json.loads(partial.arguments) if partial.arguments else {}
        except ValueError:
            args = {}
        yield AIEvent(kind="tool_call", tool_call=ToolCall(partial.id, partial.name, args))
    yield AIEvent(
        kind="done",
        stop_reason="tool_use" if partials and stop_reason == "tool_calls" else stop_reason,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
    )
