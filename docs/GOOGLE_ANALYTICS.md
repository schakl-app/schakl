# Google Analytics

> A live GA4 read surface, and the reason it exists as its own integration rather than as more
> depth in `marketing`. Business-licensed (`sku="google_analytics"`).

## 1. What it is, and what it deliberately is not

`marketing` already reads GA4. It reads it for **one** purpose: a small nightly aggregate per
linked client, folded into a dashboard beside Search Console, Google Ads, Rank Math and SE
Ranking. That is the right shape for *how is this client doing* and the wrong shape for every
other question anybody asks Analytics — which pages, which sources, which events, is the tracking
even working, what happened at 14:00 yesterday. Those need the property's **own** vocabulary: its
custom dimensions, its key events, its metadata document. A cross-source dashboard cannot carry
that without becoming a GA4 client with four other sources bolted to it.

So this is an **integration** by §6a's test — it stores nothing, owns no capability of ours, and
with the vendor gone it is gone rather than poorer. It mirrors nothing and has no models, no
migration and no cron: every answer is fetched live, under the asking user's own grant.

It **requires `google`** (the credential is a `google_connections` row carrying
`analytics.readonly`, and there is no second way to obtain one) and deliberately **not**
`marketing`: an agency that wants an agent able to answer Analytics questions should not be made
to switch on a licensed dashboard module it did not ask for. The two never read each other's rows.

## 2. The route list is the tool list

Seventeen GET routes under `/api/v1/google-analytics`, which is simultaneously the HTTP API and
**`/mcp/google-analytics`** — a dedicated Analytics tool group, derived from the router prefix and
therefore self-maintaining (`app/core/mcp/sections.py`). A route added tomorrow is served
tomorrow, and there is no hand-written list of tools to fall out of step with the code. It also
joins the `growth` bundle, which names modules and never tools.

That is the whole reason the package exists rather than a curated section naming GA tools inside
`marketing`: a curated list is a second copy of a router, and the copy is only ever wrong later,
silently, in the direction of a tool the module ships and the section does not offer.

**What exists** (Admin API v1beta) — `properties`, `properties/{id}`, `data-streams`,
`key-events`, `custom-dimensions`, `custom-metrics`, `google-ads-links`, `firebase-links`,
`data-retention`.
**What this property will answer** (Data API v1beta) — `metadata`, `compatibility`.
**What happened** — `overview`, `timeseries`, `breakdown`, `realtime`, `report`, `pivot`.

Beside them, six curated `mcp_tools` for the in-app assistant, where a single call beats several
plus a judgement: `google_analytics.properties` (grounding), `.overview`, `.breakdown`,
`.timeseries`, `.realtime`, `.report`, and `.setup` — key events, data streams, the Ads link and
the retention window in one answer, which is what "is the tracking working" actually asks.

## 3. Every operation is a read

Not a phase. There is nothing in a client's GA4 property this platform has any business writing:
the property is theirs, its configuration belongs to whoever set the tags up, and the one thing an
agency does with Analytics is ask it questions.

