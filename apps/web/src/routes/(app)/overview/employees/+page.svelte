<script lang="ts">
  /**
   * Overzicht → Medewerkers: hours, billable share, sign-off and what the billable hours were
   * worth, per colleague, over a period the tab row names (this month, last month, the quarter,
   * the year) or the two date fields spell out. Every figure opens the hours report filtered to
   * that person and that period (docs/UX.md Principle 7).
   */
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { sharePct } from "$lib/core/delta";
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { memberLabel } from "$lib/core/members";
  import { pageTitle } from "$lib/core/title";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import SummaryStrip from "$lib/core/ui/SummaryStrip.svelte";
  import { formatMinutes } from "$lib/modules/time/format";
  import { EMPLOYEE_PERIODS } from "$lib/modules/time/periods";

  import type { ComponentProps } from "svelte";

  type SummaryTile = ComponentProps<typeof SummaryStrip>["tiles"][number];

  let { data } = $props();

  // Validated pair (dataviz checks): billable #2563eb, non-billable #0d9488.
  const BILLABLE_COLOR = "#2563eb";
  const NON_BILLABLE_COLOR = "#0d9488";

  const rows = $derived(data.stats?.rows ?? []);
  const maxMinutes = $derived(Math.max(...rows.map((r) => r.minutes), 1));

  const memberName = (id: string) => {
    const m = data.members.find((mm) => mm.user_id === id);
    return m ? memberLabel(m) : "—";
  };

  const totals = $derived({
    minutes: rows.reduce((sum, r) => sum + r.minutes, 0),
    billable: rows.reduce((sum, r) => sum + r.billable_minutes, 0),
    approved: rows.reduce((sum, r) => sum + r.approved_minutes, 0),
    revenue: rows.reduce((sum, r) => sum + r.revenue, 0),
  });

  const reportHref = (userId?: string) => {
    const params = new URLSearchParams({
      date_from: data.filters.date_from,
      date_to: data.filters.date_to,
    });
    if (userId) params.set("user_id", userId);
    return `/overview/hours?${params.toString()}`;
  };

  const tiles = $derived.by((): SummaryTile[] => {
    if (rows.length === 0) return [];
    const open = totals.minutes - totals.approved;
    return [
      {
        key: "minutes",
        label_key: "time.overview.total.minutes",
        value: String(totals.minutes / 60),
        format: "hours",
        hint_key: "overview.employees.hint.people",
        hint_params: { count: rows.length },
        href: reportHref(),
      },
      {
        key: "billable",
        label_key: "time.overview.total.billable",
        value: String(totals.billable / 60),
        format: "hours",
        hint_key: "overview.hint.billable",
        hint_params: { pct: sharePct(totals.billable, totals.minutes) },
        href: reportHref(),
      },
      {
        key: "approved",
        label_key: "time.overview.status.approved",
        value: String(totals.approved / 60),
        format: "hours",
        tone: open > 0 ? "warn" : "neutral",
        hint_key:
          open > 0 ? "overview.employees.hint.open" : "overview.employees.hint.all_approved",
        hint_params: { hours: formatMinutes(open) },
        href: `${reportHref()}&status=open`,
      },
      // Nothing is a number: an org with no hourly rate has no worth to report, and a € 0 tile
      // would read as a verdict on the team rather than on the setup.
      ...(totals.revenue > 0
        ? [
            {
              key: "revenue",
              label_key: "overview.employees.value",
              value: String(totals.revenue),
              format: "money",
              hint_key: "overview.employees.hint.value",
            } satisfies SummaryTile,
          ]
        : []),
    ];
  });

  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    // A typed date is an explicit period; the preset pills go dark until one is pressed again.
    url.searchParams.delete("period");
    if (!url.searchParams.has("date_from"))
      url.searchParams.set("date_from", data.filters.date_from);
    if (!url.searchParams.has("date_to")) url.searchParams.set("date_to", data.filters.date_to);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }
  const pill = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<svelte:head>
  <title>{pageTitle(t("overview.employees.title"))}</title>
</svelte:head>

