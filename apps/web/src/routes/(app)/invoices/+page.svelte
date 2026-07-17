<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { INVOICE_COLUMNS } from "$lib/modules/invoicing/columns";
  import DocTabs from "$lib/modules/invoicing/DocTabs.svelte";
  import { docMoney } from "$lib/modules/invoicing/types";

  let { data, form } = $props();

  type Invoice = (typeof data.invoices)[number];

  const STATUSES = ["draft", "open", "paid", "cancelled"] as const;
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Invoice unbilled hours (owner request): pick the client, see what's open, draft it.
  let fromTimeOpen = $state(false);
  let ftCompany = $state("");
  let ftUntil = $state("");
  let ftPreview = $state<{ total_minutes: number; entries: unknown[] } | null>(null);
  let ftLoading = $state(false);
  async function loadUnbilled() {
    ftPreview = null;
    if (!ftCompany) return;
    ftLoading = true;
    try {
      const params = new URLSearchParams({ company_id: ftCompany });
      if (ftUntil) params.set("until", ftUntil);
      const res = await fetch(`/invoices/unbilled?${params}`);
      if (res.ok) ftPreview = await res.json();
    } finally {
      ftLoading = false;
    }
  }
  $effect(() => {
    void ftCompany;
    void ftUntil;
    if (fromTimeOpen) void loadUnbilled();
  });
  const ftHours = $derived(ftPreview ? (ftPreview.total_minutes / 60).toFixed(1) : "0");

  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));
  const money = (value: string | number | null | undefined) =>
    value == null ? "—" : fmtMoney(Number(value));

  const table = createTableLayout<Invoice>({
    all: () => INVOICE_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      number: numberCell,
      company: companyCell,
      issue_date: issueCell,
      due_date: dueCell,
      status: statusCell,
      total: totalCell,
      outstanding: outstandingCell,
      reference: referenceCell,
      reminders: remindersCell,
    }),
  });
</script>

<svelte:head>
  <title>{pageTitle(t("invoicing.invoices"))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{t("invoicing.title")}</h1>
  {#if data.canWrite}
    <div class="flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand"
        onclick={() => (fromTimeOpen = true)}
      >
        {t("invoicing.from_time.button")}
      </button>
      <a
        href="/invoices/new"
        data-sveltekit-preload-data="hover"
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >{t("invoicing.new_invoice")}</a
      >
    </div>
  {/if}
</div>

<DocTabs showQuotes={data.canQuotes} />

<!-- Every number opens (UX §7): the tiles filter the list below to exactly what they count. -->
{#if data.summary}
  <div class="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
    <button
      class="rounded-xl border border-border bg-surface-raised p-4 text-left hover:border-brand"
      onclick={() => setFilter("status", "open")}
    >
      <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("invoicing.summary.open")}
      </p>
      <p class="mt-1 text-2xl font-semibold text-text">{money(data.summary.open_total)}</p>
      <p class="text-xs text-text-muted">{data.summary.open_count}</p>
    </button>
    <button
      class="rounded-xl border border-border bg-surface-raised p-4 text-left hover:border-red-400"
      onclick={() => setFilter("overdue", data.overdueFilter ? "" : "1")}
    >
      <p class="text-xs font-semibold uppercase tracking-wide text-red-600 dark:text-red-400">
        {t("invoicing.summary.overdue")}
      </p>
      <p
        class="mt-1 text-2xl font-semibold {data.summary.overdue_count > 0
          ? 'text-red-600 dark:text-red-400'
          : 'text-text'}"
      >
        {money(data.summary.overdue_total)}
      </p>
      <p class="text-xs text-text-muted">{data.summary.overdue_count}</p>
    </button>
    <button
      class="rounded-xl border border-border bg-surface-raised p-4 text-left hover:border-brand"
      onclick={() => setFilter("status", "draft")}
    >
      <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("invoicing.summary.draft")}
      </p>
      <p class="mt-1 text-2xl font-semibold text-text">{data.summary.draft_count}</p>
    </button>
    <div class="rounded-xl border border-border bg-surface-raised p-4">
      <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("invoicing.summary.paid_year")}
      </p>
      <p class="mt-1 text-2xl font-semibold text-text">{money(data.summary.paid_this_year)}</p>
    </div>
  </div>
{/if}

<div class="mb-4 flex flex-wrap items-center gap-2">
  <SearchInput placeholder={t("invoicing.search")} />
  <div class="w-44">
    <Combobox
      items={companyItems}
      name="_filter_company"
      value={data.companyFilter}
      placeholder={t("invoicing.filter.company")}
      onselect={(v) => setFilter("company", v)}
      id="filter-company"
    />
  </div>
  {#each STATUSES as status (status)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.statusFilter === status
        ? 'bg-brand/10 text-brand ring-2 ring-brand'
        : 'bg-surface text-text-muted hover:text-text'}"
      aria-pressed={data.statusFilter === status}
      onclick={() => setFilter("status", data.statusFilter === status ? "" : status)}
      >{t(`invoicing.status.${status}`)}</button
    >
  {/each}
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

