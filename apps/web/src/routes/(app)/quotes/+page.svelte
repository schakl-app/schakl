<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { page } from "$app/state";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import type { DocumentFilterKey } from "$lib/modules/invoicing/filters";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import { QUOTE_COLUMNS } from "$lib/modules/invoicing/columns";
  import DocTabs from "$lib/modules/invoicing/DocTabs.svelte";
  import { docMoney } from "$lib/modules/invoicing/types";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";

  let { data, form } = $props();

  type Quote = (typeof data.quotes)[number];

  const STATUSES = ["draft", "open", "accepted", "rejected", "expired", "invoiced"] as const;
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Archived clients sit behind the search instead of among the live ones, and the one
  // already picked is always offered (`companies/picker.ts`).
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.companyFilter }),
  );
  const companyItems = $derived(companyPicker.live);

  /** The list's filters, rendered by the shared bar (#354) — the same three invoices has. */
  const filterDefs: FilterDef<DocumentFilterKey>[] = $derived([
    { kind: "search", key: "q", placeholder: t("invoicing.search") },
    {
      kind: "select",
      key: "company",
      placeholder: t("invoicing.field.company"),
      options: companyItems,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    {
      kind: "pills",
      key: "status",
      options: STATUSES.map((status) => ({
        value: status,
        label: t(`invoicing.quote_status.${status}`),
      })),
    },
  ]);

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
  <title>{pageTitle(navLabel("invoicing", t("invoicing.quotes")))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{navLabel("invoicing", t("invoicing.title"))}</h1>
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

<FilterBar filters={filterDefs} idPrefix="quote-filter">
  {#snippet actions()}
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
  {/snippet}
</FilterBar>

<!-- The same cells as `/invoices`, which already carried the fix (#370): `DataTable` is
     `table-fixed` with `overflow-hidden` on every `<td>`, so a cell that says nothing is cut
     mid-glyph. `truncate` only ellipsizes on a block box or a flex item — on a bare inline
     `<a>` it sets `nowrap` and nothing else, which reads as correct in the diff. -->
{#snippet numberCell(quote: Quote)}
  <a
    href="/quotes/{quote.id}"
    data-sveltekit-preload-data="hover"
    class="block truncate font-medium text-text hover:text-brand"
    >{quote.number ?? t("invoicing.quote_status.draft")}</a
  >
{/snippet}

{#snippet companyCell(quote: Quote)}
  <a href="/companies/{quote.company_id}" class="block truncate text-text-muted hover:text-brand"
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
  <span class="block truncate text-text-muted">{quote.reference ?? "—"}</span>
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

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("invoicing.quote_delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
