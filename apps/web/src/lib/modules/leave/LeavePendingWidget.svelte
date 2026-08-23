<script lang="ts">
  /** My Day widget (#156): leave requests waiting on an approver — count + the next few,
   *  linking into the team review queue (deep-linked per request, like the notification). */
  import { fmtDayMonth, RANGE_DASH } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  let { data }: { data: unknown } = $props();

  interface PendingRequest {
    id: string;
    user_name?: string | null;
    start_date: string;
    end_date: string;
    hours: number;
  }
  const payload = $derived(
    (data ?? { items: [], total: 0 }) as { items: PendingRequest[]; total: number },
  );
</script>

<!-- The total used to be smuggled into the header's link label, because the card had nowhere
     else to put it (#407). It is the row list's own sentence now, where the rows are. -->
<DashboardWidgetCard title={t("dashboard.widget.leave.pending_approvals")}>
  {#if payload.total === 0}
    <p class="text-sm text-text-muted">{t("leave.widget.pending_empty")}</p>
  {:else}
    <PanelRows
      rows={payload.items}
      total={payload.total}
      href="/leave/team"
      linkLabel={t("leave.widget.pending_all", { count: payload.total })}
      alwaysLink
    >
      {#snippet children(shown)}
        <ul class="divide-y divide-border">
          {#each shown as request (request.id)}
            <li class="py-1.5">
              <a href={`/leave/team?request=${request.id}`} class="block min-w-0 hover:text-brand">
                <span class="block truncate text-sm text-text">{request.user_name ?? "—"}</span>
                <span class="block text-xs text-text-muted">
                  {fmtDayMonth(request.start_date)}{RANGE_DASH}{fmtDayMonth(request.end_date)}
                </span>
              </a>
            </li>
          {/each}
        </ul>
      {/snippet}
    </PanelRows>
  {/if}
</DashboardWidgetCard>