{#snippet numberCell(invoice: Invoice)}
  <a
    href="/invoices/{invoice.id}"
    data-sveltekit-preload-data="hover"
    class="font-medium text-text hover:text-brand"
  >
    {invoice.number ?? t("invoicing.status.draft")}
    {#if invoice.kind === "credit_note"}
      <span class="ml-1 rounded bg-surface px-1 text-xs text-text-muted"
        >{t("invoicing.kind.credit_note")}</span
      >
    {/if}
  </a>
{/snippet}

{#snippet companyCell(invoice: Invoice)}
  <a href="/companies/{invoice.company_id}" class="text-text-muted hover:text-brand"
    >{invoice.company_name}</a
  >
{/snippet}

{#snippet issueCell(invoice: Invoice)}
  <span class="tabular-nums text-text-muted"
    >{invoice.issue_date ? fmtNumericDate(invoice.issue_date) : "—"}</span
  >
{/snippet}

{#snippet dueCell(invoice: Invoice)}
  <span
    class="tabular-nums {invoice.overdue
      ? 'font-medium text-red-600 dark:text-red-400'
      : 'text-text-muted'}">{invoice.due_date ? fmtNumericDate(invoice.due_date) : "—"}</span
  >
{/snippet}

{#snippet statusCell(invoice: Invoice)}
  {#if invoice.overdue}
    <span
      class="rounded-md bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-900/40 dark:text-red-300"
      >{t("invoicing.status.overdue")}</span
    >
  {:else}
    <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
      >{t(`invoicing.status.${invoice.status}`)}</span
    >
  {/if}
{/snippet}

{#snippet totalCell(invoice: Invoice)}
  <span class="tabular-nums text-text"
    >{docMoney(invoice.total, invoice.currency, data.locale)}</span
  >
{/snippet}

{#snippet outstandingCell(invoice: Invoice)}
  {#if invoice.status === "open"}
    <span class="tabular-nums text-text"
      >{docMoney(invoice.outstanding, invoice.currency, data.locale)}</span
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet referenceCell(invoice: Invoice)}
  <span class="text-text-muted">{invoice.reference ?? "—"}</span>
{/snippet}

{#snippet remindersCell(invoice: Invoice)}
  <span class="text-text-muted">{invoice.reminder_count > 0 ? invoice.reminder_count : "—"}</span>
{/snippet}

{#snippet rowActions(invoice: Invoice)}
  <ActionsMenu
    compact
    items={[
      { label: t("common.edit"), icon: Pencil, href: editHref(`/invoices/${invoice.id}`) },
      ...(invoice.status === "draft" && data.canWrite
        ? [
            {
              label: t("common.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => {
                deleteId = invoice.id;
                confirmDelete = true;
              },
            },
          ]
        : []),
    ]}
  />
{/snippet}

{#snippet mobileRow(invoice: Invoice)}
  <a href="/invoices/{invoice.id}" class="min-w-0 flex-1">
    <span class="block truncate text-sm font-medium text-text"
      >{invoice.number ?? t("invoicing.status.draft")} · {invoice.company_name}</span
    >
    <span
      class="mt-0.5 block truncate text-xs {invoice.overdue
        ? 'text-red-600 dark:text-red-400'
        : 'text-text-muted'}"
    >
      {docMoney(invoice.total, invoice.currency, data.locale)} ·
      {invoice.overdue ? t("invoicing.status.overdue") : t(`invoicing.status.${invoice.status}`)}
    </span>
  </a>
{/snippet}

{#snippet emptyState()}
  <p class="p-6 text-sm text-text-muted">{t("invoicing.empty")}</p>
{/snippet}

{#if form?.error}
  <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<DataTable
  rows={data.invoices}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<Modal bind:open={fromTimeOpen} title={t("invoicing.from_time.title")}>
  <form method="POST" action="?/fromTime" use:enhance class="space-y-4">
    <p class="text-sm text-text-muted">{t("invoicing.from_time.hint")}</p>
    <div>
      <label for="ft-company" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.field.company")}</label
      >
      <Combobox
        items={companyItems}
        name="company_id"
        bind:value={ftCompany}
        id="ft-company"
        placeholder={t("invoicing.field.company")}
      />
    </div>
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="ft-until" class="mb-1 block text-sm font-medium text-text"
          >{t("invoicing.from_time.until")}</label
        >
        <DateInput name="until" id="ft-until" bind:value={ftUntil} />
      </div>
      <div>
        <label for="ft-group" class="mb-1 block text-sm font-medium text-text"
          >{t("invoicing.from_time.group_by")}</label
        >
        <select
          id="ft-group"
          name="group_by"
          class="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm"
        >
          <option value="project">{t("invoicing.from_time.group_project")}</option>
          <option value="day">{t("invoicing.from_time.group_day")}</option>
          <option value="entry">{t("invoicing.from_time.group_entry")}</option>
        </select>
      </div>
    </div>
    {#if ftCompany}
      <p
        class="text-sm {ftPreview && ftPreview.total_minutes > 0 ? 'text-text' : 'text-text-muted'}"
      >
        {#if ftLoading}
          …
        {:else if ftPreview}
          {t("invoicing.from_time.preview", { hours: ftHours, count: ftPreview.entries.length })}
        {/if}
      </p>
    {/if}
    {#if form?.fromTimeError}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.fromTimeError)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (fromTimeOpen = false)}>{t("common.cancel")}</button
      >
      <button
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        disabled={!ftCompany || !ftPreview || ftPreview.total_minutes === 0}
      >
        {t("invoicing.from_time.submit")}
      </button>
    </div>
  </form>
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("invoicing.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
