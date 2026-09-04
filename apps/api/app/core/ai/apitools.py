"""The assistant's reach: every read the caller may make, and a stated list of writes.

The in-app assistant (#127) shipped with a handful of curated lookups and the sentence *"you
are read-only"*. Both halves were right for a first release and wrong for the product it sits
in: §12 already makes **every** ``/api/v1`` operation an MCP tool, so an agent in Claude
Desktop could read the domain register, the leave roster and a client's Google Ads spend while
the assistant built into the very same screen could not — and it could answer "what is due
this week" but not "then make a task for it", which is the sentence that follows.

Three decisions, and each is a rule rather than a feature.

**The read surface is the MCP surface, reached through a catalog rather than listed.** Offering
~600 tools to a chat model costs the whole context on every turn (``docs/MCP.md``, the ChatGPT
budget), so the model gets two tools instead: ``api.find`` searches the operations it may use
and answers with their input shape, ``api.get`` calls one. What is *in* the catalog is derived
from the same three facts the MCP server derives it from — the OpenAPI document, the route's
declared permission (§15's marker, read with :func:`route_markers`), and the MCP route maps'
exclusions — so a route added tomorrow is searchable tomorrow, and a route the caller may not
call is not in their catalog at all. ``ctx.can`` filters the offer; the route's own dependency
refuses the call. Both are needed and neither is sufficient (§15, the marketing tools' rule).

**A write is a named tool from a closed list, never a catalog entry.** ``ASSISTANT_WRITES`` is
the whole answer to "what may the assistant change": ordinary daily work — a task, a comment,
hours, the timer. Nothing that configures the tenant, nothing that leaves the building (a sent
invoice, a published report), nothing that points at somebody else's account. Each is offered
as its own tool with the route's own request schema as its input, gated on the route's own
permission, so the model sees ``create_task`` with the shape ``POST /tasks`` actually accepts —
no hand-written twin to drift. The list is stated here, tested against the route table, and
printed in ``docs/AI.md``; widening it is a decision, not a search result.

**The call is the HTTP request it stands for.** Every tool call travels the ASGI app
in-process with the caller's own credential and host forwarded — the MCP proxy's shape
(``app/core/mcp/server.py``), one request over — so ``require_context`` resolves the tenant,
binds RLS, resolves permissions and the company horizon exactly as for the request the
assistant itself arrived on. A write through here fires the same validation, activity trail,
events and licence gate a form submit would (§18's argument for a bulk edit, restated for a
model). The caller's session and credential are forwarded and **never** the incoming MCP
credential to anything external — the inner request is our own app.

Two smaller ones. The outer request's DB connection is handed back around the inner call
(``ctx.release_db``), because the inner request checks out its own and a tool loop that holds
two per round is §11's pool drain twice over. And a result is capped: a list answer is cut to
what fits and **says how many rows it did not show**, because a prefix that looks complete is
§17's failure with a model reading it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Request

from app.core.ai.tools import AIToolSpec, Source, ToolResult
from app.core.mcp.sections import _module_prefixes
from app.core.mcp.server import _becomes_a_tool, _tool_index
from app.core.permissions.deps import operation_index, route_markers
from app.core.tenancy import RequestContext, request_hostname

#: ``(method, path)`` of every operation the assistant may **write** through. The closed list.
#: Everything else the model can reach is a ``GET``.
ASSISTANT_WRITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("post", "/api/v1/tasks"),
        ("patch", "/api/v1/tasks/{task_id}"),
        ("post", "/api/v1/tasks/{task_id}/comments"),
        ("post", "/api/v1/time/entries"),
        ("post", "/api/v1/time/timer/start"),
        ("post", "/api/v1/time/timer/stop"),
    }
)

#: ``GET``s that answer with bytes or a spreadsheet rather than a document a model can read,
#: plus the AI surface itself (an assistant asking the assistant is a loop with a bill).
_NOT_READABLE = re.compile(
    r"/(export|pdf|ubl|thumbnail|logo|snippet|download)$|^/api/v1/files/\{|^/api/v1/ai/"
)

#: How much of a tool answer the model is shown. Past this a list is cut and says so.
MAX_RESULT_CHARS = 24_000
#: How many catalog hits a search answers with.
FIND_LIMIT = 10
#: Timeout for the in-process request. A read may itself wait on Google (20 s there).
CALL_TIMEOUT_SECONDS = 60.0

#: Router prefix → the source type the web resolves to a deep link (``core/ai/index.ts``).
_SOURCE_TYPES = {
    "tasks": "task",
    "companies": "company",
    "projects": "project",
    "contacts": "contact",
}

#: Headers copied from the assistant's own request onto every in-process call: the credential
#: (a session cookie for the web app, a bearer key for an MCP caller) and the tenant host.
_FORWARDED = ("cookie", "authorization", "x-api-key", "x-forwarded-proto")


@dataclass(frozen=True)
class Forwarding:
    """What an in-process call needs from the request the assistant arrived on."""

    app: Any
    headers: dict[str, str]


def forwarding_from(request: Request) -> Forwarding:
    headers = {
        name: value for name in _FORWARDED if (value := request.headers.get(name)) is not None
    }
    # ``resolve_org`` prefers ``X-Forwarded-Host``; the raw ``Host`` of an in-process call is
    # the fake base_url below.
    headers["x-forwarded-host"] = request_hostname(request)
    return Forwarding(app=request.app, headers=headers)


@dataclass(frozen=True)
class ApiOperation:
    """One operation the assistant may call, joined to what gates it."""

    name: str
    method: str
    path: str
    module: str | None
    summary: str
    description: str
    permission: str | None
    parameters: tuple[dict[str, Any], ...]
    body: dict[str, Any] | None

    @property
    def write(self) -> bool:
        return self.method != "get"

    @property
    def path_params(self) -> tuple[str, ...]:
        return tuple(re.findall(r"\{(\w+)\}", self.path))

    def brief(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method.upper(),
            "path": self.path,
            "module": self.module,
            "summary": self.summary,
        }

    def detail(self) -> dict[str, Any]:
        out = self.brief()
        if self.description:
            out["description"] = self.description
        if self.parameters:
            out["parameters"] = list(self.parameters)
        if self.body is not None:
            out["body"] = self.body
        return out

    def input_schema(self) -> dict[str, Any]:
        """A write tool's input: the path parameters and the body's properties, flattened."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name in self.path_params:
            properties[name] = {"type": "string", "description": f"Path parameter {name}."}
            required.append(name)
        body = self.body or {}
        for key, schema in (body.get("properties") or {}).items():
            properties[key] = schema
        required.extend(k for k in body.get("required") or () if k not in required)
        return {"type": "object", "properties": properties, "required": required}


