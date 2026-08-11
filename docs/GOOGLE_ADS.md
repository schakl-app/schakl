# Google Ads — the module, and the MCP surface it exists for

> An agency's Google Ads work is answering questions ("what did July cost", "which search terms
> burned money without converting", "is this account still optimising toward a real conversion")
> and acting on the answers. Both halves are API operations here, which means both halves are
> **MCP tools**. Design rules live in CLAUDE.md §6, §12 and §15; this documents what shipped, what
> was verified against the live API, and the traps.

## 1. What is where

| Concern | Lives in |
|---|---|
| The REST transport, paging, backoff, error model | `app/core/googleads/client.py`, `.../errors.py` |
| The guard on the query passthrough | `app/core/googleads/gaql.py` |
| "Which Ads account is this client's" | `app/core/googleads/accounts.py` — protocol; `google_ads` registers the provider |
| Accounts, the credential, the depth, the writes | `app/modules/google_ads/` (`sku="google_ads"`) |
| The spend tile on a client's marketing dashboard | `app/modules/marketing/sources/gads.py` |

**The transport is in core because two modules need it and neither may import the other** (§6).
That is the only reason; core owns no Ads *data*.

`marketing` was here first — Google Ads shipped as a source adapter with its own MCC expansion,
its own developer token and its own GAQL. The move kept every one of its behaviours and took the
duplication out.

## 2. Verified against the API, not from memory

Checked against the **v25 REST discovery document**
(`https://googleads.googleapis.com/$discovery/rest?version=v25`, revision `20260721`).

- **v25** is current (released 2026-07-22); v24 and v23 are still live. Google sunsets a version
  about a year after it ships and then answers **404 on every path under it** — which is not a
  credential problem, an account problem or a scope problem, so it gets its own error class
  (`AdsVersionError`). `SCHAKL_GOOGLE_ADS_API_VERSION` is the escape hatch for a box that
  outlives its release; `DEFAULT_API_VERSION` in `core/googleads/client.py` is the plan.
- **No `google-ads` SDK.** It is synchronous gRPC over protobuf; this API is async on Python
  3.12 and already speaks to every other Google product over `httpx` + `authlib`.
- Reads: `POST /customers/{cid}/googleAds:search`. Writes: per-resource
  `POST /customers/{cid}/<resource>:mutate`, all supporting `validateOnly` and `partialFailure`.
- Headers: `Authorization: Bearer`, `developer-token`, `login-customer-id` (the MCC),
  `linked-customer-id`. OAuth scope `https://www.googleapis.com/auth/adwords` — already
  `SCOPE_ADS` in the `google` module and already requested by marketing's connect flow.
- **JSON encoding**: int64 → a **string** (`impressions`, `clicks`, `costMicros`, `criterionId`,
  ids); doubles → number (`ctr`, `conversions`, `averageCpc`, `searchImpressionShare`); enums →
  the string name; absent optionals are **omitted entirely**, which is what makes `null ≠ 0`
  fall out for free.
- Quota: Basic access is 15 000 operations/day. `QuotaErrorDetails` carries `retryDelay` —
  honour it, it is better than any ladder we would invent.

### Two integrity rules, adopted verbatim

1. **`ctr`, `conversion_rate` and impression share are fractions, not percentages.** `0.0453` is
   4,53 %. Multiply at the point of display and nowhere else.
2. **A non-computable ratio is `null`, never `0`.** A zero reads as "measured zero", which is a
   different claim from "not computable". A layer that normalises one to the other makes every
   report downstream of it lie.

## 3. Paging is `pageToken` only

`pageSize` exists on the request message and **Google ignores it**: the page is a fixed 10 000
rows. So a bounded read cannot be expressed by asking for fewer rows — it is expressed by a
`LIMIT` in the GAQL and by `MAX_ROWS` in the client. Hitting the ceiling **raises**; it never
returns a prefix (CLAUDE.md §17 — silently taking the first 2000 rows of 2500 is the worst
outcome available, because it looks like it worked).

## 4. A retry is safe for a read and never for a write

