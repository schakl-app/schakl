# SE Ranking — the AI Search overview

> How visible a client's brand is inside AI answers — ChatGPT, Perplexity, Gemini, Google's AI
> Overviews and AI Mode — **last month, against the month before**. Read from SE Ranking's
> **Data API**, stored per month, drawn on the client's marketing dashboard and printed as a
> chapter of the monthly report.
>
> SE Ranking's *other* half — rankings, the site audit, the AI Result Tracker, all per
> project — is documented with the report it feeds: `docs/REPORTING.md` § "SE Ranking".

## 1. What is read, and what the four figures mean

One call: `GET /v1/ai-search/overview/…/time-series` for a **target** (a domain, a host or a
URL), a **country database** (`source`, alpha-2) and a **brand**. It answers a `summary` of
four figures, each as `current` / `previous` (the live API leaves `previous` null — §10), and
five monthly `time_series` streams.

SE Ranking tracks a large, fixed set of prompts per country and re-runs it monthly. Every figure
below is therefore **a sample of AI search, not all of it**, and always describes a whole month.

| Figure | What it counts | How to read it |
|---|---|---|
| **Brand presence** — *Merkvermeldingen* | The number of tracked AI answers in which the **brand name is mentioned**, whether or not the answer links to the site. | Awareness: do the engines know the brand. It depends entirely on *which brand name is counted* (§4), which is why the brand is printed under the figures. A count, not a percentage. |
| **Link presence** — *Links naar de site* | The number of tracked AI answers that **cite a page of the target as a source**, with a link. | The half that can bring a visitor. Counted independently of brand presence: an answer can name the brand without linking, or link without naming it, so neither is a subset of the other. |
| **Average position** — *Gemiddelde positie* | Where the target's link stands, on average, **among the sources an answer cites**: 1 is the first source listed. | **Lower is better.** A fall is an improvement — drawn as a down arrow in green, on the screen and in the document alike. It is a position inside an AI answer's source list, *not* a Google ranking; the report keeps the two under different names for that reason (`ai_average_position` vs `avg_position`). |
| **AI opportunity traffic** — *Potentieel AI-verkeer* | An **estimate of the monthly search volume behind** the AI answers the target appears in — the combined volume of those prompts. | The size of the audience those answers reach. **Not visitors.** Actual visits from AI are the `ai_traffic` series below and, first-hand, GA4's "AI Assistant" channel. |

The monthly series: `link_presence` and `average_position` (the same two figures, over time),
and three traffic estimates — `ai_traffic`, `organic_traffic`, `overall_traffic`. Brand presence
and opportunity traffic have **no series**, which matters in §3.

**"All engines together" is one question, not five.** The cross-engine aggregate is SE
Ranking's own figure on its own endpoint. It is *not* the sum of the engines — a prompt answered
by three engines is one prompt — so nothing in this code adds engines together, and a tenant
who picks three engines gets three blocks rather than a total nobody measured.

## 2. Two APIs, two keys, one check

