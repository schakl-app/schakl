# Marketing — the leads dashboard and the measurement profile

> What an agency used to build by hand in Looker Studio, one report per client, as one module
> that reads every client through that client's own **measurement profile** (*meetprofiel*).
> The brief behind it (Luka, September 2026) named the first validation client, Jachttrans, and
> its acceptance figures; those are a test fixture here, never a default.

## 1. The shape: a semantic layer and a fixed report, not a canvas

Five Looker pages for one client turned out to be the same fourteen questions asked in that
client's vocabulary. What differed per client was never the dashboard — it was *which event is
a request*, *which parameter carries the service*, and *what `internationaal-transport` is
called on screen*. So the vocabulary is data and the questions are code:

- **The profile** (`app/modules/marketing/leads/profile.py`, stored on
  `marketing_company_settings.lead_profile`) maps seven **functional roles** — request, form
  started, form submitted, form error, phone click, e-mail click, application — to the GA4
  events that carry them for this client (several matchers per role: exact, begins-with,
  contains, regex); names the **dimensions** the dashboard may group by (a GA4 field such as
  `customEvent:dienst`, a label per locale, a label per value, whether it is a filter); says
  which form types count as a **quote**; maps Google Ads **conversion actions** to services;
  lists the **measurement breakpoints**; can hide widgets; can override the channel grouping;
  and carries the tenant's own sentence for the fixed note.
- **The widget catalog** (`widgets.py`, `ads.py`) is the fixed list of questions, each declaring
  what it needs (roles, dimensions, an Ads account) and answered in one of eight shapes —
  scorecard, bars, line, donut, pivot, table, funnel, combo. A widget whose needs the profile
  does not meet is **withheld and named** (`unavailable`, with the reason), never drawn as a
  zero: "no funnel" and "100 % dropout" are different sentences.
- **The plan** (`ga4.py`) derives at most nine GA4 reports from the profile and the reader's
  filters, sent as two `batchRunReports` calls; **the Ads half** is three GAQL queries. Every
  widget names the report it read, so every number is traceable to one request — and GA4
  events are never joined to Ads costs on a date (the Looker lesson: a join on the one shared
  column multiplies rows the moment a second dimension appears).

Deliberately **not** a free dashboard builder: the brief said so, and a canvas would have made
every client's report a second thing to keep in step. What is Looker-like is the semantic
layer (roles and dimensions), the cross-filtering (a click on a service narrows the page), the
free date range, the sortable tables and the CSV export.

## 2. One read path

`GET /marketing/companies/{id}/leads?period=…&f=dimension:value` (`service.py`):

1. Resolve the profile; `configured: false` for a client without one — a state the screen
   teaches from, not an error.
2. Refuse a filter the profile cannot express (422 naming the dimensions it can) — a filter
   silently dropped answers a different question with every row still valid. **A page filter is
   a field a request event carries** (`LeadProfile.filter_dimensions`): it is AND-ed onto every
   report, so `error_reason` — which only an error event has — is a dimension the error widgets
   group by and never a filter. Drawn as one, a click on "validatiefout" answered zero requests
   on every tile, emptied every other filter's options and raised a silent-zero warning about a
   week that had requests in it. The widget says which it is (`LeadWidget.filterable`); the
   screen draws a row as a link on that and on nothing else.
3. Resolve the period on the org's calendar. **A period may now be a free span**:
   `2026-08-29..2026-09-03` is a token beside `30d`, `last_month` and `2026-Q3`
   (`app/core/periods.py`), clamped the same three ways, so a breakpoint can be the floor of a
   URL, an MCP call and a report alike without a second `date_from`/`date_to` parameter pair.
4. Fetch the GA4 batch and the Ads queries, or take them from Redis. **The cache holds Google's
   raw answers, keyed on the exact requests sent**: a relabel in the profile shows at once
   (labels are applied on the way out) and a changed role re-fetches because the request
   changed. One hour, which is enough for a report GA4 itself lags behind. Every Google
   round-trip runs inside `ctx.release_db()`.
5. Compute the widgets, the coverage, the warnings and the filter controls. **What a filter
   offers is what the period saw, not what the narrowed reports still contain**: under an active
   filter the options are read off the *unfiltered* plan (`widgets.period_values`) — the view
   the reader clicked from, so a Redis hit in every ordinary case, the same two batches on a
   miss, and the narrowed options on a failure. Taken from the narrowed reports they collapsed
   to the value just picked, so a second service could not be added and another could not be
   switched to without clearing first. The silent-zero check is skipped under a filter for the
   same reason the options are not: it is a statement about the measurement, not the view.

A client-facing login (`ctx.is_portal`) gets the widgets, the coverage and the fixed note — and
never the deep links, the unavailable list or the diagnostics about the agency's own setup.

## 3. Honesty is part of the payload

- **Warnings**: sampled, thresholded and `(other)`-row answers from GA4's own metadata; a
  period that starts before the hard breakpoint (with a one-click "from the breakpoint" link);
  a **silent zero** (traffic and no request over the last seven days of the span, or the whole
  span when shorter); a profile dimension the property has not registered as a custom
  dimension (the values arrive as `(not set)`); key events in GA4 that do not match the
  profile's request event; a report or a query that failed, costing its own widgets only.
