<script lang="ts">
  /**
   * Interacties (#168): the full, searchable list of contactmomenten in the shared
   * `DataTable` — the narrow pending-email queue grew into this page, and the review flow
   * (approve / reject / move) is now just its `?status=pending` filter state. Row actions
   * reuse the exact dialogs the per-record panels use. Columns sort server-side like every
   * other list (#238); the day sections only render while the order is the timeline, so
   * sections and sort can never disagree.
   */
  import {
    ArrowRightLeft,
    Check,
    CheckCheck,
    Inbox,
    Link2,
    List as ListIcon,
    Mail,
    Pencil,
    Plus,
    Search,
    Trash2,
    X,
  } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import { addMonths, isoAddDays, mondayOnOrBefore, monthOf } from "$lib/core/calendar";
  import { fmtDateTime, fmtMonthYear, fmtPeriod } from "$lib/core/format";
  import { t, tn } from "$lib/core/i18n";
  import { memberLabel, type PickerMember } from "$lib/core/members";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import MemberPicker from "$lib/core/ui/MemberPicker.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import GmailRefreshButton from "$lib/integrations/google/GmailRefreshButton.svelte";
  import { INTERACTION_COLUMNS } from "$lib/modules/interactions/columns";
  import EmlUploadForm from "$lib/modules/interactions/EmlUploadForm.svelte";
  import {
    contactChips,
    dayLabel,
    type InteractionItem,
    type InteractionKindDef,
    isGmailRow,
    kindIcon,
    kindLabel,
    localDay,
    participantNames,
    reviewIds,
    withBody,
  } from "$lib/modules/interactions/format";
  import { snippetPreview } from "$lib/modules/interactions/snippet";
  import InteractionBulkAssignDialog from "$lib/modules/interactions/InteractionBulkAssignDialog.svelte";
  import InteractionConversationDialog from "$lib/modules/interactions/InteractionConversationDialog.svelte";
  import InteractionDetailModal from "$lib/modules/interactions/InteractionDetailModal.svelte";
  import InteractionForm from "$lib/modules/interactions/InteractionForm.svelte";
  import InteractionMoveDialog from "$lib/modules/interactions/InteractionMoveDialog.svelte";
  import { recordHref, recordLabelKey, type RecordField } from "$lib/modules/interactions/scope";

  let { data, form } = $props();

  const items = $derived(data.items as InteractionItem[]);
  const kinds = $derived(data.kinds as InteractionKindDef[]);
  const kindByKey = $derived(new Map(kinds.map((k) => [k.key, k])));
  const mentionCandidates = $derived(
    data.members.map((m: { user_id: string; full_name: string | null; email: string | null }) => ({
      id: m.user_id,
      name: memberLabel(m),
    })),
  );

  const me = $derived(page.data.user?.id ?? null);
  const canWrite = $derived(can(page.data.user, "interactions.interaction.write"));
  // Naming a *colleague* is the read_all grant (#168); "mijn" and "iedereen" are nobody's, so
  // without it the picker offers those two and no roster at all. The API enforces it harder.
  const ownerMembers = $derived(data.canReadAll ? (data.members as PickerMember[]) : []);
  // Two words that are not a person, and they lead: you land on your own moments (#263) and
  // widen from there. Every choice is written out, "mijn" included — a record-scoped view
  // defaults to iedereen (#323), so deleting the param to mean "me" would make that option
  // do nothing.
  const ownerExtra = $derived([
    { value: "me", label: t("interactions.filter.mine") },
    { value: "all", label: t("interactions.filter.everyone") },
  ]);

  const table = createTableLayout<InteractionItem>({
    all: () => INTERACTION_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      subject: subjectCell,
      kind: kindCell,
      linked: linkedCell,
      owner: ownerCell,
      when: whenCell,
    }),
  });

  // Day sections only make sense while the rows *are* a timeline: any other sort would put
  // one day's rows in several sections, so the sections stand down instead of lying (#238).
  const timelineOrder = $derived(!table.sort || table.sort.replace(/^-/, "") === "occurred_at");
  const groups = $derived.by(() => {
    if (!timelineOrder) return undefined;
    const out: { key: string; label: string; collapsible: boolean }[] = [];
    for (const item of items) {
      const key = localDay(item.occurred_at);
      if (out.some((group) => group.key === key)) continue;
      out.push({ key, label: dayLabel(key), collapsible: false });
    }
    return out;
  });

  // --- filters (URL-driven; the SSR load does the actual filtering) ------------- //
  function filterHref(patch: Record<string, string | null>): string {
    const url = new URL(page.url);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    resetPage(url);
    return url.pathname + url.search;
  }
  function applyFilter(patch: Record<string, string | null>): void {
    void goto(filterHref(patch), { keepFocus: true, noScroll: true });
  }
  /**
   * A pressed control has to look pressed, and `bg-surface` **is the page** (`app.css`:
   * `--surface` is the page background, `--surface-raised` is a card on it). Every tab here
   * therefore marked itself active by painting itself the colour it was already sitting on:
   * "Deze week" selected and "Deze week" not selected differed by a font weight nobody reads at
   * a glance. Raised + ringed is the same trick a card uses to sit above the page, which is
   * exactly the relationship a chosen tab has to the row it is in.
   */
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm ${
      active
        ? "bg-surface-raised font-medium text-text shadow-sm ring-1 ring-inset ring-border"
        : "text-text-muted hover:text-text"
    }`;

  // --- the two views (#…): the review queue, and everything -------------------- //
  /**
   * The page opens on **Te beoordelen** and `?status=all` is the whole timeline. Both tabs write
   * their value out loud rather than one of them clearing the parameter, because "absent" means
   * two different things here — the queue on the plain list, everything under a record chip
   * (`+page.server.ts`) — and a tab whose href depends on which of those you are looking at is a
   * control that can point at itself.
   */
  const reviewing = $derived(data.filters.pending as boolean);

  /**
   * What narrows whichever of the two views you are on, rendered by the shared bar (#354).
   *
   * Two of the three stand down over the review queue, and both for the same reason (#253's
   * control that always refuses): every pending row is an e-mail — `record_email` is the one
   * writer that ever sets `pending`, and it writes `kind=email` — so the kind select could only
   * pick the kind already showing; and an unreviewed e-mail is private to its mailbox owner, so
   * every colleague but you would answer an empty list. The kind comes back the moment a link
   * arrives with it set, because a filter that is narrowing the list must be visible whatever
   * view it is narrowing.
   */
  const filterDefs: FilterDef<string>[] = $derived([
    { kind: "search", key: "q", placeholder: t("interactions.search") },
    {
      kind: "select",
      key: "kind",
      hidden: reviewing && !data.filters.kind,
      placeholder: t("interactions.column.kind"),
      options: kinds.map((kind) => ({ value: kind.key, label: kindLabel(kind, data.locale) })),
    },
    {
      kind: "custom",
      key: "owner",
      hidden: reviewing,
      render: ownerFilter,
      // "mijn" is the same filter spelled another way, so the bar has to be told about it or
      // "wissen" would leave the list narrowed to one person with nothing saying so.
      extraKeys: ["mine"],
      // "me" is the *default* this list opens on, not a filter somebody set — counting it would
      // offer "wissen" on arrival and badge the phone's toggle with a 1 nobody asked for.
      active: (data.filters.ownerValue ?? "me") !== "me",
    },
  ]);
  /** The viewer's own unreviewed moments, whatever this list is currently narrowed to. */
  const pendingTotal = $derived((data.pendingTotal as number | undefined) ?? 0);
  const viewTabClass = (active: boolean) =>
    `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors ${
      active
        ? "bg-brand font-semibold text-white shadow-sm"
        : "text-text-muted hover:bg-surface hover:text-text"
    }`;

  // What this list is narrowed to (#323). A panel's truncation notice links here, so the page
  // must say which record it is showing — a filtered list that presents as everything is the
  // bug one screen to the left. An unresolvable name still gets its chip: the filter is on
  // either way, and silence about it is the failure being fixed.
  const scopeChips = $derived(
    data.filters.records as { field: RecordField; id: string; label: string | null }[],
  );
  /** `include=tasks` only qualifies the project filter (#147), so it leaves with it. */
  const clearRecordHref = (field: RecordField) =>
    filterHref(field === "project_id" ? { [field]: null, include: null } : { [field]: null });

  // --- date navigation (#238): week switcher, month filter, free range ---------- //
  // All three write the same `from`/`to` URL params; the SSR load turns them into the API's
  // `date_from`/`date_to`. The bounds are org-local days, like the day-group headers.
  const dateFrom = $derived((data.filters.from as string | null) ?? "");
  const dateTo = $derived((data.filters.to as string | null) ?? "");
  const todayIso = localDay(new Date().toISOString());
  const lastDayOf = (month: string) => isoAddDays(`${addMonths(month, 1)}-01`, -1);
  /** The active range is exactly one Mon–Sun week. */
  const weekActive = $derived(
    !!dateFrom && dateFrom === mondayOnOrBefore(dateFrom) && dateTo === isoAddDays(dateFrom, 6),
  );
  /** The active range is exactly one calendar month → its "yyyy-mm", else "". */
  const monthActive = $derived.by(() => {
    if (!dateFrom || !dateTo) return "";
    const month = monthOf(dateFrom);
    return dateFrom === `${month}-01` && dateTo === lastDayOf(month) ? month : "";
  });
  function weekHref(delta: -1 | 0 | 1): string {
    // The arrows step from the active week (or from today's); the label always resets to now.
    const base = delta !== 0 && weekActive ? dateFrom : mondayOnOrBefore(todayIso);
    const start = isoAddDays(base, delta * 7);
    return filterHref({ from: start, to: isoAddDays(start, 6) });
  }
  /** The last twelve months, newest first — the month filter's options. */
  const monthOptions = Array.from({ length: 12 }, (_, i) => addMonths(monthOf(todayIso), -i));

  // --- row actions: the panel body's rules, on table rows ----------------------- //
  const isOwner = (item: InteractionItem) =>
    item.owner_user_id !== null && item.owner_user_id === me;
  // An uploaded .eml (#262) has no mailbox behind it, so it edits like a hand-logged row;
  // only gmail rows belong to the review flow.
  const mayEdit = (item: InteractionItem) =>
    !isGmailRow(item) &&
    (isOwner(item)
      ? can(page.data.user, "interactions.interaction.write", "own")
      : can(page.data.user, "interactions.interaction.write", "any"));
  const mayMove = (item: InteractionItem) => (isGmailRow(item) ? isOwner(item) : mayEdit(item));
  /**
   * `InteractionService.delete`'s own gate (`_writable_or_404`), mirrored — the key the call
   * makes, at the scope it makes it (§15).
   *
   * Deleting is **not** the edit rule, and riding `mayEdit` here is what hid that: a logged
   * Gmail e-mail is refused no edit anybody would want (its fields mirror a real message) but it
   * is an ordinary record to delete, so its ⋯ menu had no Verwijderen at all and the only way to
   * meet the rule was to tick a whole page of them and read "0 verwijderd" afterwards. Nothing
   * about the source or the status is a bar any more; whose row it is still is.
   */
  const mayDelete = (item: InteractionItem) =>
    isOwner(item)
      ? can(page.data.user, "interactions.interaction.delete", "own")
      : can(page.data.user, "interactions.interaction.delete", "any");
  /**
   * How many contact moments a row actually stands for — the fold's own badge (#272).
   *
   * A row is a folded conversation, so deleting one deletes the thread; the confirmation has to
   * count messages and not rows, or "9 contactmomenten verwijderen?" is the wrong number over a
   * page where three of them are threads.
   */
  const messageCount = (item: InteractionItem) => item.conversation_count ?? 1;
  /** The badge's words: on the queue a fold is what still waits, on the timeline what was said. */
  const countText = (item: InteractionItem) =>
    tn(
      item.status === "pending"
        ? "interactions.thread_pending_count"
        : "interactions.conversation_count",
      messageCount(item),
    );

  /**
   * Bulk review (#299): a queue of forty auto-matched emails is reviewed a screenful at a time
   * or not at all, so the review flow gets a batch form.
   *
   * The three actions ride the shared ✎ selection mode: pressing it turns the checkboxes on and
   * opens the strip above the table holding Goedkeuren / Toewijzen / Afwijzen, so a queue nobody
   * is triaging today looks like an ordinary list. **Verwijderen joins them** — a mis-logged
   * import or a test thread is a batch like any other — but there is no bulk *edit*: an
   * interaction's fields are the record of what was said, and no two rows want the same value.
   *
   * Two subsets, because the review actions genuinely differ. **Re-filing** works on any of the
   * caller's own Gmail rows — `remap` has no status check, so "approve now, file later" is a
   * real workflow. **Approving and rejecting** need a still-pending row. Each item therefore
   * carries its own subset as `eligible`, which the bar renders beside the label whenever it is
   * fewer than the selection; the API reports the rest rather than refusing the batch, but an
   * item that silently did less than it said would still be lying.
   *
   * **And a subset of none has to say so out loud.** `eligible: 0` disables the button, which
   * was the whole of the answer: an agency whose emails arrive as uploaded `.eml` files (#262)
   * or are already logged ticked three of them, pressed a greyed "Afwijzen (0)" and got
   * nothing — no message, no tooltip, no request. The count is not an explanation; it names
   * the number and not the rule, and the rule here is not guessable from the screen (these
   * *are* emails, and they *are* yours). So each action states why it cannot run over this
   * selection, and `(0)` becomes the short form of a sentence rather than the whole of it.
   */
  const canReview = $derived(can(page.data.user, "interactions.interaction.review"));
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  const selectedItems = $derived(items.filter((item) => bulkSelected.includes(item.id)));
  // A ticked row on the queue is a folded conversation, so the ids the batch is handed are the
  // thread's (`reviewIds`): one tick, every waiting message of it. The bar's count says how many
  // messages that is, and the API still refuses per row whatever it refuses.
  const bulkFilableIds = $derived(
    selectedItems.filter((item) => isGmailRow(item) && isOwner(item)).flatMap(reviewIds),
  );
  const bulkPendingIds = $derived(
    selectedItems
      .filter((item) => isGmailRow(item) && isOwner(item) && item.status === "pending")
      .flatMap(reviewIds),
  );
  /**
   * The rows a delete would actually remove — `mayDelete`, which is the API's own rule, over
   * the selection. One predicate for the ⋯ menu and the bar, because a row whose menu offers
   * Verwijderen and a bar that skips it are two answers to one question.
   */
  const bulkDeletableRows = $derived(selectedItems.filter(mayDelete));
  const bulkDeletableIds = $derived(bulkDeletableRows.map((item) => item.id));
  /** What those rows stand for: the folded threads counted out into messages. */
  const bulkDeletableCount = $derived(
    bulkDeletableRows.reduce((total, item) => total + messageCount(item), 0),
  );
  /**
   * Why a review action can do nothing with what is ticked — `undefined` while it can.
   *
   * Only over a non-empty selection: "select something first" is the bar's own sentence and a
   * better answer than this one when nothing is picked yet.
   */
  const reviewBlocked = (eligible: number, key: string) =>
    bulkSelected.length > 0 && eligible === 0 ? t(key) : undefined;
  let showBulkAssign = $state(false);
  let showBulkReject = $state(false);
  // Approve is a plain POST with no dialog in front of it, so it stays a real `<form>` — that is
  // what `use:enhance` needs, and what keeps the in-flight state and forms:check honest. A bar
  // button has an `onclick`, not a submit, so it fires the form from here. `requestSubmit()`
  // and not `submit()`: only the former dispatches a submit event, which is the event `enhance`
  // listens for — `submit()` would bypass it and do a full page POST.
  let approveForm = $state<HTMLFormElement | null>(null);

  // One configuration, spread into the ✎ in the toolbar and the strip above the table: they
  // render in different places and must never disagree about what this list can do.
  const bulkConfig = $derived({
    // The review trio only for someone who may review: they all declare that one permission,
    // and a non-reviewer holding only `delete` should get the ✎ with Verwijderen in it, not
    // three buttons that would 403.
    items: canReview
      ? [
          {
            label: t("interactions.approve"),
            icon: Check,
            onclick: () => approveForm?.requestSubmit(),
            eligible: bulkPendingIds.length,
            disabledReason: reviewBlocked(bulkPendingIds.length, "interactions.bulk.none_pending"),
          },
          {
            label: t("interactions.assign"),
            icon: ArrowRightLeft,
            onclick: () => (showBulkAssign = true),
            eligible: bulkFilableIds.length,
            disabledReason: reviewBlocked(bulkFilableIds.length, "interactions.bulk.none_filable"),
          },
          {
            label: t("interactions.reject"),
            icon: X,
            onclick: () => (showBulkReject = true),
            danger: true,
            eligible: bulkPendingIds.length,
            disabledReason: reviewBlocked(bulkPendingIds.length, "interactions.bulk.none_pending"),
          },
        ]
      : [],
    // No `fields`: nothing on a contact moment is worth setting across a selection. Delete is
    // the one generic action it takes (`app/modules/interactions/bulk.py`), and the service
    // refuses per row what it always refuses — a row still in review, or someone else's. Which
    // is exactly why it carries a count like the three above it: the API reporting a skipped
    // row is the honest answer to a batch, not a substitute for saying beforehand that none of
    // these qualify.
    deletePermission: "interactions.interaction.delete",
    deleteEligible: bulkDeletableIds.length,
    deleteDisabledReason: reviewBlocked(
      bulkDeletableIds.length,
      "interactions.bulk.none_deletable",
    ),
    // The count the dialog asks about is the count that will *go*, not the count that is ticked
    // — and a ticked row is a folded conversation, so those are different numbers. Ticking nine
    // rows of which three are threads deletes more than nine contact moments, and the one place
    // to say so is before it happens.
    deleteMessage: t("interactions.bulk.delete_message", { count: bulkDeletableCount }),
  });

  let showCreate = $state(false);
  let showUpload = $state(false);
  /** Set when the upload dialog is opened to fill the gaps in one conversation (#342). */
  let gapThreadId = $state<string | null>(null);
  let showEdit = $state(false);
  let editing = $state<InteractionItem | null>(null);
  const busy = new InFlight();

  // The inline client / project create used to live here and be handed to the form as
  // `oncreatecompany` / `oncreateproject`. Both dialogs now sit inside the form itself, which is
  // what puts them on every host that renders it — the edit modal below included, which this
  // page never wired and which therefore had no ＋ at all.
  let showMove = $state(false);
  let moving = $state<InteractionItem | null>(null);
  let showConversation = $state(false);
  let linkingConv = $state<InteractionItem | null>(null);
  let deleteId = $state("");
  /** What the row about to be deleted stands for, so the confirmation can name a whole thread. */
  let deleteCount = $state(1);
  let confirmDelete = $state(false);
  let showReject = $state(false);
  let rejecting = $state<InteractionItem | null>(null);

  // Clicking a row opens the shared detail modal (#184): the email reads with its line breaks,
  // no sideways scroll, and a pending gmail row is assigned + approved (or rejected) in place —
  // the exact review flow the per-record panels use, now on the standalone list too.
  // Deep link from the dashboard tile (issue #15) and anything else naming one moment:
  // `?interaction=<id>` opens that row's detail modal on arrival, the same shape the leave
  // calendar's `?request=` uses. A `$state` initializer rather than a `$derived`, so closing the
  // modal does not reopen it while the param is still in the URL. An id this page does not hold
  // is the load's own by-id read (`data.deepLinked`) — a notification names a note that is
  // weeks old and behind somebody else's owner filter, so "not on page 1" is the normal case
  // rather than the exception. The row wins where there is one, so the modal and the list stay
  // one object.
  const deepLinked = () =>
    items.find((item) => item.id === page.url.searchParams.get("interaction")) ??
    (data.deepLinked as InteractionItem | null) ??
    null;
  const initialDetail = deepLinked();
  let showDetail = $state(initialDetail !== null);
  let detailItem = $state<InteractionItem | null>(initialDetail);
  function openDetail(item: InteractionItem) {
    detailItem = item;
    showDetail = true;
  }

  function menuItems(item: InteractionItem) {
    const entries = [];
    if (mayEdit(item)) {
      entries.push({
        label: t("common.edit"),
        icon: Pencil,
        onclick: async () => {
          // The row's body is fetched before the form opens (#290) — the form posts that
          // field, so editing a list row without it would blank the notes on save.
          editing = await withBody(item);
          showEdit = true;
        },
      });
    }
    if (mayMove(item)) {
      const pending = item.source === "gmail" && item.status === "pending";
      entries.push({
        label: pending ? t("interactions.assign") : t("interactions.move"),
        icon: ArrowRightLeft,
        onclick: () => {
          moving = item;
          showMove = true;
        },
      });
    }
    if (mayDelete(item)) {
      entries.push({
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = item.id;
          deleteCount = messageCount(item);
          confirmDelete = true;
        },
      });
    }
    // Glue an email Gmail didn't thread automatically onto another conversation (#272) —
    // owner-only, logged gmail rows only, mirroring the API's own gate.
    if (
      item.kind === "email" &&
      item.source === "gmail" &&
      item.status === "logged" &&
      isOwner(item)
    ) {
      entries.push({
        label: t("interactions.add_to_conversation"),
        icon: Link2,
        onclick: () => {
          linkingConv = item;
          showConversation = true;
        },
      });
    }
    // "Mist er een bericht?" (#342): the rest of *this* conversation, with the gaps named. The
    // thread id came off this very row, so it asks about a conversation the poller already told
    // us about — no search, no mailbox listing, nothing new to consent to. Owner-only for the
    // same reason every other Gmail action here is: it reads through their grant, not ours.
    if (item.source === "gmail" && item.gmail_thread_id && isOwner(item)) {
      entries.push({
        label: t("interactions.gmail.thread_open"),
        icon: Search,
        onclick: () => {
          gapThreadId = item.gmail_thread_id ?? null;
          showUpload = true;
        },
      });
    }
    if (item.source === "gmail" && item.status === "pending" && isOwner(item)) {
      entries.push({
        label: t("interactions.reject"),
        icon: X,
        danger: true,
        onclick: () => {
          rejecting = item;
          showReject = true;
        },
      });
    }
    return entries;
  }

  /**
   * What a row hangs on, capped (#263). Four unbounded chips wrapped to two or three lines and
   * broke the day-grouped timeline's single-line rhythm, so a row shows only the **most
   * specific** organisational link — a task or a project already implies its client — plus the
   * person, and counts the rest into a "+N" the detail modal opens in full.
   *
   * A roster (#300) is capped the same way and for the same reason: a meeting with five people
   * would otherwise re-break the rhythm this cap exists to protect. The lead shows — it is what
   * the Contactpersoon column sorts by — and the rest join the "+N".
   */
  const CONTACT_CHIPS = 1;
  interface LinkChip {
    href: string;
    label: string;
  }
  function linkChips(item: InteractionItem): { visible: LinkChip[]; hidden: LinkChip[] } {
    const org: LinkChip[] = [];
    if (item.task_id && item.task_title)
      org.push({ href: `/tasks/${item.task_id}`, label: item.task_title });
    if (item.project_id && item.project_name)
      org.push({ href: `/projects/${item.project_id}`, label: item.project_name });
    if (item.company_id && item.company_name)
      org.push({ href: `/companies/${item.company_id}`, label: item.company_name });
    const people = contactChips(item);
    return {
      visible: [...org.slice(0, 1), ...people.slice(0, CONTACT_CHIPS)],
      hidden: [...org.slice(1), ...people.slice(CONTACT_CHIPS)],
    };
  }

  function kindText(key: string): string {
    const def = kindByKey.get(key);
    return def ? kindLabel(def, data.locale) : key;
  }
</script>

<svelte:head>
  <title>{pageTitle(navLabel("interactions", t("interactions.title")))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">
    {navLabel("interactions", t("interactions.title"))}
  </h1>
  <div class="flex flex-wrap items-center gap-2">
    <!-- Whether the feed is up to date, and a way to make it so (#341). Outside `canWrite`:
         scanning your own mailbox writes nothing here that you did not already receive, and the
         person who most needs to know the timeline is stale is the one who cannot add rows by
         hand. It draws itself only when this user's own mailbox is actually syncing. -->
    <GmailRefreshButton status={data.gmailStatus} result={form?.gmailRefresh ?? null} />
    {#if canWrite}
      <!-- An email from outside a connected mailbox is logged from its .eml export (#262). -->
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand hover:text-brand"
        onclick={() => {
          gapThreadId = null;
          showUpload = true;
        }}
      >
        <Mail size={15} aria-hidden="true" />
        {t("interactions.eml.add")}
      </button>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        onclick={() => (showCreate = true)}
      >
        <Plus size={15} aria-hidden="true" />
        {t("interactions.add")}
      </button>
    {/if}
  </div>
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

{#if scopeChips.length > 0}
  <!-- Scoped to one record (#323): its own line above the filters, because it narrows the whole
       page rather than one column of it. The name links back to the record you came from; the ✕
       widens to everything, which is the only way back that is not the browser's Back button. -->
  <div class="mb-3 flex flex-wrap items-center gap-2">
    {#each scopeChips as chip (chip.field)}
      <span
        class="inline-flex items-center gap-1.5 rounded-full bg-surface py-1 pl-3 pr-1 text-sm text-text ring-1 ring-inset ring-border"
      >
        <span class="text-text-muted">{t(recordLabelKey(chip.field))}:</span>
        <a href={recordHref(chip.field, chip.id)} class="font-medium text-text hover:text-brand">
          {chip.label ?? "…"}
        </a>
        <a
          href={clearRecordHref(chip.field)}
          aria-label={t("interactions.filter.clear_record")}
          title={t("interactions.filter.clear_record")}
          class="rounded-full p-1 text-text-muted hover:bg-surface-raised hover:text-text"
        >
          <X size={14} aria-hidden="true" />
        </a>
      </span>
    {/each}
  </div>
{/if}

<div class="mb-3 flex flex-wrap items-center gap-3">
  <!-- The two views, as one segmented control rather than two words. What it replaced was a pair
       of borderless links whose selected half painted itself `bg-surface` — the page's own colour
       — so the primary switch on the busiest screen in the app was invisible until you compared
       font weights. It leads the row because it decides what everything to its right narrows. -->
  <div
    class="inline-flex items-center gap-1 rounded-xl bg-surface-raised p-1 ring-1 ring-inset ring-border"
    data-sveltekit-preload-data="hover"
  >
    <a href={filterHref({ status: "pending" })} class={viewTabClass(reviewing)}>
      <Inbox size={15} aria-hidden="true" />
      {t("interactions.filter.pending")}
      {#if pendingTotal > 0}
        <!-- The number is the whole point of leading with this tab: an empty queue and a
             filtered one look identical without it, and a queue you cannot see the size of is
             one nobody opens. Amber where it is unselected — the same amber the rows wear —
             and carried on the brand where it is, because a brand-on-brand badge disappears. -->
        <span
          class="rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular-nums {reviewing
            ? 'bg-white/25 text-white'
            : 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300'}"
        >
          {pendingTotal}
        </span>
        <span class="sr-only"
          >{t("interactions.filter.pending_count", { count: pendingTotal })}</span
        >
      {/if}
    </a>
    <a href={filterHref({ status: "all" })} class={viewTabClass(!reviewing)}>
      <ListIcon size={15} aria-hidden="true" />
      {t("interactions.filter.all")}
    </a>
  </div>
</div>

<!-- The narrowing controls, in the shared bar (#354): search first, then the pickers, then the
     actions at the far end. The search box used to sit on the *right*, among Kolommen and the ✎,
     which is the one ordering no other list here uses.

     The view switch above stays out of it on purpose. It is not a filter: it decides which of two
     screens this is — a to-do queue or a register — and everything in the bar narrows whichever
     one you are on. -->
{#snippet ownerFilter()}
  <!-- A `MemberPicker` like every other "which colleague" control: a deactivated account is
       behind the search wearing "Gedeactiveerd", never beside the people still here. Filtering
       by somebody who has left is a real question — this is their mail — so they are found by
       typing rather than dropped. The signed-in user is excluded because "mijn" already names
       them. -->
  <div class="w-full sm:w-44">
    <MemberPicker
      members={ownerMembers}
      extra={ownerExtra}
      exclude={me ? [me] : []}
      name="_filter_owner"
      id="filter-owner"
      value={data.filters.ownerValue}
      allowEmpty={false}
      placeholder={t("interactions.filter.owner")}
      ariaLabel={t("interactions.filter.owner")}
      onselect={(v) => applyFilter({ owner: v, mine: null })}
    />
  </div>
{/snippet}

<FilterBar filters={filterDefs} idPrefix="interaction-filter">
  {#snippet actions()}
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
    <!-- Last in the toolbar, always: it is the only control here that changes what the *rows*
         do rather than what the list shows, so it sits after Kolommen rather than among the
         list's own controls. Pressing it opens the selection strip above the table. -->
    <BulkToggle bind:selecting bind:selected={bulkSelected} {...bulkConfig} />
  {/snippet}
</FilterBar>

<!-- Date navigation (#238): jump to a week, filter a month, or type any range — three ways of
     writing the same `from`/`to` params. Wraps on its own line so a phone never scrolls (#36).
     Four controls, and over the review queue they answer a question nobody asks: a queue is
     "what is still waiting", not "what came in last week", and the row is a third of the chrome
     above the one list on this screen that is a to-do list. So it stands down there — **unless a
     range is actually set**, because a link that arrives with `from`/`to` on it must still show
     what is narrowing the list, or the queue reads as short when it is only filtered (the same
     rule `FilterBar` states by opening itself when a filter is already on). -->
{#if !reviewing || dateFrom || dateTo}
  <div class="mb-3 flex flex-wrap items-center gap-2" data-sveltekit-preload-data="hover">
    <!-- The arrows exist only while a week *is* the view (#352). `← label →` is where every
         calendar in the world puts the range you are looking at, so over an unfiltered list the
         middle slot reading "Deze week" claimed a filter that was not on — and `←` from there
         landed on the week before *today*, which is not "the week before what I was looking at".
         With no week set the middle control is one button that turns the filter on, reading as
         what it is; the step arrows come back the moment there is something to step from. -->
    <div class="flex items-center gap-1">
      {#if weekActive}
        <a
          href={weekHref(-1)}
          aria-label={t("interactions.filter.prev_week")}
          class="rounded-lg border border-border px-2 py-1 text-sm text-text hover:bg-surface"
        >
          ←
        </a>
      {/if}
      <a href={weekHref(0)} class={tabClass(weekActive)}>
        {weekActive ? fmtPeriod(dateFrom, dateTo) : t("interactions.filter.this_week")}
      </a>
      {#if weekActive}
        <a
          href={weekHref(1)}
          aria-label={t("interactions.filter.next_week")}
          class="rounded-lg border border-border px-2 py-1 text-sm text-text hover:bg-surface"
        >
          →
        </a>
      {/if}
    </div>
    <select
      value={monthActive}
      onchange={(e) => {
        const month = e.currentTarget.value;
        applyFilter(
          month ? { from: `${month}-01`, to: lastDayOf(month) } : { from: null, to: null },
        );
      }}
      class="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-text"
      aria-label={t("interactions.filter.month")}
    >
      <option value="">{t("interactions.filter.all_months")}</option>
      {#each monthOptions as month (month)}
        <option value={month}>{fmtMonthYear(month)}</option>
      {/each}
    </select>
    <label for="int-date-from" class="sr-only">{t("interactions.filter.date_from")}</label>
    <div class="w-36">
      <DateInput
        name="_f_from"
        id="int-date-from"
        value={dateFrom}
        onchange={(v) => applyFilter({ from: v || null })}
      />
    </div>
    <span class="text-xs text-text-muted">–</span>
    <label for="int-date-to" class="sr-only">{t("interactions.filter.date_to")}</label>
    <div class="w-36">
      <DateInput
        name="_f_to"
        id="int-date-to"
        value={dateTo}
        onchange={(v) => applyFilter({ to: v || null })}
      />
    </div>
    {#if dateFrom || dateTo}
      <a
        href={filterHref({ from: null, to: null })}
        class="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-text-muted hover:text-text"
      >
        <X size={14} aria-hidden="true" />
        {t("interactions.filter.clear_dates")}
      </a>
    {/if}
  </div>
{/if}

{#snippet subjectCell(item: InteractionItem)}
  {@const who = reviewIds(item).length > 1 ? participantNames(item) : null}
  <span class="block min-w-0">
    <span class="flex items-center gap-2">
      <span class="truncate font-medium text-text">
        {item.subject || kindText(item.kind)}
      </span>
      {#if (item.conversation_count ?? 1) > 1}
        <!-- The email folds a conversation (#272): a small message-count badge. -->
        <span
          title={countText(item)}
          class="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted ring-1 ring-inset ring-border"
        >
          <Mail size={10} aria-hidden="true" />
          {item.conversation_count}
          <span class="sr-only">{countText(item)}</span>
        </span>
      {/if}
      {#if item.status === "pending" && !reviewing}
        <!-- Only where it distinguishes something. In the mixed timeline the amber pill is what
             picks an unreviewed row out of forty; on the queue, where every row is pending by
             definition, it is the filter printed forty times — and a badge that never varies is
             read as decoration, which is how the one on a genuinely mixed list loses its force. -->
        <span
          class="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-400"
        >
          {t("interactions.pending")}
        </span>
      {/if}
    </span>
    {#if who?.names.length || item.snippet}
      <!-- A teaser, not the mail (#263): Gmail's snippet arrives HTML-escaped and two hundred
           characters long, so it is decoded and cut at a word boundary before `truncate` ever
           gets to fit it to the column. A folded queue row stands for a conversation, so its
           line says who it is with before what was last said — the newest message's snippet
           alone reads as one message, which is the thing the fold exists to stop. -->
      <span class="mt-0.5 block truncate text-xs text-text-muted">
        {#if who?.names.length}
          <span class="text-text"
            >{who.names.join(", ")}{#if who.more > 0}
              {t("interactions.linked_more", { count: who.more })}{/if}</span
          >{#if item.snippet}&nbsp;·&nbsp;{/if}
        {/if}
        {#if item.snippet}{snippetPreview(item.snippet)}{/if}
      </span>
    {/if}
  </span>
{/snippet}

{#snippet kindCell(item: InteractionItem)}
  {@const Icon = kindIcon(item.kind)}
  <span class="flex items-center gap-1.5 text-text-muted">
    <!-- The icon is a flex item like the label beside it, so it shrinks with it: a long
         tenant-defined kind squeezed the 14px envelope down to four and a half. -->
    <Icon size={14} class="shrink-0" aria-hidden="true" />
    <span class="truncate">{kindText(item.kind)}</span>
  </span>
{/snippet}

{#snippet linkedCell(item: InteractionItem)}
  {@const chips = linkChips(item)}
  <span class="flex min-w-0 flex-nowrap items-center gap-1 overflow-hidden">
    {#each chips.visible as chip (chip.href)}
      <!-- `relative z-10` keeps the chip clickable above the row's stretched link (#59).
           Who the moment was with must not read quieter than its timestamp (#238): the chip
           carries full text colour at `text-xs`, above the muted date beside it. -->
      <a
        href={chip.href}
        title={chip.label}
        class="relative z-10 max-w-full truncate rounded-full bg-surface px-2 py-0.5 text-xs text-text ring-1 ring-inset ring-border hover:text-brand"
      >
        {chip.label}
      </a>
    {/each}
    {#if chips.hidden.length > 0}
      <!-- Not a link: the row click opens the detail modal, which lists every link in full. -->
      <span
        title={chips.hidden.map((chip) => chip.label).join(", ")}
        class="shrink-0 rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted ring-1 ring-inset ring-border"
      >
        {t("interactions.linked_more", { count: chips.hidden.length })}
      </span>
    {/if}
  </span>
{/snippet}

{#snippet ownerCell(item: InteractionItem)}
  <!-- `block`, because `overflow` does not apply to an inline box: a bare `truncate` span only
       gets its `nowrap` half, so a long name runs sideways over the next column under the
       table's fixed layout instead of ellipsizing inside its own. -->
  <span class="block truncate text-text-muted">{item.owner_name ?? "—"}</span>
{/snippet}

{#snippet whenCell(item: InteractionItem)}
  <!-- Quieter than the chips on purpose (#238): a reader scans who first, then when.
       `block truncate` rather than a bare `whitespace-nowrap`: a twelve-hour clock (#13) or a
       wider locale outgrows this column, and nowrap alone spills it over the ⋯ cell. -->
  <span class="block truncate text-xs text-text-muted">{fmtDateTime(item.occurred_at)}</span>
{/snippet}

{#snippet rowActions(item: InteractionItem)}
  <span class="relative z-10 flex items-center justify-end gap-1.5">
    {#if item.status === "pending" && isOwner(item)}
      <!-- Review-and-approve, not a bare approve: open the detail modal so the email can be read
           and a client/project/task assigned before it is shared with the team (#184). -->
      <button
        type="button"
        onclick={() => openDetail(item)}
        class="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:bg-surface"
      >
        {t("interactions.review")}
      </button>
    {/if}
    {#if menuItems(item).length > 0}
      <ActionsMenu compact items={menuItems(item)} />
    {/if}
  </span>
{/snippet}

{#snippet mobileRow(item: InteractionItem)}
  <div class="flex items-start gap-3">
    <span class="min-w-0 flex-1">
      <span class="flex items-center gap-2">
        <span class="truncate text-sm font-medium text-text">
          {item.subject || kindText(item.kind)}
        </span>
        {#if (item.conversation_count ?? 1) > 1}
          <span
            title={countText(item)}
            class="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted ring-1 ring-inset ring-border"
          >
            <Mail size={10} aria-hidden="true" />
            {item.conversation_count}
            <span class="sr-only">{countText(item)}</span>
          </span>
        {/if}
        {#if item.status === "pending" && !reviewing}
          <span
            class="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-400"
          >
            {t("interactions.pending")}
          </span>
        {/if}
      </span>
      <span class="mt-0.5 block text-xs text-text-muted">
        {kindText(item.kind)} · {fmtDateTime(item.occurred_at)}{#if item.owner_name}&nbsp;· {item.owner_name}{/if}
      </span>
    </span>
    {@render rowActions(item)}
  </div>
{/snippet}

{#snippet empty()}
  <!--
    Three empty lists, three different facts, and the old screen had one sentence for all of them.
    That was survivable while the page opened on everything; it is not now that it opens on a
    queue, because the commonest good day at an agency is a queue with nothing in it — and
    "Geen interacties in deze weergave" over an empty review tab reads as a page that failed to
    load, on the screen a user now lands on first.

    So: a filtered list says `common.no_results` (docs/UX.md — an empty list under a filter must
    not send the reader hunting for the wrong problem), an empty queue says it is *done* and
    offers the one place the rest of the moments are, and everything else keeps the old line.
    The way out is a real button, not a sentence mentioning a tab: the whole point of the empty
    state is that the reader is one click from the list they may actually have wanted.
  -->
  {#if data.filters.narrowed}
    <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
      {t("common.no_results")}
    </p>
  {:else if reviewing}
    <div class="rounded-xl border border-border bg-surface-raised p-8 text-center">
      <span
        class="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400"
      >
        <CheckCheck size={22} aria-hidden="true" />
      </span>
      <p class="mt-3 text-sm font-medium text-text">{t("interactions.review_empty")}</p>
      <p class="mx-auto mt-1 max-w-md text-sm text-text-muted">
        {t("interactions.review_empty_hint")}
      </p>
      <a
        href={filterHref({ status: "all" })}
        class="mt-5 inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        <ListIcon size={15} aria-hidden="true" />
        {t("interactions.review_empty_action")}
      </a>
    </div>
  {:else}
    <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
      {t("interactions.list_empty")}
    </p>
  {/if}
{/snippet}

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<!-- The shared banner, reading this module's own verbs: "6 goedgekeurd · 2 overgeslagen" rather
     than the generic "bijgewerkt". Same component every list uses, one namespace over — and
     `done_delete` is the one verb the two namespaces share, so a bulk delete reads correctly
     from here too. -->
<BulkResult result={form?.bulkResult} prefix="interactions.bulk" />

<!-- Approve as matched: the headline case, and a pure status change — every row keeps the
     client/project the Gmail matcher derived for it, so no dialog stands in the way. Hidden and
     fired from the mode's Goedkeuren button, which sits in the toolbar and is not a submit. -->
<form
  method="POST"
  action="?/bulkApproveInteractions"
  class="hidden"
  bind:this={approveForm}
  use:enhance={busy.keep("bulk-approve")}
>
  <input type="hidden" name="ids" value={bulkPendingIds.join(",")} />
</form>

<!-- `actionsWidth`: this list's ⋯ cell also carries a labelled Beoordelen button on a pending row
     of your own, which measures far past the 40px default. Under the fixed layout a column no
     longer widens to fit its content — it paints over the neighbour instead. -->
<DataTable
  rows={items}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  onRowClick={(item) => openDetail(item)}
  actions={rowActions}
  actionsWidth={140}
  {mobileRow}
  {empty}
  {groups}
  groupBy={timelineOrder ? (item) => localDay(item.occurred_at) : undefined}
  {selecting}
  bind:selected={bulkSelected}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>

<Modal bind:open={showCreate} title={t("interactions.add")}>
  <InteractionForm mentions={mentionCandidates} onsaved={() => (showCreate = false)} />
</Modal>

<!-- Upload an exported email (#262): the same inline-create dialogs the manual form uses. -->
<Modal
  bind:open={showUpload}
  title={gapThreadId ? t("interactions.gmail.title") : t("interactions.eml.title")}
>
  {#if showUpload}
    <EmlUploadForm
      threadId={gapThreadId}
      gmailAvailable={data.gmailStatus?.available ?? null}
      onsaved={() => (showUpload = false)}
    />
  {/if}
</Modal>

<Modal bind:open={showEdit} title={t("interactions.edit")}>
  {#if editing}
    {#key editing.id}
      <InteractionForm
        interaction={editing}
        mentions={mentionCandidates}
        onsaved={() => (showEdit = false)}
      />
    {/key}
  {/if}
</Modal>

<Modal
  bind:open={showMove}
  title={moving?.source === "gmail" && moving?.status === "pending"
    ? t("interactions.assign_title")
    : t("interactions.move_title")}
>
  {#if moving}
    {#key moving.id}
      <InteractionMoveDialog
        interaction={moving}
        approveAction="?/approveInteraction"
        onsaved={() => (showMove = false)}
      />
    {/key}
  {/if}
</Modal>

<!-- Glue an unthreaded email onto another conversation by hand (#272). -->
<Modal bind:open={showConversation} title={t("interactions.add_to_conversation_title")}>
  {#if linkingConv}
    {#key linkingConv.id}
      <InteractionConversationDialog
        interaction={linkingConv}
        onsaved={() => (showConversation = false)}
      />
    {/key}
  {/if}
</Modal>

<!-- A folded row is a conversation (#272) and deleting it deletes the thread, so the confirmation
     says how many messages that is. One press, one honest number. -->
<ConfirmDialog
  bind:open={confirmDelete}
  title={t("interactions.delete_title")}
  message={deleteCount > 1
    ? tn("interactions.delete_conversation_message", deleteCount)
    : t("interactions.delete_message")}
  action="?/deleteInteraction"
  fields={{ id: deleteId }}
/>

<Modal bind:open={showReject} title={t("interactions.reject_title")}>
  {#if rejecting}
    <form
      method="POST"
      action="?/rejectInteraction"
      class="space-y-4"
      use:enhance={busy.wrap("reject", () => async ({ update }) => {
        showReject = false;
        await update();
      })}
    >
      <input type="hidden" name="id" value={rejecting.id} />
      <p class="text-sm text-text-muted">{t("interactions.reject_message")}</p>
      <label class="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" name="suppress_thread" value="1" />
        {t("interactions.reject_thread")}
      </label>
      {#if reviewIds(rejecting).length > 1}
        <!-- Ignoring the conversation takes the rest of the queue for it along: said here,
             because a checkbox that quietly does more than its label is a broken control. -->
        <p class="pl-6 text-xs text-text-muted">
          {tn("interactions.reject_thread_pending", reviewIds(rejecting).length - 1)}
        </p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-surface"
          onclick={() => (showReject = false)}
        >
          {t("common.cancel")}
        </button>
        <Button type="submit" variant="danger" loading={busy.is("reject")} disabled={busy.active}>
          {t("interactions.reject")}
        </Button>
      </div>
    </form>
  {/if}
</Modal>

<!-- File a whole selection (#299), optionally approving it in the same step. -->
<Modal bind:open={showBulkAssign} title={t("interactions.bulk.assign_title")}>
  {#key bulkFilableIds.join(",")}
    <InteractionBulkAssignDialog
      ids={bulkFilableIds}
      approvableIds={bulkPendingIds}
      onsaved={() => (showBulkAssign = false)}
    />
  {/key}
</Modal>

<!-- Bulk reject is the one irreversible batch: each row's metadata goes and its message is
     suppressed, so a re-poll never resurrects it. Hence a modal rather than a bar button. -->
<Modal bind:open={showBulkReject} title={t("interactions.bulk.reject_title")}>
  <form
    method="POST"
    action="?/bulkRejectInteractions"
    class="space-y-4"
    use:enhance={busy.wrap("bulk-reject", () => async ({ update }) => {
      showBulkReject = false;
      await update();
    })}
  >
    <input type="hidden" name="ids" value={bulkPendingIds.join(",")} />
    <p class="text-sm text-text-muted">
      {t("interactions.bulk.reject_message", { count: bulkPendingIds.length })}
    </p>
    <label class="flex items-center gap-2 text-sm text-text">
      <input type="checkbox" name="suppress_thread" value="1" />
      {t("interactions.reject_thread")}
    </label>
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:bg-surface"
        onclick={() => (showBulkReject = false)}
      >
        {t("common.cancel")}
      </button>
      <Button
        type="submit"
        variant="danger"
        loading={busy.is("bulk-reject")}
        disabled={busy.active}
      >
        {t("interactions.reject")}
      </Button>
    </div>
  </form>
</Modal>

<!-- The full contact moment (#184): the same detail modal the per-record panels use — the email
     reads with its line breaks and no sideways scroll, and a pending gmail row is assigned +
     approved (or rejected) here instead of a bare one-click approve. -->
<InteractionDetailModal bind:open={showDetail} item={detailItem} />
