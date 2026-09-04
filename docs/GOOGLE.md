# Google Workspace integration — design notes (P3)

> Calendar, Drive, and Gmail look like one "Google integration" but they are **two token
> systems and three different data problems**. Getting the boundaries right up front is what
> keeps P3 (§10) from becoming a sync swamp. Read this before building any Google surface.
> Status: **shipped** (issue #22) — one licensed registry module `google` (sku `"google"`)
> holding the core plus `calendar/`, `drive/` and `gmail/` subpackages, with the touchpoint
> timeline in the free `interactions` module (contactmomenten). Deviations from the letter of
> this doc, decided during the build: one registry module instead of four (one license, one
> enablement, boundaries kept as subpackages); OAuth client credentials live per-org in the DB
> (Instellingen → Google, the #76 SSO pattern) with the env vars as fallback; our own
> browse-and-pick UI instead of the Google Picker; matched emails land **pending** and only
> the mailbox owner may approve/reject/remap (body fetched after approval).

## The one rule

**Login is not API access.** OIDC "Sign in with Google" (authentication) and Workspace API
access (Calendar/Drive/Gmail authorization) are separate grants with separate lifetimes.
Never make the login token carry API scopes. Build them as two flows that a nice UX *links*,
not one flow that does both.

## 1. Login ≠ API access

CLAUDE.md §3 already lists these separately ("Authlib OIDC relying-party" vs "Google OAuth
for Workspace scopes"). Keep them separate in code too:

| | OIDC login (authentication) | Workspace API access (authorization) |
|---|---|---|
| Scopes | `openid email profile` | `calendar.events`, `drive`/`drive.readonly`, `gmail.readonly`, … |
| Token | short-lived ID token, used at login | **refresh token stored server-side**, used indefinitely |
| Level | org-enforced (§P0: enabling OIDC disables local login) | per-user *or* domain-wide |
| Consent | "Sign in with Google" | "Connect your Google account" — separate, incremental |

Asking for Gmail/Drive scopes on the login screen is alarming UX and technically wrong: API
access needs `access_type=offline` + a stored refresh token, which login doesn't produce.
Even when a tenant enforces Google OIDC login, "connect Google for Calendar/Drive/Gmail" is a
distinct step.

**The bridge:** Google supports **incremental authorization**. A user who logged in via
Google can be walked up to Workspace scopes with a one-click "connect" that adds scopes to the
existing grant and returns the refresh token. It *feels* unified while staying two grants.

## 2. Self-hosting is a gift — lean into it

Each agency **self-hosts and registers its own Google Cloud project + OAuth client**, marked
**"Internal"** on the consent screen (same Workspace domain). That **skips Google's
verification and CASA security assessment** for restricted scopes (Gmail read, full Drive) —
the assessment that normally makes those scopes impractical for a SaaS. Our deployment model
(§5, "build multi-tenant, deploy single-tenant") sidesteps it entirely.

Consequences:
- Each install supplies its own OAuth client credentials in config
  (`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` per deployment) — a per-install secret, never
  hardcoded, never shared across installs.
- One Workspace domain per install makes **domain-wide delegation** (a service account
  impersonating any user in the domain) a legitimate option, not just per-user OAuth.

**Recommendation:** default to **per-user OAuth** (less privileged, standard, works even for a
personal Gmail), and support **domain-wide delegation as a tenant setting** for agencies that
want zero-friction, survives-forever central access for sync/automation. Build the abstraction
so callers ask *"give me a Google client acting as user X"* and the integration layer resolves
it — DWD if configured, else that user's stored token. Never let a caller reach for a raw
token.

## 3. Architecture: one `google` core + three surface modules

Follows the module pattern (§6).

- **`google` (core integration)** — owns the OAuth flows, the **encrypted token vault**, DWD
  config, and the client factory. `google_connections(org_id, user_id, google_sub, email,
  scopes[], refresh_token_enc, access_token_enc, expiry, ...)`. **Encrypt refresh tokens at
  rest** (envelope encryption). Handles revocation, re-consent, and incremental scope upgrades.
- **`google.calendar`, `google.drive`, `google.gmail`** — each contributes, via the registry:
  its scopes to the consent, its ARQ cron jobs (watch renewal, polling), its company/project
  panels, and its webhook routes.

Keep `org_id` on every table even though we deploy single-tenant (§5). Webhooks map the
incoming channel/notification back to org + connection via our own channel token.

## 4. Calendar

- Use **push notifications (`watch` channels) + `syncToken`** for incremental sync, not
  polling — pull only deltas (matches `docs/PERFORMANCE.md`).
- `calendar_event_links(org_id, local_type, local_id, google_event_id, calendar_id, etag)`.
- Watch channels expire (~weeks) → renew with an **ARQ cron job** (§6 `cron_jobs`).
- Start **one-way** where it's cheap: §14 already wants approved leave → Google Calendar.
  Two-way is much harder — don't sign up for it in v1.

**Shared calendars: a channel row *is* the selection (#440).** Agencies live in shared
calendars, so `google_calendar_channels` widened from one-per-connection to one per
`(connection, calendar)` — each row its own sync cursor, and the cached events carry
`calendar_id` in their identity, because an invitation legitimately exists on two calendars
under one Google event id. The primary's row is created on first sync, which is what keeps the
default (primary only) byte-identical for everyone who never touches the setting. The viewer
picks on their own account page (`GET/PUT /google/calendar/calendars` — the live `calendarList`,
briefly cached, validates every id, and the existing `calendar.events` scope already covers
reading a shared calendar, so no re-consent); deselecting removes the calendar's cached events
on the spot. The Agenda's feeds menu draws one colour/hide row per synced calendar through the
per-person split machinery (#281) — the "person" is a calendar — fed by
`GET /google/calendar/channels`, which reads the database alone. Watches stay **primary-only**;
a shared calendar's channel rides the 15-minute poll, whose staleness check now looks at every
channel. Push (schakl → Google) stays primary-only too — mirroring *into* shared calendars is a
separate decision nobody has made.

**The Agenda drops the mirror of anything it already draws natively, and the identity it drops it
by has to survive the round trip.** A leave request, a freelance availability row and a planned
task block all mirror outwards *and* come back through the events cache, so `events_feed` filters
that cache against the outbox: an event whose id sits in `calendar_event_links` is the same item
twice. **A row that repeats breaks that in the one way an id comparison cannot see.** A repeating
availability mirrors as a *single* event carrying an RRULE — which is what keeps an edit an edit
and a delete a delete, instead of a diff against whatever horizon was last placed — while the sync
expands recurrences (`singleEvents=true`), so what comes back is a series of *instances*, each
under an id of its own that the outbox has never held. Every occurrence of a freelancer's weekly
availability was drawn twice, natively and as its own mirror. An instance names its master in
`recurringEventId` and nowhere else, so the cache stores it
(`google_calendar_events.recurring_event_id`, migration `c5d81b3f7a26`, which clears every
`sync_token` so the next pull refills the parentage it could not have known) and the feed tests
both identities: the event's own id, or the series it belongs to. Worth generalising — **when you
mirror a rule rather than its occurrences, what comes back is not the thing you sent**, and any
dedup keyed on what you sent passes every test written against a one-off.

**A database cascade announces nothing, so the mirror has to be told.** The outbox learns that a
local record is gone from one place only — the emit at the removal site — and a link is the *only*
record that a Google event exists at all: once its `local_id` names a row nobody will write again,
the event is unreachable. `task_schedules.task_id` is `ON DELETE CASCADE`, so deleting a task took
its planned blocks with it in the database and told the mirror nothing, leaving the block in
someone's calendar for good. Three rules come out of fixing it. **Whatever leaves by cascade emits
first** (`TaskScheduleService.remove_for_task`, called by `TaskService.delete`) — deliberately
unscoped, because the caller was already allowed to delete the card and a colleague's block on it
is not a second permission to ask for. **A "may we write?" guard may never gate a "must this be
removed?" decision**: the push handler's org-sync and per-person-connection checks ran *before* the
reassignment tombstone, so handing a block to a colleague who never connected Google left it on the
original person's calendar — what is already in Google is now settled first, and the guards only
decide whether a *new* event is written. And the sweep cron carries an **orphan pass** as the safety
net for both, flipping a pushed `task_schedule` link whose block no longer exists to
`delete_pending`; it is what finishes the events already stranded, and what catches the next write
path that forgets. Scoped to task schedules on purpose — a leave request is cancelled, never
hard-deleted, so an unmatched `local_id` there is not evidence of anything.

**A mirrored task block is titled by whose work it is.** Every pushed block read *"Taak: …"*,
which says what kind of record it is — a calendar full of them already says that — and not the
one thing a glance at a week needs. The event is *"«client»: «taak»"* now (`push._task_summary`),
falling back to the old marker only for a task with no client; the client's **label**
(`companies.name`, never `legal_name` — a calendar is a list, not a document) travels in the
`task_schedule.saved` payload, because the mirror never re-reads a task. `d4a9b3c6f2e7` retitles
what is already in people's calendars: per org with the RLS GUC bound, rewriting the snapshot and
flipping a `pushed` link back to `pending` with its attempts reset, so `push_link` updates the
existing event in place on the next outbox sweep — never a second event, and never a tombstone
resurrected.

## 5. Drive — use it directly; do NOT put object storage in front

**Reference/link model, no sync, no mirror.** The Shared Drive is already the source of truth.
Mirroring to object storage is a permanent two-way-sync bug factory (renames, moves, conflict
resolution, permission replication, duplicate storage) for no benefit in an internal tool
where everyone already has Drive access. We have no object store in the stack today (§3) —
don't add one for this.

Instead:
- `drive_links(org_id, entity_type, entity_id, drive_file_id, drive_url, name, mime, is_folder,
  is_root, shared_drive_id)`.

**A record's folder is a decision, and it is stored** (`is_root`, one row per record by partial
unique index). It used to be "the first folder link the query returned", which was fine while
provisioning was the only way one appeared and became a coin flip the moment somebody linked a
subfolder as an attachment — and, worse, meant there was nothing for a permission to guard. Three
consequences follow, and they are the point of the picker (Klant → Drive → **Map kiezen**, the
same browser in pick mode, which also offers the folder you are *standing in*: the folder you
want is usually the one you just navigated into, not one visible in the listing):

- **An agency's client folders already exist.** Provisioning creates one *named after the client*
  under the configured parent, which is the wrong answer for every agency whose Drive predates
  schakl — and re-typing the name to make the name-match fire is a guess, not a choice. Pointing
  at the real folder is the ordinary case, not the exception.
- **Giving a record its first folder is `google.drive.write`; re-pointing or detaching one is
  `google.drive.manage`** (admin by default). The two are different acts: the first is additive,
  the second silently moves where every colleague's uploads land and where project folders nest,
  while the history stays behind in a folder nobody is looking at any more. The route declares
  the base key so deny-by-default stays enumerable and the service refines on the row (CLAUDE.md
  §15's two layers) — which is also why `DELETE /links/{id}` asks for `manage` when the link it
  names is the record's folder: that is the same act reached from the other side. Adding the
  permission without closing that door would have been theatre.
- **Provisioning never overwrites a choice.** `POST /provision` 409s on a record that already has
  a folder, and the worker flags `is_root` only when nothing else claimed it while the job sat in
  the outbox. Each change is recorded on the *record's* activity trail (`drive.folder_set` /
  `folder_changed` / `folder_cleared`), because "whose documents now live where" is exactly the
  kind of fact §16 exists for.

**Every record a folder can hang off must have a way to get one, and an upload is not a way**
(#328). `DRIVE_ENTITY_TYPES` has always included `task` and the model was ready for it — the
unique index, `_ENTITY_TABLES`, `DriveLinkCreate` — but none of the three routes to a folder
accepted one: `POST /provision` 422'd, the picker was rendered only by the client panel, and the
entity panel gated its two buttons on `entityType === "project"`. So a task's `rootFolderId`
always fell back to the project's folder, and a file uploaded from a task's panel landed among
everything that project had ever produced. Two halves, and both generalise.

- **A parent is a record, not a folder id, and the chain is walked at execution time.**
  `drive_folder_jobs.parent_entity_type` says what `parent_entity_id` names (`NULL` reads as
  `company`, which is what every pre-#328 row means and what an older replica keeps writing
  mid-rollout). `_parent_folder_id` then walks task → project → client at *worker* time rather
  than emit time, because the parent may acquire a folder while the job sits in the outbox — and
  a project that never got one is not a dead end, it is the client's folder, exactly where the
  panel already sends the browser. Auto-provisioning still covers only companies and projects:
  tasks are numerous and short-lived, so a task's folder is always somebody pressing the button.
- **An upload attaches to the record it was uploaded from.** `DriveBrowser` minted a resumable
  session, PUT the bytes, and refreshed the listing — it never wrote a `DriveLink`, though it
  already held `(entity_type, entity_id)` and passed them to `?/linkDriveFile` twelve lines
  below. **The listing is live and the link list is what survives the page**, so the link is
  unconditional: "it landed in this record's own folder" and "this record has this file" are
  different facts, and only the second one is still true tomorrow. It was never task-specific —
  a company or project upload was equally unattached, just less visibly so.
  A refusal here reports *the link* failing (`errors.google_drive_link_failed`), never the
  upload: the bytes are in Drive by then, and saying otherwise sends someone hunting for a
  problem that does not exist.

Every Drive surface is **entity-addressed** — `(entity_type, entity_id)` comes from the caller —
so each goes through `entity_visible` (CLAUDE.md §15's failure mode (4)). Holding
`google.drive.read` is not the same as being allowed to see *that* client's folder, whose name is
usually the client's own name.
- Render an embedded file browser at view time via the Drive API scoped to the folder / shared
  drive. Cache listings briefly in Redis for snappiness, but **Drive stays authoritative**.
- **Automation via the event bus (§6):** subscribe to `company.created` → create the client's
  Shared Drive folder from a template → store the link. Same for projects.

**Scope tradeoff:** browsing *existing* client folders needs `drive.readonly` or `drive`
(the narrow `drive.file` only sees files the app itself created). That's a restricted scope —
fine under the "Internal" OAuth app above, but note it.

### Unlink and delete are two acts, and a browser you can only put things into

The panel could create in three ways — upload bytes, make a subfolder, link an existing file —
and remove in none (#394). `DELETE /links/{id}` was the only ⋯ item, and its dialog says in as
many words that the Drive file stays. That was the right decision *for a link*, and what it left
was a file uploaded by mistake from a task panel sitting in a client's Drive, attached to the
record, removable only by leaving schakl for drive.google.com — after which the panel still
listed a link to a file that no longer existed.

So there are two items now, and collapsing them would be wrong in **both** directions: a
colleague tidying a task's attachments would silently bin a client's document, and a colleague
binning a bad upload would be told it is still there. Each dialog therefore states where the
file ends up, which is the only fact separating them.

- **Ontkoppelen** — `DELETE /drive/links/{link_id}`. Unchanged, wording included.
- **Verwijderen uit Drive** — `DELETE /drive/files/{drive_file_id}`. Five rules hold it up:
  - **Trash, never purge.** Drive distinguishes `files.update {trashed: true}` from
    `files.delete`; the bin is recoverable by the document's owner for thirty days, which is the
    right default for a destructive act performed on somebody else's document from a CRM.
    Permanent delete is not offered at all.
  - **It runs as the viewing user**, exactly like `browse`. Drive's permissions are the whole
    safety property and it costs nothing — the transport already worked this way. A colleague
    who cannot delete that file in Drive gets Google's own refusal, and schakl neither grants
    around it nor explains it away. That refusal has its own key
    (`errors.google_drive_delete_forbidden`): *may not open this folder* and *may not delete
    this file* have different cures and different people who grant them, so `_call` takes the
    forbidden key rather than answering every 403 with the read-flavoured sentence.
  - **Every `drive_links` row naming the file goes with it, org-wide, in one transaction** — the
    record it was deleted from and every other record that linked it. A link to a trashed file
    renders a name and 404s when clicked. Each affected record gets a `drive.file_trashed` line
    on its trail (§16); a link that was the record's *folder* gets `drive.folder_cleared` too.
  - **A folder is refused unless empty**, checked before anything is written. The panel lists a
    client's project folder beside an accidental upload, and a delete that silently took the
    folder and everything in it would be the worst control on the screen.
  - **Binning a record's own folder asks for `google.drive.manage`**, not merely
    `google.drive.write`. Detaching one already does (see above) and binning it is strictly the
    larger act, so it cannot ask for less. The check reads the links *before* the round-trip:
    asking after the file is in the bin is not a check.

The panel and the `DriveBrowser` carry the same ⋯ item, because the browser is where uploads
happen and therefore where upload mistakes are noticed. The browser's refusal renders as a strip
**above** the list rather than instead of it — the list is what names the file that stayed. And
the two lists sit on one screen, so **binning a file from one has to redraw the other**: the
browser's listing is live and belongs to no `load`, so the `invalidateAll` a form action performs
cannot reach it, and the panel would have gone on showing a file that had left Drive — the exact
fault this issue is about, one list over. `DriveBrowser` therefore takes a `reloadToken` the host
bumps (`ConfirmDialog`'s new `onsuccess`), rather than reloading on every page invalidation,
which would cost a Google round trip per unrelated save.

**Not exercised against a live Google credential.** The API paths are covered by the suite, and
both screens were driven in a browser against an in-memory fake transport (menu, both dialogs,
the trail line, the two refusals, and the cross-list redraw). Whoever first runs this with a real
account should check, in order:

1. A file uploaded from a task panel, then binned from the same panel, disappears from Drive's
   folder listing and appears in **drive.google.com → Prullenbak**, restorable.
2. A colleague with view-only access to a shared drive gets
   `errors.google_drive_delete_forbidden` and not a 500 or a generic "Drive is niet beschikbaar".
3. A file linked to two records disappears from both panels in one press.
4. A non-empty folder is refused and stays where it is; an empty one goes.
5. A shared-drive file behaves as a My Drive one does — `supportsAllDrives=true` rides every
   call, but only a real shared drive proves it.

### "Drive is enabled and I am connected, and nothing gets a folder"

`can_provision` on `GET /google/drive/state` is four facts ANDed: Drive enabled, a settings row,
an **automation connection** (`automation_connection_user_id`), a **root** (`drive_parent_folder_id`
or `drive_shared_drive_id`) and the caller's `google.drive.write`. `viewer_connected` beside it
is a different question — whether *this person's* Google account is linked — and it is the one
an admin sees answered `true` and reads as "so provisioning should work". It does not: folders
are created by the automation account so that ownership does not follow whoever happened to
click, and with no automation account chosen (Instellingen → Google → *Automatiseringsaccount*)
every record stays folder-less, every upload from the Drive browser lands in the root, and a file
uploaded to a colleague's My Drive is a 404 for everyone else until it is shared. The fix is one
dropdown, not a code path: pick the automation account (a Shared Drive as root, already
configured on most installs, is what makes the result visible to the whole team), tick
*Automatisch mappen aanmaken*, and run *Provision all* once for the existing clients.

### A Drive 403 is three different problems, and the body says which

`scopes_for` only asks for `drive` when `drive_enabled` was **already on at consent time**, so
an org that connected Google for Calendar or the marketing sources and switched Drive on
afterwards holds connections that are `active`, refresh cleanly, and are refused by every Drive
call. That is one of three ordinary 403s, each fixed somewhere else:

| Google's `reason` | What is actually wrong | The fix |
|---|---|---|
| `ACCESS_TOKEN_SCOPE_INSUFFICIENT` | the token was minted without `drive` | reconnect (Instellingen → Account) |
| `SERVICE_DISABLED` / `accessNotConfigured` | the Drive API is off in the OAuth client's Cloud project | enable it there — §7's trap, one surface further |
| *(none)* | this account cannot see that folder or shared drive | share it in Drive |

They are indistinguishable in the status line, which is exactly what `raise_for_status()` at a
call site preserves: the picker showed "Drive is niet beschikbaar" and the API logged an httpx
traceback naming the URL. Every Drive round-trip now runs inside `DriveService._call`, which
reads the reason with `describe_api_error`, logs it verbatim next to `oauth_client_hint` (the
message names the Cloud **project number** — decisive when an org is silently riding the
instance env client), and answers a 409 whose key states the fix. The scope case is refused
before the round-trip, from `connection.scopes`, and the provisioning worker skips rather than
burning five attempts on it. **An empty `scopes` list is not evidence** — it means we never
recorded the grant — so those are still left to Google to judge.

*When would object storage in front be right?* Only for offline access, full-text indexing of
file contents, or serving files to people without Drive accounts. None apply to an internal
agency tool — so, direct.

## 6. Gmail — trickiest, most privacy-sensitive

**Do not sync whole mailboxes.** Start with **matched, metadata-first logging**:

- Only link emails whose participants match a known `contact`; attach to the company/project
  timeline.
- **A known contact is not the question; a known *outsider* is** (#324). The gate read *"external
  mail still needs a known contact"* and asked whether anything had matched at all — and an
  agency's own staff are contacts, on the agency's own company, which is the setup `_internals`
  derives that company from. So a newsletter to one colleague matched the colleague, opened the
  gate, and became a pending contactmoment on the agency's own record with a notification behind
  it. On any instance set up the way this code assumes, the filter was effectively off: every
  supplier invoice, cold email, GitHub notification and password reset in the mailbox arrived in
  somebody's review queue to be rejected by hand. #305 had already moved these rows to a better
  *destination*; the sibling it left standing was that they should not be landing at all. The
  predicate is now named once (`matching.is_internal_match`) and read twice — by the gate
  (`has_external_match`) and by the ranking — because two copies of it is how they came to
  disagree. `gmail_log_internal` is the only door a message with nobody outside on it has, and
  opening it does not reopen the newsletter's.
- **"One of ours" is one set of addresses, asked in one place.** `Internals.ours` — every address
  that reaches a colleague, `users.email` plus the address each Google grant was made with —
  answers both *is this colleague-to-colleague chatter* (`internal_only`) and *is this contact row
  a colleague's* (`is_staff`). Two sets meant a mail to somebody's Workspace alias came out
  external on one question and internal on the other. `member_emails` stays narrower and stays
  behind the *company* derivation only: a colleague who connected a private Google account must
  never make whichever company that address is a contact of read as the agency's own.
- **And "one of ours" is core's question, asked whole.** A client login is a membership, so it
  lands in `member_emails` unless something takes it out — and `_internals` asked
  `portal_user_ids`, *"is this user contact-linked?"* That is one of the **two** ways to be an
  external login (§15, #274): a client invited from Instellingen → Gebruikers holds the seeded
  `client` role and no contact link, so they came back as staff. Every mail they wrote to a
  colleague was then colleague-to-colleague chatter and was dropped — no pending row, no
  notification, no log line, the failure mode this feed can least afford. And worse than the one
  address: `company_ids` is derived from `member_emails`, so *their own company* read as the
  agency's own, and behind #324's gate every other contact at that client went dark with them.
  `app.core.portal.external_user_ids` answers both halves in one place; the notification fan-out
  and the cloud domain-health recipients keep an inline copy only because each folds it into a
  statement it already runs.
- **A statement outranks an inference, and a client login is a statement.** Taking external
  logins out of `ours` fixed `is_staff` and left the *other* half of `is_internal_match`
  standing: "every company this contact is on is the agency's own". That rule is an inference
  from where somebody is filed, and it is a good one — it is what covers `administratie@` and
  the colleagues who hold no login at all. But an agency keeps more than colleagues on its own
  company: a freelancer, a subsidiary's contact person, the client whose record was made before
  their company was. Invite one of them to the portal and the two answers disagree — the
  platform issued them a **client** login (#274, the definition of not-a-colleague) while the
  company rule called them staff — and the gate believed the inference. Their whole
  correspondence was dropped as `no_external_match`, on an instance where the portal they were
  invited to was visibly working, and the review screen's only explanation was *"de afzender is
  nog geen contactpersoon buiten het bureau"* about somebody who plainly was. So
  `ContactMatch.is_client_login` short-circuits `is_internal_match`, read the two ways #274
  already needs — the contact's own `user_id` link (the portal invitation) and the address
  (a client invited straight from Instellingen → Gebruikers, who has no link to read). It
  reopens nothing #324 closed: a newsletter to a colleague still matches a colleague, and no
  colleague holds a client login.
- **What about the rows already in the queue?** Nothing retroactive ships, deliberately. The
  mis-ingested rows are indistinguishable from wanted ones by anything the server knows — a
  pending row filed on the agency's own company is also exactly what an opted-in internal mail
  looks like — so a purge would decide for the tenant, at scale, in a place where being wrong is
  invisible. Bulk reject (#299) is the answer: filter the queue to `status=pending` on the
  agency's own company, select, reject. Note that rejecting also suppresses the *thread* where
  asked to, which is right for a newsletter and not for a client conversation swept up with it.
- **The agency is not the client** (#305). An agency keeps itself in its own company list —
  that is where its own domains, hosting and invoices hang — with its staff and its
  `administratie@` address as contacts on it. Those records date from setup, so on any thread
  with a colleague in Cc they matched *first*, and the old "oldest link first" rule filed the
  mail under the agency rather than the customer who sent it: every row in the review queue
  arrived pre-filled with the wrong client, to be remapped by hand. `resolve_mappings` now
  **ranks** (`matching.py`): insiders last, then the `From` of an inbound mail and the `To` of
  an outbound one ahead of whoever was merely kept informed, then oldest link as the stable
  tie-break. "Insider" is *derived, never configured* — a staff address, or a contact whose
  companies are all companies that have a staff member as a contact, which is what identifies
  the agency's own record without a flag anyone has to remember to set. Ranked, never
  filtered: internal-only mail (`gmail_log_internal`) still maps to the agency's own company,
  because there is nothing else it could mean.
- **Whose email it is, is a header fact — never "whose poll ran first".** One email produces
  one row (the RFC-822 dedup), so when several colleagues hold a copy, the owner used to be
  decided by poll order. A shared `info@` mailbox Bcc'd on the agency's outgoing mail therefore
  claimed every one of them: the row named the wrong person, read as **inbound** (a Bcc'd copy
  is an ordinary INBOX message and carries no `SENT` label), and — a pending row being private
  to its owner with *no* admin escape (§15) — the colleague who actually wrote the mail could
  not see it anywhere. "It never arrived" and "it arrived in a queue you may not open" are the
  same screen. `matching.intended_owner` reads it off the headers instead: **outgoing** is the
  `From` when the sender is one of ours, **incoming** is the first `To` that is (then `Cc`),
  because header order is addressing order. `direction_of` takes the same fact, so a colleague's
  copy of our own mail is outbound whatever its labels say.
- **The owner's mailbox logs it; the others stand aside** (`_defer_to_owner_mailbox`). Deferring
  rather than re-stamping `owner_user_id` is what keeps the row coherent: `gmail_message_id` is
  only meaningful *inside* the mailbox it came from, so the owner's own copy is the only one
  whose deep link opens in their Gmail and whose body fetch uses their own grant. Three things
  are never deferred, and each is load-bearing: a copy carrying `SENT` (a mailbox does not give
  away its own outgoing mail, whatever the headers claim), a message naming no colleague at all
  (the shape of a Bcc-only copy — it cannot name an owner, so it must not pick one), and
  anything whose intended owner is not in `Internals.syncing_user_ids`. That last one is the
  difference between deferring and *dropping*: standing aside for a mailbox that is
  disconnected, opted out or missing the Gmail scope would lose the email outright, so in that
  case the copy we hold is the only one there will ever be and it logs where it landed.
- **Private to the mailbox is not private from the people it was sent to.** A pending row is
  the owner's and nobody else's (#172) — which, taken with the rule above, meant that a colleague
  in `To` or `Cc` had no queue entry, no notification and no way to approve an email that reached
  them, because their own copy was exactly the one that stood aside. So the ingest names, per
  pending row, every colleague whose address was on the message (`interaction_reviewers`, resolved
  through `Internals.owner_by_email`, so an external login is never one), the pending notification
  goes to all of them as **one** event, and every "is this pending row mine" question in the
  service asks the *review set* (`_mine_or_reviewing`) rather than the owner column: the queue
  (`?mine=true`), the single read, the thread desk, the fold, the approve / file / reject gates and
  the bulk loader alike. Whoever decides first decides for all: approval drops the reviewer links
  and `interaction.approved` retires the notification for every recipient (#170's resolver did
  that already — it was only ever handed one recipient); rejection deletes the row and its links
  go with it. Ownership never moves — the body fetch, the deep link and the suppression still use
  the mailbox the message is actually in — and a *logged* row stays its owner's alone, because the
  links are gone with the decision. The payload says so (`InteractionRead.reviewable`), and the
  web's review controls read that rather than `owner_user_id === me`.
- **The task link is a roster too**, for the same reason the contact link became one (#300): one
  email answers three tickets. `interaction_tasks` is the authority and `task_id` its lead — what
  derives the client, what the fill-in offer reads, and what `thread_mappings` now carries as the
  whole roster (`task_ids`) so a reply lands on the same three tasks.
- Store **metadata + a deep link** (`message-id`, `thread-id`, subject, snippet, participants,
  timestamp, `https://mail.google.com/mail/u/0/#all/<msgid>`) rather than full bodies by
  default. Pull the body on demand — lighter, faster, far less invasive.
- **The body arrives twice, and the difference is the point.** `body_text` is the `text/plain`
  part: what search reads and what the snippet is cut from. `body_markdown` is the `text/html`
  part converted by `app/core/htmlmd.py`, and it is written **only** when the message actually
  had one — because a received body is not our markdown, and rendering a plain-text mail as
  markdown would turn a sender's `*sterretjes*` into italics. Every surface renders
  `body_markdown` when it is set and `body_text` when it is not. The same conversion runs on
  an uploaded `.eml` (`interactions/eml.py`), so a synced message and an uploaded one read
  identically — the #262 rule, extended to formatting.
- **An inline image is content of that body, not an attachment of the message.** A signature
  logo is a part with a `Content-ID` the HTML points at; it is stored (`files.content_id`) and
  the body's `cid:` marker is rewritten to `file:<uuid>`, which the web resolves at render
  time. Two consequences: it renders *inside* the message, and it no longer appears as a chip
  on every mail that sender ever sent. A **remote** `<img src="https://…">` is dropped to its
  alt text at conversion — a tracking pixel is an image, and loading one tells the sender the
  agency opened the mail. This is also why the logos are affordable at all: identical bytes are
  stored once per org (`docs/STORAGE.md`).
- **Ingestion:** Gmail's real-time `watch` requires **Google Pub/Sub** (unlike Calendar) —
  extra infra. Start with **periodic ARQ polling using `historyId`** (incremental, cheap); add
  Pub/Sub push later only if latency demands it.
- **A five-minute cron is invisible, so the timeline states its own freshness** (#341,
  `gmail/refresh.py`). An email sent thirty seconds ago is simply not on the list yet, and
  nothing on the screen distinguished *not synced yet* from *not matched at all* — so the
  interactions page now prints when this mailbox was last polled and offers `POST
  /google/gmail/refresh` to poll it now. Four rules, and none of them is really about Gmail. It
  is the **caller's own** mailbox, resolved from `ctx.user` and never from a parameter: a grant
  is per-user, and "refresh everyone's mail" is one person spending colleagues' quota against
  consents they did not give (hence `google.connection.manage`, the same key as the rest of
  your own connection). The **rate limit is a row, not a Redis bucket** —
  `google_connections.gmail_manual_poll_at`, one manual poll per minute — because what is being
  protected is a per-user quota, the row is the thing that knows, and a ceiling in the database
  survives a Redis outage and holds across both API replicas; two clicks racing it are settled
  by `SELECT … FOR UPDATE`, not by application code (the `docs/PAYMENTS.md` rule, one layer
  down). It is **stamped before the poll and outside its savepoint**, so a mailbox that errors
  is reported *and* still spends its budget: otherwise the one grant most worth leaving alone is
  the one anybody can hold the button down on. That stamp is deliberately **not**
  `gmail_last_polled_at`, which answers "how fresh is this feed?", is written by the cron too,
  and — because the first poll of a new mailbox baselines and returns early without setting it —
  could not bound a freshly connected account at all. And "too soon" is a 200 carrying
  `status="cooldown"` and the seconds left, never an error envelope: it is the honest answer
  *this feed is already fresh*, and it has to arrive with `last_polled_at` beside it or the
  screen loses the one thing it was drawn to say.
- **Silent skips need one loud override** (#342, `gmail/manual.py`). The ingest has ten ways to
  decide against a message, and two are ordinary enough to hit every agency: a
  sender who is not a contact yet (`has_external_match`), and anything older than the day the
  mailbox was connected (the first poll baselines and imports nothing, on purpose). Add an
  expired `historyId`, a deferral to a mailbox that later opted out, an excluded label, an
  earlier rejection — and "why is this e-mail not on the timeline?" has no answer anybody can
  act on. The thing worth noticing is that **almost none of them are blindness; they are
  decisions**, and the message is sitting in a mailbox we already hold a grant for. So the
  owner may find one and override it. Three rules.
  **The id space is Google's, so the guard is Google's.** Every read goes through `acting_as`
  — the caller's *own* grant — and a Gmail message id means something only inside one mailbox,
  so a guessed, copied or brute-forced id answers 404. That is what makes "accept an id from
  the client" safe here and unsafe almost everywhere else: it is not an id into *our* tables,
  where the check would be ours to get right.
  **A thread we already logged is not new reach.** The commonest complaint is not "this
  e-mail" but "the *rest* of this conversation", and we hold that `gmail_thread_id` already —
  so `GET /gmail/threads/{id}` lists one thread and marks which of its messages are on the
  timeline. Since #372 a *reference* widens to its conversation too: it used to answer with the
  single message whenever the id resolved cleanly, so the better your reference the **less** you
  were shown, and "which of these are missing?" could only be asked by accident.
  **Gmail's web ids are not Gmail's API ids, and pretending otherwise fails silently.** What a
  person copies out of the address bar today is an opaque `FMfcgz…` id the API neither accepts
  nor converts. Three references *do* resolve and the parser takes exactly those — a hex id, a
  `msg-f:`/`thread-f:` decimal, and the RFC-822 `Message-ID` via `q=rfc822msgid:` (a lookup
  wearing a search's clothes, and the one reference anybody can always obtain). Anything else
  gets its **own** error key naming the two that work, because a link that can never resolve
  answered with a generic failure is how somebody concludes the feature is broken.
  **And it refuses to guess.** No contact matching, no company ranking: the caller says where
  the message is filed, exactly as an uploaded `.eml` does. Every matching rule that could have
  run here is a rule that already declined this message once.
- **The conversation is the unit of review, and the queue folds it like the timeline does**
  (`InteractionService.list` / `thread`, `docs/UX.md`). #272 gave logged e-mails a
  `conversation_id` and left a pending row deliberately without one — it joins a conversation on
  approval — so the review queue listed a twelve-message reply chain as twelve rows, each to be
  read, filed and approved on its own, over a data model that already knew they were one thread.
  Nothing was missing from the model; the *list* had never been asked. Four rules. **A pending row
  folds on the one key it can honestly have**: the mailbox owner plus the Gmail thread, never the
  logged conversation's key. A Gmail thread id means something only inside its mailbox, and an
  unreviewed row is private to its owner (#172) — the fold runs *before* the privacy condition
  narrows the timeline, so a merged group could elect a representative only one viewer may see.
  The two keys therefore stay apart, and `/thread` on the pending row is where they meet: the
  logged history anybody may read, with where it was filed, beside the caller's own waiting
  messages. **The row carries what a thread-level act needs** (`review_ids`, the pending thread
  oldest-first) because the bulk routes take ids and a queue row now stands for several — so the
  screen expands a ticked fold into the batch, the bar prints the message count whenever it differs
  from the row count, and no bulk route widens itself: a batch that quietly did more than its
  selection said is the one way it could stop meaning what its selection means (§18). **A single
  approve may take the thread with it** (`InteractionApprove.whole_thread`, on by default in the
  review desk, off by default at the API so the generated MCP tool keeps approving exactly the row
  it named), each sibling through the same `_approve_row` — the same trail, host mirrors and
  conversation folding as fifty clicks, never a shortcut past them; unticking it is how one message
  of a thread is filed somewhere else. And **"ignore this conversation" now takes the rest of the
  queue for it**: `suppress_thread` rejects the owner's other pending messages of the thread in the
  same step, said out loud under the checkbox, because a suppression that left four siblings in
  the queue one click each was half an answer. The logged history is never touched by either —
  somebody approved it. Inheritance for the *next* reply was already here (`gmail_thread_followup`,
  `thread_mappings`): a follow-up in a filed thread inherits the filing, and
  `inherit_approve` logs it without review.
- **Searching your own mailbox is allowed; browsing it is not** (#372, `manual.search`). This
  section used to argue the other way: *"a **picker** means `messages.list` over arbitrary
  personal mail rendered inside the CRM … and it would make 'schakl only ever sees matched
  mail' untrue. Refused, not deferred."* The concern is real; the conclusion was too strong,
  and the tell is what the promise is **about**. "schakl only ever sees matched mail" is a
  statement about the *poller* — what the integration ingests on its own initiative, unattended,
  into a shared timeline. It was never a promise that the owner of a mailbox may not look in
  their own mailbox. Requiring them to go to Gmail, open "Toon origineel", copy a `Message-ID`
  and paste it back protected nothing at all; it just meant that in practice nobody used the
  feature, and the one email they wanted filed stayed unfiled.
  What answers the concern is the *shape*, not the absence. **The caller's own grant**, always
  (`acting_as` with their connection — Google's authorization is the boundary, the same thing
  that makes accepting a message id safe here). **Named fields, never raw Gmail syntax**
  (`GmailSearchQuery` → `build_search_query`): we construct the query, so a colon in an address
  cannot become an operator and "what was searched for" is a sentence we can state — a free-text
  operator box would be forwarding user input to a search engine over personal mail. **Nothing
  stored**: metadata in one response, no row, no cache; content still arrives only on import,
  under the same grant. And **a hard ceiling** (`MAX_SEARCH_RESULTS = 20`), because a picker over
  an unbounded result set is exactly the mailbox browser this is careful not to be. An empty
  query is refused rather than answered — with no fields it *is* "list my mailbox".
- **Ten silent skips need one honest answer, and it is a dry run** (#372, `gmail/gates.py`).
  The override above lets you fix one message; it never told you *why* the message needed
  fixing. Every skip but one was a bare `return 0`, so "why did this e-mail never appear?" had
  no answer for the mailbox owner or for us — the code comment on the single skip that did log
  said as much. The fix is a refactor before it is a feature: the chain is now `classify(...) ->
  Decision`, a function that **decides without acting**, and `_ingest_message` fetches and acts
  on what it returns. The explainer behind the manual importer calls the *same* function, which
  is #324's rule (*"named once and read twice, because two copies of it is how they came to
  disagree"*) applied to the whole chain — an explainer that drifts from the ingest answers
  confidently and wrongly, which is worse than not answering.
  **It is a query, not a log.** The tempting design is a `gmail_skips` row per decision, and it
  is wrong twice: it would be a record of *every e-mail you receive* (newsletters, supplier
  invoices, password resets, GitHub notifications — #324's inventory of a real mailbox), which
  is strictly more than this module promises to know; and it answers speculatively for thousands
  of messages nobody will ever ask about. The question is asked about **one** message, by
  somebody looking at it, through the fetch they already requested.
  **Two exceptions, and the test is not "is it useful".** It is *"is this a failure rather than
  a policy, and would the user never know to look?"* Two qualify: a deferral to a colleague's
  mailbox that then stops polling (the e-mail is simply gone, and nobody suspects a second
  mailbox exists), and a poison message skipped so it cannot wedge the feed. Those get a
  `gmail_skips` row — **ids, a reason and a timestamp, no subject and no participants** — reaped
  after `SKIP_RETENTION_DAYS`, because a permanent record of a transient failure is a log by
  another name. The other eight are policy the dry run explains perfectly well on demand.
  **And two answers do not come from the gates at all.** A message older than the connection was
  never offered (the first poll baselines and imports nothing), and one that passes every gate
  and is *still* not here was never seen either — a `historyId` gap, which is unknowable per
  message and so is reported as the observation (`never_offered`) rather than as a cause we
  would be guessing at. Running the gates on those and printing a verdict would be the
  confident-and-wrong failure in its purest form; for both, the honest answer is "this was never
  offered" and the right response is simply to import it.
- **A second way to log an e-mail must not be a second-class e-mail** (#342). "Laat schakl deze
  taak invullen" (#327) was reachable only from `approve()` — gated `_owned_gmail_or_404` +
  `_pending_only` — so an uploaded `.eml`, which lands `logged` on purpose and never passes
  through review, **could not offer it at all**. Nobody decided that: the offer was attached to
  the *review transition* rather than to the act it actually belongs to, and the second source
  inherited the omission. It now hangs off **filing an e-mail onto a task**, which is something
  all three sources do, and one worker job serves all three (it re-defers while the body has
  not landed, which is exactly what makes an upload's already-present body and a gmail row's
  not-yet-fetched one the same case). The generalisation is worth more than the fix: when a
  capability is reached through one path's transition, adding a second path silently drops it,
  and no test fails — so hang it off the *act*, and let every path call it.
- A dedicated `email_logs` module (or generic `relations` rows, §6) attaching to
  company/contact/project, with its own company panel.
- **Privacy:** mailbox connection is per-user and opt-in; let users scope it to a label/query.
  "The CRM reads all my email" is a trust landmine even internally.

## 7. Marketing sources — enabled APIs are a *project* fact, not a token fact

The `marketing` module (#134) rides this same OAuth client, so it adds no login and no second
grant. What it does add is a dependency the other surfaces don't have: **each source calls one
or two Google APIs that must be switched on in the Cloud project the OAuth client belongs to.**

| Source | Scope | APIs to enable in the Cloud project |
|--------|-------|--------------------------------------|
| GA4    | `analytics.readonly` | **Google Analytics Admin API** (the property picker, `analyticsadmin.googleapis.com`) **and** **Google Analytics Data API** (every metric, `analyticsdata.googleapis.com`) |
| Search Console | `webmasters.readonly` | Google Search Console API (also the whole surface of the `google_search_console` integration, URL inspection included — `docs/GOOGLE_SEARCH_CONSOLE.md`) |
| Google Ads | `adwords` | Google Ads API, **plus** a per-org developer token (§ the module's own settings) |

Two APIs for GA4 is the trap: enabling only the Data API leaves the picker dead while the rest
of the module looks configured, because listing properties is an *Admin* API call.

### One consent for the module, not one per source

`/google/oauth/connect?include_marketing=1` asks for all three marketing scopes in a single
consent. The per-source flags (`include_analytics`, `include_search_console`, `include_ads`)
still exist for an API caller that wants exactly one, but **no UI uses them**: each picker used
to link its own flag, so switching on one module cost three separate walks through Google's
consent screen. Incremental authorization does not save that round-trip — it only makes the
second and third look necessary. Instellingen → Account asks for the same union when the
marketing module is on and the person may manage links, so connecting from there is enough too.

The connect link also carries `next=` — the page the consent was started from, so a picker on a
client's page lands back on that client's page. Only a **site-relative** path is honoured
(`oauth.safe_return_path`): it comes off a URL, so `//host`, `/\host`, an absolute URL and a
newline are all discarded in favour of `/settings/account`. It rides the session across the
round-trip, because Google echoes neither the OAuth state's contents nor a redirect URI that
varies per page (the redirect URI is an exact-match allowlist entry in the Cloud console).

### A link belongs to a person, and every screen says so

Marketing links sync through **one colleague's** connection. `LinkRead.connection_owner` and
`SourceMetrics.connection_owner` carry that person (their name, the Google account, and whether
they are the caller), and the panel, the tab and the picker render it. Without it, the second
employee sees a working link with no hint whose it is — connects Google again for data that is
already flowing — and nobody learns that the client's numbers stop the day that person leaves.
For the same reason `AccountsResponse.connected_via` names colleagues whose grant already
reaches the source the picker is empty for.

### Google Ads manager accounts (MCC) are the normal agency shape

Access is granted to the **manager**, not to each client under it, and
`customers:listAccessibleCustomers` answers *direct* grants only — so the raw list is one MCC id
and the picker offers none of the accounts the agency actually runs. `GAdsAdapter.list_accounts`
therefore reads `customer.manager` for each accessible customer and, for a manager, expands it
with a single `customer_client` query — which returns the **whole hierarchy**, so a nested MCC
needs no recursion. Sub-managers are dropped and their clients kept: every account in the tree is
reachable with the top-level manager as `login-customer-id`.

Each child is stored with `config["manager_id"]`, and `_headers` turns that into the
`login-customer-id` header on every later call (metrics, drill-downs, the nightly sync). Without
it those calls are made by a user with no direct grant on the account and 403. Manager accounts
are never offered as options — Google refuses metric queries against one, so linking it would
produce a permanently erroring link rather than a roll-up. The expansion is capped at
`MAX_MANAGER_CHILDREN` and going over it is **logged**, because a picker showing 500 of an
agency's 900 accounts is indistinguishable from an agency with 500.

An Ads link created before this shipped carries no `manager_id`; remove it and pick the account
again (the picker hides accounts already linked to the client, so the removal is what returns it
to the list).

### The Ads API version is a setting, because it expires

Google Ads is the one Google API here whose **URL** carries a version, and each version is
sunset roughly a year after release; from that day every path under it answers **404**. That is
not a credential, scope or account failure, so none of the picker's teaching states fit it and
the module simply looks broken (v18 sunset 2025-08-20 and did exactly this). The release pins a
current version in `marketing.sources.gads.DEFAULT_API_VERSION`, and
`SCHAKL_GOOGLE_ADS_API_VERSION` overrides it — an install that outlives its release can be
bumped from the compose file instead of waiting for one.

**A disabled API and a dead grant both come back 403.** They are not the same failure and they
do not have the same cure: a disabled API is refused before the token is even considered, so
reconnecting mints an identical token against the same project and fails identically. Google
says which is which in the response *body*, and `httpx` throws it away —
`str(HTTPStatusError)` is the status line and the URL, nothing more. So every Google call site
runs its exception through `google.client.describe_api_error`, which lifts out
`error.details[].reason` (`SERVICE_DISABLED` → the API is off, `ACCESS_TOKEN_SCOPE_INSUFFICIENT`
→ the grant is short) and Google's own message — **which names the Cloud project number**.

One classifier, `marketing.service._failure_key`, turns that into the message, so the picker,
the drill-down and the nightly sync can never disagree about what a given 403 meant:

| Google's reason | Message | Reconnect offered? |
|-----------------|---------|--------------------|
| `SERVICE_DISABLED` / `accessNotConfigured` | `marketing.api_not_enabled` | **No** — it mints the same token against the same project and fails identically |
| `ACCESS_TOKEN_SCOPE_INSUFFICIENT` (and friends) | `marketing.scope_insufficient` | Yes — this is the one case reconnecting genuinely cures |
| a **404 from Google Ads** (and only Ads) | `marketing.ads_api_version` | **No** — the version in the URL is sunset; upgrade or set `SCHAKL_GOOGLE_ADS_API_VERSION` |
| anything Google didn't name | the caller's fallback (`marketing.accounts_error`; the sync keeps Google's own sentence) | Yes |

A scope-short 403 on the account picker also comes back as `has_scope=false`, because that is
precisely what the field means — so the picker renders the "reconnect to grant access to this
source" branch it already had, rather than a generic error with a reconnect link bolted on.

**Which project, though?** `client_credentials` falls back to the instance-wide
`SCHAKL_GOOGLE_CLIENT_ID` whenever an org has not filled in Instellingen → Google. That org is
then talking to Google as *the instance operator's* Cloud project, and an admin who enables the
Analytics APIs in their own project changes nothing. Nothing else in the product makes this
visible — the connect flow, the consent screen and the granted scopes all look right — so the
failure log names the client in use (`org client …` vs `instance env client …`; a client id is
public and its leading digits *are* the project number). Expect this on a **cloud** instance in
particular, where instance-wide credentials are the norm and a per-org client is the exception.

## Suggested build order within P3

1. `google` core: OAuth connect flow + encrypted token vault + client factory (per-user first).
2. Calendar (watch + syncToken + cron renewal) — proves the webhook + cron plumbing.
3. Drive links + template-folder-on-`company.created` — high value, low risk, showcases the
   event bus.
4. Gmail matched logging (poll-based) — last: heaviest on privacy and infra.
5. Domain-wide delegation as an optional tenant setting once per-user works.

## Checklist for any Google surface

- [ ] Login and API access are separate grants; login token never carries API scopes.
- [ ] Refresh tokens encrypted at rest; revocation and re-consent handled.
- [ ] Client obtained via the "act as user X" factory, never a raw token.
- [ ] Incremental sync (syncToken/historyId), not full polling.
- [ ] Watch channels renewed by an ARQ cron job.
- [ ] `org_id` on every table; webhook maps back to org + connection.
- [ ] Minimum scopes requested; restricted scopes justified by the "Internal" app model.
- [ ] The APIs the surface calls are listed in §7, and a failure reports Google's own reason
      (`describe_api_error`) plus the OAuth client in use — never a bare status line.