<PageHeader title={t("overview.employees.title")}>
  {#snippet subtitle()}{t("overview.employees.subtitle")}{/snippet}
</PageHeader>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <div class="flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    {#each EMPLOYEE_PERIODS as preset (preset)}
      <a
        href={`?period=${preset}`}
        class={pill(data.filters.period === preset)}
        data-sveltekit-noscroll
      >
        {t(`overview.period.${preset}`)}
      </a>
    {/each}
  </div>
  <div class="flex items-center gap-2">
    <div class="w-36">
      <DateInput
        name="_f_from"
        id="p-from"
        value={data.filters.date_from}
        onchange={(v) => setFilter("date_from", v)}
      />
    </div>
    <span class="text-xs text-text-muted">–</span>
    <div class="w-36">
      <DateInput
        name="_f_to"
        id="p-to"
        value={data.filters.date_to}
        onchange={(v) => setFilter("date_to", v)}
      />
    </div>
  </div>
</div>

<SummaryStrip {tiles} />

<section class="overflow-hidden rounded-xl border border-border bg-surface-raised">
  {#if rows.length === 0}
    <p class="p-8 text-center text-sm text-text-muted">{t("overview.employees.empty")}</p>
  {:else}
    <div
      class="hidden grid-cols-[1fr_auto_auto_auto] gap-x-6 border-b border-border px-4 py-2 text-xs font-medium text-text-muted sm:grid"
    >
      <span>{t("overview.employees.column.employee")}</span>
      <span class="text-right">{t("overview.employees.column.hours")}</span>
      <span class="text-right">{t("overview.employees.column.approved")}</span>
      <span class="text-right">{t("overview.employees.column.value")}</span>
    </div>
    <div class="divide-y divide-border">
      {#each rows as row (row.user_id)}
        {@const billablePct = sharePct(row.billable_minutes, row.minutes)}
        {@const approvedPct = sharePct(row.approved_minutes, row.minutes)}
        <div class="px-4 py-3">
          <div
            class="mb-1.5 grid grid-cols-[1fr_auto] items-baseline gap-x-6 sm:grid-cols-[1fr_auto_auto_auto]"
          >
            <a
              href={reportHref(row.user_id)}
              class="min-w-0 truncate text-sm font-medium text-text hover:text-brand"
            >
              {memberName(row.user_id)}
              <span class="ml-2 text-xs font-normal text-text-muted">
                {t("overview.employees.meta", { days: row.active_days, billable: billablePct })}
              </span>
            </a>
            <span class="text-right text-sm font-semibold tabular-nums text-text">
              {formatMinutes(row.minutes)}
            </span>
            <span
              class="hidden text-right text-sm tabular-nums sm:block {approvedPct < 100
                ? 'text-amber-700 dark:text-amber-400'
                : 'text-text-muted'}"
              title={formatMinutes(row.approved_minutes)}
            >
              {approvedPct}%
            </span>
            <span class="hidden text-right text-sm tabular-nums text-text sm:block">
              {row.revenue > 0 ? fmtMoney(row.revenue) : "—"}
            </span>
          </div>
          <div
            class="flex h-3 overflow-hidden rounded-full bg-surface"
            style="width: {Math.max(4, (row.minutes / maxMinutes) * 100)}%"
            title="{formatMinutes(row.billable_minutes)} / {formatMinutes(row.minutes)}"
          >
            <div class="h-full" style="width:{billablePct}%; background:{BILLABLE_COLOR}"></div>
            <div
              class="h-full flex-1"
              style="background:{NON_BILLABLE_COLOR}; margin-left:2px"
            ></div>
          </div>
        </div>
      {/each}
    </div>
    <div
      class="flex items-center gap-4 border-t border-border bg-surface/60 px-4 py-2 text-xs text-text-muted"
    >
      <span class="flex items-center gap-1.5">
        <span class="h-2.5 w-2.5 rounded-sm" style="background:{BILLABLE_COLOR}"></span>
        {t("time.billable")}
      </span>
      <span class="flex items-center gap-1.5">
        <span class="h-2.5 w-2.5 rounded-sm" style="background:{NON_BILLABLE_COLOR}"></span>
        {t("time.not_billable")}
      </span>
      <span class="ml-auto">{t("overview.employees.value_note")}</span>
    </div>
  {/if}
</section>
