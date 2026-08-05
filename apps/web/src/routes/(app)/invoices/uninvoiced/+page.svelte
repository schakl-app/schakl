<script lang="ts">
  /**
   * "Nog te factureren" — everything the agency still has to invoice, in one page (#277, #302).
   *
   * It began as the uninvoiced-**hours** report. Hours were never the whole answer: an agency
   * also owes itself the agreement periods and the domain renewals no document covers yet, and
   * those were reachable only per client, from inside the invoice editor's picker. Arrears were
   * the worst of it — the cycle cron advances whether or not it drafted anything, so a period
   * whose automation was off simply sat there with nothing to surface it.
   *
   * Three tiles across the top, always counting all three sources, and a segmented control
   * below choosing which one the table details (docs/UX.md §7: tiles filter the list they
   * count). Read-only, like the report it grew out of: every row and every client header links
   * to `/invoices/new?company=…`, which is where building actually happens.
   */
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtMoney, fmtMonthYear, fmtNumber, fmtNumericDate, fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import { BACKLOG_COLUMNS, UNINVOICED_COLUMNS } from "$lib/modules/invoicing/columns";

  let { data } = $props();

  type Entry = NonNullable<typeof data.report>["entries"][number];
  type Item = NonNullable<typeof data.backlog>["items"][number];
  type Row = Entry | Item;

  const HOUR_GROUPS = ["day", "week", "month", "year", "company", "project", "user"] as const;
  const BACKLOG_GROUPS = ["company", "month", "source"] as const;
  const SOURCES = ["hours", "subscription", "domain"] as const;

  const isHours = $derived(data.source === "hours");

  // The grouped dimension is the section header: its own column would repeat the header on
  // every row (docs/UX.md #38: a board grouped by X declares no X column). Week/month/year
  // keep the date column — a section says the month, a row still has its day.
  const HIDE_BY_GROUP: Record<string, string> = {
    day: "date",
    company: "company",
    project: "project",
    user: "user",
  };
  //: The backlog's own version. `month` keeps the period column: the section says which month
  //: the renewal falls in, the row still has to say which span it covers.
  const BACKLOG_HIDE_BY_GROUP: Record<string, string> = { company: "company" };

  function navigate(changes: Record<string, string | null>) {
    const url = new URL(page.url);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    void goto(url, { keepFocus: true, noScroll: true });
  }

  //: Switching source drops `group`: the two vocabularies barely overlap, and carrying
  //: "project" into the renewal list would land on a grouping it does not have.
  const setSource = (value: string) =>
    navigate({ source: value === "hours" ? null : value, group: null });
  const setGroup = (value: string) => navigate({ group: value === "company" ? null : value });

  const sourceItems = $derived(
    SOURCES.map((s) => ({ value: s, label: t(`invoicing.backlog.source.${s}`) })),
  );
  const groupItems = $derived(
    isHours
      ? HOUR_GROUPS.map((g) => ({ value: g, label: t(`invoicing.uninvoiced.group.${g}`) }))
      : BACKLOG_GROUPS.map((g) => ({ value: g, label: t(`invoicing.backlog.group.${g}`) })),
  );

  // Date buckets arrive as sortable keys; the viewer's locale renders them here.
  function bucketLabel(key: string, label: string | null): string {
    if (isHours) {
      if (data.group === "day") return fmtNumericDate(key);
      if (data.group === "week") {
        const [year, week] = key.split("-W");
        return t("invoicing.uninvoiced.week", { week: String(Number(week)), year });
      }
      if (data.group === "month") return fmtMonthYear(key);
      if (data.group === "year") return key;
    } else {
      if (data.group === "month") return fmtMonthYear(key);
      if (data.group === "source") return t(`invoicing.backlog.source.${key}`);
    }
    return label || "—";
  }

  //: The rows on screen, and their subtotals — both the API's, already narrowed to the chosen
  //: source. Re-summing here would mean summing a **capped** list, which is the truncated
  //: total the endpoint caps its detail precisely to avoid.
  const backlogItems = $derived(data.backlog?.items ?? []);
  const rows = $derived<Row[]>(isHours ? (data.report?.entries ?? []) : backlogItems);

  const apiGroups = $derived(
    isHours
      ? (data.report?.groups ?? []).map((g) => ({
          key: g.key,
          label: g.label ?? null,
          count: 0,
          amount: Number(g.amount),
          minutes: g.minutes,
        }))
      : (data.backlog?.groups ?? []).map((g) => ({
          key: g.key,
          label: g.label,
          count: g.count,
          amount: Number(g.amount),
          minutes: 0,
        })),
  );
  const summaryByKey = $derived(new Map(apiGroups.map((g) => [g.key, g])));
  const sections = $derived(
    apiGroups.map((g) => ({
      key: g.key,
      label: bucketLabel(g.key, g.label),
      collapsible: true,
    })),
  );

  // Collapse is per visit, not persisted: date sections churn with the calendar and entity
  // sections with the backlog, so saved keys would mostly be stale by the next visit.
  let collapsed = $state<string[]>([]);
  $effect(() => {
    void data.group;
    void data.source;
    collapsed = [];
  });

  //: The tiles: what each source is worth in total, whichever one is detailed below. The
  //: recurring two come from `totals_by_source`, which the API computes over **everything**
  //: regardless of the `source` filter — precisely so a narrowed page still summarises all of
  //: it. Hours are the other endpoint's own total.
  const sourceTotal = (source: string) =>
    Number(data.backlog?.totals_by_source?.[source]?.amount ?? 0);
  const tiles = $derived([
    {
      source: "hours",
      label: t("invoicing.backlog.tile.hours"),
      amount: Number(data.report?.total_amount ?? 0),
    },
    {
      source: "subscription",
      label: t("invoicing.backlog.tile.subscriptions"),
      amount: sourceTotal("subscription"),
    },
    {
      source: "domain",
      label: t("invoicing.backlog.tile.domains"),
      amount: sourceTotal("domain"),
    },
  ]);
  const grandTotal = $derived(tiles.reduce((sum, tile) => sum + tile.amount, 0));

  const hours = (minutes: number) => fmtNumber(minutes / 60, 2);
  const newInvoiceHref = (companyId: string) => `/invoices/new?company=${companyId}`;
  const rowCompanyId = (row: Row) => row.company_id ?? "";

  //: `truncated` is the API's, over the **whole** backlog rather than the narrowed view, so it
  //: stays honest: rows of the other source were dropped from what was already a capped list.
  const truncated = $derived(
    isHours ? Boolean(data.report?.truncated) : Boolean(data.backlog?.truncated),
  );
  const shown = $derived(isHours ? (data.report?.entries.length ?? 0) : backlogItems.length);
  const totalCount = $derived(
    isHours ? (data.report?.total_count ?? 0) : (data.backlog?.total_count ?? 0),
  );

  const table = createTableLayout<Row>({
    all: () =>
      isHours
        ? UNINVOICED_COLUMNS.filter((c) => c.key !== HIDE_BY_GROUP[data.group])
        : BACKLOG_COLUMNS.filter((c) => c.key !== BACKLOG_HIDE_BY_GROUP[data.group]),
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
      name: nameCell,
      period: periodCell,
      automation: automationCell,
    }),
    totals: () => (isHours ? { hours: hoursTotal, amount: amountTotal } : { amount: amountTotal }),
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