All-GET has a second consequence worth stating: the licence write gate reads the **method**, so an
instance whose licence lapses keeps reading Analytics. Data is never hostage (epic #140).

## 4. Permissions

Two keys, split the way `google_ads` splits its curated reads from the GAQL passthrough:

| Key | Default | What it reaches |
|---|---|---|
| `google_analytics.property.read` | admin, member | Properties, configuration, metadata, overview, breakdown, timeseries, realtime |
| `google_analytics.report.run` | admin | `report`, `pivot`, `compatibility` — any dimension crossed with any metric |

The split is not "reads versus writes" (everything here is a read). It is *questions somebody here
designed* versus *any question at all*, and it exists because this surface is reached over MCP by
an agent holding an API key, where a key carries permission scopes. An agency can hand an agent
the curated shapes and nothing else.

**Neither is ever `client`** (#266). A GA4 grant is not narrowed by a company horizon and there is
no row here for one to narrow: this is the *agency's* Google account, not the client's data in our
database, and the two are not interchangeable.

## 5. Two APIs, one credential

GA4 answers through two services and neither can answer the other's questions. The **Admin API**
(`analyticsadmin.googleapis.com/v1beta`) knows what exists; the **Data API**
(`analyticsdata.googleapis.com/v1beta`) knows what happened, and owns the metadata document
listing what a property will actually accept. Asking the wrong one is a 404 about a resource that
plainly exists, which is the least helpful error available here — so the two bases are named once,
in `client.py`, and everything goes through `get` / `post`.

Both ride `google.client.acting_as` on `analytics.readonly`. Raw tokens never reach this module:
it is handed a client, never a credential. Request paths make the Google call inside
`ctx.release_db()` — a GA4 report is a second or two of somebody else's latency, and holding a
Postgres connection across it is how thirty clients drain a pool.

The one network seam is `client.set_transport`, at the **transport** rather than at `acting_as` or
the service, so a test travels the real OAuth client, path builder, paging loop and error
classifier. Unset, every call goes to Google — a test that forgot to install a fake fails loudly
on connect rather than quietly passing.

## 6. What GA4 gets wrong if you write the parser from memory

- **`conversions` no longer exists; it is `keyEvents`.** Asking for the retired name does not
  return zero, it 400s the whole report. Nothing here ever asks for it.
- **Every metric value is a string**, integers included. `num()` is the wire format, not defensive
  programming.
- **`totals` is a row Google sends**, not a sum of the column — and it must stay that way, because
  `engagementRate`, `bounceRate` and `averageSessionDuration` are *weighted*. A column of ratios
  added up is a number that is none of them.
- **A report answers with headers.** The shape is read back from the response rather than assumed
  from the request: a metric Google declined to return would otherwise shift every column one to
  the left, silently, with every number still plausible.
- **Dates are days in the property's own reporting timezone**, which is neither the org's nor the
  viewer's. It is reported beside the numbers rather than substituted for the org's clock — the
  platform has one answer to "what is last month" and this must not become a second one.

## 7. How it refuses

- **A credential's absence is evidence about that credential, never a verdict on the screen**
  (#399/#411). `GET /properties` *reports* `connected` / `has_scope`, because "connect Google" and
  "allow Analytics" are different acts and only the payload can say which is missing. A call that
  **names** a property refuses, because by then there is a specific thing being asked for — with
  three distinct sentences for the three states, since exactly one person can act on each.
- **A short answer says it is short.** A listing that hits its page ceiling sets `truncated`; a
  report carries `row_count` beside its rows. A prefix presented as a whole is the worst answer
  available: it looks like it worked.
- **A sampled or thresholded answer is reported as one.** A sampled number reads as a count on
  every screen it lands on; a thresholded one withholds rows about small audiences, so the parts
  not adding up to the total is a fact about Google's privacy rules rather than a bug.
- **A filter clause that parses as none of the three operators is refused, never dropped.**
  `name==value` (exact), `name=@value` (contains), `name=^value` (begins with). A filter silently
  ignored answers a different question with every row still valid — the SnelStart `$filter`
  failure, in a query string.
- **Google's refusal travels as identifiers, never as its own prose** (§9): `google_status` and
  `google_reason` in `details`, untranslated vendor English nowhere. A 400 comes back **422** —
  "fix your request" and "the provider is down" are instructions for two different people.

## 8. Cost

`overview` is **one** `batchRunReports` carrying three report requests (this period, the compared
period, the channel split), not three calls. GA4's quota is per property per day, and a screen
that costs three of everything is a screen somebody stops opening. `setup` is deliberately the
opposite trade — four Admin reads folded into one tool — because the reads are cheap and the
judgement is the part a model gets wrong when it has to remember to make the fourth call.

Row counts are clamped (250 for a report, 50 for realtime, 25 by default): the difference between
25 rows and 100 000 is not a difference in the question.

## 9. There is no web package

It contributes no screen, no panel and no nav item — the surface is the API and the MCP section —
so there is nothing for `apps/web/src/lib/integrations/` to mirror. It still appears under
Instellingen → Integraties, because that screen reads `module_kinds` from `/meta/modules`, which
is the API's own declaration. Give it a settings screen the day it needs one, and register the web
half then.
