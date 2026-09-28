# Bing Webmaster Tools

> **Status: design, nothing built.** Written 2026-09-27 from Microsoft's published API reference
> and the AI Performance announcement, never from a live key (CLAUDE.md §11: written from a
> document, which is allowed, and every parse below is a hypothesis until a real body has been
> fed to it). §11 is the checklist for the day a key is to hand; §12 is what still needs a
> decision before phase 1 starts.

## 1. What is being asked for, and the fact that shapes all of it

Bing Webmaster Tools answers two questions an agency's client asks: *how do we do in Bing* and —
the reason this is on the table — *are we cited when Copilot answers*. The second one is the
**AI Performance** report (public preview since February 2026): citations in Microsoft Copilot,
Bing's AI summaries and partner surfaces, by day, by page and by *grounding query* (the phrase
the model searched with, not the question the person typed).

**The AI Performance report has no API.** The Webmaster API's method list (`IWebmasterApi`, 60
methods) carries nothing about AI, Copilot, citations or grounding; Microsoft said in February
that API access is on the backlog for 2026, and nothing found on 2026-09-27 says it shipped. The
report has an export button. That is exactly where Search Console's Generative AI report stood
(`docs/GOOGLE_SEARCH_CONSOLE.md` §6/§6a), so the design is the one already proven there:

- **a state, not a number**, wherever nothing was uploaded;
- **the export comes in by hand** (file for a person, JSON twin for an agent), stored beside the
  synced metrics, provenance printed beside it;
- **a seam that is empty on purpose and pinned by a test**, so the day Microsoft publishes the
  method the import becomes a sync and nothing else changes shape.

Everything else Bing offers — traffic, queries, pages, crawl, index, sitemaps, URL submission —
*is* in the API and is ordinary integration work.

## 2. What Bing gives us

| Area | Methods | Notes |
|---|---|---|
| Sites | `GetUserSites`, `GetSiteRoles` | One key sees every site its user verified or was delegated |
| Traffic | `GetRankAndTrafficStats` | Daily `Clicks`, `Impressions`. **Takes `siteUrl` only — no date range** |
| Queries | `GetQueryStats`, `GetQueryPageStats`, `GetQueryTrafficStats` | `AvgImpressionPosition`, `AvgClickPosition`, clicks, impressions per query |
| Pages | `GetPageStats`, `GetPageQueryStats` | Same fields per page |
| Crawl | `GetCrawlStats`, `GetCrawlIssues` | Crawled pages, errors, pages in index, inbound links — **levels** |
| Index | `GetUrlInfo`, `GetUrlTrafficInfo`, `GetChildrenUrlInfo` | Bing's URL inspection |
| Sitemaps | `GetFeeds`, `GetFeedDetails`, `SubmitFeed` | |
| Links | `GetLinkCounts`, `GetUrlLinks` | Inbound links — Search Console's API has no equivalent |
| Keyword research | `GetKeyword`, `GetKeywordStats`, `GetRelatedKeywords` | Bing search volume |
| Submission | `SubmitUrl`, `SubmitUrlBatch`, `GetUrlSubmissionQuota` | The one write worth having |
| **AI Performance** | **none** | Dashboard + CSV export only |

Authentication is an **API key** (`?apikey=`, one per Bing user, covering all their sites) or
Bing's own OAuth 2.0. The JSON endpoint is `https://ssl.bing.com/webmaster/api.svc/json/<Method>`
and answers under a `d` node; dates arrive as `/Date(1399014000000-0700)/`.

Three consequences that are design, not detail:

- **No date parameter means no backfill.** Search Console hands over sixteen months on the first
  night; Bing hands over whatever window it keeps (about six months — to be measured). A
  year-over-year comparison (#312) therefore does not exist for a Bing link until this instance
  has *itself* stored a year. Every day not syncing is a day of history that can never be
  fetched later — the one argument for shipping the sync (phase 2) before the polish.
- **The key travels in the query string**, so it must never reach a log line, an error envelope
  or a `details` payload. The client redacts the URL before anything is raised.
- **Query and page stats are not daily rows** (weekly buckets is the expectation, §11). They are
  drill-downs read live, never folded into `marketing_metrics_daily`.

## 3. The shape: an integration, a core seam, and a marketing source

By §6a's test this is an **integration** — with Microsoft gone it is gone, not poorer.
SE Ranking's shape (adapter and key inside `marketing`) is the wrong precedent: it is why
`keyed_client` sends *every* org-key source to the SE Ranking key today. The right precedents are
`wordpress` (credential rows, reached by `marketing` through `app/core/wordpress.py`) and
`google_search_console` (a live read surface with its own MCP section).

```
apps/api/app/integrations/bing_webmaster/   models, client, service, router, permissions, mcp
apps/api/app/core/bingwebmaster.py          the seam: credential resolver + client factory
apps/api/app/modules/marketing/sources/bing.py   the adapter, source = "bing"
apps/web/src/lib/integrations/bing_webmaster/    settings screen, website panel
```

- `kind=KIND_INTEGRATION`, `sku="bing_webmaster"`, **`requires=()`**. It needs neither
  `marketing` (an agency may want only the agent tools) nor `websites` (the panel is an
  enrichment) nor `microsoft` — Bing's OAuth is its own authorization server, not Entra/Graph,
  and the 365 connection carries no Webmaster scope.
- `MarketingLink.source` is `String(16)` with no DB enum: `"bing"` needs no migration.

### The credential is a row

`bing_webmaster_accounts(org_id, label, api_key_encrypted, last_verified_at, last_error,
sites_seen, …)`. Cloudflare's rule (§10), for Cloudflare's reason: the agency holds one Bing
account with delegated access to most clients' sites, *and* some clients verified their own site
under their own Microsoft account and hand over their key. A single per-org setting cannot say
that, and the same `siteUrl` may legitimately be visible through two keys — so nothing picks an
account for you: a marketing link stores `config.account_id` beside `external_id = siteUrl`.

API key first. OAuth is the better long-term door (revocable per app, no secret in a URL) and is
deliberately phase 5: it needs an app registration in Bing Webmaster Tools per instance, a
callback route and a refresh loop, and buys nothing the key does not while we are read-mostly.

### Health

Whatever sets a flag says what clears it (§10): a successful read clears `last_error`. A 401 is
"this key was deleted or regenerated" and names the account; a site missing from
`GetUserSites` is "this account no longer has access to the site" and names the *link* — two
sentences, two people who can fix them (the SnelStart rule). `_health` in marketing currently
knows only Google as something that can be disconnected; it asks the adapter instead.

## 4. The integration's own surface

Routes under `/api/v1/bing-webmaster`, which is also `/mcp/bing-webmaster` and joins the
`growth` bundle. The site is a **query parameter** (`?site=`), for the reason Search Console's
is; the account is resolved from the site unless `?account=` names one.

| Group | Routes |
|---|---|
| What exists | `accounts` (CRUD + `verify`), `sites`, `sitemaps`, `sitemap` |
| What happened | `overview`, `timeseries`, `queries`, `pages`, `query-pages`, `movers` |
| What the index holds | `inspect` (`GetUrlInfo`), `crawl`, `crawl-issues`, `links` |
| Keyword research | `keywords` |
| AI Performance | `ai-performance` (§5) |
| The one write | `POST submit` (+ `GET submit/quota`) |

Permissions:

| Key | Default | Reaches |
|---|---|---|
| `bing_webmaster.site.read` | admin, member | every GET above |
| `bing_webmaster.account.manage` | admin | the credential rows |
| `bing_webmaster.url.submit` | admin | asking Bing to crawl a URL |

Never `client`: a key is narrowed by no company horizon. A client sees Bing through the
marketing dashboard and their report, which are. `submit` is its own key because it spends a
daily quota that belongs to the client's site, and an agent's key should be mintable without it.
Everything but `submit` and the account rows is a GET, so an expired licence keeps reading.

No `query` passthrough: Bing's API has no query language to pass through.

## 5. AI Performance: state, import, seam

### What the report measures

| Figure | Meaning | Kind |
|---|---|---|
| Total citations | Times a page of the site was shown as a source in an AI answer | daily count → **sum** |
| Average cited pages | Unique pages cited per day | daily **level** → averaged, never summed |
| Page-level citations | Citations per URL over the chosen span | period table |
| Grounding queries | Phrases the model retrieved with, and their citations | period table, **sampled** |

No clicks, no position, no country, no device, no split per surface.

### Storage

- The **Dates** export writes `ai_citations` and `ai_cited_pages` onto the link's
  `marketing_metrics_daily` rows — one table, so the tile, the trend, the compare, the overview
  and the report read them the way they read clicks. Both join `IMPORTED_METRICS` (absent is
  never zero; a sync never erases an upload). `ai_cited_pages` also joins `AVERAGED_METRICS`:
  thirty-one daily levels added together is the #381 fault.
- The **Pages** and **Grounding queries** exports are not daily and cannot go there.
  `bing_ai_performance_tables(org_id, link_id, kind, period_start, period_end, rows JSONB,
  sampled, imported_at, imported_by)`, unique on `(org, link, kind, period_start, period_end)`
  so a re-upload replaces. A table is only ever printed for **the span it was exported for**; it
  is never re-cut to a different month, because a top-25 of a quarter is not a top-25 of July.

### Routes

The existing pair is generalised rather than copied:
`POST /marketing/links/{id}/ai-visibility/import` and `…/rows` stop refusing every source but
Search Console and dispatch on the adapter (`adapter.ai_import`: which files it accepts, which
metric keys it writes). `aiv_import.py` becomes two parsers behind one defensive reader — the
caps, the zip handling and the refuse-rather-than-guess posture are shared, the header sets are
per vendor.

### The seam

`client.AI_PERFORMANCE_METHODS: tuple[str, ...] = ()`, pinned by a test that fails the day it is
populated. `GET /bing-webmaster/ai-performance` answers `available: false`, the report's URL and
the date the method list was last checked (`API_CHECKED`) — and, where an upload exists, what
was uploaded and how far it runs. When Microsoft ships the method: the adapter's `fetch_daily`
starts writing the same two keys, they leave `IMPORTED_METRICS`, and the import route stays as
the way to load the months before the API's own window.

### Four AI measurements, never one number

After this the platform holds four, and they measure different things:

| Source | Measures | Unit |
|---|---|---|
| Search Console (import) | shown in AI Overviews / AI Mode | impressions |
| **Bing (import)** | cited as a source in Copilot / Bing AI | citations |
| SE Ranking AI Search | brand and link presence across five engines | presence, position |
| Rank Math AI Visibility | what assistants answer about the brand | mentions, citations |

They are laid out side by side, each under its own name and source (#312); they are never
summed, averaged or turned into a score. A Google "impression" and a Bing "citation" are not the
same event, and an "AI visibility 1.480" tile would be the plausible number nothing can
contradict. Note the collision: Rank Math already has a metric called `citations`, which is why
Bing's key is `ai_citations` and its label says *Citaties in Copilot*.

## 6. Marketing

### The source

| | |
|---|---|
| `MarketingSource.BING` | `"bing"` |
| Synced metrics | `clicks`, `impressions`, `ctr` (derived), `crawled_pages`, `crawl_errors`, `indexed_pages` |
| Imported metrics | `ai_citations`, `ai_cited_pages` |
| Averaged | `ctr` (weighted by impressions), the three crawl levels, `ai_cited_pages` |
| Lower is better | `crawl_errors` |
| Drill-downs | `queries`, `pages`, `movers`, `ai_pages`, `ai_queries` |
| Auth | `AUTH_ACCOUNT_KEY` — new |

`position` is **not** a synced daily metric unless §11 shows a daily position exists:
`GetRankAndTrafficStats` documents clicks and impressions only, and a position averaged out of
the top-query table would be a number that is neither Bing's nor ours. It lives in the
drill-downs, per query, where Bing states it.

### What changes in marketing itself

- **`keyed_client` dispatches on the adapter**, not on "anything that is not Google or a site
  key is SE Ranking". The adapter names its resolver; Bing's goes through
  `app/core/bingwebmaster.py`. This is the fourth auth kind, and #300's prediction ("a new source
  is one line") missed authentication for the fourth time — worth fixing as a dispatch table
  rather than as one more branch.
- **`layout.DRILLDOWNS_BY_SOURCE` is derived from the adapters.** It is a hand-kept copy that
  already lacks `rankmath`, so a curated layout naming a Rank Math drill-down is 422'd today.
  Found while reading for this design; fixed in phase 2 regardless.
- **The sync stores what arrives.** `fetch_daily(start, end)` reads the whole window Bing
  returns and keeps the days inside the asked range; `marketing_backfill_link` gets what exists
  and says how far back that was (§17: a short answer names its size). Trailing re-pull at 7
  days until §11 measures how late Bing's numbers settle.
- **Dates are Pacific days** (the `-0700` in the payload). Passed through and documented, as
  Search Console's are — never "corrected" into a second answer to "what is last month" (§8).
- **Section order.** `_links` orders by source string, so `bing` would sort above GA4. Order by
  the enum, as `linked_clients` already does.
- **The overview grid** has three hardcoded columns (GA4, Search Console, Ads). Bing gets none
  by default: for a Dutch client Bing is a few percent of search, and a fourth column of small
  numbers on every row is noise. Its clicks are reachable per client.

### On the screen

- **Picker**: a fifth source row. No account configured → the sentence and a link to the
  integration's settings screen (the credential's absence decides a sentence, never whether the
  control is drawn, #399). One account → sites listed straight away; several → the site list
  says which account each came through.
- **Dashboard section** "Bing": clicks, impressions, CTR, the crawl/index strip, drill-downs.
- **AI card/tiles** in that section: nothing uploaded → the dashed card with the sentence, the
  report link and the upload control (staff only, #447); uploaded → two tiles, the two
  drill-down tables with their span and the word *steekproef* on grounding queries, and the
  provenance line.
- **An "AI-zichtbaarheid" group** on the dashboard that gathers the AI tiles of every linked
  source in one place, each still labelled by source. Today they are scattered over three
  sections; with a fourth it becomes worth a heading. Layout only — no new number.
- **Tenant source names** (#446): `bing` joins `PORTAL_LABEL_SOURCES`. It is *not* portal-neutral
  — a client knows what Bing is, as they know what Google is.
- **Web types**: the `MarketingSource` union and its five `Record<>` lists; `format.ts`'s
  duplicate `PORTAL_NEUTRAL_SOURCES` should read the API's instead of restating it.

## 7. Reporting

| Section | Position | Content | Default |
|---|---|---|---|
| `marketing.search_engines` (existing) | 20 | gains a **Bing row** beside Google where a Bing link exists: clicks and impressions per engine, from the engines themselves rather than from GA4's referrer guess | on |
| `marketing.bing` | 45 | tiles + top queries + top pages, month against the comparison month | **off** |
| `marketing.ai_citations` | 76 | citations this month against last, by-week chart, top cited pages, top grounding queries | on where an upload covers the month |

- `marketing.bing` is off by default for the overview grid's reason; an agency switches it on
  per template or per client (`report_profiles.sections`, #373).
- `marketing.ai_citations` compares with the **previous month**, not last year, and states its
  own span (`compare_period`): the report did not exist before February 2026, so a year-over-year
  badge would be a comparison with nothing — and it is a level-like question anyway (is the work
  moving it), which is the rankings rule.
- A month no upload covers prints **no section**, and the review screen's warnings strip says
  *"Bing AI Performance: geen export voor augustus"* — the agency reads that, the client never
  does. A half-covered month is refused the same way: citations for 1–14 August under a heading
  that says August is a number half its size.
- The pages/queries tables print only where a stored table's span **is** the report's month.
- Picker copy states what feeds each section ("Bing Webmaster Tools — handmatige export").
- `present.py` needs nothing but the `marketing.metric.<key>` labels; the model is told that
  grounding queries are a sample and are the model's own search phrases, not customers' words,
  so it does not write "klanten zochten op…".
- `rankings.effective_source` is **not** widened. Keyword positions stay SE Ranking / Search
  Console: a third source there is a third way for two months to be incomparable.

## 8. Everywhere else

| Surface | What Bing brings |
|---|---|
| **Instellingen → Integraties** | its row, description, link to its one settings screen |
| **Settings screen** | account rows: label, key (write-only), *Controleer* (GET — answers on an expired licence), sites seen, last error |
| **Website page** | an `EntityPanelSpec` on `website`: indexed or not, last crawl, crawl issues, sitemap state, and *Indexering aanvragen* with the quota left **on the button** (#305) |
| **Client hub** | nothing new — Bing rides the marketing panel (no sixth integration card, #411) |
| **MCP** | `/mcp/bing-webmaster`, the `growth` bundle, curated assistant tools (`sites`, `overview`, `queries`, `pages`, `inspect_url`, `ai_performance`, `keywords`); `marketing.*` tools are source-generic and pick it up |
| **Assistant** | `api.find`/`api.get` reach the GETs by derivation; `submit` stays off `ASSISTANT_WRITES` |
| **Client portal** | the marketing section and tiles, horizon-scoped; no report link, no upload, no provenance line |
| **Notifications** (phase 4) | `bing.crawl_errors_spiked`, `bing.site_lost` (account no longer sees a linked site), `marketing.ai_export_due` — a reminder on the 2nd of the month to whoever manages the link, because a figure a person must remember to upload is a figure that silently stops |
| **Activity trail** | link created/removed, `marketing.ai_imported` (exists), account added/removed, URL submitted |
| **WordPress / deploy hooks** (phase 5) | submit a URL when a page is published through the bridge |
| **Licence** | `LICENSE-COMMERCIAL.md` Integrations list, `LICENSE` markers in both packages, `test_entitlements`' sku dict, `config.enabled_modules` |
| **Site docs** | a row in `marketing-sources.mdx`, `apps/site/src/data/integrations/bing.json` |

Not proposed: domain verification through Cloudflare DNS (`AddSite`/`VerifySite`), block/remove
URL tools, crawl-setting writes. Each is a way to take a client's site out of Bing from a chat
window, and nobody asked.

## 9. Performance and cost

- Bing publishes no per-method quota beyond URL submission; §11 measures. Until then the nightly
  sync is **one request per link** (traffic) plus one (crawl), sequential per account, and a
  throttled answer ends that account's run and keeps what was read.
- Live reads run inside `ctx.release_db()`. The dashboard's drill-downs stream behind the shell
  as every source's do; the warm-up job includes Bing's.
- Because query/page stats take no date range, `movers` is computed from the two newest
  buckets Bing returns, and says which two.
- The dashboard read adds no query: Bing's rows ride the existing per-link metrics read. A
  `count_queries` budget test pins it.

## 10. Phases

| Phase | Delivers | Gate |
|---|---|---|
| **1** | Integration package, account rows, settings screen, `sites` + the read routes, MCP section, licence plumbing | key verified against a live account, §11 run |
| **2** | Marketing source: sync, dashboard section, drill-downs, picker; `keyed_client` dispatch; `DRILLDOWNS_BY_SOURCE` derived | a linked site shows a week of synced days |
| **3** | AI Performance: generalised import, tables, card/tiles, AI group, `ai-performance` route + tool, report sections | a real export uploaded and rendered in a report |
| **4** | Website panel, URL submission, notifications | |
| **5** | OAuth, submit-on-publish | |

Phases 1 and 2 are small and phase 2 is the urgent one (§2: history that is not stored is
gone). Phase 3 is where the value the question was about sits.

## 11. The checklist to run against a live key

1. `GetUserSites`: are delegated sites listed, and how is a domain property spelled?
2. `GetRankAndTrafficStats`: how many days back; is there a position field; how many days late
   is the newest row; do the last days move on a re-read?
3. `GetQueryStats` / `GetPageStats`: what does `Date` mean per row (a week? which day of it?);
   how many rows; is there a row cap and does anything say so?
4. A deleted key, a wrong key, a site the key cannot see: status code and body of each.
5. Throttling: what a burst of fifty requests answers.
6. **The AI Performance export**: file type (CSV or zip); one file per tab or one for all;
   header names in an English and a Dutch account; date format; whether a span beyond some
   limit is refused; whether the grounding-query export says it is sampled.
7. Compare an uploaded month's total with the dashboard's own total for the same span.

## 12. Decisions wanted before phase 1

1. **Credential**: account rows (proposed) or a single agency key in Instellingen → Marketing,
   SE Ranking's shape? Rows cost one table and one screen and are the only shape that holds a
   client's own key.
2. **Is `marketing.bing` worth a report section at all**, or is the Bing row in *Zoekmachines*
   plus the AI chapter the whole client-facing story?
3. **The monthly upload**: is a person uploading three exports per client per month acceptable
   until Microsoft ships the API, or should phase 3 wait for it? (An agent with the JSON twin
   can do the upload; it cannot do the export.)
4. **URL submission**: wanted, and if so admin-only?
5. **The AI group** on the dashboard: regroup the existing AI tiles under one heading, or leave
   each under its source?