<div class="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
  {#each tiles as tile (tile.source)}
    <button
      type="button"
      class="rounded-xl border p-3 text-left transition hover:border-brand {data.source ===
      tile.source
        ? 'border-brand bg-brand/5'
        : 'border-border bg-surface-raised'}"
      aria-pressed={data.source === tile.source}
      onclick={() => setSource(tile.source)}
    >
      <span class="block text-xs font-medium uppercase tracking-wide text-text-muted"
        >{tile.label}</span
      >
      <span class="mt-1 block text-lg font-semibold tabular-nums text-text"
        >{fmtMoney(tile.amount)}</span
      >
    </button>
  {/each}
  <div class="rounded-xl border border-border bg-surface-raised p-3">
    <span class="block text-xs font-medium uppercase tracking-wide text-text-muted"
      >{t("invoicing.backlog.tile.total")}</span
    >
    <span class="mt-1 block text-lg font-semibold tabular-nums text-text"
      >{fmtMoney(grandTotal)}</span
    >
  </div>
</div>

<div class="mb-4 flex flex-wrap items-center gap-2">
  <label class="text-sm text-text-muted" for="uninvoiced-source"
    >{t("invoicing.backlog.source")}</label
  >
  <div class="w-44">
    <Combobox
      items={sourceItems}
      name="_source"
      value={data.source}
      onselect={(v) => setSource(v)}
      id="uninvoiced-source"
    />
  </div>
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

