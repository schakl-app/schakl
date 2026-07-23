<script lang="ts">
  /**
   * Interacties (#168): the full, searchable list of contactmomenten in the shared
   * `DataTable` — the narrow pending-email queue grew into this page, and the review flow
   * (approve / reject / move) is now just its `?status=pending` filter state. Row actions
   * reuse the exact dialogs the per-record panels use. Columns sort server-side like every
   * other list (#238); the day sections only render while the order is the timeline, so
   * sections and sort can never disagree.
   */
  import { ArrowRightLeft, Pencil, Plus, Trash2, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { addMonths, isoAddDays, mondayOnOrBefore, monthOf } from "$lib/core/calendar";
  import { fmtDateTime, fmtMonthYear, fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import { INTERACTION_COLUMNS } from "$lib/modules/interactions/columns";
  import {
    dayLabel,
    type InteractionItem,
    type InteractionKindDef,
    kindIcon,
    kindLabel,
    localDay,
  } from "$lib/modules/interactions/format";
  import InteractionDetailModal from "$lib/modules/interactions/InteractionDetailModal.svelte";
  import InteractionForm from "$lib/modules/interactions/InteractionForm.svelte";
  import InteractionMoveDialog from "$lib/modules/interactions/InteractionMoveDialog.svelte";

  let { data, form } = $props();

  const items = $derived(data.items as InteractionItem[]);
  const kinds = $derived(data.kinds as InteractionKindDef[]);
  const kindByKey = $derived(new Map(kinds.map((k) => [k.key, k])));
  const mentionCandidates = $derived(
    data.members.map((m: { user_id: string; full_name: string | null; email: string }) => ({
      id: m.user_id,
      name: m.full_name || m.email,
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
    url.searchParams.delete("offset");
    return url.pathname + url.search;
  }
  function pageHref(offset: number): string {
    const url = new URL(page.url);
    if (offset <= 0) url.searchParams.delete("offset");
    else url.searchParams.set("offset", String(offset));
    return url.pathname + url.search;
  }
  function applyFilter(patch: Record<string, string | null>): void {
    void goto(filterHref(patch), { keepFocus: true, noScroll: true });
  }
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm ${
      active ? "bg-surface font-medium text-text" : "text-text-muted hover:text-text"
    }`;

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
  const mayEdit = (item: InteractionItem) =>
    item.source === "manual" &&
    (isOwner(item)
      ? can(page.data.user, "interactions.interaction.write", "own")
      : can(page.data.user, "interactions.interaction.write", "any"));
  const mayMove = (item: InteractionItem) =>
    item.source === "gmail" ? isOwner(item) : mayEdit(item);

  let showCreate = $state(false);
  let showEdit = $state(false);
  let editing = $state<InteractionItem | null>(null);
  const busy = new InFlight();

  // Inline company / project create from the form's pickers (#115, docs/UX.md — per-picker
  // definition of done). The form passes what was typed out; the dialogs live here and answer
  // through `inlineCreated` (slots interaction_company / interaction_project) so the form
  // auto-selects the new row.
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  let qcProjectOpen = $state(false);
  let qcProjectName = $state("");
  // The project dialog's own client picker: fetched on first open, never on page load
  // (docs/PERFORMANCE.md — a rarely opened dialog must not tax every load).
  let qcCompanyItems = $state<{ value: string; label: string }[]>([]);
  let qcCompaniesLoaded = false;
  async function openProjectQuickCreate(name: string) {
    qcProjectName = name;
    qcProjectOpen = true;
    if (qcCompaniesLoaded) return;
    qcCompaniesLoaded = true;
    const response = await fetch("/api/v1/companies?limit=200&count=false", {
      headers: { accept: "application/json" },
    });
    const items: { id: string; name: string }[] = response.ok
      ? ((await response.json()).items ?? [])
      : [];
    qcCompanyItems = items.map((c) => ({ value: c.id, label: c.name }));
  }
  let showMove = $state(false);
  let moving = $state<InteractionItem | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  let showReject = $state(false);
  let rejecting = $state<InteractionItem | null>(null);

  // Clicking a row opens the shared detail modal (#184): the email reads with its line breaks,
  // no sideways scroll, and a pending gmail row is assigned + approved (or rejected) in place —
  // the exact review flow the per-record panels use, now on the standalone list too.
  let showDetail = $state(false);
  let detailItem = $state<InteractionItem | null>(null);
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
        onclick: () => {
          editing = item;
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

  function linkChips(item: InteractionItem): { href: string; label: string }[] {
    const chips: { href: string; label: string }[] = [];
    if (item.company_id && item.company_name)
      chips.push({ href: `/companies/${item.company_id}`, label: item.company_name });
    if (item.project_id && item.project_name)
      chips.push({ href: `/projects/${item.project_id}`, label: item.project_name });
    if (item.task_id && item.task_title)
      chips.push({ href: `/tasks/${item.task_id}`, label: item.task_title });
    if (item.contact_id && item.contact_name)
      chips.push({ href: `/contacts/${item.contact_id}`, label: item.contact_name });
    return chips;
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

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
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
  {#if data.canReadAll}
    <!-- Widening past yourself is the read_all grant (#168); the API enforces it harder. -->
    <select
      value={data.filters.owner ?? ""}
      onchange={(e) => applyFilter({ owner: e.currentTarget.value || null, mine: null })}
      class="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-text"
      aria-label={t("interactions.filter.owner")}
    >
      <option value="">{t("interactions.filter.everyone")}</option>
      {#each data.members as member (member.user_id)}
        <option value={member.user_id}>{member.full_name || member.email}</option>
      {/each}
    </select>
  {/if}
  <div class="ml-auto flex items-center gap-2">
    <SearchInput placeholder={t("interactions.search")} />
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
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
      {#if item.status === "pending"}
        <span
          class="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-400"
        >
          {t("interactions.pending")}
        </span>
      {/if}
    </span>
    {#if item.snippet}
      <span class="mt-0.5 block truncate text-xs text-text-muted">{item.snippet}</span>
    {/if}
  </span>
{/snippet}

{#snippet kindCell(item: InteractionItem)}
  {@const Icon = kindIcon(item.kind)}
  <span class="flex items-center gap-1.5 text-text-muted">
    <Icon size={14} aria-hidden="true" />
    <span class="truncate">{kindText(item.kind)}</span>
  </span>
{/snippet}

{#snippet linkedCell(item: InteractionItem)}
  <span class="flex flex-wrap gap-1">
    {#each linkChips(item) as chip (chip.href)}
      <!-- `relative z-10` keeps the chip clickable above the row's stretched link (#59).
           Who the moment was with must not read quieter than its timestamp (#238): the chip
           carries full text colour at `text-xs`, above the muted date beside it. -->
      <a
        href={chip.href}
        class="relative z-10 rounded-full bg-surface px-2 py-0.5 text-xs text-text ring-1 ring-inset ring-border hover:text-brand"
      >
        {chip.label}
      </a>
    {/each}
  </span>
{/snippet}

{#snippet ownerCell(item: InteractionItem)}
  <span class="truncate text-text-muted">{item.owner_name ?? "—"}</span>
{/snippet}

{#snippet whenCell(item: InteractionItem)}
  <!-- Quieter than the chips on purpose (#238): a reader scans who first, then when. -->
  <span class="whitespace-nowrap text-xs text-text-muted">{fmtDateTime(item.occurred_at)}</span>
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

<DataTable
  rows={items}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  onRowClick={(item) => openDetail(item)}
  actions={rowActions}
  {mobileRow}
  {empty}
  {groups}
  groupBy={timelineOrder ? (item) => localDay(item.occurred_at) : undefined}
  onsort={table.onSort}
  onresize={table.onResize}
/>

{#if data.total > data.limit}
  <div class="mt-4 flex items-center justify-between text-sm" data-sveltekit-preload-data="hover">
    <span class="text-text-muted">
      {t("interactions.page_of", {
        from: data.offset + 1,
        to: Math.min(data.offset + data.limit, data.total),
        total: data.total,
      })}
    </span>
    <div class="flex gap-2">
      {#if data.offset > 0}
        <a
          href={pageHref(data.offset - data.limit)}
          class="rounded-lg border border-border px-3 py-1.5 text-text hover:border-brand hover:text-brand"
        >
          {t("common.previous")}
        </a>
      {/if}
      {#if data.offset + data.limit < data.total}
        <a
          href={pageHref(data.offset + data.limit)}
          class="rounded-lg border border-border px-3 py-1.5 text-text hover:border-brand hover:text-brand"
        >
          {t("common.next")}
        </a>
      {/if}
    </div>
  </div>
{/if}

<Modal bind:open={showCreate} title={t("interactions.add")}>
  <InteractionForm
    mentions={mentionCandidates}
    onsaved={() => (showCreate = false)}
    oncreatecompany={(name) => {
      qcCompanyName = name;
      qcCompanyOpen = true;
    }}
    oncreateproject={(name) => void openProjectQuickCreate(name)}
  />
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

<!-- The full contact moment (#184): the same detail modal the per-record panels use — the email
     reads with its line breaks and no sideways scroll, and a pending gmail row is assigned +
     approved (or rejected) here instead of a bare one-click approve. -->
<InteractionDetailModal bind:open={showDetail} item={detailItem} />

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  definitions={data.companyDefinitions}
  locale={data.locale}
  pickerSlot="interaction_company"
  error={form?.qcError ?? null}
/>

<!-- Inline project create from the form's picker (docs/UX.md — per-picker definition of done). -->
<Modal bind:open={qcProjectOpen} title={t("time.quick_create.project")}>
  {#key qcProjectName + String(qcProjectOpen)}
    <form
      method="POST"
      action="?/createProject"
      use:enhance={busy.wrap("create-project", () => ({ result, update }) => {
        if (result.type === "success") qcProjectOpen = false;
        void update({ reset: false });
      })}
      class="space-y-3"
    >
      <div>
        <label for="qc-int-project-name" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.name")}</label
        >
        <input
          id="qc-int-project-name"
          name="name"
          value={qcProjectName}
          required
          class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="qc-int-project-company" class="mb-1 block text-sm font-medium text-text"
          >{t("projects.field.company")}</label
        >
        <Combobox
          items={qcCompanyItems}
          name="company_id"
          id="qc-int-project-company"
          placeholder={t("projects.field.company")}
        />
      </div>
      {#if form?.qcError}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.qcError)}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (qcProjectOpen = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.is("create-project")} disabled={busy.active}>
          {t("common.create")}
        </Button>
      </div>
    </form>
  {/key}
</Modal>
