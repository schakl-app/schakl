# Microsoft 365 integration — design notes

> Outlook calendar, OneDrive and Outlook mail look like one "Microsoft integration" and are —
> exactly as with Google — **one token system and three different data problems**. This is the
> Google Workspace integration (`docs/GOOGLE.md`) answered a second time against Microsoft
> Graph, on the same seams, with the rules that are about the *agency* rather than about a vendor
> lifted into core so the two feeds run one set of them. Read `docs/GOOGLE.md` first: what is
> not restated here is the same there, on purpose.
> Status: **shipped** as one licensed registry integration `microsoft` (sku `"microsoft"`)
> holding the core plus `calendar/`, `onedrive/` and `outlook/` subpackages. **Written from
> Microsoft's Graph documentation and exercised against a scripted Graph, never against a live
> tenant** — §9 carries the checklist to run the day one arrives, and every parse is defensive
> until it has.

## The one rule

**Login is not API access.** OIDC "Sign in with Microsoft" (`docs/SSO.md`, an org-level
Authlib relying party) and Graph API access (Calendar/OneDrive/Mail authorization) are separate
grants with separate lifetimes. The login token never carries Graph scopes; "connect your
Microsoft account" is a distinct step on the person's own account page, however the org signs in.

## 1. Login ≠ API access — and no id token either

| | OIDC login (authentication) | Graph access (authorization) |
|---|---|---|
| Scopes | `openid email profile` | `offline_access User.Read Calendars.ReadWrite Files.ReadWrite.All Mail.Read` |
| Token | short-lived id token, used at login | **refresh token stored server-side, encrypted**, rotated on every refresh |
| Level | org-enforced | per-user |
| Consent | "Sign in with Microsoft" | "Microsoft koppelen" — separate, incremental |

Two decisions differ from Google and both are made in `oauth.py` rather than discovered later:

- **The connect consent asks for no `openid`.** Identity comes from Graph's `/me` with the
  access token just issued: object id, directory, address. The v2.0 issuer of an id token for a
  `common` / `organizations` registration is per-directory (`…/{tid}/v2.0`) while the discovery
  document for those pseudo-tenants prints a placeholder, so an id-token validator has to be
  taught to look the other way — and a validator taught to look the other way is worse than
  none. `/me` answers the same three facts with the credential we store anyway.
- **`offline_access` is the refresh token.** Without it Microsoft issues none and the
  connection dies in an hour; it rides every consent, and the callback refuses a first connect
  that came back without one (`errors.microsoft_no_refresh_token`).

Refresh tokens are not scope-bound in v2.0 — a refresh may ask for any scope the user has
consented to for this registration — so scopes union across consents here too, and a later
"reconnect with my mailbox" adds `Mail.Read` without losing the calendar.

**There is no revocation endpoint.** Disconnecting deletes the stored tokens and nothing more;
the account card says where the user revokes the registration's access on the Microsoft side
(`myapps.microsoft.com`), because a control that pretends to have done it is worse than one that
says it cannot.

## 2. Your own app registration

Each agency registers its own **Entra app registration** (Instellingen → Microsoft 365, the
#76 SSO pattern; `SCHAKL_MICROSOFT_CLIENT_ID` / `_SECRET` / `_TENANT_ID` are the instance-wide
env fallback). What the admin carries across:

- the **Redirect URI** the settings page prints — derived from the org's hostname, never typed;
- the **tenant** the registration signs people in against: `common` (any work, school or
  personal account), `organizations`, or one directory's id. A single-tenant registration is the
  right answer for an agency on one Microsoft 365 tenant: it keeps a colleague's personal
  Outlook.com account off the consent screen;
- the **delegated permissions** above, admin-consented once if the tenant requires it;
- the **notification URL** the settings page also prints, for a proxy in front of the host
  (Cloudflare Access and the like): Graph's change notifications come from Microsoft's servers,
  and a proxy that only lets browsers through turns them away silently.