# --------------------------------------------------------------------------- #
# The index — built once per process from the app's own route table
# --------------------------------------------------------------------------- #
_DESCRIPTION_CHARS = 280
_SCHEMA_DEPTH = 6


def _compact(schema: Any, components: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """A JSON schema the model can read: refs inlined, titles and examples dropped, nullable
    unions folded. Depth-capped because a recursive schema (a report's sections) has no
    bottom, and a model does not need one."""
    if not isinstance(schema, dict):
        return {"type": "object"}
    if depth > _SCHEMA_DEPTH:
        return {"type": "object"}
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        return _compact(components.get(name, {}), components, depth + 1)
    out: dict[str, Any] = {}
    for key in (
        "type",
        "enum",
        "format",
        "default",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "required",
    ):
        if key in schema:
            out[key] = schema[key]
    description = schema.get("description")
    if isinstance(description, str) and description.strip():
        out["description"] = " ".join(description.split())[:_DESCRIPTION_CHARS]
    if isinstance(schema.get("properties"), dict):
        out["properties"] = {
            k: _compact(v, components, depth + 1) for k, v in schema["properties"].items()
        }
        out.setdefault("type", "object")
    if "items" in schema:
        out["items"] = _compact(schema["items"], components, depth + 1)
        out.setdefault("type", "array")
    for union in ("anyOf", "oneOf"):
        if isinstance(schema.get(union), list):
            variants = [_compact(v, components, depth + 1) for v in schema[union]]
            nullable = any(v.get("type") == "null" for v in variants)
            variants = [v for v in variants if v.get("type") != "null"]
            if len(variants) == 1:
                merged = {**variants[0], **{k: v for k, v in out.items() if k != "type"}}
                if nullable:
                    merged["nullable"] = True
                return merged
            out[union] = variants
            if nullable:
                out["nullable"] = True
    if isinstance(schema.get("allOf"), list):
        for part in schema["allOf"]:
            merged = _compact(part, components, depth + 1)
            props = {**out.get("properties", {}), **merged.pop("properties", {})}
            out.update(merged)
            if props:
                out["properties"] = props
    return out


def _module_of(path: str, prefixes: dict[str, str]) -> str | None:
    for name, prefix in prefixes.items():
        if path == prefix or path.startswith(f"{prefix}/"):
            return name
    return None


def build_index(app: Any) -> list[ApiOperation]:
    """Every operation the assistant could ever call — before the caller's permissions."""
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    names, _ = _tool_index(app)
    prefixes = _module_prefixes()
    operations, _ = operation_index(app)
    index: list[ApiOperation] = []
    for op in operations:
        if not op.path.startswith("/api/v1/"):
            continue
        write = (op.method, op.path) in ASSISTANT_WRITES
        if not write and (
            op.method != "get"
            or not _becomes_a_tool(op.path, "GET")
            or _NOT_READABLE.search(op.path)
        ):
            continue
        doc = spec["paths"][op.path][op.method]
        permissions, _exemptions = route_markers(op.route)
        parameters = tuple(
            {
                "name": p["name"],
                "in": p.get("in", "query"),
                "required": bool(p.get("required")),
                **(
                    {"description": " ".join(p["description"].split())[:_DESCRIPTION_CHARS]}
                    if p.get("description")
                    else {}
                ),
                "schema": _compact(p.get("schema", {}), components),
            }
            for p in doc.get("parameters", [])
            if p.get("in") in ("query", "path")
        )
        body = None
        request_body = doc.get("requestBody", {}).get("content", {})
        json_body = request_body.get("application/json")
        if json_body is not None:
            body = _compact(json_body.get("schema", {}), components)
        index.append(
            ApiOperation(
                name=names.get(op.operation_id, op.operation_id),
                method=op.method,
                path=op.path,
                module=_module_of(op.path, prefixes),
                summary=" ".join(str(doc.get("summary", "")).split()),
                description=" ".join(str(doc.get("description", "")).split())[:_DESCRIPTION_CHARS],
                permission=permissions[0][0] if permissions else None,
                parameters=parameters,
                body=body,
            )
        )
    return index


def index_for(app: Any) -> list[ApiOperation]:
    """The index, built on first use and kept on the app: ``app.openapi()`` walks the whole
    route table and is not something to repeat per chat turn."""
    cached = getattr(app.state, "ai_api_index", None)
    if cached is None:
        cached = build_index(app)
        app.state.ai_api_index = cached
    return cached


def allowed(ctx: RequestContext, op: ApiOperation) -> bool:
    """Offered to this caller? The route's declared permission, at any scope — the service
    still refines ``:own``/``:any`` on the row, exactly as for the HTTP request."""
    return op.permission is None or ctx.can(op.permission)


def usable(ctx: RequestContext, app: Any) -> list[ApiOperation]:
    return [op for op in index_for(app) if allowed(ctx, op)]


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1]


