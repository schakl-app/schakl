<script lang="ts">
  /**
   * The meetings register: every recorded meeting, newest first — its client, when, how long,
   * where it is in its lifecycle. The shared table (columns the viewer picks, a sort the API
   * applies), a selection for clearing out recordings nobody wants, and the shared pager
   * (CLAUDE.md §9, §18).
   */
  import Mic from "@lucide/svelte/icons/mic";
  import Plus from "@lucide/svelte/icons/plus";

  import { invalidate } from "$app/navigation";
  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import BulkBar from "$lib/core/bulk/BulkBar.svelte";
  import BulkResult from "$lib/core/bulk/BulkResult.svelte";
  import BulkToggle from "$lib/core/bulk/BulkToggle.svelte";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pollWhile } from "$lib/core/poll.svelte";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import {
    MEETING_COLUMNS,
    workerHolds,
    type MeetingFilterKey,
  } from "$lib/modules/meetings/columns";
  import { fmtDuration, inFlight, kindLabel } from "$lib/modules/meetings/format";
  import MeetingStatusPill from "$lib/modules/meetings/MeetingStatusPill.svelte";

  let { data, form } = $props();

  type Meeting = (typeof data.meetings)[number];

  const meetings = $derived(data.meetings);
  // The recorder needs a provider that can transcribe *and* the feature on; a control that
  // could only refuse is not drawn (off means invisible, #126).
  const canRecord = $derived(data.canWrite && aiEnabled(page.data.user, "meeting_assist"));
  const narrowed = $derived(
    Boolean(data.filters.q || data.filters.company_id || data.filters.status),
  );

  // A worker is still holding one of these rows: re-read the list until it is not.
  pollWhile(
    () => meetings.some((m) => inFlight(m.status)),
    () => invalidate("meetings:list"),
    6000,
  );

  // --- bulk (the ✎ selection mode in the toolbar) ----------------------------
  // Delete and nothing else: nothing on a meeting is a value a selection could share. A row a
  // worker is reading is refused by the API, so it is counted out here first — the button says
  // how many it will actually remove, and says why when that is none (#299).
  let selecting = $state(false);
  let bulkSelected = $state<string[]>([]);
  const deletable = $derived(
    meetings.filter((m) => bulkSelected.includes(m.id) && !workerHolds(m.status)),
  );
  const bulkConfig = $derived({
    deletePermission: "meetings.meeting.delete",
    deleteEligible: deletable.length,
    deleteDisabledReason:
      bulkSelected.length > 0 && deletable.length === 0
        ? t("meetings.bulk.none_deletable")
        : undefined,
    deleteMessage: t("meetings.bulk.delete_message", { count: deletable.length }),
  });

  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.filters.company_id }),
  );
  const filterDefs: FilterDef<MeetingFilterKey>[] = $derived([
    { kind: "search", key: "q", placeholder: t("meetings.list.search_placeholder") },
    {
      kind: "select",
      key: "company",
      placeholder: t("meetings.list.all_clients"),
      options: companyPicker.live,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    {
      kind: "pills",
      key: "status",
      options: [
        { value: "ready", label: t("meetings.status.ready") },
        { value: "failed", label: t("meetings.status.failed") },
      ],
    },
  ]);

  const table = createTableLayout<Meeting>({
    all: () => MEETING_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      title: titleCell,
      company: companyCell,
      occurred_at: whenCell,
      duration: durationCell,
      status: statusCell,
      action_items: actionItemsCell,
      decisions: decisionsCell,
      kind: kindCell,
      project: projectCell,
      owner: ownerCell,
    }),
  });
</script>

<svelte:head>
  <title>{pageTitle(navLabel("meetings", t("nav.meetings")))}</title>
</svelte:head>