The client secret is write-only and stored encrypted (the same Fernet scheme as every other
secret); an Entra secret **expires** (24 months at most), and the day it does every refresh
fails with `invalid_client` — which is why a dead grant is reported to the owner *once*
(`microsoft.connection_error`) rather than once per cron tick, and why the log line names the
registration in use (`registration_hint`).

## 3. Architecture: one `microsoft` core + three surface subpackages

Follows the module pattern (§6) and the Google integration's layout exactly:

- **`microsoft` (core)** — the connect flow, the encrypted token vault
  (`microsoft_connections`), the "act as user X" client factory (`client.acting_as`, an httpx
  client rooted at `SCHAKL_MICROSOFT_GRAPH_BASE_URL` so no call site ever spells a host), and
  the per-org settings row.
- **`microsoft.calendar`, `microsoft.onedrive`, `microsoft.outlook`** — each contributes its
  scopes to the consent, its ARQ cron jobs, its panels, its routes, and registers onto the same
  core seams Google registers onto: the busy provider (`app/core/busy.py`), the mailbox
  composition (`app/core/mailbox/internals.py`), the calendar mirror
  (`app/core/calendarmirror.py`).

**What was lifted into core to make a second provider possible** — and is therefore shared with
`google` rather than copied from it:

| Seam | What it holds | Why it could not stay in `google` |
|---|---|---|
| `app/core/mailbox/matching.py` | participants, the intended owner, colleague-only chatter, the contact match and its ranking (#305, #324, #274) | a rule about the agency; §6 forbids `microsoft` importing `google.gmail` |
| `app/core/mailbox/gates.py` | `SkipReason`, `Decision`, `GateCache` — the vocabulary of *why* a message was declined | the web draws one set of sentences for both feeds |
| `app/core/mailbox/internals.py` | who counts as *us*, composed across every registered mailbox provider | a copy held by a Gmail mailbox must defer to the Outlook mailbox of the colleague it was addressed to, and the reverse |
| `app/core/mailbox/policy.py` | the approval / thread-followup policy enums | two settings screens, one vocabulary |
| `app/core/calendarmirror.py` | what a mirrored event *says*: the snapshot, the title, the breakdown, the RRULE | a Graph event and a Google event carry the same words |

`interactions` gained a second connected-mailbox source: `InteractionSource.OUTLOOK` beside
`GMAIL`, folded into `MAILBOX_SOURCES` for every rule that is really "a row from somebody's
mailbox" (the owner-only review flow, the no-edit rule, the body sweep). The two id columns kept
their names (`gmail_message_id` / `gmail_thread_id` now hold *the provider's* message and
conversation ids for any such row) and were widened to 512, because a Graph id is a
~150-character base64 string. The `interaction.approved` / `interaction.rejected` payloads
carry `source`, and each feed's subscriber acts only on its own rows — a payload without one
reads as gmail, which is what every payload before this integration was.

## 4. Calendar

The same shape as Google's (`docs/GOOGLE.md` §4): a **local cache** the Agenda reads, filled by
**incremental sync**, kept fresh by **push notifications** with a **poll fallback**, and a
**one-way outbox** for approved leave, planned task blocks and freelance availability. Graph
differs in four places worth knowing.

- **The delta is windowed, and the window is fixed.** `calendarView/delta` is started over a
  `startDateTime..endDateTime` span (today − 30 d … today + 365 d) and every later delta link
  continues inside *that* span; an event created for the day after it never arrives. So the
  channel remembers `window_end` and re-baselines (drops the delta link, wipes its cache,
  refills) once the end is fewer than 120 days away — the same reset a `410`/
  `SyncStateNotFound` forces, on a calendar instead of on failure.
- **Occurrences, not masters.** `calendarView` expands recurrences; an occurrence names its
  series in `seriesMasterId`, which is what lets the Agenda drop every instance of a repeating
  availability row the outbox pushed as one rule (`docs/GOOGLE.md`'s "a row that repeats" rule,
  `series_master_id` here).
- **Subscriptions expire in days, not weeks.** A change-notification subscription on
  `/me/events` lives ~3 days; the hourly cron renews any with less than a day left
  (`PATCH /subscriptions/{id}`). Registration has a **validation handshake**: Graph POSTs
  `?validationToken=…` to the notification URL and expects the token echoed back as text within
  ten seconds, so the webhook answers that before it looks for a body — and it discloses
  nothing, so it needs no gate. A notification carries our `clientState`
  (`{org}.{connection}.{secret}`, the Google channel-token pattern); the handler binds RLS from
  it, compares the secret constant-time against the connection's channels, and enqueues a sync
  on a match. It answers **202** to everything, match or not: Graph retries a non-2xx, and a
  forged notification retried is worse than one dropped.
- **Every instant is UTC on the wire.** `acting_as` sends `Prefer: outlook.timezone="UTC"`, so
  a `start.dateTime` of `2026-07-08T09:00:00.0000000` is parsed as UTC and stored as an instant;
  an `isAllDay` event carries its date pair with Graph's exclusive end, made inclusive at read
  time exactly as the Google cache does.

Push (schakl → Outlook) builds a Graph event from the shared snapshot: `subject`, a `text` body
with the per-day breakdown or the task deep link, `start`/`end` with the org's IANA zone,
`isAllDay`, `showAs` (`free` for an offered extra day, `busy` otherwise — the availability rule),
a weekly `recurrence` for a repeating row, and a `singleValueExtendedProperties` entry naming the
schakl record, which is what marks the event as ours for any future reconciliation. Create is
`POST /me/events` (or `/me/calendars/{id}/events`), update `PATCH /me/events/{id}`, delete
`DELETE /me/events/{id}` where 404/410 is an event already gone. Shared calendars are selected on
the account page exactly as Google's are (`GET/PUT /microsoft/calendar/calendars`), each a channel
of its own; watches stay on the default calendar only.

The busy seam (`microsoft.calendar`) answers a colleague's window without a title, the
free/busy rule; the feed source on the Agenda is `microsoft.calendar`, coloured and hidden per
calendar through the per-person split machinery. The two share that key on purpose: the
scheduling dialog's "Gelezen: …" legend names an integration's provider through the web
calendar-source registry (`calendarSourceLabelKey`), so the tasks module never carries a list
of calendars it is not allowed to know about.

## 5. OneDrive — the reference model, addressed by two ids

**Reference/link model, no sync, no mirror** — every argument in `docs/GOOGLE.md` §5 holds, and
the seven rules there (the folder is a stored decision, first-folder is `write` and re-pointing is
`manage`, provisioning never overwrites a choice, unlink and delete are two acts, a folder is
refused unless empty, everything is entity-addressed through `entity_visible`, an upload attaches
to the record it was uploaded from) are implemented identically in `onedrive/service.py`. What
Graph changes:

- **A drive item is `(drive_id, item_id)`.** A link into a SharePoint document library and a
  link into somebody's personal OneDrive are otherwise indistinguishable strings, so every link,
  every listing row and every browser crumb carries both. The org's root is
  `onedrive_drive_id` (the library's drive id — the Shared Drive analogue — or a OneDrive's) plus
  `onedrive_parent_folder_id` (an item, or `root`). Without a drive id nothing can be
  provisioned and the panel says so.
- **Search covers the subtree.** `items/{id}/search(q=…)` is a search under the folder rather
  than a filter of its children, so the browser's header says which folder was searched and the
  results may be deeper than one level.
- **Upload is a session.** `createUploadSession` hands back a pre-authenticated URL the
  browser PUTs the bytes to in `Content-Range` fragments; the bytes never transit our API, and
  the final fragment's response is the drive item whose id gets linked to the record.
- **A template is copied asynchronously.** `POST …/copy` answers `202` with a monitor URL, which
  the provisioning worker polls (bounded) until `completed`; a copy is recursive, so a client's
  folder is one copy of the template folder under a new name rather than a walk of its children.
- **Delete is the recycle bin.** `DELETE /drives/{d}/items/{id}` moves the item to the recycle
  bin (93 days on SharePoint, 30 on OneDrive), the same "trash, never purge" posture Drive gets.

The permission split, the activity trail (`onedrive.folder_set` / `folder_changed` /
`folder_cleared` / `file_trashed`), the Redis-cached listing, the scope check before the round
trip (`missing_files_scope` — a connection made before OneDrive was switched on is `active` and
refused by every call) and the `_call` translation of Graph's refusal into a key that states the
fix are the Drive service's, one integration over.

## 6. Outlook mail — the same feed, a different cursor

**Do not sync whole mailboxes.** Matched, metadata-first logging: only mail whose participants
match a known contact outside the agency, landing `pending` for the mailbox owner (and the
colleagues on the message) to approve, the body fetched only after approval, an `.eml`-shaped
manual import for what the poller declined, a named-field search over the owner's own mailbox and
nothing stored from it. Every rule in `docs/GOOGLE.md` §6 applies verbatim because it is the same
code: `outlook/service.py` runs the core matching, the core gates, the core internals, and writes
through the interactions module's published surface with `source="outlook"`. What is Graph's:

- **The cursor is an instant, not a history id.** Graph's `messages/delta` is *per folder*, and a
  message a rule moved into `Clients/Acme` on arrival never appears in the Inbox's delta —
  exactly the mail an agency most wants logged. Gmail's history covers the whole mailbox; the
  nearest Graph equivalent is the whole-mailbox listing `GET /me/messages` filtered on
  `receivedDateTime ge <cursor>` and ordered ascending, so the feed reads forward from the
  instant its last poll reached (`outlook_cursor_at`, with a five-minute overlap that the
  already-logged and suppression gates make harmless). The first poll baselines the cursor and
  imports nothing, on purpose.
- **Folders say what labels said.** Drafts, Junk Email and Deleted Items are `NOT_A_MESSAGE`
  by `parentFolderId` (their well-known ids are resolved once per poll); Sent Items is what
  `SENT` was for direction. The owner's opt-out is a **category** (`outlook_excluded_category`,
  `SkipReason.EXCLUDED_CATEGORY`) — Outlook's word for a label.
