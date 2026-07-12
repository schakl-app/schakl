# MCP server — AI access to the API

> The platform exposes an MCP (Model Context Protocol) server so AI clients — Claude
> Desktop/Code, agents, anything speaking MCP — can work with the instance's data under the
> same authorization as any API caller. Design rules live in CLAUDE.md §12; this documents
> what shipped and how to connect.

## What it is

- **Transport:** Streamable HTTP at **`/mcp`**, mounted on the API app (served through
  Traefik on the same tenant hostname as the app). Stateless, JSON responses — every
  JSON-RPC POST stands alone, so it works behind any load balancer and from plain `curl`.
- **Tool surface:** every `/api/v1` operation is exposed as a tool, generated from the API's
  own OpenAPI spec (FastMCP's OpenAPI integration) and proxied **in-process** back to the
  REST API. There is no second data path: a tool call goes through `require_context` —
  hostname → org, RLS bound, permissions resolved — exactly like the HTTP request it is.
  Tool names are the API's operation names (`list_companies`, `create_task`, `time_summary`,
  …). The session flows (`/auth`, `/setup`) and the instance-operator surface (`/instance`)
  are excluded.
- **Kill switch:** `SCHAKL_MCP_ENABLED=false` removes the whole surface.

## Authentication: API keys (OAuth later)

The server authenticates with the platform's **API keys** (#20) rather than running an OAuth
2.1 authorization server:

- a key already carries **per-key permission scopes** — exactly the "permissions per MCP
  key" model wanted here. A key scoped to `companies.company.read` can list clients and
  nothing else; the API's deny-by-default route permissions answer every call.
- keys are **tenant-scoped** (a key presented on another org's hostname is simply not
  found), **revocable**, rate-limited, and may be **non-expiring** (owner's choice).
- a personal key is additionally capped by its owner's *live* permissions on every request;
  a service-account key carries exactly its granted scopes.

The MCP proxy forwards the caller's `Authorization` / `X-API-Key` header plus the tenant
hostname onto every internal call — an unauthorized tool call surfaces the API's own 401/403
envelope as a tool error.

An OAuth 2.1 resource-server layer (RFC 9728 protected-resource metadata) can be added later
for clients that require the full OAuth flow, without touching the tool surface.

## Connecting a client

Mint a key under **Instellingen → Account → API-sleutels** (personal) or **Instellingen →
Service-accounts** (headless), selecting the permissions the AI may exercise.

Claude Code:

```bash
claude mcp add --transport http schakl https://<your-domain>/mcp \
  --header "Authorization: Bearer schakl_…"
```

Any other Streamable-HTTP client: endpoint `https://<your-domain>/mcp`, header
`Authorization: Bearer schakl_…` (or `X-API-Key: schakl_…`).

## Design notes

- **Read-first is a key-scope decision, not a server one.** CLAUDE.md §12's read-first rule
  is honoured by minting read-only keys; the surface itself includes writes because the
  owner asked for the full API. A cautious instance mints keys with read scopes only.
- The per-module `mcp.py` seams (curated, hand-written tools like `companies.find`) remain
  the path for *richer* tools than a 1:1 endpoint mapping; the OpenAPI-derived set is the
  baseline that keeps every route reachable.
- The MCP sub-app's session manager starts inside the API's lifespan (`app.main.lifespan`).
  Tests that exercise `/mcp` must enter it explicitly — see `tests/test_mcp_api.py`.
- **Never** forward the incoming MCP credential to any *external* service (confused-deputy);
  the proxy only ever calls the API in-process.
