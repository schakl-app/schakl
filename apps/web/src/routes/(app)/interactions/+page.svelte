<script lang="ts">
  /**
   * Interacties (#168): the full, searchable list of contactmomenten in the shared
   * `DataTable` — the narrow pending-email queue grew into this page, and the review flow
   * (approve / reject / move) is now just its `?status=pending` filter state. Row actions
   * reuse the exact dialogs the per-record panels use; day sections follow the API's one
   * order (the timeline), so sections and sort can never disagree.
   */
  import { ArrowRightLeft, Pencil, Plus, Trash2, X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { INTERACTION_COLUMNS } from "$lib/modules/interactions/columns";
  import {
    dayLabel,
    type InteractionItem,
    type InteractionKindDef,
    kindIcon,
    kindLabel,
    localDay,
  } from "$lib/modules/interactions/format";
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

  const groups = $derived.by(() => {
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
  let showMove = $state(false);
  let moving = $state<InteractionItem | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  let showReject = $state(false);
  let rejecting = $state<InteractionItem | null>(null);

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

  /** The record this row lives on — where clicking it takes you. */
  function rowHref(item: InteractionItem): string {
    if (item.company_id) return `/companies/${item.company_id}`;
    if (item.project_id) return `/projects/${item.project_id}`;
    if (item.task_id) return `/tasks/${item.task_id}`;
    if (item.contact_id) return `/contacts/${item.contact_id}`;
    return "";
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
  <title>{pageTitle(t("interactions.title"))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{t("interactions.title")}</h1>
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
      <!-- `relative z-10` keeps the chip clickable above the row's stretched link (#59). -->
      <a
        href={chip.href}
        class="relative z-10 rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted ring-1 ring-inset ring-border hover:text-brand"
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
  <span class="whitespace-nowrap text-text-muted">{fmtDateTime(item.occurred_at)}</span>
{/snippet}

{#snippet rowActions(item: InteractionItem)}
  <span class="relative z-10 flex items-center justify-end gap-1.5">
    {#if item.status === "pending" && isOwner(item)}
      <form method="POST" action="?/approveInteraction" use:enhance>
        <input type="hidden" name="id" value={item.id} />
        <button
          type="submit"
          class="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:bg-surface"
        >
          {t("interactions.approve")}
        </button>
      </form>
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
  rowHref={(item) => rowHref(item)}
  actions={rowActions}
  {mobileRow}
  {empty}
  {groups}
  groupBy={(item) => localDay(item.occurred_at)}
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
  <InteractionForm mentions={mentionCandidates} onsaved={() => (showCreate = false)} />
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
      use:enhance={() =>
        async ({ update }) => {
          showReject = false;
          await update();
        }}
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
        <button
          type="submit"
          class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("interactions.reject")}
        </button>
      </div>
    </form>
  {/if}
</Modal>
