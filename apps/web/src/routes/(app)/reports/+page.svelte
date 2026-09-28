<script lang="ts">
  /**
   * The report register (issue #300) — every client's reports, newest period first.
   *
   * The shared table (columns the viewer picks, a sort the API applies), a selection for
   * clearing out drafts nobody will send, and the shared pager (CLAUDE.md §9, §18).
   *
   * The screen is the same for staff and for a client login; what differs is what the API
   * serves, which columns belong to the reader (`columnsForViewer`, #373) and which controls
   * their permissions draw. `!isPortal` is never the gate for a control here.
   */
  import FileText from "@lucide/svelte/icons/file-text";
  import Play from "@lucide/svelte/icons/play";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";

  import { enhance } from "$app/forms";
  import { invalidate } from "$app/navigation";
  import { page } from "$app/state";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pollWhile } from "$lib/core/poll.svelte";
  import { InFlight } from "$lib/core/submit.svelte";
  import { columnsForViewer } from "$lib/core/table/columns";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import {
    REPORT_COLUMNS,
    REPORT_FILTER_STATUSES,
    type ReportFilterKey,
  } from "$lib/modules/reporting/columns";
  import {
    audienceLabel,
    fmtDate,
    needsAttention,
    periodLabel,
    statusLabel,
  } from "$lib/modules/reporting/format";
  import ReportStatusPill from "$lib/modules/reporting/ReportStatusPill.svelte";

  let { data, form } = $props();

  type Report = (typeof data.reports)[number];

  const busy = new InFlight();
  const reports = $derived(data.reports);
  const locale = $derived(data.locale ?? "nl");
  /**
   * A client reads their finished documents; staff review a queue. The amber warning count and
   * the "nakijken" link are the queue's vocabulary — a client seeing "3 waarschuwingen" beside
   * a report they were told was finished learns something that is true, none of their business,
   * and alarming (the detail page's rule, #373). Layout only: every control on this page stays
   * gated on its own API key.
   */
  const reader = $derived(data.isPortal);
  const narrowed = $derived(
    Boolean(data.filters.company_id || data.filters.audience || data.filters.status),
  );

  /**
   * "Genereer alles" queues a job per client and returns immediately, so the whole point of
   * this list right afterwards is watching the batch land. Only while something on the page is
   * actually running — the interval stops on its own when the last row leaves `generating`.
   */
  const anyGenerating = $derived(reports.some((r) => r.status === "generating"));
  pollWhile(
    () => anyGenerating,
    () => invalidate("reporting:reports"),
  );

  // --- bulk (the ✎ selection mode in the toolbar) ----------------------------
  // Delete and nothing else. The module has no separate delete key — the record's own delete
  // declares `reporting.report.write` — so that is the key mirrored here (#310). A report that
  // was sent is a document a client has read: the API refuses it, so it is counted out before
  // anyone presses and the button says how many will actually go (#299).
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  const deletable = $derived(reports.filter((r) => bulkSelected.includes(r.id) && !r.sent_at));
  const bulkConfig = $derived({
    deletePermission: "reporting.report.write",
    deleteEligible: deletable.length,
    deleteDisabledReason:
      bulkSelected.length > 0 && deletable.length === 0
        ? t("reporting.bulk.none_deletable")
        : undefined,
    deleteMessage: t("reporting.bulk.delete_message", { count: deletable.length }),
  });

  // Archived clients behind the search rather than among the live ones; the client this list is
  // filtered by is always offered (`companies/picker.ts`).
  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.filters.company_id ?? "" }),
  );
  /**
   * The filters are the desk's: a client has one company, one kind of document and no workflow
   * states to look for, so each is hidden rather than drawn empty.
   */
  const filterDefs: FilterDef<ReportFilterKey>[] = $derived([
    {
      kind: "select",
      key: "company",
      hidden: !data.canWrite,
      placeholder: t("reporting.list.all_clients"),
      options: companyPicker.live,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    {
      kind: "select",
      key: "audience",
      hidden: !data.canWrite,
      placeholder: t("reporting.list.all_audiences"),
      options: [
        { value: "client", label: audienceLabel("client") },
        ...(data.canSeeInternal ? [{ value: "internal", label: audienceLabel("internal") }] : []),
      ],
    },
    {
      kind: "pills",
      key: "status",
      hidden: !data.canWrite,
      options: REPORT_FILTER_STATUSES.map((status) => ({
        value: status,
        label: statusLabel(status),
      })),
    },
  ]);

  const table = createTableLayout<Report>({
    all: () => columnsForViewer(REPORT_COLUMNS, page.data.user),
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      company: companyCell,
      period: periodCell,
      audience: audienceCell,
      status: statusCell,
      sent: sentCell,
      title: titleCell,
      generated_by: generatedByCell,
      created_at: createdCell,
    }),
  });
</script>

