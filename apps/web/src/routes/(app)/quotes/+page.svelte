<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { QUOTE_COLUMNS } from "$lib/modules/invoicing/columns";
  import DocTabs from "$lib/modules/invoicing/DocTabs.svelte";
  import { docMoney } from "$lib/modules/invoicing/types";

  let { data, form } = $props();

  type Quote = (typeof data.quotes)[number];

  const STATUSES = ["draft", "open", "accepted", "rejected", "expired", "invoiced"] as const;
  let deleteId = $state("");
  let confirmDelete = $state(false);

  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }
  const companyItems = $derived(data.companies.map((c) => ({ value: c.id, label: c.name })));

  const table = createTableLayout<Quote>({
    all: () => QUOTE_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      number: numberCell,
      company: companyCell,
      issue_date: issueCell,
      valid_until: validCell,
      status: statusCell,
      total: totalCell,
      reference: referenceCell,
    }),
  });
</script>

<svelte:head>
  <title>{pageTitle(t("invoicing.quotes"))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{t("invoicing.title")}</h1>
  {#if data.canWrite}
    <a
      href="/quotes/new"
      data-sveltekit-preload-data="hover"
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >{t("invoicing.new_quote")}</a
    >
  {/if}
</div>

{#if data.canInvoices}
  <DocTabs />
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
      >{t(`invoicing.quote_status.${status}`)}</button
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

{#snippet numberCell(quote: Quote)}
  <a
    href="/quotes/{quote.id}"
    data-sveltekit-preload-data="hover"
    class="font-medium text-text hover:text-brand"
    >{quote.number ?? t("invoicing.quote_status.draft")}</a
  >
{/snippet}

{#snippet companyCell(quote: Quote)}
  <a href="/companies/{quote.company_id}" class="text-text-muted hover:text-brand"
    >{quote.company_name}</a
  >
{/snippet}

{#snippet issueCell(quote: Quote)}
  <span class="tabular-nums text-text-muted"
    >{quote.issue_date ? fmtNumericDate(quote.issue_date) : "—"}</span
  >
{/snippet}

{#snippet validCell(quote: Quote)}
  <span
    class="tabular-nums {quote.expired && quote.status === 'open'
      ? 'text-red-600 dark:text-red-400'
      : 'text-text-muted'}">{quote.valid_until ? fmtNumericDate(quote.valid_until) : "—"}</span
  >
{/snippet}

{#snippet statusCell(quote: Quote)}
  <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
    >{t(`invoicing.quote_status.${quote.status}`)}</span
  >
{/snippet}

{#snippet totalCell(quote: Quote)}
  <span class="tabular-nums text-text">{docMoney(quote.total, quote.currency, data.locale)}</span>
{/snippet}

{#snippet referenceCell(quote: Quote)}
  <span class="text-text-muted">{quote.reference ?? "—"}</span>
{/snippet}

{#snippet rowActions(quote: Quote)}
  <ActionsMenu
    compact
    items={[
      { label: t("common.edit"), icon: Pencil, href: editHref(`/quotes/${quote.id}`) },
      ...(["draft", "rejected", "expired"].includes(quote.status) && data.canWrite
        ? [
            {
              label: t("common.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => {
                deleteId = quote.id;
                confirmDelete = true;
              },
            },
          ]
        : []),
    ]}
  />
{/snippet}

{#snippet mobileRow(quote: Quote)}
  <a href="/quotes/{quote.id}" class="min-w-0 flex-1">
    <span class="block truncate text-sm font-medium text-text"
      >{quote.number ?? t("invoicing.quote_status.draft")} · {quote.company_name}</span
    >
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {docMoney(quote.total, quote.currency, data.locale)} ·
      {t(`invoicing.quote_status.${quote.status}`)}
    </span>
  </a>
{/snippet}

{#snippet emptyState()}
  <p class="p-6 text-sm text-text-muted">{t("invoicing.quotes_empty")}</p>
{/snippet}

{#if form?.error}
  <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<DataTable
  rows={data.quotes}
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

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("invoicing.quote_delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
