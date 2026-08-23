<script lang="ts">
  /** My Day widget: who is off today (approved leave), so nobody plans a meeting blind. */
  import { fmtClockTime, RANGE_DASH } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  let { data }: { data: unknown } = $props();

  interface Absence {
    id: string;
    user_name: string;
    status: string;
    start_date: string;
    end_date: string;
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
    <!-- Bounded by headcount rather than by a client's history, so the whole day's list *is*
         the answer — it collapses to a handful and expands in place, with no hand-over to
         invent (#407). -->
    <PanelRows rows={absences} collapsed={5}>
      {#snippet children(shown)}
        <ul class="space-y-1.5">
          {#each shown as absence (absence.id)}
            <li class="flex items-center justify-between gap-2 text-sm">
              <!-- The absence, not "the leave module" (issue #15): `?request=` is the same deep link
               the calendar chip and the approval notification use. -->
              <a
                href="/leave/team?request={absence.id}"
                class="min-w-0 truncate text-text hover:text-brand">{absence.user_name}</a
              >
              <!-- Single-day spans only, like the calendar feed: a Thu-15:00 → Fri-12:00 request
               snapshots (15:00, 12:00), a window that describes neither day. Times follow the
               personal clock preference (#13). -->
              {#if absence.start_date === absence.end_date && absence.resolved_start_time && absence.resolved_end_time}
                <span class="shrink-0 tabular-nums text-xs text-text-muted">
                  {fmtClockTime(absence.resolved_start_time)}{RANGE_DASH}{fmtClockTime(
                    absence.resolved_end_time,
                  )}
                </span>
              {/if}
            </li>
          {/each}
        </ul>
      {/snippet}
    </PanelRows>
  {/if}
</DashboardWidgetCard>
