# WordPress — one credential per website, four surfaces

> One WordPress **Application Password** per website, and the Rank Math **AI Visibility**
> numbers it reads, as a fifth `marketing` source.
>
> **Never exercised against a live site with a Content AI subscription.** Everything here was
> written from the plugin source (`seo-by-rank-math`, stable tag **1.0.275**, downloaded from
> wordpress.org) and the official WordPress documentation — which CLAUDE.md §11 permits; it
> bans writing an integration *from memory*, not from a document. §1 carries the checklist to
> run the day a real credential arrives, and every parse is defensive until it has. This is the
> posture `docs/OXXA.md` takes, for the same reason.

## 1. The finding that makes this one feature instead of two

The ask was two things: read Rank Math AI Visibility into the marketing module, and hold a
WordPress credential per website so we can also drive **WordPress MCP**. They are the same
credential against the same host.

Rank Math 1.0.273 shipped `includes/abilities/`, an implementation of WordPress 6.9's
**Abilities API**. Four of those abilities are AI Visibility:

| Ability | Reads |
|---|---|
| `rank-math/get-ai-visibility-overview` | site summary + every tracked brand's score, rank, sentiment, mentions, citations, analysis status |
| `rank-math/get-ai-visibility-brand-insights` | latest analysis: competitors, per-query results, rank, sentiment, citations, raw transcripts |
| `rank-math/get-ai-visibility-brand-queries` | the prompts a brand is tracked on |
| `rank-math/create-ai-visibility-brand` | (write) register a brand for tracking |

Every one is registered with shared meta (`includes/abilities/class-abilities.php`):

```php
'show_in_rest' => true,
'mcp'          => [ 'public' => true ],
```

Those two lines are the whole story. `show_in_rest` puts them on core's
`/wp-json/wp-abilities/v1/rank-math/<ability>/run`; `mcp.public` makes the **MCP Adapter**
plugin expose them as MCP tools at `/wp-json/mcp/<server>`. Both authenticate as any WordPress
REST caller does, which for us means an Application Password:

```
                          ┌─ /wp-json/wp/v2/*                        (posts, media, users, plugins)
one app password ───────► ├─ /wp-json/wp-abilities/v1/…/run          (WP 6.9 core, opt-in per ability)
per website               ├─ /wp-json/mcp/<server>                   (MCP Adapter plugin)
                          └─ /wp-json/rankmath/v1/ai-visibility/*    (Rank Math's own proxy)
```

That is why the credential is its own module rather than a private field of a marketing source:
`marketing` is one of four things that will want it.

### Checklist for the first live credential

Nothing below has met a real site. Before trusting any of it, on one client install:

1. Confirm the AI Visibility module is active and the site is connected to a Rank Math account
   with a **Content AI subscription** (the feature is subscription-gated; the plugin ships the
   code either way and answers `aiv_unauthorized` / 401 when unconnected — which the panel
   already renders as its own line, so this is visible rather than mysterious).
2. `POST /api/v1/wordpress/sites/{id}/verify` and read the capability list: `rest`, `admin`,
   `abilities`, `rankmath_aiv`, `mcp`. Each should match what the site actually has.
3. `GET /wp-json/rankmath/v1/ai-visibility/overview?refresh=1` by hand and check the brand keys
   against §3 — that is the one shape the sync depends on.
4. `GET /wp-json/wp-abilities/v1/abilities` — confirm the four `rank-math/…` entries are listed
   for this user, since that is what the MCP half (gate 3) will read.
5. Confirm the host does not strip the `Authorization` header (a real and common failure on
   shared hosting; it surfaces here as `credential_refused` with `rest_not_logged_in` beside it).
6. Note the analysis cadence actually observed on the plan the agency buys — it decides whether
   §3's daily snapshot is honest or is recording the same number seven times.

## 2. What Rank Math's REST surface actually is

`includes/modules/ai-visibility/Api/` is a **cache-backed proxy**, not a data source. The site
holds no AI Visibility data of its own: `Base_Controller::remote_request()` calls Rank Math's
backend with headers taken from the site's registration (`x-username`, `x-site-url`,
`x-cai-api-key`) and caches the answer in `wp_options` (`rank_math_aiv_dashboard`) plus
per-brand transients.

