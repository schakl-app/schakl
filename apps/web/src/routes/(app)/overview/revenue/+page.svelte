<script lang="ts">
  /**
   * Overzicht → Omzet. Two answers to "how much did we turn over", and the page keeps them
   * apart because they are different numbers with a gap worth reading:
   *
   * - **Gefactureerd** is the ledger's — what was invoiced in the year, per month against the
   *   year before, per client and per kind, excl. or incl. tax on one toggle. Drawn where the
   *   invoicing module is on and this manager may read the agency's turnover (#266's `:any`).
   * - **Waarde van geboekte uren** is what the billable hours were worth at each employee's
   *   rate, invoiced or not (#226) — the figure this tab has always shown, kept whole below.
   */
  import { delta, sharePct } from "$lib/core/delta";
  import { fmtMoney } from "$lib/core/format";
  import { t, tn } from "$lib/core/i18n";
  import { stateFromTone, stateTextClass } from "$lib/core/state";
  import { pageTitle } from "$lib/core/title";
  import Card from "$lib/core/ui/Card.svelte";
  import DonutChart from "$lib/core/ui/charts/DonutChart.svelte";
  import MonthlyComparisonChart from "$lib/core/ui/charts/MonthlyComparisonChart.svelte";
  import { BAND_HEADING } from "$lib/core/ui/headings";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import RankList, { type RankRow } from "$lib/core/ui/RankList.svelte";
  import { stateIcon } from "$lib/core/ui/state-icons";
  import SummaryStrip from "$lib/core/ui/SummaryStrip.svelte";
  import YearStepper from "$lib/core/ui/YearStepper.svelte";

  import type { ComponentProps } from "svelte";

  type SummaryTile = ComponentProps<typeof SummaryStrip>["tiles"][number];

  let { data } = $props();

  const year = $derived(data.year);
  const previousYear = $derived(data.year - 1);
  const incl = $derived(data.vat === "incl");
  const invoiced = $derived(data.invoiced);
  const hoursValue = $derived(data.hoursValue);

  const hrefFor = (y: number, vat = data.vat) => `?year=${y}${vat === "incl" ? "&vat=incl" : ""}`;
  const pill = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;

  // --- the ledger -----------------------------------------------------------------
  const invoicedTotal = $derived(invoiced ? (incl ? invoiced.total_incl : invoiced.total_excl) : 0);
  const invoicedPrevious = $derived(
    invoiced ? (incl ? invoiced.previous_incl : invoiced.previous_excl) : 0,
  );
  const invoicedDelta = $derived(delta(invoicedTotal, invoicedPrevious));

  const ledgerTiles = $derived.by((): SummaryTile[] => {
    if (!invoiced) return [];
    const out: SummaryTile[] = [
      {
        key: "invoiced",
        label_key: incl ? "overview.tile.invoiced_incl" : "overview.tile.invoiced_excl",
        value: String(invoicedTotal),
        format: "money",
        tone: invoicedDelta?.tone,
        hint_key: invoicedDelta ? "overview.hint.vs_previous" : "overview.hint.no_previous",
        hint_params: { delta: invoicedDelta?.text ?? "", year: previousYear },
        href: "/invoices",
      },
      {
        key: "previous",
        label_key: "overview.revenue.tile.previous",
        value: String(invoicedPrevious),
        format: "money",
        hint_key: "overview.revenue.tile.previous_hint",
        hint_params: { year: previousYear },
        href: `/overview/revenue${hrefFor(previousYear)}`,
      },
      {
        key: "tax",
        label_key: "overview.revenue.tile.tax",
        value: String(invoiced.total_tax),
        format: "money",
        hint_key: "overview.revenue.tile.tax_hint",
        hint_params: { excl: fmtMoney(invoiced.total_excl), incl: fmtMoney(invoiced.total_incl) },
      },
      {
        key: "paid",
        label_key: "overview.revenue.tile.paid",
        value: String(invoiced.paid_incl),
        format: "money",
        hint_key: "overview.revenue.tile.paid_hint",
        hint_params: { pct: sharePct(invoiced.paid_incl, invoiced.total_incl) },
        href: "/invoices?status=paid",
      },
      {
        key: "outstanding",
        label_key: "overview.tile.outstanding",
        value: String(invoiced.outstanding_incl),
        format: "money",
        tone: invoiced.outstanding_count > 0 ? "warn" : "neutral",
        hint_key: "overview.revenue.tile.outstanding_hint",
        hint_params: { count: invoiced.outstanding_count },
        href: "/invoices?status=open",
      },
    ];
    // Nothing is a number (SummaryStrip): a year with no credit notes draws no tile for them.
    if (invoiced.credited_excl > 0) {
      out.push({
        key: "credited",
        label_key: "overview.revenue.tile.credited",
        value: String(invoiced.credited_excl),
        format: "money",
        hint_key: "overview.revenue.tile.credited_hint",
        href: "/invoices",
      });
    }
    return out;
  });

  const kindRows = $derived.by((): RankRow[] => {
    if (!invoiced) return [];
    return invoiced.by_kind
      .filter((k) => k.excl > 0 || k.previous_excl > 0)
      .map((k) => {
        const d = delta(k.excl, k.previous_excl);
        return {
          key: k.kind,
          label: t(`overview.revenue.kind.${k.kind}`),
          value: k.excl,
          valueText: fmtMoney(k.excl),
          meta: t("overview.home.share", { pct: sharePct(k.excl, invoiced.total_excl) }),
          badge: d ? { text: d.text, tone: d.tone } : null,
        };
      });
  });

  const clientRows = $derived.by(() => {
    if (!invoiced) return [];
    const total = incl ? invoiced.total_incl : invoiced.total_excl;
    const rows = invoiced.top_clients.map((c) => {
      const current = incl ? c.incl : c.excl;
      return {
        key: c.company_id,
        name: c.name,
        href: `/companies/${c.company_id}`,
        current,
        excl: c.excl,
        incl: c.incl,
        share: sharePct(current, total),
        previous: c.previous_excl,
        delta: delta(c.excl, c.previous_excl),
      };
    });
    const other = incl ? invoiced.other_incl : invoiced.other_excl;
    if (other > 0 || invoiced.other_previous_excl > 0) {
      rows.push({
        key: "other",
        name: t("overview.revenue.other"),
        href: "",
        current: other,
        excl: invoiced.other_excl,
        incl: invoiced.other_incl,
        share: sharePct(other, total),
        previous: invoiced.other_previous_excl,
        delta: delta(invoiced.other_excl, invoiced.other_previous_excl),
      });
    }
    return rows;
  });

  // --- the hours' worth -----------------------------------------------------------
  const companyName = (id?: string | null) =>
    data.companies.find((c) => c.id === id)?.name ?? t("time.general");
  const hoursDelta = $derived(
    hoursValue ? delta(hoursValue.total_current, hoursValue.total_previous) : null,
  );
  const hoursTiles = $derived.by((): SummaryTile[] => {
    if (!hoursValue) return [];
    return [
      {
        key: "hours_current",
        label_key: "overview.revenue.hours_tile.current",
        value: String(hoursValue.total_current),
        format: "money",
        tone: hoursDelta?.tone,
        hint_key: hoursDelta ? "overview.hint.vs_previous" : "overview.hint.no_previous",
        hint_params: { delta: hoursDelta?.text ?? "", year: previousYear },
        href: `/overview/hours?date_from=${year}-01-01&date_to=${year}-12-31`,
      },
      {
        key: "hours_previous",
        label_key: "overview.revenue.tile.previous",
        value: String(hoursValue.total_previous),
        format: "money",
        hint_key: "overview.revenue.hours_tile.previous_hint",
        hint_params: { year: previousYear },
      },
      ...(invoiced
        ? [
            {
              key: "gap",
              label_key: "overview.revenue.hours_tile.gap",
              value: String(invoiced.total_excl - hoursValue.total_current),
              format: "money",
              hint_key: "overview.revenue.hours_tile.gap_hint",
            } satisfies SummaryTile,
          ]
        : []),
    ];
  });
  const slices = $derived(
    (hoursValue?.top_clients ?? []).map((c) => ({
      label: companyName(c.company_id),
      value: c.revenue,
      href: c.company_id ? `/companies/${c.company_id}` : undefined,
    })),
  );
