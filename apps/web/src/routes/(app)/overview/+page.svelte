<script lang="ts">
  import { Check, Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { TIME_REPORT_COLUMNS } from "$lib/modules/time/columns";
  import EntryForm from "$lib/modules/time/EntryForm.svelte";
  import EntryStatusPill from "$lib/modules/time/EntryStatusPill.svelte";
  import TimeEntryRow from "$lib/modules/time/TimeEntryRow.svelte";
  import { formatMinutes, formatTime } from "$lib/modules/time/format";

  let { data, form } = $props();

  const report = $derived(data.report);
  const entries = $derived(report?.items ?? []);
  const totals = $derived(report?.totals ?? null);

  // Edit / delete a single entry straight from the report (UX: record actions live in ⋯).
  type Entry = (typeof entries)[number];
  let editingEntry = $state<Entry | null>(null);
  let showEdit = $state(false);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  function openEdit(e: Entry) {
    editingEntry = e;
    showEdit = true;
  }

  const memberName = (id?: string | null) => {
    const m = data.members.find((mm) => mm.user_id === id);
    return m ? m.full_name || m.email : "";
  };
  const companyName = (id?: string | null) => data.companies.find((c) => c.id === id)?.name ?? "";
  const projectName = (id?: string | null) => data.projects.find((p) => p.id === id)?.name ?? "";
  const taskTitle = (id?: string | null) => data.tasks.find((tk) => tk.id === id)?.title ?? "";
  function entryLabel(e: {
    company_id?: string | null;
    project_id?: string | null;
    task_id?: string | null;
  }) {
    const parts = [
      companyName(e.company_id),
      projectName(e.project_id),
      taskTitle(e.task_id),
    ].filter(Boolean);
    return parts.length ? parts.join(" · ") : t("time.general");
  }

  // --- the table ---------------------------------------------------------------
  const table = createTableLayout<Entry>({
    all: () => TIME_REPORT_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      date: dateCell,
      employee: employeeCell,
      company: companyCell,
      project: projectCell,
      task: taskCell,
      description: descriptionCell,
      minutes: minutesCell,
      billable: billableCell,
      status: statusCell,
      approver: approverCell,
      invoiced_at: invoicedAtCell,
    }),
    // The footer's figures are the API's, over the whole filtered set. Summing `entries` would
    // silently print the total *of the page*, which looks exactly like the right answer (#37).
    totals: () => ({
      minutes: minutesTotal,
      billable: billableTotal,
      status: statusTotal,
    }),
  });

  /** Bound to the table's checkbox column. Reset by `DataTable` whenever the row set changes. */
  let selected = $state<string[]>([]);

  // --- filters (query params → SSR reload) ------------------------------------
  const statuses = ["open", "approved", "to_invoice", "invoiced"] as const;
  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  const bulkClass =
    "rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-text-muted hover:border-brand hover:text-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("time.overview.title"))}</title>
</svelte:head>