There is **no public, documented Rank Math AI Visibility API** we could call directly, and the
credential it uses is the site's own Content AI key. So going *through WordPress* is not a
workaround, it is the only sanctioned path — and it happens to be the path that also gives us
MCP.

Routes, under namespace `rankmath/v1/ai-visibility` (`Rest_Helper::BASE` is `rankmath/v1`), all
gated on **`manage_options`**:

| Method | Route | Note |
|---|---|---|
| GET | `/overview` | `?refresh=bool`, `?search=string` |
| GET | `/brands/{uuid}/insights` | competitors + per-query results + transcripts |
| GET | `/brands/{uuid}/queries` | tracked prompts |
| POST/PATCH | `/brands`, `/brands/{id}`, `/brands/{id}/queries/{qid}` | writes (not used yet) |

### The ability and the REST route are not interchangeable

The single easiest thing to get wrong here, and it fails silently.
`Get_AI_Visibility_Overview::execute()` calls `Cache::get_dashboard()` and **nothing else** —
its own `refresh` input is documented as bypassing the 12-hour cache and is in fact used only
for telemetry. The REST controller is the one that can force upstream:

```php
$cached = Cache::get_dashboard();
if ( null !== $cached && ! $force ) { … return stale-or-fresh … }
$result = $this->remote_request( 'GET', '/api/v1/overview' );
```

So **the nightly sync calls `GET /rankmath/v1/ai-visibility/overview?refresh=1`**, and
`tests/test_marketing_rankmath.py::test_the_sync_forces_a_fresh_upstream_fetch` pins it. An
implementation that reached for the ability because it is the newer surface would chart a
number that moves only when a human opens the WordPress dashboard — a chart of when somebody
last logged in, drawn as a chart of a client's AI visibility.

## 3. What the data is, and what it is not

Per brand, from `map_overview_brand()`: `id` (uuid), `name`, `url`, `locale`, `status`,
`score`, `rank`, `avg_sentiment`, `mentions`, `citations`, `last_analyzed`, `analysis_status`.

Two things it is **not**:

- **It is not a time series.** Every upstream path is "latest"; there is no history endpoint
  anywhere in the plugin. So `fetch_daily` ignores `start` and writes **one** row stamped
  `end` — the trend line exists because we store snapshots, not because Rank Math has one.
  Answering thirty identical rows for a thirty-day window would be a flat line that looks like
  measurement.