- **Identity is `internetMessageId`.** The RFC-822 `Message-ID`, angle brackets included, is
  what the cross-mailbox dedup compares with Gmail rows, so a thread half the agency reads in
  Outlook and half in Gmail is still one entry per email. The conversation is `conversationId`,
  stored in `gmail_thread_id` (§3).
- **A reference is a Graph id, an OWA link or a Message-ID.** Outlook on the web puts the
  message id in its URL (`/mail/…/id/<id>`) and Graph reads that id back, unlike Gmail's opaque
  web ids; a `Message-ID` is looked up with `$filter=internetMessageId eq '…'`. A message
  resolved widens to its conversation (`$filter=conversationId eq '…'`), and a search is a KQL
  string built from named fields (`participants:`, `subject:`, `received>=`), never forwarded
  operator syntax.
- **The body arrives in the format it was written.** `GET /me/messages/{id}?$select=body` with
  `Prefer: outlook.body-content-type="html"` answers HTML when the message had it (→
  `body_markdown` through `core/htmlmd.py`, and a stripped `body_text` for search) and plain
  text otherwise — the "a received body is not our markdown" rule; inline parts are matched by
  `contentId` against what the converted body references and stored as `content_id` files.

`Instellingen → Microsoft 365` holds the same three policy switches Google's page holds
(approval mode, thread follow-up, log internal mail), reading the shared enums; the interactions
screen draws one refresh button and one message picker for whichever mailbox the viewer holds —
and both, when they hold both.