def _score(op: ApiOperation, tokens: list[str]) -> int:
    name = op.name.lower()
    path = op.path.lower()
    summary = op.summary.lower()
    description = op.description.lower()
    module = (op.module or "").lower()
    score = 0
    for token in tokens:
        if token in name:
            score += 4
        if token in module:
            score += 3
        if token in path:
            score += 2
        if token in summary:
            score += 2
        if token in description:
            score += 1
    return score


def find(
    ctx: RequestContext, app: Any, query: str, module: str | None = None
) -> list[ApiOperation]:
    ops = usable(ctx, app)
    if module:
        wanted = module.strip().lower().replace("-", "_")
        ops = [op for op in ops if (op.module or "").replace("-", "_") == wanted]
    tokens = _tokens(query or "")
    if not tokens:
        return sorted(ops, key=lambda op: (op.module or "", op.name))[:FIND_LIMIT]
    scored = [(op, _score(op, tokens)) for op in ops]
    scored = [(op, s) for op, s in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[1], pair[0].write, pair[0].name))
    return [op for op, _ in scored[:FIND_LIMIT]]


def modules_summary(ctx: RequestContext, app: Any) -> str:
    """One line per module the caller can read anything of — for the system prompt, so the
    model searches the right shelf rather than guessing whether a module exists here."""
    counts: dict[str, int] = {}
    for op in usable(ctx, app):
        if not op.write:
            counts[op.module or "core"] = counts.get(op.module or "core", 0) + 1
    return ", ".join(f"{name} ({n})" for name, n in sorted(counts.items()))


