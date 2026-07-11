<script lang="ts">
  /** My Day widget: time logged today + running-timer state (CLAUDE.md §10). */
  import { t } from "$lib/core/i18n";
  import { formatMinutes } from "./format";

  let { data }: { data: unknown } = $props();

  interface Summary {
    date: string;
    minutes: number;
    running: { id: string; description: string | null } | null;
  }
  const summary = $derived((data ?? { minutes: 0, running: null }) as Summary);
</script>

<div class="rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-text">{t("dashboard.my_day.time")}</h2>
    <a href="/time" class="text-xs text-brand hover:underline">{t("nav.time")}</a>
  </div>
  <!-- The figure links to the time list it totals (issue #15 — aggregates link to their list). -->
  <a href="/time" class="block text-2xl font-semibold text-text hover:text-brand"
    >{formatMinutes(summary.minutes)}</a
  >
  <p class="mt-1 text-sm text-text-muted">
    {#if summary.running}
      <span class="inline-flex items-center gap-1.5">
        <span class="h-2 w-2 animate-pulse rounded-full bg-green-500 dark:bg-green-400"></span>
        {t("time.timer.running")}
      </span>
    {:else}
      {t("dashboard.my_day.no_timer")}
    {/if}
  </p>
</div>