`googleAds:search` is idempotent. `campaigns:mutate` is not, and a retried create is a second
campaign spending a second budget. So the backoff ladder is on `search` alone. A `validateOnly`
mutate is a read in disguise and is retried like one.

`RESOURCE_EXHAUSTED` (the daily allowance) is **not** retried either: it will not reset inside a
request, and retrying it only spends the remaining quota faster.

## 5. A refusal is a oneof buried three levels down

Google Ads answers a `google.rpc.Status` whose `details[]` carries a `GoogleAdsFailure`, and the
field that says what happened has **167** possible names in v25. Reading the HTTP status alone
collapses three different sentences, with three different people who can fix them, into one 403:

| What Google says | What it means | Who fixes it |
|---|---|---|
| `USER_PERMISSION_DENIED` | this Google login has no grant on that account | the client, or whoever manages the MCC link |
| `DEVELOPER_TOKEN_NOT_APPROVED` | the agency never finished the API Center application | the agency, at Google |
| `CUSTOMER_NOT_ENABLED` | the client's account is suspended | the client, in billing |

`classify()` maps the oneof, and the developer-token values **outrank their group** because
"reconnect your Google account" is the wrong instruction for all of them.

**Nothing that leaves the process carries a credential.** The developer token has no
recognisable shape and Google quotes it back inside `trigger` when it is the thing at fault, so
it is scrubbed **by value**; refresh tokens (`1//…`), access tokens (`ya29.…`), client secrets
(`GOCSPX-…`) and bearer headers go by pattern.

Google's own sentence is genuinely useful, so it lives on `google_ads_accounts.last_error` where
an admin can read it. The error **envelope** carries an i18n key and never provider text (§9).

## 6. Absence raises; it never returns `None`

A `None` customer id reaches the URL builder and asks Google about a customer named `None`.
That comes back **404**, which this module's own error model reads as *"the API version is
sunset"* — the most misleading sentence available for what is really an unlinked account.

`AdsNotConfigured` is a presentable state, not a bug: the picker, the dashboard tile and the sync
each draw it as "Ads is not set up yet". A **default no-op provider is registered at import**, so
the seam answers even on an instance where `google_ads` is disabled, and marketing degrades to a
label rather than an ImportError that would take the API and the worker down with it.

## 7. The account link, and why marketing keeps a copy

`google_ads_accounts` is the authority for *which customer, through which manager, on whose
grant*. `marketing_links.google_ads_account_id` points at it.

`marketing_links.external_id` stays populated anyway, and that is deliberate rather than
leftover: it is what the panel prints and what `deep_link` builds from, and
`SourceMetrics.external_id` is typed `str`. A `None` there is a validation error — and company
panels compose with **no per-panel `try`**, so one unlinked Ads account would 500 the *entire*
company hub rather than blank one tile. The join is the truth; the column is a display copy with
a stated owner.

`UNIQUE (org_id, customer_id)` deliberately excludes `company_id`: two companies legitimately
share one Ads account (a holding and its trading name), and `marketing_links` has never
constrained it either. That is also what makes `attach()` an idempotent upsert another module's
write path can call without ever risking a unique violation it would surface as a 500.

## 8. The credential moved house (expand/contract)

`google_ads_settings.developer_token_encrypted` is the new home. The migration **copied** the
ciphertext rather than moving it — Fernet derives one process-wide key with no per-row salt, so a
byte copy decrypts identically — and marketing still reads its own legacy column.

Resolution order in `marketing.resolve_ads_developer_token`:

1. the `google_ads` module, through the seam (where an admin rotates it now);
2. marketing's own legacy column, so an install that never enabled the module keeps working;
3. the deprecated `SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN` env var.

The **contracting** release drops the legacy column, that branch, and marketing's own Ads token
field. Until then both answer the same thing on an upgraded box.

## 9. The query passthrough

`POST /google-ads/accounts/{id}/query`, gated on its own `google_ads.query.run`.

Two risks are **not** on the list, structurally:

- **Cross-account access.** The customer id is in the URL path, built from our own row. A GAQL
  query cannot name a customer.
