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
| Accounts, the credential, the depth, the writes | `app/integrations/google_ads/` (`sku="google_ads"`) |
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

### The mirror runs both ways, or one screen lies (#338)

`MarketingService._attach_ads_account` has always recorded the account here when a marketing
link was created. The other direction had no mirror, and half a fact is worse than none: an
account linked through `POST /google-ads/accounts` — which is what Instellingen → Google Ads
posts, and what the client page's Google Ads panel used to send everybody to — wrote *only* the
account row. So a client's Google Ads panel listed the account while the marketing panel
directly above it went on saying *"koppel een Google-account om Analytics, Search Console en Ads
van deze klant te tonen"*, and `/marketing` listed the client as having no source at all. The
Ads was connected; two screens said it was not, and the cure (do it again, in the other panel)
was discoverable from nothing on any of them.

`attach()` now emits **`google_ads.account.attached`** on the in-process event bus and
`marketing` subscribes it (`modules/marketing/events.py`). Four things about it are load-bearing:

- **The seam is the bus, not an import.** This module names `marketing` nowhere, and an instance
  that never enabled marketing simply has no subscriber (CLAUDE.md §6).
- **Only an account with a `company_id` emits.** A marketing link requires a company, and an
  account attached to none is the agency's own — which is exactly why the hand-typed form in
  Instellingen still exists and still has an "Ons eigen account" option.
- **The handler writes the link row directly** rather than calling `create_link`, which would
  re-enter `_attach_ads_account` → `attach` → the handler. It would terminate, but a write that
  recurses through two modules to settle is not a thing to have to re-derive.
- **It matches on the normalised id.** `marketing_links.external_id` holds whatever the caller
  stored (`1242643293`, `124-264-3293`, `customers/1242643293`) while `customer_id` is normalised
  on the way in, so comparing the raw text would miss the link that already exists and mint a
  second one for the same account.

