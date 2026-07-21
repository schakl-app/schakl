# Changelog

## Unreleased

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