- **Coverage**: the `(not set)` share of requests per dimension, always visible.
- **The fixed note**: every breakpoint with its text, "requests, not orders", the tenant's own
  sentence, and when the numbers were read from Google.
- **Every ratio the API did not send is `null`, never `0`**: cost per conversion with no
  conversions, impression share on a campaign Google reports none for.

## 4. Channel grouping

GA4's `sessionDefaultChannelGroup` is regrouped into *Organisch / Advertenties / AI / Overig*
for the client (`DEFAULT_CHANNEL_GROUPS`): the agency's house grouping lives on
`marketing_settings.channel_groups` (Instellingen → Marketing), a client's profile may override
it whole. **Cross-network is advertising** — it is where Performance Max reports, and a
grouping that files only Paid Search under ads misses most of the paid traffic. The donut keeps
the raw channels (six slices, the rest folded into "other") with each slice's group beside it;
the grouped view is its own bar widget.

## 5. The screen

`MarketingLeadsSection` sits above the per-source sections on the client's Marketing tab, on
`/marketing`, and on the client's portal homepage tile. **Each part is a section drawn the way
the per-source sections are** — a white card on the page's ground, figures as tinted `stat`
tiles, lists and charts as outlined boxes inside it (docs/UX.md, the visual system) — because
the first version drew hairline boxes with no fill directly on the page: tiles the colour of
the background. The two-column grid is **arranged, not declared**: which widgets exist depends
on the profile, so a half-width widget with no half beside it takes the row rather than leaving
a block-sized hole. Filters sit *inside* the Leads section (they narrow what GA4 measured; the
advertising figures are never joined to them): one labelled row per dimension, a chip per value
the period saw, the picked ones filled and carrying their own ✕. A click on a bar or a table row
of a `filterable` widget toggles that value — all through the URL
(`?f=service:autotransport`), so a narrowed dashboard is a link and the back button undoes a
click. **What is picked is read from the URL, not from the payload**, and the section says
"Bijwerken…" and dims while the narrowed read runs: the payload describes the previous view for
the seconds Google takes, and a chip that lights up after the answer lands reads as a press
that missed. Tables sort and page locally (the API caps rows and says so) and export CSV; the funnel
draws dropout in amber from 35 % and red from 60 %, the thresholds the Looker build used, and
nothing else on the page is red. The period picker gained a free span beside the months and
quarters. The **profile editor** (`/companies/{id}/marketing/profile`, `marketing.link.manage`)
picks event names, custom dimensions, key events and conversion actions from a live catalog of
the client's own property and account (`GET …/leads/catalog`), so nothing is typed from memory.

`MarketingAiSearchSection` sits **below** the per-source sections on both hosts: SE Ranking's AI
Search overview for the client — brand mentions, links, average position and opportunity traffic
in AI answers, last month against the month before (`docs/SERANKING.md`). It is about the
*client*, not about a link, so it is drawn whether or not any source is linked and the website
filter does not apply to it; like the leads it streams, because the first view of a month is a
read from SE Ranking. Off until the agency switches it on, since every read spends its units.

## 6. Reuse: the report section

`marketing.leads` is a `ReportSectionSpec` on the registry (`report_sections.py`), reading the
same service over the report's month: the four scorecards as totals, requests and the funnel
per service as rows. The document and the dashboard therefore cannot disagree, and a client
without a profile gets no section rather than an empty one.

## 7. What the validation client checked

`tests/test_marketing_leads.py` reproduces the Jachttrans acceptance period (29 August – 3
September 2026, spec §9) from GA4-shaped rows: 35 requests, 21 quotes, 81 phone/e-mail
contacts, 21 failures, the channel split (Organic Search 19, Cross-network 8 → *ads*), the
per-service list with the tenant's label, the funnel including `werken-bij` at 1 started → 0
submitted → 100 %, and a division by zero that never happens. The Ads figures (€ 179,03, 11
conversions, € 2.510 value, the impression-share table with a `null` where Google sent
nothing) come from the same test through the fake Ads transport. The live Ads queries were
run against the real account through the breik CRM connector before the module was written;
the GA4 report shapes (`customEvent:dienst`, `eventName` filters) likewise. The same test
runs the APEX profile — different event names, no service dimension, no Ads account — and
asserts that a second client is configuration only.

Not verified against a live GA4 credential from this code path: the `batchRunReports` call
travels the real OAuth client and path builder in the tests through a transport fake, and the
FilterExpression shapes (`andGroup`, `orGroup`, `inListFilter`, `FULL_REGEXP`) are written
from the Data API v1beta reference. The day a live property answers, check the three things
the reference cannot: that `inListFilter` with `caseSensitive: false` is accepted on
`eventName`, that a `customEvent:` dimension unregistered in GA4 answers `(not set)` rather
than a 400, and that two batches of five keep their order.

## 8. Settings that host the seams

`SCHAKL_GOOGLE_ANALYTICS_DATA_BASE_URL`, `SCHAKL_GOOGLE_ANALYTICS_ADMIN_BASE_URL` and
`SCHAKL_GOOGLE_ADS_API_HOST` name the Google hosts the leads dashboard (and the Ads client)
call, so a test stack can point them at a fake without a code change — the Microsoft rule
(`docs/MICROSOFT.md`), applied here. The older marketing source adapters keep their constants
until they are moved over the same way.