{#if !isHours && backlogItems.length > 0}
  <p class="mb-3 rounded-lg border border-border px-3 py-2 text-xs text-text-muted">
    {t("invoicing.backlog.arrears_hint")}
    {#if data.backlog?.org_auto_invoice_mode}
      {" "}{t("invoicing.backlog.org_default", {
        mode: t(`invoicing.auto.${data.backlog.org_auto_invoice_mode}`),
      })}
    {/if}
  </p>
{/if}

{#if truncated}
  <p class="mb-3 text-sm text-amber-700 dark:text-amber-400">
    {t(isHours ? "invoicing.uninvoiced.truncated" : "invoicing.backlog.truncated", {
      shown: String(shown),
      total: String(totalCount),
    })}
  </p>
{/if}

{#snippet dateCell(row: Row)}
  <span class="tabular-nums text-text">{fmtNumericDate((row as Entry).entry_date)}</span>
{/snippet}

{#snippet companyCell(row: Row)}
  {#if row.company_id}
    <a href="/companies/{row.company_id}" class="text-text-muted hover:text-brand"
      >{row.company_name}</a
    >
  {:else}<span class="text-text-muted">—</span>{/if}
{/snippet}

{#snippet projectCell(row: Row)}
  <span class="text-text-muted">{(row as Entry).project_name ?? "—"}</span>
{/snippet}

{#snippet userCell(row: Row)}
  <span class="text-text-muted">{(row as Entry).user_name ?? "—"}</span>
{/snippet}

{#snippet descriptionCell(row: Row)}
  <span class="text-text-muted">{(row as Entry).description || "—"}</span>
{/snippet}

{#snippet hoursCell(row: Row)}
  <span class="tabular-nums text-text">{hours((row as Entry).minutes)}</span>
{/snippet}

{#snippet amountCell(row: Row)}
  <span class="tabular-nums text-text">{fmtMoney(Number(row.amount))}</span>
{/snippet}

{#snippet nameCell(row: Row)}
  <span class="text-text">{(row as Item).name}</span>
{/snippet}

{#snippet periodCell(row: Row)}
  {@const item = row as Item}
  <span class="tabular-nums text-text-muted"
    >{fmtPeriod(item.period_start ?? item.period_end, item.period_end)}</span
  >
  {#if item.future}
    <span class="ml-1 text-xs text-text-muted">· {t("invoicing.backlog.future")}</span>
  {/if}
{/snippet}

{#snippet automationCell(row: Row)}
  <span class="text-text-muted">{t(`invoicing.auto.${(row as Item).auto_mode}`)}</span>
{/snippet}

{#snippet hoursTotal()}
  {hours(data.report?.total_minutes ?? 0)}
{/snippet}

{#snippet amountTotal()}
  {fmtMoney(
    isHours
      ? Number(data.report?.total_amount ?? 0)
      : backlogItems.reduce((sum, i) => sum + Number(i.amount), 0),
  )}
{/snippet}

{#snippet groupSummary(key: string)}
  {@const g = summaryByKey.get(key)}
  {#if g}
    <span class="inline-flex items-center gap-3 text-xs tabular-nums text-text-muted">
      {#if isHours}
        <span>{t("invoicing.uninvoiced.hours_short", { hours: hours(g.minutes) })}</span>
      {:else}
        <span
          >{g.count === 1
            ? t("invoicing.backlog.count_items_one")
            : t("invoicing.backlog.count_items", { count: String(g.count) })}</span
        >
      {/if}
      <span class="font-medium text-text">{fmtMoney(g.amount)}</span>
      {#if data.group === "company" && key && data.canWrite}
        <a
          href={newInvoiceHref(key)}
          data-sveltekit-preload-data="hover"
          class="font-medium text-brand hover:underline"
          >{t("invoicing.uninvoiced.create_invoice")}</a
        >
      {/if}
    </span>
  {/if}
{/snippet}

{#snippet mobileRow(row: Row)}
  {#if isHours}
    {@const entry = row as Entry}
    <span class="block truncate text-sm font-medium text-text"
      >{entry.company_name ?? "—"}{entry.project_name ? ` · ${entry.project_name}` : ""}</span
    >
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {fmtNumericDate(entry.entry_date)} · {t("invoicing.uninvoiced.hours_short", {
        hours: hours(entry.minutes),
      })} · {fmtMoney(Number(entry.amount))}
    </span>
  {:else}
    {@const item = row as Item}
    <span class="block truncate text-sm font-medium text-text"
      >{item.name}{item.company_name ? ` · ${item.company_name}` : ""}</span
    >
    <span class="mt-0.5 block truncate text-xs text-text-muted">
      {fmtPeriod(item.period_start ?? item.period_end, item.period_end)} · {fmtMoney(
        Number(item.amount),
      )}
    </span>
  {/if}
{/snippet}

{#snippet emptyState()}
  <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
    {t(isHours ? "invoicing.uninvoiced.empty" : "invoicing.backlog.empty")}
  </p>
{/snippet}

<DataTable
  {rows}
  columns={table.columns}
  widths={table.widths}
  locale={data.locale}
  groups={sections}
  groupBy={(row) =>
    isHours
      ? (row as Entry).group_key
      : data.group === "month"
        ? (row as Item).period_end.slice(0, 7)
        : data.group === "source"
          ? (row as Item).source
          : ((row as Item).company_id ?? "")}
  {groupSummary}
  {collapsed}
  oncollapse={(keys) => (collapsed = keys)}
  {mobileRow}
  empty={emptyState}
  rowHref={data.canWrite
    ? (row) => (rowCompanyId(row) ? newInvoiceHref(rowCompanyId(row)) : "")
    : undefined}
  onRowClick={data.canWrite
    ? (row) => {
        if (rowCompanyId(row)) void goto(newInvoiceHref(rowCompanyId(row)));
      }
    : undefined}
  onresize={table.onResize}
/>
