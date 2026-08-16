# Google Tag Manager

The `google_tag_manager` integration: a client's container as a first-class surface, and as MCP
tools. Business-licensed (`sku="google_tag_manager"`).

Half of an agency's marketing work is *making the measuring happen* — a conversion on a new form,
an event on a quote request, a tag for the campaign that starts on Monday — and until now the
whole of it happened in a browser tab schakl knew nothing about. Which container is this client's,
what is live in it, who put that tag there and when, and whether the change somebody staged three
weeks ago was ever published: none of that had an answer here.

## 0. The checklist for the day a live credential arrives

**Nothing in this integration has been exercised against a live Tag Manager account.** It is
written from the API's own **discovery document**
(`https://tagmanager.googleapis.com/$discovery/rest?version=v2`, revision 20260812) rather than
from memory — CLAUDE.md §11 bans the second, not the first — and driven end to end in tests through
a stateful fake installed at the transport (`apps/api/tests/gtm_fake.py`), so the paging loop, the
fingerprint handling, the path builder and the error classifier are all real code under test.

What a document cannot settle is the **tag templates' parameter vocabulary**, because it is not in
the API: GTM validates parameter keys against the template and answers `400` naming the field. So
the failures below are loud rather than silent — but they are still an hour of somebody's day, and
this list is what to walk the first time a real grant exists. `GTM-NPGFR9W9` (account `6371679663`,
container `261371074`) is the test container to walk it against.

1. **The consent screen actually offers the four scopes.** The Cloud project must have the **Tag
   Manager API** enabled, and the OAuth client must be allowed to ask for `tagmanager.readonly`,
   `tagmanager.edit.containers`, `tagmanager.edit.containerversions` and `tagmanager.publish`.
   Press *Google koppelen* from Instellingen → Tag Manager and check the granted scope list on the
   connection afterwards — Google returns what it granted, not what was asked for.
2. **`accounts/containers:lookup?tagId=GTM-…` resolves.** This is the only call that turns what is
   on a client's website into the numeric pair the rest of the API needs.
3. **A GA4 event tag is accepted with `measurementIdOverride`.** The single most likely place to be
   wrong. GTM answers `vendorTemplate.parameter.measurementIdOverride: The value must not be empty`
   for the wrong key, so a refusal here means `recipes.build_ga4_event_tag` needs the other spelling.
4. **A Google Ads conversion tag is accepted with `conversionId` / `conversionLabel` /
   `enableConversionLinker`.** Same shape of risk, one template over.
5. **A `formSubmission` trigger with `checkValidation` **and** a filter is accepted**, and the same
   trigger without a filter is accepted with `checkValidation: false`. The rule encoded in
   `build_trigger` is that the first is legal and *Check Validation* on an unfiltered trigger is
   not; if GTM disagrees, it is one branch.
6. **`elementVisibility` takes `selector` / `visiblePercentageMin` as top-level typed singletons
   and `selectorType` / `firingFrequency` inside `parameter`.** The least certain of the six trigger
   kinds, because it is the one with the most options.
7. **`built_in_variables?type=clickElement` on a workspace that already has it.** The code swallows
   the refusal; confirm it *is* a refusal and not a 500.
8. **`workspaces/{id}:create_version` on an empty workspace answers 200 with no `containerVersion`.**
   The whole `empty=true` path depends on it.
9. **`versions:live` on a container that has never been published answers 404**, not 200 with an
   empty body. A container in that state must stay `status="active"`.
10. **A `PUT` with a stale `fingerprint` answers 409.** That is the entire concurrency story.

## 1. Why an integration, and why not `marketing`

§6a's test: cancel the vendor and is the thing *gone*, or merely poorer? Cancel Tag Manager and
there is nothing left here at all — every row is a pointer into somebody else's state.

`MarketingSource` still, correctly, does not list GTM, and that decision was about *metrics*: a
container has no marketeer-facing numbers of its own, and the conversions it fires arrive through
GA4 already. This module is not a source; it is the surface that **creates** the thing GA4 later
reports on. It sits under Marketing in the menu beside Google Ads — the dashboard says how the
client is doing, Ads says what the advertising is doing, this says what is measuring any of it.

It `requires` only `google`, not `marketing`: the credential is a `google_connections` row and
there is no second way to get one, while an agency who wants their clients' tagging under control
and no traffic dashboard must not be made to switch on a module they did not ask for.

## 2. The credential

There is no credential of its own. Tag Manager rides the **per-user Google grant** the `google`
integration already owns (`docs/GOOGLE.md` §2) — no developer token, no API key, nothing on a
settings screen to paste.

Four scopes, asked for together by `/api/v1/google/oauth/connect?include_tag_manager=true`, and
deliberately **not** folded into `include_marketing`: the dashboard's three sources are read-only
measurement, and asking for the ability to publish to a client's website on the consent screen of
somebody who only wanted a traffic chart is how an agency learns to click through consent screens
without reading them.