# --------------------------------------------------------------------------- #
# Calling
# --------------------------------------------------------------------------- #
def _sources(op: ApiOperation, payload: Any) -> tuple[Source, ...]:
    """Chips for what was read or written, where the record type has a page to link to."""
    source_type = _SOURCE_TYPES.get(op.module or "")
    if source_type is None:
        return ()
    rows: list[Any]
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        rows = payload["items"][:8]
    elif isinstance(payload, list):
        rows = payload[:8]
    elif isinstance(payload, dict):
        rows = [payload]
    else:
        return ()
    sources = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        label = row.get("title") or row.get("name") or ""
        if isinstance(label, str) and label:
            sources.append(Source(type=source_type, id=str(row["id"]), label=label))
    return tuple(sources)


def _fit(payload: Any) -> tuple[Any, dict[str, Any] | None]:
    """Cut a long answer to what fits, and say what was left out."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return payload, None
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = list(payload["items"])
        total = len(items)
        while len(items) > 1 and (
            len(json.dumps({**payload, "items": items}, default=str)) > MAX_RESULT_CHARS
        ):
            items = items[: max(1, len(items) * 3 // 4)]
        return {**payload, "items": items}, {"rows_shown": len(items), "rows_in_answer": total}
    if isinstance(payload, list):
        items = list(payload)
        total = len(items)
        while len(items) > 1 and len(json.dumps(items, default=str)) > MAX_RESULT_CHARS:
            items = items[: max(1, len(items) * 3 // 4)]
        return items, {"rows_shown": len(items), "rows_in_answer": total}
    return text[:MAX_RESULT_CHARS], {"characters_shown": MAX_RESULT_CHARS, "characters": len(text)}


async def call(
    ctx: RequestContext,
    forwarding: Forwarding,
    op: ApiOperation,
    *,
    path_params: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    body: Any = None,
) -> ToolResult:
    """One in-process request, with the caller's credential, through every gate the route has."""
    url = op.path
    given = path_params or {}
    for name in op.path_params:
        value = given.get(name)
        if value in (None, ""):
            return ToolResult(
                data={"error": "missing_path_parameter", "parameter": name, "path": op.path}
            )
        url = url.replace("{" + name + "}", quote(str(value), safe=""))
    params = {k: v for k, v in (query or {}).items() if v is not None} or None
    async with ctx.release_db():
        transport = httpx.ASGITransport(app=forwarding.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://schakl.internal",
            timeout=CALL_TIMEOUT_SECONDS,
        ) as client:
            response = await client.request(
                op.method.upper(),
                url,
                params=params,
                json=body if op.write else None,
                headers=forwarding.headers,
            )
    content_type = response.headers.get("content-type", "")
    if response.status_code == 204:
        return ToolResult(data={"status": 204, "result": None})
    if "json" not in content_type:
        return ToolResult(
            data={
                "error": "not_readable",
                "status": response.status_code,
                "content_type": content_type,
                "note": "This operation answers with a file, not a document; tell the user where "
                "to download it instead.",
            }
        )
    try:
        payload = response.json()
    except ValueError:
        return ToolResult(data={"error": "not_readable", "status": response.status_code})
    if response.status_code >= 400:
        # The API's own envelope: an i18n key the model reports on, never something to retry
        # around. A 404 here has §15's existence-hiding meaning, exactly as over HTTP.
        return ToolResult(
            data={"error": "refused", "status": response.status_code, "detail": payload}
        )
    fitted, cut = _fit(payload)
    data: dict[str, Any] = {"status": response.status_code, "result": fitted}
    if cut:
        data["truncated"] = {
            **cut,
            "note": "Only part of the answer is shown. Ask a narrower question (filters, "
            "limit, page) rather than treating this as the whole.",
        }
    return ToolResult(data=data, sources=_sources(op, payload))