<svelte:head>
  <title>{pageTitle(t("nav.reports"))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-start justify-between gap-3">
  <div>
    <h1 class="mt-2 text-xl font-semibold text-text">{t("nav.reports")}</h1>
    <p class="text-sm text-text-muted">{t("reporting.list.subtitle")}</p>
  </div>
  {#if data.canWrite}
    <form method="POST" action="?/generateAll" use:enhance={busy.keep("all")}>
      <Button type="submit" loading={busy.is("all")} disabled={busy.active}>
        <Play size={15} />
        {t("reporting.list.generate_all")}
      </Button>
    </form>
  {/if}
</div>

{#if form?.batch}
  {#if form.batch.enrolled === 0}
    <!-- Nobody is enrolled. A bare "0" here reads as a broken button; say which step is
         missing, and how many clients are waiting for it. -->
    <p
      class="mb-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
    >
      {t("reporting.list.nobody_enrolled")}
      {#if form.batch.unconfigured > 0}
        {t("reporting.list.unconfigured", { count: String(form.batch.unconfigured) })}
      {/if}
    </p>
  {:else}
    <p class="mb-4 rounded-lg bg-surface px-4 py-3 text-sm text-text">
      {t("reporting.list.batch_queued", { count: String(form.batch.queued) })}
      {#if form.batch.skipped.length > 0}
        <span class="text-text-muted">
          · {t("reporting.list.batch_skipped", { count: String(form.batch.skipped.length) })}
        </span>
      {/if}
    </p>
  {/if}
{:else if form?.queued}
  <p class="mb-4 rounded-lg bg-surface px-4 py-3 text-sm text-text">
    {t("reporting.list.queued")}
  </p>
{:else if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
  >
    {t(form.error)}
  </p>
{/if}

<FilterBar filters={filterDefs} idPrefix="report-filter">
  {#snippet actions()}
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
    <!-- Last in the toolbar, always (docs/UX.md): the one control that changes what the rows
         do rather than what the list shows. Not drawn for a viewer who may not delete. -->
    <BulkToggle bind:selecting bind:selected={bulkSelected} {...bulkConfig} />
  {/snippet}
</FilterBar>

{#snippet companyCell(report: Report)}
  <span class="flex min-w-0 items-center gap-2">
    <a
      href={`/reports/${report.id}`}
      class="block truncate font-medium text-text hover:text-brand"
      data-sveltekit-preload-data="hover">{report.company_name}</a
    >
    {#if !reader && report.warning_count > 0}
      <span class="shrink-0 text-xs text-amber-600 dark:text-amber-400">
        {t("reporting.list.warnings", { count: String(report.warning_count) })}
      </span>
    {/if}
  </span>
{/snippet}

{#snippet periodCell(report: Report)}
  <span class="block truncate text-text-muted">{periodLabel(report, locale)}</span>
{/snippet}

{#snippet audienceCell(report: Report)}
  <span class="block truncate text-text-muted">{audienceLabel(report.audience)}</span>
{/snippet}

{#snippet statusCell(report: Report)}
  <span class="flex min-w-0 items-center gap-1">
    <ReportStatusPill status={report.status} size="xs" />
    {#if report.status === "generating"}
      <RefreshCw size={13} class="shrink-0 animate-spin text-text-muted" />
    {/if}
  </span>
{/snippet}

{#snippet sentCell(report: Report)}
  <span class="flex min-w-0 items-center gap-2 text-text-muted">
    <span class="truncate">{report.sent_at ? fmtDate(report.sent_at, locale) : "—"}</span>
    {#if !reader && needsAttention(report.status) && !report.sent_at}
      <a href={`/reports/${report.id}`} class="shrink-0 text-brand hover:underline">
        {t("reporting.list.review")}
      </a>
    {/if}
  </span>
{/snippet}

{#snippet titleCell(report: Report)}
  <span class="block truncate text-text-muted">{report.title || "—"}</span>
{/snippet}

{#snippet generatedByCell(report: Report)}
  <span class="block truncate text-text-muted">{report.generated_by_name ?? "—"}</span>
{/snippet}

{#snippet createdCell(report: Report)}
  <span class="tabular-nums text-text-muted"
    >{report.created_at ? fmtDateTime(report.created_at) : "—"}</span
  >
{/snippet}

{#snippet mobileRow(report: Report)}
  <a href={`/reports/${report.id}`} class="min-w-0 flex-1">
    <span class="block truncate text-sm font-medium text-text">{report.company_name}</span>
    <span class="mt-0.5 block truncate text-xs text-text-muted">{periodLabel(report, locale)}</span>
  </a>
  {#if !reader}
    <ReportStatusPill status={report.status} size="xs" />
  {/if}
{/snippet}

{#snippet emptyState()}
  <div class="px-6 py-12 text-center">
    <FileText size={28} class="mx-auto mb-3 text-text-muted" />
    <p class="text-sm text-text-muted">
      {narrowed ? t("reporting.list.empty_filtered") : t("reporting.list.empty")}
    </p>
    {#if data.canWrite && !narrowed}
      <p class="mx-auto mt-2 max-w-md text-sm text-text-muted">
        {t("reporting.list.empty_hint")}
      </p>
    {/if}
  </div>
{/snippet}

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

<DataTable
  rows={reports}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  {locale}
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