- **Mutation.** GAQL has no write syntax and `googleAds:search` has no mutate verb.

What is left is genuinely ours to bound, and `gaql.check()` does it:

- `FROM <resource>` must be in an **allow-list** (~35 of v25's 183). `customer_user_access`
  (every login's e-mail), `billing_setup` / `account_budget` (payment data) and
  `customer_client_link` (the MCC's whole client tree — how a scoped key would enumerate
  accounts it was never linked to) are absent on purpose.
- `LIMIT` is imposed, not requested, and clamped at `MAX_LIMIT`. A clamp is **reported** in
  `warnings`.
- A query selecting `metrics.*` with no date bound is refused: it is the most expensive shape
  available and the answer is almost never what was wanted.

Parsing is **quote-aware, not a regex**. `WHERE campaign.name = 'FROM billing_setup'` contains
the word FROM, and a guard using `\bFROM\s+(\w+)` would run its allow-list check against a string
literal the caller controls.

## 10. Permissions, and why the writes are split four ways

The obvious design is one `google_ads.write`, and it is wrong for the reason this module exists:
the surface is reached over MCP by an agent holding an API key, and **a key carries permission
scopes**. One write key means the only key you can mint may do everything — so "let the assistant
clean up search terms overnight" and "let the assistant change budgets" become the same grant.

| Key | Gates |
|---|---|
| `google_ads.settings.manage` | the developer token, linking, verifying |
| `google_ads.account.read` | every read (default `admin`, `member`) |
| `google_ads.policy.manage` | the per-client Ads policy and the decisions log |
| `google_ads.query.run` | the passthrough above |
| `google_ads.campaign.write` | campaigns, ad groups, ads |
| `google_ads.budget.write` | budgets — separate, because this is the money |
| `google_ads.keyword.write` | keywords |
| `google_ads.negative.write` | negatives and shared lists |

All admin-only by default and **never `client`** (#266): before granting any of these to the
seeded client role, list every route the key gates — `account.read` covers cost-per-click and the
agency's own spend, which no company horizon narrows into something safe to show a client.

Beside the permissions there is one instance-wide kill switch,
`google_ads_settings.writes_enabled`. The permission decides *who*; this decides *whether*, in one
place an owner can reach in a hurry without editing eight role grants.

## 11. Traps

1. **The MCC is the normal agency shape, not an edge case.** `listAccessibleCustomers` answers
   *direct* grants only, and an agency's user is granted the manager — so the raw list is one id
   and a picker built on it is empty of every client the agency runs. Each manager is expanded
   with one `customer_client` query, and every child is tagged with the manager it must be
   reached through. Without that tag, every later call is made by a login holding no grant on
   that account and 403s.
2. **Manager accounts are never offered as linkable.** Google refuses metric queries against one,
   so linking it produces a permanently erroring row rather than a roll-up.
3. **Dates are in the *account's* timezone**, not the org's. `google_ads_accounts.time_zone`
   holds Google's own answer for exactly this reason.
4. **Ads reports money in micros**, as an int64 — i.e. a JSON string. Divide by 1 000 000 *after*
   coercing, or a plausible-looking concatenation or zero reaches a chart.
5. **A health flag that only ever turns on is a bug with a long tail.** `verify` clears `status`
   and `last_error` on success, or a row nothing is wrong with keeps its red line forever.
6. **Every in-request Google call goes inside `ctx.release_db()`**, entering the client *first*.
   A request pins one pool connection for its whole transaction; held across a thirty-second
   call, a handful of these drain the pool and the site appears to freeze.

## 12. Testing

`core/googleads.set_transport()` is the only network seam, and it is unset in production: a test
that forgets to stub fails on connect against `googleads.googleapis.com` rather than quietly
passing. `acting_as` takes the transport through, so tests exercise the **real** header builder,
paging and error classification rather than a stub of them.

- `tests/test_google_ads_core.py` — the GAQL guard and the failure classifier. No database, no
  network; fast enough to run alone.
- `tests/test_google_ads_api.py` — settings and account CRUD, tenant isolation, the company
  horizon on the parameterless account list, and that marketing keeps working through the seam.