The handler is a side effect of a write the caller was already allowed to make, so it asks for
no permission of its own (§16's rule for the activity trail) — but it **does** check
`sku_writable("marketing")`, because a mirror is still a write into a licensed module and an
expired one goes read-only rather than half-writable.

The web half follows from the same sentence: there is now **one** connect control
(`MarketingConnectDialog`), it posts to `POST /marketing/links`, and it is reachable from the
client's page, from the client's Marketing tab, from `/marketing/google-ads` and from
`/marketing`. It is gated on `marketing.link.manage` — the key the call actually makes, not
`google_ads.settings.manage`, which is what the screen is *about* (#310).

**This module draws no card on the client hub** (#411). It used to: a half-width card under the
marketing panel listing this client's Ads accounts and whether each still answers. Both facts
are the marketing panel's `gads` row and its health badge one card up, and the card's connect
button was already `MarketingConnectDialog`, so what it added over its neighbour was a second
place to read the same answer. `last_error` and `last_verified_at` are still stored, still on
`/marketing/google-ads` and still in the MCP surface; what the hub keeps is the badge.

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

## 9a. The read surface *is* the tool surface

Every `/api/v1` operation becomes an MCP tool (§12), so the route list is the tool list. Three
things follow, and they are why the handlers look the way they do:

- **The docstring is the tool description** an agent reads to decide whether to call it. It says
  what the read answers *and what it does not* — a model cannot see the source.
- **The parameters are the tool's arguments**, so they are named for the question (`period`,
  `campaigns`) rather than for the GAQL (`segments_date`, `campaign_id_in`).
- **Handler names must shorten uniquely** at `_api_v1_`, or the tool falls back to the full
  unreadable operation id. `google_ads_campaigns`, not `list_campaigns` — which would collide.

| Route | Answers |
|---|---|
| `snapshot` | account totals + every campaign — start an analysis here |
| `campaigns` / `ad-groups` | performance and settings, most expensive first |
| `keywords` | match type, bid, Quality Score |
| `negatives` | ad-group, campaign **and** shared-list exclusions in one answer |
| `search-terms` | what people typed, with what has already been decided about each |
| `ads` | ad strength and policy approval |
| `devices` / `geo` | per device; physical user location, not targeting |
| `conversions` | what the account optimises toward, and what it recorded |
| `changes` | field-level old → new, 30 days |
| `recommendations` | Google's own advice, with projected impact |
| `keyword-ideas` | `:generateKeywordIdeas` — volume and competition from seeds or a URL |
| `query` | the gated GAQL passthrough |

Beside them, `mcp.py` contributes three **curated** tools — the shapes where one call beats three
plus arithmetic a model should not be doing: `google_ads.accounts` (grounding a client name to an
id), `google_ads.overview` (a period against the same period a year earlier, deltas computed),
and `google_ads.wasted_spend` (costly non-converting terms **minus what is already excluded** —
the cross-reference is the part a model gets wrong, and a "recommendation" to exclude something
already excluded wastes an account manager's afternoon).

### The envelope

Every read returns the same shape: which account answered, the period *with its dates*, the
account's currency and timezone, `fetched_at`, `row_count`, `total_rows`, `offset`, `totals`,
`rows`, `extra` — and `warnings`.

**`warnings` is load-bearing.** Truncation, a shortened change-history window, a geo read that
fell back to country level, provisional recent figures and a filter that matched no campaign are
reported there and nowhere else. A caller that ignores it will eventually present a capped list
as a complete one.

Three details that cost a debugging session each if missed:

- **`totals` re-derives its ratios** from the summed components. The average of thirty daily CTRs
  is not the CTR of thirty days.
- **A period is resolved in the account's timezone**, so `last_month` for an account set to
  America/New_York is a different set of impressions than last month in Europe/Amsterdam. It is
  the one wall-clock question in the product that does *not* resolve against the org (§8), and
  the data is the reason.
- **`resolve_campaign_ids` returns `None` for no filter and `[]` for no match.** Collapsing them
  turns a typo in a campaign name into a report on the whole account. `_empty()` is where the
  distinction is acted on.

Money is rendered in the **account's** currency on the web, not the tenant's: an agency in
Amsterdam runs accounts billed in GBP and SEK, and `fmtMoney` — right everywhere else in the
product — would label every one of them `€`.

### The page, and the order the three steps run in

Every list read takes `q`, `limit` and `offset` (`reporting.Slice`), and the four reads whose rows
carry a Google status take `status` as well. They are carried as **one object** for a reason that
is not tidiness: the order they are applied in is the whole correctness argument, and
`ReadResult.narrow` is the single place it lives.

**Filter, then total, then slice.** Each of the other orders is wrong in a way nothing on the
screen can reveal:

- **Filtering the page searches a prefix.** Page 1 of a search for "dakraam" would show only the
  dakraam terms that happened to be among the twenty most expensive, and a reader cannot tell that
  from an account with three of them — CLAUDE.md §9's sample-of-itself, one layer in from the URL.
- **Totalling the page** prints "Totaal" under fifty rows over a figure describing nine hundred,
  or the reverse. The footer describes the list, so a filtered list gets filtered totals.
- **Counting after the slice** makes the pager say "1 tot 50 van 50" on every page — the truncated
  total (#37) again.

Three consequences worth stating:

- **The search runs in Python and never as a GAQL literal.** That is the same rule
  `resolve_campaign_ids` follows for the campaign filter — no caller-supplied string reaches the
  query text, ever. It costs nothing, because the fetch is the read's own `LIMITS` ceiling either
  way, and it is what lets the search see the whole list rather than whatever a `LIKE` would have
  let Google return. `SEARCH_FIELDS` names the keys per read, deliberately: a search over every
  key of a row matches ids and micro amounts nobody typed, so "1" would find every campaign.
- **A page is not a truncation.** `rows_truncated` means Google had more rows than the read's
  ceiling and they are gone; a page is fully described by `total_rows`, and warning about it would
  cry wolf until nobody reads the warning that does mean something. The two are tested apart.
- **`status=REMOVED` widens the fetch it filters.** `include_removed` decides what Google is asked
  for and `status` decides what is kept, so the one status a person picks on purpose would
  otherwise be the one that always answers nothing. Reconciled in `reads.py`, never left to
  whoever built the URL.

On the web, the report screen renders the shared `FilterBar` and `Pagination` like every other
list: `?page=`/`?size=` in the URL, `resetPage` on every filter, and the size saved per **view**
(`reportTableId`) because a five-column exclusions list and a twelve-number keyword list are
different tables that happen to share a route. Switching tab or period rebuilds the URL from
scratch, which is how it drops the page, the search and the status together — a search for
"dakraam" carried into the negatives tab would open it looking empty for a reason nothing on the
screen explains.

## 9b. The nightly mirror, and what it is for

Two tables (`google_ads_metrics_daily`, `google_ads_changes`), one cron at **05:15** — after
`marketing`'s 04:45, because both walk every org making outbound Google calls and stacking them
on one minute is how a box with thirty clients meets its own rate limits at four in the morning.

**The point is that a comparison stops being a second API call.** A tile showing this month
against the same month last year is otherwise two live Ads reads per client per page load,
against a shared daily operation quota, for figures that stopped changing weeks ago. `GET
/google-ads/accounts/{id}/trend` answers entirely from stored rows: fast, free, and it still
renders when Google is down.

Three properties make the mirror safe, and each is a way this normally goes wrong:

- **A re-run overwrites; it never appends.** The window is re-pulled every night because Ads
  conversions keep arriving for days after the click — a day read once is a day read too early.
  Both tables therefore carry a key describing what a row *is*, and `dim_key` is `NOT NULL
  DEFAULT ''` rather than nullable: Postgres treats NULLs as distinct inside a unique
  constraint, so a nullable key column silently turns every upsert into an insert.
- **Google gives change events no id at all**, so a change is identified by `(instant, resource,
  operation)`. Less specific collapses two real edits; more specific (the changed fields)
  re-inserts the same event whenever Google fills its own history in a little further.
- **One broken account does not stop the others.** A failure is recorded on the row
  (`last_sync_error`, separate from `last_error` because verify and sync ask different
  questions) and the loop continues. A sync that raised would leave nineteen working accounts
  unsynced because of one revoked grant.

What is **not** stored is as deliberate: keywords, search terms, negatives and ads stay live.
They are unbounded, they change constantly, and the live read answers them better. Only the
bounded dimensions a *trend* needs — account, campaign, device — are mirrored.

`missing_days` on the trend payload says how many days of the window have no stored row. That
means "not synced yet", never "no spend", and the difference is why it is reported rather than
smoothed over: a chart with a silent gap makes the second claim while meaning the first.

The module contributes two report sections (#300): a **client**-facing performance table and an
**internal** change summary. Both read the mirror, which is what makes a report of last March
still printable next March — and the split is because of what the second one says. "The daily
budget went from 40 to 400 on the 3rd, by stan@" is exactly the sentence an agency wants in
front of itself and exactly the one it does not want in front of the client whose budget that
was.

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

## 10a. The policy, and the decisions log

`google_ads_policies` and `google_ads_decisions` are what turn the tool surface from a data pipe
into something an agent can reason with. Neither reaches Google.

**One table, and `account_id IS NULL` is the agency's house policy.** The alternative — a
per-account table beside a block of columns on `google_ads_settings` — is the same vocabulary
written twice, with two validators and two schemas that drift the first time a field is added to
one. One record type makes `policy.resolve()` one function, and the house row is editable through
the same endpoint as an account's.

That needs a **partial unique index**: `account_id` is nullable, and Postgres treats NULLs as
distinct inside a unique constraint, so `UNIQUE (org_id, account_id)` alone permits any number of
house rows and "the house policy" quietly becomes "whichever one came back first". The same lesson
`dim_key` learned by being `NOT NULL DEFAULT ''`, in the one place that shape is not available.

**It hangs off the account, not off the client**, though the issue that asked for it said
"per-client". Three reasons, and they are the reasons `google_ads_accounts` is itself a row: a
write always names an *account*, so the policy guarding it must be findable from one without
guessing; `company_id` is nullable, so a company-anchored policy could never cover the agency's own
account; and one client legitimately runs two accounts — a brand and a shop — whose protected terms
and budget ceilings are not the same rules.

### Three layers, and they must not fuse

#300's rule, applied to advertising instead of prose: **product invariants are code, the agency's
standing rules are a row, and what is true about one advertiser is a row.**

Lists **union**, scalars **inherit**, prose **stays separate**. A house exclusion list an account
could silently replace is a list nobody can rely on; a house steering paragraph concatenated onto a
client's is how *"we never bid on competitor names"* and *"this client sells competitor parts"*
become one contradictory instruction. So the resolved policy hands a model two labelled strings
(`agency_steering`, `account_steering`) and lets it hold both.

Exactly one value is built in: `max_budget_increase = 1.0`. The choice of *which* guard is built in
is the whole argument — a **relative** ceiling needs no knowledge of an account, so it can be
defaulted honestly, and it catches the extra zero (10× is not 2×) while permitting an ordinary
seasonal change. An absolute one cannot be defaulted at all: any figure invented here would refuse
a legitimate budget on one account and wave through a mistake on another. The consequence is worth
stating plainly, because it is the gap somebody will otherwise find the hard way: **a budget
*create* has no previous amount, so nothing relative can bound it** — an account with no
`max_daily_budget` bounds a new budget by the permission alone.

### What is enforced, and the check worth reading

`protected_terms`, `banned_phrases`, `max_daily_budget`, `max_budget_increase` and `max_cpc` are
checked before a mutation leaves the process. The rest shapes what an agent *proposes*.

**A proposed negative is refused only when it would actually block a protected term.** A naive
version refuses any exclusion *containing* a protected word, and it is wrong in the direction that
matters: an EXACT negative on `beugel kosten` cannot stop `beugel` from serving, so refusing it
teaches an agency that the guard cries wolf — and the next thing they do is switch it off. So
`policy.blocks()` models Google's own matching: EXACT blocks only the identical term, PHRASE a term
containing the words in order and adjacent, BROAD a term containing all the words in any order. An
unknown match type is treated as BROAD, because the failure direction here must be "refused
something harmless" rather than "let a client's brand go dark".

### A call-level refusal raises; a row-level one is reported

CLAUDE.md §18, landing exactly. A budget over the ceiling **is** the call — one budget, one answer
— so it is a 422 naming the field and the limit (#305: show the constraint working). A protected
term inside a batch of twelve exclusions is one row: refusing all twelve because the guard did its
job on one of them punishes the caller for something that worked. So it is skipped, reported in
`skipped`, and the protected term it *would* have blocked is named — "refused" invites an argument
with the software, "would also block *beugel*" invites a fix.

### The decisions log, and the one entry that exists nowhere else

Append-only, newest-wins per `(subject, scope)`. Everything in it except one kind is observable
from the account afterwards; **`kept` is not.** "We looked at this search term and chose not to
exclude it" leaves no trace in Google at all, which is exactly why the same term is proposed again
next month, and the month after, until the account manager stops reading the list.

Which is why `POST /negatives` takes a `keep` array beside `terms`: a review pass decides both
halves at once, and a log holding only the exclusions re-proposes everything that was kept. It
rides `negative.write` rather than `policy.manage` because a key that may exclude a term may
certainly record that it chose not to — strictly the weaker act under the stronger key.

**There is deliberately no unique index on the log, and that inverts the payments rule.** CLAUDE.md
§10 says an idempotency guarantee belongs in the database, and it says so because a duplicate
`InvoicePayment` is money counted twice. A duplicate history row is a duplicate history row. The
service refuses to append a decision identical to the standing one; a race that slips two through
costs one redundant line, where a constraint would 500 an agent's ordinary second call and would
make "excluded in March, kept in June, excluded again in September" unrecordable.

`expires_on` is nullable and usually NULL, but a permanent silence is the wrong default for a
judgement about a market: "not worth excluding at today's CPC" stops being true, and without a date
nobody revisits it.

Reading the log is `account.read` — it is context every proposal needs, and `wasted_spend`
subtracts it. *Recording* a standing decision is `policy.manage`. The write routes record their own
decisions under their own keys, because recording is a side effect of a write the caller was
already allowed to make and never its own grant (§16).

## 10b. The write surface

Nineteen routes across the six mutate resources, each declaring one of the four write keys.

| Route | Key | Notes |
|---|---|---|
| `POST` / `PATCH` / `DELETE /budgets` | `budget.write` | the money |
| `POST` / `PATCH` / `DELETE /campaigns` | `campaign.write` | created **PAUSED** |
| `POST` / `PATCH` / `DELETE /ad-groups` | `campaign.write` | created **PAUSED** |
| `POST /keywords`, `PATCH /keywords`, `POST /keywords/remove` | `keyword.write` | batched |
| `POST /negatives`, `POST /negatives/remove`, `POST` / `DELETE /negative-lists` | `negative.write` | batched |
| `POST` / `PATCH /ads`, `DELETE /ad-groups/{id}/ads/{id}` | `campaign.write` | created **PAUSED** |

Seven things are worth knowing before touching it.

**Deleting is its own verb, because at Google it is its own operation.** `status: "REMOVED"` is
output-only — sending it answers `requestError.INVALID_ENUM_VALUE`, *"Enum value 'REMOVED' cannot
be used"* — so for a year the `PATCH` routes documented a removal they could never perform, and
keywords and negatives were removable only because they were the two resources with a route that
built a real `remove` operation. `_status_and_name` now refuses the enum where the message can
name the route that works, and every created resource has a `DELETE`. Two consequences worth
knowing: removing a campaign does **not** cascade its children to REMOVED and afterwards they
cannot be removed at all (`contextError.OPERATION_NOT_PERMITTED_FOR_REMOVED_RESOURCE`), so a clean
tree is deleted leaf-first; and `validate_only` is a query parameter on `DELETE` rather than a
body, because a DELETE body is dropped by enough proxies that "it validated" and "it deleted"
would be one silently-ignored field apart.

**Creating a campaign takes an existing `budget_id`, and cannot make one.** That is the four-way
split holding: creating a budget is somebody's decision made with `budget.write`, and a campaign
route that could conjure one would make `campaign.write` a budget key with extra steps. It is also
what keeps the act atomic — two mutates cannot be one transaction, so a campaign create that failed
after its budget succeeded would leave an orphan nobody goes looking for. `PATCH /campaigns` will
not move a campaign onto a different budget either, for the same reason: the field lives on the
campaign but its effect is "this campaign now spends up to a different number".

**`validateOnly` is on every one**, and it is the real dry run: Google validates against the
*actual* account structure and applies nothing. Better than a test account, which serves no ads and
therefore holds nothing worth validating against.

**Partial failure is a property of the route, not of the batch size.** The batch routes always send
`partialFailure: true`; the single-resource ones never do. Deciding it from the runtime operation
count would mean an agent excluding one term gets a raised error and one excluding two gets a
per-row report — the same tool answering in two shapes depending on how much work it was given.

**A partial failure arrives on an HTTP 200** and `classify()` cannot see it: `partialFailureError`
is a bare `google.rpc.Status` rather than the `{"error": …}` envelope every other failure path
walks. `errors.partial_failures()` is the reader, and it keeps
`location.fieldPathElements[].index` — the only link from a refusal back to the operation that
caused it. `results` still carries one slot per operation and the refused ones are **empty
objects**, so a client that reads "a result means it worked" reports eleven successes as twelve.

**`updateMask` is derived from the body it is sending**, never taken as an argument. A hand-written
mask and a hand-written body are two spellings of one list, and the day they disagree Google
applies the intersection and reports success. It is lowerCamelCase in REST (`amountMicros`),
matching the JSON body rather than the proto.

**Nothing raises after Google has been changed.** `ctx.release_db()` commits on entry, so anything
written after the client block is rolled back by `require_context` if an exception escapes it — a
mutation Google applied whose decision row was rolled back is the worst state available here. Once
the mutate returns, every remaining problem becomes a warning on the outcome.

### What the browser can do, and what it deliberately cannot

The screens carry the two acts a person performs on them: **reviewing a search-terms list**
(exclude some, keep the rest, one request) and **pausing or resuming a campaign**. Creating a
campaign, an ad group, a keyword set and an ad is the MCP surface's job — it is four dependent
calls with a budget decision in the middle, and a browser wizard for it is not what this module is
for.

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
7. **A campaign create needs `contains_eu_political_advertising`.** Required in v25 — the EU
   political advertising regulation — and a create without it fails every time with
   `fieldError: REQUIRED` naming a field that appears in no integration guide. It is an argument
   on the route (`eu_political_advertising`, default false) rather than a constant, because it is
   a legal declaration made on the advertiser's behalf. Found by replaying our own payload against
   a live account after every create had failed silently behind a 502.
8. **`campaign.start_date` does not exist.** v25 names them `startDateTime`/`endDateTime`, and
   the shorter name is an `UNRECOGNIZED_FIELD` query error rather than a null. Check the
   discovery document before adding a field: `curl "https://googleads.googleapis.com/$discovery/rest?version=v25"`.
9. **`user_location_view` carries only `country_criterion_id`.** Country, region and city come
   from *segments*, and the resource names they return (`geoTargetConstants/2528`) need a second,
   batched lookup to become readable places.
10. **`change_event.old_resource` is a wrapper**, not the message. Its single populated key names
   the resource type, and `changedFields` is a FieldMask relative to what is *inside* it. Walking
   from the wrapper yields "from null to null" for every change — plausible and useless.

## 12. Testing

`core/googleads.set_transport()` is the only network seam, and it is unset in production: a test
that forgets to stub fails on connect against `googleads.googleapis.com` rather than quietly
passing. `acting_as` takes the transport through, so tests exercise the **real** header builder,
paging and error classification rather than a stub of them.

- `tests/test_google_ads_core.py` — the GAQL guard and the failure classifier. No database, no
  network; fast enough to run alone.
- `tests/test_google_ads_api.py` — settings and account CRUD, tenant isolation, the company
  horizon on the parameterless account list, and that marketing keeps working through the seam.
