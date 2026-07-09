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
  In the client-state pickers (`AssigneePicker` for the employees on a client or project,
  `ContactDraftField` for contacts on a not-yet-created client) **clicking any other chip promotes
  it**, so the marker never doubles as a control. `LinkField` is the one exception: its chips
  navigate to the linked record, so it cannot promote on click and keeps a separate small control.
  Every chip carries an ✕ to drop it. `AssigneePicker` posts the whole roster in one hidden field
  (an edit surface has exactly one save button); `LinkField` posts per chip, because there each
  link is its own record. Detail headers name the primary and render the rest as an `AvatarStack`
  of initials. **"Mine" filters match any assignee, never only the primary** — otherwise the
  feature is invisible to everyone but the owner.
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
- **Forms are SSR form actions** with `use:enhance`. Mind the default reset: forms whose
  inputs must keep their values after save use `update({ reset: false })`.
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
