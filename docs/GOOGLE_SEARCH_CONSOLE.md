# Google Search Console

> A live Search Console read surface, its own MCP section, and the honest answer to "how visible
> are we in AI Overviews". Business-licensed (`sku="google_search_console"`).

## 1. What it is, and what it deliberately is not

`marketing` already reads Search Console. It reads it for **one** purpose: a nightly four-metric
aggregate per linked client (clicks, impressions, CTR, position), folded into a dashboard beside
GA4, Google Ads, Rank Math and SE Ranking, with three live drill-downs (top queries, top pages,
movers). That is the right shape for *how is this client doing* and the wrong shape for every
other question anybody asks Search Console — which queries land on which page, is this URL
indexed and why not, which sitemap carries errors, what happened at 09:00 this morning, is the
site seen in Discover at all. Those need the property's own vocabulary and two APIs the dashboard
never calls (sitemaps, URL inspection).

So this is an **integration** by §6a's test — it stores nothing, owns no capability of ours, and
with the vendor gone it is gone rather than poorer. It mirrors nothing and has no models, no
migration and no cron: every answer is fetched live, under the asking user's own grant.

It **requires `google`** (the credential is a `google_connections` row carrying
`webmasters.readonly`, and there is no second way to obtain one) and deliberately **not**
`marketing`: an agency that wants an agent able to answer Search Console questions should not be
made to switch on a licensed dashboard module it did not ask for. The two never read each other's
rows. The one thing they share is a function — `client.generative_ai_report_url` — so that the
dashboard's AI-visibility card and the assistant's tool cannot point at two different URLs (§6a's
published-interface rule, the same way `marketing` already imports the scope constants from
`google.oauth`).

## 2. The route list is the tool list

Thirteen GET routes under `/api/v1/google-search-console`, which is simultaneously the HTTP API
and **`/mcp/google-search-console`** — a dedicated Search Console tool group, derived from the
router prefix and therefore self-maintaining (`app/core/mcp/sections.py`). It also joins the
`growth` bundle, which names modules and never tools.

**What exists** — `sites`, `site`, `sitemaps`, `sitemap`.
**What happened** (Search Analytics) — `overview`, `search-types`, `timeseries`, `breakdown`,
`hourly`, `movers`.
**What the index holds** — `inspect` (the URL Inspection API).
**Generative AI** — `ai-visibility` (§6).
**Any question at all** — `query`.

Beside them, seven curated `mcp_tools` for the in-app assistant: `google_search_console.sites`
(grounding), `.overview`, `.breakdown`, `.movers`, `.inspect_url`, `.ai_visibility` and `.query`.

### The property is a query parameter, never a path segment

A `siteUrl` is `sc-domain:klant.nl` or `https://www.klant.nl/` — a value with a scheme and
slashes in it. A FastAPI path parameter is decoded *before* it is matched, so `%2F` becomes `/`
and the route stops matching; Analytics can put its property id in the path because a property
id is a number. Every route here takes `?site=`, and `client.site_url` reads a bare hostname
(`klant.nl`, which is what a model will spell) as the domain property.

## 3. Every operation is a read

