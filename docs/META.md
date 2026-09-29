# Meta (Facebook Pages, Instagram, Meta ads)

> **Status: built, and never run against Meta.** The design below was written on 2026-09-29
> from Meta's published documentation and the code was written from the design; every call is
> exercised against a stateful fake (`tests/meta_fake.py`) in the test suite and in a browser.
> **No request has reached `graph.facebook.com`.** CLAUDE.md §11 allows an integration written
> from a document and asks for exactly this sentence: every parse here is a hypothesis until a
> real response has been fed to it. Claims are marked **[O]** (read on an official Meta page),
> **[S]** (secondary source only) or **[U]** (could not be verified). §11 is the half-day
> checklist that turns the **[U]**s into measurements, and it is to be run before the first
> client's post depends on this. §12 records the decisions the owner took.

## 1. What is being asked for, and the fact that shapes all of it

Schedule and publish posts to clients' Facebook Pages and Instagram accounts, and create and
manage their Meta ads, from schakl and therefore from MCP — **without** App Review, Business
Verification or Access Verification, i.e. without handing Meta company documents.

**Meta gates who may *grant* a token, not what the token may do.** An app that has passed no
review holds every permission at *Standard Access*, and a Standard Access permission "can only
be requested from app users who have a role on the requesting app" **[O]** — or a role in the
Business portfolio that owns the app **[O]**. Review, Business Verification and Access
Verification are all triggered by one thing: a token granted by somebody *outside* that circle.

So the verification-free route exists, and it has exactly one shape:

- the **agency creates its own Meta app**, inside its **own Business portfolio**;
- every token is granted by the agency's own **system user** or its own staff;
- clients never log in. They **share their Page, Instagram account and ad account with the
  agency's portfolio as a partner** (or add an agency employee to them), which is what agencies
  already do to work in Meta Business Suite.

What this rules out, permanently, without verification:

| Shape | Why not |
|---|---|
| "Connect your Facebook" button for a client | A non-role user grants the token → Advanced Access → App Review + Business Verification |
| One Meta app shipped by schakl for every install | The app serves other businesses → Tech Provider → all three gates |
| Meta's own hosted Ads MCP (`mcp.facebook.com/ads`) | Agencies acting for other businesses need Advanced Access on `ads_mcp_management` **[O]**; ads only, no organic publishing |

**Verdict: feasible, with three unproven points and one policy risk** (§2). None of the
unproven points can be settled by reading; all three can be settled in an afternoon (§11).
The owner chose to build first and measure on the first live credential, accepting that an
afternoon's finding may still change a parse.

## 2. What Meta allows without verification

| Capability | Permissions | Without verification? | Caveat |
|---|---|---|---|
| Create app on an unverified portfolio | — | Yes **[O]** | Privacy policy URL required by Platform Terms |
| System user + token | any Standard Access scope | Yes **[O]** | 1 system user + 1 admin system user on the Limited ads tier |
| Assets the agency's portfolio **owns** | all below | Yes **[O]** | — |
| Client assets **shared as partner** | all below | Probably **[U]** | Assignable to a system user **[O]**; that Standard Access suffices is stated nowhere and secondary sources disagree. **Pilot item 1** |
| Publish / schedule a Page post, photo, video, reel | `pages_manage_posts`, `pages_read_engagement`, `pages_show_list` | Yes, with a caveat | Content made by an app in Development mode "can only be seen by role users" **[O]**. **Pilot item 2** |
| Publish to Instagram (feed, reel, story, carousel) | `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`, `pages_show_list` | Yes, with caveats | Account must be professional and linked to a Page; media at a public URL; 100 posts / 24 h |
| Page and Instagram insights | `read_insights`, `instagram_manage_insights` | Yes | Metric names churned in 2025–26 (§8) |
| Read ads and ad insights | `ads_read` | Yes | Limited tier rate limits (§7) |
| Create and manage ads | `ads_management`, `business_management`, `pages_manage_ads` | Probably **[U]** | Limited tier is labelled "for development only"; no statement found that ads do not deliver. **Pilot item 3** |
| Webhooks | `pages_manage_metadata` | Unknown **[U]** | A forum answer says Advanced Access; design for polling |

