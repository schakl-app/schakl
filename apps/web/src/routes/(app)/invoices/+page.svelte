<script lang="ts">
  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { CircleMinus, Download, FileArchive, Pencil, Trash2 } from "@lucide/svelte";

  import { page } from "$app/state";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import { editHref } from "$lib/core/edit-intent";
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ImpexBar from "$lib/core/impex/ImpexBar.svelte";
  import { InFlight } from "$lib/core/submit.svelte";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import { filterUrl, type FilterDef } from "$lib/core/filters/types";
  import type { InvoiceFilterKey } from "$lib/modules/invoicing/filters";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";
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

  // --- originals (docs/INVOICING.md) ------------------------------------------
  // A zip of the PDFs an imported back catalogue was sent as, matched by invoice number. Its
  // own dialog rather than a step of the import wizard: the spreadsheet and the archive come
  // from different places (the ledger export, a mail folder) and rarely on the same day, and
  // the report it answers with is a different shape from a row report. Gated on the write key
  // the API route declares — no bulk permission, because attaching forty PDFs you may each
  // attach is the same act repeated (§18).
  const busy = new InFlight();
  let originalsOpen = $state(false);
  let originalsInput = $state<HTMLInputElement | null>(null);
  let originalsDropError = $state<string | null>(null);
  const originalsReport = $derived(form?.originals ?? null);
  const originalsCounts = $derived(
    originalsReport
      ? (
          [
            ["matched", originalsReport.matched?.length ?? 0],
            ["already_attached", originalsReport.already_attached?.length ?? 0],
            ["unmatched", originalsReport.unmatched?.length ?? 0],
            ["ambiguous", originalsReport.ambiguous?.length ?? 0],
            ["not_pdf", originalsReport.not_pdf?.length ?? 0],
          ] as const
        ).filter(([, count]) => count > 0)
      : [],
  );

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

  /** The list's filters, rendered by the shared bar (#354) — same three, same order, as quotes. */
  const filterDefs: FilterDef<InvoiceFilterKey>[] = $derived([
    { kind: "search", key: "q", placeholder: t("invoicing.search") },
    {
      kind: "select",
      key: "company",
      // A client is looking at their own documents, so a control for choosing *whose* has
      // nothing to offer them (#266).
      hidden: !data.canReadRegister,
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
        label: t(`invoicing.status.${status}`),
      })),
    },
    {
      kind: "pills",
      key: "overdue",
      options: [{ value: "1", label: t("invoicing.filter.overdue") }],
    },
  ]);

  /**
   * The summary tiles narrow the list to exactly what they count (UX §7).
   *
   * They live above the bar rather than in it, so they go through `filterUrl` — the one place
   * that drops the page along with the filter. Building the URL by hand here is how a tile ends
   * up serving page 7 of the old list as page 7 of the new one.
   */
  function setFilter(key: InvoiceFilterKey, value: string) {
    void goto(filterUrl(page.url, key, value), { keepFocus: true, noScroll: true });
  }

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
      origin: originCell,
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

<FilterBar filters={filterDefs} idPrefix="invoice-filter">
  {#snippet actions()}
    {#if data.canReadRegister}
      <!-- Export carries what the screen is narrowed by, so the file *is* the list on screen,
           whole (docs/UX.md) — the API declares exactly these on the export route. A client
           reads only their own copies, and bulk is not theirs either way (§17). -->
      <ImpexBar
        entity="invoice"
        readPermission="invoicing.invoice.read"
        writePermission="invoicing.invoice.write"
        filters={{
          q: data.q,
          company_id: data.companyFilter,
          status: data.statusFilter,
          overdue: data.overdueFilter,
          sort: data.table.sort,
        }}
        locale={data.locale}
        {form}
      />
      {#if data.canWrite}
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
          onclick={() => (originalsOpen = true)}
        >
          <FileArchive class="h-4 w-4" />
          {t("invoicing.originals.title")}
        </button>
      {/if}
    {/if}
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

{#snippet originCell(invoice: Invoice)}
  {#if invoice.origin === "imported"}
    <span
      class="inline-block max-w-full truncate rounded-md bg-surface px-2 py-0.5 align-middle text-xs text-text-muted"
      >{t("invoicing.origin.imported")}</span
    >
  {:else}
    <span class="text-text-muted">{t("invoicing.origin.native")}</span>
  {/if}
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

<Modal bind:open={originalsOpen} title={t("invoicing.originals.title")}>
  <form
    method="POST"
    action="?/originals"
    enctype="multipart/form-data"
    use:enhance={busy.keep("originals")}
    class="space-y-3"
  >
    <p class="text-sm text-text-muted">{t("invoicing.originals.hint")}</p>
    <div
      class="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-border p-3"
      use:filedrop={{
        input: () => originalsInput,
        disabled: busy.active,
        onerror: (key) => (originalsDropError = key),
      }}
    >
      <input
        bind:this={originalsInput}
        type="file"
        name="file"
        accept="application/zip,.zip"
        required
        class="text-sm text-text"
        onchange={() => (originalsDropError = null)}
      />
      <span class="text-xs text-text-muted">{t("common.drop_hint")}</span>
    </div>
    {#if originalsDropError || form?.originalsError}
      <p class="text-sm text-red-600 dark:text-red-400">
        {t(originalsDropError ?? form?.originalsError ?? "")}
      </p>
    {/if}
    {#if originalsReport}
      <!-- The whole report, counts first and then every file that did not land, by name:
           "3 gekoppeld" alone leaves the reader guessing which of the forty were not. -->
      <div class="rounded-lg bg-surface p-3 text-sm">
        {#if originalsCounts.length === 0}
          <p class="text-text-muted">{t("invoicing.originals.nothing")}</p>
        {:else}
          <ul class="space-y-0.5">
            {#each originalsCounts as [key, count] (key)}
              <li class={key === "matched" ? "text-text" : "text-text-muted"}>
                {t(`invoicing.originals.${key}`, { count })}
              </li>
            {/each}
          </ul>
        {/if}
        {#each [...(originalsReport.unmatched ?? []), ...(originalsReport.ambiguous ?? []), ...(originalsReport.not_pdf ?? [])] as name (name)}
          <p class="mt-1 truncate font-mono text-xs text-text-muted">{name}</p>
        {/each}
      </div>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (originalsOpen = false)}>{t("common.close")}</button
      >
      <Button loading={busy.is("originals")} disabled={busy.active}>
        {t("invoicing.originals.upload")}
      </Button>
    </div>
  </form>
</Modal>
