<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import { formatMinutes } from "$lib/modules/time/format";

  let { data } = $props();

  // Validated pair (dataviz checks): billable #2563eb, non-billable #0d9488.
  const BILLABLE_COLOR = "#2563eb";
  const NON_BILLABLE_COLOR = "#0d9488";

  const rows = $derived(data.stats?.rows ?? []);
  const maxMinutes = $derived(Math.max(...rows.map((r) => r.minutes), 1));

  const memberName = (id: string) => {
    const m = data.members.find((mm) => mm.user_id === id);
    return m ? m.full_name || m.email : "—";
  };

  const totals = $derived({
    minutes: rows.reduce((sum, r) => sum + r.minutes, 0),
    billable: rows.reduce((sum, r) => sum + r.billable_minutes, 0),
    approved: rows.reduce((sum, r) => sum + r.approved_minutes, 0),
  });
  const pct = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0);

  function setFilter(key: string, value: string) {
    const url = new URL(page.url);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    void goto(url, { keepFocus: true, noScroll: true });
  }
</script>

<svelte:head>
  <title>{t("overview.productivity.title")}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-end justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("overview.productivity.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("overview.productivity.subtitle")}</p>
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

<!-- Totals -->
<div class="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
  <div class="rounded-xl border border-border bg-surface-raised p-4">
    <p class="text-xs text-text-muted">{t("time.overview.total.minutes")}</p>
    <p class="mt-1 text-lg font-semibold tabular-nums text-text">
      {formatMinutes(totals.minutes)}
    </p>
  </div>
  <div class="rounded-xl border border-border bg-surface-raised p-4">
    <p class="text-xs text-text-muted">{t("time.overview.total.billable")}</p>
    <p class="mt-1 text-lg font-semibold tabular-nums text-text">
      {formatMinutes(totals.billable)}
      <span class="text-sm font-normal text-text-muted"
        >({pct(totals.billable, totals.minutes)}%)</span
      >
    </p>
  </div>
  <div class="rounded-xl border border-border bg-surface-raised p-4">
    <p class="text-xs text-text-muted">{t("time.overview.status.approved")}</p>
    <p class="mt-1 text-lg font-semibold tabular-nums text-text">
      {formatMinutes(totals.approved)}
      <span class="text-sm font-normal text-text-muted"
        >({pct(totals.approved, totals.minutes)}%)</span
      >
    </p>
  </div>
  <div class="rounded-xl border border-border bg-surface-raised p-4">
    <p class="text-xs text-text-muted">{t("overview.productivity.employees")}</p>
    <p class="mt-1 text-lg font-semibold tabular-nums text-text">{rows.length}</p>
  </div>
</div>

<!-- Per-employee bars -->
<section class="overflow-hidden rounded-xl border border-border bg-surface-raised">
  {#if rows.length === 0}
    <p class="p-8 text-center text-sm text-text-muted">{t("overview.productivity.empty")}</p>
  {:else}
    <div class="divide-y divide-border">
      {#each rows as row (row.user_id)}
        {@const billablePct = pct(row.billable_minutes, row.minutes)}
        <div class="px-4 py-3">
          <div class="mb-1.5 flex items-baseline justify-between gap-3">
            <span class="min-w-0 truncate text-sm font-medium text-text"
              >{memberName(row.user_id)}</span
            >
            <span class="shrink-0 text-sm font-semibold tabular-nums text-text">
              {formatMinutes(row.minutes)}
              <span class="ml-2 text-xs font-normal text-text-muted">
                {t("overview.productivity.meta", { days: row.active_days, billable: billablePct })}
              </span>
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
    </div>
  {/if}
</section>
