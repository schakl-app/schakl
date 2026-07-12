"""Executable AI tools — the ``ModuleDescriptor.mcp_tools`` seam made runnable (#127, §12).

Modules declare :class:`AIToolSpec`s in their ``mcp.py``; the assistant executes them through
this registry, and the P4 MCP server can serve the *same* catalog externally later — one tool
list, two consumers. Every handler runs under the caller's :class:`RequestContext`, so a tool
can never answer beyond the user's role (§15) or tenant (Golden Rule 1): the same service
paths run as for the HTTP request the context came from.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.core.ai.providers import ToolDef
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.registry import registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    """A record a tool answer came from — rendered as a deep-linking chip (#127)."""

    type: str
    id: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id, "label": self.label}


@dataclass(frozen=True)
class ToolResult:
    """What a handler returns: JSON-able data plus the records it was grounded in."""

    data: Any
    sources: tuple[Source, ...] = ()


#: A handler receives the caller's context and the model-supplied arguments; it goes through
#: the module's own tenant-scoped service, never around it.
ToolHandler = Callable[[RequestContext, dict[str, Any]], Awaitable[ToolResult]]


@dataclass(frozen=True)
class AIToolSpec:
    """One executable tool a module contributes on its descriptor's ``mcp_tools``."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    #: Offered to the model only when the caller holds this (any scope satisfies). The
    #: handler's service still enforces the row-level rule — this filter is what keeps a
    #: tool the caller may never use out of the model's view entirely.
    permission: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


def available_tools(ctx: RequestContext) -> list[AIToolSpec]:
    """Every executable tool of the enabled modules the caller may use."""
    specs: list[AIToolSpec] = []
    for module in registry.enabled(settings.enabled_modules):
        for spec in module.mcp_tools:
            if not isinstance(spec, AIToolSpec):
                continue
            if spec.permission is not None and not ctx.can(spec.permission):
                continue
            specs.append(spec)
    return specs


def tool_defs(specs: list[AIToolSpec]) -> list[ToolDef]:
    return [ToolDef(s.name, s.description, s.input_schema) for s in specs]


def get_tool(ctx: RequestContext, name: str) -> AIToolSpec | None:
    for spec in available_tools(ctx):
        if spec.name == name:
            return spec
    return None


async def run_tool(ctx: RequestContext, spec: AIToolSpec, args: dict[str, Any]) -> ToolResult:
    """Execute one tool; failures become data the model can read, never a 500.

    An :class:`AppError` (404 on a row outside the caller's scope, 403 on a refined check)
    is exactly the answer the model should see — "not found" — with the same
    existence-hiding semantics the REST API has (§15).
    """
    try:
        return await spec.handler(ctx, args)
    except AppError as exc:
        return ToolResult(data={"error": exc.message_key})
    except Exception:
        logger.exception("AI tool %s failed", spec.name)
        return ToolResult(data={"error": "errors.ai_tool_failed"})


def result_text(result: ToolResult) -> str:
    """The tool result as the model sees it. Record content rides inside a JSON document —
    data, never instructions; the system prompt states that explicitly (#127)."""
    return json.dumps(result.data, ensure_ascii=False, default=str)
