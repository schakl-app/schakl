<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtMoney, fmtMonthYear, fmtNumber, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import { UNINVOICED_COLUMNS } from "$lib/modules/invoicing/columns";

  let { data } = $props();

  type Entry = NonNullable<typeof data.report>["entries"][number];

  const GROUP_OPTIONS = ["day", "week", "month", "year", "company", "project", "user"] as const;

  // The grouped dimension is the section header: its own column would repeat the header on
  // every row (docs/UX.md #38: a board grouped by X declares no X column). Week/month/year
  // keep the date column — a section says the month, a row still has its day.
  const HIDE_BY_GROUP: Record<string, string> = {
    day: "date",
    company: "company",
    project: "project",
    user: "user",
  };

  function setGroup(value: string) {
    const url = new URL(page.url);
    if (value && value !== "company") url.searchParams.set("group", value);
    else url.searchParams.delete("group");
    void goto(url, { keepFocus: true, noScroll: true });
  }

  const groupItems = $derived(
    GROUP_OPTIONS.map((g) => ({ value: g, label: t(`invoicing.uninvoiced.group.${g}`) })),
  );

  // Date buckets arrive as sortable keys; the viewer's locale renders them here.
  function bucketLabel(key: string, label: string | null): string {
    if (data.group === "day") return fmtNumericDate(key);
    if (data.group === "week") {
      const [year, week] = key.split("-W");
      return t("invoicing.uninvoiced.week", { week: String(Number(week)), year });
    }
    if (data.group === "month") return fmtMonthYear(key);
    if (data.group === "year") return key;
    return label || "—";
  }

  const reportGroups = $derived(data.report?.groups ?? []);
  const summaryByKey = $derived(new Map(reportGroups.map((g) => [g.key, g])));
  const sections = $derived(
    reportGroups.map((g) => ({
      key: g.key,
      label: bucketLabel(g.key, g.label ?? null),
      collapsible: true,
    })),
  );

  // Collapse is per visit, not persisted: date sections churn with the calendar and entity
  // sections with the backlog, so saved keys would mostly be stale by the next visit.
  let collapsed = $state<string[]>([]);
  $effect(() => {
    void data.group;
    collapsed = [];
  });

  const hours = (minutes: number) => fmtNumber(minutes / 60, 2);
  const newInvoiceHref = (companyId: string) => `/invoices/new?company=${companyId}`;

  const table = createTableLayout<Entry>({
    all: () => UNINVOICED_COLUMNS.filter((c) => c.key !== HIDE_BY_GROUP[data.group]),
    pref: () => data.table.pref,
    sort: () => null,
    cells: () => ({
      date: dateCell,
      company: companyCell,
      project: projectCell,
      user: userCell,
      description: descriptionCell,
      hours: hoursCell,
      amount: amountCell,
    }),
    totals: () => ({ hours: hoursTotal, amount: amountTotal }),
  });
</script>

<svelte:head>
  <title>{pageTitle(navLabel("invoicing.uninvoiced", t("invoicing.uninvoiced.title")))}</title>
</svelte:head>

<div class="mb-1 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">
    {navLabel("invoicing.uninvoiced", t("invoicing.uninvoiced.title"))}
  </h1>
</div>
<p class="mb-4 text-sm text-text-muted">{t("invoicing.uninvoiced.subtitle")}</p>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <label class="text-sm text-text-muted" for="uninvoiced-group"
    >{t("invoicing.uninvoiced.group_by")}</label
  >
  <div class="w-44">
    <Combobox
      items={groupItems}
      name="_group"
      value={data.group}
      onselect={(v) => setGroup(v)}
      id="uninvoiced-group"
    />
  </div>
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

{#if data.report?.truncated}
  <p class="mb-3 text-sm text-amber-700 dark:text-amber-400">
    {t("invoicing.uninvoiced.truncated", {
      shown: String(data.report.entries.length),
      total: String(data.report.total_count),
    })}
  </p>
{/if}

{#snippet dateCell(entry: Entry)}
  <span class="tabular-nums text-text">{fmtNumericDate(entry.entry_date)}</span>
{/snippet}

{#snippet companyCell(entry: Entry)}
  {#if entry.company_id}
    <a href="/companies/{entry.company_id}" class="text-text-muted hover:text-brand"
      >{entry.company_name}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet projectCell(entry: Entry)}
  <span class="text-text-muted">{entry.project_name ?? "—"}</span>
{/snippet}

{#snippet userCell(entry: Entry)}
  <span class="text-text-muted">{entry.user_name ?? "—"}</span>
{/snippet}

{#snippet descriptionCell(entry: Entry)}
  <span class="text-text-muted">{entry.description || "—"}</span>
{/snippet}

{#snippet hoursCell(entry: Entry)}
  <span class="tabular-nums text-text">{hours(entry.minutes)}</span>
{/snippet}

{#snippet amountCell(entry: Entry)}
  <span class="tabular-nums text-text">{fmtMoney(Number(entry.amount))}</span>
{/snippet}

{#snippet hoursTotal()}
  {hours(data.report?.total_minutes ?? 0)}
{/snippet}

{#snippet amountTotal()}
  {fmtMoney(Number(data.report?.total_amount ?? 0))}
{/snippet}

{#snippet groupSummary(key: string)}
  {@const g = summaryByKey.get(key)}
  {#if g}
    <span class="inline-flex items-center gap-3 text-xs tabular-nums text-text-muted">
      <span>{t("invoicing.uninvoiced.hours_short", { hours: hours(g.minutes) })}</span>
      <span class="font-medium text-text">{fmtMoney(Number(g.amount))}</span>
      {#if data.group === "company" && g.key && data.canWrite}
        <a
          href={newInvoiceHref(g.key)}
          data-sveltekit-preload-data="hover"
          class="font-medium text-brand hover:underline"
          >{t("invoicing.uninvoiced.create_invoice")}</a
        >
      {/if}
    </span>
  {/if}
{/snippet}

{#snippet mobileRow(entry: Entry)}
  <span class="block truncate text-sm font-medium text-text"
    >{entry.company_name ?? "—"}{entry.project_name ? ` · ${entry.project_name}` : ""}</span
  >
  <span class="mt-0.5 block truncate text-xs text-text-muted">
    {fmtNumericDate(entry.entry_date)} · {t("invoicing.uninvoiced.hours_short", {
      hours: hours(entry.minutes),
    })} · {fmtMoney(Number(entry.amount))}
  </span>
{/snippet}

{#snippet emptyState()}
  <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
    {t("invoicing.uninvoiced.empty")}
  </p>
{/snippet}

<DataTable
  rows={data.report?.entries ?? []}
  columns={table.columns}
  widths={table.widths}
  locale={data.locale}
  groups={sections}
  groupBy={(entry) => entry.group_key}
  {groupSummary}
  {collapsed}
  oncollapse={(keys) => (collapsed = keys)}
  {mobileRow}
  empty={emptyState}
  rowHref={data.canWrite
    ? (entry) => (entry.company_id ? newInvoiceHref(entry.company_id) : "")
    : undefined}
  onRowClick={data.canWrite
    ? (entry) => {
        if (entry.company_id) void goto(newInvoiceHref(entry.company_id));
      }
    : undefined}
  onresize={table.onResize}
/>