<PageHeader title={navLabel("meetings", t("nav.meetings"))}>
  {#snippet subtitle()}{t("meetings.list.subtitle")}{/snippet}
  {#snippet actions()}
    {#if canRecord}
      <a
        href="/meetings/new"
        class="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        data-sveltekit-preload-data="hover"
      >
        <Plus size={15} />
        {t("meetings.list.new")}
      </a>
    {/if}
  {/snippet}
</PageHeader>

<FilterBar filters={filterDefs} idPrefix="meeting-filter">
  {#snippet actions()}
    <ColumnPicker
      all={table.pickerColumns}
      visible={table.visibleKeys}
      sort={table.sort}
      onchange={table.onColumnsChange}
      onsort={table.onSort}
    />
    <!-- Last in the toolbar, always (docs/UX.md): the one control that changes what the rows
         do rather than what the list shows. -->
    <BulkToggle bind:selecting bind:selected={bulkSelected} {...bulkConfig} />
  {/snippet}
</FilterBar>

{#snippet titleCell(meeting: Meeting)}
  <a
    href={`/meetings/${meeting.id}`}
    class="block truncate font-medium text-text hover:text-brand"
    data-sveltekit-preload-data="hover">{meeting.title}</a
  >
{/snippet}

{#snippet companyCell(meeting: Meeting)}
  {#if meeting.company_id && meeting.company_name}
    <a
      href={`/companies/${meeting.company_id}`}
      class="block truncate text-text-muted hover:text-brand">{meeting.company_name}</a
    >
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet whenCell(meeting: Meeting)}
  <span class="whitespace-nowrap tabular-nums text-text-muted"
    >{fmtDateTime(meeting.occurred_at)}</span
  >
{/snippet}

{#snippet durationCell(meeting: Meeting)}
  <span class="tabular-nums text-text-muted">{fmtDuration(meeting.duration_seconds) || "—"}</span>
{/snippet}

{#snippet statusCell(meeting: Meeting)}
  <MeetingStatusPill status={meeting.status} />
{/snippet}

{#snippet actionItemsCell(meeting: Meeting)}
  <span class="tabular-nums text-text-muted">{meeting.action_item_count || "—"}</span>
{/snippet}

{#snippet decisionsCell(meeting: Meeting)}
  <span class="tabular-nums text-text-muted">{meeting.decision_count || "—"}</span>
{/snippet}

{#snippet kindCell(meeting: Meeting)}
  <span class="block truncate text-text-muted">{kindLabel(meeting.kind)}</span>
{/snippet}

{#snippet projectCell(meeting: Meeting)}
  {#if meeting.project_id && meeting.project_name}
    <a
      href={`/projects/${meeting.project_id}`}
      class="block truncate text-text-muted hover:text-brand">{meeting.project_name}</a
    >
  {:else}
    <span class="text-text-muted">—</span>
  {/if}
{/snippet}

{#snippet ownerCell(meeting: Meeting)}
  <span class="block truncate text-text-muted">{meeting.owner_name ?? "—"}</span>
{/snippet}

{#snippet mobileRow(meeting: Meeting)}
  <a href={`/meetings/${meeting.id}`} class="min-w-0 flex-1">
    <span class="block truncate text-sm font-medium text-text">{meeting.title}</span>
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {meeting.company_name ? `${meeting.company_name} · ` : ""}{fmtDateTime(meeting.occurred_at)}
    </span>
  </a>
  <MeetingStatusPill status={meeting.status} />
{/snippet}

{#snippet emptyState()}
  <div class="px-6 py-12 text-center">
    <Mic size={28} class="mx-auto mb-3 text-text-muted" />
    <p class="text-sm text-text-muted">
      {narrowed ? t("meetings.list.empty_filtered") : t("meetings.list.empty")}
    </p>
    {#if canRecord && !narrowed}
      <p class="mx-auto mt-2 max-w-md text-sm text-text-muted">{t("meetings.list.empty_hint")}</p>
    {/if}
  </div>
{/snippet}

<!-- A batch the API refused whole (an expired licence, a permission withdrawn mid-session)
     must say so: a confirm that closes over an unchanged list reads as a button that did
     nothing. -->
{#if form?.error}
  <p class="mb-3 text-sm text-red-600 dark:text-red-400" role="alert">{t(form.error)}</p>
{/if}

<BulkBar {selecting} bind:selected={bulkSelected} {...bulkConfig} />

<BulkResult result={form?.bulkResult} />

<DataTable
  rows={meetings}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
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