SE Ranking sells two APIs and **issues a token for each**: the *project API*
(`api4.seranking.com` — projects, keyword positions) and the *Data API*
(`api.seranking.com/v1` — AI Search, the subscription). Its own MCP server takes them as two
separate headers. One key often reaches both; often it does not, and then the rankings work
while AI Search answers 403 — which, reported as "SE Ranking refuses the key", sends an admin to
re-issue a key that is working (§10's "two credentials, two error paths").

- `marketing_settings.seranking_api_key_encrypted` — the key every install already has.
- `marketing_settings.seranking_data_api_key_encrypted` — **optional**. `NULL` means *use the
  key above for the Data API too* (the `NULL` = inherit idiom, applied to a credential).
  Removing it is its own tick (`clear_seranking_data_api_key`), because an empty box means
  "keep it" like every other write-only secret on that form.
- `GET /marketing/settings/seranking/check` — asks each API **with the key that would actually
  be used for it** and answers `project_api` / `data_api` as `ok` · `denied` · `failed` ·
  `not_configured`, plus the Data API plan's `units_left` / `units_limit` and what the AI Search
  overview costs per month at the current settings. Free: `/account/subscription` is the one
  Data API call SE Ranking does not charge for, which is what makes it the probe. A GET, so it
  keeps answering on an expired licence — exactly when somebody is checking credentials. The
  settings screen saves a key typed a moment ago *before* it checks: somebody who pastes a key
  and presses "controleer" means that key.

Every probe fails softly (the Cloudflare rule: a probe is evidence, never the gate).

## 3. Which month the figures are

The platform always asks about **the last complete month on the org's calendar**
(`aisearch.service.expected_month`). SE Ranking's answer names no month of its own — `summary`
says `current` and `previous` — so the month is read off the time series beside it
(`aisearch.parse_overview`), and the two ways it can differ from what was asked are handled
rather than assumed away:

| SE Ranking's newest series point | What is stored | What the screen says |
|---|---|---|
| the month asked about (the ordinary case) | `summary` as given; `data_month = period_month` | "augustus 2026 · vergeleken met juli 2026" |
| an **older** month (not published yet) | `summary` as given; `data_month` = that older month | the figures, **labelled with the month they are**, and "SE Ranking heeft augustus nog niet gepubliceerd". Re-asked weekly — it costs a full read to find out. The **report** withholds the chapter and says why on the run's warnings: July's figures under an August cover would be the one number on the document from another month. |
| the month **still running** | link presence and position are **re-read from the series** for the month asked about and the one before it; brand presence and opportunity traffic, which have no series, keep what SE Ranking called `previous` as their value — which the live API leaves null, so in practice they are **blank** for that month | the figures for the month asked about, a note saying two of them are missing, `realigned: true` on the payload |

SE Ranking's own `change_absolute` / `change_percent` are **not read**: their sign convention
differs per metric (a position that improved from 9,2 to 8,5 arrives as `+0.7`), and after a
realignment they describe two other months. The change is computed from the two values kept
(`aisearch.change`), as a direction *and* a verdict. And where there is no `previous`, there is
**no change** — SE Ranking's API reports a first snapshot as `+100 %`, which is a baseline
dressed as growth (their own skills say so).

## 4. The target and the brand

Both are facts about one client, so both are per-client settings with sensible derivations:

- **Target**: the client's own setting → the linked SE Ranking project's domain
  (`marketing_links.config.url`, or a display name that plainly is a domain) → the client's
  first website. A pasted `https://www.klant.nl/` is stored as `www.klant.nl`; a real path
  survives, because `scope=url` needs one. `target_origin` on the payload says which it was.
- **Brand**: brand presence counts *that brand's* mentions, so a wrong attribution is a wrong
  number that looks exactly like a right one. Order: the client's own setting → the brand SE
  Ranking attributes to the target (`/ai-search/discover-brand`, 100 units, asked **once** per
  target and stored on the snapshot row) → none, which lets SE Ranking resolve one silently.
  A discovered brand is sent **explicitly** on the overview call, so the figure is attributable
  to a named brand, and printed under the figures. `brand_fits` is false when the discovered
  brand shares no letters with the client's name or the domain's own label
  (`fietsenwinkel-goes.nl` → "Janssen"): a **hint** to type the right one, never a refusal.
  The editor's *Merk opzoeken* answers into the brand box, for the domain typed in the box
  beside it, where somebody who knows the client can see it is right before it is saved.

## 5. Settings: off until somebody switches it on

`marketing_settings.ai_search` (house) and `marketing_company_settings.ai_search` (one
client's diff) — the `rankings` idiom: `NULL` and an absent key both mean *inherit*, a client
may differ **in either direction**, and one `aisearch.resolve()` answers for the dashboard, the
report and the settings screen.

| Setting | House | Client | Default |
|---|---|---|---|
| `enabled` | ✓ | ✓ | **false** |
| `engines` — `all`, `ai-overview`, `ai-mode`, `chatgpt`, `perplexity`, `gemini` | ✓ | ✓ | `["all"]` |
| `source` — the country database | ✓ | ✓ | `nl` |
| `scope` — `base_domain` · `domain` · `url` | ✓ | ✓ | `base_domain` |
| `target`, `brand` | — | ✓ | derived (§4) |

**Off by default, and that is a cost decision.** One read is **800 units** of the agency's own
Data API plan, per engine choice, per client, per month. An upgrade must not start spending
them, so the code default is off, the house default is a tick in Instellingen → Marketing, and
the price is printed under the choice that sets it — on the settings screen, in the client's
editor, and on the refresh button (#305: show the constraint working). The house switch applies
to clients **with an active marketing link**, not to every company in the register: leads and
suppliers are never read from a marketing screen. An empty engine list inherits rather than
meaning "on, and asking nothing".

A house `target` or `brand` is dropped on the way in: one domain's numbers under every client's
name is not a default anybody means.

## 6. One read path, and a paid call made once

`AiSearchService.overview(company_id)` is what the dashboard, the report chapter and the
`marketing.ai_search` assistant tool all call. It answers for last month and **fetches it
first where it is not stored** — so "always shows last month" is a property of the read, not
of a cron that may or may not have run. A client nobody opened this month costs nothing; the
first colleague, client or report run that does pays for it once.

`marketing_ai_search_snapshots` holds one row per (client, target, country, scope, engine,
brand, month). **A stored answer, not a cache**: a report printed from it must reprint the same
figures next year. The request is part of the key — a changed brand is a different question,
and answering it from the old row would print the old brand's numbers under the new name.

- **Claimed in the database before it is called** (docs/PAYMENTS.md's rule): the row is
  inserted as `fetching` under the unique key (`ON CONFLICT DO NOTHING`), or an existing one
  is taken with a conditional `UPDATE`. The page streams, two colleagues open it at once, the
  report worker runs beside them — whoever got the row makes the call, everybody else reads
  `fetching` and the block asks again a few seconds later.
- **The connection is handed back** around every vendor call (`ctx.release_db()`, §11); its
  entry commit is also what publishes the claim to the other replica.
- **The free probe first**: `/account/subscription` says whether the key reaches the Data API
  and whether the plan can afford the read — so an empty plan is reported as one without a
  refused paid call to prove it.
- **A refusal is stored, and re-asked on its own clock**: `failed` after an hour, `denied`
  after six, `insufficient` after a day; a month SE Ranking had not published, after a week.
  The manager's *Opnieuw ophalen* (`POST …/ai-search/refresh`, `marketing.link.manage`) asks
  again at once — its own verb, never a parameter on the read, because it spends units.
- **A refused re-read keeps the month already stored.** The row goes back to `ok` with its
  figures, and the refusal rides that one response as `notice`. Overwriting a good month with a
  refusal would lose figures a report may already have printed, for nothing.
- **An older month keeps showing** while this one is refused: the block draws the newest
  stored month, labelled as the month it is, with the refusal still reported to a manager.

Who sees what: a **manager** (`marketing.link.manage`) gets the settings, the house defaults,
the discovered brands, the units left and every refusal by name. A **reader** gets the figures.
A **client** (`ctx.is_portal`) gets the figures and nothing about the agency's desk — no
refusal, no vendor name in any sentence (#446), and a block with nothing to show is left out.
One level up the same rule: `no_key`, `no_target` and a `ready` answer whose every block was
left out all read as **`off`** to a client, which draws no section at all — "er is geen
SE Ranking-sleutel opgeslagen" names a supplier and a settings screen they cannot open. The
footer's "merk bepaald door SE Ranking" has a vendor-free twin for the same reason.

A client's page view **may** be the read that fetches the month. That is deliberate: the cost
is bounded by the claim (one read per client, engine choice and month, whoever asks first),
the agency switched it on with the price in front of it, and a dashboard that shows a client
last month's figures only after a colleague happens to open it first is a worse product for
no saving.

## 7. Routes

| Route | Permission | |
|---|---|---|
| `GET /marketing/companies/{id}/ai-search` | `marketing.metrics.read` | the overview; fetches last month where it is missing |
| `POST /marketing/companies/{id}/ai-search/refresh` | `marketing.link.manage` | re-ask SE Ranking now (800 units per engine choice) |
| `PUT /marketing/companies/{id}/ai-search/settings` | `marketing.link.manage` | the client's diff, **posted whole** — a `null` field follows the house. Saving asks SE Ranking nothing. |
| `POST /marketing/companies/{id}/ai-search/brand` | `marketing.link.manage` | which brand SE Ranking attributes to a target (100 units) |
| `GET /marketing/settings/seranking/check` | `marketing.link.manage` | §2 |
| `PUT /marketing/settings` | `marketing.link.manage` | `seranking_data_api_key`, `clear_…`, `ai_search` |

Every one is a generated MCP tool (§12); the assistant also gets the curated
`marketing.ai_search`, whose description says in words that opportunity traffic is not visitors.

## 8. The report chapter

`marketing.ai_search_overview` (`report_sections._ai_search_overview`), position 78 — after
Search Console's AI Overviews chapter, before the AI Result Tracker's. It reads the same
service for the month the report is about, so the document and the screen cannot disagree and
a report run early in the month is usually what fetches it.

- **Tiles**: the four figures with their change. The headline is the `all` aggregate where the
  agency reads it, else the first engine picked. Report-side names are prefixed
  (`ai_brand_presence` …) so `ai_average_position` is lower-is-better without touching a rank
  tracker's `avg_position`.
- **Compared with the month before, and saying so** directly under the tiles
  (`compare_period`): these are levels in a channel where a year ago most engines cited nobody
  — the rankings chapter's argument (#312's exception). The cover's "vergeleken met …"
  describes the traffic chapters, which is why a chapter with a span of its own is also passed
  over for the cover's "In één oogopslag" strip while another chapter has figures. Where it is
  **all** a client has, it leads the cover — and then the cover's caption names *its* span
  (`cover_compare_label`, `context._headline_span`): month-over-month badges over "vergeleken
  met augustus 2025" is a sentence about percentages that are not on the page. A tenant's own
  design keeps `compare_label` unchanged and may read the new key.
- **A chart** of link presence over the last six months, handed over as ISO months
  (`2026-08`) and named in the document's language by the renderer. It says what it draws
  (`chart.metric` → `chart_caption`, "Links in AI-antwoorden"): every other chart on the
  document sits beside a table that names its metric, and this chapter usually has none.
- **A table per engine** only where more than one engine choice is read.
- **Withheld, with a reason**: a month SE Ranking had not published, no Data API access, a plan
  out of units — each prints no chapter and puts a warning on the run (`withheld` + `notes`),
  because a loss with nothing taking its place has to be stated to the agency.

## 9. Hosts

`SCHAKL_SERANKING_API4_URL` and `SCHAKL_SERANKING_API_V1_URL` name SE Ranking's two hosts, so
a test stack points them at a fake without a code change (the Microsoft rule,
`docs/MICROSOFT.md`). Nothing else in the product spells either host.

## 10. The checklist to run against a live Data API key

Written from SE Ranking's public API reference, its own MCP server's source and its published
skills — **schakl's own adapter has not yet been run against a live Data API credential** (§11
bans writing an integration from memory, not from a document). Every parse is defensive until it
has been.

**What one live read has settled** (2026-09-19, through SE Ranking's hosted connector, which
reshapes the answer — so it confirms the contract and the units, not our JSON paths):

- The all-engines read (no `engine`) **answers**: `breik.nl`, `source=nl` → brand presence 11,
  link presence 21, AI opportunity traffic 0, average position 5,14. So item 2's path exists,
  and §1's reading holds: both presence figures are **counts**, the position is a decimal rank.
- The connector's contract is `target`, `source`, `engine` (omit for all), `brand` — and it
  states that **`scope` is ignored** on this endpoint's fixed monthly window. We still send it
  (the reference lists it and sending it is harmless), but until item 7 says otherwise a changed
  scope should be expected to change the *target's derivation* (§4) and not the vendor's figures.
- A first read of a target has **no prior-period baseline**: the vendor suppresses its own
  deltas. Ours are computed from the two values we keep (`aisearch.change`): a missing
  `previous` draws no change at all, and a `previous` of `0` draws the absolute move with no
  percentage — never the ±100 % a division would invent.
- The subscription document carries `units_limit` / `units_left` and the misspelt
  `expiraton_date`, exactly as item 1 assumes.

**What a direct run of the adapter's own calls settled** (2026-09-22, `curl` against
`api.seranking.com` with breik.'s Data API key — raw JSON, so these *are* our JSON paths):

- **The two keys are two keys.** The Data API key (a UUID) answers `/account/subscription` and is
  refused by the project API (`403 {"message":"No token"}`); the project key (40 hex characters)
  lists the sites and is refused by the Data API (`401`, `error_description` "Authentication
  failed …"). Both read as `denied` — §2's second key is not optional for breik., it is required.
- Items 1, 2 and 4 hold: `subscription_info` carries `units_limit` / `units_left` /
  `expiraton_date`; the aggregate path answers; `time_series[].date` is `YYYY-MM`, **fifteen**
  months of it, not five.
- **`previous` is null on every figure**, for a target read before as much as for a new one, and
  `change_percent` is then `100`. So the month before is *never* the vendor's: link presence and
  position take it from the series in every case (`parse_overview`), and brand presence and
  opportunity traffic — which have no series — take it from the month **this instance stored**
  a month earlier (`_block`), or have no comparison. Before this was found, the ordinary case
  compared nothing at all.
- **Item 3 has no single answer: the newest month is per target.** On the 22nd, `breik.nl`'s
  newest point was 2026-09 (so it is realigned, and brand presence / opportunity traffic are
  blank for August — the vendor publishes them for the newest month only) while `alga.nl`'s was
  2026-08 (the ordinary row). Reading early in the month is what keeps all four figures; since
  the dashboard reads lazily, the first view after the 1st usually does.
- **Only AI Overviews has a series in the NL database.** `ai-mode`, `chatgpt`, `perplexity`
  and `gemini` answer either `no_index: true` with every figure null (a small site), or presence
  counts with an **empty** series and `average_position: 0` (bol.com). `0` is read as *no
  position* (`aisearch._rank`), and `no_index` is stored as `no_data` — said in words on the
  dashboard, a warning on the report run, and no section for a client. An engine name the API
  does not know (`bogus`) is answered the same way, `200` + `no_index`, never a 400. For a Dutch
  client, `all` (which equals `ai-overview` there) is the useful choice.
- **Item 7: `scope` changes nothing.** `breik.nl` / `base_domain` and `www.breik.nl` / `domain`
  answered byte-identical figures. A changed scope still costs a re-read (it is part of the
  snapshot key) for the same numbers.
- The overview answers `brand` and `brand_origin` itself, so item 6's `discover-brand`
  (`["breik"]`, `["ALGA"]` — it does name small Dutch domains) is the same attribution the read
  would have made silently.

The day a key is in the instance, check, in this order — the first is free:

1. `GET /marketing/settings/seranking/check` — does `subscription_info` carry `units_left` and
   `units_limit` under those names? (The live API misspells `expiraton_date`; both are read.)
2. **The aggregate's path.** "Every engine" is sent to
   `/v1/ai-search/overview/aggregated/time-series` with no `engine`, which is what SE Ranking's
   MCP server does; the public reference documents only `…/by-engine/…`, where `engine` is
   required. If the aggregate path answers 404, that is the one line to change
   (`SeRankingAdapter.ai_search_overview`) — and it will read as `failed`, not as zeros.
3. **Which month `current` is.** Open a client on, say, the 5th: is the newest `time_series`
   point last month (the ordinary row of §3's table) or the month still running? Both are
   handled; this says which one production lives in, and whether `_RETRY_LAGGING` (a week)
   suits how late SE Ranking publishes.
4. `time_series[].date` — `YYYY-MM` as documented, or a full date? Both parse.
5. A refused call's body: is "Insufficient funds" still the phrase, and is it a 400?
   (`classify_refusal`.) A key without Data API access: 401 or 403? Both read as `denied`.
6. `discover-brand` for a Dutch SME domain: does it name anything at all? If it is routinely
   empty for small sites, the brand box deserves a louder hint than it has.
7. **Does `scope` move the figures?** Read one target twice, `base_domain` and `domain`, a
   month apart from nothing else (1 600 units). SE Ranking's connector says the parameter is
   ignored here. If the two answers are identical, say so under the scope control in both
   editors — a setting that changes nothing and costs a re-read is #305's silent field.