Two operating facts follow from the second unproven point. If posts from a Development-mode
app are indeed invisible to the public, the app is switched to **Live**, which needs no review
but does need a reachable privacy policy URL and the annual **Data Use Checkup** — a
self-declaration, no documents **[O]**. And Business-type apps are documented elsewhere as
having no modes at all **[O]**; which regime a newly created 2026 app falls under is visible
only in its dashboard.

**The policy risk.** Meta defines a Tech Provider as a business that accesses "business data
owned by other businesses in order to provide services … to those businesses" **[O]**. An
agency is that, by the plain words. The *enforcement* is tied to requesting Advanced Access,
which this route never does, and no enforcement case against this pattern was found **[U]**.
Where Meta does flag a business, the documented consequence is an e-mail and sixty days to
complete verification **[O]**, not an instant ban. Low probability, high impact: one suspended
app stops publishing for every client at once. It is a risk to accept knowingly, not one the
design removes.

## 3. The account model

Three layers, and each is a row rather than a setting (Cloudflare's rule, §10 of CLAUDE.md).

**The app is the tenant's own.** App id and app secret are stored per org, encrypted
(`app/core/crypto`), entered in Instellingen → Integraties → Meta. No instance-level fallback is
offered as a convenience: an instance-wide app on a multi-org cloud install *is* the Tech
Provider case. This matches the cloud posture, where Google and AI credentials are already
bring-your-own per org.

**The credential is a row, and there is one kind: a system user's token.** Generated in
Business Settings for the portfolio's system user and pasted in. It belongs to the business and
survives any employee leaving. A token granted by a *person* signing in through Facebook Login
was considered and **not built** (§12): it carries the off-boarding problem schakl already knows
(§15, deactivated members), where a leaver's token is an outage for every asset reached through
it. "Share it with our portfolio" is the condition the agency sets for a client. A personal
token that is pasted in anyway works, and the settings screen says what it is and what happens
when that person leaves.

### The token's clock

A system user token is issued as **never expiring** or for **60 days** **[O]**, and which one
it is decides everything after it.

| What | How |
|---|---|
| Reading the clock | `GET /debug_token`, authenticated with the app token `{app-id}\|{app-secret}`. `expires_at: 0` is *never*, stored as `NULL` |
| Proof on every call | `appsecret_proof` = HMAC-SHA256 of the token with the app secret, sent with every request, so a leaked token alone is not enough |
| Renewing | `GET /oauth/access_token?grant_type=fb_exchange_token&client_id&client_secret&set_token_expires_in_60_days=true&fb_exchange_token=…`. The answer is a **new** token; the old one stays valid until its own expiry **[O]** |
| When | Nightly at 03:40, for every token with **20 days or less** left (`REFRESH_WINDOW`). Twenty, because a token that has expired **cannot be renewed at all** — only replaced by hand in Business Settings |
| When it fails | Reported to the holders of `meta.settings.manage` at **14, 7 and 1 day** before expiry, once per stage (`warned_days`), and once more when it has expired. In the bell and by e-mail by default |
| Which credential failed | A refused **app secret** is not a token problem and is not reported as one (`meta.error.app_secret_refused`): the token is fine and the fix is a different field (SnelStart's two-credentials rule) |
| Page tokens | Derived per Page (`GET /{page-id}?fields=access_token`), cached encrypted for 7 days, and **forgotten the moment their parent token changes** |
| Never gated | The refresh job is not licence-gated: a lapsed licence that let every token run out would turn a billing event into an outage nobody can undo |

The settings screen prints the clock in words: *Verloopt nooit*, or *Geldig tot 28 nov 2026,
nog 59 dagen* with *schakl verlengt het vannacht* once it is inside the window, and *Nu
verlengen* in the row's menu for somebody who would rather not wait for the night.

**An asset is a row, linked to a client.** `meta_assets(org_id, credential_id, kind
[page | instagram | ad_account], external_id, company_id NULL, …)`, unique on
`(org_id, kind, external_id)`. Decided columns (`company_id`, `active`) sit apart from observed
ones (`name`, `username`, `currency`, `timezone`, `tasks`, `linked_page_id`,
`account_status`), with `status`, `last_error`, `last_verified_at` and a statement of what
clears each flag. Nothing ever picks an asset for a client: discovery lists what the credential
can reach and a person links it.

Discovery reads the portfolio's edges (`owned_pages`, `client_pages`, `client_ad_accounts`,
`owned_instagram_accounts`, and `instagram_business_account` per Page) **[O]**. Whether
`/me/accounts` answers for a system user token is **[U]**, so it is not the primary path.

## 4. Where it sits in schakl

By §6a's test these are integrations: with Meta gone they are gone.

```
apps/api/app/core/metagraph/            transport, error classification, asset resolver seam
apps/api/app/integrations/meta/         credentials, assets, organic posts   prefix /meta-business
apps/api/app/integrations/meta_ads/     ads read + write, policy, decisions  prefix /meta-ads
apps/web/src/lib/integrations/meta/ , meta_ads/
apps/web/src/routes/(app)/marketing/social/      the planner, a post's page, the channels
apps/web/src/routes/(app)/marketing/meta-ads/    accounts, campaigns, decisions, limits
apps/web/src/routes/(app)/settings/meta/         the app, the tokens, who publishes
```

- **The URL prefix cannot be `/meta`.** `app/core/meta.py:46` already mounts `/api/v1/meta`
  (`/meta/me`, `/meta/tenant`, `/meta/modules`), and an MCP section is derived from the router
  prefix — `/mcp/meta` would list core routes, one of them unauthenticated, beside the new
  tools. The package keeps the name; the prefix differs, as Tag Manager's does (`/gtm`).
- **Two packages, `google` / `google_ads`'s split.** `meta` requires nothing; `meta_ads`
  requires `meta`. An agency that only plans posts does not buy or enable the ads half, and an
  agent handed `/mcp/meta-business` sees 30 tools and one handed `/mcp/meta-ads` another 30.
- **The transport is core's**, because `marketing` reads ad metrics and may import neither
  package (§6) — `core/googleads`'s reason.
- Both join the `growth` bundle. Permissions are never granted to the `client` role.

### Permissions

The split follows where the audience changes (Tag Manager's rule) and where money moves
(Google Ads' rule).

| Key | Default | What it covers |
|---|---|---|
| `meta.settings.manage` | admin | App, credentials, linking assets to clients |
| `meta.asset.read` | admin, member | Assets, published posts, insights |
| `meta.post.write` | admin, member | Drafts: create, edit, attach media, offer for approval |
| `meta.post.publish` | admin | Approve, schedule, publish now, change a planned post, retry |
| `meta_ads.account.read` | admin, member | Campaigns, ad sets, ads, insights |
| `meta_ads.campaign.write` | admin | Create and edit campaigns, ad sets, creatives, ads — **always created `PAUSED`** |
| `meta_ads.budget.write` | admin | Budgets and bids |
| `meta_ads.ads.activate` | admin | `PAUSED` → `ACTIVE`: the act that spends |
| `meta_ads.policy.manage` | admin | Guardrails |

**Scheduling is publishing.** A scheduled post is a stored decision that a worker executes
with nobody in front of it, so it is gated when it is written (#335): `post.publish`, not
`post.write`. That makes "let the assistant prepare next month's posts and I will approve
them" an API key rather than a conversation — and makes a key that may draft and never
broadcast the sensible default for an agent.

## 5. Organic publishing

**Who publishes a planned Facebook post is the tenant's choice** (Instellingen → Meta →
Publiceren, `meta_settings.facebook_scheduler`), because each answer costs something the other
does not.

| | `schakl` (default) | `meta` |
|---|---|---|
| Who publishes | schakl's worker, on the minute | Meta's own scheduler, handed the post on approval |
| Visible in Meta Business Suite before it is live | No | Yes |
| schakl is down at the due time | Goes out late | Goes out on time |
| Changing it before the due time | A local edit | Taken back from Meta first; refused whole if Meta will not let go |
| A colleague changes it at Meta | Cannot happen | Can, and schakl is not told |
| Window | Any time | 10 minutes to 30 days ahead **[O, and three pages give three windows]**; outside it the post waits here and is handed over when the window opens |

**Instagram is always schakl's worker**: its API has no scheduling at all **[O]**. A delivery
records which scheduler it was given (`meta_post_targets.scheduler`), so flipping the setting
changes what is planned *next* and never what is already handed over.

A post is one row and its **deliveries** are rows of their own: `meta_posts(company_id, format,
body, link, media[], scheduled_at, status, approved_by…)` and `meta_post_targets(post_id,
asset_id, channel, body_override, status, external_id, permalink, container_id, attempts,
last_error…)`. One post to a Page and an Instagram account is one decision and two outcomes,
which is what makes *partly published* sayable. Statuses: `draft` → `review` → `scheduled` →
`publishing` → `published` | `partial` | `failed`, and `cancelled`. Swept by a cron once a
minute (nothing in the repo defers a job for days, and Redis should not be what remembers a
client's campaign).

- **Approval is a status, not a conversation.** Somebody holding `post.write` *offers* a draft
  (`review`); somebody holding `post.publish` approves it by giving it a time. A key that may
  write and not publish is what an agent is handed.
- **A refusal is fixable.** A post that failed on every channel is taken back to a draft,
  changed and sent again (`POST …/unschedule`); one that landed anywhere is not, because a
  draft of a live post is a second post.
- **One post is one act, decided by the database.** The sweep claims a row with a conditional
  `UPDATE … WHERE status = 'scheduled'` before calling out — two API replicas and a retrying
  worker share no memory (docs/PAYMENTS.md's rule).
- **Meta has no idempotency key** **[U as an absence]**, so a create that timed out is of
  *unknown* outcome and is **never retried blind**. The row goes to `publishing`, and the next
  sweep looks first: Instagram's container answers `status_code = PUBLISHED`; a Page's recent
  posts are read and matched. A duplicate post on a client's Page is the failure this exists to
  prevent.
- **Instagram media must be fetched by Meta from a public URL** **[O]**. The public file route
  serves `branding` only (`storage/service.py:33`), so the post's media gets a capability URL
  in the public invoice's shape (`secrets.token_urlsafe(32)`, `no-referrer`, `noindex`), minted
  when the container is created and **withdrawn once the post is published**. An instance
  behind Cloudflare Zero Trust must exempt that one path, or Meta's fetcher is refused and
  every Instagram image fails — a deployment requirement that belongs in docs/DEPLOY.md. Video
  and reels can use the resumable upload and need no public URL.
- **Instagram takes JPEG only**, 4:5 to 1.91:1, up to 8 MB **[O]**. The draft is checked when
  it is saved, not when it is published: a refusal at 09:00 on the day is a post nobody sends.
  A PNG is served to Meta as JPEG; an aspect ratio outside the range is an issue naming the
  picture and its measurements.
- **A picture is a JSON call too** (`POST /posts/{id}/images`, base64). The browser uploads
  multipart; a tool call cannot, and an agent that could write every part of an Instagram post
  except the picture could publish nothing there.
- **A browser's line breaks are stored as line breaks.** A form submits CRLF: counted and
  published as sent, a caption two characters under Instagram's limit is refused and Meta prints
  the carriage returns.
- **Limits are read, not assumed**: 100 Instagram posts and 30 Page reels per 24 h **[O]**.
- **Published state is polled.** Webhook access is unverified, and an outcome the agency must
  know about (failed, token dead) is a notification to whoever scheduled it.
- **On the agenda**: a `CalendarSourceSpec` with `move`, so a planned post is dragged to
  another day by whoever holds `post.publish`.
- **On the client's page**: a *Social* panel with the channels managed for them and what goes
  out next, and a *Meta Ads* panel listing their ad accounts. Both read our own rows.
- **A client sees and approves in the portal** — **not built**. `approved_by` is on the row
  from the start for it, and until then both models declare `__portal_horizon_clause__` as
  `false()`: a portal login reads no post and no channel.

## 6. Ads

Campaign → ad set → creative → ad, on `ads_management`. The write spine is Google Ads' own:
resolve the account (404, never 403) → kill switch → policy → build → one call with the
database released (§11) → a decision row and a trail line per applied change.

- **Everything is created `PAUSED`**, which the API supports on all three levels, and
  `execution_options=["validate_only"]` gives a true dry run **[O]**. Activation is its own
  key, so an agent can build a whole campaign that cannot spend a cent.
- **A retry is safe for a read and never for a write**; a retried create is a second campaign
  on a second budget.
- **Policy**, three layers as in `google_ads/policy.py`: a relative ceiling built in (a budget
  may at most double), house and per-account absolute ceilings, a daily-budget maximum. Ad set
  budgets may change only four times an hour **[O]**, which is refused here with the number
  rather than discovered as error 613.
- **Fields a Dutch agency needs on every call**, none guessed:
  `special_ad_categories` (required, `NONE` allowed); `dsa_beneficiary` and `dsa_payor` on any
  ad set targeting the EU — resolved from the account's defaults, else from the client's legal
  name (`document_name`), and refused when neither exists; `is_adset_budget_sharing_enabled`;
  an explicit `targeting_automation.advantage_audience`; `instagram_user_id`, the
  `instagram_actor_id` it replaced having been removed in 2025 **[O]**.
- **`ISSUES_ELECTIONS_POLITICS` with EU targeting is refused before the call**: Meta stopped
  those ads in the EU in October 2025 **[O]**.
- **Boosting a post** is a creative with `object_story_id`, which ties the two packages
  together and is the most likely first thing anybody asks an agent for.
- **What Meta does not store is worth storing**: the decisions log, including "looked at this
  and chose not to act" (#318).

## 7. Performance and cost

The Limited tier scores each ad account at 60 points per 300 seconds, a read costing 1 and a
write 3 **[O]**: about twenty writes per five minutes per account. A campaign with one ad set
and one ad is roughly twelve points. That is ample for a person or an agent building
campaigns one at a time and useless for bulk edits, so bulk ads writes are not offered.
Every ads figure on a screen is a **live read**, streamed behind the page's shell and asked
for one period at a time; there is no nightly mirror yet (§8). The account itself is read from
our own row (`GET /accounts/{id}`), so opening an ads screen costs Meta nothing until the
figures are asked for (`…/live`, `…/insights`). `X-Business-Use-Case-Usage` is read on every response and a refusal carries the minutes to
wait in `details` (§9). The Full tier needs App Review and is therefore out of scope.

## 8. Marketing and reporting

**Not built.** Meta is not yet a source on the marketing dashboard or a section in the monthly
report. What is known for when it is: `MarketingLink.source` is `String(16)`, so `meta_ads`
needs no migration; a fourth auth kind is needed in `keyed_client`, resolved through
`core/metagraph/assets.py`, which is the seam this build already registers into. Two warnings:
Meta removed `impressions` and the page-fans metrics in 2025–26 in favour of `views` /
`*_media_view`, and the reference page still lists metrics the changelog removed **[O]** — the
organic insights read here asks for one metric at a time for exactly that reason, so a metric
Meta stopped serving costs its own tile and never the page. And the report's existing "social"
section is GA4's *Organic Social* traffic split, not post performance; a real one is a new
section, not a rename.

## 9. Versions

Graph and Marketing API **v26.0** (2026-07-29). Marketing v24.0 expires 2026-10-06 **[O]**.
Marketing versions live about a year, so the pinned version is one setting
(`SCHAKL_META_API_VERSION`, beside `SCHAKL_META_GRAPH_URL`), printed at the foot of the
settings screen, and a sunset version is classified as its own error (`meta_version`) rather
than as a bad request — a version sunset is otherwise an outage that arrives on a date nobody
wrote down.

## 10. What is built, and what is not

| Built | Where |
|---|---|
| The app, tokens with their clock, discovery, channels linked to clients | `meta`, Instellingen → Meta, Marketing → Social → Kanalen |
| Posts: draft, approval, schedule, publish now, retry, rework, duplicate, cancel | Marketing → Social |
| Both schedulers for Facebook, as a setting | `meta_settings.facebook_scheduler` |
| The sweep, the look-before-retry for a lost answer, the media capability URL | `publisher.py`, `media.py` |
| Agenda feed with drag to another day; panels on the client's page | `calendarSources`, `panels.py` |
| Notifications: a post that did not go out, a token that could not be renewed | `meta.post_failed`, `meta.token_expiring` |
| Ads: accounts, campaigns, ad sets, ads, insights; `PAUSED` creates; budgets; activation; boost; limits; decisions | `meta_ads`, Marketing → Meta Ads |
| Two MCP sections, both in the `growth` bundle | `/mcp/meta-business` (30 tools), `/mcp/meta-ads` (30 tools) |

| Not built | Why |
|---|---|
| Meta as a marketing source and a report section | §8 |
| Client approval in the portal | §5 |
| Stories; a video uploaded from the file store | A story has no caption and its own rules; the file store takes no video. A video or reel is attached by its public `https` address |
| Webhooks | Access is unverified (§2); published state is polled |
| The personal-login credential | §3, §12 |
| Creating or editing an ad from a screen | The screens read, change a budget, pause and activate. Building a campaign is an API and MCP act, always `PAUSED`, and Ads Manager is one click away for the rest |

## 11. The pilot: half a day, before the first client depends on it

1. Create the app in the agency's portfolio with the Page, Instagram and "Create & manage
   ads" use cases. Note whether the dashboard shows Development/Live or Unpublished/Published.
2. Create one admin system user, install the app, generate a token with every scope in §2.
   Read `debug_token`: `scopes`, `expires_at`, `data_access_expires_at`.
3. **On a client asset shared as partner, not on the agency's own**: publish a Page post and
   open it logged out. If invisible, switch the app Live and repeat.
4. Publish one Instagram image from a public URL on a partner-shared account.
5. Create a `PAUSED` campaign, ad set and ad; then activate it at the minimum budget for an
   hour and confirm delivery in Ads Manager.
6. Schedule a Page post natively, edit it, delete it — settling the DELETE conflict in case
   §12's first decision goes the other way.
7. Subscribe a Page webhook and record whether it is delivered.
8. Generate a **60-day** token, let the nightly job renew it (or press *Nu verlengen*), and
   read `debug_token` again: a new `expires_at`, the old token still valid.
9. Put the instance behind its usual gateway and publish one Instagram image: Meta's fetcher
   must reach `/api/v1/meta-business/media/…` without a session (docs/DEPLOY.md).
10. Record every error body verbatim and put them in `tests/meta_fake.py`. They become the
   fixtures (§10 of CLAUDE.md: a fixture written in the shape you expect cannot find the fault
   in the shape you receive).

If item 3 or 5 fails on partner-shared assets and succeeds on owned ones, the verification-free
route covers only what the agency itself owns, and the feature as asked needs Business
Verification and App Review after all.

## 12. Decisions taken

| Question | Decision |
|---|---|
| Own scheduler or Meta's for Facebook? | **A setting**, per tenant, defaulting to schakl's own (§5) |
| The Tech Provider risk (§2) | **Accepted**, knowingly. Verification of the agency's own app stays available later as insurance and changes no code |
| The personal-login credential | **Not built.** Clients share with the portfolio (§3) |
| One package or two | **Two**: `meta` and `meta_ads` |
| May an agent activate ads? | **Yes**, behind `meta_ads.ads.activate`, which is on no default role but admin and on no key unless somebody ticks it |
| Client approval in the portal | Later (§10) |

## 13. The screens, and the rules that came out of using them

Found in a browser against the fake, and each generalises past Meta.

- **Autosave goes to the API, never through the form.** A form action that succeeds hands focus
  back to the page, so an autosave built on `requestSubmit` took the caret out of the box a
  second and a half after every pause in typing. The buttons still post the form; the autosave
  is a `fetch` (the meeting page's shape).
- **A blank draft is a list of things to do, not a list of errors.** What an empty post still
  needs is drawn without alarm; a red line starts once there is something on the page for it
  to be about.
- **A published post is read, not a form of disabled boxes.** The page has one layout for
  writing and one for the record.
- **The trail holds the decisions and not the typing.** A draft's edits are not recorded — it
  saves itself after every pause — while a change to a planned post is, and every move between
  statuses is a named line. What became of the post is the system's line, written when it
  became that.
- **The page follows a post around its planned time**: from half a minute before until a
  quarter of an hour after, and never outside it.
- **Linking saves typing and decides nothing.** A channel called *Nova Fietsen* opens with the
  client of that name filled in, marked as a suggestion, and a Page offers its own Instagram
  account in the same step.
- **A secret that was just stored does not stay in the field it was typed into.**
- **A picture that broke before the page woke up is still broken** (`Avatar`): the `error`
  event of a server-rendered image fires before hydration attaches a handler, and a remote
  avatar whose signed address has expired is the ordinary case.