- **It is not daily.** Analyses run on Rank Math's own cadence per plan, so consecutive
  snapshots legitimately repeat. `last_analyzed` is carried into the stored metrics so a report
  can say what it is actually comparing rather than announcing a 0% week (#312's rule).

### What each number is, and on what scale

Read out of the plugin's own UI (1.0.276) rather than guessed, because the scale is the whole
meaning and two of the five were being printed on the wrong one:

| key | Rank Math's own name | what it counts | scale |
|---|---|---|---|
| `ai_visibility_score` | AI Visibility Score | rank, mentions, citations and sentiment folded into one figure | 0-100, printed `X / 100`; green from 70, red under 40 (`ScoreBadge`) |
| `mentions` | Mentions | times the brand was named in the answers to the tracked prompts | count, a running total as at the last analysis |
| `citations` | Citations | the subset of those mentions that **linked** to the brand's site — *"mentions that include a link to the selected brand's website"* | count |
| `avg_sentiment` | Avg sentiment | how positively the brand is described, averaged over its mentions | **0-100, printed as a percentage**; green from 70, red under 50 (`SentimentBadge`) |
| `brand_rank` | Avg Rank | the brand's average place among the brands an answer names | 1, 2, 3 … — 1 is best, hence `LOWER_IS_BETTER` |

Two corrections came out of writing that table down, and both had shipped:

- **`avg_sentiment` is not a −1…1 ratio.** `format.ts` said so in a comment that admitted it was
  a guess, and formatted at two decimals — so a mildly positive brand printed `46,00` beside a
  mentions count, a number with no unit and no ceiling. The plugin renders `${round(score)}%`.
- **`enabled`, the prompts drill-down's only column, had no label at all.** `marketing.metric.enabled`
  did not exist, so the header printed the literal key `marketing.metric.enabled` over a column
  of `1` and `0`. It is now *Gevolgd* over *Ja* / *Nee*.

The cadence is **weekly** by default (`AddBrandModal`'s frequency select; "Daily (Coming Soon)"
is disabled), which is the plugin-side confirmation of §3's "it is not daily".

**Every Rank Math metric is in `AVERAGED_METRICS`**, including `mentions` and `citations`.
Those two *look* like counts and are not: Rank Math reports running totals as of the last
analysis, so two daily snapshots of "18 mentions" mean eighteen, not thirty-six. Summing a
month of `ai_visibility_score` would produce a four-figure visibility score — the trap
`avg_position` already documents, with five ways to fall in.

## 4. The module

`apps/api/app/modules/wordpress/` mirrors `cloudflare` and `uptime` one level down the tree:
Cloudflare is something a **domain** has; uptime and WordPress are things a **website** has.

- **`wordpress_sites`** — one row per website, `UNIQUE (org_id, website_id)`. That index *is*
  "one unified credential per website"; the service's 409 is the friendly half of it.
  Org-scoped, RLS-forced, auditable (§16 — the trail records `password_changed`, never a value).
- **The credential is a row, not a setting** (`cloudflare`'s rule): an agency holds dozens of
  client sites and none of them is "the" WordPress account.
- **Decided vs observed, in separate columns.** `base_url` / `username` / the password are
  intent; `capabilities` / `capability_errors` / `rankmath_version` / `mcp_server_path` are the
  last thing the site said about itself. `capabilities_checked_at` is separate from
  `capabilities` because an empty map cannot distinguish *we looked and this reaches nothing*
  from *nobody has ever looked*.
- **No `wp_version`.** Core does not publish its version over REST, and the question that
  column would have answered ("new enough for the Abilities API?") is answered honestly by
  whether `wp-abilities/v1` is in the site's own REST index — which `capabilities["abilities"]`
  records. A version string we could not observe would be a stored fact nobody checked.
- **Company horizon (#285).** No `company_id` here, and the client is the website's *domain's*
  client — two joins, declared as `__company_horizon_clause__`. Without it the repository's
  column match finds nothing and therefore filters *nothing at all*, handing every restricted
  membership the org's WordPress administrator credentials. Pinned by
  `test_a_restricted_member_lists_only_their_own_clients_sites`.

### The probe is evidence, never the gate

`client.probe_capabilities()` runs five independent probes and **keeps every refusal**:

| capability | probe | a `False` means |
|---|---|---|
| `rest` | `GET /wp/v2/users/me?context=edit` | the credential itself was refused |
| `admin` | that user's `capabilities.manage_options` | valid credential, not an administrator |
| `abilities` | `GET /wp-abilities/v1/abilities` | WordPress older than 6.9 |
| `rankmath_aiv` | `GET /rankmath/v1/ai-visibility/overview` | plugin absent, or no Content AI subscription |
| `mcp` | the `mcp/` namespace in `GET /wp-json/` | the MCP Adapter is not installed |

None gates another, because the states are independent in reality: Rank Math is routinely
absent from a site whose posts API is perfectly healthy, and MCP from a site where Rank Math
works. **A read that succeeds outranks a probe that refuses** — a credential that reached
`wp/v2` is `active` even if the other four failed, and only one refused by *every* probe is
called refused. `unreachable` and `not_wordpress` are separate from `credential_refused` for
the same reason `uptime` splits `needs_reauth` off `error`: they say nothing about the password,
and reporting them as auth failures sends somebody to re-mint a credential that was never wrong.

**Failing softly is about not raising, not about not remembering.** Every ✗ carries the site's
own error text (`rest_no_route`, `rest_forbidden`, `aiv_unauthorized`) — untranslated on
purpose, because it is a quote, and translating a quote is how a diagnosis stops matching the
log line an admin is reading. A ✗ with no explanation is the one state nobody can act on.

**The flag has a mirror.** `_record_probe` assigns `last_error` unconditionally, so a probe that
gets through clears the red line a previous one set. A status flag that only ever turns on is a
bug with a long tail (`docs/CLOUDFLARE.md`'s `_flag_account`), and
`test_a_verify_that_succeeds_clears_the_error_it_set` is what stops it coming back.

**The fake rejects a bad credential everywhere.** `tests/wordpress_fake.py` authenticates every
route before doing anything else; its toggles turn individual *surfaces* off and none of them
makes a wrong password work. A stand-in kinder than the real server is a stand-in the bug hides
in — specifically, a probe that concludes "the credential is fine" from an endpoint it never
authenticated against.

### The permission cost, stated plainly

Every Rank Math AI Visibility route is `manage_options`, so **the application password belongs
to a WordPress Administrator**. There is no read-only shape to ask for. `wordpress_sites` is
therefore a table of full-admin credentials for every client site the agency touches — a
materially bigger blast radius than a Cloudflare token scoped to DNS reads.

Hence: `wordpress.site.manage` is **admin-only** and never folded into `websites.website.write`;
neither key is granted to `client`; the password is write-only through the API and absent from
every response, log line and trail entry; and disconnecting forgets the credential without
revoking it at the far end (that is the client's act on their own profile screen, and doing it
as a side effect of tidying a list would break whatever else that password was minted for).
`wordpress.site.read` goes to `member`, because "is this client's site connected?" is a question
an account manager asks while doing ordinary work (#310).

### The screen

One `EntityPanelSpec` on the **website** detail page, position 20 (uptime holds 40) — no nav
item, for `cloudflare`'s reason: WordPress is not a place you go. The website route needed no
edit to receive it beyond one import and one spread of `wordpressActions`, because it already
renders `entityPanelsFor(enabled, "website", user)`.

The panel's `load` is one stored-state read and touches no outside service, so a website page
renders at the same speed whether the client's WordPress is up, down, behind a firewall or gone.
Going and looking is the panel's own explicit action.

## 5. Rank Math as a marketing source

`MarketingSource.RANKMATH`, adapter in `sources/rankmath.py`. It is the **client-facing
AI-visibility figure**; SE Ranking's `ai_search` stays the per-LLM drill-down it already was.
Two vendors' scores presented as one dashboard number is not a screen anyone can summarise
(#312), so only one of them is a tile.

```python
RANKMATH_METRICS = ["ai_visibility_score", "mentions", "citations", "avg_sentiment", "brand_rank"]
```

`brand_rank` is also in `LOWER_IS_BETTER`. Drill-downs are `competitors` and `queries`, fetched
live and never stored.

### A metric nobody can name is a metric nobody can act on

The other four sources speak a vocabulary a marketeer already owns: everyone knows what a
session is. Rank Math's five are new to *both* audiences, and the labels are as short as a tile
allows — so "Bronvermeldingen 6" sat next to "Vermeldingen 18" with nothing on the screen saying
that the first is the subset of the second that carried a link. The agency could not explain the
dashboard it had built and the client could not read the one it had been given.

So a metric carries its sentence: `marketing.metric_help.<key>`, with siblings
`marketing.drilldown_help.<kind>` and `marketing.source_help.<source>`, resolved by
`format.ts`'s `metricHelp` / `drilldownHelp` / `sourceHelp`. Three rules:

- **A miss is the empty string, not the key.** `t()` answers with the key it could not find (the
  `channelLabel` rule), so an undescribed metric would print `marketing.metric_help.sessions`
  under its tile. Collapsing a miss to `""` and rendering nothing for it is what makes this
  safe to ship for one source and fill in for the other four later, one key at a time and with
  no code change.
- **Written out where it is read, hovered only where there is no room.** The tab's KPI tiles and
  the drill-down headings print the sentence; the company panel's four-figure summary, the
  drill-down column headers and edit mode's drag targets carry it as `title`. A hover is not an
  explanation on a phone, and the client's own dashboard is the surface this is for.
- **It is the same section component for both audiences.** `MarketingSourceSection` renders the
  staff tab and the portal widget, so there is no version of this that explains the numbers to
  the agency and not to the client.

### The third auth kind

`sources/base.py` knew `AUTH_GOOGLE` (per-user OAuth grant, scopes, reconnect) and
`AUTH_ORG_KEY` (one agency key). WordPress is neither: **one credential per website**, therefore
per *link*, with its own failure states.

#300's docstring predicted a new source is "one line in `SOURCES`, no service change" and missed
exactly one thing — authentication. The fifth source missed it the same way, for a third kind of
credential. Two identical surprises is a pattern, so the per-kind branches at the call sites
became one dispatch:

```python
@asynccontextmanager
async def keyed_client(session, org_id, source, website_id=None) -> AsyncIterator[Any]: ...
```

raising `SourceNotConfigured` (carrying a per-kind i18n key: an org key is set in Instellingen,
a site credential on one client's website page). Google is deliberately *not* in it — its client
needs a per-user connection, an incremental scope check and a reconnect prompt, none of which is
expressible as "hand me a credential", which is what `AUTH_GOOGLE` means.

The credential is resolved **before** the accounts cache is consulted, which is what the org-key
path always did: an install that has not configured a source answers `configured=False` without
a cache round trip, because that is the answer on every page load forever, not a miss worth
caching.

### The seam, and why the client rides on it

`marketing` may not import `app.modules.wordpress` (§6), so `app/core/wordpress.py` holds a
registered `resolve_credential` **and** a registered `open_client` factory — the
`app/core/registrar/presence.py` shape. The factory is there because the *transport* is the
module's decision too: the day the client grows a retry policy or a per-site TLS quirk, it
should land in one file rather than at whichever call sites had imported the class.

With the module disabled nothing is registered and the resolver answers `None`, which every
caller already handles — it is the same answer as "this website has no credential yet".

### Link rules

A `rankmath` link **requires** `website_id` and a connected website, refused at create time with
`errors.marketing_rankmath_website_required` / `..._not_connected` rather than discovered as a
`last_error` on the first nightly run. `GET /marketing/accounts` gained an optional `website_id`
— additive at the API boundary on purpose, since a required parameter would 422 the four
existing sources.

## 6. Migration & rollback

`b5d1c4e78a02_wordpress_create_sites` is expand-only: it creates `wordpress_sites` (RLS forced)
and widens `marketing_links.source` from `varchar(8)` to `varchar(16)`. `"rankmath"` is exactly
eight characters — it fit, and a schema depending on that coincidence breaks on the next source.
Widening a varchar is metadata-only in Postgres, and an older release keeps reading and writing
its four short values unchanged, so this needs no two-release dance (`docs/WORKFLOW.md`).

The downgrade narrows the column back, which **fails on any `rankmath` row** — the honest
behaviour: the rollback path is to unlink those first, and Postgres raises rather than
truncating, so nothing is silently lost.

## 7. Not built yet

- **The MCP half.** `mcp_server_path` is discovered and stored, but schakl has no MCP *client*
  (`app/core/ai/tools.py` is a registry of our tools; `app/core/mcp/server.py` is us serving
  MCP). The cheaper alternative — an `AIToolSpec` calling the abilities REST endpoints directly
  — needs no adapter dependency and is the likelier next step. Whichever is built:
  **confused-deputy (§12) applies in the other direction** — never pass an incoming MCP
  credential outward; the site's password is resolved from our row, under our permission check,
  for our caller's org. The two credentials must never meet.
- **The report section.** The insights payload (competitors, per-query results, raw transcripts)
  is the most quotable material in the module and belongs in a monthly report — through
  `report_sections` on the descriptor, and through `present.py`, or a Dutch client will read
  `avg_sentiment: 0.4595` back at them.
- **Writes.** Creating a brand, editing prompts, `POST /brands/{id}/generate-queries`.
- **An Instellingen overview** of every connected site. The panel is the working surface today.