| scope | what it buys |
|---|---|
| `tagmanager.readonly` | list and read containers, workspaces, tags, triggers, variables, versions |
| `tagmanager.edit.containers` | change a workspace — and Google accepts it as a read scope too |
| `tagmanager.edit.containerversions` | freeze a workspace into a version |
| `tagmanager.publish` | make a version live on the client's website |

Not requested: `delete.containers`, `manage.users`, `manage.accounts`. A scope nobody's code path
needs is a scope on the consent screen frightening an agency for nothing.

**The scope is checked before the call, not after.** Google's own refusal says *permission denied*,
and what actually happened is that this connection was minted before the org asked for the GTM
scopes. One of those sends somebody to the client's Tag Manager permissions; the other is one
reconnect. `GtmWriteService._require_scope` answers the second.

## 3. What is stored, and what is read live

The observed-vs-decided rule (CLAUDE.md §10) applies harder here than anywhere else in the tree: a
container is edited by us, by the client's own marketeer and by whoever set it up in 2019, all in
the same week.

`gtm_containers` mirrors **only what a panel needs**: the name, the live version, the counts off
that version, and how many changes are staged and unpublished. That is what makes the company hub
render without waiting for Google (#364). Everything else — the tag list, the trigger list, the
version history, the workspace status — is a **live** call on the container's own page, where
waiting is the point rather than a surprise. A tag list that is a mirror answers the wrong
question, because half the edits to it are made in the Tag Manager interface by people who do not
work here.

What schakl *decides* is `company_id`, `website_id` and `active`. Unlinking deactivates and
**touches nothing at Google**: an agency that stops working for a client does not thereby delete
the tracking off their website.

`gtm_conversions` is the third kind of fact, and the one that has nowhere else to live: **what we
created in somebody else's container**. Google records that a trigger and a tag exist; it records
nowhere that together they are *"offerte aangevraagd"*, that an agency promised to keep it working,
or that it was set up from here rather than by hand. Without the row, the next person to look has
to read the container and guess — the same argument `google_ads_decisions` makes about a decision
not to act. It carries both halves for the reason `cloudflare_zones` does: `config` is what was
asked for, `tag_id`/`trigger_id`/`status` are what was last observed, so a conversion whose tag
somebody deleted in Tag Manager is an expressible state rather than a row that silently claims to
be working.

## 4. Workspaces, and the trap in them

A GTM workspace is a **shared draft**, not a personal branch. Writing into "Default Workspace" puts
our half-finished change in front of whoever else is mid-edit — and *their* next Publish ships it.

So `gtm_settings.own_workspace` is on by default and `workspace_name` is tenant-visible (the
client sees it in Tag Manager, so an agency wants their own name on it, not ours).
`resolve_workspace_path` answers "which workspace" once, for reads and writes alike, and the
`create` flag is what separates them: a read must never bring a workspace into existence as a side
effect of somebody opening a screen.

## 5. The recipe, and the escape hatch

A tag is a `type` string plus an array of key/value `Parameter` objects whose legal keys are
decided by the tag *template*, and nothing in the API document says what they are. A model — or a
person — composing that from first principles gets it wrong, and the interesting half of getting it
wrong is silent: a tag that fires into nothing looks exactly like a tag that works.

`recipes.py` therefore states two tag kinds and six trigger kinds, chosen because they are what an
agency sets up over and over and because their vocabulary is short enough to state honestly:

- `ga4_event` → a `gaawe` tag with `eventName` and **`measurementIdOverride`**;
- `ads_conversion` → an `awct` tag with `conversionId`, `conversionLabel` and
  `enableConversionLinker` (always on: without it the tag reports conversions it cannot attribute
  to a click, which is a number that looks right and is not);
- triggers: `page_view`, `form_submit`, `link_click`, `element_click`, `element_visibility`,
  `custom_event`, each narrowable with `url_contains`.

**It never guesses a value.** A measurement id, a conversion id and a CSS selector all come from
the caller. There is no "we'll find the GA4 property" step, because picking the wrong one sends a
client's conversions to somebody else's property and nothing on any screen would say so.

Everything the recipe does not cover goes through `POST …/tags` and `POST …/variables`, which take
GTM's own `type` and parameter array and are judged by **GTM's own validator**. That is a better
answer than a half-modelled recipe: a hand-written body fails loudly where a wrong recipe would
deploy quietly. It is also the surface an MCP agent uses most.

Two smaller rules the recipe encodes because their failure is invisible:

- **A trigger's built-in variables are switched on with it.** GTM happily stores a trigger whose
  `{{Click Element}}` resolves to nothing, and the tag then never fires with no error to read.
- **`{{_event}}`, not `{{Event}}`.** The first needs no variable enabled; the second is one.

## 6. Permissions, and the line that matters

Four keys (`apps/api/app/integrations/google_tag_manager/permissions.py`), and the line is between
the third and the fourth.

| key | what it opens |
|---|---|
| `google_tag_manager.settings.manage` | link/unlink a container, verify one, the kill switch |
| `google_tag_manager.container.read` | containers, workspaces, tags, triggers, variables, versions, the snippet, the conversions (`admin` + `member`) |
| `google_tag_manager.tag.write` | change a workspace, and freeze it into a version |
| `google_tag_manager.version.publish` | make a version live on the client's website |

Editing a workspace changes a **draft**: real, recorded, served to nobody. Publishing changes what
runs in every visitor's browser, immediately, with no review step behind it. That is the split an
agency wants to hand out separately, and precisely the one a single `google_tag_manager.write`
would destroy: with it, *"let the assistant prepare the tracking for the new campaign and I will
look it over"* is an API key holding `tag.write` and nothing else.

Version *creation* rides `tag.write` rather than earning a fifth key — a version is the act of
writing down what was staged, and gating it behind the publish permission would leave the staging
half unable to finish its own work.

Beside the permissions sits **`gtm_settings.writes_enabled`**, the instance-wide kill switch. The
permission decides *who*; this decides *whether*, in one place an owner can reach in a hurry after
watching something surprising appear on a client's website. It defaults to `true` for
`google_ads`' reason: the write permissions are already admin-only and deny-by-default, so a switch
defaulting off is a second lock on a door nobody can open anyway.

None of the four is ever granted to the seeded `client` role (#266). The read alone covers every
tag on the container, which includes the conversion values, the remarketing ids and whatever the
previous agency left behind.

## 7. The MCP surface

Every `/api/v1` operation is a tool (CLAUDE.md §12), so the route list *is* the tool list — which
is why the handlers are named for what an agent would ask for (`create_gtm_tag`, `list_gtm_tags`,
`create_gtm_conversion`) and why the router prefix is `/gtm` rather than `/google-tag-manager`: the
prefix's last segment is the section URL an agent is pointed at (`/mcp/gtm`), and that URL gets
pasted into somebody else's settings screen. It is also a member of the `growth` bundle
(`/mcp/growth`), beside `google_ads`, `marketing` and `reporting`.

`workspace_id` is an optional query parameter on nearly every route and almost nobody passes it:
absent means "the workspace schakl writes in", so neither a human nor a model has to learn what a
GTM workspace is before it can list a tag.

## 8. The failure model

GTM answers like the rest of Google and unlike Google Ads: an ordinary
`{"error": {"code", "status", "message", "details": [{"reason"}]}}` body. What `errors.py` adds is
the classification, because the status code alone is not the diagnosis.

| what happened | what it is | what fixes it |
|---|---|---|
| `403` + `SERVICE_DISABLED` | the Tag Manager API is off in the Cloud project | an operator, in Google Cloud |
| `403` + `ACCESS_TOKEN_SCOPE_INSUFFICIENT` | the grant predates the GTM consent | one reconnect |
| `403` + `rateLimitExceeded` | a rate, answered as 403 by Google's older APIs | waiting |
| `403` otherwise | this Google account has no access to that container | the client's GTM admin |
| `409` | a **fingerprint mismatch** — somebody edited it in Tag Manager | open it again |
| `400` | GTM refused the payload; its message names the parameter | the caller |

A retry is safe for a read and **never** for a write: `tags.create` is not idempotent, and a
retried create is a second tag firing a second time on somebody's website. Only `GET` is retried.

Two rules are the same as everywhere else. The envelope carries an **i18n key**, never Google's
text (§9); Google's own sentence goes on the container row's `last_error` where an admin reads it.
And nothing that leaves the process carries a credential — `scrub` is imported from
`app.core.googleads.errors` rather than copied: it is core, it is not Ads-specific (it redacts
refresh tokens, access tokens, client secrets and client ids by *shape*), and a second copy is a
second thing nobody updates when Google mints a new credential format.

## 9. The nightly observation

`gtm_sync_all` runs at **05:35**, after `marketing` (04:45) and `google_ads` (05:15) — all three
walk every org making outbound Google calls, and stacking them on one minute is how a box with
thirty clients meets its own rate limits at four in the morning.

It refreshes what the row mirrors, per container, swallowing each container's own failure onto its
own row so one unreachable container costs one red line rather than the other nineteen containers'
refresh. `verify` and the cron call the **same** `observe`, deliberately: two code paths asking
Google the same question is how a screen and a cron come to disagree about whether a client's
container is healthy.

The one it exists for is `workspace_changes`. A change staged in a workspace weeks ago that nobody
published is the commonest way a client's tracking quietly stops being what they were told it is,
and nothing else surfaces it — nobody looks at a container they have no reason to open.
