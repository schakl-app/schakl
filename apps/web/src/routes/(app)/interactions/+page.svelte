<script lang="ts">
  /**
   * Interacties (#168): the full, searchable list of contactmomenten in the shared
   * `DataTable` — the narrow pending-email queue grew into this page, and the review flow
   * (approve / reject / move) is now just its `?status=pending` filter state. Row actions
   * reuse the exact dialogs the per-record panels use. Columns sort server-side like every
   * other list (#238); the day sections only render while the order is the timeline, so
   * sections and sort can never disagree.
   */
  import { ArrowRightLeft, Check, Link2, Mail, Pencil, Plus, Trash2, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import { addMonths, isoAddDays, mondayOnOrBefore, monthOf } from "$lib/core/calendar";
  import { fmtDateTime, fmtMonthYear, fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
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
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm ${
      active ? "bg-surface font-medium text-text" : "text-text-muted hover:text-text"
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
  const bulkFilableIds = $derived(
    selectedItems.filter((item) => isGmailRow(item) && isOwner(item)).map((item) => item.id),
  );
  const bulkPendingIds = $derived(
    selectedItems
      .filter((item) => isGmailRow(item) && isOwner(item) && item.status === "pending")
      .map((item) => item.id),
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
    // refuses per row what it always refuses — a row still in review, or someone else's.
    deletePermission: "interactions.interaction.delete",
    deleteMessage: t("interactions.bulk.delete_message", { count: bulkSelected.length }),
  });

  let showCreate = $state(false);
  let showUpload = $state(false);
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
  let confirmDelete = $state(false);
  let showReject = $state(false);
  let rejecting = $state<InteractionItem | null>(null);

  // Clicking a row opens the shared detail modal (#184): the email reads with its line breaks,
  // no sideways scroll, and a pending gmail row is assigned + approved (or rejected) in place —
  // the exact review flow the per-record panels use, now on the standalone list too.
  // Deep link from the dashboard tile (issue #15) and anything else naming one moment:
  // `?interaction=<id>` opens that row's detail modal on arrival, the same shape the leave
  // calendar's `?request=` uses. A `$state` initializer rather than a `$derived`, so closing the
  // modal does not reopen it while the param is still in the URL; an id the current page does
  // not hold simply lands on the list.
  const deepLinked = () =>
    items.find((item) => item.id === page.url.searchParams.get("interaction")) ?? null;
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
    if (mayEdit(item)) {
      entries.push({
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = item.id;
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
  {#if canWrite}
    <div class="flex flex-wrap items-center gap-2">
      <!-- An email from outside a connected mailbox is logged from its .eml export (#262). -->
      <button
        type="button"
        class="inline-flex items-center gap-1.5 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand hover:text-brand"
        onclick={() => (showUpload = true)}
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
    </div>
  {/if}
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
  <div class="flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    <a href={filterHref({ status: null })} class={tabClass(!data.filters.pending)}>
      {t("interactions.filter.all")}
    </a>
    <a href={filterHref({ status: "pending" })} class={tabClass(data.filters.pending)}>
      {t("interactions.filter.pending")}
    </a>
  </div>
  <select
    value={data.filters.kind ?? ""}
    onchange={(e) => applyFilter({ kind: e.currentTarget.value || null })}
    class="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-text"
    aria-label={t("interactions.column.kind")}
  >
    <option value="">{t("interactions.filter.all_kinds")}</option>
    {#each kinds as kind (kind.key)}
      <option value={kind.key}>{kindLabel(kind, data.locale)}</option>
    {/each}
  </select>
  <!-- You land on your own moments (#263) and widen from there. Narrowing to yourself is
       nobody's grant; naming a *colleague* is the read_all one (#168), so only that option
       list is gated — the API enforces it harder either way.
       Every choice is written out, "mijn" included: a record-scoped view defaults to iedereen
       (#323), so deleting the param to mean "me" would have made that option do nothing. -->
  <select
    value={data.filters.ownerValue}
    onchange={(e) => applyFilter({ owner: e.currentTarget.value, mine: null })}
    class="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-text"
    aria-label={t("interactions.filter.owner")}
  >
    <option value="me">{t("interactions.filter.mine")}</option>
    <option value="all">{t("interactions.filter.everyone")}</option>
    {#if data.canReadAll}
      {#each data.members as member (member.user_id)}
        {#if member.user_id !== me}
          <option value={member.user_id}>{memberLabel(member)}</option>
        {/if}
      {/each}
    {/if}
  </select>
  <!-- `flex-wrap`: three controls on one unwrappable line pushed Kolommen off the right edge of
       a phone and scrolled the whole page sideways (docs/UX.md — a toolbar that cannot wrap). -->
  <div class="ml-auto flex flex-wrap items-center gap-2">
    <SearchInput placeholder={t("interactions.search")} />
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
  </div>
</div>

<!-- Date navigation (#238): jump to a week, filter a month, or type any range — three ways of
     writing the same `from`/`to` params. Wraps on its own line so a phone never scrolls (#36). -->
<div class="mb-3 flex flex-wrap items-center gap-2" data-sveltekit-preload-data="hover">
  <div class="flex items-center gap-1">
    <a
      href={weekHref(-1)}
      aria-label={t("interactions.filter.prev_week")}
      class="rounded-lg border border-border px-2 py-1 text-sm text-text hover:bg-surface"
    >
      ←
    </a>
    <a href={weekHref(0)} class={tabClass(weekActive)}>
      {weekActive ? fmtPeriod(dateFrom, dateTo) : t("interactions.filter.this_week")}
    </a>
    <a
      href={weekHref(1)}
      aria-label={t("interactions.filter.next_week")}
      class="rounded-lg border border-border px-2 py-1 text-sm text-text hover:bg-surface"
    >
      →
    </a>
  </div>
  <select
    value={monthActive}
    onchange={(e) => {
      const month = e.currentTarget.value;
      applyFilter(month ? { from: `${month}-01`, to: lastDayOf(month) } : { from: null, to: null });
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

{#snippet subjectCell(item: InteractionItem)}
  <span class="block min-w-0">
    <span class="flex items-center gap-2">
      <span class="truncate font-medium text-text">
        {item.subject || kindText(item.kind)}
      </span>
      {#if (item.conversation_count ?? 1) > 1}
        <!-- The email folds a conversation (#272): a small message-count badge. -->
        <span
          title={t("interactions.conversation_count", { count: item.conversation_count })}
          class="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted ring-1 ring-inset ring-border"
        >
          <Mail size={10} aria-hidden="true" />
          {item.conversation_count}
          <span class="sr-only"
            >{t("interactions.conversation_count", { count: item.conversation_count })}</span
          >
        </span>
      {/if}
      {#if item.status === "pending"}
        <span
          class="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-400"
        >
          {t("interactions.pending")}
        </span>
      {/if}
    </span>
    {#if item.snippet}
      <!-- A teaser, not the mail (#263): Gmail's snippet arrives HTML-escaped and two hundred
           characters long, so it is decoded and cut at a word boundary before `truncate` ever
           gets to fit it to the column. -->
      <span class="mt-0.5 block truncate text-xs text-text-muted">
        {snippetPreview(item.snippet)}
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
            title={t("interactions.conversation_count", { count: item.conversation_count })}
            class="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted ring-1 ring-inset ring-border"
          >
            <Mail size={10} aria-hidden="true" />
            {item.conversation_count}
            <span class="sr-only"
              >{t("interactions.conversation_count", { count: item.conversation_count })}</span
            >
          </span>
        {/if}
        {#if item.status === "pending"}
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
  <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
    {t("interactions.list_empty")}
  </p>
{/snippet}

<BulkBar {selecting} selected={bulkSelected} {...bulkConfig} />

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
  selectable={selecting}
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
<Modal bind:open={showUpload} title={t("interactions.eml.title")}>
  {#if showUpload}
    <EmlUploadForm onsaved={() => (showUpload = false)} />
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

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("interactions.delete_title")}
  message={t("interactions.delete_message")}
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
