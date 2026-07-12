<script lang="ts">
  /**
   * The recurring agreements covering this project (issue #30 follow-up): each linked
   * subscription with its included-hours burn for the current period — the same scale every
   * budget bar uses, and the number answers "is this work inside the retainer?" in place.
   */
  import { burnBarClass, burnBarWidth, burnPct } from "$lib/core/burn";
  import { fmtNumber, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  interface PanelUsage {
    period_start: string | null;
    period_end: string | null;
    included_hours: string | number | null;
    used_hours: number;
    overage_hours: number;
  }

  interface PanelSubscription {
    id: string;
    name: string;
    status: string;
    interval: string;
    next_invoice_date: string | null;
    included_hours: string | number | null;
    usage: PanelUsage | null;
  }

  let { data }: { data: unknown; context?: unknown; lookups?: unknown } = $props();
  const subscriptions = $derived(
    ((data as Record<string, unknown>)?.subscriptions ?? []) as PanelSubscription[],
  );
</script>

{#if subscriptions.length === 0}
  <p class="text-sm text-text-muted">{t("subscriptions.panel.project_empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each subscriptions as sub (sub.id)}
      {@const included = sub.usage?.included_hours != null ? Number(sub.usage.included_hours) : null}
      {@const used = sub.usage?.used_hours ?? 0}
      {@const pct = included ? burnPct(used, included) : null}
      <li class="py-2">
        <div class="flex flex-wrap items-center gap-2">
          <a
            href="/subscriptions"
            class="min-w-0 flex-1 truncate text-sm font-medium text-brand hover:underline"
            >{sub.name}</a
          >
          <span class="rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted"
            >{t(`subscriptions.status.${sub.status}`)}</span
          >
        </div>
        <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-text-muted">
          {#if included != null}
            <span class="tabular-nums">
              {t("subscriptions.panel.usage", {
                used: fmtNumber(used),
                included: fmtNumber(included),
              })}
            </span>
            {#if (sub.usage?.overage_hours ?? 0) > 0}
              <span class="font-medium text-red-600 dark:text-red-400">
                {t("subscriptions.panel.overage", {
                  hours: fmtNumber(sub.usage?.overage_hours ?? 0),
                })}
              </span>
            {/if}
          {:else}
            <span class="tabular-nums">
              {t("subscriptions.panel.usage_unbounded", { used: fmtNumber(used) })}
            </span>
          {/if}
          {#if sub.usage?.period_end}
            <span>
              {t("subscriptions.panel.until", { date: fmtNumericDate(sub.usage.period_end) })}
            </span>
          {/if}
        </div>
        {#if pct != null}
          <div class="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface">
            <div class="h-full {burnBarClass(pct)}" style="width: {burnBarWidth(pct)}"></div>
          </div>
        {/if}
      </li>
    {/each}
  </ul>
{/if}
