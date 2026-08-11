"""MCP server (CLAUDE.md §12): the API surface as tools, API-key authenticated.

Stateless streamable HTTP with JSON responses, so each JSON-RPC POST stands alone — exactly
how these tests drive it. Authorization is the platform's API keys: the proxy forwards the
caller's credential on every in-process call, so the key's scopes govern each tool.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager

from app.main import app as fastapi_app
from tests.conftest import auth_cookie, make_tenant


@asynccontextmanager
async def mcp_running():
    """A fresh MCP sub-app swapped into the /mcp mount, its lifespan entered around the calls.

    Three constraints meet here: the httpx ASGI transport never runs lifespans; a
    streamable-HTTP session manager only runs *once* per instance (so tests can't share the
    mounted one); and anyio cancel scopes must exit in the task they entered, which rules out
    a pytest fixture (teardown runs in a different task). Production enters the mounted
    instance's lifespan once, in ``app.main.lifespan``."""
    from app.core.mcp import build_mcp_asgi_app

    fresh = build_mcp_asgi_app(fastapi_app)
    mount = next(r for r in fastapi_app.routes if getattr(r, "path", None) == "/mcp")
    original = mount.app
    mount.app = fresh
    try:
        async with fresh.lifespan(fresh):
            yield
    finally:
        mount.app = original

#: The surfaces ``app/core/mcp/server.py`` excludes by path, as the same expression it uses.
_EXCLUDED_PATHS = re.compile(r"^/api/v1/(auth|setup|instance|users)(/|$)")


def _excluded_tool_names() -> set[str]:
    """Every tool name the excluded surfaces *would* contribute, had they not been excluded.

    Derived from the spec rather than guessed, and carrying both forms the name builder can
    produce — the short one it prefers, and the full operationId it falls back to on a
    collision — so the assertion cannot quietly stop covering an operation.
    """
    names: set[str] = set()
    for path, operations in fastapi_app.openapi()["paths"].items():
        if not _EXCLUDED_PATHS.match(path):
            continue
        for operation in operations.values():
            op_id = operation.get("operationId") if isinstance(operation, dict) else None
            if op_id:
                names |= {op_id, op_id.split("_api_v1_")[0]}
    assert names, "no excluded surface found in the spec — the pattern stopped matching"
    return names

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


async def _rpc(client, method: str, params: dict | None = None, *, auth: dict) -> dict:
    response = await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={**_MCP_HEADERS, **auth},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_mcp_tools_reflect_the_api_and_enforce_key_scopes(client_for) -> None:
    t = await make_tenant("mcp-basic")
    headers = await auth_cookie(t.user)
    async with mcp_running(), client_for(t.host) as c:
        await c.post("/api/v1/companies", json={"name": "MCP BV"}, headers=headers)
        minted = await c.post(
            "/api/v1/api-keys",
            json={"name": "mcp", "scopes": ["companies.company.read"]},
            headers=headers,
        )
        assert minted.status_code == 201, minted.text
        auth = {"Authorization": f"Bearer {minted.json()['secret']}"}

        listed = await _rpc(c, "tools/list", auth=auth)
        tools = {tool["name"] for tool in listed["result"]["tools"]}
        assert "list_companies" in tools
        # Session flows, self-service account routes and the operator surface are excluded.
        # Asked of the *paths* the exclusion is written against, never of a substring of the
        # tool name: ``/api/v1/uptime/instances`` is an ordinary module route whose tool is
        # ``list_uptime_instances``, and a name containing "instance" is not the instance
        # console. A test that reads it as one goes red on the next module that ships a word.
        assert not (_excluded_tool_names() & tools), sorted(_excluded_tool_names() & tools)

        called = await _rpc(
            c, "tools/call", {"name": "list_companies", "arguments": {}}, auth=auth
        )
        assert called["result"].get("isError") is not True, called
        assert "MCP BV" in json.dumps(called["result"])

        # A tool outside the key's scopes surfaces the API's own 403 as a tool error.
        member_tools = [name for name in tools if "member" in name]
        denied = await _rpc(
            c, "tools/call", {"name": "list_members", "arguments": {}}, auth=auth
        )
        assert member_tools and denied["result"].get("isError") is True, denied


async def test_mcp_is_tenant_scoped(client_for) -> None:
    a = await make_tenant("mcp-iso-a")
    b = await make_tenant("mcp-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with mcp_running(), client_for(a.host) as ca:
        await ca.post("/api/v1/companies", json={"name": "Alpha Klant"}, headers=a_headers)
        a_key = (
            await ca.post(
                "/api/v1/api-keys",
                json={"name": "a", "scopes": ["companies.company.read"]},
                headers=a_headers,
            )
        ).json()["secret"]
    async with mcp_running(), client_for(b.host) as cb:
        b_key = (
            await cb.post(
                "/api/v1/api-keys",
                json={"name": "b", "scopes": ["companies.company.read"]},
                headers=b_headers,
            )
        ).json()["secret"]
        # B's key on B's host never sees A's rows…
        result = await _rpc(
            cb,
            "tools/call",
            {"name": "list_companies", "arguments": {}},
            auth={"Authorization": f"Bearer {b_key}"},
        )
        assert "Alpha Klant" not in json.dumps(result)
        # …and A's key presented on B's host is simply not found there (401 → tool error).
        crossed = await _rpc(
            cb,
            "tools/call",
            {"name": "list_companies", "arguments": {}},
            auth={"Authorization": f"Bearer {a_key}"},
        )
        assert crossed["result"].get("isError") is True


async def test_mcp_rejects_anonymous_tool_calls(client_for) -> None:
    t = await make_tenant("mcp-anon")
    async with mcp_running(), client_for(t.host) as c:
        result = await _rpc(
            c, "tools/call", {"name": "list_companies", "arguments": {}}, auth={}
        )
        assert result["result"].get("isError") is True


async def test_meta_modules_advertises_the_mcp_surface(client_for, monkeypatch) -> None:
    """``/meta/modules`` says whether ``/mcp`` is mounted, and whether it is licensed.

    Instellingen → API en MCP prints a ``claude mcp add`` line from these two flags. They
    cannot come from ``licensed_modules``, which is filtered to registry modules — MCP is
    core code with its own sku — so without them the screen would hand out a command that
    fails in the user's terminal on an instance where the surface is switched off.
    """
    from app.config import settings

    t = await make_tenant("mcp-meta")
    async with client_for(t.host) as c:
        body = (await c.get("/api/v1/meta/modules")).json()
        assert body["mcp_enabled"] is True
        # No license installed in tests: the bootstrap window still covers the surface, so
        # "enabled" and "entitled" agree here. What matters is that they are asked separately.
        assert body["mcp_entitled"] is True

        monkeypatch.setattr(settings, "mcp_enabled", False)
        off = (await c.get("/api/v1/meta/modules")).json()
        assert off["mcp_enabled"] is False
        # Never "unmounted but licensed": the screen must not offer a command either way.
        assert off["mcp_entitled"] is False
