"""MCP server (CLAUDE.md §12): the API surface as tools, API-key authenticated.

Stateless streamable HTTP with JSON responses, so each JSON-RPC POST stands alone — exactly
how these tests drive it. Authorization is the platform's API keys: the proxy forwards the
caller's credential on every in-process call, so the key's scopes govern each tool.
"""

from __future__ import annotations

import json
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
        assert not any(
            "setup" in name or "instance" in name or name.startswith("users_")
            for name in tools
        )

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