</script>

<svelte:head>
  <title>{pageTitle(t("overview.revenue.title"))}</title>
</svelte:head>

<PageHeader title={t("overview.revenue.title")}>
  {#snippet subtitle()}{t("overview.revenue.subtitle", { year, previous: previousYear })}{/snippet}
  {#snippet actions()}
    <div class="flex flex-wrap items-center gap-3">
      {#if invoiced}
        <div class="flex items-center gap-1" data-sveltekit-preload-data="hover">
          <a href={hrefFor(year, "excl")} class={pill(!incl)} data-sveltekit-noscroll>
            {t("overview.revenue.vat.excl")}
          </a>
          <a href={hrefFor(year, "incl")} class={pill(incl)} data-sveltekit-noscroll>
            {t("overview.revenue.vat.incl")}
          </a>
        </div>
      {/if}
      <YearStepper {year} hrefFor={(y) => hrefFor(y)} />
    </div>
  {/snippet}
</PageHeader>

{#if invoiced}
  <SummaryStrip tiles={ledgerTiles} />

  <Card kind="panel" title={t("overview.revenue.invoiced_monthly")} class="mb-4">
    <MonthlyComparisonChart
      current={incl ? invoiced.months_incl : invoiced.months_excl}
      previous={incl ? invoiced.months_previous_incl : invoiced.months_previous_excl}
      currentLabel={String(year)}
      previousLabel={String(previousYear)}
    />
    <p class="mt-3 text-xs text-text-muted">
      {tn("overview.revenue.documents_note", invoiced.invoice_count, { year })}
    </p>
  </Card>

  <div class="mb-8 grid gap-4 lg:grid-cols-3">
    <Card kind="panel" title={t("overview.revenue.by_kind")}>
      <RankList rows={kindRows} emptyText={t("overview.revenue.empty_invoiced", { year })} />
      {#if kindRows.length > 0}
        <p class="mt-3 text-xs text-text-muted">{t("overview.revenue.by_kind_note")}</p>
      {/if}
    </Card>

    <Card kind="panel" title={t("overview.revenue.top_clients", { year })} class="lg:col-span-2">
      {#if clientRows.length === 0}
        <p class="text-sm text-text-muted">{t("overview.revenue.empty_invoiced", { year })}</p>
      {:else}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-text-muted">
                <th class="pb-2 font-medium">{t("overview.revenue.column.client")}</th>
                <th class="pb-2 text-right font-medium">
                  {incl ? t("overview.revenue.vat.incl") : t("overview.revenue.vat.excl")}
                </th>
                <th class="pb-2 text-right font-medium">{t("overview.revenue.column.share")}</th>
                <th class="pb-2 text-right font-medium">{previousYear}</th>
                <th class="pb-2 text-right font-medium">{t("overview.revenue.column.change")}</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-border">
              {#each clientRows as row (row.key)}
                {@const state = stateFromTone(row.delta?.tone)}
                {@const Mark = row.delta ? stateIcon(state) : null}
                <tr>
                  <td class="py-2 pr-3">
                    {#if row.href}
                      <a href={row.href} class="font-medium text-text hover:text-brand"
                        >{row.name}</a
                      >
                    {:else}
                      <span class="text-text-muted">{row.name}</span>
                    {/if}
                  </td>
                  <td class="py-2 text-right font-semibold tabular-nums text-text"
                    >{fmtMoney(row.current)}</td
                  >
                  <td class="py-2 text-right tabular-nums text-text-muted">{row.share}%</td>
                  <td class="py-2 text-right tabular-nums text-text-muted">
                    {fmtMoney(row.previous)}
                  </td>
                  <td class="py-2 text-right tabular-nums">
                    {#if row.delta}
                      <span class="inline-flex items-center gap-0.5 {stateTextClass(state)}">
                        {#if Mark}<Mark size={13} aria-hidden="true" />{/if}
                        {row.delta.text}
                      </span>
                    {:else}
                      <span class="text-text-muted">—</span>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if incl}
          <p class="mt-3 text-xs text-text-muted">{t("overview.revenue.previous_excl_note")}</p>
        {/if}
      {/if}
    </Card>
  </div>
{:else if !data.invoicing}
  <p class="mb-6 text-sm text-text-muted">{t("overview.revenue.no_invoicing")}</p>
{/if}

<!-- The hours' worth. A section, not a second page: the two figures are read side by side. -->
<section id="hours-value" class="scroll-mt-4">
  <h2 class="{BAND_HEADING} mb-1">{t("overview.revenue.hours_value")}</h2>
  <p class="mb-4 text-sm text-text-muted">{t("overview.revenue.hours_value_intro")}</p>

  {#if hoursValue && hoursValue.total_current === 0 && hoursValue.total_previous === 0}
    <!-- Nothing is a number: an org with no hourly rate has no worth to chart, and three € 0
         tiles over an empty axis would read as a verdict on the year rather than on the setup. -->
    <p class="text-sm text-text-muted">{t("overview.revenue.empty")}</p>
    <p class="mt-1 text-xs text-text-muted">{t("overview.revenue.rate_hint")}</p>
  {:else if hoursValue}
    <SummaryStrip tiles={hoursTiles} />

    <div class="grid gap-4 lg:grid-cols-3">
      <Card kind="panel" title={t("overview.revenue.monthly")} class="lg:col-span-2">
        <MonthlyComparisonChart
          current={hoursValue.months_current}
          previous={hoursValue.months_previous}
          currentLabel={String(year)}
          previousLabel={String(previousYear)}
        />
      </Card>
      <Card kind="panel" title={t("overview.revenue.top_clients", { year })}>
        {#if slices.length === 0}
          <p class="text-sm text-text-muted">{t("overview.revenue.empty")}</p>
        {:else}
          <DonutChart
            {slices}
            otherLabel={t("overview.revenue.other")}
            otherValue={hoursValue.other_revenue}
            centerLabel={t("overview.revenue.center_label")}
          />
        {/if}
        <p class="mt-4 text-xs text-text-muted">{t("overview.revenue.rate_hint")}</p>
      </Card>
    </div>
  {/if}
</section>
