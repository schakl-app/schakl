# UX conventions — schakl

> The design language of this CRM. Read this before building or changing any screen; it
> encodes decisions the owner has already made (often by correcting earlier versions).
> When a new convention emerges from feedback, add it here.

## Principles

1. **Mobile-first, always.** Every screen must be fully usable on a phone — this is part of
   the definition of done, not a nice-to-have. Grids stack, tables get `overflow-x-auto`,
   the sidebar becomes the hamburger drawer, primary actions get a reachable button (FAB).
2. **Snappy over clever. Performance is incredibly important — a slow page is a bug.** Users
   notice slow navigation immediately. Keep SSR loads lean: shared lookups live in a **layout
   load** (they don't rerun on filter/tab navigation); page loads fetch only what changes;
   heavy aggregates are opt-in (`meta=false`, `count=false` on lookup fetches). Links preload
   on hover (`data-sveltekit-preload-data`). **Before writing a page, count its API calls —
   see [docs/PERFORMANCE.md](PERFORMANCE.md).**
3. **Use mode vs edit mode.** Working *with* a record (ticking checklist items, commenting,
   changing status, logging time) is the default surface. Changing a record's *definition*
   (title, relations, budgets, recurrence) lives behind an explicit edit mode, reached via
   the ⋯ (dots) menu. Destructive actions live in the same menu and always confirm.
4. **Accountability is a feature.** Overdue work is loudly red everywhere (rows, widgets,
   counts). Extending a deadline requires a reason, and every meaningful change lands in the
   record's activity feed with actor + timestamp. Approval locks records for non-managers.
   Invoiced implies approved — states never contradict each other.
   **The activity trail is a core capability, not a per-screen nicety** (issue #67, CLAUDE.md §16):
   a mutable record opts into it with `AuditableMixin`, its service records field edits (`created`,
   `updated {changes}`) through `ActivityService` in the writing transaction, and a core-contributed
   panel renders the trail on its detail page. So "every meaningful change lands in the feed" is a
   platform guarantee that every auditable entity inherits, not a sentence each new screen has to
   remember to make true. The actor is named from a snapshot, so a deleted user's work never becomes
   "the system" (#64). The panel hangs last — history sits under the working surfaces, not above them.
5. **Everything reusable is a template, org-wide.** Task templates, checklist templates,
   dashboard layout templates: define once for the whole instance, apply anywhere. Templates
   are both *manageable* in a dedicated place and *creatable from where you work* ("save as
   template" on a live checklist; quick-create in a picker).
6. **Admin config lives under Instellingen.** Org-wide knobs (branding, modules, team
   defaults, labels) belong in Settings — never as buttons inside a working screen. Personal
   preferences (language, own dashboard layout) belong to the user (profile menu → personal
   settings, or inline "customize" affordances that only touch their own view).
7. **Every number opens.** A figure the user cannot take apart is a figure they will not trust,
   and "12,5 / 40 u" invites exactly one question: *which hours?* So an aggregate is never a
   dead end. **Answer it in place** — the records behind a total belong on the page where the
   total is shown (the Uren panel under a project's budget bar), scoped to exactly what the
   number counted, with the same ⋯ edit/delete the records get anywhere else. Then link out to
   the full report for slicing, pre-filtered, never as the only way in: a reporting screen is
   manager-only, and the person who wants to know where the budget went often isn't one.
   The same rule governs a client's `unbudgeted_hours`, a task's checklist count, a
   timesheet total. If a panel truncates, it says so — silent truncation reads as "that's all
   of them" (docs/PERFORMANCE.md). **A convenience like this is not a nice-to-have bolted onto
   one screen; it is what the screen was for.**

## Interaction patterns

- **Dates are European everywhere**: displayed and typed as `dd-mm-jjjj` via the shared
  `DateInput` (never a bare `<input type="date">` — browsers render those US-style). Its
  calendar popup must anchor to the field. Formatting goes through `core/format.ts`
  (locale → nl-NL / en-GB).
- **Pickers are type-ahead comboboxes** (`core/ui/Combobox`), never long native selects, and
  **every entity-reference picker offers inline-create — this is per-picker definition of done,
  not an optional flourish.** Typing an unknown name offers "＋ … toevoegen", which opens the
  entity's *full* create dialog — real fields plus the tenant's custom-field definitions from the
  API, prefilled with what was typed (never a name-only stub form) — and on save **auto-selects**
  the new record in the picker, so the user never leaves the surface they were on. The machinery
  is built into `Combobox`: pass its `oncreate` and follow `contacts/ContactDraftField.svelte`
  (draft-in-form, auto-selects the new chip) or the `time` page's quick-create (server create →
  reselect). A picker that only lists preloaded options and sends the user *elsewhere* to create
  the missing registrar / provider / client is a bug — that is precisely what the first domains and
  hosting forms shipped as (#115). The one exception is an entity with no create path of its own —
  an employee is *invited*, not created — so leave those select-only.
- **Quick-add where the user is**: contacts on the client page, projects/clients from the
  time entry form, checklist items on the card. The full forms still exist on their own
  pages; quick-add is an accelerator, not a replacement.
- **People attached to a record are "one primary, N others"** — the same chips-plus-type-ahead
  shape everywhere. **The primary is marked by the brand colour and nothing else: no star, no
  emoji, no glyph of any kind.** A coloured chip among grey ones already says which one is
  primary; a ★ next to it is decoration, and decoration is what makes a dense screen look cheap.
  Because colour cannot be read by a screen reader (WCAG 1.4.1), the primary chip carries an
  `sr-only` label — that, not a glyph, is how the meaning is made accessible.
  **Clicking a chip promotes it to primary** — the marker never doubles as a control — and each
  chip carries an ✕ to drop it. Both gestures are *edit-mode only* (Principle 3): attaching,
  detaching and re-designating the primary all change the record's definition. So `LinkField`
  (contacts on a client, clients on a contact) shows quiet navigation chips by default and only
  turns them into buttons, reveals the ✕ and reveals the type-ahead once its parent is `editing`.
  A panel that has no page-level edit mode to ride carries its own ⋯ → Bewerken / Klaar (the
  contacts panel on a client does; the client page's own ⋯ edits the *client*, a different
  surface). The pickers used *inside* an edit surface — `AssigneePicker` for the employees on a
  client or project, `ContactDraftField` for contacts on a not-yet-created client — are always
  interactive, because the surface itself is already edit mode.
  `AssigneePicker` posts the whole roster in one hidden field (an edit surface has exactly one
  save button); `LinkField` posts per chip, because there each link is its own record.
  Detail headers name the primary and render the rest as an `AvatarStack` of initials.
  **"Mine" filters match any assignee, never only the primary** — otherwise the feature is
  invisible to everyone but the owner.
- **Show an inherited value, don't hide it behind a placeholder** (#81). When the API will
  auto-assign something on save — a new project inherits the client's verantwoordelijke — the
  form pre-fills that value the moment the client is picked, so the assignment is visible and
  obviously already made. An empty field with a "wordt overgenomen" hint reads as unset and gets
  re-picked by hand. The pre-fill is web-only: the server still does the same inheritance, so a
  field left untouched stores exactly what the placeholder promised.
- **One shared row/tile per concept** (`TaskRow`, panel rows): title link, chips (labels,
  checklist n/m, ⏱ allocated), red overdue date, assignee initials — identical wherever the
  concept appears.
- **Drag-and-drop with graceful fallback**: reorder tasks and dashboard tiles by dragging
  (fractional `position` midpoints — never renumber); keep an arrow/menu alternative where
  dragging is impractical.
- **Every dashboard widget is a bordered card, via `core/ui/DashboardWidgetCard`** (#166). The
  dashboard grid wraps each tile in a bare `<div>` — the card chrome (border, `bg-surface-raised`,
  padding, title row with an optional "show all" link) is the widget's own responsibility, and the
  shared wrapper is how it stops being re-typed per widget. Both the empty and the populated state
  render inside the card; a bare `<p>` sitting naked in the grid is the bug this rule exists for.
- **Record actions live behind the ⋯ menu, never as bare buttons.** Every record-level
  **Edit** and **Delete** (on a list row, a card, or a detail header) is reached through the
  shared overflow menu (`core/ui/ActionsMenu`, the ⋯ / three-dots kebab) — never a standalone
  button sitting in the row or header. This is deliberate: an exposed Delete gets clicked by
  accident. The trigger is an icon button; items are labelled with a lucide icon; the Delete
  item is red (`danger`). Non-destructive, reversible toggles that aren't "edit the
  definition" (e.g. change status, mark billable, activate/deactivate) may stay inline.
  **This applies to inline sub-items too** — a comment, checklist item, checklist or link
  carries its own ⋯ menu (`ActionsMenu compact` — borderless, smaller) for Edit/Delete, not a
  hover-revealed ✕. You must always be able to **edit a comment (etc.) or delete it**, and
  every such edit/delete is **written to the record's activity feed** with actor + timestamp
  (the API `_record`s `comment_edited` / `comment_deleted` / `link_deleted` /
  `checklist_deleted` / `checklist_item_deleted`, …). **Creating and completing count too** —
  the trail once recorded only a checklist *disappearing*, so ticking an item off, the most
  routine thing that happens on a task, was invisible (#61). And a row says *what* happened:
  a comment entry carries an excerpt and links to the comment, rather than reading "commented"
  and sending the reader hunting.
- **Edit on a list row opens the record in edit mode** (#78). A list has no edit surface of its
  own — the form lives on the detail page, and duplicating it onto the overview would be a second
  copy to keep in sync. So the row ⋯ → Bewerken is a *link* to the detail page carrying `?edit=1`
  (above the red Verwijderen), and the detail page reads that marker once, on mount, to open its
  existing edit affordance — the client's edit `Modal`, the contact's / project's inline `editing`
  toggle. The param name lives once in `core/edit-intent.ts` (`editHref` writes it, `editIntent`
  reads it) so the two sides can't drift, and it seeds a `$state` initializer, not a `$derived`:
  the surface opens on arrival, then the user can close it without the URL forcing it back open.
  The underlying edit surface still differs per module (modal vs inline) — unifying *that* is a
  separate follow-up; this issue makes the *entry point* consistent (Verwijderen was one click away
  on every list while Bewerken was not there at all).
- **A feed names a person from a snapshot, never from a live join** (#64). Every FK to
  `users.id` is `ON DELETE SET NULL`, so a joined-in display name is the one thing that cannot
  survive the account it joins to. Store the name when the row is written: a name with no live
  account is a departed human ("Jane Smith (verwijderd)"), and **no name at all is the system**
  — which is what a NULL actor already meant, because the recurrence cron writes one on purpose.
  Without the snapshot the two collapse into each other and a person's work is silently
  reattributed to a bot. The live account still wins while it exists, so a rename shows through
  the whole history at once.
- **Confirmation dialogs** (`ConfirmDialog`) for **every** delete — no exceptions, including
  deletes reached from the ⋯ menu and from inside an edit surface (e.g. deleting a time
  registration). The ⋯ Delete item opens the dialog; the dialog owns the posting form.
  Approved/locked states explain themselves via tooltip + a clear error message key.
- **Rows that represent an editable record carry a ⋯ menu — including in reporting tables.**
  The Overzicht → Uren report gives each time entry a compact ⋯ (Bewerken opens the shared
  `EntryForm` in a `Modal`; Verwijderen confirms). A list of records is never read-only just
  because it's a "report": if you can see a registration there, you can edit/delete it (subject
  to the same role/lock rules the API enforces — managers may edit approved/others' entries).
- **Activate/deactivate lives in the ⋯ menu too**, not as a bare inline button (custom-field
  definitions: ⋯ → Bewerken / Deactiveren / Verwijderen). It's a non-destructive toggle so it
  doesn't confirm, but it belongs with the record's other actions, not loose in the row.
- **Personal view options are inline "customize" affordances** that only touch the current
  user's own view (UX Principle 6) — e.g. the timesheet's 7-day vs Mon–Fri **Weergave** switch
  and its jump-to-date picker sit quietly in the toolbar and persist per user (via
  `/api/v1/prefs`), never in org Settings.
- **Every list is the shared `DataTable`, driven by column descriptors** (`core/table/columns.ts`)
  — never a hand-rolled `<ul>` per concept. This is not a per-page choice: clients, projects,
  contacts, tasks, verlof and the Overzicht reports all get configurable, sortable columns, and a
  new list starts from the table rather than earning its way to it. Where a list needs something
  the table lacks, **grow the table** — the reporting screen's bulk selection and totals row, the
  task board's status sections — rather than forking a seventh bespoke grid. The user picks, orders, resizes and sorts the columns
  from the **Kolommen** popover on the list itself (personal, per user, `prefs.tables.<list>`),
  and a tenant's custom fields appear there as columns with no per-module code. Three rules the
  component enforces so lists can't drift apart:
  - **Sorting and paging belong to the server.** A list shows a page of a longer set, so sorting
    the rows you happen to hold sorts the wrong set. A header is clickable only when the API can
    order by that column (`sortKey`, not `sortable`) — a header that claims to sort and doesn't is
    worse than a quiet one. Derived and custom-field columns are honest about this.
  - **Every sort is reachable from the Kolommen menu, not only from a header.** Below `sm` there
    *are* no headers, so a header-only sort is a sort mobile users don't have. The menu is the one
    surface both sizes share: each sortable column carries an ↕ that cycles ascending → descending →
    off, and the active sort is named at the top. Headers stay clickable on desktop; they are the
    shortcut, never the only way in. Sorting by a *person* (assigned employee) orders by their
    display name — never by a user id, which is what a naive `ORDER BY` on the FK would do.
  - **A hidden column costs nothing.** An expensive column (the budget roll-up) is an opt-in
    aggregate: the page's `load` asks the API for it only when the column is visible. This is why
    column metadata is a plain module and the cell renderers are snippets — a server load can read
    the first and cannot import the second.
  - **A grid is not a mobile UI.** Below `sm` the table gives way to the concept's shared row, never
    a twelve-column sideways scroll. Rows keep their ⋯ `ActionsMenu`, and since a `<tr>` cannot be
    wrapped in an `<a>`, the primary cell carries the link and the row highlights.
    The same rule kills a grid before it is drawn: the **permission matrix** (Instellingen → Rollen,
    issue #19) is a `<details>` accordion per module of `label … control` rows, with a *select all*
    / *clear* pair in each module's header and **one** save button at the end. Nothing about it asks
    a phone to scroll sideways, so nothing does. A permission carrying a scope (`:own` / `:any`)
    gets a three-way control, not a checkbox — "may edit their own hours" and "may edit anyone's"
    are different grants and a tick cannot say which. Its selection is component state
    (`bind:group`), never a one-way `checked={…}`: a radio rendered one-way loses its mark on
    hydration, and the next save then silently strips what the user never touched.
  - **A column sorts by what it prints.** A person sorts by display name, a client/project/task by
    its name — never by the foreign key behind it (the API resolves each with a correlated
    subquery, which a join would turn into duplicated rows). A small closed vocabulary sorts by
    *meaning*: `priority` ranks low→high and `status` runs along the workflow, because
    alphabetically they read `high, low, normal` and `done, in_progress, open`. A value the server
    genuinely cannot order by — a derived status pill, a JSONB custom field — carries no `sortKey`
    and gets a quiet header.
  - **Grouping and sorting compose.** A sort orders rows *within* each section and never reorders
    the sections; so a board that groups by status declares no status column, because sorting one
    would visibly do nothing. Which sections are folded is a personal view option, saved with the
    columns.
- **A panel is how a number opens.** A module hangs a panel off another module's detail page by
  registering an `EntityPanelSpec` (`core/registry.ts`), never by having the host page import it —
  a tenant with the module disabled then simply never renders it, and pays for no call. The panel
  loads through the typed client inside the host's `Promise.all`, and the host hands down the
  lookups it already fetched (`EntityPanelLookups`) rather than letting the panel refetch 200 rows
  the page is holding. A panel that edits its records posts to the **host page's** form actions,
  because that is where SvelteKit actions live.
- **A period an aggregate counts from is the API's, not the browser's.** `budget_period` resolves
  to a *local* Amsterdam day (`projects/budget.py::period_start_date`), and the entries behind a
  budget bar are filtered by exactly that day. A page that recomputed it in UTC landed on the
  previous day for half the year, quietly dragging last month's evening into this month's total.
- **Budget burn has exactly one scale**, in `core/burn.ts` — green < 75 %, amber < 100 %, red ≥ 100 %.
  The percentage is **unclamped** so an over-budget project reports a negative remainder and reads
  red; only the drawn bar's width clamps, because a bar cannot be 130 % long. A record with no
  budget shows an em-dash and still reports what it spent — never a fabricated total, and never a
  reassuring zero.
- **Forms are SSR form actions** with `use:enhance`. Mind the default reset: forms whose
  inputs must keep their values after save use `update({ reset: false })`.
- **An edit surface shows every field the view shows.** If a record's page displays it, its edit
  modal edits it — a field the view has and the editor hides sends the user hunting for a second
  surface. The client's edit modal therefore carries its contact persons alongside name, status and
  assignees, even though the links are their own records: the picker collects them client-side and
  the form action reconciles them against what the client already has. The contacts *panel* stays
  as the quick-add accelerator, not as the only way in.
- **One save button per editing surface — never per field.** An edit mode collects all its
  fields into a single form (use the HTML `form="…"` attribute / the `formId` prop on
  `Combobox`/`DateInput` when fields live in different layout columns) with one save at the
  end. Per-field save buttons are a known corrected mistake.
- **Native controls inherit the huisstijl** via `accent-color: var(--brand-primary)` on
  `:root` (checkboxes, radios). But `<html lang>` does **not** control how they format:
  browsers render `<input type="date">` and `<input type="time">` after the *browser/OS*
  locale, so an en-US machine gets US dates and an AM/PM clock whatever the document says.
  Dates go through `DateInput`, times through `TimeInput` — both own the field, post a
  hidden ISO / `HH:MM` value, and parse loose typing. Time is always 24-hour: never
  introduce an AM/PM surface.
- **Budgets colour-code burn**: green < 75 %, amber < 100 %, red ≥ 100 % — the same scale
  for task time budgets and project hour budgets (total or monthly).
- **Verlof is tracked in hours, shown with a days equivalent** (`≈ n dagen`). The divisor is the
  employee's **average scheduled working day**, computed by the API — never `contracturen ÷ 5`,
  which tells a three-day part-timer their working day is 4,8 hours long. Employees request under
  Verlof (balance cards + one request form); managers approve/reject under Verlof → Team
  (approve/reject are inline status actions; reject asks an optional reason) and register leave on
  someone's behalf (ziekmelding). Approved leave appears on the timesheet as its own teal row,
  never mixed into worked totals, and on the Agenda.
- **A work schedule is employment data, so it lives on the person** (Instellingen → Gebruikers →
  ⋯ → Werkrooster), not buried in Instellingen → Verlof. It is a weekly grid: per weekday a
  working-day toggle, start/end, and the day's **breaks as a repeater** — a morning coffee break
  next to lunch is an ordinary shape, so a second break is one click, and each day carries a
  copy-to-other-days action. Times go through `TimeInput`, never a native `<input type="time">`.
  Breaks are **not re-sorted while you type**: the API stores them sorted and hands them back that
  way, whereas reordering rows on every committed time yanks the field out from under the cursor.
  The grid renders *outside* its `<form>` and posts through `form="…"` — its `TimeInput`s each emit
  a hidden field, and a form they are not inside is a form they cannot pollute.
  `contracturen` is a **derived, read-only column** that links to the person: hours follow from the
  schedule, they are never typed. Someone still carrying pre-schedule contract hours is flagged
  where they are listed, rather than being silently measured against the org default.
  Org config — verloftypen (wettelijk/bovenwettelijk carry-over rules live here, not in code), het
  standaardrooster, feestdagen, and yearly saldi — lives under Instellingen → Verlof.
- **A feestdag is nobody's working day, not somebody's absence.** So it never renders as one more
  coloured chip beside three people's vakantie: on the Agenda it is a quiet dashed marking that
  links nowhere and never counts toward a "busy day" heatmap; on the timesheet it marks the *day
  column*, because it is a property of the day rather than a row in the grid. The rule lives in
  `core/calendar.ts::eventChipClass`, once, so no view can drift.
  Not every feestdag is a day off everywhere (Goede Vrijdag is worked at many Dutch employers), so
  Instellingen → Verlof → Feestdagen seeds the whole list and lets the tenant **deactivate** the
  ones they work. Deactivate, never delete: a deleted holiday comes back on the next import, a
  deactivated one does not, and it renders and counts nowhere in the meantime.
- **Long-form user text is markdown** (issue #66), authored through the shared `RichTextEditor`
  and rendered through the shared `Markdown` component — never a bare `<textarea>`, and never
  `{@html}` outside that one component. Store the markdown *source* in the existing `Text` column;
  never store pre-rendered HTML, or a later sanitizer fix cannot protect the rows already written.
  The editor is markdown-with-preview, not WYSIWYG — a small bold/italic/link/heading/list
  toolbar so nobody types syntax (the link button opens an inline URL popover, never
  `window.prompt`, #228), and a Write ↔ Preview toggle — because a heavy editor bundle fights
  *"snappy over clever"* on an SSR/PWA shell. This is the design-language rule; it is not a task
  feature.
  **Which fields get it, and which stay plain:** the *long-form* ones — a task/checklist/checklist-
  item description, a comment, project/company/contact notes, invoice/quote/subscription notes,
  a custom-field `LONG_TEXT` — get the editor, **including the same field inside a template**
  (a subscription template's notes, a task template item's description, #228). One-liners do
  **not**: a title, a `TimeEntry` description, a leave note. Rich text is for text that has
  structure to gain from it, not for every string. Headings render `###` and deeper only —
  `h1`/`h2` stay stripped everywhere (`core/markdown.ts`): notes and descriptions are supporting
  text, and a uniform rule beats a per-field exception.
  **Rendering is the security boundary.** `{@html}` lives only in `Markdown.svelte`, and everything
  it prints has been through DOMPurify in `core/markdown.ts`; the API also strips raw HTML on write
  (`core/richtext.py`) so a stored value is inert even for a consumer that renders it another way.
  Any consumer that must show the words *without* the markup — a notification excerpt, an email, a
  PDF, a `DataTable` cell — flattens to plain text first (the API's `markdown_to_plaintext`); it
  never truncates raw markdown by character count, which severs a link mid-`()`.

## Navigation

- Sidebar: Dashboard and Agenda open it, Overzicht (managers) and Instellingen (managers)
  close it — those four are fixed core items. Everything between is **module-contributed**
  (Klanten, Contactpersonen, Interacties, Projecten, Taken, Uren, Verlof, …), ordered by each
  module's declared `position` **as the default only** (#169): an org admin sets a team-wide
  order/visibility under Instellingen → Navigatie, and each person can override it for
  themselves (Account → Mijn zijbalk) — resolution is own row → org default → declared
  positions, `DashboardPref`'s model exactly (`NavPref`). Hiding applies to module items
  only; the fixed core items are not anyone's to hide. A module enabled after a layout was
  saved still appears (fallback to its declared position), so a pref can never make new
  functionality invisible. Icons from lucide; collapsible to an icon rail; on mobile it is a
  drawer behind the hamburger — the saved order carries over unchanged.
- **Agenda is a core surface like the dashboard**: the month view composes event feeds that
  modules contribute via the registry (`calendarSources`) — today the team's approved/pending
  leave; Google Calendar plugs into the same seam in P3. Pending items render muted with a
  "?"; on mobile the grid becomes a per-day agenda list.
- Sections with multiple surfaces use **submenu tabs** at the top (Taken | Sjablonen;
  Verlof: Mijn verlof | Team; Overzicht: Uren | Productiviteit | Omzet; Abonnementen:
  Abonnementen | Standaardabonnementen | Abonnementstypes) — not nested sidebars. The
  convention (owner call, #229): the tab row sits at the **very top of the section, above
  the page heading**, rendered by the section's `+layout.svelte` as pill-styled `<a>` links
  to sub-routes, each tab gated on its own permission — `/overview/+layout.svelte` is the
  reference. Plain links, no Tabs primitive; a viewer whose permissions leave only one tab
  gets no tab row at all. Every tab that lists rows is a full `DataTable` (filters, sort,
  personal columns), not a card list.
- **A catalog staff touches day-to-day is a tab on the working page, not an Instellingen
  screen** (#229, after the task-templates precedent). The Instellingen index card deep-links
  to the tab (`/subscriptions/templates`, like `/tasks/templates`), and a retired settings
  route 301-redirects there so old links keep working.
- The header holds only the profile menu (avatar → name, personal settings, logout).
  Language lives in personal settings, not the header.

## i18n & theming

- Every string through `t()` with keys in `messages/en.json` (source) **and** `nl.json`
  (complete, natural Dutch) in the same change. Dutch is the default UI language.
- **Tenant-entered translations are always optional** (owner policy, 2026-07-17). App strings
  ship complete in both locales; the *tenant's own* labels (leave types, contact types, custom
  fields, e-mail templates, roles, …) never demand both languages — one language is enough and
  a missing locale falls back to the other at render time. Editors use the shared
  `core/ui/I18nTextField` — **one field with an NL/EN switcher, never two side-by-side
  inputs** — which posts every locale (`label_nl`/`label_en`) so form actions stay unchanged,
  and deliberately carries no `required` (a required attribute on a hidden locale input blocks
  the submit invisibly). The e-mail template editor follows the same switch-a-language shape
  with its per-locale forms.
- Branding (logo, colors, brand name incl. hide-name option, favicon) is runtime, per
  tenant, via Huisstijl — never hardcoded. Charts use their own validated, colorblind-safe
  palette (see the dataviz procedure), not the tenant color.

## Known mistakes to not repeat

- Buttons that configure org-wide behaviour placed inside a working screen (the old "save
  as team default" on the dashboard) — config goes to Settings.
- Native date and time inputs (US format, AM/PM clock, popup anchored to the window corner) —
  assuming a native control honours our locale hints when it does not.
- Two favicon `<link>`s competing (static + tenant) — exactly one, tenant-driven.
- Edit-everything screens with no read/use mode — cards got an explicit mode split.
- Refetching all lookups on every filter/tab navigation — that's what layout loads are for.
- A desktop-only sidebar with no mobile navigation at all.
- Bare **Delete** / **Edit** buttons exposed on a row or header (accidental-click magnets) —
  they belong in the ⋯ `ActionsMenu`, and every delete confirms via `ConfirmDialog`.
- A ★ (or any emoji/glyph) marking the primary chip on top of its brand colour — the colour is
  the marker, the glyph was noise. Meaning that colour alone carries goes in an `sr-only` label.
- Chip fields that were editable in use mode: a stray click could detach a contact or move the
  primary. Linking, unlinking and promoting are definition changes and live behind edit mode.
- A burn bar clamped at 100 % (`Math.min(100, pct)`): a project 40 % over budget drew exactly like
  one that had just landed on it. Clamp the bar, never the number.
- A hardcoded `<ul>` per list. Six of them and no user could hide a column; the seventh is what
  `DataTable` exists to prevent.
- Taking `.date()` of a UTC instant to name a local day. Amsterdam's midnight is 22:00 UTC the day
  *before* in summer, so a monthly budget reported its period as starting 30 June. Half the year the
  bug is invisible, which is why it is pinned on a fixed date rather than on `today`.
- A totals row summed from `rows`. The page holds 200 of a longer set, so it prints the total *of
  the page* — which looks exactly like the right answer. Totals come from the API.
- **A flex or grid item without `min-w-0`.** Its `min-width` defaults to `auto`, so it is sized by
  its widest descendant instead of by the row. The shell's content column had no `min-w-0`, so one
  over-wide page did not scroll or clip — it *grew the shell*: `<body>` laid out at 716 px on a
  360 px phone while `initial-scale=1` kept one CSS pixel on one device pixel, the right half fell
  off screen, and the app read as "loaded zoomed in" (#36). Pinch-zooming out revealed the whole
  document, which is why the layout looked fine. The rule holds for grid items too. Never reach for
  `maximum-scale`/`user-scalable=no` (an accessibility regression) or a body-level `overflow-x:
  hidden` (it hides the next bug as well as this one) — make the document actually fit.
- **A toolbar that cannot wrap.** Title + a fixed-width `SearchInput` + the Kolommen picker + the
  primary button on one flex line has a min-content width around 490 px, which no phone has. Give
  the toolbar its own `flex-wrap` row, the way the clients list does.
- **A flex `<input>` without `min-w-0`.** `flex-1` alone cannot shrink it: a form control keeps its
  browser-default width (~228 px) as its min-content floor, so the row it sits in never fits a
  phone. This is not the same thing as an explicit `min-w-[12rem]`, and it is easy to clear the
  wrong suspect.
- **A raw `{@html}`, anywhere but `Markdown.svelte`.** Before #66 the app had zero `{@html}` and so
  zero XSS surface on user text — rich text deliberately took that on, in exactly one audited place
  that sanitizes first. A second `{@html}` (or piping markdown into an email/PDF without the shared
  render path) reopens the hole the one component exists to close. Route it through `Markdown`.
- **Feeding raw markdown to something that shows plain text.** A notification excerpt, a truncated
  cell, a `title=` attribute — given `**bold** [x](url)` it prints the syntax, and cutting it by
  character count can sever a link mid-`()`. Flatten with `markdown_to_plaintext` *before* the cut.

- **Dutch copy avoids the English em dash.** The "X — Y" construction that reads naturally in
  English is not correct Dutch and had crept through the whole UI and site. In `nl.json`, the
  site's Dutch content and the Dutch docs: use a **colon** when the second part explains the
  first ("Opgeslagen: 3 dagen ingepland"), a **semicolon or comma** for an afterthought
  ("…vergrendeld; vraag een beheerder"), or **parentheses** for status labels ("Verlopen
  (respijtperiode)"). A real *gedachtestreepje* (a paired, spaced aside mid-sentence) remains
  legitimate but rare — `leave.recurring.hint` is the reference example. Ranges keep the en
  dash without spaces (`ma–vr`, `{from}–{to}`), and the `—` empty-value placeholder in tables
  stays. English strings are unaffected; this is a Dutch-only rule (owner feedback, 2026-07-12).