# --------------------------------------------------------------------------- #
# The tools, per request
# --------------------------------------------------------------------------- #
_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Keywords in English naming what you want to read: the record "
            "type, the module, an action (list, get, summary, report). Example: 'domains "
            "expiring', 'leave balance', 'invoices outstanding'.",
        },
        "module": {
            "type": "string",
            "description": "Optional: limit the search to one module by its name.",
        },
    },
    "required": ["query"],
}

_GET_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The operation name from api.find."},
        "path_params": {
            "type": "object",
            "description": "Values for the {placeholders} in the operation's path.",
            "additionalProperties": True,
        },
        "query": {
            "type": "object",
            "description": "Query parameters, as api.find listed them. Prefer filters and a "
            "small limit over reading everything.",
            "additionalProperties": True,
        },
    },
    "required": ["name"],
}


def api_tools(ctx: RequestContext, forwarding: Forwarding) -> list[AIToolSpec]:
    """The catalog pair plus one named tool per allowed write the caller may perform."""
    app = forwarding.app
    by_name = {op.name: op for op in usable(ctx, app)}

    async def _find(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "")
        module = args.get("module")
        hits = find(ctx, app, query, str(module) if module else None)
        return ToolResult(
            data={
                "operations": [op.detail() for op in hits if not op.write],
                "note": "Call one with api.get. Writes are separate tools, not api.get.",
            }
        )

    async def _get(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
        name = str(args.get("name") or "")
        op = by_name.get(name)
        if op is None or op.write:
            return ToolResult(
                data={
                    "error": "unknown_operation",
                    "note": "No readable operation by that name is available to you. Use "
                    "api.find to look one up; writes have their own tools.",
                }
            )
        path_params = args.get("path_params")
        query = args.get("query")
        return await call(
            ctx,
            forwarding,
            op,
            path_params=path_params if isinstance(path_params, dict) else None,
            query=query if isinstance(query, dict) else None,
        )

    specs = [
        AIToolSpec(
            name="api.find",
            description="Search the operations you may call to read this workspace's data — "
            "every module the organisation uses. Returns each operation's name, parameters "
            "and, for reads, what it answers. Search in English.",
            input_schema=_FIND_SCHEMA,
            handler=_find,
        ),
        AIToolSpec(
            name="api.get",
            description="Call one read operation from api.find by name, with its path and "
            "query parameters. Read-only.",
            input_schema=_GET_SCHEMA,
            handler=_get,
        ),
    ]
    for op in by_name.values():
        if op.write:
            specs.append(_write_tool(forwarding, op))
    return specs


def _write_tool(forwarding: Forwarding, op: ApiOperation) -> AIToolSpec:
    path_params = set(op.path_params)

    async def _write(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
        given = {k: v for k, v in args.items() if k in path_params}
        body = {k: v for k, v in args.items() if k not in path_params}
        return await call(ctx, forwarding, op, path_params=given, body=body)

    what = op.summary or op.name.replace("_", " ")
    return AIToolSpec(
        name=op.name,
        description=f"{what} ({op.method.upper()} {op.path}). Writes a record the user asked "
        "for; the answer is the stored record.",
        input_schema=op.input_schema(),
        handler=_write,
        permission=op.permission,
    )


def describe_call(app: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """What the ``tool`` SSE event says about a call, so the panel can print "reads tasks…"
    or "creates a task…" rather than the bare tool name."""
    event: dict[str, Any] = {"name": name}
    if name == "api.get":
        wanted = str(args.get("name") or "")
        op = next((o for o in index_for(app) if o.name == wanted), None)
    elif name == "api.find":
        op = None
    else:
        op = next((o for o in index_for(app) if o.name == name and o.write), None)
    if op is not None:
        event.update({"operation": op.name, "method": op.method.upper(), "module": op.module})
    return event


__all__ = [
    "ASSISTANT_WRITES",
    "ApiOperation",
    "Forwarding",
    "allowed",
    "api_tools",
    "build_index",
    "call",
    "describe_call",
    "find",
    "forwarding_from",
    "index_for",
    "modules_summary",
    "usable",
]
