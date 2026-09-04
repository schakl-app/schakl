# Changelog

_Releases v0.25.0 through v0.41.0 are written up on their GitHub Releases; this file resumes at v0.42.0._

## v0.42.0 — 2026-09-04

Search Console as its own read surface with an honest answer about AI visibility, a task that
is named before it exists and is always a client's, a subscription backlog that lists what the
cron is about to draft, and two member tables that open on the colleagues who still work here.

No migrations: the schema head is unchanged from v0.41.0, so rolling the image back is safe.
Two new permission keys, both on the new integration. Thirteen new endpoints, all `GET`; no
existing route changes shape, but one query parameter and one request field are gone from
tasks (see the upgrade notes). No new environment variables. The public API reference is
regenerated.

### Google Search Console (integration)

- **Search Console is a read surface of its own.** It was reachable from an agent only as the
  marketing module's stored four-metric aggregate and three drill-downs, while Analytics had
  seventeen routes and its own section. It now has the same shape: thirteen `GET` routes under
  `/api/v1/google-search-console` — the sites and sitemaps a connection may read, an overview,
  the split per search type, the daily and hourly series, a breakdown by any dimension, the
  movers between two spans, the URL inspection, and a free-form query behind its own
  permission. They are derived into the `/mcp/google-search-console` section and the `growth`
  bundle, and seven curated tools reach the in-app assistant, so "how did this site do in
  search last month" is answerable in the box built for asking it.
- **The property travels as `?site=`**, because a `siteUrl` is a URL: a path parameter is
  decoded before it is matched, `%2F` becomes `/`, and the route stops matching.
- **Every enum is checked here before the round trip.** Dimensions, search types, data states
  and the rest were read from Google's own discovery document (revision `20260902`); an unknown
  value is refused with the list that would have worked in `details`, where Google's own 400
  names neither the bad value nor the good ones.
- **AI visibility is a state, not a number.** Search Console gained a Generative AI performance
  report in June 2026 — impressions in AI Overviews and AI Mode — and the Search Analytics API
  still accepts exactly six search types and no generative-AI value. So `GET /ai-visibility`
  answers `available: false` with the report's own URL and never estimates the figure from the
  web totals, where AI Overviews are folded in with no way to separate them. The marketing
  dashboard's Search Console section draws the same fact as a dashed card with a link — never a
  tile with a number — and withholds it from a portal login. The seam is pinned by a test that
  fails the day Google adds the search type, which is when the card wants revisiting.
- Two permissions: `google_search_console.site.read` (the curated shapes; administrators and
  employees by default) and `google_search_console.report.run` (any dimension crossed with any
  filter; administrators only). The integration requires the Google connection and nothing
  else, and it is licensed under its own sku, `google_search_console`.
- Not verified against a live Google grant: twenty-two tests run on a fake transport, and the
  browser pass covered the card, the settings listing and the MCP section through a scoped key.

### Tasks

- **A task is named before it exists.** The placeholder create — one click, a row titled
  "Naamloze taak", italicised in every list and gathered by a "Naamloos" pill — is deleted
  rather than disabled. Every `Nieuwe taak` (the list, the client header, the client's Taken
  panel) opens the create dialog, a whitespace title is refused at the schema, the pill and the
  `?unnamed=1` filter are gone, and rows an instance already has keep their one free deadline
  move and their italics until they are named.
- **And it is always a client's.** A task with no client is on no client's page, in no export,
  on no report and outside every company horizon. Unlike a deadline it has no honest default,
  so nothing invents one: the create refuses with the field named
  (`errors.tasks_company_required`), the update refuses clearing it, an automation rule's task
  inherits the client of the task it fired on, and the import marks the column required so the
  preview names the row. The one indirection is a project — naming the project is naming the
  client, because a project has exactly one. The column stays nullable for a release
  (expand/contract), and a legacy row without a client keeps taking every other edit.
- On screen the dialog draws its own client picker wherever the surface has not pinned one,
  with no "Wissen"; the dictation sheet holds Aanmaken until a client is picked; the
  contactmoment dialogs refuse before the round trip; and the task page's client picker lost
  its clear affordance.

### Subscriptions