<div class="mb-4">
  <h1 class="text-xl font-semibold text-text">{t("time.overview.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("time.overview.subtitle")}</p>
</div>

<!-- Filters -->
<div class="mb-4 flex flex-wrap items-center gap-2">
  <div class="w-44">
    <Combobox
      items={data.members.map((m) => ({ value: m.user_id, label: m.full_name || m.email }))}
      name="_f_user"
      id="f-user"
      value={data.filters.user_id}
      placeholder={t("time.overview.employee")}
      onselect={(v) => setFilter("user_id", v)}
    />
  </div>
  <div class="w-44">
    <Combobox
      items={data.companies.map((c) => ({ value: c.id, label: c.name }))}
      name="_f_company"
      id="f-company"
      value={data.filters.company_id}
      placeholder={t("time.field.company")}
      onselect={(v) => setFilter("company_id", v)}
    />
  </div>
  <div class="w-44">
    <Combobox
      items={data.projects.map((p) => ({ value: p.id, label: p.name }))}
      name="_f_project"
      id="f-project"
      value={data.filters.project_id}
      placeholder={t("time.field.project")}
      onselect={(v) => setFilter("project_id", v)}
    />
  </div>
  <div class="w-36">
    <DateInput
      name="_f_from"
      id="f-from"
      value={data.filters.date_from}
      onchange={(v) => setFilter("date_from", v)}
    />
  </div>
  <span class="text-xs text-text-muted">–</span>
  <div class="w-36">
    <DateInput
      name="_f_to"
      id="f-to"
      value={data.filters.date_to}
      onchange={(v) => setFilter("date_to", v)}
    />
  </div>
  {#each statuses as status (status)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.filters.status === status
        ? 'bg-brand text-white'
        : 'border border-border text-text-muted hover:border-brand hover:text-brand'}"
      onclick={() => setFilter("status", data.filters.status === status ? "" : status)}
      >{t(`time.overview.status.${status}`)}</button
    >
  {/each}
  <div class="ml-auto">
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
  </div>
</div>

{#if form?.error}
  <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<!-- Cells ------------------------------------------------------------------- -->
{#snippet dateCell(e: Entry)}
  <span class="whitespace-nowrap tabular-nums text-text">
    {fmtNumericDate(e.started_at.slice(0, 10))}
    <span class="text-xs text-text-muted">{formatTime(e.started_at)}</span>
  </span>
{/snippet}

{#snippet employeeCell(e: Entry)}
  <span class="whitespace-nowrap font-medium text-text">{memberName(e.user_id) || "—"}</span>
{/snippet}

{#snippet companyCell(e: Entry)}
  {@const name = companyName(e.company_id)}
  {#if name}
    <a href="/companies/{e.company_id}" class="truncate text-text hover:text-brand">{name}</a>
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet projectCell(e: Entry)}
  {@const name = projectName(e.project_id)}
  {#if name}
    <a href="/projects/{e.project_id}" class="truncate text-text hover:text-brand">{name}</a>
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet taskCell(e: Entry)}
  {@const title = taskTitle(e.task_id)}
  {#if title}
    <a href="/tasks/{e.task_id}" class="truncate text-text hover:text-brand">{title}</a>
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet descriptionCell(e: Entry)}
  <span class="block truncate text-text-muted">{e.description || "—"}</span>
{/snippet}

{#snippet minutesCell(e: Entry)}
  <span class="font-semibold text-text">{formatMinutes(e.minutes)}</span>
{/snippet}

{#snippet billableCell(e: Entry)}
  {#if e.billable}
    <span class="inline-flex text-green-600 dark:text-green-400" title={t("time.billable")}>
      <Check size={16} />
      <span class="sr-only">{t("time.billable")}</span>
    </span>
  {:else}
    <span class="text-text-muted" title={t("time.not_billable")}>
      —<span class="sr-only">{t("time.not_billable")}</span>
    </span>
  {/if}
{/snippet}

{#snippet statusCell(e: Entry)}
  <EntryStatusPill entry={e} />
{/snippet}

{#snippet approverCell(e: Entry)}
  <span class="truncate text-text-muted">{memberName(e.approved_by_user_id) || "—"}</span>
{/snippet}

{#snippet invoicedAtCell(e: Entry)}
  <span class="tabular-nums text-text-muted">
    {e.invoiced_at ? fmtNumericDate(e.invoiced_at.slice(0, 10)) : "—"}
  </span>
{/snippet}

<!-- Footer: every figure the totals cards used to carry, now aligned under the column it
     describes. All seven come from the API's `totals`, over the whole filtered set. -->
{#snippet minutesTotal()}
  <span class="text-text">{formatMinutes(totals?.minutes ?? 0)}</span>
{/snippet}

{#snippet billableTotal()}
  <span class="text-text">{formatMinutes(totals?.billable_minutes ?? 0)}</span>
{/snippet}

{#snippet statusTotal()}
  <span class="flex flex-col gap-0.5 text-xs font-normal">
    <span class={totals?.open_minutes ? "text-amber-600 dark:text-amber-400" : "text-text-muted"}>
      {t("time.overview.total.open")}: {formatMinutes(totals?.open_minutes ?? 0)}
    </span>
    <span class={totals?.to_invoice_minutes ? "text-brand" : "text-text-muted"}>
      {t("time.overview.total.to_invoice")}: {formatMinutes(totals?.to_invoice_minutes ?? 0)}
    </span>
    <span
      class={totals?.invoiced_minutes ? "text-green-600 dark:text-green-400" : "text-text-muted"}
    >
      {t("time.overview.total.invoiced")}: {formatMinutes(totals?.invoiced_minutes ?? 0)}
    </span>
  </span>
{/snippet}

<!-- A list of records is never read-only because it is a "report" (docs/UX.md). -->
{#snippet rowActions(e: Entry)}
  <ActionsMenu
    compact
    items={[
      { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(e) },
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => {
          deleteId = e.id;
          confirmDelete = true;
        },
      },
    ]}
  />
{/snippet}

{#snippet mobileRow(e: Entry)}
  <div class="flex items-center gap-2">
    <div class="min-w-0 flex-1">
      <TimeEntryRow entry={e} label={entryLabel(e)} employee={memberName(e.user_id)} />
    </div>
    {@render rowActions(e)}
  </div>
{/snippet}

<!-- Bulk bar. Selection is per page, and says so: "select all" can only ever mean the rows that
     were fetched, and a bulk approve must not reach records the user never saw. -->
{#snippet selection(ids: string[])}
  <div
    class="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-brand/30 bg-brand/5 px-3 py-2"
  >
    <span class="text-xs font-medium text-text">{t("table.selected", { count: ids.length })}</span>
    <span class="text-xs text-text-muted">{t("table.selection_page_only")}</span>
    {#each [{ action: "approve", label: t("time.overview.approve") }, { action: "unapprove", label: t("time.overview.unapprove") }, { action: "invoice", label: t("time.overview.mark_invoiced") }, { action: "uninvoice", label: t("time.overview.unmark_invoiced") }] as bulkAction (bulkAction.action)}
      <form method="POST" action={`?/${bulkAction.action}`} use:enhance>
        <input type="hidden" name="entry_ids" value={ids.join(",")} />
        <button class={bulkClass}>{bulkAction.label}</button>
      </form>
    {/each}
  </div>
{/snippet}

{#snippet empty()}
  <p
    class="rounded-xl border border-border bg-surface-raised p-8 text-center text-sm text-text-muted"
  >
    {t("time.overview.empty")}
  </p>
{/snippet}

<DataTable
  rows={entries}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  selectable
  bind:selected
  {selection}
  actions={rowActions}
  {mobileRow}
  {empty}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<!-- Edit a single entry (UX §3: definition edits behind the ⋯ menu, in a modal). -->
<Modal bind:open={showEdit} title={t("time.edit_entry")}>
  {#if editingEntry}
    {#key editingEntry.id}
      <EntryForm
        action="?/updateEntry"
        deleteAction="?/deleteEntry"
        entry={editingEntry}
        date={editingEntry.started_at.slice(0, 10)}
        companies={data.companies}
        projects={data.projects}
        tasks={data.tasks}
        error={form?.error ?? null}
        oncancel={() => (showEdit = false)}
        ondone={() => (showEdit = false)}
      />
    {/key}
  {/if}
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("time.delete")}
  message={t("time.delete_confirm")}
  action="?/deleteEntry"
  fields={{ id: deleteId }}
/>
