<script lang="ts">
  import { CircleMinus, Download, Pencil, Trash2 } from "@lucide/svelte";

  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import { navLabel, pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { INVOICE_COLUMNS } from "$lib/modules/invoicing/columns";
  import DocTabs from "$lib/modules/invoicing/DocTabs.svelte";
  import { docMoney, docStatus, MAX_ARCHIVE_DOCUMENTS } from "$lib/modules/invoicing/types";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";

  let { data, form } = $props();

  type Invoice = (typeof data.invoices)[number];

  // `draft` is the agency's own working state: an `:own` viewer never receives one, so
  // offering the chip would be a filter that can only ever empty the page (#266).
  const STATUSES = $derived(
    data.canReadRegister
      ? (["draft", "open", "paid", "cancelled"] as const)
      : (["open", "paid", "cancelled"] as const),
  );
  let deleteId = $state("");
  let confirmDelete = $state(false);

  // --- bulk (the ✎ selection mode in the toolbar) ----------------------------
  // Download and delete, and deliberately nothing else: everything else an invoice has is money
  // or its place in a lifecycle, and a status moves by *doing* something — issuing, sending,
  // recording a payment — each with its own rules. Clearing out a batch of drafts is one real
  // want, and the API allows drafts only (`app/modules/invoicing/bulk.py`), so a mixed selection
  // comes back as "3 verwijderd · 5 overgeslagen" naming why rather than refusing the lot.
  //
  // Handing a month of invoices to the accountant is the other, and it is the row menu's
  // Download over a selection: one zip of exactly the PDFs those rows would have given one at a
  // time (#307). A *link*, not a handler — it is a navigation, and the API is a GET so it keeps
  // working on an expired licence, where "print what you already billed" must not be gated.
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  // A draft has no document, exactly as in the row menu — so it is not in the archive and the
  // button says how many of the picked rows it will actually hand over (docs/UX.md, #299).
  const downloadable = $derived(
    data.invoices
      .filter((i) => bulkSelected.includes(i.id) && i.status !== "draft")
      .map((i) => i.id),
  );
  const bulkConfig = $derived({
    items: [
      {
        label: t("invoicing.action.download_pdf"),
        icon: Download,
        eligible: downloadable.length,
        // Over the cap the control refuses and says why, rather than quietly archiving an
        // arbitrary fifty of the rows that were ticked (the API declares the same number).
        disabledReason:
          downloadable.length > MAX_ARCHIVE_DOCUMENTS
            ? t("invoicing.bulk.download_limit", { count: MAX_ARCHIVE_DOCUMENTS })
            : undefined,
        href: `/invoices/download?${downloadable.map((id) => `ids=${id}`).join("&")}`,
      },
    ],
    deletePermission: "invoicing.invoice.delete",
    deleteMessage: t("invoicing.bulk.delete_message", { count: bulkSelected.length }),
  });

  function setFilter(key: string, value: string) {
    const url = resetPage(new URL(page.url));
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  // "Facturatie" is the agency's word for its own section; a client is looking at their own
  // copies, so the same screen names itself for whoever opened it (#266). The tenant's nav
  // rename still wins for staff — `navLabel` is what carries it.
  const heading = $derived(
    data.canReadRegister ? navLabel("invoicing", t("invoicing.title")) : t("invoicing.title_own"),
  );

  // Archived clients sit behind the search instead of among the live ones, and the one
  // already picked is always offered (`companies/picker.ts`).
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.companyFilter }),
  );
  const companyItems = $derived(companyPicker.live);
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
  <title
    >{pageTitle(
      data.canReadRegister ? navLabel("invoicing", t("invoicing.invoices")) : heading,
    )}</title
  >
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{heading}</h1>
  {#if data.canWrite}
    <a
      href="/invoices/new"
      data-sveltekit-preload-data="hover"
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >{t("invoicing.new_invoice")}</a
    >
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
    {#if data.canReadRegister}
      <button
        class="rounded-xl border border-border bg-surface-raised p-4 text-left hover:border-brand"
        onclick={() => setFilter("status", "draft")}
      >
        <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
          {t("invoicing.summary.draft")}
        </p>
        <p class="mt-1 text-2xl font-semibold text-text">{data.summary.draft_count}</p>
      </button>
    {/if}
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
  {#if data.canReadRegister}
    <div class="w-44">
      <Combobox
        items={companyItems}
        archived={companyPicker.retired}
        archivedLabel={companyArchivedLabel()}
        name="_filter_company"
        value={data.companyFilter}
        placeholder={t("invoicing.filter.company")}
        onselect={(v) => setFilter("company", v)}
        id="filter-company"
      />
    </div>
  {/if}
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
  <!-- The list's own controls, pushed right: the filters read left-to-right, what you can *do*
       with the list sits at the far end, and that is the same on every list here. -->
  <div class="ml-auto flex flex-wrap items-center gap-2">
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

{#snippet numberCell(invoice: Invoice)}
  <a
    href="/invoices/{invoice.id}"
    data-sveltekit-preload-data="hover"
    class="flex min-w-0 items-center gap-1 font-medium text-text hover:text-brand"
  >
    <span class="truncate">{invoice.number ?? t("invoicing.status.draft")}</span>
    {#if invoice.kind === "credit_note"}
      <!-- A glyph, not the word (#341). This used to be a `shrink-0` "Creditfactuur" badge on the
           reasoning that the kind is what the row is about — but the column is 130px, 98px of it
           inside the padding, the badge measured 84px and refused to give any of it back, so
           `2026-0006` was handed 10px and rendered as `2.`. The one document hardest to tell
           apart from its neighbours was the only one you could not read the number of. No width
           fixes that: a longer number or a longer translation just moves the threshold. 14px of
           icon cannot starve the identity in any locale, and the word rides along in `sr-only`
           (and in `title` for a sighted hover) rather than being lost. -->
      <span class="shrink-0 text-text-muted" title={t("invoicing.kind.credit_note")}>
        <CircleMinus class="size-3.5" aria-hidden="true" />
        <span class="sr-only">{t("invoicing.kind.credit_note")}</span>
      </span>
    {/if}
  </a>
{/snippet}

{#snippet companyCell(invoice: Invoice)}
  <!-- `block`, because `overflow` does not apply to an inline box: a bare `truncate` here
       would set `nowrap` and nothing else, and a long client name would spill into the dates. -->
  <a href="/companies/{invoice.company_id}" class="block truncate text-text-muted hover:text-brand"
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
  {@const state = docStatus(invoice)}
  {#if state.tone === "danger"}
    <!-- `inline-block max-w-full`, so the chip keeps its shrink-to-fit shape and still clips:
         "Geannuleerd" is wider than the 120px column allows. -->
    <span
      class="inline-block max-w-full truncate rounded-md bg-red-100 px-2 py-0.5 align-middle text-xs font-medium text-red-700 dark:bg-red-900/40 dark:text-red-300"
      >{t(state.key)}</span
    >
  {:else}
    <span
      class="inline-block max-w-full truncate rounded-md bg-surface px-2 py-0.5 align-middle text-xs text-text-muted"
      >{t(state.key)}</span
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
  <span class="block truncate text-text-muted">{invoice.reference ?? "—"}</span>
{/snippet}

{#snippet remindersCell(invoice: Invoice)}
  <span class="text-text-muted">{invoice.reminder_count > 0 ? invoice.reminder_count : "—"}</span>
{/snippet}

{#snippet rowActions(invoice: Invoice)}
  <ActionsMenu
    compact
    items={[
      // Was drawn for everyone (#253's rule, missed here): a viewer who cannot write got a
      // "Bewerken" that the API refuses. A client portal login made that visible (#266).
      ...(data.canWrite
        ? [{ label: t("common.edit"), icon: Pencil, href: editHref(`/invoices/${invoice.id}`) }]
        : []),
      // The download the client came for. A draft has nothing to render, so it is offered
      // exactly when there is a document — the detail page's own rule.
      ...(invoice.status !== "draft"
        ? [
            {
              label: t("invoicing.action.download_pdf"),
              icon: Download,
              href: `/invoices/${invoice.id}/pdf`,
            },
          ]
        : []),
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
  <p class="p-6 text-sm text-text-muted">
    {data.canReadRegister ? t("invoicing.empty") : t("invoicing.empty_own")}
  </p>
{/snippet}

{#if form?.error}
  <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

<DataTable
  rows={data.invoices}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={rowActions}
  {mobileRow}
  empty={emptyState}
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

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("invoicing.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