Not a phase. There is nothing in a client's Search Console property this platform has any
business writing: a sitemap is submitted by whoever deploys the site, a property is verified by
whoever owns the domain, and the one thing an agency does with Search Console is ask it
questions. All-GET has a second consequence worth stating: the licence write gate reads the
**method**, so an instance whose licence lapses keeps reading Search Console. Data is never
hostage (epic #140).

## 4. Permissions

| Key | Default | What it reaches |
|---|---|---|
| `google_search_console.site.read` | admin, member | Sites, sitemaps, overview, search types, timeseries, breakdown, hourly, movers, URL inspection, AI visibility |
| `google_search_console.report.run` | admin | `query` — any dimensions, filters, aggregation and data state |

The split is *questions somebody here designed* versus *any question at all*, the same one
`google_analytics` and `google_ads` draw, and it exists because this surface is reached over MCP
by an agent holding an API key, where a key carries permission scopes.

**Neither is ever `client`** (#266). A Search Console grant is the *agency's* Google account and
is narrowed by no company horizon: the same connection reaches every client's property, and a
query-level table of what people searched for is not something a portal login should be able to
pull for a site that is not theirs. A client sees Search Console through the marketing dashboard,
which is horizon-scoped, and nothing else.

## 5. Two hosts, one credential, one vocabulary

Search Console answers on **two hosts**. `www.googleapis.com/webmasters/v3` (the product's old
name) carries sites, sitemaps and the whole Search Analytics query surface; the URL Inspection
API lives on `searchconsole.googleapis.com/v1` and nowhere else. Both are the one *Google Search
Console API* to enable in the Cloud project, both ride `webmasters.readonly`, and both are named
once in `client.py`.

Every enum the module accepts — search types, dimensions, filter operators, aggregation types,
data states — was read from Google's **discovery document**
(`searchconsole.googleapis.com/$discovery/rest?version=v1`, revision `API_REVISION_CHECKED`)
rather than remembered (CLAUDE.md §11). An unknown value is refused **here**, with the list of
values that would have worked in `details`, before the round trip is spent: Google's own 400
names neither the bad value nor the good ones.

Request paths make the Google call inside `ctx.release_db()`; the overview, the search-type
split and the movers issue their queries concurrently, since the quota (1 200 a minute per
site) is generous and the cost that matters on a screen is the wall clock. The one network seam
is `client.set_transport`, at the **transport**, so a test travels the real OAuth client, path
builder and error classifier.

## 6. AI visibility: a state, not a number

Search Console gained a **Generative AI performance report** in June 2026 — impressions in AI
Overviews and AI Mode, by page, country, device and date — and rolled it out to every property by
the end of August. As of the discovery document revision `20260902` the Search Analytics API still
accepts exactly six search types (`web`, `image`, `video`, `news`, `discover`, `googleNews`) and
no generative-AI value, and the bulk export does not carry the report either. The numbers exist;
the API does not return them.

Three consequences, all deliberate:

- **`GET /ai-visibility` answers `available: false`** with `reason` (an i18n key), the report's
  own URL (`performance/search-analytics/ai?resource_id=…`) and the discovery revision it was
  checked against. It refuses only when the credential is missing — the link lands in *this*
  account — and it never estimates the figure from the web totals, where AI Overviews are folded
  in with no way to separate them. A tool that answered a plausible number here would be the
  worst kind of wrong, because nothing on any screen could contradict it. The assistant tool's
  description says the same thing in words a model reads.
- **The marketing dashboard's Search Console section carries a card, not a tile.**
  `SourceMetrics.ai_visibility` (`{available, report_url}`) is built by the marketing adapter
  from the same function, drawn as a dashed card with the sentence and the link, and withheld
  from a portal login for the reason `deep_link` is (#447: the link lands in the agency's
  account). A tile with a number would be a metric; this is a state with a cure.
- **`client.GENERATIVE_AI_SEARCH_TYPES` is the seam**, empty on purpose and pinned by
  `test_the_generative_ai_report_is_not_in_the_api_yet_and_the_seam_says_so`. The day Google
  publishes a search type for the report, it is added there: `ai_visibility` starts answering
  the overview shape per generative feature under `sources`, the dashboard card's `available`
  flips, and nothing else in the package, on the screen or in the tool catalog changes shape.
  The test failing is the reminder that the card and the reason then want revisiting.

Rank Math AI Visibility, already on the dashboard, is a **different measurement** (what AI
assistants answer when asked about the brand, read through the client's WordPress), which is why
the two are never presented as one number (#312) and why the tool description names the
difference.

## 7. What Search Console gets wrong if you write the parser from memory

- **A row's group-by values are a positional list.** `keys` follows the order of the request's
  `dimensions`, and a dimension-less query answers one row with no `keys` at all. Every row is
  reshaped to `{dimensions: {…}, metrics: {clicks, impressions, ctr, position}}` on the way out.
- **Google reports no row total.** Every paged read asks for one row more than it keeps and sets
  `truncated` when it arrives (§17's rule). A prefix presented as a whole looks like it worked.
- **Google ranks by clicks and offers no other sort.** `breakdown?order=-impressions` is applied
  locally over the first thousand clicks-ranked rows, and the answer carries
  `google_search_console.warning.order_window`: a top-25 by impressions out of the first thousand
  by clicks is not the same list as a top-25 by impressions.
- **The last two or three days do not exist under Google's default `dataState`.** The curated
  reads ask for `all`, and `fresh_from` names the first day still being collected, because a
  number that will move tomorrow should not be read as one that will not.
- **`hour` answers nothing under any state but `hourly_all`**, keeps ten days, and is never
  final. The hourly read is the one whose window ends *today*; a `query` naming `hour` has its
  data state forced rather than 400ed.
- **Dates are Pacific-time days**, not the org's. They are passed as the org resolved them and
  the fact is documented beside the numbers rather than corrected: the platform has one answer
  to "what is last month" (§8) and this must not become a second one.
- **A sitemap's `errors` and `warnings` are strings**, as is every count in this API.
- **`country` is ISO 3166-1 alpha-3, lower case** (`nld`), and `device` is upper case
  (`DESKTOP`, `MOBILE`, `TABLET`).
- **The filter grammar is Google's six operators, one token each** — `==`, `!=`, `=@`, `!@`,
  `=~`, `!~` — on the five filterable dimensions. A clause that parses as none of them, or names
  `date`/`hour`, is refused rather than dropped (the SnelStart `$filter` lesson).
- **"Not yours" and "not allowed" are one status code on Google's side** (403), and so is a
  URL-inspection of a page outside the property (400). They come back as 403 and 422 with
  Google's `reason` in `details` and its prose nowhere (§9).

## 8. Cost

`overview` is three concurrent queries (this period, the compared period, the device split);
`search-types` six; `movers` two. `inspect` is one URL per call, because its quota is its own —
2 000 a day per property — and a sweep would spend a client's whole allowance on a question
nobody asked. Row counts are clamped (1 000 at most, 25 by default).

## 9. There is no web package

It contributes no screen, no panel and no nav item — the surface is the API and the MCP section,
and the one client-facing thing it produces (the AI-visibility card) is drawn by `marketing`
from the adapter, as it should be. It still appears under Instellingen → Integraties, because
that screen reads `module_kinds` from `/meta/modules`. Give it a settings screen the day it needs
one, and register the web half then.

## 10. Verifying against a live property

Everything above was built from Google's discovery document and exercised against a fake
transport (`tests/gsc_fake.py`). To run it against a real property: connect Google on
Instellingen → Google with an OAuth client whose Cloud project has the *Google Search Console
API* enabled, consent with `include_search_console=1` (or `include_marketing=1`), then
`GET /api/v1/google-search-console/sites` should list the properties that account holds. The
checks worth doing once, because a document can be wrong about a live answer: `hourly` returns
`hour` keys with an offset (`…T09:00:00-07:00`), `inspect` on a page outside the property is a
422 naming Google's reason, and `breakdown?dimension=searchAppearance` lists Google's own
appearance names.
