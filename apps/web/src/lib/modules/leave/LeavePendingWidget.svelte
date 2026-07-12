<script lang="ts">
  /** My Day widget (#156): leave requests waiting on an approver — count + the next few,
   *  linking into the team review queue (deep-linked per request, like the notification). */
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

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

{#if payload.total === 0}
  <p class="text-sm text-text-muted">{t("leave.widget.pending_empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each payload.items as request (request.id)}
      <li class="py-1.5">
        <a href={`/leave/team?request=${request.id}`} class="block min-w-0 hover:text-brand">
          <span class="block truncate text-sm text-text">{request.user_name ?? "—"}</span>
          <span class="block text-xs text-text-muted">
            {fmtDayMonth(request.start_date)}–{fmtDayMonth(request.end_date)}
          </span>
        </a>
      </li>
    {/each}
  </ul>
  <a href="/leave/team" class="mt-2 inline-block text-xs font-medium text-brand hover:underline">
    {t("leave.widget.pending_all", { count: payload.total })}
  </a>
{/if}
