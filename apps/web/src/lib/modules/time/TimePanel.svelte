<script lang="ts">
  /** Company-detail panel: total time logged against this company (CLAUDE.md §6). */
  import { t } from "$lib/core/i18n";
  import { formatMinutes } from "./format";

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface RecentEntry {
    id: string;
    description: string | null;
    minutes: number;
    started_at: string;
  }
  const totalMinutes = $derived((data.total_minutes ?? 0) as number);
  const recent = $derived((data.recent ?? []) as RecentEntry[]);
</script>

<p class="text-sm text-text">
  {t("time.total_logged")}:
  <span class="font-semibold text-text">{formatMinutes(totalMinutes)}</span>
</p>

{#if recent.length > 0}
  <ul class="mt-3 divide-y divide-border">
    {#each recent as entry (entry.id)}
      <li class="flex items-center justify-between py-2 text-sm">
        <span class="text-text">{entry.description ?? "—"}</span>
        <span class="text-text-muted">{formatMinutes(entry.minutes)}</span>
      </li>
    {/each}
  </ul>
{/if}