- **An overdue agreement is on the backlog, and is billed in one run.** An audit of the demo
  instance found every agreement with a next invoice date months in the past absent from "Nog
  te factureren" while the cron drafted one historic period for each of them a night. Two rules
  were each right alone and wrong together: the first activation derived the cycle date as start
  plus one period, which for an onboarded agreement landed years back and handed the cron every
  period since — the back-billing the backlog's floor exists to refuse — and that floor, applied
  to the anchor as well as to the history behind it, hid from the backlog exactly the period the
  cron was about to draft. A derived cycle date is now the first boundary of the start date's
  grid still ahead (an explicit date stays the operator's, everywhere); the floor bounds what
  the walk reaches and never where the cycle sits, so the anchor and every boundary forward of
  it up to today are offered; and the nightly cycle cron catches up in one run instead of one
  period per night.
- **The list takes the contacts list's shape**: sections per standard subscription, the client
  as the primary column and the default sort, the name muted, the client's page moved to the ⋯.
  A passed next invoice date is drawn as an overdue deadline, and the strip gained
  "Factuurdatum verstreken", opening the backlog narrowed to agreements for whoever may read it.

### Invoicing and payments

- **Two em dashes went.** A period picked into an invoice — a domain renewal, an agreement's
  lines — was written as `klant.nl — 01-01-2026 - 31-12-2026`, one run of dashes between a name
  and a range that is itself dashed; it is `klant.nl (01-01-2026 - 31-12-2026)` now, the shape
  the nightly cron's own renewal line already had. And the Mollie payment description, which is
  what the payer reads on their bank statement, was `2026-0042 — breik.`; a statement is the one
  surface whose typography is not ours, so it is a plain hyphen there now. Lines already on a
  document keep the text they were saved with.

### Leave

- **The two member tables open on the colleagues who still work here.** The balances on
  `/leave/team` and the entitlement table under Instellingen → Verlof listed everyone who ever
  worked here flat between the ones who still do. Both now show the active roster first, then a
  "Gedeactiveerd (n)" strip that is closed by default and absent when nobody has left, opening
  to the former colleagues' rows with an amber badge on each. Nothing is deleted: the vacation
  split, the employment ⋯ and the entitlement edit all keep working inside the fold.

### Housekeeping

- The task import test named no client on any row, which this release refuses on purpose; it
  names one now. That one assertion was the only red on `dev` since the required-client commit.

### Upgrade notes

- **No migrations.** The schema head is `d4a9b3c6f2e7`, the same as v0.41.0.
- **`POST /tasks` without a client is refused** with a 422 naming `company_id`, unless the body
  names a project. An automation, an MCP agent or an import that created tasks with neither will
  meet that refusal from now on; a rule that fires on a task inherits that task's client and
  needs nothing changed. `PATCH /tasks/{id}` refuses an explicit `null` client; absent still
  means leave alone, so an update naming only `status` works on a legacy row. The `unnamed`
  field on the create body is gone (a caller that still sends it gets a named task, since the
  title it sends is the title), `?unnamed=1` on `/tasks` is no longer recognised, and
  `TaskRead.unnamed` stays for the rows an instance already has.
- **The task import requires a `company` column.** A sheet without one is refused before any
  row is read, and a row with an empty cell is reported by row.
- **Overdue agreements bill on the first night.** An agreement whose next invoice date is
  already in the past shows on "Nog te factureren" the moment you upgrade, and the nightly cron
  drafts every outstanding period up to today in one run rather than one per night. Nothing
  before the agreement's anchor is invented and no stored cycle date is moved; from now on a
  date the platform derives at first activation lands on the first boundary still ahead
  instead of one period after the start. Read the backlog before the first night if you would
  rather cancel a period than have it drafted.
- **Google Search Console is switched on per organisation** under Instellingen → Integraties
  and needs the Google integration enabled beside it. On a self-hosted install its sku
  (`google_search_console`) has to be on the licence key before it can be enabled. A Google
  connection made before Search Console was part of the grant has to be reconnected once, and
  the error says so. Its
  two permissions are granted to the seeded roles once, on the first start after upgrade:
  `site.read` to administrators and employees, `report.run` to administrators.
- **The `growth` MCP bundle grew.** A key scoped to `/mcp/growth` sees the thirteen new tools
  as soon as the integration is enabled and the key holds the permission; a key that holds
  neither sees nothing new.

## v0.24.0 — 2026-08-10

Notifications that reach a phone with the laptop shut, quiet hours that are finally applied,
tasks a client could be shown and could not read, and screens that stopped using the whole
monitor.

### Browser notifications (#309)

- **A notification can reach you with the tab closed.** Web Push joins the bell, e-mail, Slack
  and the other webhook channels as a fifth way of being told something happened, and it is the
  only one that works when the browser is not open — a task you are mentioned in reaches your
  phone. Instellingen → Meldingen carries a block for the device you are on: allow it once per
  browser, and the per-event matrix that already routes the other channels routes this one.
- Registering takes nothing typed and nothing configured on the server. Each browser is its own
  device in the list, with the moment it was last reached, and can be switched off from any of
  them.
- **Allowing the browser is the whole opt-in.** You are then pushed the things that were already
  urgent — a task assigned to you, a mention, an overdue item, a leave request and its decision —
  and nothing that was only ever meant for tomorrow's digest. The alternative was a permission
  dialog followed by silence and a second screen nobody was sent to; the split is what keeps a
  phone from being woken to deliver yesterday's news. Every one of those events is a row in the
  matrix, so an organisation or a person can change any of it.
- **One message per person, never per device.** A daily digest of ten events is one notification
  wherever you registered, not thirty, and it counts as delivered the moment a single device
  takes it: a phone that is off and a laptop that is open means you were reached. A browser that
  was cleared or thrown away is removed the first time it is written to, and costs the message
  none of its retries.
- **Quiet hours are applied at last.** The window has been stored since notification settings
  shipped and read by nothing — the hint under the field said so out loud. A notification whose
  moment falls inside it now waits until the window ends, on your organisation's clock, correct
  across a daylight-saving boundary and across a window that runs over midnight. It holds back
  the channels that interrupt you (e-mail, chat and webhooks, browser push) and never the in-app
  bell, which interrupts nobody and would make the app look broken if it were held. **This
  changes when e-mail and chat messages arrive for anyone who had filled the field in** — see the
  upgrade notes.
- "Direct" now means within fifteen seconds rather than within a minute, which is the difference
  between a notification and a reminder.
- Nothing to install: the keys each organisation identifies itself with are generated on first
  use, so the feature is never silently off after an unattended upgrade.

### A task marked visible for the client was invisible to the client

- **The client's task list asked the wrong question and always got nothing back.** It filtered on
  "assigned to me", and an unanswered assignee filter resolves to the signed-in user — which
  means an *employee*, since a client is assigned as a contact person. So a portal login asked
  for tasks assigned to an employee who is not one, and read an empty list however many tasks
  staff had ticked as visible. The assignee filter is staff-only now.
- **Every count a client read was the whole organisation's.** The visibility rule sat on the read
  path and not on the counting path, so the client panel said "Taken (12)" above a list of one.
  Stated once on the model now, so the list, the total, the detail page, the panel and the
  comment target agree by construction.
- **And an internal to-do ticked visible reached every client.** A task attached to no client was
  read as "not client data" and let through to all of them. A task's client is now the one it
  names, or its project's when it names none — which is the ordinary case, since a task on a
  project never fills the field in.
- **The employee lookup handed a client the address book.** It is open to every signed-in member
  and a portal contact is one, so every colleague's e-mail address travelled to the browser the
  moment a client-reachable screen drew a picker. Names stay, because a client is meant to see
  who is on their task; the address does not.

### Tasks

- **Every list says whether the client can read the task.** `Zichtbaar voor klant` was a checkbox
  on the card and nowhere else, so the one thing worth knowing before writing in a task was a
  click away from every list it appears in. One marker — an eye, or a struck-through eye — on the
  board, its mobile row, the project to-do list, the client panel and the card.
- "Hidden" is drawn only where there is somebody to be hidden from, and a visible task that
  reaches nobody is drawn in amber, because that is the tick that is failing to do what its owner
  meant. A client never sees the marker: every task they can reach is visible to them.

### Client reporting

- **A report that stopped generating said "bezig met genereren" for ever**, and there were six
  separate ways to get there. The screen never asked again after the first load, so a run that
  finished forty seconds later still read as busy; a healthy run was being killed at five minutes
  by a background-worker default, through a path that skipped the module's own failure handling;
  a second attempt within the hour queued nothing at all and left the row claiming a worker had
  it; the job was queued before its own record was saved, so the worker could find nothing; and
  the code that records a failure was itself broken, so ordinary failures never wrote a status
  either.
- All six are fixed, and there is now a sweep that clears runs nobody is working on any more —
  because a worker that is killed outright runs no error handler, so the only possible answer
  lives outside the process. **Reports stuck on "generating" today are cleared by the first tick
  after the upgrade**, without a hand-written correction.
- A narrative that times out now yields a report with all of its numbers and a warning, rather
  than nothing.

### Cloudflare (#278)

- **A token refused for being used from the wrong place read as an invalid token.** Cloudflare
  lets you restrict a token to a list of IP addresses; asked from anywhere else it refuses every
  endpoint with a numbered code that says exactly that. The message shown was the one sentence
  that cannot lead to the fix — check that the token is valid and has the required permissions —
  while the token was valid and the permissions were right. Cloudflare's own code is now read
  before the status that carried it, so a refusal it named is reported by name.
- **Instellingen → Cloudflare lists your Pages projects**, with the sites matched to each. They
  had only ever appeared as a number on a banner the next click throws away, so "did the sync
  find my projects, and are my sites attached to them?" — two different questions — was
  answerable on no screen.
- **The two findings that matter before a zone exists were computed and then dropped.** A domain
  marked as a redirect here with nothing behind it at Cloudflare, and the same name in two
  accounts, were both worked out by the server and discarded by the panel, which only drew its
  findings box for an already-connected zone.
- **A refused permission stopped costing a redirect that had already been made.** Saving a
  domain-wide redirect writes a rule and a DNS record, and those are two different token
  permissions. A token allowed the first and not the second failed the whole request *after* the
  rule existed at Cloudflare, and the rollback erased the only record of it — so the panel showed
  no redirect, the next press added a second rule to a live client's zone, and the one after that
  a third. The DNS placeholder is its own step now, its refusal is a note on the row in
  Cloudflare's own words, and a retry updates the rule it already knows about.
- **A missing permission is no longer reported as a rejected credential.** They had been
  collapsed into one message, which told an administrator the one thing that had not happened.
  "Wat dit token mag" also checks the two permissions the redirect button actually uses, which
  were the two it never mentioned.
- **"Nameservers wijzen nog niet naar Cloudflare" is only said when it is known.** A lookup that
  timed out and a domain that genuinely delegates nowhere were indistinguishable, so every way of
  not knowing rendered as an instruction to go and change something that was very possibly
  already correct.

### Contacts (#310)

- **An employee can add and edit contact persons again.** Managing the people at a client is
  ordinary day-to-day work, and the `Medewerker` role held neither permission for it, so adding a
  new marketing contact at a client was refused.
- **And granting the permission that says so now actually works.** Creating a contact *at a
  client* attaches it in the same breath, and attaching is a second permission that no label on
  any of those screens named — so the picker, the contactpersonen panel and the link, unlink and
  primary-contact controls were all drawn behind the first key while calling the second. Both are
  a `Medewerker` default now, deleting a contact stays with administrators, and every attaching
  control mirrors the permission it really calls: a tenant who takes the default away gets a
  read-only panel instead of a refusal.

### Screens, charts and uploads

- **Every screen has a measure now.** `<main>` was told to fill whatever it was given, so on a
  3178px display a settings card was 1430px of box around one sentence, a client list put a row's
  name and its status a metre apart while truncating both, and a dashboard tile stranded a name
  and its number at opposite ends of the screen. Past a point, wider is not more readable, it is
  further to look. A laptop is untouched, a 1920px desktop loses 16px a side, and only genuinely
  wide monitors gain margin.
- **A chart on a wide screen was a chart with the zoom stuck on.** The trend chart and the
  revenue comparison were drawn at a fixed size and scaled to the container, which magnifies
  everything inside — on a 3178px screen that meant one chart taller than the fold with 59px axis
  labels, and on a phone the same labels at 6px. Both now measure the space they are given and
  draw into it, so 10px type is 10px type at every width and a bar stays a proportion of its slot.
  Verified at 390, 1280, 1920 and 3178px.
- **Every upload takes a dropped file.** Eleven controls were click-to-browse and nothing else,
  which is not the gesture people reach for first: an attachment, a client logo, a spreadsheet and
  a saved e-mail all arrive by being dragged out of a mail client or a folder. Dropping is an
  accelerator and never the only way — the button underneath still works, a clearly wrong file
  type is refused the way the file dialog refuses it, and each control says that it takes a drop.
  Four of them were also unreachable by keyboard on the way past, and are not any more.
- **Two forms asked for the narrowing field after the fields it narrows.** The task form put the
  project picker above the client and the client below the assignee, so both fields the client
  filters were answered first and then visibly shrank. The employment wizard drew the week grid
  above the checkbox deciding whether the grid may be filled in at all.
- **A task created from a contact-moment picker showed as "—".** The inline create answered with
  the task's identity and its links but never its title, so the one option the user had just made
  was the one they could not identify.

### Housekeeping

- **The API test suite runs as four parallel shards**, thirty-two minutes down to under ten. It
  had grown long enough that back-to-back pushes cancelled it before it finished, so it reported
  on almost nothing: a test had been failing for three days without a word, and that test is the
  first thing the split found. Every commit still runs every test. Linting moved out in front, on
  its own runner and with no database, because a red build here is nearly always lint drift and
  that answer used to arrive last.

### Upgrade notes

- **Three migrations, all additive.** One column on reports, two new tables for push devices, and
  one that writes the browser-push defaults into every organisation that already exists. Nothing
  is dropped, renamed or retyped, and rolling the image back is safe.
- **Quiet hours now do something.** If your organisation had filled in a quiet window, e-mail and
  chat notifications that previously went out inside it will now be held until it ends. The in-app
  bell is unaffected. The window is in Instellingen → Meldingen.
- **Nobody is pushed until they allow their browser**, per person and per device, so an instance
  that upgrades and where nobody does behaves exactly as it did. The upgrade does write the
  defaults into your organisation's notification settings — the urgent events on, the digest
  events off — so the first person who allows their browser receives those events rather than
  silence. It is written as your organisation's own setting, editable in Instellingen →
  Meldingen, and it never overwrites a choice already made there.
- **`Medewerker` gains two contact permissions on startup** — creating and editing contact
  persons, and attaching them to clients. Deleting a contact is unchanged and stays with
  administrators. If you deliberately want an employee not to touch contacts, untick them again
  after the upgrade.
- **Reports stuck on "bezig met genereren" clear themselves** within fifteen minutes of the
  upgrade.

## v0.23.0 — 2026-08-07

A link on the invoice that a client without an account can actually open, invoices by the
handful, the report designer, and the logo that had quietly stopped printing on everything.

### Paying an invoice from the paper

- **The QR code on an invoice now leads somewhere a stranger can go.** It pointed at the client
  portal, which is a product an agency buys per client — so for the clients who hold no login,
  and that is most of them, the code on the paper opened a sign-in screen they could never get
  past. An issued invoice now carries its own address, and whoever holds it opens that one
  document, with its pay button, with no account and no session (#268, #269).
- **What the link grants is what handing somebody the paper already grants**: read this invoice,
  download it, pay what it owes. Nothing else. The page is built out of the client-portal
  machinery rather than a new kind of login, so the seller's bank details, the price list, the
  template library and the org-wide unbilled backlog are refused by exactly the code that
  refuses them to a signed-in client, and a draft is invisible (#266).
- The page carries no menu — a visitor would find every door locked — works with JavaScript
  switched off, and is served `noindex` and `no-referrer`, because the address *is* the
  credential and the very next thing a payer does is leave for a payment provider.
- **The off switch is retroactive.** Instellingen → Facturatie decides whether these links work
  at all, and the setting is read before the address is, so unticking it withdraws links already
  printed and posted. It is on after the upgrade; see the upgrade notes.
- Staff see the link on the invoice screen, so it can be read out when a client rings up. A
  client's own session never shows it.
- **Coming back from a checkout no longer says "open".** Mollie confirms out of band and makes
  no promise about arriving before the browser does, so a payer who had just paid landed on the
  word for "unpaid". The invoice's own page now asks once when it loads and follows a payment
  that is still settling, and the signed-in screen says "we are confirming this payment" rather
  than showing a status that has been overtaken.

### The QR code, and the line beside it

- **There is a colour picker now, and a preview that proves the rule.** The previous release
  shipped no field on the grounds that a hex box invites an invoice nobody's phone can read.
  Right about the danger, wrong about what was preventing it: the guarantee was always the
  substitution, and what was missing was any way to *see* it happen. So both colours, the logo
  (yours, none, or an upload) and the caption are yours to set, the editor draws the real code
  from what you have not saved yet, and it says in words when a combination was replaced.
- The readability rule got stricter while the controls got looser: the two colours are judged
  and replaced **as a pair**, because half-correcting produces a code that passes a contrast
  check and still loses the camera, and a light-on-dark code is refused outright — it measures
  beautifully and scans worse everywhere.
- **One appearance, read by the document, the mail and the preview alike.** They disagreed: a
  template set to plain printed a black code on paper and mailed a coloured one.
- **The "pay online" block prints two things and now switches them separately** — the clickable
  wording, and the address spelled out underneath. That address used to be inert and meaningless;
  it is a credential now, readable over a shoulder, in a photocopy, in any screenshot of the
  invoice. An agency that wants the click without publishing the address in 8pt no longer has to
  turn the whole block off. Both default on, and a template written before the change prints
  exactly what it printed yesterday.

### Invoicing

- **Tick a run of invoices and download them as one zip** (#307). The selection mode on the
  invoices list could only delete; the other thing an agency does with a quarter's invoices is
  hand them over. Each entry is the same document the single download gives you — one renderer,
  pinned byte-for-byte by a test — filed under its own number. Capped at fifty, which is the
  page size, so "tick the page, download it" fits exactly and the control says so instead of
  quietly taking the first fifty of a hundred and twenty.
- It is a download, not an action: an expired licence makes a module read-only, and filing your
  own paperwork is not the thing to lock somebody out of.
- Rendering fifty invoices costs what rendering one costs, per invoice: the seller block, the
  branding and the logo are read once for the batch rather than three round trips per document.

### Client reporting

- **Every chart was in the document and none of them were on the page.** A percentage width on
  an SVG is the idiom every browser honours and the print engine does not resolve, so the
  preview drew four charts and the PDF the client received drew zero — the exact preview/print
  drift the shared renderer exists to prevent. Fixed, and pinned by a test that walks the
  rendered pages and demands the chart have area, rather than checking the markup, which is the
  one place the bug was not.
- Three things only became visible once charts printed: category labels ran through each other,
  the label strip was sized for horizontal text so rotated names ran out of the chart and
  through the legend, and a chart left to fill the column took half a page for five bars. All
  three corrected, and where the table is narrow the chart now sits beside it.
- **Six column headings reached the client as MARKETING.METRIC.DELTA** — derived and split
  metrics that no screen but the report itself emits, so nothing ever showed them. Named, in
  both languages.
- **The whole run could die on one line.** Every section came back "could not be built" and a
  client with two connected sources was reported as having none: the shared gather step keyed
  its cache on an object Python refuses to hash, and the per-section safety net turned one type
  error into eight identical warnings and an empty document. Fixed, plus the test that was
  missing — one that gathers real sections from real stored figures and asserts the numbers.
- **The design editor**, which the module shipped without: which design, the cover photo, which
  sections print, the body and its stylesheet, beside a live preview. The preview renders
  through the same path the PDF does, against your own most recent report — or, before the first
  one exists, a sample document under the real section headings.
- **A design you could store, never reach, and lose by renaming.** Instellingen → Rapportage drew
  four of a template's ten fields and posted the other six as empty anyway, so renaming a
  template — or ticking "default" — silently threw away a custom design. And "start from the
  standard design" now exists, so authoring no longer means writing against a template context
  you had to learn by heart.
- Alternating section bands, because "separated by air" holds for three sections and a client
  report runs to nine; and a row's colour dot is now its own segment in the chart above it,
  where before a six-colour chart sat above a table repeating none of them.
- **A batch that ran for nobody says so.** "Generate this period" answered `0` on an instance
  where clients had live data sources but nobody had been enrolled yet — technically correct
  and indistinguishable from a broken button. It now says how many clients are enrolled, how
  many are ready to be, and where to do it.
- The internal analysis reads the same sections the client document does — traffic, rankings,
  conversions, referral, social — instead of only the site audit. The two documents describe the
  same month from the same figures; what makes them different is the question asked of them. The
  audit stays internal, because a list of somebody's technical faults is working material.
- The house voice is seeded by the scheduled run too, not only by somebody opening the settings
  screen — the first report is the one the feature gets judged by.

### Every branded document has been printing without its logo

- The loader every document uses built its storage path the way it did before files were
  de-duplicated, so it had been reading nothing back: the agency logo on invoices, quotes and
  every branded outgoing mail, the letterhead mark, the client's logo and the cover image on a
  report — all of them silently falling back. Nothing caught it because "branding must never be
  able to fail an invoice" is the correct rule and is exactly what hid this. The test that comes
  with the fix uploads through the real route and reads back through the real loader, because a
  fake keyed on whatever the loader asks for would have agreed with itself and proved nothing.

### Domains and websites

- **A renewal is invoiced on the day the registration lapses**, not on an anniversary of the
  afternoon somebody typed it in. The billing date was derived from the start date, which for
  any portfolio onboarded in bulk was the import date — so a whole register invoiced on the
  wrong day, every year, and re-saving the record could not fix it because nothing ever asked
  the registrar. The connected registers answer now; what they observed is shown beside the date
  you bill on, never silently swapped for it, and the form offers it in one click.
- The date is editable at last — on the form, in a spreadsheet and across a selection — which is
  what makes an already-onboarded register fixable without a migration per agency. Clearing it
  means "work the default out again", never "stop invoicing": that is a decision `invoiceable`
  already carries.
- **Both lists you could not search now have a box.** The domain register and the website list
  were the last two indexes with none, which stopped being a small omission when lists started
  paging: on a register hundreds of names long, the only way to reach the fifty-first was to page
  towards it.
- **One filter bar, on every list.** Each screen had grown its own copy of the same dozen lines,
  and copies drift: the domains list read a search parameter it never drew a box for, and one
  list's "wissen" cleared three hand-named filters and would have left a fourth behind. Below
  phone width the filters now collapse behind one toggle carrying a count, while the actions stay
  put.
- The domain and website cards on a client's page are the first page of their list, with the true
  total beside it — where before each card ran an unbounded query to draw five rows and threw the
  rest away.

### Cloudflare

- **A valid token read "Token problem" beside a zone list that was filling in perfectly.** The
  account-owned tokens an agency mints — the kind that do not leave with whoever created them —
  are refused by the endpoint that checks user tokens and work everywhere else. That endpoint was
  asked first and its opinion was treated as a verdict on the whole credential. Every check now
  fails softly, a read that succeeds outranks a check that refuses, and only a token refused
  everywhere is called invalid.
- **And a red line that nothing could clear.** Nothing but a manual re-verify took an account out
  of the error state, so a credential that was fixed — or was never broken — kept its warning
  through every sync that plainly worked. A successful read clears it, and a missing scope is now
  reported as the lesser news it is rather than as a broken token.
- **A failed sync says why.** The note explaining the failure was written and then rolled back by
  the very error it described, so the settings screen — whose whole job is to say what is wrong
  with a credential — was the one place that never found out.
- A Cloudflare account reached by an account-owned token had a permanently blank Pages panel, and
  it read as "no Pages projects" rather than "never asked". The sync fills in what it needs when
  exactly one answer exists, and says so instead of guessing when there are several.

### Import & export

- **A wizard that only recognised its own words is no wizard.** A domain register kept by hand —
  Dutch headers, "Status", "Parked" — failed on all 154 of its rows, and two of the three reasons
  were ours: a column named exactly like one of ours was not suggested for it, and a dropdown
  value was compared byte for byte while a yes/no two lines away already understood "Ja". Both now
  follow the app's own translations in every language it ships, so "Parked", "ACTIVE" and
  "Geparkeerd" all land in the right column. It forgives spelling, never vocabulary: a value that
  is no option at all is still a row error.

### Marketing

- **SE Ranking had a connector and no front door.** The adapter, the agency key and the report
  sections all shipped last release, and there was no way to link a project from the interface at
  all. The picker offers it now, and knows that "not configured" means a different thing per
  source — reconnecting a Google account fixes neither a missing developer token nor a missing
  agency key, so it is no longer offered as the cure.

### Projects, time and lists

- **A project can be attached to a client.** The edit form on a project's page had no client
  field and never posted one, so a project made from the projects list — which creates the record
  and drops you straight into editing it — could never be attached to anybody, and one filed to
  the wrong client at creation stayed there.
- **And now it must be.** A project is work done for somebody: the budget, the hours it prices,
  the invoice those land on and the panel it appears under all read the client. Creating one
  without a client is refused, an existing project can be **moved** between clients but never
  orphaned, and the button on the list asks the single question it must before creating —
  prefilled from the filter you were already looking at, with the usual ＋ for a client that does
  not exist yet. Projects that predate the rule keep their empty client and are fixed by picking
  one; nothing is invented for them, and nothing else about them changes.
- **Logging a time entry no longer blanks the date you were logging.** Saving reset the whole
  form, and a browser reset empties a field rather than restoring it — so the second entry of an
  afternoon began by asking which day it was again. The entry's own fields are cleared for the
  next one; the day, the client, the project and the kind of work stay where you put them.
- **On a narrow screen, the column a table dropped was the name.** Measured at 820px: the
  identity column — the only cell that links out of the row — was allotted zero width while every
  optional column beside it kept its own, so the list scrolled sideways with the name nowhere on
  it. The floor that was supposed to prevent this was expressed in a way a fixed table layout
  ignores entirely. Every list gets its name column back, and an honest sideways scroll; wide
  screens are unchanged.

### Site and housekeeping

- Vendor-supplied marks for Mollie and SnelStart, a path for vendors who publish no vector at
  all, and a fix for the one place the logos never reached — the home page, where the first thing
  a visitor sees kept drawing monograms after the real marks landed. A build check now fails on a
  mark rendered without one, because a silent fallback that looks deliberate needs a check rather
  than more care.
- The commercial boundary is enforced rather than remembered: one script checks, in both
  directions, that every licensed module marks its own directories and that the licence lists
  exactly those. It had drifted three ways at once by the time anybody looked — all of them
  invisible in a diff and none of them caught by a test.

### Upgrade notes

- **Two migrations.** One adds columns only. The other is a one-off correction to domain renewal
  dates, guarded four ways: only where a registrar has actually answered, only forward in time
  (a lapsed registration is a thing to look at, not a date to bill on), only where no invoice has
  already claimed the period, and only where the date really changes. An instance with no
  registrar connected matches nothing and every existing date stands.
- **Public invoice links are on after the upgrade**, which is what makes the QR on an invoice
  work. Only invoices issued from now on carry one, and Instellingen → Facturatie turns the whole
  thing off — retroactively, including links already printed.
- **A project now requires a client.** Existing projects without one keep working and are not
  changed; they are refused only when saved, until a client is picked. Spreadsheet imports and
  bulk edits follow the same rule, and both name the row and the column rather than failing the
  file.
- Nothing is dropped, renamed or retyped, so rolling the image back is safe.

## v0.22.0 — 2026-08-06

Online payments, a monthly client report, de-duplicated file storage, and paging on every list.

### Online payments

- **A client can now pay an invoice online, and the payment books itself.** Connect a Mollie
  account under Instellingen, and an open invoice offers a hosted checkout with whatever methods
  that account has enabled. What comes back is an ordinary payment on the
  invoice, so the outstanding amount, the status flip, the reminder schedule and the accounting
  export all behave exactly as they do for a payment a bookkeeper enters by hand. Nothing in
  invoicing knows a provider was involved.
- **The provider is a seam, not the feature.** Mollie is the first implementation and Stripe or
  Adyen are a package rather than a rewrite — worth the one file it costs today, because the
  alternative is rebuilding the settle path at the moment a live customer depends on it.
- **Four ways in, one door.** The portal button, the invoice mail, the reminder and the QR on the
  document all lead to the invoice's own page in the client portal, never to a provider's checkout
  URL. A checkout URL is a bearer credential on paper, it expires in fifteen minutes while the
  invoice does not, and mailing one alongside the portal button would give a client two valid ways
  to settle one debt. The portal starts the checkout at the moment the client presses.
- **The mail's pay button is deliberately stricter than the document's link**: no button unless a
  provider is connected and the invoice can actually be collected. An installation with no
  provider connected sends byte-for-byte the mail it sent before.
- **The QR carries your own branding.** Your accent colour in the modules, your logo in the
  middle, on the document and in the mail alike. It stays scannable by construction rather than by
  preview: a logo raises the error correction, it covers at most a fifth of the code, a clean patch
  sits behind it, and an accent too pale to read against white is printed near-black instead. There
  is deliberately no field to type a QR colour into.
- A payment that never reported back repairs itself: an hourly check per organisation, plus a
  "check status" button on the invoice. A **test**-mode key follows the whole loop and settles
  nothing, so an account left on a test key produces an obviously stuck screen rather than quietly
  wrong revenue.
- Starting a checkout is its own permission (`invoicing.payment.link`) rather than the one that
  registers a payment: pressing "pay" settles nothing, while recording a payment is a bookkeeping
  claim.
- **This has not been run against a live Mollie account.** It is built from Mollie's published API
  documentation and tested against fakes cut from that same document, which proves the code agrees
  with the documentation and not with the provider. `docs/MOLLIE.md` lists what to check the day
  you connect a real key.

### Client reporting

- **The monthly client report is a record, not a job output.** A new licensed module: one report
  per client per month, drafted, reviewed, published and sent. Every number the document prints is
  frozen on the report itself, which is what lets you reopen last March and see what was actually
  sent, keeps the prose and the tables describing the same figures, and makes a re-run update the
  document instead of mailing the client a second copy.
- **Review is the default and auto-send is a choice you make per client.** The review screen puts
  the editable prose on the left, one paragraph per section in print order, each with its own
  rewrite that regenerates only that section; on the right is the document itself, the same
  artefact the PDF prints. A paragraph you edited by hand is marked as such and survives a
  regenerate.
- **The voice is yours.** Your editorial rules — first person plural, the words you never use, no
  advice in the client's document — live in a tone of voice you can read and change, not compiled
  into the product. A banned phrase is checked after the text is generated rather than merely
  requested, and lands on the report's warnings, which the agency sees and the client never does.
  What is true about one customer lives on that customer, alongside their recipients and their
  schedule.
- **Sections come from the modules that own the data.** Marketing contributes traffic, search
  engines, rankings, referral, social, conversions and AI-search visibility, plus a site audit that
  stays internal. Adding a chapter is a change where the numbers live.
- **A dashboard stops being a table on the other twenty-nine days of the month.** The insight
  behind a monthly report is that a client cannot read an analytics table but can read the
  sentence. That sentence now also appears on the marketing panel, the marketing tab and the
  portal widget — the latest published report's paragraph for the section being drawn, dated, so
  it never pretends to describe today. With reporting switched off or unlicensed, every one of
  those screens renders exactly as before.
- A client sees exactly their own published, client-facing reports: never a draft, never an
  internal one, never another company's, on the list, the totals, the detail, the PDF and the
  attachments alike.
- Invoices, quotes and reports are now printed by one shared renderer instead of one per module.
  Charts are inline vector rather than an image fetched from somewhere, so a client's figures never
  travel in a URL.
- The permission that gated the cross-client marketing overview is renamed to say so
  (`marketing.overview.read`). It is applied automatically on upgrade and grants nobody anything
  new.

### Marketing

- **SE Ranking joins Google Analytics, Search Console and Ads as a source.** Rankings, the site
  audit and AI-search visibility — most of what a monthly client report is made of. It
  authenticates with one organisation-wide API key rather than a per-user Google sign-in, so it is
  configured or it is not; nobody is told to "reconnect your Google account" because an agency key
  is missing.
- Written against the live API rather than from memory, which caught three things a plausible
  implementation gets wrong and each of which fails silently: positions are answered per search
  engine, so a client tracking two engines would have reported half its keywords; position zero
  means "not ranking" and averaged in reports a better position the worse a client does; and audit
  findings are keyed by check code, so the obvious parse finds nothing and hands you a clean site.

### Contactmomenten

- **An e-mail keeps its formatting.** An HTML message was stripped to bare words — every list,
  heading, link, emphasis and quote level gone — and drawn as pre-wrapped text. The message's own
  HTML is now converted at the moment it arrives and rendered as it was written. Only mail that
  actually had an HTML part is converted: text a sender typed stays the text they typed, so nobody's
  asterisks silently become italics.
- **A signature logo is part of the letter, not a paperclip.** An embedded image now renders in the
  body and leaves the attachment list, where it used to appear as a chip on every mail that sender
  ever sent. Remote images are dropped to their alt text, because a tracking pixel is an image and
  drawing one reports back that you opened the mail.
- **A logged call, meeting or note now shows what was written in it.** The timeline previewed
  e-mails and nothing else: a phone call you had typed a paragraph into drew its title, its time
  and then blank space, so the one thing worth reading was invisible until you opened the row.
  Every contactmoment now carries a teaser of its own notes, on the record's timeline and in the
  Interacties list alike — including the ones already logged, without anything having to be
  converted.
- **The subject is no longer required.** A call is titled by what it *is*, and every screen already
  fell back to the kind ("Telefoongesprek") when there was no subject — Gmail and uploaded e-mails
  have always arrived without one. Demanding a subject meant inventing a title before you could
  write down what was said. Leave it empty and the field shows the title the row will carry; an
  existing one can be cleared the same way.
- **Opening a folded conversation and clicking an older message now works on the first click.** It
  used to flicker, stay shut, and open on the second try.
- **An e-mail waiting for review no longer defaults to your own company.** Most agencies keep
  themselves in their own client list — it is where their own domains, hosting and invoices hang —
  with their staff and their `info@`/`administratie@` address as contact people on it. Those
  records date from the day the system was set up, so on any thread with a colleague in Cc they
  were matched first and the mail was filed under the agency instead of the customer who actually
  sent it. The customer is now looked for first: your own people and your own company rank last,
  and among the rest the sender of an incoming message — or the addressee of one you sent — counts
  for more than whoever was merely kept in Cc. Nothing to configure: which company is yours is
  worked out from your own data. Mail purely between colleagues, if you log it, still lands on your
  own company as before.

### Lists, selection and paging

- **Every list screen pages instead of showing the first two hundred rows and calling it the
  list.** A client list that had outgrown the cap handed you a prefix that looked exactly like the
  whole answer, with row 201 reachable only by guessing a search term narrow enough to pull it into
  view. The page is in the address, so the back button lands where you left, a page can be sent to a
  colleague, and the scroll position comes back with it. Changing a filter, a search or a sort
  returns you to page one, because page 7 of the old filter is usually nothing at all in the new
  one. How many rows you like is a personal preference saved beside your column layout (50 by
  default; 25, 100 and 200 offered).
- Paged: clients, contacten, domeinen, websites, projecten, taken, facturen, offertes, abonnementen,
  verlof, verlof/team, interacties, meldingen, hosting, the OXXA register and automation runs. Three
  screens are deliberately left whole and it is written down why: the Cloudflare zone inventory, the
  uninvoiced-hours report whose subtotals span the entire set, and the verlof approval queue that
  exists to be emptied.
- **Acting on a selection is a mode now.** Every list carried a checkbox column whether or not
  anyone was selecting, so every reader paid for a writer's feature. Press the pencil in the
  toolbar — always the last control, on every list — and the checkboxes appear together with
  Bewerken and Verwijderen in their own strip above the table. Press it again and the list goes
  back to being a list. A bulk edit is the same edit fifty times: the same validation, the same
  activity line, the same rules. A row that refuses says why, and the other forty-nine still land.
- Draft invoices and mis-logged contactmomenten can be deleted in a batch. Neither can be
  bulk-*edited*: an invoice is money with a lifecycle, and a contactmoment is the record of what was
  said. Selecting a mix of invoices answers, for example, "3 verwijderd, 3 overgeslagen — alleen
  concepten kunnen worden verwijderd".
- **Selecting a run of rows takes one shift-click**, from the row as well as from its box, and
  collapsed sections stay out of it. **The checkbox is no longer a 16-pixel target**: the whole
  column it sits in ticks the row, so being a few pixels off no longer opens the record you were
  trying to select.
- **The tables stopped scrolling sideways.** A declared column width used to be advice only, so the
  interacties grid asked for 1210 pixels, laid out at 1423 and scrolled on any laptop while its
  truncation never appeared. Columns now hold their width, one absorbs the slack, and text
  truncates instead of wrapping to five lines. Rows drop from around 120 pixels to about 55.
- **A picker that builds a list keeps its list open.** Adding three people to a contactmoment cost
  two trips through nowhere: the chip landed, the list vanished, and the only thing that reopened it
  was leaving the field and coming back. Clicking the field now opens the list whether or not it was
  already focused, and the six pickers that add to a list stay open between picks. A single pick
  still closes, because there the pick is the answer.

### Invoicing

- **The mails your clients read are yours to write.** The invoice, the quote and the payment
  reminder join the two sign-in mails in the template editor, so an agency that spent a week on its
  invoice design is no longer dunning in our words. Both delivery paths honour it — the one you
  press and the one on the schedule, which is where every reminder actually comes from — and the
  text is taken in the language of the document it accompanies. Leave a template empty and the
  built-in text is sent, exactly as before.
- **Three fixes to how a document offers to be paid.** The QR printed the full width of the sheet
  in the preview and the PDF alike, pushing the sample to three pages. The printed pay-link address
  was set in the label style, which uppercases — and a URL path is case-sensitive, so the one reader
  it exists for, the one who cannot click it, was being handed a 404. And the QR and the pay-online
  line now sit inside the payment box under the bank details, a rule between them: transfer above,
  one gesture below, rather than centimetres apart across the page.

### Files and storage

- **The same file is stored once.** A signature logo arriving on five hundred e-mails was five
  hundred objects, and so was the PDF a client sends twice or a logo re-uploaded after a crop that
  changed nothing. Identical content now shares one stored object per organisation — never across
  organisations, because ending a tenancy deletes its storage outright and that must never reach
  another tenant's bytes.
- Deleting a file is immediate and no longer waits on the storage backend. A nightly job per
  organisation folds older files into the shared shape and reclaims genuinely unreferenced content a
  grace period later, so "I deleted the wrong file" stays a support question rather than a restore.
  Nothing about reading or serving a file changed.

### Writing in two languages

- **One language switcher per screen instead of one per field.** Every translatable field carried
  its own NL/EN toggle, which is right for one field and absurd for a dozen: filling in the English
  column of Instellingen → Navigatie meant flipping fourteen switches by hand, in order. The choice
  is now made once at the top of the page, card or dialog and every field below follows it — on the
  settings dialogs, roles, custom fields, the navigation editor, the mail templates, the invoice
  template editor and the marketing dashboard. It remembers what you chose and opens in your own
  interface language. Nothing about what gets stored changes.

### Domains and Cloudflare

- **A Pages link is checked again instead of frozen at the moment it was made.** A hostname still
  provisioning read "pending" forever, one deleted in Cloudflare's own dashboard still read as
  linked, and a hostname attached there before schakl saw the account was invisible here. A sync now
  discovers what each project actually serves and adopts what matches a domain you hold; a check
  refreshes it. Drift is reported, never quietly resolved — a link the project no longer serves
  keeps its row, with the date it first went missing, because "since when" is the question.
- **A domain served from Pages no longer needs its DNS here.** A Pages hostname belongs to the
  project, not to a zone, and the panel nonetheless hid the whole Pages section unless the domain's
  DNS was managed in Cloudflare — precisely the case the feature exists for. It now says what it can
  and cannot do, and the project picker names the account whenever you hold more than one.
- **The panel says when the check it is showing ran.** "Geen conflicten" from a check in March and
  one from a minute ago were the same sentence. The date comes from the observations the report is
  built from, so a report assembled from nothing does not claim to be fresh.
- **Adding a domain no longer opens with nine radio buttons about invoicing.** Both invoicing
  decisions move into one disclosure that starts closed on a new domain and states, in its heading,
  what the current defaults resolve to. Editing an existing domain still opens it.

### Tasks

- **The schedule picker stopped blanking what you had already typed.** Anyone who started typing
  before the list of schedulable tasks arrived had the field cleared under them — a narrow window on
  a fast connection and an ordinary one on a slow one, and it looked like losing focus rather than a
  reload.

### Deployment and operations

- **A redeploy is no longer an outage.** Every cloud redeploy answered 500 for its whole duration,
  on every page including sign-in: the API was pinned to a single replica that had to stop before
  its replacement started, while the web app stayed up and kept serving straight into nothing. The
  constraint was never "one replica" — it was "one migration at a time", which is now a database
  lock taken by the migration itself. Two API replicas roll over one at a time, the loser of the
  lock waits and then finds the schema already current, and the web app has a health check of its
  own so it only takes traffic once it can actually answer.
- **The API reference is reachable.** Swagger UI, ReDoc and the OpenAPI document sat at the root,
  which no deployment routes to the API, so the reference resolved to the web app's 404 page in
  every installation there has ever been. They are now at `/api/docs`, `/api/redoc` and
  `/api/openapi.json`, which needs no change at your edge. `SCHAKL_API_DOCS_ENABLED=false` removes
  the pages.

### Website and documentation

- **The manual caught up with the product**: 43 pages per language where there were six — one per
  module you can switch on, one per integration worth a guide, and the administration set
  (installing, upgrading, licences, modules, e-mail, storage, single sign-on, two-factor). They are
  written as instructions: where the screen is, the real Dutch field names, the permission keys and
  their defaults, and the thing that will otherwise bite you. Where the product has never met a live
  credential, the page says so.
- **The public site describes what actually ships**: 25 feature pages where there were eight
  cards, an integrations page covering 22 connections in nine categories, and five more animated
  recreations of the real screens. Every integration carries an honest status — connected and
  exercised, shipping but never yet run against a live credential from that vendor, or planned —
  because selling the middle one as available would be a claim the repository itself contradicts.
- The content editor can sign in with GitHub, the site's own canonical addresses were corrected to
  `schakl.dev`, and four new checks now fail the build for the four ways this site used to break
  without anyone noticing.

### Upgrade notes

- **Seven migrations, all additive.** A self-hosted installation upgrades itself unattended as
  usual; nothing is dropped, renamed or retyped.
- **Cloud operators only**: the two-replica rolling update and the web health check depend on this
  image. Do not apply them against an older tag — see `docs/DEPLOY.md`, "Rolling updates".
- **Rolling back after file de-duplication has one caveat**: the previous release's delete removes
  the stored bytes outright, which after this upgrade may be shared with other files. Reading and
  serving are unaffected either way. `docs/STORAGE.md` has the detail.
- If you have automation or bookmarks pointing at `/docs`, `/redoc` or `/openapi.json` on the API,
  they move under `/api/`.
- Reporting and Mollie are new licensed modules and are off until enabled. The renamed marketing
  permission is rewritten for you on first start; roles and API keys keep exactly what they held.

## v0.21.0 — 2026-08-05

### Invoicing

- **An invoice is now drawn once, not twice.** The document on screen and the document in the
  client's inbox were built by two separate pieces of code that each had to be changed in step,
  and the failure that shape eventually produces is a client reading one invoice on screen and a
  different one in their mail. There is now a single document: the preview shows exactly the page
  that gets printed to PDF.
- **Invoice and quote templates are editable.** A template decides which blocks appear, in what
  order, and with which fields, in a visual editor — with your own wording on the letterhead and
  your own colour on its rules. Blocks the law requires (the number, the date, the VAT
  breakdown, the reverse-charge notice) can be moved but not switched off. A saved layout records
  only your *changes*, so fields added by a later release still show up rather than being
  invisible to everyone who ever saved a template.
- **Editing a draft invoice no longer causes the same work to be billed twice.** An invoice line
  did not record what it billed, and saving the editor rewrites all the lines — so opening an
  invoice the automatic run had prepared, changing a single word and saving it released the
  underlying claim, and that client was billed again for the same period the same month. Hours
  had the mirror-image problem: removing an hours line left those hours marked invoiced with no
  invoice billing them, so they could not be billed again without a database edit. Both are
  fixed, and a line now carries its own record of what it covers.
- **A credit note now actually corrects the balance.** Crediting produced a document and changed
  nothing else, so a fully credited invoice stayed open, stayed overdue, and kept receiving
  payment reminders for money the client no longer owed. The credit note could not be settled
  either, so a refund you had already paid out still read as due. Crediting also **hands the
  work back**: the hours, the agreement's month and the domain's year are released, so you can
  bill them again correctly — which is usually the whole reason for crediting.
- **Domain renewals are their own kind of line.** A register of forty domains renewing across the
  year is reconciled line by line against the registrar's own invoice, and it used to sit in the
  same band as your hosting retainers. Domains now have their own section in the editor, their
  own tab in the "still to invoice" picker, and their own band on the document. Invoices written
  before this release keep the shape they had, so a document a client has already read does not
  change.
- **"Nog te factureren" now covers everything owed** rather than only part of it.
- Smaller ones: the VAT split is stated once instead of twice, an amount field steps by one euro
  rather than by a cent, the framed document reloads when the document changes, and two dead ends
  in the template editor are gone.

### Domains, DNS and registrars

- **Cloudflare integration.** A domain marked as a redirect finally has something behind it: a
  real Cloudflare redirect rule that schakl creates, reads back, and can tell you has been changed
  in the Cloudflare dashboard since. DNS records can be viewed and exported, and Cloudflare Pages
  projects can be linked to a client's domain. Credentials are stored per Cloudflare account
  rather than one per installation, because an agency has its own account and its clients bring
  theirs — and nothing ever picks an account for you, since a zone created in the wrong account
  cannot be moved, only deleted and rebuilt.
- **OXXA registrar integration.** Read the register, see which domains you actually hold, and push
  a nameserver change — which is what finishes "connect this domain to Cloudflare". Built against
  OXXA's official API documentation. **It has not yet been run against a live OXXA account**,
  because no credentials were available; `docs/OXXA.md` lists what to check first when you connect
  one.
- **A domain can now say whether it should be invoiced at all**, and the register can answer for
  it. An agency's domain list mixes names you registered and renew for the client with names the
  client registered themselves and merely asked you to point somewhere — and only the registrar's
  register can tell those apart. Each domain is "yes", "no", or "follow the register". Only a
  register that has actually been read can narrow what gets invoiced, so **an installation that
  is already invoicing domains bills exactly what it did before** until you connect one.
- Anything an integration mirrors from outside now stores *what it decided* separately from *what
  it last saw*, so a change somebody made in the provider's own dashboard is reported as a
  difference instead of being silently overwritten.

### Contactmomenten

- **A contactmoment can name everyone who was in it.** It could name exactly one person, so a call
  that reached two of them was either logged twice or logged with one of them picked as the
  winner — and the other person's own page quietly left it out. Attendees are now chips with a
  type-ahead on every screen that writes one, with one of them marked as the lead so lists still
  have a name to print and sort by. Filtering by a person finds the moments they were *in*, not
  just the ones they led, and the Google Calendar mirror follows.
- **Review a whole selection at once.** The Gmail review queue cost one open-read-approve round
  trip per row, so a morning's forty e-mails was forty dialogs — which in practice meant the queue
  was not reviewed, it was abandoned. Select rows and approve, file or reject them together.
  Approving does not touch the links, so every row keeps the client, project or task the Gmail
  match gave it.

### AI and voice

- **Speak a time entry instead of typing it.** Your browser records, your own speech provider
  transcribes, and the transcript lands in the quick-add field for you to read and correct before
  anything is parsed or saved. Nothing is saved automatically, and the audio goes only to the
  service your organisation configured. Speech is its **own** setting rather than reusing the
  chat provider, because Anthropic — this product's default — has no transcription service at
  all, so "reuse the chat provider" would configure nothing.
- **Quick add stopped throwing away part of what you typed.** Saying "niet declarabel" or "half
  uur pauze" or naming an entry type was silently discarded and the entry landed on the form's
  defaults. Those three are now understood. Quick add is also faster and fills the form
  immediately instead of after a page reload.

### The client portal, and who can see what

- **Clients can see and download their own invoices.** A portal login lists and opens only its own
  companies' *issued* invoices — never a draft, never another company's — with the status
  including overdue, and downloads the same PDF you would.
- **The portal is now a module of its own**, with its own licence, rather than living inside
  contacts. Whether a client login stays restricted to its own company never depends on the
  licence — only inviting *new* people does.
- **Signing in as a client's contact person no longer refuses when it should simply do less.**
  Giving clients invoice access meant staff who had been signing in as a client suddenly could
  not, because the check demanded they already hold everything the client does. The session is
  now capped to what the staff member holds instead of refused outright. Every trail records who
  was really acting.
- **A colleague limited to a portfolio of clients is now limited everywhere.** The restriction
  only worked where a record pointed at a client directly, so contacts, websites, counts, activity
  trails and attached files still showed the whole organisation. All four gaps are closed.

### Import and export

- **Twelve kinds of record travel by spreadsheet where six did** — domains, websites, hosting and
  both rate cards join the list, along with the subscription fields that were missing.
- **Export and import now sit on every list that has them**, next to the column picker, rather
  than only in Instellingen.

### Security



- **A sign-in now belongs to the organisation you signed in to.** The account list is shared by
  the whole installation and the password check never looked at which organisation the address
  was typed on, so on an installation running **more than one** organisation the sign-in screen
  of organisation A accepted a member of organisation B and gave them a real session on A's
  address. They could not read A's data — every screen refused them — but the session existed,
  and no boundary should depend on every future screen remembering to refuse. The address is
  now looked up **within the organisation being signed in to**: someone else's credentials
  answer exactly like a wrong password, and a password-reset or verification mail can no longer
  be triggered from an organisation the account has nothing to do with. The session itself
  names its organisation and is not a session anywhere else — not even for someone who is a
  member of both. A single-organisation installation is unaffected in every respect but one:
  **everyone signs in again once** after this upgrade, because sessions issued before it name
  no organisation.
- **Single sign-on now proves the browser that started the sign-in finished it** (PKCE, RFC
  7636). The one-time code the identity provider hands back travelled the redirect on its own,
  so anything that could observe that address — a proxy log, browser history, an extension, a
  redirect URI registered a little too loosely at the provider — could have redeemed it. Every
  sign-in now carries a challenge whose answer never leaves the server. Nothing to configure;
  providers that ignore it behave exactly as before.
- **Removing someone who signs in with SSO now sticks.** "Create a membership on first sign-in"
  was really "create one whenever there isn't one", so taking a person out of Instellingen →
  Gebruikers lasted until their next sign-in and then quietly undid itself — with the removal
  sitting on the audit trail. An organisation now remembers that it once admitted an account,
  and only a genuine first sign-in provisions. Restoring access is a deliberate act again: add
  the membership back, with the role you mean. Someone signing in at a *different* organisation
  for the first time is still provisioned there. Existing memberships were all counted as
  "already admitted", so nobody is re-provisioned by the upgrade itself.
- **Someone the identity provider knows but this organisation does not is now refused**, with
  *"Your account is not a member of this organisation"* on the sign-in screen, instead of being
  handed a session every screen then had to turn away. The sign-in screen also finally shows
  why a federated attempt bounced back to it; before, it silently redisplayed the form.
- **A colleague scoped to a portfolio of clients no longer sees other clients' people named in
  their e-mail and notes.** Where one part of the app has to *name* a record another part owns
  — the contact behind a participant address on a logged e-mail, the person behind an @mention
  in a note — the lookup only checked the organisation, not the client portfolio the reader is
  limited to. A contact belongs to its client through a link table rather than a column, so the
  restriction had nothing to match on and did nothing at all. Those lookups now go through one
  shared route that asks the owning part of the app, so a portfolio restriction applies to a
  borrowed name exactly as it does to the record itself: an address outside the portfolio reads
  as a plain address, and an @mention of a person outside it is dropped on save.

### Fixed

- **The "your domain is not working" mail now says which record is wrong, and reaches only
  administrators** (#291). It listed no evidence at all: an admin was told the hostname, the
  certificate *or* the DNS needed attention and had to go and find out which. The daily sweep
  already knew — it decides the verdict from a per-layer check — so the mail now carries that
  reasoning: the records the domain needs, the value each must hold, what DNS answers instead,
  and every failing layer's own explanation, in the same strings the settings screen renders.
  Links point at the organization's slug host, since the custom domain is exactly what may not
  be answering. Recipients are the people who can act on it — holders of `settings.domain.write`
  or the owner wildcard — and never an external login: a client-role or portal account is
  excluded even where a misconfigured role granted it the permission. The mail names its own
  recipients, so an admin can see a colleague already has it. Finally, a problem is remembered
  as reported only once a mail actually went out: an organization whose administrators are all
  inactive, or whose e-mail transport is down, is alerted again tomorrow instead of having its
  outage silently marked as handled.
- **Dates, deadlines and scheduled mail now follow your organisation's own timezone.** Three
  places still assumed Amsterdam and three more assumed UTC, so on an installation configured for
  another zone a budget period rolled over at the wrong midnight and hours landed in the wrong
  month, "due today" named the wrong day for recurring tasks and reminders, and the daily digest
  went out an hour early in Lisbon and an hour late in Warsaw.
- **Listing a client's contacts sorted by name no longer fails** with a server error.
- **The contact list groups people under their client**, rather than presenting one flat list.
- **A cancelled Google Calendar meeting is mirrored as cancelled** instead of disappearing.
- **A Google permission error now says which one it is**, instead of "try reconnecting" for every
  possible cause, and connecting Google returns you to the page you started from.
- **The role editor names its permissions.** Twenty of them were displayed as raw internal keys.
- **"Uren boeken" on a client's page keeps that client** instead of clearing it.
- The all-day row on the calendar lines up with the hour grid beneath it, and the settings
  sidebar follows the three screens that moved out of that section.

### Upgrade notes

- **Everyone signs in again once.** Sessions now name the organisation they were created in, and
  a session from before this release names none, so it is refused. There is nothing to run and
  nothing to configure — people simply sign in again. See the first Security entry for why.
- **No database decision to make.** All thirteen migrations only add tables and nullable columns;
  nothing is dropped or rewritten, so the upgrade runs unattended as usual.
- **Domain invoicing does not change by itself.** Every domain whose invoicing decision has not
  been set keeps billing exactly as it did until you connect a registrar and sync it.

## v0.20.0 — 2026-07-31

### Fixed

- **Cloudflare for SaaS custom domains activate on a Free, Pro or Business zone again** (#293).
  Every custom-hostname request carried `custom_origin_sni`, which is an Enterprise-only
  entitlement, so Cloudflare refused the whole create with *"Access to setting a custom origin SNI
  has not been granted"* and the customer's domain stayed unverified even with correct DNS. The
  field is now sent only when an operator explicitly configures `SCHAKL_CLOUD_CF_ORIGIN_SNI` —
  never derived — and it no longer doubles as the origin server, so an entitled operator's SNI
  rewrite cannot re-route the origin with it. Cloudflare presents the custom origin server's own
  name as SNI by default, which is the value that was being derived anyway, so Full (strict) is
  unaffected. A refusal over a token scope or a plan entitlement now answers
  `errors.cloudflare_not_entitled` instead of the retryable "try again in a moment", and the API
  log names what the operator has to change. A hostname added by hand in the Cloudflare dashboard
  is still adopted by the next verify.
- **An import preview now names the row a bad phone number is on** (#289). Phone numbers were
  only checked once the import was already being written, so a file with one malformed number
  previewed clean and then failed as a whole with no row, no column and nothing to correct —
  leaving the blank cells and the ninety valid numbers looking equally guilty. Phone columns
  are validated with every other column type now, read in the row's own country exactly as
  saving that record would read it, and an empty cell stays what it always was: nothing to
  import, not a rejection.
- **Signing in as a member of another organisation now works from the console** (#288). On a
  cloud installation the console lives on its own address, so the administrator's session was
  simply not present on the organisation's address — and on a customer's own domain it never can
  be. The jump landed on that organisation's login screen instead of opening it. It now crosses
  over a **single-use, two-minute link**: the organisation's address redeems it server-side for
  the session and the grant, everything is re-checked on arrival (the address it was issued for,
  the administrator still holding the right, the organisation's service PIN still standing), and a
  link that was already used — reopened from history, or from a shared screen — refuses and says
  so instead of quietly signing anyone in again. No long-lived credential travels in a URL, a
  stolen grant on its own is still worthless, and the administrator's session on a customer's
  address expires together with the impersonation. Ending it returns to the console.

## v0.19.0 — 2026-07-29

Almost everything here is for whoever *runs* an installation rather than whoever uses one.
Nothing in this release changes what an agency sees, and no existing install is affected until
its operator opts in: every new capability is off by default.

### Added

- **Customer domains can be served through Cloudflare for SaaS** (#199). A verified custom
  domain becomes a custom hostname on the operator's zone and Cloudflare issues the
  certificate, so Traefik needs no per-domain router and no ACME resolver. The instance-level
  API token is read from the environment or a Docker secret, never stored in the database,
  never returned by an endpoint and never sent to the browser; it needs exactly two zone-scoped
  permissions. Verification registers the hostname *before* the org row changes, so a
  Cloudflare outage leaves a domain unverified rather than verified with no certificate behind
  it.
- **Provisioning an org creates its address** (#199). The zone is checked for
  `<slug>.<base_domain>` and a name already taken is refused rather than colliding; the
  reserved list gained the cloud infrastructure names, since an org slugged `edge` would shadow
  the fallback origin every custom hostname routes through. Renaming an org moves the record.
- **An organisation can be given an end date** (#199), settable from the console or the
  provisioning API. Past it the organisation is warned (banner and e-mail) while still fully
  usable, then suspended but recoverable, then terminated: archived, its Cloudflare records
  released, its stored files deleted and its rows purged. `NULL` means unlimited and is the
  default for every existing organisation, so no upgrade puts anyone on a path to deletion. Two
  separate switches gate it, because the last step cannot be undone — the intended first
  deployment warns and suspends for real while destroying nothing.
- **A second person can operate an installation without holding everything** (#26). Instance
  access used to be one flag meaning "everything, across every organisation". There are now
  two principals: an owner, who holds every capability implicitly, and an administrator holding
  an explicit set — view organisations, read the audit trail, manage plans and end dates,
  export, sign in as a member, permanently delete, manage API keys. Managed at Console →
  Administrators; inviting creates the account and the person sets a password through the usual
  flow. Granting access is owner-only and deliberately not itself a capability, and the last
  owner can never be removed.
- **An export can be a complete one** (#26). `GET /instance/orgs/{id}/archive` returns the rows
  *and* every stored file as a zip — what an agency leaving should take, and what the automated
  termination archives before destroying anything. Importing one restores the files too.
- **Object storage reclaims space when an organisation is deleted** (#190). Terminating an
  organisation now removes its objects instead of leaving them to be paid for indefinitely.
- **Any setting can be read from a file** (`SCHAKL_<SETTING>_FILE`), so every credential can be
  a Docker secret rather than an environment variable. An unreadable, empty or misspelled one
  refuses the boot and names the path, because the alternative is a container that starts and
  quietly runs without a credential.
- Deployment stacks for Docker Swarm, including a Portainer-ready one that routes through
  Traefik labels, with managed PostgreSQL and S3-compatible object storage.

### Fixed

- **An imported organisation read its files out of the source organisation's storage.** A file
  row carries an organisation-prefixed key, and the importer copied it verbatim, so a restored
  organisation pointed into another one's key space — the same bytes at first, and a broken
  reference the moment either organisation was deleted. Imported files are now re-keyed onto
  the organisation that owns them.
- **The instance surface had no enumerable authorization guard.** The check that every tenant
  route declares a permission deliberately skipped `/api/v1/instance`, which left the one
  surface that can read, export, impersonate and purge *any* organisation covered only by a
  single hand-written test. Every route there now declares its capability, and a route that
  declares neither a capability nor a reasoned exemption fails the build.

### Upgrade notes

- Four additive migrations, all nullable, all reversible. Existing rows are untouched: every
  current operator stays an owner holding everything, and every organisation gets no end date.
- The cloud features are inert unless configured. `SCHAKL_CLOUD_LIFECYCLE_ENABLED` and
  `SCHAKL_CLOUD_LIFECYCLE_DESTRUCTIVE` both default to off, and the second is what allows an
  irreversible purge — deploy with it off until the dates have been checked against real
  organisations.
- Revoking the "sign in as a member" capability does not end a session already in flight; it
  lapses within one impersonation window (at most an hour). Withdrawing the administrator's
  access is the immediate lever.

## v0.18.0 — 2026-07-27

### Added

- **Clients have a klantnummer.** Instellingen → Bedrijven sets the format (`{jaar}`, `{seq:4}` and friends, previewed live as you type), the next number, whether new clients are numbered automatically, and offers a one-off "number existing clients" action that fills only the blanks. The number is searchable, sortable and shown as an optional column; typing one by hand is fine, and a duplicate is refused. Invoice and quote number formats gained the same live preview.
- **Imports match on the klantnummer before the name.** A client that was renamed since the last export re-imports onto itself rather than arriving as a second company, and two rows that reach the same client — one by number, one by name — are reported as a duplicate instead of one silently overwriting the other.
- **The organisation has a country** (Instellingen → Branding). A phone number written the way people actually write it (`0612345678`) is now read in that country instead of being refused, so a client list imports without hand-editing every number first; a company's own country still wins, and a number that already carries `+32` is never reinterpreted. The phone picker starts on the organisation's country rather than guessing from the browser.
- The client importer accepts the company phone number, which it previously had no column for.
- **You choose what every column of your file means.** Import is now a three-step wizard: pick a file or paste a table, map the columns, check, import. Each column of *your* file gets a row showing its first real values, and a picker for what it should become — a field, one of your own custom fields, or nothing. schakl. fills the mapping in from your column headings ("Klantnummer", "Bedrijfsnaam", "Plaats" and dozens more, in Dutch and English) and leaves what it does not recognise blank rather than guessing. Columns you do not map are simply skipped, so a file carrying ten columns of which three matter no longer has to be cleaned up first, and headers no longer have to be renamed to internal keys. A file that carries more than one column records can be matched on lets you pick which one.
- **A client list can bring its contact people with it.** Voornaam, achternaam, e-mail, telefoon and functie can be mapped in a *client* import: the contact is created, linked, and the first one becomes that client's primary contact. Re-importing the list updates that person rather than duplicating them, an empty cell never wipes their details, and a later import never reassigns who the primary contact is. The columns only appear for someone who may write contacts.
- **Import is no longer CSV-only.** An Excel workbook (`.xlsx`, with a worksheet picker), a tab- or semicolon-separated file and a block pasted straight from a spreadsheet all import the same way a CSV does. The file's encoding is detected rather than assumed, so a Dutch Excel export with accented names is read instead of refused, and Excel's own types arrive as the text a column expects: a client number does not become `1234.0`, a date does not become a timestamp. An uploaded workbook is vetted before it is opened — declared sizes, member count and compression ratio — so a zip bomb is refused without being decompressed, and a file over the row, column or size limit is always an error rather than a silent partial import.

### Fixed

- **A client-portal login could download the entire client list** as a CSV export. Bulk import and export are now their own capability, held by staff roles only, on top of each entity's own read/write permission.
- The company importer built its billing block from a positional slice of its field list, so inserting a column would have shifted every imported client's VAT number, address and city one field along without any error.

## v0.17.0 — 2026-07-21

### Added

- **The shared text editor is now WYSIWYG.** Headings render styled instead of showing `###`, links show as blue label text, Enter continues a bullet or numbered list (an empty item exits it), and typing `### `, `- `, `1. ` or `**bold**` converts as you type. The Write/Preview toggle is gone because the editor is the preview. Clicking a link opens an inline popover to edit, open or remove it, and typing after a link is plain text again instead of silently extending the link. The stored value never stops being markdown, so the API sanitizer, the renderer, PDF/UBL flattening and the activity log are untouched; the editor loads lazily after hydration, and without JavaScript the plain textarea renders exactly as before.
- **Every long-form notes field uses the editor.** Company notes, contact notes (which previously had no edit surface at all), invoice and quote notes, subscription notes, the project description and the template variants all get the shared editor, which grows heading, bullet-list and numbered-list toolbar buttons and an inline link popover in place of the browser prompt. The fields render as markdown on their detail views, and the two consumers that print words rather than markup — the invoice PDF and the UBL note — flatten to plain text so customer documents never show literal asterisks.
- **@ mentions and # task references work in every rich-text editor.** The editor fetches its own candidates on first focus (org members, host-scoped contacts and tasks), so every surface gets both triggers without per-page wiring. The # dropdown now also names each task's status, assignee and due date, so two same-titled tasks are distinguishable.
- **Every outgoing e-mail leaves as branded HTML.** Password-reset, invite, notification, invoicing and test mails are wrapped in the tenant's branding — logo, brand name, primary color — at the one send seam. Notification e-mails render the same sentences as the in-app feed, with deep links, instead of raw event codes; invoice, quote and reminder mails use the tenant's brand name rather than the internal org name; and a failed brand resolve sends the mail unstyled rather than not at all.
- **The interactions list is navigable.** Sortable columns (date, subject, type, contact, owner), a week switcher, a twelve-month filter and a free date range that all drive the same URL parameters, with date bounds interpreted as org-local calendar days. The contact and company chips read at full strength again — who before when.
- **Close a task while logging a contact moment.** Picking a task in the create form reveals the "close the task with this" checkbox; saving records the moment and moves the task to a finished status, and a failed close never takes the saved moment down with it — the form says what was saved and why the close bounced.
- **A price increase can target a single subscription or standard subscription.** The bulk price increase now takes exactly one scope — everything, one type, one subscription or one standard subscription — and subscription and standard-subscription rows carry a shortcut that opens the modal locked to that row. A single-row change never silently drags a template default along.
- **Show/hide toggle on password fields.** Login, setup, password reset, the cloud console and the account page get an eye toggle so a typo cannot lock you out. Write-only admin secrets (SMTP password, API keys, client secrets, the Ads developer token) deliberately stay plain password fields.
- **Technical keys are derived from the label, never hand-typed.** Custom fields, leave types, roles, contact types, interaction kinds, time-entry types and subscription types no longer ask for an immutable key slug next to the label — the key is generated from the label, a duplicate is reported against the label the tenant actually typed, and a conflict inside the roles dialog is now visible in the dialog instead of behind it. Labels are required in only one language; the other falls back.
- **Creating a task lands on the task, not a form.** Every "new task" entry point (the tasks page, the client page's header action and its tasks panel) creates a minimal task — placeholder title, assigned to its creator, pre-linked to the client the entry point knew — and opens the detail page in edit mode, so creating and editing are one surface and the duplicated inline form is gone. An abandoned placeholder stays a real, deletable task.
- **A project covered by a subscription gets its hours from the agreement.** When an active subscription with included hours is linked, the project's hours budget derives from it (several agreements sum, each converted to its monthly equivalent), the field locks in the edit form with the source named, and the API refuses direct writes. The project's own value returns on unlink; budget amount and hourly rate stay editable.
- **The client portal shows what the client may open.** The portal navigation now renders from the same permission-filtered registry staff use, so tasks, projects, websites, domains, hosting and contacts granted to the client role are reachable — every row still scoped server-side to the client's companies and per-task visibility. Calendar, settings, overview and notifications stay staff-only.

### Changed

- **All money is priced at the employee rate.** Cost, revenue and invoicing now price a logged hour at the rate of the employee who logged it (falling back to the org default), never a project-configured rate. Billable entries without a project now count toward revenue, invoicing from time groups its lines per person so two people on one project bill at their own rates, and the project page's planned-value and margin figures are retired — with one rate source they no longer mean anything.
- **Subscription templates are now "standard subscriptions".** The developer word "template" is gone from the interface. The catalog — standard subscriptions and subscription types — moved from a widget under the live list to sub-route tabs at the top of the Subscriptions section, as full tables with search, filters, sorting and the personal column picker. Instellingen → Abonnementen is retired; the old route redirects to the new one.
- **A new subscription starts active.** An agency records an agreement when it starts, so creation now stamps the activation, spawns the type's onboarding tasks and derives the next invoice date immediately — unless another status is picked explicitly in the modal. Imports still default to draft, so a bulk import cannot silently go live.
- **The subscriptions overview sorts on every column it shows**, including client, type, amount (at today's price) and included hours.
- MRR and ARR are spelled out in Dutch: "Maandelijks/Jaarlijks terugkerende inkomsten".
- Marketing site: the demo buttons open a small "demo is on its way" dialog instead of linking to a demo instance that does not exist yet.

### Fixed

- **The task edit form's project picker ignored the selected client.** The company and project pickers were fully independent lists; the client now narrows the project list, picking a project backfills its client, and switching to a client that does not own the selected project clears that pick instead of silently saving a cross-client pair.
- **Dutch wording when a task needs a contact moment before closing.** The prompt strung "vastleggen" into the location phrase, which misparsed; it now reads "Registreer er eerst een onder Contactmomenten".
- **The task picker on a new contact moment offered every client's tasks.** Opening "Contactmoment vastleggen" from a client page listed the whole org's tasks until a project was picked; the picker — and the move/koppel dialog — now narrows to the current client.
- **Logging a contact moment from a task or project page pre-fills the client and project.** The form opened with empty pickers even though the host task (or project) fixed both; they now preset from the host's own links — still repointable — and the saved moment carries them explicitly instead of relying on server-side derivation.
- **A subscription created without a next-invoice date was silently never invoiced.** The "Volgende factuur" field is gone from the create modal (there is nothing to anchor it against yet); the date is now derived on the first transition into active — start date plus one billing period — for create-as-active, the edit modal, the bulk status action and pre-existing empty drafts alike. An explicitly set date is never overwritten.
- **Portal clients no longer pass for staff.** The team list (Instellingen → Gebruikers) hides portal logins, and every assignee/staff picker stops offering them — only memberships holding a non-client role are pickable.
- **A portal login could read the org's whole address book.** Contacts carry no direct client link, so the portal's company horizon never filtered them; they are now scoped through their company links like every other portal read.

### Upgrade notes

- No database migration; API and web only. Rollback to v0.16.0 is safe.
- Pricing behaviour changes with the employee-rate switch: project-level hourly rates are no longer read or written anywhere. The `projects.hourly_rate` column stays in the schema this release (expand/contract) and is dropped in a following one.

## v0.16.0 — 2026-07-20

### Added

- **Log a contact moment from the client and project page headers.** Both detail pages carry a "Contactmoment vastleggen" button that opens the interaction form with the client (or project and its client) already set.
- **Close or create a task while approving an email.** The review dialog offers "Rond de taak af met dit contactmoment" whenever a task is picked (one finished status is applied silently, several offer a pick), and the task picker creates a new task inline — prefilled, auto-selected, with the dialog's client and project carried along.
- **Long email conversations fold.** An email's quoted history (earlier replies, forwarded blocks) collapses behind Gmail's own ⋯ gesture; the current message stays readable on its own.
- **Opt-in logging of colleague-to-colleague email** (Instellingen → Google). Internal mail always arrives pending — filing it onto a client or project is the reviewer's call — and unknown external senders stay out as before. Off by default.
- **Subscriptions: in-page catalog management** — subscription types, templates and prices are edited where they are used, with template-locked names, one-language labels and a bulk price increase.
- **Websites: the technical owner** can be recorded as the agency or the client, by name.
- **Domains: a typed domain reduces to its bare root** (`www.example.nl/page` → `example.nl`) on entry.
- **Contacts: duplicate email addresses are rejected** with a clear error instead of silently creating a second person.
- **Pickers show a visible ＋ button** wherever inline-create is available, instead of only revealing it after typing an unknown name.

### Changed

- **Detail pages are ordered by use.** Contactmomenten moved up beside contacts/projects/tasks on the client page (and under Uren on a project); Websites and Domeinen sank to the bottom as rarely-consulted assets; the activity trail now always renders last — on the project page it previously sat above the to-dos.

### Fixed

- **Password-reset and invite emails send again.** The API resolved the tenant from the raw `Host` header, which for requests proxied by the web server is the internal service name — every reset and invite mail was silently dropped while the test mail worked. Both invite surfaces also report honestly now when a send fails.
- **Gmail polling: a client invited to the portal no longer silences their email.** A portal login is a membership, and the colleague-chatter filter counted every membership as staff — so inviting a contact to the portal dropped their entire correspondence before matching. Portal logins are excluded from the staff set.
- **Gmail polling: one broken message can no longer wedge a mailbox.** A message whose ingest kept failing re-aborted every poll at the same point and the feed silently stopped; each message now ingests independently and a failure is logged and skipped.
- **Checkbox settings save correctly again.** The shared form checkbox posts a different value than a raw one, and several settings pages (Google surfaces, SSO, verloftypen, huisstijl, taaksjablonen, Gmail sync, a project's billable default) still compared against the old value — saving them silently unchecked every box. All checkbox reads are presence-based now.

## v0.15.2 — 2026-07-20

### Fixed

- **PWA manifest behind an authenticating proxy.** Browsers fetch the web-app manifest without cookies, so an instance behind Cloudflare Access saw the request bounced to the Access login and rejected on CORS, on every page load. The manifest link now carries `crossorigin="use-credentials"`, which sends the session cookie and lets the request through the proxy.
- The app declares the standard `mobile-web-app-capable` meta alongside the `apple-` prefixed one, silencing Chrome's deprecation warning while iOS Safari keeps the spelling it reads.

## v0.15.1 — 2026-07-20

### Fixed

- **Marketing: the key-events label editor froze on "Loading" with real GA4 data.** The v0.15.0 editor created a missing label entry while the table was rendering, which Svelte rejects as a state mutation during render; the card then never finished loading, so key events could not be renamed. Label entries are now created on the first keystroke instead. No migration; web-only.

## v0.15.0 — 2026-07-20

One feature: the marketing dashboard rework. The top-level Marketing page and the client's marketing tab become the same screen, and curating a client's dashboard now works like arranging your own My Day board.

### Marketing

- **One dashboard, two entrances.** The top-level Marketing page and a client's marketing tab render the same shared dashboard component. The layout editor is available from both — previously it existed only on the client tab, so curating from the Marketing page meant knowing to go through the client page first.
- **Edit in place, like My Day.** The pencil turns the real dashboard editable instead of swapping it for a form: drag tiles by their grip to reorder, hide a tile with its cross (hidden tiles wait in a strip below to re-add with one click), and rename tiles inline in both languages on the tile itself. Drill-downs toggle on their own cards, with disabled ones shown as quiet placeholders that cost no Google call. The default chart metric is a select, and the whole source can be hidden from the client via the section header. Every change saves immediately; there is no separate save step to forget.
- **Key-event labels, typed where the events show.** Each GA4 key event gets its client-friendly name (per locale) directly in the key-events table rows. Labels whose event did not surface in the current range stay editable below the table, and an event can be added by its raw GA4 name, so labeling never depends on a live Google call.

### Upgrade notes

- No database migration; the change is web-only. Rollback to v0.14.0 is safe.
- Stored layouts (tile order, names, hidden tiles and sources, key-event labels) carry over unchanged; only the editing surface is new.

## v0.14.0 — 2026-07-20

A smaller release: brute-force protection on login, a batch of domains, marketing and invoicing polish, and multi-arch container images.

### Security

- **Rate limiting on login and password reset.** The login endpoint had no throttle, so a client could fire 100+ password guesses a minute. A Redis fixed-window limiter now caps attempts per client IP per tenant (10/min for login, 5/min for forgot/reset, separate budgets), reusing the pattern already proven for API keys so the ceiling holds across API replicas. It fails open on a Redis outage so sign-in never blocks, and the web app surfaces a 429 as "too many attempts" instead of "wrong password."

### Domains and websites

- A domain with status "redirect" now carries the address it redirects to, shown as a field on the form and a link on the detail view.
- The new-website form's domain picker and the hosting quick-create dialog now follow the inline-create rule everywhere (docs/UX.md, #115): typing an unknown domain or provider opens the full create form in a dialog and auto-selects the result.
- Fixed: quick-creating a second entity (e.g. a hosting account right after a domain) no longer clears the first picker's selection.

### Marketing

- The curated per-client dashboard layout (#192) gains per-key-event labels (a client-friendly name per locale for each GA4 key event) and a toggle to hide a whole source from the client/portal view while keeping it available to re-enable in edit mode.

### Core

- Instellingen → Navigatie can now rename module nav items and sidebar group headings per locale. The renamed label follows through everywhere — sidebar, group headings, and every module page's heading and browser title.

### Invoicing

- Picking a client on a new invoice now prefills one line per unbilled approved time entry (description, hours, rate), replacing the separate "Uren factureren" bridge button. Prefilled lines are ordinary lines you can edit or remove before saving.

### UX

- Fixed: Tab now commits the highlighted option in a combobox instead of discarding it, matching Enter, while still moving focus to the next field.

### Infrastructure

- **Multi-arch container images.** The release workflow now builds both `schakl-api` and `schakl-web` for `linux/amd64` (x86-64) **and** `linux/arm64` (ARM), publishing each tag as a manifest list on GHCR. Self-hosters can run schakl unchanged on ARM hosts (Hetzner Ampere/CAX, AWS Graviton, Apple Silicon); `docker pull` selects the right variant automatically. No Dockerfile or compose changes were needed — the base images and all dependencies already ship arm64 artefacts.
- Fixed: the `SCHAKL_SECRET_KEY` guard in `compose.yaml` quoted its error message incorrectly, which strict YAML parsers (Compose v5) rejected as an invalid nested mapping.

### Upgrade notes

- One additive database migration (domains gain a nullable `redirect_url`); no destructive changes, rollback to v0.13.0 is safe.

## v0.13.0 — 2026-07-18

The commercial release: most extension modules move behind the license key, the invoicing module grows into a complete billing flow (products, server-rendered PDFs, the time and subscription bridges in the UI), websites take hosting's place in the menu and on the client page, and a broad UX pass lands breadcrumbs on every page and fixes a whole class of silent form-save bugs.

### Licensing

- Seven previously free modules are now licensed skus: time, projects, domains, websites, hosting, interactions and HR — joining leave, subscriptions, invoicing, automation, marketing and Google Workspace. The existing semantics apply unchanged: enabling needs a covering key, past expiry+grace a module goes read-only, and exports always work.
- The bootstrap-grace clock restarts at upgrade time, so an installation whose original trial window lapsed gets the standard two weeks of full function for the newly licensed modules instead of losing time tracking mid-flight.
- Writing crons of the newly licensed modules stand down while their sku is not writable; the paid set is pinned by a test so it only ever changes on purpose.

### Invoicing

- **Default products**: named line presets (description, unit, price, tax rate) under Instellingen → Facturatie, dropped onto an invoice or quote with one pick. Lines keep snapshotting what they copy, so re-pricing a product never rewrites an issued document.
- **Server-rendered PDF**: the API renders the invoice/quote document itself (template columns and texts, seller and bill-to blocks, totals, the document's own locale). Sending an invoice or quote now **attaches its PDF** — previously the customer received only a text summary — and both detail pages get a Download PDF action. All four mail transports gained attachment support.
- **Time tracking, reachable**: the invoices page gets "Uren factureren" — pick the client, see the open approved/billable hours live, choose the grouping, land on the draft. The bridge existed in the API; no screen called it.
- **Subscriptions, visible**: an invoice drafted by the subscription cycle now carries a chip with its billing period instead of looking hand-made.
- The editor pre-fills issue date and the org's payment term / quote validity; the rendered document prints the seller's phone and the client's e-mail and CoC number, and an invoice without template payment text still states how to pay (total, deadline, IBAN, reference).

### Websites and the client hub

- Hosting moves out of the main menu to Instellingen → Hosting (agencies reuse the same hosting); the assets group gets a **Websites** page instead — every client website in one list, created by connecting it to a domain.
- The client page swaps its hosting panel for a websites panel with quick-add, the contacts panel gets an add button in use mode, the time panel a "log hours" shortcut and the invoicing panel a "new invoice" shortcut, both with the client preset.
- Time entries can link to the subscription the hours are worked under (optional picker on the entry form and the report's edit modal); subscription usage counts directly linked entries alongside the linked-project roll-up.
- Marketing reads per website: the Marketing page, the client's marketing tab and the client-portal dashboard get website tabs (all sites, per site, client-wide).

### E-mail

- Org-wide HTML signature under Instellingen → E-mail, appended automatically to every outgoing mail (sanitised on write and on send); text-only mails are promoted to HTML so the signature renders as authored.
- Tenant e-mail templates are edited one language at a time behind a switcher.

### Privacy

- Pending (unreviewed) Gmail interactions are now private to their mailbox owner with **no admin escape**: `read_all` no longer opens other users' pending queues, and a pending row is absent — not forbidden — for everyone else.

### UX

- **Breadcrumbs on every page**, rendered once by the layout: module roots, settings screens and record names ("Klanten › Acme › Marketing"), replacing 45 hand-written back links.
- A whole class of silent save bugs is gone: every submitting checkbox and radio in the app was rendered one-way and could lose its mark on hydration, stripping stored state on the next save (roles, org modules, task labels, settings toggles, …). All of them now hold their state in the component, via the shared `FormCheckbox` and `bind:group`.
- Tenant translations are always optional: label editors (contact types, leave types, custom fields, tax rates, roles, …) show one field with an NL/EN switcher, and a missing language falls back at render time.
- Nine new dashboard widgets across two rounds: recurring revenue, outstanding invoices, open quotes, project budget burn and who's off today, in the set widget styling.

### Upgrade notes

- Four additive database migrations apply automatically: the bootstrap-grace restart, the time-entry subscription link, the e-mail signature column, and the products table. No destructive changes; rollback to v0.12.0 is safe.
- The API gains one dependency, `fpdf2` (pure Python) for the PDF renderer; the Docker image needs no system packages (it uses the DejaVu font when present and degrades gracefully otherwise).
- License keys minted before this release do not cover the newly licensed skus. Reissue customer keys with the modules they use before their bootstrap-grace window (14 days from upgrade) runs out.
- The hosting page moved to `/settings/hosting`; `/websites` is new. Saved bookmarks to `/hosting` will 404.


## v0.12.0 — 2026-07-17

A large release: five parallel work streams merged — the security audit remediation, two-factor authentication, the invoicing and quotes module, the cloud (multi-org) posture, and the client-hub batch covering issues #190 through #198 plus the portal, HR and mobile work that followed it.

### Security

- Full security audit of the API and web app (#29): tenant isolation, the RBAC core, the injection surface, rich-text and branding sanitization, and license/API-key handling all held. Four critical/high findings are fixed in this release; the remaining findings are documented with remediations in `SECURITY_AUDIT.md`, and an adversarial test suite now runs with the normal CI so the audit is a ratchet rather than a snapshot.
- The API refuses to boot in production on a default, publicly known, or short `SECRET_KEY`. See the upgrade notes below.
- Conferring the `owner` role now requires `settings.roles.manage`, closing a privilege-escalation path from `members.member.write`.
- OIDC sign-in only adopts a pre-existing local account when the IdP asserts `email_verified`, closing an account-takeover path via a permissive IdP.
- `javascript:`, `data:` and `vbscript:` URL schemes are rejected at the API for company websites and task links (stored XSS).
- A record's activity trail now also requires the entity's own read permission on top of `activity.read`.

### Two-factor authentication

- TOTP with QR enrollment, ten single-use backup codes, and an optional SMS factor (instance-configured gateway; only ever an add-on to TOTP). Login becomes a two-step challenge for enrolled accounts; all verify paths share brute-force damping.
- Org admins can reset a member's second factor from Instellingen → Gebruikers (audited); an org that enforces SSO keeps MFA at the IdP.
- Self-service email change guarded by the current password; the unguarded `email` field on the bare profile update is closed.

### Invoicing and quotes

- A native `invoicing` module (#207): sales invoices and quotes raised inside the CRM, wired into unbilled approved time, subscription cycles, and the new company billing-identity fields (#11 — VAT/CoC and postal address, snapshotted onto issued documents).
- Tenant-configurable locale-dependent tax rates, document templates, automatic payment reminders, per-document currency and locale, and an accounting seam for a bookkeeping package to take over.

### Cloud posture (business-licensed)

- `SCHAKL_DEPLOYMENT=cloud` turns an installation into the operator-run multi-org posture: an instance console on the apex host, a provisioning API behind instance API keys, org plans (trial, standard, unlimited) with a daily trial-expiry cron, and an included instance e-mail transport orgs can opt into.
- Service PIN: the instance owner cannot open an org's data until an org admin generates a time-boxed, revocable PIN (#199, partial).
- Wildcard main-domain ingress plus customer custom domains via CNAME with automatic per-domain TLS (#202).
- Self-hosted behaviour is unchanged; the cloud surface returns 404 unless the posture is enabled.

### Client portal and per-task visibility

- Contacts can be invited to a client portal login (#193): a reduced shell, a curated dashboard with the client's own logo (#196), and a data horizon limited to their companies.
- Tasks carry a "visible to client" flag: portal logins see exactly the flagged tasks of their companies, can comment on them, and never see the activity trail, uploads, or staff panels. Existing installations receive the client comment grant through a data migration.

### Client hub

- Quick-create from the client page: permission-gated "new" affordances on the tasks, projects, domains, hosting and subscriptions panels open the module's own create form with the client preselected; a domain row links to its website or offers creating one.
- Company groups (#191): a per-membership company data horizon, enforced in the tenant-scoped repository and visible on the users screen.

### HR

- A new `hr` module with a personal page per employee, reached from the profile menu: leave balance, current contract, and a per-category dossier (contract copy, growth plans, bonus agreements, benefits, CAO). Dossier managers upload and remove documents; every filing lands on the activity trail.

### Marketing

- Per-client marketing tab layout editor (#192): reorder, hide and relabel tiles per source, enforced server-side.
- Marketing links can attach to a specific client website; pickers offer the client's websites and the marketing tab groups per site.
- Fixed the mobile drill-down overflow (#195).

### Tasks

- A strict use-versus-edit split on the task detail: the default surface is working the task (status, checklist ticking, comments, planning); everything structural lives behind the edit mode, and empty structural sections no longer render.
- Ticking the last open to-do offers to move the task to its terminal status, and explains the closing contact-moment requirement where one applies.
- Task references in rich text via `#` with autocomplete (#197); mobile fixes for the vanishing row title and the filter bar.

### Platform

- S3-compatible object storage via instance environment variables (`SCHAKL_STORAGE_S3_*`), with per-file backend dispatch so existing local files keep working (#190).
- Real PWA and iOS home-screen icons derived per tenant from an uploaded app icon (#198).
- My Day dashboard tiles keep equal spacing regardless of their heights.

### Upgrade notes

- `SCHAKL_SECRET_KEY` is now required in production: an installation still running the former default key will refuse to boot. Set a strong value before upgrading.
- Fourteen additive database migrations apply automatically on upgrade; the chain has a single head and a real downgrade path.
- The deprecated `marketing_company_settings.show_key_events` column stays readable this release and will be dropped in the next one.
- New orgs enable the `hr` and `invoicing` modules by default; existing orgs enable them under Instellingen → Modules.
