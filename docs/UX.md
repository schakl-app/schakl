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
   **Entering a mode is a menu item; leaving it is a button** (#337). The toggle changes *shape*
   with the mode, not only its label: use mode is ⋯ → Bewerken, edit mode is a visible **Klaar**
   (or **Annuleren**, where the surface posts) standing where the ⋯ stood. A ⋯ whose only item is
   "Klaar" is the tell — a button wearing a menu's coat, and two clicks plus a menu for the one
   act the user still wants. Both shapes live in `EditToggle` (`$lib/core/ui/`), which keeps
   drawing the menu for whatever *else* the screen put in it.
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
- **A date and an instant travel down the same `string`, so the value decides, not the caller.**
  The API sends a wall-clock date (`2026-07-07` — a due date, an expiry, a contract start) and an
  instant (`2026-07-07T09:12:33Z` — when something was checked, uploaded, decided), and the two are
  read in different zones: a date in UTC or it slips a day, an instant in the *tenant's* zone
  (CLAUDE.md §8) or two colleagues read different days for the same event. Every formatter in
  `core/format.ts` used to assume the first shape and pin `timeZone: "UTC"`, parsing by
  concatenating a midnight onto the string — so an instant became `…T09:12:33ZT00:00:00Z`, an
  Invalid Date, and printed as **`NaN-NaN-0NaN`** (the year is `String(NaN).padStart(4, "0")`).
  It shipped on five screens at once — the three Google Ads panels' *"gecontroleerd"*, the cloud
  console's trial and lifecycle dates, the domain health card's certificate expiry, the HR document
  list — because there is nothing for the build to catch: both shapes are `string`, so the types
  agree and `svelte-check` passes, and the garbage only appears on a row that actually carries a
  timestamp. The discrimination now lives in one dependency-free place (`core/wire-date.ts`, pinned
  by `tests/unit/wire-date.test.ts`) and reads the shape off the value — an instant carries a `T` —
  so any date formatter may be handed either. Choosing to *show* the time is still a separate
  decision: an "as of" line that a button refreshes (a verification, a health probe, a sync) uses
  `fmtDateTime`, because a bare date on something you just pressed already said today.
- **A duration is typed, not counted** (#326). Every field whose subject is a span of time —
  a task's budget, a scheduled block, worked hours, a break — takes free text through the shared
  `core/ui/DurationInput` and the one parser behind it (`core/duration.ts`): `1:40`, `100`,
  `100m`, `1h40` and `1,5` all land on the same minutes, and the field canonicalises to `1:40`
  afterwards. **Never a stepped `<input type="number">` in minutes.** That control asked an agency
  to do the arithmetic (an hour and a half is `90`) while the read directly above it said
  `1h 30m`, and its `step="15"` was a rule nobody ever decided: Chrome blocked the submit with
  *"the two nearest valid values are 90 and 105"* on a number the API accepts without complaint.
  A client-side rule stricter than the server's is a control refusing valid input, so this one
  states **no** range of its own — the API is the authority, and a second copy of that rule here
  is one that drifts. What travels is the text, not a hidden number, and the server action runs
  the same parser (`parsePostedMinutes`), so a post with JS off lands on the same value. Text it
  cannot read is refused visibly, through the browser's own validity machinery; it never guesses
  a number nobody typed.
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
  an employee is *invited*, not created — so those carry no ＋. They are still comboboxes: "no
  inline create" is a statement about the ＋, never a licence to keep a native `<select>`.
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
  **A picker's opening list is a suggestion, so a record whose life is over is not on it —
  and is still findable, and still says what it is.** Every client picker in the app was
  `companies.map(c => ({value: c.id, label: c.name}))`, twenty-odd copies of a mapping that
  could not tell a client the agency stopped working for from one it works for today; the
  project pickers were the same one line. Hiding those rows outright is the other mistake, and
  the worse half on its own: people do book a forgotten hour on a project they closed last week,
  and a picker that cannot name it sends them to another screen. So `Combobox`'s `archived`
  bucket takes them — out of the opening list, found by typing, never ranked above a live row —
  and one helper per module decides which rows those are (`$lib/modules/companies/picker`,
  `$lib/modules/projects/picker`, over `$lib/core/picker`). Three rules travel with it. **A
  status is said out loud rather than implied by its bucket**, so a paused project reads "On
  hold" while it is still on offer and an archived client reads "Gearchiveerd" under the
  search. **Whatever is already picked is always offered**, or the field cannot say what is in
  it and an archived value renders as an empty box. And **core holds none of the vocabulary**:
  a shared picker (`PartyPicker`, `FilterBar`'s select) *takes* the module's lifecycle, exactly
  as `Combobox` takes `archivedLabel` instead of holding a word. The retired sets differ on
  purpose — a client is only retired by the archive, because a lead is being chased and an
  offboarding client is still being invoiced, while a project is retired by `completed` as well,
  because delivered work is not something to suggest booking against.
  **And where the rows are *people*, the picker is one component** (`core/ui/MemberPicker`). The
  rule above had been written down once and then re-applied by hand at every call site — each
  spelling out `splitMemberOptions`, `archived`, `archivedLabel` — while the three controls that
  predated the helper were still native `<select>`s: the interacties owner filter, the automation
  rule's assignee and the takensjabloon's. A `<select>` has no search to hide anything behind, so
  those three degraded to "last, under an `<optgroup>`", which puts a colleague who left in March
  one keystroke from being picked; and an eleventh, the beschikbaarheid form's *whose week is
  this*, had no split at all because it was written from `memberLabel` rather than from the
  helper. That is the shape a shared rule takes when it lives in a function instead of in a
  control: it is right wherever somebody remembered it. `MemberPicker` takes the roster and
  answers with the whole rule — deactivated accounts out of the opening list, findable by typing,
  wearing "Gedeactiveerd", always offered while the field holds them — plus the two knobs the
  call sites actually differ on. `extra` leads the list with the choices that are **not a
  person** ("Mijn contactmomenten" / "Iedereen" on a filter, "Verantwoordelijke van de klant" on a
  template, which resolves at apply time), and `exclude` drops the ids another control already
  names — the owner filter excludes the signed-in user, because "mijn" is that same answer in
  words. Reach for `splitMemberOptions` directly only where the control is not a single-value
  picker: the assignee chips, the party picker.
- **Quick-add where the user is**: contacts on the client page, projects/clients from the
  time entry form, checklist items on the card. The full forms still exist on their own
  pages; quick-add is an accelerator, not a replacement.
- **People attached to a record are "one primary, N others"** — the same chips-plus-type-ahead
  shape everywhere. **The primary is marked by a ★ *and* the brand colour**, plus an `sr-only`
  label. This **reverses** the original rule ("the colour and nothing else: no star, no emoji, no
  glyph of any kind"), and the reversal is the interesting part. A coloured chip among grey ones
  does say which one is primary — but only when there *are* grey ones, and a client with one
  contact person has a lone gold pill with nothing to contrast against; on a gold-branded tenant
  that pill is also indistinguishable from an amber warning chip, so the person to ring first read
  as a problem. Colour was carrying two meanings and neither of them reached a screen reader.
  Both *pill* surfaces obey it (`LinkField`, `AssigneePicker`): they sit on the same screens, and a
  marker that means "primary" on one card and nothing on the next teaches the reader that it means
  nothing. `Assignees` — the read-only avatar row — is not a pill and keeps its full-vs-muted
  contrast; a glyph beside a face fixes nothing there.
  **The glyph says *that* a chip is special, never *what* or *which direction*** (#374). That is
  words' work, and on a direction-ambiguous surface the words must say the direction:
  `company_contacts.is_primary` means "the primary contact **for that company**", so the
  clients-on-a-contact block reads *"hoofdcontact bij deze klant"* and never a bare *"primair"* —
  which invites a reading that does not exist (*this person's main client*) and turns the promote
  click into an unannounced write to a different client's configuration. So every chip carries a
  `title` naming itself, and edit mode states the promote gesture in one line of text: a gesture
  discoverable only by hovering the thing you did not know to hover is not discoverable.
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
- **A layout the user arranges is stored, never recomputed** (#325). The My Day board's two
  columns were cut out of one flat list at `ceil(n/2)` on every render, so the column a tile was in
  was a function of its index and nothing else. Three complaints, one cause: dropping a tile at the
  top of the *other* column rebuilt the identical list and snapped back; dropping it at the bottom
  moved it and teleported whatever sat on the boundary the other way; and adding a widget from the
  gallery re-cut a board nobody had dragged. The columns are `dashboard_prefs.columns` now, with
  `widgets` kept as the flat reading order a phone renders and NULL still meaning "the halfway
  split", so a layout saved before this keeps looking like itself until its owner moves something.
  The tell to reach for: a **derived** value the user can drag.
  Two further rules came off the same screen. **A hidden form is still a form**: this one carried a
  bare `use:enhance`, whose success path is `update()` — i.e. `invalidateAll` — so every drop re-ran
  the page load, refetched every widget's API calls and blinked all thirteen tiles back to their
  skeletons to persist an order the browser already had on screen, *twice*, because
  `svelte-dnd-action` dispatches `finalize` on both zones of a cross-zone drop. `pnpm forms:check`
  governs only forms a user types into, so nothing was ever going to catch this one: a save that
  teaches the client nothing invalidates nothing, and the streamed per-tile promises are kept **by
  identity**, so even the one reload that is genuinely needed — a widget added from the gallery has
  no data — puts a skeleton on that tile alone.
  And **hit-test a drag on the cursor, not on the dragged element's centre**
  (`useCursorForDetection`): the default agrees with the pointer only while the dragged thing is
  small relative to its target, and a 130 px widget over an emptied column's 96 px `min-h-24` put
  the cursor inside the empty stack with the tile's centre below it — the column you could empty
  was the column you could not refill.
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
- **A conversation is a feed, so it folds, it is counted, and its order is the reader's**
  (`$lib/modules/tasks/TaskComments.svelte`). The rules above were written for the three-comment
  task and were all still true at seventy: one flat column, oldest-first, no count, and nothing
  said about the API's 200-row cap. Five things generalise to any threaded discussion the product
  grows. **Order is a preference and it applies to threads only** — an answer must follow its
  question, so replies stay oldest-first and what the control flips is the order of the openers.
  The default is **newest-first**, because a chat pins its viewport to the bottom and a section on
  a record page does not: inheriting the chat convention is what put the news at the bottom of the
  page. It is a per-user pref (`/api/v1/prefs`, namespace `comments`), not a URL parameter — this
  is how one person reads, not which records are on screen — applied optimistically and saved in
  the background, because a reorder that waits for a round trip reads as a control that is broken.
  **The list folds from the far end and the fold counts what it hides**: the newest few threads
  stay open, "Toon 23 oudere reacties" is one line above or below them depending on the order, and
  a thread's own earlier answers fold the same way. A list that simply stops looks exactly like a
  list that is complete (Principle 7), which is also why the cap now says so (`comments_truncated`,
  answered by reading one row more than is kept). **The count is on the heading**, because "how
  much is there to read?" is the first question a discussion is asked and a folded list cannot
  answer it by being looked at. **A deep link expands before it scrolls**: `?comment=<id>` is what
  the notification inbox, the mail button and the activity trail all point at, and the section
  unfolds whatever hides that message, marks it, scrolls it to the middle of the viewport and
  opens the reply composer under it seeded with an `@mention` of its author — "someone answered
  you" and "you are about to answer" are one motion and it used to be three clicks. Arriving is a
  *navigation*, so the reveal is repeated over the second after it lands: SvelteKit's
  post-navigation `reset_focus()` and the editor's async mount each hand focus back to `<body>`
  after we take it, and one attempt loses to whichever runs last. A `?comment=` the page does not
  hold (deleted, or past the cap) says so in a strip — swallowing it is what a broken link looks
  like. And **posting marks what you wrote**, for the same reason: reading oldest-first your own
  comment lands at the far end of a long list while the composer stayed at the top.
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
- **A destructive action states its consequences in the dialog, and the reversible neighbour it
  should probably have been sits above it** (`ConfirmDialog`'s `consequences`). One sentence is
  enough for "delete this row?" and stops being enough the moment an action has effects the
  record it names does not show. Instellingen → Gebruikers offered exactly one way to off-board
  somebody — "Toegang intrekken", asking *"Toegang van dit lid intrekken?"* — which deletes the
  membership: accurate, complete as a question, and silent about the two things that actually
  happen. Roles and klantgroep assignments go with it (`ON DELETE CASCADE`), and every screen
  that names a person resolves the name *through* a membership, so a departing colleague's
  thousand logged hours, their tasks and their contactmomenten all went nameless the moment it
  was pressed. Nothing in the database was lost and nothing on any screen said so. Three rules
  come out of it. **The list is what makes a choice between two actions informed**: Deactiveren
  says what is kept (the name everywhere, the roles, the contract, the rooster, the tarief) and
  that Activeren undoes it; Intrekken says what is lost and *names Deactiveren as the thing you
  probably want*. **The gentler action goes above the destructive one and is not red** — it
  destroys nothing, so it takes `variant="primary"`, and it still confirms, because ending a
  colleague's access deserves a pause even when it is reversible. And **an entity with a
  lifecycle needs the lifecycle before it needs the delete**: the read half of "deactivated
  colleague" had been built for a year (the picker split, the roster badge, the login refusal)
  against a column no screen could write, so the only lever an admin had was the destructive one.
  A status a screen can *display* and cannot *set* is a missing control, not a finished feature.
- **A member is edited in a modal, because a member has no detail page.** The #78 rule below —
  row ⋯ → Bewerken is a link carrying `?edit=1` to the record's own page — needs a page to link
  to. Instellingen → Gebruikers is the record surface for a colleague, so its Bewerken opens a
  `Modal` over the roster (name and status; the e-mail address is shown read-only, because it is
  the account's identity across the whole instance and the key an OIDC login matches on, so
  editing it here can silently detach somebody's Google sign-in). Two states are drawn
  differently on purpose: an account this org deactivated offers Activeren, and one disabled
  *outside* this workspace (`is_active` false with no `deactivated_at` — the instance's own axis,
  §5) says so instead, rather than drawing a control that would refuse (#253). The one residual
  caveat, stated where it happens: reactivating a staff account also lifts the instance flag, so
  on a multi-org instance that re-enables their login in their other org too — which is why it
  takes an explicit press and never rides along with a rename.
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
    itself: the bar states the honest range ("51–100 van 812") and offers
    **25 / 50 / 100 / 200** per page. Four things it must keep being:
    - **Always there** (#334). The bar used to hide itself whole below one page, on the grounds
      that a pager over nine rows is decoration — true of the arrows, false of everything beside
      them. "12 van 12" is the answer to "heeft mijn filter iets gedaan", and a short list is
      the only place the reader cannot count for themselves; the size selector was unreachable
      on exactly the lists a 50-row default is worst for. So the frame, the count and
      **Per pagina** render at twelve rows and at zero (where the range becomes "Geen
      resultaten" rather than "0–0 van 0"), and only the arrows and numbered chips stand down
      when there is nowhere to step. Seven screens printed their own "Totaal: 12" under the
      heading to work around this, in two different wordings, saying it twice on a long list —
      they are gone, and a heading count is not the answer to wanting one back.
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
  - **A tick lives exactly as long as its row is on screen** (#330). `DataTable` used to empty the
    whole selection whenever `rows` changed *identity*, which is every load of the same route and
    not every change of the row set — so the ticks were thrown away by things that are not a
    different set at all. Switching the page size from 25 to 100 lost twelve of them while all
    twelve rows stayed on screen; so did a `reloadOn` column toggle and any `invalidateAll()`
    raised by something else on the page, and since the reset wrote through a `$bindable` the
    page's own selection was emptied without the page ever hearing about it. Worse, the one
    gesture that would let a bulk action reach past a screenful — raising the page size — was
    exactly the gesture that threw the selection away. **Intersecting** with the rows on screen
    says the same thing honestly and needs no bookkeeping about *why* they changed: a filter drops
    what vanished, page 2 drops page 1's, 25 → 100 drops nothing, and a bulk delete that landed
    drops precisely the rows it removed. The scope sentence beside the count ("De selectie geldt
    alleen voor deze pagina") is then true rather than approximately true — and it stays per page
    deliberately, because the API's `MAX_BULK_IDS` is 200 *because* that is the largest page the
    pager offers: a selection spanning pages would buy nothing `?size=200` does not, and 422 past
    it. The ✕ beside the count is the way to drop a selection without leaving the mode.
  - **A control that describes a selection stays on screen while the selection is being made**
    (#331). On a 100- or 200-row page you scroll down to pick rows, and everything that tells you
    what you are doing — the count, the scope, each action's `eligible` suffix — was above the
    fold at the moment it was being decided; you ticked, scrolled back up, and found out there
    whether you had ticked eleven or twelve. The strip is `sticky` while the mode is on, and three
    details are the whole of it: what sticks is an **opaque wrapper**, because the bar's own
    `bg-brand/5` would smear the rows scrolling through it; it sits at `z-20`, over the table and
    under `ActionsMenu`'s `fixed` panel; and below `sm` the actions **scroll sideways rather than
    wrap**, because a wrapping strip stuck to the top of a phone eats a third of the screen. A
    list nobody is editing is unaffected — no bar, and the first row is exactly where it was.
  - **Two controls that look alike must not have different scopes** (#332). With twelve rows
    ticked, the strip's Verwijderen meant twelve and every row's ⋯ Verwijderen still meant one,
    with nothing on screen to tell them apart and the confirm naming a single record only *after*
    the click. Both mistakes were available, including the expensive one: ticking twelve, opening
    the ⋯ on one of them, and deleting a single client while believing you deleted twelve. So the
    record menu is **withdrawn while the rows are being picked** — there is one control at a time,
    and it is the one that describes what you picked. "Being picked" is the mode where a list has
    one and a live tick where it does not, so the permanently selectable lists (the uren report,
    the two leave rosters) keep their menus right up to the moment they would become ambiguous.
    The trailing cell is held open rather than dropped, so no column reflows; and because the
    phone row is the *page's* own snippet, `DataTable` hides its ⋯ with one rule against
    `ActionsMenu`'s `data-actions-menu` marker rather than nine `{#if}`s that would drift apart.
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
  - **A screen that holds a queue opens on the queue, and the queue carries its size.**
    Interacties opened on the whole timeline with the unreviewed e-mails scattered through it
    wearing an amber pill, and its two views were a pair of borderless words whose *selected*
    half painted itself `bg-surface` — the page's own colour (`app.css`). So the primary switch
    on the busiest screen in the app was invisible, and the one job the screen exists for was
    something you scrolled for. Four rules came out of fixing it and none is about interactions.
    **A pressed control must not be painted the colour it is standing on**: `bg-surface` marks
    nothing on a `--surface` page; a chosen tab is raised and ringed, or carries the brand, the
    way a card sits above the page it is on. **The count is the control**: "Te beoordelen 11"
    says there is work, says how much, and — because it is read in the *layout* load, never the
    page's — keeps saying it while the list below is searched, dated and paged, which is what
    lets a reader tell an empty queue from a filtered one. **A default that hides rows owes the
    hidden state a URL** (`?status=all`) and owes the *endpoint* nothing: the API's own filter
    stays unset, because the pickers, the panels and the generated MCP surface read the same
    route and must still be told everything (CLAUDE.md §9, #329). And **a view that cannot be
    narrowed does not draw the narrowing control**: the queue is one person's mailbox by
    construction, so the owner select would have only one non-empty answer, and every pending row
    is an e-mail, so the kind select would have one too — both stand down there, and both come
    back the moment a link arrives with the parameter set, or the list is narrowed by something
    the screen does not show. The empty states then split three ways, because they are three
    different facts: filtered → `common.no_results`; queue empty → *say it is done* and put a
    real button to the full list in the middle of it; nothing at all → the old line. The one an
    inbox-zero screen must never show is "Geen interacties in deze weergave", which on the view a
    user now lands on first reads as a page that failed to load.
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
  A fourth thing has to hold that the other three quietly assume: **the destination must be able
  to take the filter** (#323). Contactmomenten had the cap and the honest count — *"De 8 meest
  recente van 137 worden getoond"* — and nowhere to go, because `/interactions` never read the
  four record filters the API had taken since #147. The sentence that exists to admit a
  truncation was itself the end of the road. Three things fix it and generalise. The notice
  **is** the link (a navigation is an `<a href>`, never a click handler), so it previews, opens
  in a tab and survives a middle click like every other "see the rest" control. The list then
  **says what it is narrowed to** — the record's name, linking back to it, with an ✕ that widens
  — because a filtered list presenting as everything is the same lie one screen along; a name
  the reader may not resolve still gets its chip, since the filter is on either way. And a
  **default that answers the unfiltered page may not survive the scoped one**: `/interactions`
  lands you on your own moments (#263), which over a team-visible panel's link would have
  answered 12 under a notice that said 137.
- **A panel is how a number opens.** A module hangs a panel off another module's detail page by
  registering an `EntityPanelSpec` (`core/registry.ts`), never by having the host page import it —
  a tenant with the module disabled then simply never renders it, and pays for no call. The panel
  loads through the typed client inside the host's `Promise.all`, and the host hands down the
  lookups it already fetched (`EntityPanelLookups`) rather than letting the panel refetch 200 rows
  the page is holding. A panel that edits its records posts to the **host page's** form actions,
  because that is where SvelteKit actions live.
- **A row has to identify the record, not merely describe it** (#400). The client's Uren panel
  showed a description and a duration, so *"Back-up teruggezet op de testomgeving"* appeared three
  times on one client — three days, three colleagues, three indistinguishable lines — on the
  screen somebody reads while that client is on the phone. Three rules generalise past it. **What
  is already over the wire and undrawn is the cheapest fix available**: `started_at` was in the
  payload *and declared in the component's own interface*, so the whole "when" half cost nothing
  to fetch — and once there is a date to group by, ten rows across six days read as six days of
  work rather than as a list. **"Who" is a `PersonChip`, everywhere** — a name beside a face,
  resolved from the lookup the host page already holds, never a bare user id and never a bare
  initials disc. And **the fourth fact rides as a marker rather than a column**: whether we bill
  for an hour is worth a glyph, not a heading, and the glyph carries the state as well as the
  colour, because a tenant's brand may be green.
- **A dialog that shows six fields must write six fields** (#400). A record's panel corrects a row
  in place, and the full form for that row is usually a *scope* form — a client, a project, a task,
  each a type-ahead over a lookup the host would have to load on every page open for a dialog most
  opens never reach. So the panel draws the correction (`EntryQuickEdit`) instead, and the safety
  property is entirely in how the host's action reads it: `form.has()`, `undefined` keys vanishing
  in `JSON.stringify`, and `exclude_unset` at the API — CLAUDE.md §18's *absent means leave alone*,
  applied to a form that deliberately shows less than the record holds. Reading the missing fields
  with `?? null` instead is the same defect as a permission-hidden block wiped by a restricted
  caller's ordinary save, and it is invisible in review: the diff reads as thorough.
- **A period an aggregate counts from is the API's, not the browser's.** `budget_period` resolves
  to a *local* Amsterdam day (`projects/budget.py::period_start_date`), and the entries behind a
  budget bar are filtered by exactly that day. A page that recomputed it in UTC landed on the
  previous day for half the year, quietly dragging last month's evening into this month's total.
- **Budget burn has exactly one scale**, in `core/burn.ts` — green < 75 %, amber < 100 %, red ≥ 100 %.
  The percentage is **unclamped** so an over-budget project reports a negative remainder and reads
  red; only the drawn bar's width clamps, because a bar cannot be 130 % long. A record with no
  budget shows an em-dash and still reports what it spent — never a fabricated total, and never a
  reassuring zero.
- **And exactly one block that draws it**: `core/ui/BudgetBar.svelte` (#313), `variant="block"` for
  a card, `variant="inline"` for a table cell. It exists because the scale being documented in one
  module did not stop a fourth surface from hand-rolling it: the task card had its own
  `bg-green-500`/amber/red ladder at 75/100 and its own `Math.min(100, pct)`, written before
  `burn.ts` and never reconciled with it. The component is **unit-agnostic on purpose** — the
  caller passes the two raw numbers (which decide the colour) and the formatted strings (which say
  it in that module's unit and words), because a task budgets minutes and a project budgets hours.
  Reach for it before writing a bar.
- **A bare `x / y` is spent-of-budget, and a remainder always carries its own word** (#340,
  `core/hours.ts`). `0 / 5 u` and `5 / 5 u` are the same nine glyphs: My Day printed the spend,
  the companies and projects lists printed what was left of the same budget, and both drew the
  identical bar underneath — so on the list an empty bar sat beside the figure `5`, which reads
  as "5 used". One screen apart, the same project. The meaning was chosen because two things
  already agreed on it: the bar has always drawn the spend, and `/time` already printed
  `0 / 5 u deze periode`. What is left is the more useful sentence on a client list, so it did
  not disappear — it moved to the hover, in words (`5 u over`), where it cannot be mistaken for
  the other number. `HoursCell`'s tooltip used to *lead* with `{spent} van {budget} u` while the
  cell beside it showed the remainder, so a single element disagreed with itself.
  Three rules generalise past this bug. **The words live with the numbers**: `core/hours.ts` is
  `modules/tasks/budget.ts` for the unit a project budgets in, and a shared *component* only
  fixes half of it while five callers still write the sentence themselves. **A column header is
  part of the figure** — the cell could not be corrected without renaming *Beschikbare uren* to
  *Geboekt / budget*, because a header naming the other reading is the same bug one row up. And
  **an ambiguous figure is not fixed by formatting it better**: `{spent} / {budget}` was
  perfectly formatted on both screens.
- **A task's hour budget belongs where the hours are spent, not only on the task** (#313). The
  allocation existed for a year and was drawn on exactly one screen — the task's own card, the one
  place you are *not* when you are logging against it or deciding what to pick up. It is now on
  the entry form under the task picker (beside the project's, because a task's budget is the
  tighter constraint and neither answers the other's question), in the task list's budget column,
  and on the compact row's ⏱ pill, which reads `1u 30m / 3u` instead of the allocation alone.
  Every one of them **degrades to the plain allocation** rather than to a zero when the API
  withholds the burn: `logged_minutes` is absent, not `0`, for a caller without `time.entry.read`
  — which is how a client-portal login (it holds `tasks.task.read`) never sees what the agency
  burned. Mirroring the *key* the API checks, not `!isPortal`, is what makes that one rule instead
  of four (§15).
- **Hours reach an agreement through its project, never through a second picker.** Logged time
  attaches to a **project**, and a project covered by an active subscription burns against that
  agreement's included hours (#225) — so the timesheet's entry form no longer offers a subscription
  to log against. Two pickers meant two places a retainer's remaining hours could be counted, and
  they could disagree. What replaced it is the answer itself, on the screen that spends the hours:
  the picked project's **remaining** hours under the project field (named after the agreement they
  came from: "Uit: Onderhoudsabonnement"), and a **Beschikbare uren** panel beside the form on the
  one burn scale, hottest first. Both render from the project lookup
  the page already loads with `hours=true` — a number this useful should cost no extra call, and the
  form asks for one *less* than it did. The "no budget" hint only appears when the caller actually
  asked for the burn: a lookup fetched without `hours=true` knows nothing, and silence beats a
  confident "geen urenbudget" that is really "didn't look".
  **And a panel beside a form is about the form's record, not about the org.** The panel listed
  *every* budgeted project the agency has — a scrolling column of other clients' work, next to a
  form filling in one of them, where the useful line was already off the bottom. It now follows the
  entry's own selection: a picked project **is** the answer, a picked client shows exactly what its
  project picker offers (its own projects, plus the client-less ones that are loggable under every
  client), and only an entry naming neither falls back to the full list. Two rules generalise. The
  selection lives in the form, so the form **reports** it (`onscope`) rather than the host guessing
  from the record it handed in — a create form's client and project move while it is being filled.
  And a narrowed panel that finds nothing says so in words instead of unmounting: a box that
  vanishes the moment you pick a client reads as broken. The one exception is a picked project with
  no budget, which the form already answers under the project picker — where the question was
  asked, and where it is not a second empty box.
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
  - **A quick-add row also has to keep the caret: `busy.clearAndFocus(key, name)`** (#367). A
    field that exists to be filled in over and over — a checklist to-do, a tag — wants the reset
    *and* wants the cursor left in it, and the second half does not come for free: `applyAction`
    ends every successful action with SvelteKit's `reset_focus()`, an accessibility rule written
    for navigations and applied to form results too, which focuses `document.body`. So Enter
    added the item and then quietly took the field away, and adding five to-dos cost five trips
    back to the mouse. The affordance refocuses the form's own input once the update has settled
    (so no `bind:this` inside an `{#each}`), only on success — a refusal leaves focus where the
    error handling put it — and places the caret at the *end*, because anything typed while the
    request was in flight survives the reset that fired before it.
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
  end. Per-field save buttons are a known corrected mistake. **And one exit, in one place**
  (#337): a detail page keeps its Opslaan/Annuleren at the foot of the form *and* an
  `EditToggle` exit at the heading — a long record scrolls its own buttons out of view — but
  never a third one folded back into the ⋯. A panel that saves each act as it happens exits
  with **Klaar**; a surface that posts exits with **Annuleren**, the same word its form uses.
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
- **A screen that is no longer signed in says so, and offers the way back on the spot**
  (`core/ui/SessionGuard.svelte`, mounted once on the authenticated shell). Sign out in one tab
  and the session cookie is gone for the whole browser, but the other tabs go on drawing a
  working CRM — every control refusing, none of them able to say why — until something makes
  them ask. The page was not merely stale; it was *claiming* to be signed in. Three signals
  raise the prompt, and they are different in kind on purpose: a `BroadcastChannel` message
  (instant, free, same-origin by definition), a `/session` probe when a tab returns to the
  foreground (throttled to 20s, **not a poll** — a tab left open overnight makes no requests),
  and any same-origin proxy route that already answers 401 passing that on
  (`reportUnauthorized`, which the notification bell's existing minute poll now does — it is the
  only signal that reaches a tab somebody is sitting and reading). Four rules generalise beyond
  auth. **Announce the state, never the intention**: the first version broadcast from the
  sign-out button, so the receiving tab's confirming probe raced the very cookie deletion it had
  been told about, won, and stood the prompt back down half a second after raising it — the
  announcement moved to `/login`, where arriving *is* the proof, since its own load bounces
  anyone still holding a session. **A failed probe is not a verdict**: a dropped connection is
  not a sign-out, and answering one with a sign-in wall over a page that was working a second
  ago is far worse than answering late, so anything short of a clear "no" reads as "keep going"
  (the `cloudflare` lesson, CLAUDE.md §10, in another module). **Recover in place rather than
  redirecting**: a bounce to `/login` throws away the half-written note, the filters, the scroll
  position — everything that did not need to be lost — so the dialog signs you back in
  (`/session/signin`, the login screen's own calls, 2FA included) and the screen you were on is
  still the screen you are on. And **a blocking dialog needs an escape hatch that still nags**:
  refusing to let someone copy unsaved text out of the page behind it is data loss committed to
  prevent confusion, so "Nu niet" collapses it to a bar that will not go away. Escape and a
  backdrop click do not dismiss it (hence not `Modal`), the address is prefilled from whoever
  was using that tab, and the long way round (`/login?next=…`, `core/redirect.ts`) lands them
  back on the same screen.
- **Recovering is not reloading, and the difference is somebody's unsaved work.** The obvious
  end to the flow above is `invalidateAll()` — and it is the one step that can destroy what the
  prompt existed to save: 51 inputs in this app take their `value` straight from `data`, so a
  re-read overwrites a half-typed field with the server's copy. It is also unnecessary. For the
  **same** person the page's data is exactly as stale as it was a minute ago; the session ending
  did not make it staler, and nothing about signing back in fixes anything a re-read would. For
  a **different** person it is mandatory — the screen must stop showing what the previous
  account could see. So every signal carries a `userId` (`/session`, `/session/signin`, the
  `signed-in` broadcast) and the re-read is conditional on it. The general rule: **when a
  recovery path re-fetches, ask what it is fixing** — "refresh everything" is a reflex, and
  here the reflex is the data loss.
- **A refused submit is where unsaved work is most at risk and the app explains itself worst.**
  Press Opslaan with a dead session and the action calls the API without a valid cookie, so
  what comes back is whatever error key that route happens to use — "er ging iets mis" over a
  form that will not save, with nothing connecting it to a session that ended in a tab since
  closed. `InFlight.wrap` (`core/submit.svelte.ts`) is the seam all 290 enhanced forms already
  pass through, so a `failure`/`error` result asks there — `noticeFailedSubmit()`, a **question,
  never a conclusion**, silent when the session is fine, and skipped entirely on a screen with
  no guard mounted. Nothing is lost either way: SvelteKit resets a form only on `success`, so
  the typed values sit untouched under the prompt, and pressing Opslaan again is the whole
  recovery.

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
  third-party integrations. Each group answers one question: what this workspace looks like
  (Werkruimte), who may use it (Team & toegang), what shape our data takes (Gegevens &
  keuzelijsten), how each module behaves (**Modules**), what it talks to (**Integraties**) — the
  last two split apart along CLAUDE.md §6a, because "a module is configured" and "an integration is
  connected" are not the same kind of setting and do not fail the same way. **The screen that
  switches each collection on leads its own group** (#378): Instellingen → Modules first under
  Modules, Instellingen → Integraties first under Integraties. Before that, one screen switched
  both on, it was filed under Werkruimte, and it was called "Modules" — the same word a group
  heading fourteen cards down the index used for something else. A card is named after what is *on*
  it — the screen holding only client numbering is "Klantnummering", not "Bedrijven", which read as
  a sibling of Klantgroepen and was neither.
- **The Instellingen rail** (`core/settings/SettingsShell.svelte`) renders from `xl` up as a sticky
  column, below it as a disclosure over the content, and **on every screen in the section including
  the index**. It lists exactly what the index would show that viewer, marks the current screen,
  and resolves a deep link (`/settings/roles/<id>`) to its section by longest matching href.
  Two earlier positions were wrong and are worth keeping written down, because both were reasoned
  from a plausible principle (#378).
  *"The index needs no rail — its cards are the navigation."* Its cards are the **descriptions**.
  Forty of them stack 3050 px tall in a ragged two-column grid, and the screen you come back to in
  order to find something was the one screen with nothing to find it with. It carries the rail now,
  with `search={false}`, because its own content owns the search over that same list — the rule
  being **one search box per screen**, never two filtering different things.
  *"A flat list of links is the rail."* Forty-one links are 1628 px inside a box 902 px tall on a
  1440 × 950 laptop and 752 px on a 1280 × 800 one, so 45–54% of the tree sat below the fold of a
  **nested** scroller that the page's own scrollbar does not move and overlay scrollbars do not
  advertise. Standing on Instellingen → Modules, the rail beside you ended at Import & export: the
  group that screen belongs to was missing from its own navigation, along with Integraties and
  Systeem. **So the groups collapse** (`SettingsNav`), under three rules that keep collapsing from
  becoming hiding: the group holding the active screen is open *unconditionally* — it is where you
  are, not a preference; a search opens every group that matches, because a result you must reveal
  is not a result; and what you open by hand is remembered in `localStorage`. A closed group shows
  its item count, or an empty group and a collapsed one look identical. Seven headings fit at any
  width; forty-one links fit at none, and the forty-second would only have made it worse.
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
- **The breadcrumb row follows the way in, and only as far as the record confirms it**
  (`core/breadcrumbs.ts`, `core/breadcrumb-labels.ts`, `core/breadcrumb-trail.svelte.ts`). The app
  is a graph, not a tree: a project hangs off a client, a task off a project, and each is also
  reachable from its own list in the sidebar. A purely path-derived row is therefore right about
  *where you are* and silent about *how you got here* — opening a project from Acme's page read
  "Projecten › Site herbouw", with no way back to the client whose page you were reading a click
  ago. So the previous page's record is offered to the next one as a candidate ancestor and is
  drawn **only if the new record names it** (`project.company_id === Acme.id`): history suggests,
  the record decides. That is the whole safety property. A trail assembled from visit order alone
  starts lying the first time somebody opens a record in a new tab, follows a notification link, or
  walks two unrelated screens in a row — it would be a back button claiming to be a hierarchy. It
  walks *back* rather than resetting when the immediately previous record is not a parent, so
  leaving a task for one of its client's invoices keeps `Klanten › Acme`. And it is browser-only:
  `afterNavigate` never runs server-side, so a first load, a reload and a shared link all render the
  plain path-derived row, which is the honest answer — nobody came from anywhere.
  Three rules keep the row readable and true. **A dynamic segment is one the route says is
  dynamic**, not one that *looks* like a UUID — a Google Ads account id is a number, so
  `/marketing/google-ads/4155551234` printed the raw customer id as a crumb until `page.route.id`
  became the authority. **A section crumb is whatever the sidebar calls that section**: a tenant
  renaming Klanten to Relaties (#169) renamed the nav item and nothing else, so the crumb went on
  contradicting the menu directly above it; the nav registry is read first and the static map is the
  fallback for the sections that contribute no nav item. And **length is capped, not thrown away** —
  past four crumbs the middle folds into a "…" that opens, because a trail which silently dropped
  its ancestors is worse than a long one: those crumbs are the only link to the records they name.
  A label is clipped by width with its full text in `title`, never shortened in JavaScript, which
  would mean deciding where a name may break.
  **A record may name its client through a collection as well as through a column** (#401). The
  confirmation was a scalar foreign key read off the record, which modelled one-to-many and nothing
  else — and a contact belongs to its clients through `company_contacts`, so `ContactRead` answers
  with a *list* and carries no `company_id`. `record["company_id"]` was `undefined`, which reads as
  "not this client" and never as "this record cannot answer the question": the trail reset, and "up"
  from a client's contact person became the org-wide address book. That was the team's complaint,
  and it failed on exactly one entity — a task opened from the same page kept its client, which is
  what makes the mechanism worth fixing rather than replacing. So `PARENT_RULES` takes a column
  *or* a collection and confirms on the first that matches. The safety property is untouched: the
  record still decides, it is still the record's own data, and a contact of a different client
  still refuses the crumb. This is CLAUDE.md §15's "failure mode (1) — no anchor" one layer out,
  which is why the test sweeps every record type the row can be about against the **generated API
  types** rather than against this paragraph: a model whose client link is indirect declares
  `__company_horizon_clause__` on the server for the same reason, and a new detail page now has to
  say which of the two it is.
  `tests/unit/breadcrumbs.test.ts` sweeps the real route tree and fails on any segment nothing
  names. That is the enforcement this row needs: it is rendered by the layout for every page, so a
  new screen gets one whether or not anyone thought about it, and "nobody thought about it" looked
  like `prettify()` — the slug with a capital letter, in English, on a Dutch-default app. `/reports`
  read "Reports" and `/companies/<id>/reporting` read "Reporting" for exactly that reason, and
  nothing in the build noticed, because a prettified slug renders perfectly well.
- **A link back to a screen names the screen you were on, not the section it belongs to**
  (`core/screen-position.ts`, `core/screen-position.svelte.ts`). Open Klanten, page to 3, scroll to
  the fiftieth row, click it, then click "Klanten" in the crumb row: you landed on page 1, at the
  top, with the client you had just been reading about two pages and several hundred pixels away.
  Both halves of that are the same mistake — the crumb href was rebuilt from the path, and
  `/companies` is a different screen from `/companies?page=3&status=active&sort=-name`. So every
  navigation records, per pathname, the query string the visitor had there and how far down the page
  they were, and the crumb row links to *that*, with the scroll offset restored on arrival.
  Four things hold it up. **The crumb row carries the slice; the sidebar does not.** Klanten in the
  sidebar is how you go to the section, and a nav item that quietly reapplied last hour's filters
  would be a control that does not do what it says — so the two now differ on purpose, and the
  difference is legible in the href. **Restoration requires an exact URL match**, which is what
  makes that distinction work without a second mechanism: the crumb asks for `?page=3` and gets its
  offset back, the nav item asks for the bare path and gets the top of it, and a filter change, a
  page step or a fresh search all land at the top by construction rather than by each list
  remembering to say so. **The back button is left alone** — SvelteKit already restores scroll per
  history entry, so `popstate` is skipped and the two can never fight over one number. And **it is
  keyed by pathname, with every screen recording**, not by a list of list routes: a registry is a
  list somebody has to remember to add to, which is exactly the failure the crumb row itself exists
  to prevent. A record's tabbed detail page and a long form come back to where they were being read
  without opting in. The handful of screens carrying their own "← Rapportages" link above the crumb
  row call `returnHref(path)` for the same answer.
  It lives in `sessionStorage` — the same lifetime as SvelteKit's own scroll restoration, this tab
  and this visit — capped, evicting least-recently-left first.
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
- **An error page is a screen, and a tenant's client sees it too** (docs/DEPLOY.md). One card,
  the same shape as the login card: the tenant's logo or name, a sentence naming what happened, a
  single link, and the status code last and quietest — a visitor is not helped by "404" set at
  48px. What it says comes from one table (`$lib/core/errors/copy.ts`), shared by the in-app page
  and the two standalone renderers, so the wording cannot drift between "the API is restarting"
  and "the whole app is gone". Three rules are load-bearing:
  - **The status is what we interpret, never the message.** The old page printed
    `t(page.error.message)` — an i18n key on an API error and English prose on a SvelteKit one,
    so roughly half the time it showed the visitor the literal text `errors.not_found`. A
    message is used only when the catalogue actually holds it (`hasMessage`), which is what tells
    one of ours apart from the framework's.
  - **A gateway status says "even niet bereikbaar", never "er ging iets mis".** That is what a
    rolling redeploy looks like from outside; telling an agency's client that something broke,
    over a planned rollover, sends them to the phone — and it is not true.
  - **"Probeer opnieuw" is only offered where retrying can work**, and it is a full document
    load (`data-sveltekit-reload`): the thing that failed is the server, so re-running the same
    load inside the same page proves nothing. A 404 and a 403 answer identically however many
    times they are asked, so there the link goes home instead (#253, a control that always
    refuses is a broken control).

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
- **An inline-create dialog that quietly offers less than the field it stands in for.**
  `TaskQuickCreate` — the "＋ taak toevoegen" behind every task picker, and the only way a task
  is made while reviewing a pending e-mail — asked for *one* assignee with a plain Combobox,
  years after tasks grew a roster (#375). The dialog was not wrong when it was written; it was
  never revisited, and nothing failed: a task created there simply came out assigned to one
  person, so "assign the pair" meant opening the task afterwards — the exact trip the inline
  create exists to save. It draws `AssigneePicker` now, the same chips the full form does, and
  the form action forwards `assignees` rather than a lone id. Two rules come out of it. **A
  quick-create is a shortcut to a record, not a smaller kind of record**: when the entity gains
  a field, the dialog is part of the change. And **a permission mirror must follow the roster
  it now creates** — "sluit deze taak hiermee" is gated on `tasks.task.write:own`, which means
  *any* assignee server-side (`caller_may_write_task`), while the browser's `canWriteTask` read
  only the starred one; harmless while a quick-created task had exactly one assignee, and a
  disappearing control the moment it could have two.
- **A create that writes the row before it asks anything** (#391). `Nieuwe taak` posted a whole
  task on one click — a placeholder title ("Naamloze taak"), a due date of nothing, an assignee it
  picked itself — and landed the user in edit mode over it. That is create-then-edit (#230,
  Principle 3), and the principle is right: a record's definition is edited on exactly one
  surface, so there is no second create form to keep in step. What was wrong is *where the line
  falls*. Closing the tab is not cancelling; the row is on the board, in the client's Taken panel,
  in the export and in `GET /api/v1/tasks`, and nobody made it. `unnamed` (#350) was the mitigation
  — mark those rows so a list can italicise them, print the reader's own word for *unnamed*, and
  offer a filter that finds them — and marking a row is not the same as not writing it.
  So **what identifies the record is asked for before it exists, and everything else stays behind
  create-then-edit.** The dialog for that already existed twice over: `TaskQuickCreate`, which
  every picker's inline-create opens, and the dictation sheet (#382), which refuses
  create-then-edit outright because a spoken task "arrives with all of them already reviewed on
  screen". Both produced named rows the whole time; the list's ＋ was the one entry point that
  skipped the question. It now opens the same dialog — as do the client header, the client's Taken
  panel and a project's to-do list, so there is one answer to *how does a task get made* — and its
  action redirects into edit mode exactly as before. Where the user lands still belongs to the
  surface: a list hands the new task over in edit mode, a to-do list stays where it is, because
  to-dos are written several at a time.
  Two smaller rules ride along. **A default the surface used to apply silently becomes a prefilled
  control, not a dropped feature** — `Nieuwe taak` assigned its creator, so the dialog opens with
  that person on the roster as a chip that can be taken off, which is the same behaviour and a
  visible one. And **the body every entry point posts is one function** (`$lib/modules/tasks/create`),
  because "the title is the caller's" is invisible in a diff and is exactly the kind of rule a
  later refactor re-introduces a placeholder for; it is asserted without a browser in
  `tests/unit/task-create.test.ts`.
- **Two names for one record, drawn as two equal fields.** A client has a label ("Bakkerij
  Jansen") and, sometimes, a legal name ("J. Jansen Holding B.V.") that invoices must be
  addressed to (`companies.legal_name`, `docs/INVOICING.md`). Side by side under "Naam" and
  "Juridische naam", that is a *question* — which one do I fill in? — where the screen should be
  giving an *answer*. So the label keeps the top of the form and the H1, and the second name
  sits inside **Factuurgegevens**, first, above the address: what it belongs to is the block a
  document freezes, not the block a screen prints. Its placeholder is the label, so leaving it
  empty **shows** what will happen instead of describing it.
  Where it is *read* follows the same rule, and the operative word is **differs**: the API sends
  `null` for "the label is also the legal name", so the header line, the billing card and the
  panel all draw nothing at all for the ordinary client — a value equal to the label is treated
  as absent too, because it arrives from an import or from somebody being cautious and is still
  not a second fact. Where it does differ, the client page says so under the H1 (muted, prefixed
  with its label, so it reads as a fact about the record and not as a second title): the invoice
  this client gets will be headed with a name the H1 does not contain, and nobody should have to
  open a card to discover that. The list column is opt-in and not sortable, because the register
  is read by label; it exists for the one job — reconciling a bank statement, a bookkeeper's
  list — where the other name is the only one you have. Search matches **both**, always.
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
- ~~A ★ (or any emoji/glyph) marking the primary chip on top of its brand colour~~ — **reversed**:
  the colour turned out to be the mistake, not the glyph. It said nothing on a chip that had no
  grey sibling to contrast with, and on an amber-branded tenant it said *warning*. The chip carries
  a ★ now; see the rule above. Left struck through rather than deleted, because a design decision
  that was made, held, and then overturned is worth more here than a clean list.
- A primary marker with no words anywhere near it. The glyph says *that* one chip differs; only
  text says what it means, and — on the clients-on-a-contact block, which points the other way
  round from how it reads — which direction it points (#374).
- Chip fields that were editable in use mode: a stray click could detach a contact or move the
  primary. Linking, unlinking and promoting are definition changes and live behind edit mode.
- A burn bar clamped at 100 % (`Math.min(100, pct)`): a project 40 % over budget drew exactly like
  one that had just landed on it. Clamp the bar, never the number.
- A hardcoded `<ul>` per list. Six of them and no user could hide a column; the seventh is what
  `DataTable` exists to prevent.
- **A `shrink-0` badge sharing the identity cell with the identity.** The invoices list drew a
  "Creditfactuur" badge beside the number and marked the *badge* as the thing that must not
  shrink, on the reasoning that the kind is what the row is about (#341). In a `table-fixed` grid
  that reasoning is a measurement, and it lost: the column is 130 px, 98 px of it inside the
  padding, the badge took 84 px, and `2026-0006` was handed 10 px and rendered as **`2.`** — so
  the one document hardest to tell apart from its neighbours (same client, same date, only the
  sign differs) was the only one whose number could not be read. Two rules. **The identity wins
  its own cell**: whatever else lands there yields first, and a marker that cannot yield has to be
  small enough that it never needs to. And **widening the column is not a fix, it is a new
  threshold** — a longer number or a longer translation walks straight back into it, which is why
  the word became a 14 px glyph carrying its label in `sr-only` (and in `title` for a sighted
  hover) rather than a badge with a bigger budget. That is not the ★ mistake above: this glyph
  replaces text that no longer fits and says the same thing to a screen reader that the badge did,
  where the star duplicated a colour that already carried the meaning.
- **A list that opens on everything it has, rather than on what anyone is working on.** Klanten
  listed the archive among the live clients and sorted newest-first, so the first screen of an
  agency's oldest relationship was whoever they signed up last, mixed with people they stopped
  working for years ago (#329). It now opens on every status but archived, A–Z. Two rules came
  out of it, and both are about *saying so*. **A narrowing default is a selected pill, not the
  absence of one**: a list quietly missing its archive looks identical to a list that has none,
  and the only thing that can tell them apart is a control showing itself on. So "Niet
  gearchiveerd" sits in the pill row, next to "Alles" for the other half — a state you can reach
  needs a token in the URL (`?status=all`) or it cannot be linked, bookmarked or reached back to.
  And **the export is handed what the screen resolved, never what the URL says**: `ImpexBar`'s
  `filters` exist so the spreadsheet is the list on screen, and passing the token instead of the
  resolved filter is how the archived rows quietly come back in the file.
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

  **And it closed the hole for exactly the pages the rule was not written about** (#365). The
  contact/project/task pages compose the *web* registry's `EntityPanelSpec`; the **company hub**
  composes the **API**'s `PanelSpec`, which was never given the field — so `GET
  /companies/{id}/panels` declared `companies.company.read` once and then called thirteen
  providers, and a member holding exactly that key received the client's contacts, projects,
  tasks, hours (what somebody worked on, for how long, and whether we bill for it), websites,
  domains with their resolved prices, and the full change history with actor names. Seven of
  thirteen providers self-checked; six did not, and "each provider remembers" is not a rule, it is
  a hope. `PanelSpec.requires_permission` now filters in `registry.panels_for(entity_type, names,
  ctx.can)`, so the provider is never *called* — a check that still runs the query saves no round
  trip and produces the answer anyway. `explicit_public` is the `no_permission_required` of this
  seam: a declaration, with a reason, and a panel carrying neither is a build break
  (`tests/test_company_panels_permissions.py`).
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
- **A shared component that hides more than it should, so its callers reimplement the rest.**
  That same pager then dropped its whole `<nav>` below one page, reasoning correctly that arrows
  over nine rows are decoration and taking the count and the size selector with them. Seven of
  nineteen lists answered by printing their own total under the heading — two wordings, stated
  twice on a long list — and the twelve that did not simply never told a user how many rows they
  were looking at. The tell is the workaround: when several call sites grow the same little
  patch, the shared component is refusing something they all need, and the fix belongs inside it
  (#334). The narrower lesson is worth keeping too: **"this control can do nothing here" is a
  reason to stand the control down, never the surrounding information.**
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
- **A measure is a rule about reading, and a grid is not read.** The entry above says the number was
  chosen against the densest screen this app has. It was not: /tasks with every optional column
  switched on wants 1812 px, and no width in that set is a taste — it is the arithmetic of the
  columns the user switched on. On a 2560 px monitor the page was held at 1600 with every column
  about a tenth under what it asked for (Titel 286 of 360, Labels 180 of 200) and 720 px of screen
  sitting idle beside it; below a 1600 px window the same shortfall starts costing rows, and it was
  worse still before the shrink was made to share (the finding that started this: Titel at its
  160 px floor with nine of eleven titles truncated, next to a 198 px column of em-dashes). So a
  page-level table now **claims** the width its columns actually ask for
  (`$lib/core/ui/measure.svelte.ts`), and the shell grants it bounded twice — never below the
  measure, so a short list still reads inside it and is never stretched thin, and never past the
  room that exists. Tasks with twelve columns lands at 1814 px with every column at its declared
  width and 253 px of margin still to spare: the point is not full bleed. How wide a grid is, is
  arithmetic; how wide a paragraph is, stays a judgement. The header's controls take the same
  measure, or the avatar drifts off the table it sits over. The claim is made from an effect, so a
  wide grid widens once at hydration — the shell cannot see its own content until the content
  exists.
- **That arithmetic now lives in `$lib/core/table/widths.ts`, pure and tested.** #346 has been fixed
  twice — the identity column handed *zero*, then the identity column handed its *floor* while a
  column of em-dashes kept 99 % of its width — and both times the fix read fine in the diff. It is
  invisible in every functional test (each row renders, every value is right; only the columns are
  absurd) and invisible in any screenshot taken at the width you happen to develop at, which is why
  it is now asserted at the widths nobody develops at rather than measured in a browser once. One
  detail came out of the pinning: round a shrunken width *down*. A dozen columns each rounded up sum
  past the box, and the grid answers a two-pixel overshoot with a scrollbar.
- **An `sr-only` label can give the whole document a sideways scrollbar.** It is absolutely
  positioned, and with no positioned ancestor inside the scroll box its containing block is the page
  — and a clip does not apply to a box whose containing block sits outside it. So on any grid too
  wide for its screen, the ⋯ header's 1 px screen-reader label stood at the *table's* right edge and
  the shell scrolled sideways behind a scroller that was already doing the scrolling. `relative` on
  the scroll box is the whole fix. Worth remembering the shape: when a document scrolls horizontally
  and everything visible fits, look for what is positioned, not for what is wide.
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

- **A guard that throws the destination away.** Following a link to a screen you are not signed in
  for landed you on the dashboard, so every deep link anyone pasted into a chat, mailed to a
  colleague or bookmarked arrived as "sign in, then go and find it yourself" — and the one place
  it costs most is the notification mail, whose entire purpose is a link to one record. The
  reading side had been complete since the login screen was written (`?next=` parsed, threaded
  through the 2FA step, landed on); the single line that redirects an anonymous visitor was
  passing `"/login"`. That asymmetry is the thing to watch for: **a half-built feature whose
  present half is the one you read first looks finished.** Three rules came out of fixing it.
  *One producer* (`loginPath`, `$lib/core/redirect.ts`) rather than an `encodeURIComponent` at
  each guard, so the next guard cannot repeat it — the tenant app and the instance console now
  share it, each naming its own login screen and landing page. *The value travels the way the
  request does*: a form action posts to `?/login`, so the page's query string is gone on the
  submit **and on every failure re-render** — hence a hidden field on the way in and an echo in
  every action result on the way out, or one mistyped password silently forgets the target with
  JS disabled. And *a target crossing a boundary rides the server*, never the URL: the SSO
  round-trip parks it in the session beside Authlib's state, because the IdP echoes `state` and
  `redirect_uri` back to us and this value is about to become a `Location` header. Which is the
  standing rule for all of them — an untrusted path goes through `safeInternalPath` at the point
  of *use*, since `//evil.example` and `/\evil.example` both read as another origin to a browser
  and a login screen is exactly where a look-alike host is worth the most.

- **Two front doors to one fact, and the discoverable one was the wrong one.** A client's page
  drew Marketing and Google Ads as two panels, one under the other, both about Google Ads and
  each offering to connect it. They wrote to different tables: the marketing panel's picker
  recorded the marketing link *and* the Ads account row, while the Google Ads panel's "Account
  koppelen" sent you to Instellingen → Google Ads, which recorded only the account. So the
  obvious path left the client half-connected — the Google Ads panel listed the account, the
  marketing panel directly above it still said nothing was connected, and `/marketing` agreed
  with the panel that was wrong. Nothing on any of the three screens could explain it, and the
  cure was to do it a second time somewhere else. Three rules (#338).
  **The write is the thing to unify, not the button.** Deduplicating the controls without the
  API mirror (`google_ads.account.attached` → `marketing`) would have left the same split state
  reachable through the MCP surface and the hand-typed form; deduplicating the write without the
  controls would have left two screens teaching two different gestures. Do both, and do the write
  first — it is what makes any of the buttons safe to point at the same place.
  **A panel that is about X must be able to do X, without leaving the client.** Every other panel
  on a company page keeps the client in the link it offers (`＋ Nieuwe website` →
  `?company=<id>&new=1`); the Ads panel dropped it and landed you on an org-wide credentials
  screen. If the reason a control lives elsewhere is "that is where the table is managed", the
  control is in the wrong place.
  **A field a picker can answer must never be typed.** Instellingen asked for the customer id, the
  account name *and* the `Beheerdersaccount (MCC)` by hand — the last of which is the one value
  that 403s every later call on that account if it is wrong. `GET /google-ads/accounts/available`
  had resolved all three since the module shipped, walking the manager hierarchy and tagging each
  child with the manager it must be reached through, and **no screen called it**: `grep` found it
  only in `schema.d.ts`. A finished endpoint with no caller is not a spare part; it is a screen
  somebody still has to write.
- **One subject, three widgets, three places.** The task card treated "when is this happening" as
  three unrelated controls: Vervaldatum in the details card, Planning in the main column, and
  Herhaling as a three-control box at the very bottom of the sidebar, below Labels, edit-mode only.
  The seams between them were where the UX failed, and every failure was a *seam* failure — a
  recurrence you could not read back, a repeat that dropped fields nobody had decided about, a
  completed task keeping its planned block while its successor started unplanned. Four rules came
  out of fixing it (#335).

  **A rule you can write is a rule you can read.** `{freq, interval, mode}` was writable in three
  boxes and readable nowhere: use mode showed a chip saying `↻ Maandelijks` and no interval, no
  mode and no next date — and it *could not have* shown one, because `recurrence_next_run` was
  stored and exposed to no caller. A control whose stored value has no read state is half a
  feature; the read state is what makes a wrong setting findable. So the rule is one sentence
  ("Elke maand · op dag 1 · op schema") assembled by one function, and the chip, the Planning card
  and the editor's own preview all print it.

  **The number that will be stored is shown while it is being typed, and the API is the one who
  says it** (`POST /leave/requests/preview`'s precedent, #48). Clamping, leap years and "never in
  the past" are arithmetic; a browser that re-derived them would be a second opinion about a
  question the API already answers (#312). The preview goes through a `+server.ts` beside the
  page, never `fetch("/api/v1/…")` from the browser — only traefik routes that prefix, so the same
  call 404s on every dev server and the preview would silently never appear.

  **A control that acts on the *stored* record, offered inside an edit form, saves first.** #230's
  create-then-edit is right — the record exists, so Inplannen is reachable without a save — but
  the schedule modal prefills from what is stored, so typing a title and a budget and pressing
  Inplannen booked a block called "Naamloze taak" for a default hour, Google event included. One
  round trip ahead of the one the user asked for, through the same single save, and edit mode
  stays open because the user asked to plan and not to stop editing. Disabling the button was the
  rejected alternative: a padlock on the thing the user is most likely to want next (#253).

  **A hand-off nobody is told about did not happen.** Completing a recurring task spawned its
  successor and said nothing — the trail read "verplaatst van Open naar Klaar", exactly like an
  ordinary task. Both ends now carry a dated, linked activity line, and the finish prompt is where
  the two remaining consequences are stated: a future planned block that would otherwise stay
  standing in the Agenda and in Google (removable in the same confirm, named with its date), and
  the good news that the rule has already scheduled the next one.

- **A page with two orderings cannot be reordered** (#393, the task page). The same card asked
  *when* before it had said *what*: Planning sat above Omschrijving and Checklists, which is not
  a decision anybody made — #335 pulled three scattered widgets into one Planning card and left it
  where the old details card had been. Moving it was one line. The other half of the request was
  not: Drive had to sit between the task's own content and Reacties, and it could not, because the
  page had **two** orderings with no way to interleave them — the source order of its hand-written
  sections, and the `position` each contributed panel declares (`google` 55, `interactions` 60) —
  and every panel therefore rendered after every section. So the page's own sections are snippets
  now, each carrying a number on the *same scale the panels already use*, and one `{#each}` renders
  the merged list. Three things follow. **The page still names no module** (CLAUDE.md §6): both
  panels keep the position they declare and neither file was touched, so what changed is that the
  page stopped assuming its sections all come first. **Moving a section is one number in one
  array**, which is what makes the next "put X above Y" a five-second change rather than this one
  again. And **a load that hands a panel to a page must hand over its `position` too** — the server
  had been dropping it as a detail of sorting, which is exactly why the page could not sort.

  Its sibling on the same screen: **two surfaces for one idea have to say which is which.**
  Links & bijlagen (files stored here) and Drive (references into the client's Google Drive) read
  as one question to a colleague — *waar staan de bestanden van deze taak* — and now sit next to
  each other, which makes the difference more pressing rather than less: "verwijderen" means
  destroying a file in one and unlinking a reference in the other. They are not merged for exactly
  that reason; the card carries one line naming where its bytes live instead. The line it replaced
  had promised *"een Google Drive-koppeling volgt in een latere fase"* to a page that was by then
  rendering the Drive panel directly underneath it.

- **A page that only *composes* has no foreground, and every card on it is equally unimportant**
  (#364, the client hub). The registry handed the company page a list of panels, the page drew
  each as a full-width card in `position` order, and that was the whole layout. A card holding
  eight invoices and a card saying *"Deze klant heeft nog geen Drive-map."* were the same width,
  the same weight and cost the same to scroll past — 4.6 screens on a well-filled client, 2.9 on a
  young one, ten of whose fourteen cards were a heading over a negative sentence. Four rules came
  out of redesigning it, and none of them costs the composition (§6 is intact: the page still
  draws whatever the registry hands it, and names no module).

  **A card is for content; an absence is a sentence, and ten absences are one sentence with ten
  links.** A module with nothing to show does not earn a heading, a border and 100 px. `PanelSpec`
  declares `empty_when(data)` — only the module can read its own payload — the API sends
  `empty: true`, and the page folds every such panel into one *"Nog niets vastgelegd"* strip of ＋
  chips. Ten ＋ actions in one row are easier to find than ten cards to scroll past, so this
  *improves* discoverability. The one thing it must not cost is the control the empty panel
  existed to offer: a chip links to the module's own create screen (`emptyHref` on the web spec,
  because routing is a web question), and where the module has no such screen — Drive's "koppel
  een map", Google Ads' connect flow live *in the panel* — the chip unfolds that card in place.

  **A panel declares its own weight, because only the module knows.** `prominence` is a working
  surface (something the reader acts on today) or a **register** (correct, occasionally consulted,
  never news); `size` is full or half. How the page then fits them together is the entry below.

  **Vital signs are the panels seam one level up.** `SummarySpec` / `SummaryTile` let a module
  contribute one number, a label, a tone and a link; core lays them out under the header. Not one
  of *openstaand bedrag, uren deze maand, open taken waarvan n over tijd, laatste contactmoment,
  eerstvolgende verlenging* was on the page before, though every one was derivable from a panel
  the reader had to scroll to and add up by eye. Each tile **opens what it counted** (principle 7,
  applied at the top rather than inside a card), the value travels **raw** with its units so the
  reader's locale formats it (§8), and a module returns **no tile** rather than a zero — a strip
  permanently reading "€ 0,00 openstaand" is the chrome the redesign exists to remove.

  **A panel with a control beside its title draws its own heading row.** The host owns the `<h2>`,
  so such a panel had nowhere to put its ✎ and pushed a button row *underneath* — a band of empty
  card with one control floating in it. `ownsHeader` + `PanelHeader` puts them on one line; the
  title still comes from the API's `title_key`, so a panel does not get to rename itself by
  drawing its own header.

- **A grid of cards is a *row* layout, and rows are what leave the holes.** #364 read the two
  declarations above correctly and then drew them the one way that cannot fit: a row is as tall as
  its tallest card, so a short card leaves the space under it empty, and a full-width panel
  arriving after an odd number of halves leaves half a row empty beside the one before it. On a
  real client that measured a **271 px void** under Abonnementen, a whole empty half-row beside
  Uren, and four ragged bottom edges — a page that reads as unfinished, which is #364's own
  complaint one layer down. `items-start` is not the fix for that; it is the setting that makes the
  holes exact. So the ordered panels are cut into **blocks**, and each block gets the layout its
  own count deserves — every one hole-free by construction rather than by luck:

  - **A card alone on its row takes the row.** A half-width panel with no half-width neighbour is
    drawn full width; the alternative is a bordered rectangle beside nothing.
  - **Two cards match.** With two there is nothing to pack, so they sit in a stretching two-column
    grid: bottoms level, one edge, no gap. This is not "stretch everything" — the old warning
    against stretching a two-row list to match a tall card still stands, which is exactly why
    three or more do something else.
  - **Three or more pack.** CSS multi-column: each card keeps its natural height and the browser
    balances the lanes. Real masonry with no measuring, no layout jump after hydration, and no
    second opinion about a height the browser already knows. It costs one thing worth writing
    down — a multi-column container is a fragmentation context, so a panel that opens a dropdown
    belongs in the primary lane (pairs and solos) or wants a viewport-anchored popover.

  Every kind collapses to one column below `lg`, so this is a desktop rule and a phone still gets
  one stack. The same complaint reached the vital-signs strip, where the *count* is what varies: a
  fixed five-column grid fits five tiles and nothing else, so a client with no invoices contributed
  four and the strip stopped 232 px short of the right edge, the empty slot reading as a tile that
  had failed to load. "Nothing is a number" (above) is what makes the count variable in the first
  place, so the tiles **share** the row (`flex-1`) instead of being dealt into slots sized for a
  count nobody promised — one row at `lg` and up, whole rows below it.

- **One edit surface for every size of edit.** Everything about a client — thirty fields, its
  contact people and its logo — was changed in one 512 px `Modal` that rendered **1445 px tall**
  on a 900 px laptop, so Opslaan started below the fold and changing a billing address put the
  logo uploader on screen. The size of the edit surface should match the size of the edit (#364):

  - **Tier 1, one field, in place.** The status pill was already the right control in the right
    place; it just did nothing. It opens a `Combobox`, PATCHes on pick, and the trail records it.
    Submit **one frame after** `onselect` (`requestAnimationFrame`, the shape `LinkField` uses):
    the handler fires before the binding reaches the hidden input, so submitting straight from it
    posts the value that was there when the dropdown opened — the pill flicks back to what it
    already said and the write is a silent no-op.
  - **Tier 2, a section.** Gegevens and Factuurgegevens each carry their own ✎ that flips *that
    group* into edit mode, the pattern the contactpersonen panel already used. What makes it safe
    is on the server: the update action patches **only the fields the form actually carried**
    (`form.has(...)`, never `?? ""`), so a section save cannot null what it left out — absent
    means leave alone and an explicit `null` clears, exactly as bulk edit reads it (§18).
  - **Tier 3, the whole record**, in a `SlideOver` rather than a `Modal`: docked right and full
    height it fits a long form without going below the fold, and the record you are editing
    against stays visible beside it.

  And **a save must say so.** The app had no toast primitive at all: the dialog closed, one value
  changed somewhere in a 4116 px page, and if you had scrolled you would not see it.
  `$lib/core/ui/toast.svelte` + `ToastHost` is that gap closed once, in core — a report, never a
  question; not an error channel (a form's own error stays beside the control that produced it);
  and never the only copy of anything, which is what makes auto-dismissal safe.

- **`replaceState` during hydration takes the rest of the page with it.** Clearing a consumed
  `?edit=1` from an `$effect` (or from `afterNavigate` on the first load) throws *"Cannot call
  replaceState before router is initialized"*, and a throw in the hydration pass aborts every
  effect after it — which left the edit surface's `Combobox`es showing their placeholder over
  perfectly good values, a symptom that looks nothing like its cause. Consume a URL intent on a
  **user gesture** instead (`clearEditIntent()` when the surface closes), which also covers the
  ways out a handler never sees: the ✕, Escape and the backdrop.

- **A dialog whose backdrop is `fixed` inside a scrolling wrapper hands the wheel to the page.**
  `Modal`'s backdrop was `fixed inset-0` *inside* the `fixed inset-0 overflow-y-auto` wrapper, so
  it was positioned against the viewport rather than against the wrapper and was not part of its
  scroll chain: with the pointer over the dim area the wheel scrolled the document behind by
  600 px while the dialog stood still. Body scroll was never locked either. On the tallest dialog
  in the app that was the difference between reaching Opslaan and not. `absolute` within the
  wrapper, plus `overflow: hidden` on the documentElement while open (`position: fixed` would jump
  the page to the top).

- **A backdrop is measured against the thing that scrolls, not against the viewport.** The same
  `Modal`, the other half of the same bug. `absolute inset-0 min-h-full` on a child of the
  `fixed inset-0 overflow-y-auto` port resolves to exactly *one viewport height*, because that is
  the port's own height — it does not grow with what is inside it. So an online-meeting note that
  laid out 2 555 px tall dimmed the first 720 px and left the rest of the page at full brightness,
  with the title and the ✕ scrolled off the top: the dialog read as broken rather than as long,
  and closing it meant scrolling back up to find the ✕. Three rules come out of fixing it.
  - **The element the backdrop is measured against must be the one that grows.** The port is now
    transparent and holds a single `relative flex min-h-full` wrapper *in flow* — at least the
    viewport, taller when the dialog is — and the backdrop is `absolute` against that. It still
    may not be `fixed`, for the scroll-chain reason above: `fixed` and `absolute`-against-the-port
    both cover one viewport and stop, and only one of them also breaks the wheel.
  - **A header that scrolls out of reach is not a header.** Title and ✕ are `sticky top-0`, opaque
    and ruled — the shape `SlideOver` has always had, so the two dialogs now agree — and the title
    is `line-clamp-2`, because on the surface that needed this (an e-mail subject) an unbounded
    one would push the body off the screen it is pinned to.
  - **Internal scrolling is the tempting fix and the wrong one here.** Capping the card and giving
    the *body* `overflow-y-auto` is what most dialogs do, and it would clip every absolutely
    positioned descendant whether or not anything overflows — including the `Combobox` list, which
    deliberately hangs past the bottom edge of a short dialog. Scroll the whole overlay; pin the
    header.

  `SessionGuard` (deliberately not a `Modal`) had the sibling flaw: an item centred *in* its own
  scroll port overflows equally in both directions, and the part above the top is unreachable at
  any scroll position — so on a short window the product name and the title of a dialog you cannot
  dismiss were simply not there. Centre on a `min-h-full` wrapper inside the port, never on the
  port itself. Those two are the app's only full-screen scroll ports; everything else that scrolls
  is a `max-h-*` list inside a card.


- **A field that is required is asked for at every door, and a screen may not offer a way to
  empty it** (#392, tasks' deadline). *"Binnen het CRM moet altijd een datum bekend zijn, zodat de
  taak zichtbaar blijft en niet kan worden overgeslagen."* An undated task is not merely
  unscheduled: it is missing from `?due=overdue`, from the Agenda's deadline feed and from both
  dashboards' overdue counts, so it is invisible to the whole urgency vocabulary — which is why
  the answer is a required field rather than a warning. Four rules generalise past this one field.
  **Every create surface asks, and there is one place that makes them.** The task board's ＋, the
  client header, the client's Taken panel, a project's to-do list and every picker's inline-create
  are five doors onto one write, and a rule enforced at four of them is a rule with a hole in it —
  so the deadline joined the title inside `taskCreateBody` (#391), which refuses without either
  rather than inventing one, and `TaskQuickCreate` and the dictation sheet mark both fields
  `required` so the refusal is met before the round trip. The API is still the boundary
  (`TaskCreate.due_date` is required; `TaskUpdate` refuses an explicit `null`).
  **A deadline is not a calendar booking.** `Vervaldatum` and `Geplande blokken` sit in one
  Planning section (#335) and only the first is mandatory: setting one never implies the other,
  and planning the work into the agenda stays optional.
  **`required` on a control the form does not own validates nothing, and it looks identical to
  one that does.** A control is a candidate for constraint validation only while it is associated
  with the form being submitted, so on the single-save detail layouts — where the field sits
  outside `<form id="task-edit">` and joins it by `form=` — `required` on `DateInput`'s visible
  box was inert, and putting it on the hidden input beside it would have been inert too (a hidden
  control is barred from validation by definition). The visible box now carries `form={formId}`
  as well; it has no `name`, so it submits nothing and only validates. Invisible in review, and
  invisible in use until somebody saves an empty field: check `form.checkValidity()` in a browser
  rather than reading the attribute. Its sibling: **a required field's picker loses its
  "Wissen"** — a control that empties a box the very next submit refuses is #253's control that
  can only refuse, and drawing it teaches the user a gesture the form will punish.
  **The rows written before the rule are a design problem, not a migration problem.** The column
  stays nullable for a release (expand/contract, docs/WORKFLOW.md), so an instance upgrades
  carrying tasks the new rule forbids: they open, they render and they save in every field, the
  edit form says in one amber line what it will ask for, `?undated=1` gathers them, and the ✎ bulk
  edit dates a whole selection at once. A refusal on the status of somebody's own backlog would
  have been the first thing an agency met after upgrading.
