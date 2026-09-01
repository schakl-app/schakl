<script lang="ts">
  /**
   * The client's tasks on their own homepage (#450).
   *
   * Two lists under one heading, and the order is the point: **what is asked of the client** —
   * the open tasks assigned to the contact behind this login (`/tasks/dashboard-mine`, which
   * resolves a portal session to its contact, #453) — and under it the other open work on their
   * account they may follow (`/tasks?open=true`, the same horizon-scoped, client-visible set
   * `/tasks` lists). A client never had a tasks tile at all: both staff widgets are staff-only
   * and matched the *user's* roster rows, which a contact assignee never has.
   *
   * Overdue stays red (docs/UX.md principle 4): `DueDate` carries the state, and a task the
   * agency is waiting on is exactly the row a client's dashboard exists to show.
   */
  import { t } from "$lib/core/i18n";
  import { orgToday } from "$lib/core/today";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";

  import DueDate from "./DueDate.svelte";

  let { data }: { data: unknown } = $props();

  interface Row {
    id: string;
    title: string;
    due_date: string | null;
  }
  interface Payload {
    mine: Row[];
    mineTotal: number;
    others: Row[];
    othersTotal: number;
  }
  const EMPTY: Payload = { mine: [], mineTotal: 0, others: [], othersTotal: 0 };
  const payload = $derived((data ?? EMPTY) as Payload);
  const today = orgToday();
</script>

{#snippet rows(list: Row[])}
  <ul class="divide-y divide-border">
    {#each list as task (task.id)}
      <li class="flex items-center justify-between gap-3 py-2">
        <a
          href={`/tasks/${task.id}`}
          class="min-w-0 flex-1 truncate text-sm text-text hover:text-brand">{task.title}</a
        >
        <DueDate due={task.due_date} {today} />
      </li>
    {/each}
  </ul>
{/snippet}

<DashboardWidgetCard
  title={t("dashboard.widget.tasks.portal")}
  href="/tasks"
  linkLabel={t("nav.tasks")}
>
  {#if payload.mine.length === 0 && payload.others.length === 0}
    <p class="text-sm text-text-muted">{t("tasks.portal.empty")}</p>
  {:else}
    {#if payload.mine.length > 0}
      <section>
        <h3 class="mb-1 text-sm font-semibold text-text">
          {t("tasks.portal.mine")}
          <span class="text-xs font-normal tabular-nums text-text-muted">({payload.mineTotal})</span
          >
        </h3>
        {@render rows(payload.mine)}
      </section>
    {/if}
    {#if payload.others.length > 0}
      <section class:mt-4={payload.mine.length > 0}>
        <h3 class="mb-1 text-sm font-semibold text-text-muted">
          {t("tasks.portal.others")}
          <span class="text-xs font-normal tabular-nums">({payload.othersTotal})</span>
        </h3>
        {@render rows(payload.others)}
      </section>
    {/if}
  {/if}
</DashboardWidgetCard>
