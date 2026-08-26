"""MCP server (CLAUDE.md §12): the API surface as tools, API-key authenticated.

Stateless streamable HTTP with JSON responses, so each JSON-RPC POST stands alone — exactly
how these tests drive it. Authorization is the platform's API keys: the proxy forwards the
caller's credential on every in-process call, so the key's scopes govern each tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

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


async def _rpc(
    client, method: str, params: dict | None = None, *, auth: dict, url: str = "/mcp/"
) -> dict:
    response = await client.post(
        url,
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
    """No credential at all is refused at the transport, before any JSON-RPC is read.

    This used to answer 200 with a tool error, and both halves of the change are the point: an
    OAuth client discovers the authorization server *by being refused* (see
    ``test_mcp_oauth.py``), and an anonymous ``tools/list`` disclosed the tenant's whole module
    set. A credential that is merely *wrong* still surfaces as the API's own envelope on the
    individual tool call — that path is untouched.
    """
    t = await make_tenant("mcp-anon")
    async with mcp_running(), client_for(t.host) as c:
        response = await c.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_companies", "arguments": {}},
            },
            headers=_MCP_HEADERS,
        )
        assert response.status_code == 401, response.text
        assert "resource_metadata=" in response.headers["www-authenticate"]

        # A bad key is a different failure and keeps its old shape.
        wrong = await _rpc(
            c,
            "tools/call",
            {"name": "list_companies", "arguments": {}},
            auth={"Authorization": "Bearer schakl_deadbeef_nope"},
        )
        assert wrong["result"].get("isError") is True


async def test_mcp_refuses_the_standalone_stream_instead_of_holding_it_open(client_for) -> None:
    """``GET /mcp`` answers 405 rather than opening a stream that can never carry a message.

    The transport is stateless, so nothing is ever routed to the standalone SSE stream the
    spec lets a client open — the SDK opens it anyway and holds the connection, the task group
    and its memory streams until somebody times out. Clients probe with ``GET``; one that hangs
    reports "the server timed out", which is the wrong sentence about the right fact.

    Asserted with a deadline, because the bug's signature is *no response at all*: without the
    guard this call never returns and the test would hang instead of failing.
    """
    t = await make_tenant("mcp-get")
    async with mcp_running(), client_for(t.host) as c:
        response = await asyncio.wait_for(
            c.get("/mcp/", headers={"Accept": "application/json, text/event-stream"}),
            timeout=10,
        )
        assert response.status_code == 405, response.text
        assert response.headers["allow"] == "POST"
        # 405 rather than the 401 an anonymous POST now gets, and the ordering is deliberate:
        # ``GET`` is not offered here to *anyone*, so answering "authenticate first" would send
        # a client off to complete an OAuth flow that cannot fix it.
        assert "www-authenticate" not in response.headers


#: Bytes of ``tools/list`` the compact profile may spend (CLAUDE.md §12).
#:
#: ChatGPT's ceiling is stated in *tokens* — 5,000 for every tool's name, description and
#: input schema together — and a tokenizer is a network download and a dependency this suite
#: is not going to grow for one assertion. So the budget is expressed in the bytes the server
#: actually sends, converted at a deliberately pessimistic **3.0 chars/token**: the measured
#: ratio for this payload is 3.89 (o200k_base), so 14,300 bytes is at most ~4,767 tokens on
#: the pessimistic reading and ~3,700 on the real one. Either way it is under the cap, and the
#: conversion only ever errs towards failing this test early.
#:
#: Raised 14,000 → 14,300 when #437/#443 added ``burn`` (projects list) and ``task_id``
#: (time report) — two parameters an agent genuinely filters by, ~100 bytes together, watched
#: here exactly as this number exists for.
_COMPACT_BUDGET_BYTES = 14_300


async def test_mcp_compact_profile_fits_a_chat_client(client_for) -> None:
    """``/mcp/compact`` is the curated read-only set, small enough for ChatGPT to accept.

    This is the specification for :data:`app.core.mcp.server._COMPACT_TOOLS`, not a smoke
    test. The full surface is ~527,000 tokens — a hundred times ChatGPT's allowance — and the
    profile exists solely to be under it, so the number below is the feature. A name added to
    the curated set without watching this assertion is how the profile silently stops being
    addable, and the failure would otherwise appear in somebody else's settings screen weeks
    later as an error message we never see.
    """
    from app.core.mcp.sections import _COMPACT_TOOLS

    t = await make_tenant("mcp-compact")
    headers = await auth_cookie(t.user)
    async with mcp_running(), client_for(t.host) as c:
        await c.post("/api/v1/companies", json={"name": "Compact BV"}, headers=headers)
        minted = await c.post(
            "/api/v1/api-keys",
            json={"name": "compact", "scopes": ["companies.company.read"]},
            headers=headers,
        )
        auth = {"Authorization": f"Bearer {minted.json()['secret']}"}

        listed = await _rpc(c, "tools/list", auth=auth, url="/mcp/compact")
        tools = listed["result"]["tools"]

        # Exactly the curated set — no name in it that resolves to nothing, which is the way
        # this list rots: a route is renamed and its entry here quietly stops matching.
        assert {tool["name"] for tool in tools} == set(_COMPACT_TOOLS)

        # Response schemas are 79% of the full surface's bytes and buy a caller nothing at
        # decision time. Their absence is most of why the profile fits.
        assert not [tool for tool in tools if tool.get("outputSchema")]

        spent = len(json.dumps(tools, separators=(",", ":")))
        assert spent <= _COMPACT_BUDGET_BYTES, (
            f"the compact profile spends {spent:,} bytes of its {_COMPACT_BUDGET_BYTES:,} "
            f"budget across {len(tools)} tools — drop one, or narrow a schema"
        )

        # Same server, same credential path: the profile narrows a listing, it is not a
        # second data path and it is not a second answer about authorization.
        called = await _rpc(
            c,
            "tools/call",
            {"name": "list_companies", "arguments": {}},
            auth=auth,
            url="/mcp/compact",
        )
        assert called["result"].get("isError") is not True, called
        assert "Compact BV" in json.dumps(called["result"])

        # …and the full surface is untouched, so a section can never become the default.
        full = await _rpc(c, "tools/list", auth=auth)
        assert len(full["result"]["tools"]) > 100


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


async def test_a_module_section_is_derived_from_the_module_router(client_for) -> None:
    """``/mcp/google-ads`` lists that module's tools and nothing else — and nothing lists them.

    This is the specification for the *derivation*, not for a list. A module section is built
    from the module's own router prefix, so a route added tomorrow is served here tomorrow;
    the assertions below therefore compare against the OpenAPI document rather than against
    names written down in a test, which would be the same stale copy one layer out.
    """
    from app.core.mcp.sections import build_sections
    from app.core.mcp.server import _tool_index

    _, paths = _tool_index(fastapi_app)
    sections = build_sections(paths)
    expected = {
        tool
        for tool, path in paths.items()
        if path == "/api/v1/google-ads" or path.startswith("/api/v1/google-ads/")
    }
    assert sections["google-ads"].tools == expected

    # The segment boundary is the whole reason `_under` exists: `/api/v1/google-ads/...` is not
    # under `/api/v1/google`, and a plain prefix match says it is — which would fold every
    # Google Ads tool into the Workspace module's section with nobody able to see why.
    assert not (sections["google-ads"].tools & sections["google"].tools)

    t = await make_tenant("mcp-section")
    headers = await auth_cookie(t.user)
    async with mcp_running(), client_for(t.host) as c:
        minted = await c.post(
            "/api/v1/api-keys",
            json={"name": "s", "scopes": ["companies.company.read"]},
            headers=headers,
        )
        auth = {"Authorization": f"Bearer {minted.json()['secret']}"}

        listed = await _rpc(c, "tools/list", auth=auth, url="/mcp/google-ads")
        served = {tool["name"] for tool in listed["result"]["tools"]}
        assert served == expected

        # Response schemas are dropped for every section, not only the curated one: a section is
        # asked for by somebody who needed a smaller surface, and this is the largest reduction
        # available that costs a caller nothing at the moment they decide.
        assert not [tool for tool in listed["result"]["tools"] if tool.get("outputSchema")]

        # A section narrows a *listing*. It is not an authorization boundary: a tool outside it
        # still answers, still through require_context, still capped by the key's scopes.
        called = await _rpc(
            c,
            "tools/call",
            {"name": "list_companies", "arguments": {}},
            auth=auth,
            url="/mcp/google-ads",
        )
        assert called["result"].get("isError") is not True, called

        # A typo'd segment is refused, and the refusal names what exists. Never widened to the
        # full surface: somebody who typed `/mcp/google-add` asked for 45 tools and would
        # silently receive 623, which looks like it worked and is not recoverable by reading.
        unknown = await c.post(
            "/mcp/google-add",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={**_MCP_HEADERS, **auth},
        )
        assert unknown.status_code == 404, unknown.text
        assert "google-ads" in unknown.json()["error"]["message"]


async def test_a_bundle_names_modules_and_never_tools(client_for) -> None:
    """``/mcp/infra`` is exactly the union of its modules' sections.

    Asserted as a union rather than as a count, because that identity is what keeps a bundle as
    self-maintaining as the sections it unions — the moment a bundle could hold a tool its
    modules do not, it has become a list somebody has to keep up to date.
    """
    from app.core.mcp.sections import KIND_BUNDLE, build_sections
    from app.core.mcp.server import _tool_index

    _, paths = _tool_index(fastapi_app)
    sections = build_sections(paths)

    for key in ("agent", "infra", "finance", "growth"):
        bundle = sections[key]
        assert bundle.kind == KIND_BUNDLE
        union: set[str] = set()
        for module in bundle.modules:
            prefix = next(
                s.key for s in sections.values() if s.modules == (module,) and s.kind == "module"
            )
            union |= sections[prefix].tools
        assert bundle.tools == union, key


async def test_meta_mcp_describes_every_section_the_server_serves(client_for) -> None:
    """``/meta/mcp`` is what Instellingen → API en MCP renders, and it agrees with the server.

    The agreement is the assertion. Two places computing "which sections exist" is exactly how a
    settings screen ends up printing a URL that answers with the full surface — visible to a
    user, invisible in review.
    """
    t = await make_tenant("mcp-meta-sections")
    headers = await auth_cookie(t.user)
    async with mcp_running(), client_for(t.host) as c:
        body = (await c.get("/api/v1/meta/mcp", headers=headers)).json()
        assert body["enabled"] is True
        assert body["total_tools"] > 100
        rows = {row["key"]: row for row in body["sections"]}

        # The three kinds are on the payload because the screen groups on them and they mean
        # genuinely different things.
        assert rows["compact"]["kind"] == "curated"
        assert rows["infra"]["kind"] == "bundle"
        assert rows["google-ads"]["kind"] == "module"

        # A module section labels itself with the key the modules screen already uses, so the
        # two can never be one translation apart.
        assert rows["google-ads"]["label_key"] == "module.google_ads.label"
        assert rows["google-ads"]["path"] == "/mcp/google-ads"
        assert rows["infra"]["modules"] and "uptime" in rows["infra"]["modules"]

        # Counts are the number a client actually receives, which is why the index is filtered
        # by the route maps rather than by a second copy of the exclusions.
        listed = await _rpc(
            c,
            "tools/list",
            auth={
                "Authorization": "Bearer "
                + (
                    await c.post(
                        "/api/v1/api-keys",
                        json={"name": "m", "scopes": ["companies.company.read"]},
                        headers=headers,
                    )
                ).json()["secret"]
            },
            url="/mcp/invoicing",
        )
        assert len(listed["result"]["tools"]) == rows["invoicing"]["tool_count"]


def test_importing_mcp_is_quiet_and_leaves_authlib_deprecations_audible():
    """``import fastmcp`` must not print Authlib's ``authlib.jose`` deprecation on every boot.

    FastMCP's JWT verifier imports the module; we never mount that provider and never touch
    ``authlib.jose`` ourselves, and the import is still there on 3.x, so there is no upgrade
    that fixes it. ``app/core/mcp/__init__`` silences it for the length of one import — and
    the two things that make that work are invisible in a diff, which is what this pins:

    * the ``authlib.deprecate`` pre-import is not a stray line. ``simplefilter`` prepends, so
      Authlib's own ``always`` filter installed *inside* the block would sit in front of our
      ignore and the warning would print regardless.
    * ``catch_warnings`` restores the filter list it entered with, so that same inside-the-block
      install would then be undone on the way out, taking every *other* Authlib deprecation
      with it. We are an Authlib consumer in our own right (OIDC, Google OAuth).

    A subprocess because a warning is raised once per process and the suite has long since
    imported this package by the time any test runs.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONWARNINGS"}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.core.mcp, warnings, authlib.deprecate as d;"
            "print(any(f[2] is d.AuthlibDeprecationWarning for f in warnings.filters))",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert "authlib.jose" not in proc.stderr, f"the deprecation is loud again:\n{proc.stderr}"
    assert proc.stdout.strip() == "True", (
        "Authlib's own 'always' filter did not survive the import, so every future Authlib "
        f"deprecation is now silent too:\n{proc.stdout}{proc.stderr}"
    )
