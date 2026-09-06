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

## 6a. The numbers come in by hand, and that is stated on every surface they reach

The report has an **export button**, and that is the one honest way its figures get onto a
client's dashboard and into their monthly report while the API stays silent (re-checked against
discovery revision `20260905`: still six search types). So a Search Console link takes the
file — `POST /marketing/links/{id}/ai-visibility/import` (multipart: the CSV of the Dates
table, or the whole zip the button produces) and its JSON twin `…/ai-visibility/rows`
(`[{day, impressions}]`, the shape an agent can send) — and writes the figure as
`ai_impressions` **beside** the four synced metrics on the same `marketing_metrics_daily`
rows (`marketing/aiv_import.py`, `MarketingService.import_ai_visibility`). One table, so the
tile, the trend, the compare, the overview grid and the report section read it the way they
read clicks. Five rules hold it up.

- **Absent is not zero.** `IMPORTED_METRICS` names the hand-imported keys, and
  `aggregate` leaves one *out* of a period no row carries it in rather than summing it to `0`.
  That is what keeps the tile off the dashboard and the section off the report for a client
  whose agency never uploaded the export: a "Vertoningen in AI 0" is a claim about their AI
  visibility, and §6's whole argument is that a plausible figure nothing can contradict is the
  worst kind of wrong.
- **A sync never erases an upload.** The nightly re-pull of Search Console's trailing window
  replaces a day's metrics wholesale; `_upsert_daily` keeps every `IMPORTED_METRICS` key the
  row already had, or last week's upload would last one night.
- **The file is read defensively, in the direction of refusing.** Written from what the report
  documents rather than from a file (the OXXA rule: no property with the report was to hand),
  so a zip is searched for the member with a date column, a lone CSV must carry one, a
  weekly or monthly export is refused with a sentence saying to export by day (one row stored
  as one day would print the month a seventh of its size), Google's `~` and `-` are zeros,
  headers match in English and Dutch, and every cap is checked before the work it bounds.
  §10's checklist has the run to do the day a real export arrives.
- **Provenance prints beside the number.** The last upload's timestamp and span sit on the
  link (`config.ai_import`) and ride `SourceMetrics.ai_visibility.imported` onto the card,
  because a figure a person has to remember to upload must say how far it runs — the tile
  alone reads as live. Staff only, like the card: a portal login gets the tile and the
  metric's own help sentence, which says where the number comes from.
- **The report gets its own section, not a fifth tile on Search Console's.**
  `marketing.ai_overviews` (position 75, beside SE Ranking's AI-search section, which is a
  different measurement) carries the month's total against the comparison month and a
  by-week chart of both — labelled `1-7`, `8-14` … rather than by ISO week, so last year's
  weeks line up bar for bar. The Search Console section prints the synced four and nothing
  else; a tile printed twice under two headings is one fact read as two.

Uploading rides `marketing.link.manage` (putting numbers under a client's name is
configuration, not a read), records `marketing.ai_imported` on the client's trail, and is
refused on any source but Search Console. The multipart route is excluded from the MCP tool
surface by method, as every multipart route is (CLAUDE.md §10); the JSON twin is the tool.

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
and the one client-facing thing it produces (the AI-visibility card, with its upload control
since §6a) is drawn by `marketing` from the adapter, as it should be. It still appears under Instellingen → Integraties, because
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

And for §6a, the day a real export of the Generative AI report is to hand: open it and check
that the zip's dates table is named as `aiv_import` expects (a `.csv` member whose header starts
with `Date`/`Datum`), that a daily export's dates are ISO (`2026-08-01`) and not the account's
locale, and what a weekly export's header says (`Week` is the guess). Upload it on a test
client, compare the response's `total` with the console's own total for the same span, and
adjust the header sets in `aiv_import.py` if anything differs — the parser refuses rather than
guesses, so a mismatch shows as a 422 naming the columns, never as zeros.
