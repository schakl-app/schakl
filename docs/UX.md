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
  **A nested quick-create inherits the parent form's context** (#247): when the outer form already
  has a client selected and its picker opens a quick-create for a second entity (a project, a
  contact, a hosting account), that dialog opens with the *same* client pre-filled — as a default
  the user can still change, never a blank field they must re-answer. Re-asking wastes the work
  they just did and risks silently orphaning the new record onto a different client. Thread the
  known client id through the create component (`companyId` / `initialCompanyId` / `linkCompany`,
  as the working examples do) **and** make sure the server action behind it actually reads and
  sends that id — a pre-filled checkbox the action ignores links nothing. This is easy to
  reintroduce one field at a time, so when you add a new inline quick-create, check it forwards
  the context the parent already knows.
  **And the auto-select must not fire on a form result it did not ask for.** A quick-create
  answers through `page.form.inlineCreated`, which outlives the dialog that produced it: a picker
  living in a per-record dialog (the contactmoment review, keyed on the row) is a *fresh instance*
  the next time it opens, and one that trusts whatever is already on `page.form` will pre-select
  the project created for the previous record — pre-filled, plausible, and filed onto the wrong
  row the moment the user approves. Seed the "already handled" id from `page.form` at mount, so
  only a create made by *this* instance is acted on.
  **And the context narrows the picker, not just its ＋: a picker on a form that has a client
  lists that client's rows, and keeps narrowing as the client changes.** The contact picker on a
  contactmoment offered the agency's whole address book. Two shapes did it: it read only the
  client the *host page* had pinned — so on Interacties, where nothing is pinned, it never
  narrowed at all no matter which client the moment was being filed to — and a scoped fetch that
  came back empty quietly widened back to the org, so a client with no linked contacts got
  everyone. Both end the same way: a call logged against someone at another client, which reads
  as perfectly ordinary on every screen afterwards because nothing downstream cross-checks it.
  Scope on the form's **effective** client (pinned, picked, or backfilled from a project/task
  pick), re-fetch when it changes, and drop a selection the new client does not know — the
  cascade already does exactly that to a task when its project changes. Say so when you drop
  one, rather than leaving the field to look like it blanked itself. And do not fall back to the
  unscoped list when a client's is empty: that is a real answer, and the ＋ is what turns it into
  one row, pre-linked to the same client.
  **The ＋ belongs to the component that draws the picker, not to the page that happens to host
  it.** The obvious wiring is a callback — the form raises "the user typed a name that isn't
  there", the page owns the dialog and the action. It reads cleanly, and it silently makes the
  affordance *per host*: the contactmoment form's client and project ＋ were passed in by
  `/interactions` alone, so the same component rendered on a company, project, contact or task
  page had none, and even that page's own **edit** modal — three lines below the create one —
  was never wired. Nobody notices, because each screen looks deliberate on its own. So put the
  quick-create dialog **inside** the component and post to an action the module exports
  (`interactionActions`), which every host already spreads: the ＋ then arrives with the panel
  instead of having to be remembered per screen. The host-owned shape is also what let a page
  quietly ship a *stub* form — the one this replaced wrote a name and a client, skipping the
  billable flag and the tenant's own project custom fields — because the real dialog and the
  picker it serves had drifted onto different screens.
  A self-contained dialog needs its custom-field definitions without the host's load, so it
  fetches them on **first open** and holds "Aanmaken" until they land (`ProjectQuickCreate`,
  `CompanyQuickCreate`). That is also the cheaper shape: the definitions used to ride every
  single page load for a modal most visits never open.
  **And the ＋ is a write control, so it self-gates on the API's own permission** (CLAUDE.md §15) —
  the timeline is client-reachable, and `!isPortal` is not the gate.
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
  client or project, `ContactDraftField` for contacts on a not-yet-created client,
  `ContactChips` for the people a contactmoment was with (#300) — are always
  interactive, because the surface itself is already edit mode.
  `AssigneePicker` posts the whole roster in one hidden field (an edit surface has exactly one
  save button); `LinkField` posts per chip, because there each link is its own record.
  **"Mine" filters match any assignee, never only the primary** — otherwise the feature is
  invisible to everyone but the owner.
  **A picker that builds a list keeps its list open** (`keepOpenOnSelect`): the chip appears, the
  field empties, and the next name is one click away. Closing after each pick was not merely
  brisk — it was a **dead end**, because the only thing that opened the list was the input's
  `focus` event, and the mouse never left the input to fire another one. Adding three people to a
  contactmoment meant click-away-click-back twice, on a control that looked broken while it did
  it. The general half of that fix stands under every picker, single-value ones included:
  **clicking the field opens the list, whether or not it was already focused** — a closed dropdown
  under a focused cursor is a state the user has no gesture for. Everything else about a single
  pick is unchanged: it still closes, because there the pick *is* the answer.
- **One person, one shape: `PersonChip` — avatar *and* name, together.** Read-only surfaces that
  show people (list cells, detail headers) render every person the same way through `Assignees`:
  the verantwoordelijke first in the plain text colour, the rest muted. Naming the first person in
  full and degrading the rest to bare initials discs — what `AvatarStack` used to do — puts two
  different renderings of a person in one row, and the second one reads as a badge rather than a
  colleague. A cell that runs out of room drops *people* — a table cell names the verantwoordelijke
  and counts the rest (`+3`, all of them named in the tooltip), so a row never grows a second line —
  and never strips the names off the people it keeps. The bare `Avatar` disc stays the right call
  where a surface shows **only** discs and nothing else: `TaskRow`'s meta strip, the profile menu.
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
  dragging is impractical. The arrows are not a fallback nobody uses — they are the only reorder a
  keyboard or a screen reader can reach, so both gestures ship together and both produce the same
  thing.
  **A short, bounded list states the whole order instead** (a task's checklists and the items
  inside one, `POST …/checklists/order`): midpoints exist to avoid a large write, and there is no
  large write here — a handful of rows renumber in one statement, which is also the only shape that
  cannot half-apply or drift between two clients trading midpoints. The payload is a statement
  about *order*, never about membership: a row it omits keeps its relative place after the named
  ones, so a list a colleague added mid-drag is appended rather than dropped or 409'd.
  Two traps in the Svelte half, each of which shipped a bug once (`tasks/[id]/+page.svelte`):
  **`$state([])` filled by an `$effect` server-renders nothing** — an effect does not run on the
  server, so the whole section appeared a frame after hydration — while **a writable `$derived`
  renders but hands `svelte-dnd-action` an array it does not own, and the drag never starts.**
  Initialise the state inline *and* re-arm it from the record with an effect. And a zone whose rows
  hold checkboxes, menus or inputs stays `dragDisabled` until a grip takes the pointer down, with
  its **own** flag per nesting level — one shared flag lets an item's grip arm the list around it.
- **Every dashboard widget is a bordered card, via `core/ui/DashboardWidgetCard`** (#166). The
  dashboard grid wraps each tile in a bare `<div>` — the card chrome (border, `bg-surface-raised`,
  padding, title row with an optional "show all" link) is the widget's own responsibility, and the
  shared wrapper is how it stops being re-typed per widget. Both the empty and the populated state
  render inside the card; a bare `<p>` sitting naked in the grid is the bug this rule exists for.
- **Nothing on a dashboard tile is a dead end** (#15, extended). A tile exists to be left: a
  *record* it names opens that record, and an *aggregate* opens the list it is a total of — with
  the filter that makes the two numbers agree, not the module's front door. So a per-client count
  carries `company_id`, an overdue badge adds `due=overdue`, "team deze maand" splits into the
  time report and the omzet page, and a client's next invoice opens the subscriptions list
  filtered to them. Two rules keep it honest. **A bucket with no record behind it still needs a
  destination**: the open-tasks tile's "everything hanging off no client and no project" row had
  none, so the API grew `?unlinked=1` — inventing the filter is the fix, dumping the reader on an
  unfiltered list is not. And **a fallback label may never be a word a tenant could have used**:
  that same row borrowed `time.general` ("Algemeen"), which read as a real project, sat beside
  real projects, and on an instance that *had* one appeared twice. It says
  `tasks.filter.unlinked` now — the same words as the chip on the list it opens, so the
  destination confirms where you landed. The only text left unlinked is empty-state copy and a
  restatement of a figure already linked beside it.
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
- **A comment thread is one level deep, and answering is not "editing"** (#312). Replies hang off
  their opener under a single left rule at one indent — a second level indents itself off a phone
  and gives two readers two different reading orders, so the API *re-roots* a reply-to-a-reply
  onto the same thread rather than refusing it. **Reply** is therefore an ordinary inline control
  under the message (use mode), not an ⋯ item; the ⋯ menu stays for Edit/Delete. Opener and answer
  render through **one snippet** — they differ in where they sit and how loud they are, never in
  what they can do — and the reply composer seeds an `@mention` of whoever is being answered, so a
  thread with three people in it still says who a given answer is for. Two consequences to keep:
  deleting an opener **takes its answers with it** (`ON DELETE CASCADE`), so the confirm counts
  them out loud and the activity row says how many — an undo-less delete may never describe one
  comment while five disappear; and the composer keeps its draft on failure and closes only on
  success (`update({ reset: result.type === "success" })`), because the words are not the server's
  to throw away.
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
  - **A declared column width is a width, not a hint — so the grid never scrolls sideways.** The
    table is `table-fixed`. Under the browser's default auto layout a `width` is only advice: the
    used width is `max(width, min-content)`, and every cell here truncates with `white-space:
    nowrap`, which makes a column's min-content its whole unbroken line. `overflow: hidden` does
    not reduce that — it clips only once a definite width exists, which auto layout never gives —
    so the interacties table asked for 1210 px, laid out at 1423, and scrolled sideways on any
    laptop while its ellipsis never appeared and the resize handle wrote a number the layout was
    ignoring. A fixed layout makes all three true at once. Two obligations come with it, because
    a fixed layout cannot invent slack: **exactly one column carries no width and absorbs the
    rest**, and **every other column a list shows by default needs a sensible width**, because
    the ones without share the remainder equally — eleven equal columns is its own kind of wrong.
    The absorbing column is the `primary` one by default, which is right wherever the identity
    column is also the long one; a list where it is not says so with `flex` (an invoice is
    identified by a number 130 px wide, and the widest thing on its row is the client). A list whose trailing ⋯ cell holds more than the ⋯ says so
    with `actionsWidth`; a column no longer widens to its content, it paints over its neighbour.
    And a `truncate` span must be `block`: `overflow` does not apply to an inline box, so a bare
    one sets `nowrap` and nothing else, and spills instead of ellipsizing.
  - **Every list ends in a pager, and the pager is the address bar** (`core/ui/Pagination.svelte`,
    docs/PERFORMANCE.md). A list is where the whole set lives, so it never shows a prefix of
    itself: the bar states the honest range ("51–100 van 812"), offers **25 / 50 / 100 / 200** per
    page, and appears only once there is more than one page — a pager over nine rows is
    decoration. Three things it must keep being:
    - **Links, not buttons.** `<a href="?page=3">` is what gives the back button, middle-click,
      preload-on-hover and a page you can send someone. Opening a client from page 4 and coming
      back to page 1 was the bug; the URL carrying the view is the fix, and SvelteKit restores
      the scroll position on top of it.
    - **Reset by every filter.** Search, a status pill, a client picker, a re-sort — each drops
      `?page=` (`resetPage`), because page 7 of the old filter is usually nothing at all in the
      new one, and an empty page reads as "the filter found nothing".
    - **A saved size, a per-visit page.** How many rows you want is a personal preference and
      rides in `prefs.tables.<list>.page_size` beside the column layout (UX Principle 6, never an
      org setting). *Which* page you are on is not a preference at all — it belongs to the URL,
      or two tabs would fight over one number.
    On a phone the numbered pages give way to "Pagina 2 van 17" with prev/next: twelve tap
    targets six pixels apart is not a control. And because a page is a slice, a **group heading
    inside one counts the slice** — a sectioned list says so out loud rather than letting
    "Acme (2)" read as the whole answer.
  - **Every sort is reachable from the Kolommen menu, not only from a header.** Below `sm` there
    *are* no headers, so a header-only sort is a sort mobile users don't have. The menu is the one
    surface both sizes share: each sortable column carries an ↕ that cycles ascending → descending →
    off, and the active sort is named at the top. Headers stay clickable on desktop; they are the
    shortcut, never the only way in. Sorting by a *person* (assigned employee) orders by their
    display name — never by a user id, which is what a naive `ORDER BY` on the FK would do.
  - **A list that can travel by spreadsheet says so on the list.** Export and Import are one
    shared component (`core/impex/ImpexBar`) sitting beside the Kolommen popover on every such
    screen — clients, contacts, projects, taken, urenstaat, abonnementen and their two catalogs,
    domeinen, websites, hosting, domeintarieven. Not a per-page decision: the first round shipped
    the pair hand-written on two screens and absent from the other ten, so someone holding a
    spreadsheet of domains had to guess it lived in Instellingen. Export carries the list's
    **current** filters (so the file is the list on screen, whole — not the loaded page), Import
    opens the shared wizard, and both controls check the bulk permission *and* the entity's own
    before they render, mirroring the two gates the API declares. Instellingen → Import & export
    stays as the overview of what can travel at all; it is never the only way in.
  - **Acting on several rows is a mode, and the ✎ is how you enter it** (`core/bulk/BulkToggle`
    + `core/bulk/BulkBar`). A list is for reading, so there are **no checkboxes until someone
    asks for them**: pressing ✎ is what turns the list into something you are editing. Pressing
    it again puts the list back and drops whatever was picked — a selection nobody can see must
    not survive to be acted on by the next thing that opens. Where each half lives is the rest of
    the rule:
    - **The ✎ is the last control in the toolbar, on every list**, after Kolommen. It is the only
      one there that changes what the *rows* do rather than what the list shows, so it sits apart
      from them rather than among them — and a list whose Export/Kolommen cluster is not already
      right-aligned gets `ml-auto` so the ✎ lands in the same place everywhere.
    - **The actions are their own strip, above the table** — the brand-tinted frame this app uses
      for a live selection, holding the count on the left and the buttons on the right. They are
      not more toolbar: Export changes what you *get*, Verwijderen changes what *is*. Rendering
      them inline made the toolbar reflow every time the mode opened and made the new controls
      read as more list chrome.
    - They are **disabled until something is ticked**, with the reason in the title, because in
      this mode the buttons are the point and hiding them would leave a mode whose purpose is
      invisible. When the user holds neither the entity's write nor its delete, the ✎ is not
      drawn at all — there is no mode to enter.

    Two earlier shapes were wrong and are worth naming: a bar that appeared *as you ticked* moved
    the table down mid-gesture, walking the rows away from the cursor on a list you tick
    top-down; and a permanently visible checkbox gutter made every reader pay for a writer's
    feature.
  - **A bulk action says what it will actually do, and reports what it did** (#299). A selection
    is rarely uniform — the interacties list mixes still-pending emails with reviewed ones, and
    someone else's mailbox with your own — so each button acts on **its own eligible
    subset** and carries that count whenever it is smaller than the selection ("Goedkeuren (2)"
    over eight rows), and is disabled at zero. A button that silently did less than it said is the
    failure this prevents. Afterwards the page states the honest outcome — "6 goedgekeurd · 2
    overgeslagen", with the distinct reasons (`core/bulk/BulkResult`) — because the API reports
    ineligible rows instead of rolling the good ones back (raising mid-batch would undo the
    forty-nine that worked), and a UI that swallowed that would be claiming work it did not do.
    The eligible-subset filter is still only UX: the API re-checks every row, so the menu may
    narrow the batch but never widens it.
  - **A bulk action that produces a file is a link, and a limit it can exceed says so** (#307).
    The invoices list's Download hands over the ticked invoices as one zip, and it is an `<a
    href>` with `data-sveltekit-reload` — a download is a *navigation*, so middle-click and
    "save as" work and there is no handler pretending to be one (the `ImpexBar` rule, applied to
    a selection). Two things separate it from the buttons beside it. Its subset follows the same
    rule as everything else here — a draft has no document, exactly as in the row menu, so it is
    excluded and the count says so — but a **cap** is not a subset: over the fifty the API will
    render, "Download (50)" would state a number and still leave *which* fifty to chance, so the
    control refuses and names the limit (`BulkAction.disabledReason`) instead. And it needs no
    write permission at all: the page's own read is the gate, which is what lets a client
    download their own invoices in one go and is why the ✎ appears for a reader on this list.
  - **A field you did not touch is not sent** (`core/bulk/BulkEditDialog`). The edit dialog opens
    blank over a selection that disagrees with itself — twelve domains at four registrars — so an
    empty control can only honestly mean "leave each row's own alone". Reading it as "empty them
    all" would wipe, on every row the user never looked at, exactly the value they had not thought
    about. **Clearing is therefore a separate, deliberate tick**, offered only where the field can
    be cleared at all, and labelled with what clearing *means* where "empty" understates it — a
    domain with no invoicing decision follows its register (#298), it is not "not invoiced". The
    same rule decides which controls may appear here: every one of them needs an "unchanged"
    state, which is why there is no party picker (it always holds a type) and why a yes/no field
    is a two-option type-ahead rather than a checkbox (which is always either ticked or not).
  - **Selecting the rows has to be cheaper than acting on them.** A bulk bar over a queue of forty
    auto-matched emails is worth nothing if reaching it costs forty clicks, so **shift extends the
    selection** from the last row ticked to the clicked one, and it does so from the row as well as
    from its checkbox — a reviewer reaching for a range must never be answered with a detail modal.
    The span takes the state the clicked row is moving to, and it walks the **visible** order only:
    a range that quietly swept up rows inside a collapsed section is how a bulk reject reaches an
    email nobody read. The gutter **cell** is the checkbox's hit area, not the 16 px box in it —
    every list here opens the record on a row click, so a near-miss was not "nothing happened", it
    was the wrong dialog over the tick the user meant. Two browser details make or break that pair
    and are commented at the seam: a stretched `<label>` is also what keeps the near-miss out of the
    row handler, and the click is handled *on* the label because chrome suppresses a label's
    forward-to-its-control the moment shift turns the click into a text-selection gesture.
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
  - **A row may belong to several sections.** `groupBy` may return more than one key, and the
    record is then drawn under each — a contact linked to two clients is listed under both,
    because the alternative is picking one client to be "theirs" and that fact does not exist
    (`is_primary` marks the primary contact *for a company*, not a person's primary company).
    It is one record shown twice, not two, so the id repeats across sections and never within
    one. A row that matched *no* declared section still falls to "Overig" — but one that matched
    at least one does not, or a two-client contact whose second client fell off the page would
    appear both where it belongs and under "Overig". The grouped-by column may still be worth
    keeping when it says something the section heading cannot: from inside the Acme section, the
    client chips are the only thing that says this person also sits under Globex. It loses its
    `sortKey` rather than the whole column.
  - **A sectioned list must say when it is only a page.** Counts in section headings read as
    complete answers — "Acme (2)" above a client that has seven contacts is a wrong answer, not a
    partial one. A capped list therefore prints what is on screen out of the total, and says how
    to narrow it (`contacts.truncated`). A cap is reported, never silent (docs/PERFORMANCE.md).
  - **A list is filtered by one shared bar, and the bar owns the whole strip**
    (`core/filters/FilterBar.svelte`, `core/filters/types.ts`). Each screen had grown its own
    copy of the same twelve lines — a `setFilter` that rebuilt the URL, a `resetPage`, a `goto`,
    a pill loop, a "wissen" link — and copies drift in ways nobody notices: the domains list
    read `?q=` in its load and never rendered a box for it, so the filter existed and was
    unreachable; the abonnementen list's "wissen" deleted three hand-named keys, so a fourth
    filter would have survived being cleared. A screen now declares `FilterDef[]` — `search`,
    `select`, `pills` — and gets the rest. Four rules ride along with it:
    - **The URL is the view, on both sides.** A def carries no value: the bar reads the current
      one off `page.url`, which is where the `+page.server.ts` load reads it too (`readFilters`),
      so the controls and the request cannot disagree. `value` is the one escape hatch, for a
      filter whose *absent* state is not its empty state (taken opens on **your** tasks).
    - **A filter is a query parameter or it is not a filter.** Narrowing `data.rows` in the
      browser filters the page that happened to load and leaves the total counting everything —
      the bug this file already names below. If an API cannot express it, that is a missing
      query parameter, not a licence to slice in the browser.
    - **"Wissen" clears the bar's own keys and nothing else.** Not `href="/domains"`, which also
      throws away the sort and the page size the user chose; those are not filters.
    - **Below `sm` the filters collapse behind one toggle carrying a count, and the actions do
      not.** Six stacked controls push the rows a screen down, but Kolommen and the ✎ are not
      filters and hiding them under "Filters" would misname both. The count is what stops a
      *collapsed* bar from silently explaining an empty list — and an empty list under a filter
      says `common.no_results`, never "je hebt nog geen domeinen", which sends the reader
      hunting for the wrong problem.
- **A panel is the first page of the list it links to.** A client card shows five domains and
  five websites and then hands over — but only if the three things that make the hand-over
  honest hold. It shows the **whole count**, not the shown one, because five rows with nothing
  to contradict them read as the complete answer (the truncated-total failure, #37, in
  miniature). Its **deep link carries the filter** (`/domains?company=<id>`) rather than dumping
  the reader on the unfiltered register to find the client again. And the API provider uses the
  **same sort the list defaults to**, so "Alle 23 bekijken" continues where the card stopped
  instead of reshuffling into an order the reader has to re-scan. The `?company=` on that link
  is the same parameter the card's `＋ nieuw` already used, and it now does both jobs: it filters
  the list *and* prefills the create dialog, because they are one intent and two parameters
  would let them disagree.
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
- **Hours reach an agreement through its project, never through a second picker.** Logged time
  attaches to a **project**, and a project covered by an active subscription burns against that
  agreement's included hours (#225) — so the timesheet's entry form no longer offers a subscription
  to log against. Two pickers meant two places a retainer's remaining hours could be counted, and
  they could disagree. What replaced it is the answer itself, on the screen that spends the hours:
  the picked project's **remaining** hours under the project field (named after the agreement they
  came from: "Uit: Onderhoudsabonnement"), and a **Beschikbare uren** panel beside the form listing
  every budgeted project on the one burn scale, hottest first. Both render from the project lookup
  the page already loads with `hours=true` — a number this useful should cost no extra call, and the
  form asks for one *less* than it did. The "no budget" hint only appears when the caller actually
  asked for the burn: a lookup fetched without `hours=true` knows nothing, and silence beats a
  confident "geen urenbudget" that is really "didn't look".
- **Forms are SSR form actions** with `use:enhance` — and **a form that stays mounted after a
  successful save always passes `update({ reset: false })`.** The default success path calls
  `form.reset()`, which rewinds every control to its server-rendered default *without firing any
  event Svelte can see*. On a persistent edit surface (a settings `?/save`, an inline editor, the
  roles matrix, the notification matrix) that is not cosmetic: state-backed controls
  (`FormCheckbox`, `bind:group` radios, state-driven selects) keep the new value in state while
  the DOM shows the old mark — the save *looks* undone, and the **next** submit posts the rewound
  DOM marks, silently stripping what the user saved a minute ago. This shipped as a live bug on
  Rollen, Gebruikers → rollen, and the notification matrix at once (#253). So: bare `use:enhance`
  is for one-shot forms only — a delete/toggle/test button, or a create form that unmounts or
  re-keys itself on success. Everything else says `reset: false`. `FormCheckbox` additionally
  re-asserts its own state after any form reset, so a forgotten callback can no longer strip
  checkbox marks — but radios and selects have no component guard; the form-level rule is the
  convention.
  - **Read a posted checkbox by its presence, never by its value** — `checked(form, name)` from
    `$lib/core/forms`. A checkbox posts *its own* `value` and an unticked one posts nothing at
    all, so any comparison naming a particular string is a bug waiting for somebody to change
    the control. `FormCheckbox` sends `"true"`; a bare `<input type="checkbox">` sends `"on"`;
    reporting's actions compared against `"on"` while drawing `FormCheckbox`, so **every**
    checkbox in the module posted `false` whatever the user ticked. It cost that module its
    default report template — and therefore the design, accent and cover image of every
    generated report — and it switched a client's reporting profile to inactive on every save.
    Nothing about it is visible in review (the literal looks plausible) or in use (the box is
    ticked on screen; only the next page load disagrees), which is exactly why it is a helper
    rather than a corrected string.
  - **A setting with three states is a select, not a checkbox.** `NULL` = inherit is the
    house idiom (§14), and a box cannot tell "off" from "not chosen". `triflag(form, name)`
    reads one; the empty option is inherit, beside the cadence and delivery fields that already
    work that way. Reporting's per-client "publiceer in portaal" was read by an action that
    expected a hidden marker field the page had never drawn, so the override existed everywhere
    except where a user could reach it.
  - **A `bind:value` text field is the worst case: pressing Save empties it in front of you.**
    The rule above was written about *marks* (a checkbox rewinding, a select snapping back), which
    undersold it and let the same bug ship again on Instellingen → Bedrijven and → Facturatie
    (#77). A bound text input has no `value` **attribute** — Svelte sets the property — so its
    `defaultValue` is `""`, and `form.reset()` blanks it. Svelte listens for `reset` to keep
    bindings truthful, so it then writes that emptiness **back into your state**: the content is
    destroyed, not just hidden, and the next submit posts the blank. The user's word for it is
    "I pressed save and my text disappeared", and they are describing data loss.
  - **The affordance: `busy.keep(key)`** (`core/submit.svelte.ts`) — `wrap()` with
    `reset: false`, named for the intent. Reach for `keep()` on any form that edits something
    that already exists, and `busy.clear(key)` when you actively want the form emptied for the
    next entry. Choosing between two named methods is a decision; remembering to hand-write a
    `reset: false` callback is a thing to forget, and twenty components had each re-derived it.
  - **The affordance was not enough on its own, so the rule is now enforced**
    (`scripts/forms-check.mjs`, `pnpm forms:check`, run in CI's web job and by the pre-commit
    hook on any staged `.svelte`). It shipped a *third* time after the two above — Instellingen
    → Facturatie again, where the page's defaults block had been given `keep()` and the seller
    block one section above it had not, so editing the agency's own company name and pressing
    Opslaan emptied all eleven fields at once. That is the tell: this bug is not a screen anyone
    forgot, it is a *form* anyone forgets, and it hides next to forms that got it right. The
    check reads every `use:enhance`d form, and if it carries a control the user types into it
    demands the intent be **stated**: `keep()`, `clear()`, or an explicit `reset:` in your own
    callback (`reset: !entry` — the time entry form, which creates *or* edits). `clear()` does
    exactly what bare `use:enhance` already did; it exists so that emptying a form is something
    someone chose rather than something everyone inherited. Nothing is exempt by naming or by
    folder — a form that genuinely wants the reset takes one word to say so.
  - **The component guard for text is `defaultValue`.** A shared field that owns a `bind:value`
    input should also set `defaultValue={/* the value it mounted with */}`, so a reset restores
    the saved value instead of blank — the text-input analogue of what `FormCheckbox` does for
    marks, and what makes the field safe in a form whose author forgot the rule. See
    `core/ui/NumberFormatField.svelte`.
- **Loading / in-flight state: a button whose request is under way says so** (#242, #279). A
  `use:enhance` submit with no feedback reads as broken on a slow connection and takes a
  double-click as a double-submit. The wiring is `core/submit.svelte.ts` (`InFlight`): one
  `const busy = new InFlight()` per component, `use:enhance={busy.wrap(key, existingFn?)}`
  around the form — it preserves the form's own callback exactly (`reset: false` included; no
  callback means plain `update()`, like bare `use:enhance`) — and the shared `core/ui/Button`
  with `loading={busy.is(key)}`, which disables the button and shows the shared
  `core/ui/Spinner` beside the label (the label itself stays; no "…" rewording, so no extra
  i18n keys). A surface with one form drops the key (`busy.wrap()` + `loading={busy.active}`);
  sibling forms that mutate the same record (the contact portal's enable/resend/disable) and
  per-row action buttons key by action or row id, so the in-flight one spins while
  `disabled={busy.active}` holds the others; two submit buttons in one form (CSV import's
  preview/commit) key off `submitter` instead. Every delete already has it: `ConfirmDialog`
  owns the posting form and spins its confirm button itself. `Button` is also where the house
  button styles live (`variant`: primary/secondary/success/danger/danger-outline — success is
  the green approve; `size`: md/sm/xs) instead of being re-typed per call site — a new button
  starts from it. Auto-submitting
  controls with no labeled button (checkbox toggles, chip ✕, hidden `requestSubmit()` forms)
  stay as they are, and so do deliberately *quiet* text-link submits (save-as-template on a
  checklist, the dashboard's reset-layout): promoting those to a bordered `Button` would
  un-quiet a surface on purpose kept calm, and quiet is a design decision, not a gap.
  `Spinner` (a lucide loader on Tailwind's `animate-spin`) is `aria-hidden` — it always
  accompanies visible text, never replaces it.
- **An uploaded image is shown, not spelled out.** A file input paired with a text field holding the
  stored address is how Huisstijl shipped, so uploading a logo wrote `/api/v1/files/<uuid>/public`
  into a box the admin then stared at: an implementation detail of where the bytes went, answering
  no question anyone has and inviting an edit that must never be made by hand. `core/ui/ImageField`
  is the shape — a thumbnail of what is set, **Bestand kiezen** / **Vervangen** and **Verwijderen**
  beside it, and a chosen file previewed locally (`createObjectURL`) and named before the save, so
  the picture is confirmed *before* it is stored. The URL folds behind "een gehoste URL gebruiken",
  because pointing at an already-hosted asset is a real if rare need; it opens by default only when
  the stored value genuinely is such an address, never for an upload. It posts exactly what the
  plain markup did (a URL field, an optional file field, empty URL clears), so no form action
  changes shape. The account page's avatar was already right — thumbnail, upload, remove, no URL —
  and branding was the outlier. Two details worth keeping: a dead address falls back to the empty
  placeholder rather than leaving a browser's broken-image glyph in the card, and the file input is
  `sr-only`, never `hidden`, because a `display:none` control is not focusable and the upload would
  be unreachable by keyboard.
- **Every upload takes a dropped file** (`core/ui/filedrop`, house convention). Eleven upload
  controls shipped as click-to-browse and nothing else, which is the one gesture people no longer
  reach for first: an attachment, a client logo, a spreadsheet and a `.eml` all arrive by being
  dragged out of a mail client or a folder. `use:filedrop` on whatever the user is plausibly aiming
  at — the thumbnail, the file listing, the field — is the whole change. It lands the files on the
  **input** (`input.files` + a bubbling `change`), never past it, so whatever the control already
  did on change happens unchanged: a multipart form really carries the bytes, a `requestSubmit()`
  still fires, a `FormData` POST still runs. Nothing about how a control uploads has to be known by
  the action, and nothing about it changes when the drop is added. Three rules hold: it is an
  **accelerator, never the only path** (the button underneath keeps working, which is the same
  fallback rule the reorder drags follow), `accept` is honoured *the way the native picker honours
  it* — a clearly wrong type is refused with `errors.upload_type`, a file the browser could not
  type at all goes through for the server to judge, exactly as the dialog's "All files" escape
  hatch does — and a control **says it takes a drop** (`common.drop_hint` beside the button),
  because an affordance nobody can see is one nobody uses. The highlight is one rule in `app.css`
  keyed on `data-filedrop`, not a hover class re-typed per site.
- **A password reveal (eye) toggle sits on user-password fields only** (#235, owner call): login,
  setup, reset-password and the account page's password fields use the shared
  `core/ui/PasswordInput` — the places where a mistyped password locks someone out. Write-only
  admin secrets (SMTP password/API key, Google & SSO client secret, AI key, Ads developer token)
  stay plain `type="password"` inputs: they are pasted rather than typed, the stored value is
  never displayed anyway, and the toggle just adds chrome to Instellingen.
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
- **Contract, werkweek and vrije tijd are one wizard, not three modals** (Instellingen →
  Gebruikers → ⋯ → **Dienstverband**, and the same item on the team leave roster). They used to be
  three separate ⋯ entries, and that split is exactly why the relationship between contract hours
  and working hours read as arbitrary: they were never three decisions. Contract hours only mean
  something measured against the week that is actually worked, and free days exist *because* the
  two differ. Three steps, one save: **contract** (period + hours) → **werkweek** (the grid, plus
  the one question the system cannot infer: does this person take the hours below the full-time
  norm as free time to schedule, built into their roster, or an agreed figure?) → **vrije tijd**
  (the pattern, prefilled from what the pot buys). Every number is derived on screen as it is
  typed — contract, rooster, vrije tijd on one line — because the previous design let a reduced
  contract grow a pot of free days in silence, and a four-day part-timer grow one twice over. The
  save reports what it did, and when a changed contract orphans already-placed free days it lists
  them and offers to take them back; it never cancels them as a side effect.
- **A work schedule is employment data, so it lives on the person**, not buried in Instellingen →
  Verlof — and on the *contract*, because a schedule change usually is a contract change. It is a
  weekly grid: per weekday a working-day toggle, start/end, and the day's **breaks as a repeater**
  — a morning coffee break next to lunch is an ordinary shape, so a second break is one click, and
  each day carries a copy-to-other-days action. Times go through `TimeInput`, never a native `<input type="time">`.
  Breaks are **not re-sorted while you type**: the API stores them sorted and hands them back that
  way, whereas reordering rows on every committed time yanks the field out from under the cursor.
  The grid renders *outside* its `<form>` and posts through `form="…"` — its `TimeInput`s each emit
  a hidden field, and a form they are not inside is a form they cannot pollute.
  `contracturen` is a **derived, read-only column** that links to the person: hours follow from the
  schedule, they are never typed. Someone still carrying pre-schedule contract hours is flagged
  where they are listed, rather than being silently measured against the org default.
  Org config — verloftypen (wettelijk/bovenwettelijk carry-over rules live here, not in code), het
  standaardrooster, feestdagen, and yearly saldi — lives under Instellingen → Verlof.
- **How a verloftype draws on the Agenda is a tenant setting, not a side effect** (#270). Each type
  chooses *Hele dag* (a full-width chip, the default) or *Per uur* (a block on the hours it covers,
  in the dag- and weekweergave). Roostervrije tijd (ADV) ships as *Per uur*: it is time off inside
  the normal schedule, and drawing it as a full-day bar makes one free afternoon look like a week of
  vakantie. A request spanning several days stays a full-day chip whatever the type says — a single
  block across Monday to Friday would claim the nights too. The maandweergave never draws by hour,
  so the setting is invisible there. Drag-to-reschedule works on *Per uur* blocks as well as
  full-day chips, day-granular either way: a free-time day is drawn per hour and is the one absence
  an employee is entitled to shift, so excluding blocks excluded exactly the thing people move.
  Dropping a block on another day column keeps its window and lets the API re-price; dragging it
  *vertically* to change the window is deliberately not offered — that edit lives in the aanvraag.
- **Vrije tijd has its own card on Verlof, not a balance tile.** Free days are laid down as
  approved leave, so once they are all placed, entitled and approved are equal and a balance tile
  reads "0 u over" — true, and no answer to the only two questions anyone has: when is my next day
  off, and can I move it. The card leads with the next date, lists the upcoming days with
  Verplaatsen / Annuleren per row, and breaks the pot into dit jaar / opgenomen / nog in te
  plannen. Its type is filtered out of the balance grid, because stating the same balance twice —
  once uselessly — is worse than stating it once.
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
  The editor is **WYSIWYG over markdown source** (#255, reversing the earlier
  markdown-with-preview decision once the owner had used it): a Tiptap view, lazy-loaded after
  hydration so ProseMirror never weighs on first paint — SSR/no-JS still render a plain textarea
  with the raw source, and the *stored value never stops being markdown*
  (`lib/core/richtext/editor.ts` parses through `renderMarkdown` and serializes back the house
  conventions, mention markers included). Headings, lists, links and mention chips render styled
  while typing; `### `, `- `, `1. `, `**bold**` convert as you type; Enter continues a list and
  exits on an empty item; links show as blue label text with the URL behind the toolbar's inline
  popover (never `window.prompt`, #228 — with the caret on a link it edits/removes it). There is
  no Write ↔ Preview toggle anymore: the editor is the preview. This is the design-language rule;
  it is not a task feature.
  **Which fields get it, and which stay plain:** the *long-form* ones — a task/checklist/checklist-
  item description, a comment, project/company/contact notes, invoice/quote/subscription notes,
  a custom-field `LONG_TEXT` — get the editor, **including the same field inside a template**
  (a subscription template's notes, a task template item's description, #228). One-liners do
  **not**: a title, a `TimeEntry` description, a leave note. Rich text is for text that has
  structure to gain from it, not for every string. Headings render `###` and deeper only —
  `h1`/`h2` stay stripped everywhere (`core/markdown.ts`): notes and descriptions are supporting
  text, and a uniform rule beats a per-field exception.
  **`@` and `#` work in every editor, not just where a page wired them** (#237). `@` mentions a
  colleague or contact, `#` references a task as a deep link — and both belong to the editor, not
  to the surface it happens to sit on. A `RichTextEditor` given no explicit candidate lists
  fetches the defaults itself on first focus (`core/richtext/candidates.ts`: org members, plus
  the host company's contacts and the host project/company's recent tasks via the `scope` prop —
  a page pays nothing for an editor nobody touches). The `#` dropdown names each task's status,
  assignee and due date — two "Bellen met klant" rows are indistinguishable by title alone — and
  an overdue date reads red like everywhere else. Only the task page passes its own lists (its
  scoped, status-named candidates); a new surface should pass `scope` and nothing more.
  **Rendering is the security boundary.** `{@html}` lives only in `Markdown.svelte`, and everything
  it prints has been through DOMPurify in `core/markdown.ts`; the API also strips raw HTML on write
  (`core/richtext.py`) so a stored value is inert even for a consumer that renders it another way.
  Any consumer that must show the words *without* the markup — a notification excerpt, an email, a
  PDF, a `DataTable` cell — flattens to plain text first (the API's `markdown_to_plaintext`); it
  never truncates raw markdown by character count, which severs a link mid-`()`.
  **A received e-mail renders through the same component, and only because the API converted it**
  (`interactions.body_markdown`, `docs/GOOGLE.md`). The condition is the whole rule: text a
  *sender* wrote is not markdown, so a plain-text mail keeps its plain-text branch — rendering it
  as markdown would turn their `*sterretjes*` into italics and swallow `[iets]`. Two things ride
  that distinction. `Markdown` grows an `images` prop, on **only** here, which draws
  `![alt](file:<uuid>)` — an e-mail's own `cid:` parts, already downloaded, served from our
  storage — and nothing else: a remote `<img>` in a mail is a tracking pixel, the API drops it at
  conversion, and no other surface may fetch a picture at all. And the marker is a **stored
  marker, not a URL**, like `mention:` and `crm://`: the renderer resolves it, so no API path is
  frozen into a body and a consumer that draws no images ignores it.

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
- **Instellingen is a registry, not a page** (`core/settings-nav.ts`). Its thirty-five screens were
  described in four places at once — the index card grid, the permission list that decides whether
  the sidebar item appears, the breadcrumb slug→title map, and each screen's own guard — and the
  copies had drifted: two entries named permissions no screen guards on, eight screens appeared in
  none of them, so an admin holding only `settings.nav.manage` could not reach Instellingen at all.
  One entry per screen now carries its href, its title/subtitle keys, the permission(s) that open
  it, the module that owns it and its posture flags; the grid, the section rail, the crumbs and
  `canAccessSettings` all read that list, so a new settings screen is one entry rather than four
  edits three of which someone will forget.
  Visibility has **three** gates, all of them UX (the route still guards itself, CLAUDE.md §15):
  **permission** — holding any one of the listed keys; **module** — a tenant who switched `leave`
  off has no Verlof to configure, and without this the owner's `*` opened a screen whose API routes
  are not even mounted; **posture** — cloud-only or instance-owner-only, which permission cannot
  express, because `*` satisfies a check for a capability the box does not have.
  The index also **searches** — title, subtitle, and a hidden `settings.search.*` keyword line per
  screen. Past about twenty entries, typing "btw" beats reading five group headings, and the
  keywords are where the words that are *not* on the card go ("wachtwoord" → Mijn account, "smtp" →
  E-mail). And a refusal inside the section lands on `/settings`, never on the dashboard: being
  thrown out of the whole area you were navigating is disorienting, and the index refuses again,
  honestly, if the visitor may open nothing there.
  The **grouping** is part of the same job. "Modules & workflows" had become a fifteen-card junk
  drawer holding the org's dashboard defaults, its outgoing mail transport, its AI provider and a
  cloud support switch side by side, while Google Workspace sat two groups away from the other two
  third-party integrations. Five groups, each answering one question: what this workspace looks
  like (Werkruimte), who may use it (Team & toegang), what shape our data takes (Gegevens &
  keuzelijsten), how each module behaves (Modules & werkprocessen), what it talks to (Communicatie
  & koppelingen). A card is named after what is *on* it — the screen holding only client numbering
  is "Klantnummering", not "Bedrijven", which read as a sibling of Klantgroepen and was neither.
- **The Instellingen rail** (`core/settings/SettingsShell.svelte`) renders from `xl` up, and never
  on the index itself — there the cards *are* the navigation, with subtitles the rail has no room
  for. Below `xl` the content keeps the full column: a 13 rem rail on a laptop costs every settings
  form a fifth of its width to save one click, and the app-wide breadcrumb row is already the way
  back. It lists exactly what the index would show that viewer, marks the current screen, and
  resolves a deep link (`/settings/roles/<id>`) to its section by longest matching href.
  It is a **component, not a route layout**, because of the bullet two above this one: the three
  catalogs that live on their working page (#229) are Instellingen screens at a `/tasks/`,
  `/subscriptions/` or `/domains/` URL, and a layout under `/settings/` can only wrap its own
  subtree. So they mounted no rail at all — clicking Taaksjablonen, Standaardabonnementen or
  Domeinprijzen in the rail dropped you out of the section and took the menu with it, on exactly
  the three screens whose URL gives no hint how to get back. Each now mounts `SettingsShell`
  through its own one-route `+layout.svelte`, and its `+layout.server.ts` resolves the posture flag
  the rail needs via the shared `settingsShellData()`, so every rail lists the same entries. On
  `/tasks/templates` the tasks tab row renders *inside* the shell: the page is a tab of the tasks
  section **and** an Instellingen screen, and both ways in stay true.
  `tests/unit/settings-rail.test.ts` fails if a registry entry outside `/settings/` has no shell —
  nothing else in the build would notice, because the screen renders perfectly well without it.
- The header holds only the profile menu (avatar → name, personal settings, logout).
  Language lives in personal settings, not the header.

## i18n & theming

- Every string through `t()` with keys in `messages/en.json` (source) **and** `nl.json`
  (complete, natural Dutch) in the same change. Dutch is the default UI language.
- **Tenant-entered translations are always optional** (owner policy, 2026-07-17). App strings
  ship complete in both locales; the *tenant's own* labels (leave types, contact types, custom
  fields, e-mail templates, roles, …) never demand both languages — one language is enough and
  a missing locale falls back to the other at render time. Editors use the shared
  `core/ui/I18nTextField` — **one field, never two side-by-side inputs** — which posts every
  locale (`label_nl`/`label_en`) so form actions stay unchanged, and deliberately carries no
  `required` (a required attribute on a hidden locale input blocks the submit invisibly).
- **One language switcher per surface, at the top — never one per field** (owner feedback,
  2026-08-05). *Which* language you are typing in is a fact about the whole screen, not about a
  label, so it is chosen once: `core/ui/I18nLocaleSwitcher` goes at the top of the page, card or
  dialog, and every translatable field under it follows the shared choice in
  `core/i18n-edit.svelte.ts`. The rule is what the old shape argued for at the wrong scale — a
  switcher beside each label is right for one field and absurd for a dozen, and Instellingen →
  Navigatie proved it by drawing one per nav item plus one per group, each flipped by hand to
  write the English column. Consequences worth knowing:
  - **The choice is a module singleton, not a context.** A dialog opened from a page is its own
    component tree, so a provider would have to be threaded into every modal holding a label
    field; and carrying the choice across screens is exactly what someone filling in the English
    column of six settings pages wants. It persists in `localStorage`, is never written on the
    server, and opens on the reader's own UI language.
  - **A page and the dialog it opens may each render one** — they share the state, so they cannot
    disagree, and a dialog that covers its page still carries the control it needs.
  - **The locales come from the catalog, not from a hardcoded pair.** `editLocales()` derives them
    from Paraglide's `locales`, so a third language is still just a JSON file (CLAUDE.md §8); a
    surface whose languages come from *data* (the mail templates, one row per `(kind, locale)`)
    passes its own list and `resolveEditLocale` narrows the shared choice to it.
  - **Fields carry no language chrome of their own** — no tab strip, no "NL" prefix, no `(nl)` in
    the label. The switcher above says which language this is; repeating it per row is the noise
    the rule exists to remove. What a field *may* show is the value it would fall back to, as its
    placeholder.
  - **This applies to hand-rolled per-locale editors too**, not just `I18nTextField`: the invoice
    template editor (field labels + the three text blocks), the mail templates, and the marketing
    dashboard's tile and key-event names all read the same shared locale.
- Branding (logo, colors, brand name incl. hide-name option, favicon) is runtime, per
  tenant, via Huisstijl — never hardcoded. Charts use their own validated, colorblind-safe
  palette (see the dataviz procedure), not the tenant color.
  Huisstijl carries two subjects and says so: **Merk** (name, tab title, logo/favicon/app icon,
  colors) and **Regio & formaat** (language, timezone, country, currency). They were one
  eleven-control grid under a heading that promised only "logo, kleuren en merknaam", so "waar stel
  ik de valuta in?" had no scent to follow. Naming the halves costs nothing; it is still one form
  and one save button, because splitting the save is the mistake this page would make next.

## Known mistakes to not repeat

- Buttons that configure org-wide behaviour placed inside a working screen (the old "save
  as team default" on the dashboard) — config goes to Settings.
- **A form filling itself in from the database and leaving the user to delete the wrong
  parts.** Picking a client on a new invoice used to drop *every* unbilled hour they had onto
  it as lines. It looked helpful and was the opposite: a partly-billable month became a list to
  prune rather than a list to choose from, and nothing on screen said where the lines had come
  from. What replaced it is the shape to copy — the section states what is waiting ("12"), and
  a picker adds only what was ticked. Offer the count, never the contents.
- **A per-field control for a page-level decision.** Every translatable label carried its own
  NL/EN switcher, so Instellingen → Navigatie drew a dozen of them and writing the English column
  meant flipping each one, in order, by hand — and the marketing tile editor answered the same
  question by stacking both languages in every tile. Neither is a field-level choice: ask once, at
  the top of the surface, and let the fields follow (`I18nLocaleSwitcher`, under i18n & theming).
- **A per-row field that can only be filled in one way.** The invoice line editor asked for a
  *unit* on every line, including the hours ones, where the only correct answer is "uur" — and
  for a *type*, which is just the section the line already sits in. Both were dropped: derive
  what the kind determines, and ask only where the answer is genuinely open (a service line
  really is sold in stuks or dagen).
- **A picker that hides what it has already done.** A billed subscription period is listed and
  disabled with "al gefactureerd", not omitted: "did I invoice March?" is the question the
  picker exists to answer, and answering it by omission is what produces the duplicate.
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
- **A bare `use:enhance` on a form that survives its own save.** The default reset rewound the
  roles matrix and the users-page role ticks to their server-rendered marks on every save — the
  UI read as "it didn't save", and the next save posted the rewound marks. The rule lives under
  Interaction patterns: persistent surfaces pass `update({ reset: false })`, one-shot buttons
  and self-unmounting create forms may stay bare.
- **A submit button with no in-flight state.** The contact-portal "Enable" gave zero feedback
  while its request ran (#242): on a slow connection it read as broken, and every extra click
  was another submit. The convention lives under Interaction patterns (Loading / in-flight
  state): `submitting` state + `core/ui/Button` with `loading`.
- **A control that renders without checking `can()`.** Row ⋯ Edit/Delete, New-buttons, and
  quick-action links shipped ungated on half the lists (#253): every role saw them and the API
  403'd on submit. A control that posts a permission-gated action renders inside
  `{#if can(page.data.user, "<module>.<resource>.<action>")}` — matching the API route's
  declared permission, base key only (a scoped `:own` holder must still see their button). The
  ⋯ menu hides entirely when no item survives; `DataTable` gets `actions={... ? rowActions :
  undefined}` so the empty column disappears too.
- **The base key on a control that belongs to a *row*.** "Base key only" is right for the list's
  own controls — Nieuw, the bulk ✎, the section's tabs — and wrong for every control attached to
  one record, because that is the layer where the API refines the scope. `tasks.task.write:own`
  means **assignee** (#12), and it is what the seeded `member` role holds: so
  `can(user, "tasks.task.write")` answered `true` on every row, and the tasks list drew a live
  complete-toggle on all forty of a member's colleagues' tasks, the card offered them ⋯ →
  Bewerken, and every checklist tick posted a 403. So a control the service refines per row asks
  per row — `canWriteTask(page.data.user, task)`
  (`$lib/modules/tasks/permissions.ts`, the browser's mirror of
  `TaskService._ensure_task_writable`), the same shape the calendar's task feed already used for
  `draggable` (`mine ? writeOwn : writeAny`). Two corollaries. A **shared row component**
  self-gates on the row it was handed, so no caller can reintroduce it. And a control over a
  *set* of rows — the project to-do's drag-reorder — needs the write on **every** row in it: a
  list you can reorder halfway is worse than a plain one, because the handles claim the order is
  yours to set. The API is still the boundary; what this fixes is a screen that lied about it.
- **Panels composed for everyone, whatever the viewer may read.** Nav items and dashboard widgets
  have always declared `requiresPermission`; contributed detail-page panels did not, so a contact,
  project or task page rendered every enabled module's panel for every viewer — a member without
  `interactions.interaction.read` got an empty *Contactmomenten* block, with its create control
  beside the heading and a wasted 403 behind it. `EntityPanelSpec.requiresPermission` closes it,
  and `entityPanelsFor(enabled, entityType, user)` takes the viewer as a **required** argument
  rather than an optional one, because an optional one is exactly how the next detail page ships
  the ungated version. The browser-side "which component draws this key" lookup is a separate
  function (`entityPanelComponent`) that needs no viewer — the load already decided. Skipping the
  panel skips its `load`, so this is a round-trip saved as well as a lie removed. Omit the
  declaration only where the endpoint needs no permission, or where the panel deliberately draws
  its own refusal state because that state is worth telling apart from an empty one (`oxxa`
  distinguishes "you may not look" from "there is no register account yet").
- **A write control that leaks to the client portal because it's a *shared* component or a
  "use-mode" affordance.** The portal (a `client`-role login, #193) is not a separate UI: it
  renders the **same** components as staff, and detail pages compose them without a portal filter —
  the company hub renders every module panel as-is, and shared rows like `TaskRow` render on the
  tasks list, the project to-do and the company panel at once. So `#253`'s rule has a second half:
  **every write control on a client-reachable surface must gate itself**, and that includes the
  ones that don't look like writes. A checklist tick, a complete-toggle, a drag-reorder handle, a
  quick-add row, an inline "＋ nieuw" that opens a create form — these read as "using", but each
  posts a write the `client` role does not hold, and living *outside edit mode* is not a gate
  (#244). Gate them on the API's own permission (`can(page.data.user, "tasks.task.write")`,
  `"files.file.write"`, `"contacts.contact.write"`, …), base key, exactly like any other control —
  not on `!isPortal` (which mirrors the API less precisely and still leaks to a non-writer staff
  member). A component reused by several hosts **self-gates internally** (`import { page }` +
  `can`, the way `DomainsPanel` and now `TaskRow` do) so a new caller can never reintroduce the
  leak by forgetting a prop. The whole `client` write surface is: **its own task comments, its own
  dashboard/nav layout, and its own notification inbox** — nothing else is writable, so treat any
  other write affordance reachable by a portal login as a bug. The API side is fenced by
  `tests/test_rbac_deny_by_default.py::test_client_role_is_read_only_except_own_comments` (it walks
  the live route table, so a new write route ships covered); the UI side has no automated guard, so
  audit every write control by hand when you add a client-reachable panel, list or shared row.
- **An index that renders every card and lets the routes sort it out.** Instellingen showed all
  thirty-odd screens to anyone who could open *one* of them, so an agency that granted
  `settings.branding.write` and nothing else handed that person a wall of cards, twenty-nine of
  which 303'd them back to the dashboard on click. It is #253's rule ("a control that renders
  without checking `can()`") applied to a whole grid rather than a button, and the fix is the same
  one: render from what the viewer may actually open. A landing page for a section is not exempt
  from the rule because it only contains links — a link that always refuses is a broken control.
- **A screen with no guard at all, because "the API enforces it".** Instellingen → E-mail and →
  Single sign-on had no `can()` in their loads, so any member could open the outgoing-mail
  transport and the identity-provider form: three guaranteed 403s and an empty admin form that
  could never save. The API being the boundary is why this was not a data *leak*; it is still a
  screen that lies about what the visitor may do, and it is why every settings route declares its
  permission (#19) rather than trusting the call it makes.
- **A whole *screen* that leaks, not just a control.** The pass above gated the controls inside
  client-reachable pages and missed the page that *is* a write surface: `/tasks/templates` — the
  org-wide task-automation and checklist repositories — hung off the tasks sub-nav and read behind
  `tasks.task.read`, which a `client` holds. So a portal login sat one tab from their task list,
  looking at the agency's internal process library, with a "nieuw sjabloon" form the API refuses.
  Two lessons. When **every** control on a screen writes, the gate belongs on the screen and on the
  link to it — the sub-tab, the Instellingen card, and a `redirect(303, …)` in the route load — not
  on each button: rendering a management page read-only either lies (an empty list that is really a
  403) or leaks. And **gate the read, not only the write**: a permission-gated form is still a leak
  while its `GET` sits on a key the client holds. Those two lists now read as "you may edit a task"
  (`tasks.task.write`) and "you may apply a template" (`tasks.template.apply`), so a portal login
  cannot enumerate them at all, and the load skips the fetch it would 403 on.
- **One screen for two audiences, gated on `!isPortal` instead of on the key.** Facturen (#266)
  is the case the rule above does not cover: it is *not* a write surface, so it should not be
  gated whole — a client belongs on it, reading their own invoices. What differs is the **chrome**
  around the list: the Concepten tile and its filter chip, the client picker, "Nieuwe factuur",
  the ⋯ Bewerken, the *Van abonnement* provenance chip, the activity trail, and the sibling
  *Nog te factureren* in the sidebar. Each of those is a control whose API answer is
  `invoicing.invoice.read:any`, so each renders behind `can(user, "invoicing.invoice.read", "any")`
  — the same key **and scope** the route declares. `!isPortal` would have been shorter and wrong
  twice over: it still shows the agency's draft count to a restricted staff member, and it stops
  reading like a permission the admin can see in Instellingen → Rollen. A scoped key is the tool
  for "the same screen, two depths"; `NavItem.requiresScope` exists so the sidebar can express it
  too, because a nav link that always 403s is a broken control (#253).
  Two things travel with it. The heading and the empty state say *Mijn facturen* / "U heeft nog
  geen facturen" rather than the agency's own *Facturatie* — a screen names itself for whoever
  opened it. And the load **skips what only the editor consumes**: the contacts, tax rates,
  products, templates, settings and custom-field lookups exist for `DocumentForm` alone, so they
  now hang off `canWrite`. That was five wasted round-trips for every read-only viewer before it
  was a leak, and the client's invoice page went from eight API calls to two.
  The **pay control** (epic #269) is the same rule at the other end of the scale: it is a write
  a client legitimately holds, so it gates on `can(user, "invoicing.payment.link")` — base key,
  because a client holds `:own` — and never on `isPortal`, which would have drawn it for a
  restricted staff member who cannot start one and hidden it from the person it exists for.
  Whether it can be *spent* is a second question, and `InvoiceRead.online_payment` answers it
  without letting the client read which provider accounts the agency has connected: a padlock
  the viewer can do nothing about is worse than no button, and the account list itself sits at
  `:any` (`docs/PAYMENTS.md` §8).
- **A refusal that hides which of the two gates fired.** Permissions say *may they*, the company
  horizon says *which rows exist for them* (CLAUDE.md §15), and out-of-horizon deliberately answers
  `404 errors.not_found` so a get-by-id can't leak existence. Correct — but a `client`-role login
  scoped to no company at all gets that same "niet gevonden" on *everything*, and the admin's only
  lever, granting permissions, can never fix it. #274 reached us as "we granted the right
  permission and it still says not found". Two rules came out of it. Where the refusal is about
  the **caller's own account** rather than a specific row, say so — `errors.no_company_scope` names
  the missing link and leaks nothing, because it describes their login, not our data. And put the
  same fact where the admin is already looking: Instellingen → Gebruikers badges such an account
  "Ziet geen klanten" next to the existing "Beperkte zichtbaarheid" chip. A state that makes every
  screen fail identically needs one place that explains it.
- Taking `.date()` of a UTC instant to name a local day. Amsterdam's midnight is 22:00 UTC the day
  *before* in summer, so a monthly budget reported its period as starting 30 June. Half the year the
  bug is invisible, which is why it is pinned on a fixed date rather than on `today`.
- A totals row summed from `rows`. The page holds one slice of a longer set, so it prints the
  total *of the page* — which looks exactly like the right answer. Totals come from the API.
- **A list that shows the first N and calls it the list.** Every index asked for 200 rows at
  `offset: 0` and stopped, so a tenant who outgrew the cap got a prefix indistinguishable from
  the whole answer, and row 201 was reachable only by guessing a narrow enough search. Two
  screens had grown their own prev/next by hand; twelve had nothing. They all share one pager
  now (`core/ui/Pagination.svelte`), and the reason it is shared is that the interesting parts —
  the URL carrying the page so the back button works, every filter resetting it, the size saved
  per user — are exactly the parts a hand-rolled copy leaves out.
- **A filter applied in the browser.** The clients list narrowed `data.companies` by status in
  the page. That was survivable only while the page *was* the list: against a paged list it
  filters the fifty rows you happen to hold and reports a total counted over all of them. The
  API already took `status` — the export was sending it. If an API cannot filter it, that is a
  missing query parameter, not a licence to filter the slice.
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
- **A shell with no content measure.** `<main>` was `flex-1 p-6` and nothing else, so every screen
  was as wide as the monitor. On a 3178 px display a Instellingen card became 1430 px of box around
  one sentence, the clients list put a row's name and its status a metre apart *while truncating
  both*, and a dashboard tile stranded "Bakkerij Jansen" and its number at opposite ends of the
  screen. Past a point wider is not more readable, it is further to look. One `max-w-content`
  (`--container-content`, app.css) now wraps the page and, separately, the header's controls — the
  bar keeps its full-bleed background and rule, but what sits *in* it lines up with the page below,
  or the avatar drifts away from the content's right edge. The number is chosen against the densest
  screen (a client list with every optional column on), not against prose, and it binds only above a
  1888 px window, so laptops are untouched. A screen that genuinely needs the whole width opts out
  by not using the class — never by raising the number for everyone.
- **An inline-SVG chart with a constant `viewBox` and `class="w-full"`.** That pair does not size a
  chart, it fixes its *aspect ratio*, and the browser then scales every user unit inside it —
  gridlines, strokes and, fatally, type. The marketing trend chart was drawn 720×200; on a 3178 px
  screen it rendered 3130×869 with 59 px axis labels, a single chart taller than the fold, and on a
  390 px phone the same labels came out at 6 px. One bug at both ends, invisible on the laptop it
  was built on and invisible to every test, because the SVG was valid and only its size was absurd.
  Measure the container (`bind:clientWidth`) and draw at **1 user unit = 1 CSS px**, so 10 px type
  is 10 px everywhere (`$lib/core/ui/charts/geometry.ts`, pinned by
  `tests/unit/chart-geometry.test.ts`). Two corollaries. **A height must not be derived from the
  width**: growing one with the container reads as the obvious fix and is a scrollbar oscillation
  waiting to happen — taller chart, taller page, scrollbar appears, container narrows, chart
  shortens, scrollbar goes, forever, on whichever screen sits at the knife-edge. Give a chart a
  taller *design* height instead. And **type has an absolute legible size; a bar does not** —
  freezing bar widths too would leave twelve 14 px threads spaced 250 px apart, so a mark read
  against its neighbours stays a proportion of its slot. A chart with a fixed pixel box
  (`Sparkline`, `DonutChart`) never had any of this and needs no measuring.
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

- **Two lists of one thing, split by a distinction the reader cannot see.** Instellingen →
  Meldingen showed "Mijn kanalen" *and* "Externe kanalen" stacked on one screen, and the honest
  answer to "why two?" was an implementation detail: one kind was routed per event from the matrix
  above, the other by an event-filter checkbox list and a single channel-wide cadence of its own.
  The reader saw two boxes of the same nouns (Slack, Teams, webhook) and no way to tell which they
  wanted. Worse, the second mechanism was a **capability gap wearing a UI**: a shared room could
  not group per event the way e-mail could, because it had one cadence for everything (#295).
  Two rules. **When a screen needs a heading to explain why something is duplicated, unify the
  mechanism instead of labelling the halves** — here every channel became one column of the matrix,
  and the section is just "Kanalen". And **when two variants of one concept differ by scope, the
  page is the scope**: my transports live on my settings screen, the org's shared rooms on the org
  defaults screen, each under the matrix that routes it, so neither page ever shows a list it
  cannot act on. Suspect any screen rendering the same component twice with a boolean prop.

- **Assigning `select.value` imperatively, on a select Svelte renders.** #314 gave the task card's
  status select a second job: pick a finished status and it opens the finish prompt instead of
  submitting, so the pick is put back until the confirm commits it. Putting it back with
  `select.value = task.status` marks the control **dirty**, and the browser then ignores the
  `selected` attribute Svelte rewrites on the next render — so confirming really did finish the
  task, the budget bar and the activity trail updated, and the sidebar went on reading *Open*
  until a hard reload. It looked exactly like a failed save. Bind the value
  (`bind:value={statusValue}`, re-armed from the record in an `$effect`) and write to the state,
  never to the DOM node; the same applies to any control a handler needs to rewind — a cancelled
  picker, an optimistic toggle that has to go back.
