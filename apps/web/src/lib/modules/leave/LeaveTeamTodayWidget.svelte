<script lang="ts">
  /** My Day widget: who is off today (approved leave), so nobody plans a meeting blind. */
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  let { data }: { data: unknown } = $props();

  interface Absence {
    id: string;
    user_name: string;
    status: string;
    resolved_start_time?: string | null;
    resolved_end_time?: string | null;
  }
  const absences = $derived(((data ?? []) as Absence[]).filter((a) => a.status === "approved"));
</script>

<DashboardWidgetCard
  title={t("dashboard.widget.leave.team_today")}
  href="/calendar"
  linkLabel={t("nav.calendar")}
>
  {#if absences.length === 0}
    <p class="text-sm text-text-muted">{t("leave.widget.team_today_empty")}</p>
  {:else}
    <ul class="space-y-1.5">
      {#each absences as absence (absence.id)}
        <li class="flex items-center justify-between gap-2 text-sm">
          <span class="min-w-0 truncate text-text">{absence.user_name}</span>
          {#if absence.resolved_start_time && absence.resolved_end_time}
            <span class="shrink-0 tabular-nums text-xs text-text-muted">
              {absence.resolved_start_time}–{absence.resolved_end_time}
            </span>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</DashboardWidgetCard>
