<script lang="ts">
  /**
   * Available (remaining) hours against a budget, with a burn bar (#25).
   *
   * `12,5 / 40 u` plus a bar on the one documented scale (`core/burn.ts`). Over budget the number
   * goes negative and red rather than clamping to a reassuring zero; the bar's *width* clamps,
   * because a bar cannot be 130 % long.
   *
   * With no budget there is nothing to remain, so this shows an em-dash — never a fabricated
   * total — while still reporting the hours that were spent. Hours the budget never covered
   * (unapproved, or on the client's unbudgeted work) are named in the tooltip: excluded from the
   * arithmetic, never dropped from the record.
   */
  import { burnBarClass, burnBarWidth, burnPct, burnTextClass } from "$lib/core/burn";
  import { fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  interface Hours {
    period?: string | null;
    budget_hours?: number | null;
    spent_hours?: number;
    unapproved_hours?: number;
    unbudgeted_hours?: number;
    remaining_hours?: number | null;
  }

  let { hours }: { hours?: Hours | null } = $props();

  const budget = $derived(hours?.budget_hours ?? null);
  const spent = $derived(hours?.spent_hours ?? 0);
  const remaining = $derived(hours?.remaining_hours ?? null);
  const pct = $derived(burnPct(spent, budget));

  const periodLabel = $derived(
    hours?.period ? t(`table.hours.period.${hours.period}`) : t("table.hours.period.mixed"),
  );

  // Everything the bar deliberately does not account for, said out loud on hover.
  const caveats = $derived(
    [
      hours?.unapproved_hours
        ? t("table.hours.unapproved", { hours: fmtNumber(hours.unapproved_hours) })
        : null,
      hours?.unbudgeted_hours
        ? t("table.hours.unbudgeted", { hours: fmtNumber(hours.unbudgeted_hours) })
        : null,
    ].filter(Boolean) as string[],
  );

  const tooltip = $derived(
    [
      budget != null
        ? t("table.hours.of_budget", { spent: fmtNumber(spent), budget: fmtNumber(budget) })
        : t("table.hours.no_budget"),
      budget != null ? periodLabel : null,
      ...caveats,
    ]
      .filter(Boolean)
      .join(" · "),
  );
</script>

{#if !hours}
  <span class="text-text-muted">—</span>
{:else if budget == null}
  <!-- No allowance to burn. The spend is still on the record. -->
  <span class="text-text-muted" title={tooltip}>
    —
    {#if spent > 0 || hours.unbudgeted_hours}
      <span class="ml-1 text-xs">
        ({fmtNumber(spent || (hours.unbudgeted_hours ?? 0))} u)
      </span>
    {/if}
  </span>
{:else}
  <span class="inline-flex flex-col items-end gap-1" title={tooltip}>
    <span class="whitespace-nowrap text-xs">
      <span class="font-medium {burnTextClass(pct)}">{fmtNumber(remaining ?? 0)}</span>
      <span class="text-text-muted">/ {fmtNumber(budget)} u</span>
      {#if caveats.length > 0}<span class="text-text-muted" aria-hidden="true">*</span>{/if}
    </span>
    <span class="h-1.5 w-full min-w-16 overflow-hidden rounded-full bg-surface">
      <span
        class="block h-full rounded-full {burnBarClass(pct)}"
        style="width: {burnBarWidth(pct)}%"
      ></span>
    </span>
  </span>
{/if}
