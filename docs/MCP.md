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

## The human-readable reference lives under `/api/` too

Swagger UI is at **`https://<tenant-host>/api/docs`**, ReDoc at `/api/redoc`, and the document
itself at `/api/openapi.json` — the same spec the tool surface above is generated from.

They are not at FastAPI's defaults, and the reason is the same one that puts MCP at `/mcp`:
**the edge routes exactly two prefixes to the API service**, `/api/` and `/mcp`
(`infra/traefik/dynamic*.yml`, `infra/compose.portainer.yml`), and everything else to the SSR
web app. At the framework defaults the reference sat at `/docs`, `/redoc` and `/openapi.json`,
which every deployment handed to SvelteKit — a route it does not have. So the API documentation
was not disabled anywhere; it was **unroutable**, and the symptom was the web app's 404 page
where the reference should have been. Serving it from inside the one prefix that reaches this
service fixes it with no edge change on an existing install.

`SCHAKL_API_DOCS_ENABLED=false` removes the HTTP surface. It does **not** remove the spec:
`app.openapi()` builds the document from the route table and never reads `openapi_url`, so the
tool builder above and `scripts/gen-client.sh` keep working on an instance that serves no docs
at all. `apps/api/tests/test_api_docs.py` pins the paths rather than the mere existence of a
document — a test that asserted `app.openapi()` returns something passed the entire time the
reference was unreachable.

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

**Instellingen → API en MCP** (`settings/api`) is the screen: pick what you are connecting, pick
what the key may do, and it prints the connection for that client with the freshly minted secret
already in it. Headless keys that belong to no person stay under **Instellingen →
Service-accounts**.

It is a screen rather than the card it used to be at the bottom of Mijn account, and the reason
generalises: **a credential shown once has to arrive with its instructions**. The old card handed
back a secret and stopped — and the word "MCP" appeared nowhere in the web app at all, so the
surface documented here was, in the product, undiscoverable. The flow now ends where it used to
begin, and the read-first rule below is a *default on a radio button* instead of a paragraph
someone had to find.

Claude Code:

```bash
claude mcp add --transport http schakl https://<your-domain>/mcp \
  --header "Authorization: Bearer schakl_…"
```

Any other Streamable-HTTP client: endpoint `https://<your-domain>/mcp`, header
`Authorization: Bearer schakl_…` (or `X-API-Key: schakl_…`).

**The screen asks whether the surface is there before it offers the command.** `/meta/modules`
carries `mcp_enabled` (is `/mcp` mounted at all — `SCHAKL_MCP_ENABLED`) and `mcp_entitled` (does
the license cover the `mcp` sku, which `LicenseGateASGI` enforces on the whole mount). Neither
could come from `licensed_modules`: that list is filtered to registry modules and MCP is core
code with its own sku, so nothing in the payload could answer the question. Without them the
guide would print a `claude mcp add` line that fails in the user's terminal on an installation
that never had the surface — #253's "a link that always refuses is a broken control", except the
refusal happens somewhere the app cannot see it. When either flag is false the AI-assistant
option is still *shown*, and says which of the two is missing; it just is not a button.

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