## 7. Enabled APIs are a registration fact

Graph needs no per-API enablement (nothing like Google's "enable the Calendar API in this
project") — but a delegated permission the registration never declared, or that an admin never
consented to, is refused with `ErrorAccessDenied`/`Authorization_RequestDenied`, which
`GraphApiError.scope_insufficient` reads as a reconnect and the OneDrive service reports as
`errors.microsoft_onedrive_scope_missing`. The failure log names the registration
(`registration_hint`), for the same reason the Google log names the Cloud project: an org riding
the instance env registration by accident looks right on every screen but one.

## 8. Both at once

An agency on Microsoft 365 that keeps one Google account for Analytics is ordinary, and so is a
colleague who connects both. Each rule that could disagree was decided rather than left:

- **A person with both calendars gets the block in both.** Two integrations subscribe to the
  same bus events and each pushes for the person it holds a connection for; choosing one on
  their behalf is not a decision the platform can make.
- **A person with both mailboxes is one colleague** — `owner_by_email` carries both addresses,
  `syncing_user_ids` is the union, and a copy held by either mailbox defers to the other when
  that is where the message was addressed.
- **The Agenda draws two sources** (`google.calendar`, `microsoft.calendar`), each with its own
  colour and per-calendar rows; the busy dialog composes three thirds and a fourth.
- **The hub draws two panels** (`google.drive.company`, `microsoft.onedrive.company`), and a
  record's Drive folder and OneDrive folder are two independent decisions.

## 9. Not exercised against a live tenant — the checklist

Everything above is covered by the suite against a scripted Graph, and every screen was driven
in a browser against a fake identity platform and Graph (the connect round trip, the settings
page, the calendar selection, the Outlook review flow, the OneDrive browser and provisioning).
Whoever first runs this with a real tenant should check, in order:

1. The consent screen lists exactly the five delegated permissions and comes back with a
   refresh token; a second connect with the mailbox ticked adds `Mail.Read` and keeps the rest.
2. `GET /me` answers `mail` for a work account; for an account with no mailbox it answers only
   `userPrincipalName`, which is what the connection then reads as its address.
3. A calendar subscription registers against the public HTTPS host (the validation handshake
   arrives within ten seconds) and a change in Outlook reaches the Agenda inside a minute; on a
   box without public HTTPS the channel parks on `failed` and the 15-minute poll carries it.
4. The `calendarView/delta` window: an event created 14 months out is absent until the
   re-baseline, as documented, and never silently.
5. A leave request approved lands in the requester's Outlook calendar as all-day, an extra
   availability day as *free*, and a task block titled `«client»: «task»`.
6. Outlook mail: a message moved by an inbox rule still logs; the sent copy of an outgoing mail
   reads outbound; a category named in the connection's opt-out excludes; a Message-ID pasted
   from "View message source" resolves; an OWA link resolves.
7. OneDrive: browsing a SharePoint library the viewer is a member of works with
   `Files.ReadWrite.All` alone; a 10 MiB upload arrives in the folder via the session URL and is
   linked to the record; a template copy completes and lands the client's folder; a non-empty
   folder is refused; a binned file appears in the recycle bin.
8. Throttling: a `429` from Graph on the poll is logged with `Retry-After` and the next tick
   picks the mailbox up — never a wedged feed.

## Checklist for any Microsoft surface

- [ ] Login and Graph access are separate grants; the connect consent carries no `openid`.
- [ ] `offline_access` on every consent; refresh tokens encrypted at rest and rotated on refresh.
- [ ] Client obtained via `acting_as`, never a raw token; paths only — the host is a setting.
- [ ] Incremental sync (delta link / cursor), never a full listing on a schedule.
- [ ] Subscriptions renewed by an ARQ cron; the webhook echoes the validation token and answers
      `202` to everything else.
- [ ] `org_id` on every table; a notification maps back to org + connection through our own
      `clientState`.
- [ ] Minimum scopes requested; `Files.ReadWrite.All` justified by the SharePoint library.
- [ ] A failure reports Graph's own `error.code` (`describe_api_error`) plus the registration in
      use — never a bare status line.
