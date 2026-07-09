# UX conventions — vlotr

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
- **Pickers are type-ahead comboboxes** (`core/ui/Combobox`), never long native selects.
  Typing an unknown name offers "＋ … toevoegen", opening a *full* create dialog — real
  fields plus the tenant's custom-field definitions from the API, prefilled with what was
  typed. Never a name-only stub form.
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
- **One shared row/tile per concept** (`TaskRow`, panel rows): title link, chips (labels,
  checklist n/m, ⏱ allocated), red overdue date, assignee initials — identical wherever the
  concept appears.
- **Drag-and-drop with graceful fallback**: reorder tasks and dashboard tiles by dragging
  (fractional `position` midpoints — never renumber); keep an arrow/menu alternative where
  dragging is impractical.
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
  `checklist_deleted` / `checklist_item_deleted`, …).
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
- **Verlof is tracked in hours, shown with a days equivalent** (`≈ n dagen` from the
  employee's contract hours ÷ 5). Employees request under Verlof (balance cards + one
  request form; hours pre-filled from the date range, editable for part days); managers
  approve/reject under Verlof → Team (approve/reject are inline status actions; reject asks
  an optional reason) and register leave on someone's behalf (ziekmelding). Org config —
  verloftypen (wettelijk/bovenwettelijk carry-over rules live here, not in code), contract
  hours, and yearly saldi — lives under Instellingen → Verlof. Approved leave appears on the
  timesheet as its own teal row, never mixed into worked totals, and on the Agenda.

## Navigation

- Sidebar: Dashboard → Agenda → **Relaties** (Klanten / Projecten / Contactpersonen as a
  collapsible group) → Taken → Uren → Verlof → Overzicht (managers) → Instellingen
  (managers). Icons from lucide; collapsible to an icon rail; on mobile it is a drawer
  behind the hamburger.
- **Agenda is a core surface like the dashboard**: the month view composes event feeds that
  modules contribute via the registry (`calendarSources`) — today the team's approved/pending
  leave; Google Calendar plugs into the same seam in P3. Pending items render muted with a
  "?"; on mobile the grid becomes a per-day agenda list.
- Sections with multiple surfaces use **submenu tabs** at the top (Taken | Sjablonen;
  Verlof: Mijn verlof | Team; Overzicht: Uren | Productiviteit | Omzet) — not nested
  sidebars.
- The header holds only the profile menu (avatar → name, personal settings, logout).
  Language lives in personal settings, not the header.

## i18n & theming

- Every string through `t()` with keys in `messages/en.json` (source) **and** `nl.json`
  (complete, natural Dutch) in the same change. Dutch is the default UI language.
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
