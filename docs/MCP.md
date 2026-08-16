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
- **Sections:** `/mcp` is that whole surface. **`/mcp/<section>`** is the same server — same
  session manager, same lifespan — answering `tools/list` with less. See
  [Sections](#sections-one-server-many-doors).
- **Authentication:** an API key, or an OAuth 2.1 flow that mints one. See
  [OAuth](#oauth-21-the-token-is-an-api-key). An MCP request carrying **no** credential is
  answered `401` with a `WWW-Authenticate` challenge — that refusal is how an OAuth client
  discovers where to authenticate.
- **Kill switch:** `SCHAKL_MCP_ENABLED=false` removes the whole surface.

## Sections: one server, many doors

`/mcp` is every operation — ~620 tools, about two megabytes of `tools/list`. That is the right
answer for a coding agent, which reads the list once and tolerates it. It is the wrong answer
for everything else, and the reason is not politeness: a chat client loads every tool into the
model's context on *every turn*, and a specialist agent handed 620 tools picks worse than the
same agent handed 45.

A **section** is one URL that answers `tools/list` with less. Same server, same session manager,
same lifespan, same credential path — only the listing changes. Pick the one that matches what
the client is for:

| Client | URL | Tools |
|---|---|---|
| Claude Code, Claude Desktop, coding agents | `https://<host>/mcp` | ~620 |
| ChatGPT (as an app/connector) | `https://<host>/mcp/compact` | 14, read-only |
| A general-purpose agent / n8n | `https://<host>/mcp/agent` | ~127 |
| A Google Ads agent | `https://<host>/mcp/google-ads` | ~45 |
| Hosting & domains | `https://<host>/mcp/infra` | ~86 |
| Invoicing | `https://<host>/mcp/finance` | ~83 |
| Marketing & reporting | `https://<host>/mcp/growth` | ~82 |
| One module on its own | `https://<host>/mcp/<module>` | varies |

Instellingen → API en MCP lists every section this instance serves, with its live tool count,
and writes the URL into the connection command for you. `GET /api/v1/meta/mcp` is the same list
for anything else that needs it.

### Three kinds, and the differences are the design

**A module section is derived, never written down.** `/mcp/google-ads` is exactly the tools whose
route lives under that module's own router prefix, read from the registry at boot. Nothing lists
them, because a hand-written list of one module's tools is a second copy of its router — and the
copy is only ever wrong *later*, silently, in the direction of a tool the module ships and the
section does not offer. A module that grows an endpoint tomorrow serves it here tomorrow.

**A bundle names modules, never tools**, for the same reason. It exists because an agency job is
not a module: "the domain register and what answers on it" spans seven of them, and no module
boundary can express it. Naming modules keeps a bundle exactly as self-maintaining as the
sections it unions — `test_a_bundle_names_modules_and_never_tools` asserts the identity rather
than a count, because the moment a bundle could hold a tool its modules do not, it has become a
list somebody has to keep up to date.

**A curated section is the only one that names tools**, and may only exist where an *external*
ceiling makes a module boundary useless. There is exactly one: `compact`. ChatGPT's limit is
**5,000 tokens for all tools together** — name, description and input schema — and it refuses a
server that exceeds it. The full surface is a hundred times that, and no amount of schema
trimming reaches the cap at 620 tools: that is ~8 tokens each, which does not buy a name. Only
fewer tools works. `_COMPACT_TOOLS` lives in `app/core/mcp/sections.py` and is pinned by
`test_mcp_compact_profile_fits_a_chat_client`, which is the actual specification — **add a name
there without watching that number and the profile silently stops being addable**, in somebody
else's settings screen, weeks later, with an error nobody here ever sees.

### Two reductions, and the second is the one that carries it

Filtering to the section's set is the obvious half. Dropping `outputSchema` is the half that
makes the budget: response schemas are **79% of the full surface's bytes**, and a client needs
them to *validate* a result it already has, never to decide whether to call. Six single tools in
the full surface each exceed ChatGPT's entire allowance on their own, and every one of them is a
response schema wearing a tool's name.

It is dropped for **every** section, not only the curated one: a section is asked for by somebody
who needed a smaller surface, and this is the largest reduction available that costs a caller
nothing at the moment they decide. `/mcp` is the URL that means "give me everything" and keeps
everything.

### A section narrows a listing. It is not an authorization boundary

A tool outside the section still answers, still through `require_context`, still capped by the
calling key's scopes. Saying otherwise would stand a second, weaker answer next to the one the
API already gives — and the weaker one would be the one a reader trusts, because it is the one
printed on the screen. What a credential may do is decided when it is minted and re-decided on
every request; what a URL *lists* is a context budget. The settings screen says so in as many
words, because the opposite reading is the natural one.

### A typo is refused, not widened

`/mcp/google-add` answers `404` naming the sections that exist. Falling back to the whole surface
would be the friendlier-looking choice and the wrong one: somebody who typed that asked for 45
tools and would silently receive 620, so the client either chokes on a budget or picks worse from
a list nobody meant to give it — and nothing anywhere says why. A refusal that names the sections
is recoverable in one read; a surface that is fourteen times too big is not recoverable at all,
because it looks like it worked.

### The tool→section index is read off the server, never predicted

`mcp_names` supplies a short name only where it is unique across the whole spec; everything else
keeps its operationId, and FastMCP then derives a name from *that* — splitting at the first `__`
(the delimiter FastAPI puts around a path parameter) and capping the result. So
`delete_account_api_v1_cloudflare_accounts__account_id__delete` is served as
`delete_account_api_v1_cloudflare_accounts`.

An index keyed on the operationId therefore matches **no tool at all** for those: 27 of them were
absent from their own module's section, and the section still looked plausible because the other
597 were there. `_tool_routes` reads the built server's own registry instead. That is a private
attribute, and deliberately the lesser evil — restating the naming rule here would be a copy of
somebody else's implementation detail, and a stale copy drops tools *quietly*, which is the
failure above one release later. This breaks loudly, and
`test_a_module_section_is_derived_from_the_module_router` compares a section against what
`tools/list` actually answers, so a FastMCP upgrade that moves it turns CI red rather than a
customer's agent stupid.

## `GET /mcp` answers 405, and that is deliberate

Streamable HTTP lets a client open a standalone `GET` stream for server-initiated messages.
That stream only means something while the server holds a session, and this one is **stateless
by choice** — so nothing would ever be routed to it and it would never end. The SDK does not
join those two facts: it refuses `DELETE` with 405 the moment it sees no session id, then opens
the `GET` stream anyway, holding a connection, a task group and two memory streams per probe
for as long as the caller or the edge will wait.

Clients probe with `GET`, and a client that hangs there reports *"the server timed out"* rather
than *"that verb is not offered"* — the wrong sentence about the right fact, and one that
looks like an outage. `RefuseStandaloneStream` answers 405 with `Allow: POST` instead. It is
written against the transport, not against any one client.

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

**And it is not public.** Being reachable was only half the question, and the other half went
unasked for exactly as long as the first one was wrong. A route FastAPI builds for its own docs
carries no dependency and cannot be given one, so all three paths answered `200` to no
credential at all — the full route table, every request and response schema, and the tenant's
enabled module set, on every instance ever shipped. That was never an *authorization* hole: each
operation behind those URLs still travels `require_context`, and Swagger UI's "Try it out" is a
browser making the call `curl` would make, so a stranger pressing it collected 401s. What leaked
was the **map** — which integrations this agency runs, and the exact shape of every request body
worth attacking.

So `app/core/apidocs.py` serves the three paths behind the gate of the API they describe: a
session or an API key for *this* org, and never a client-portal login, because externality is
its own axis (CLAUDE.md §15, #274) and the agency's internal route table is not something a
client signs in to see. A browser reaches it on the cookie it already holds — same origin, so
Swagger UI's own fetch of the document is authenticated too — and every response is `no-store`,
because a body that exists only because a credential was presented must not be held by anything
in front of us.

The lesson generalises past this surface, and is why `apps/api/tests/test_anonymous_denied.py`
now sits beside the permission sweep: **"may this caller?" and "is there a caller?" are
different questions behind different gates**, and a repo that only ever asks the first will keep
answering the second by accident. That file sweeps every operation with no credential at all,
plus the two surfaces the OpenAPI document cannot see — this reference, and `/mcp` itself.

`SCHAKL_API_DOCS_ENABLED=false` still removes the HTTP surface entirely. It does **not** remove
the spec: `app.openapi()` builds the document from the route table and never reads
`openapi_url`, so the tool builder above and `scripts/gen-client.sh` keep working on an instance
that serves no docs at all — `gen-client.sh` defaults to the offline exporter, and only its
optional `OPENAPI_URL=` path fetches over HTTP, which now needs a credential like any other
read. `apps/api/tests/test_api_docs.py` pins the paths *and* the gate rather than the mere
existence of a document: a test asserting `app.openapi()` returns something passed the entire
time the reference was unreachable, and one fetching it without credentials would have passed
the entire time it was open.

## Authentication: API keys, and OAuth mints one

Two ways to hold a credential, and **only one kind of credential**.

### API keys

The platform's keys (#20) already carry per-key permission scopes, are tenant-scoped, revocable
and optionally non-expiring — precisely the "permissions per MCP key" model §12 asks for:

- a key scoped to `companies.company.read` can list clients and nothing else; the API's
  deny-by-default route permissions answer every call.
- keys are **tenant-scoped** (a key presented on another org's hostname is simply not found),
  revocable and rate-limited.
- a personal key is additionally capped by its owner's *live* permissions on every request; a
  service-account key carries exactly its granted scopes.

The MCP proxy forwards the caller's `Authorization` / `X-API-Key` header plus the tenant hostname
onto every internal call — an unauthorized tool call surfaces the API's own 401/403 envelope as a
tool error.

### OAuth 2.1: the token *is* an API key

Clients that want a connector rather than a pasted secret — Claude and ChatGPT among them — get a
full OAuth 2.1 flow: RFC 7591 dynamic client registration, authorization code with PKCE (`S256`
only; 2.1 drops `plain`), refresh, RFC 7009 revocation, and RFC 8414/9728 discovery.

**It issues no new kind of credential.** What redemption hands back is an `api_keys` row belonging
to the person who consented, so every rule that already governs a personal key governs an OAuth
session unchanged: scopes capped by the owner's live permissions on *every* request, the company
horizon of whoever consented, tenant scoping by hostname, revocation, rate limiting. There is no
access-token table, because a second credential would be a second set of answers about what it may
do — and the second answer is always the one missing a rule. The protocol contributes a handshake;
it contributes no authority.

**And the authorization server authenticates nobody.** It has no login of its own: consent runs on
the browser session the web app already holds, which may have been a local password with 2FA or
this org's OIDC federation. A login here would have meant a second password path, a second 2FA
decision and a second answer to "which org is this session for" — three copies of things that are
already right once.

### Where the endpoints live, and why they are split

| | URL | Served by |
|---|---|---|
| Protected-resource metadata | `/.well-known/oauth-protected-resource/mcp[/<section>]` | web app (proxy) |
| Authorization-server metadata | `/.well-known/oauth-authorization-server` | web app (proxy) |
| Authorization (consent) | `/oauth/authorize` | web app (a page) |
| Registration | `/api/v1/oauth/register` | API |
| Token | `/api/v1/oauth/token` | API |
| Revocation | `/api/v1/oauth/revoke` | API |

The split is not arbitrary. **The edge routes exactly `/api/` and `/mcp` to the API service and
everything else to the SSR web app** (§12: *a route the edge does not forward is a route nobody
has* — the API reference already learned that the expensive way, by being unreachable in every
deployment for months). Both RFCs put their documents on the **root of the host**, which is not
ours to answer on. Adding an edge rule for `/.well-known/oauth-*` would need every existing
self-hosted install to update its Traefik config before a connector worked, with the failure mode
"Add connector does nothing" and no screen able to explain it — so the web app serves the
documents and **the API authors them**. Nothing at the edge changes.

The endpoints those documents *advertise* are then free to live where they belong: consent is a
page a person reads, so it is the web app's; token and registration are machine-to-machine, so
they are the API's under a prefix the edge already forwards. A metadata document is exactly the
mechanism for saying so.

### The rules that hold it up

**A webhook body is a hint and a form field is a request.** The consent form is a browser form, so
the narrowing a person did on screen is re-derived against two authorities before anything is
written: the permission catalog, and the consenting user's own live grants. A client asking for
everything cannot talk a member into granting what the member does not have.

**Single use is the database's job.** Redemption is a conditional
`UPDATE … WHERE redeemed_at IS NULL RETURNING`. A client that retries a slow token request has two
exchanges in flight against two API replicas that share no memory, and "have we redeemed this?"
followed by a write leaves a window every retry enters — the same shape `docs/PAYMENTS.md` already
lost once to an application-level check. The loser of the race updates zero rows and is refused,
which is also the correct answer to a *replayed* code.

**Every way of being wrong answers the same.** Expired, replayed, issued to another client, PKCE
mismatch — all `invalid_grant`, because telling them apart tells the holder of a stolen code which
part they still need. Revocation always answers 200 for the same reason (RFC 7009 §2.2): an
endpoint that distinguishes "revoked" from "no such token" is a token oracle.

**A redirect URI is matched by equality, and a bad one is never redirected to.** An unregistered
client or an unregistered target is refused *on the consent page*, never by bouncing to the URI in
question — that is the open redirector the exact-match list exists to prevent, and it would hand an
attacker the `state` as well. The *deny* action re-validates the target through the API before
redirecting, because by then it is a POST body rather than the value the page was loaded with.

**These routes answer in the RFC's error shape, not the house envelope**, and it is the one place
this module is allowed to disagree with §9. The caller is somebody else's MCP client reading
`{"error": "invalid_grant"}` off a documented contract; handing it `{"error": {"code": …,
"message": "errors.oauth_invalid_grant"}}` would be an i18n key sent to a program that cannot
translate it, inside a field it will try to string-compare. The two endpoints a *browser* calls
keep the house envelope, because the thing rendering their errors is our own web app.

**Refresh tokens do not rotate, and that is a decision rather than an omission.** OAuth 2.1 asks a
public client to rotate or sender-constrain. Rotation without a replay window turns one dropped
HTTP response into a connector that is silently dead until somebody re-consents, on a machine
nobody here can see. What limits exposure instead is the hour-long access token — rotated on every
refresh — plus three kill switches a token table would not have: revoke the key, revoke the client,
or remove the person's membership. Written down here so a later reader can weigh it rather than
discover it.

**Registration is the one unauthenticated write a stranger can repeat**, which is what RFC 7591
is for — a client that has never met this instance has to be able to become addable. It is
rate-limited by IP, capped per org, its redirect URIs are validated to https-or-loopback (or a
native custom scheme) before the row exists, and it grants **nothing**: the row can read no byte of
tenant data until a person signs in and consents.

### `/mcp` refuses an anonymous request

An MCP request carrying neither `Authorization` nor `X-API-Key` is answered `401` with
`WWW-Authenticate: Bearer resource_metadata="…"`, naming the section's own URL as the resource
(RFC 8707 binds a token to an audience, and "near enough" is not something an audience check does).

**This is a behaviour change**: `tools/list` used to answer anonymously. Both halves of the fix
matter. A client that speaks OAuth discovers the authorization server *by being refused*, so a 200
here means "Add connector" can never complete a flow. And listing 620 tool names to nobody in
particular disclosed the tenant's entire module set and feature surface before anyone had proved
they may see it. Authenticated calls are untouched, including wrong ones — a bad key still
surfaces as the API's own envelope on the individual tool call.

`GET` is still 405 rather than 401, and the ordering is deliberate: that verb is not offered here
to *anyone*, so answering "authenticate first" would send a client off to complete a flow that
cannot fix it.

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

The screen's **"Which tools"** row picks the section (see [Sections](#sections-one-server-many-doors))
and rewrites the printed URL as you choose, with each option carrying its live tool count — the
count is the argument, so it belongs on the control rather than in a paragraph underneath.

Claude Code:

```bash
claude mcp add --transport http schakl https://<your-domain>/mcp \
  --header "Authorization: Bearer schakl_…"
```

Any other Streamable-HTTP client: endpoint `https://<your-domain>/mcp[/<section>]`, header
`Authorization: Bearer schakl_…` (or `X-API-Key: schakl_…`).

ChatGPT, as an app/connector, takes **`https://<your-domain>/mcp/compact`** — the full surface is
a hundred times its tool budget and it will refuse it outright.

**Or no key at all.** A client that speaks OAuth needs only the endpoint: it discovers the
authorization server from the `401` challenge, registers itself, and sends the user through a
consent screen on their ordinary session. Connected clients are listed on the same settings
screen, and disconnecting one revokes every session it ever opened.

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
  owner asked for the full API. A cautious instance mints keys with read scopes only. The same
  applies to a *section*: `/mcp/google-ads` carries that module's writes too, because the point
  of a Google Ads agent is to change campaigns — what it may actually change is the key's
  business, and `compact` is read-only because a chat client is where an unasked-for call is
  most likely.
- **Core routes belong to no section.** `/settings`, `/roles`, `/api-keys`, `/nav`, `/prefs` and
  the rest are the instance's own administration, and an agent doing a job does not administer
  the instance. A rule rather than an oversight, so a core surface worth offering gets added with
  a reason beside it.
- The per-module `mcp.py` seams (curated, hand-written tools like `companies.find`) remain
  the path for *richer* tools than a 1:1 endpoint mapping; the OpenAPI-derived set is the
  baseline that keeps every route reachable.
- The MCP sub-app's session manager starts inside the API's lifespan (`app.main.lifespan`).
  Tests that exercise `/mcp` must enter it explicitly — see `tests/test_mcp_api.py`.
- **Never** forward the incoming MCP credential to any *external* service (confused-deputy);
  the proxy only ever calls the API in-process.
