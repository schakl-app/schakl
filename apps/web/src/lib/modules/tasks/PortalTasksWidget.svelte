<script lang="ts">
  /**
   * "Jullie taken" — what is asked of the client, on their own homepage (#450, narrowed).
   *
   * Only the open tasks **assigned to one of the client's own people** (`?assigned_to=contact`,
   * on the company the switcher selected), each row naming who: a task the agency is waiting on
   * is exactly the row a client's dashboard exists to show, and a colleague's row is still theirs
   * to notice. The rest of the account's open work — what *we* are doing — used to sit under this
   * heading as "other tasks" and is the *Werkzaamheden* tile now (`PortalWorkWidget`): two
   * questions, two tiles, and a task appears in exactly one of them.
   *
   * Overdue stays red (docs/UX.md principle 4): `DueDate` carries the state.
   */
  import { t } from "$lib/core/i18n";
  import { orgToday } from "$lib/core/today";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import DueDate from "./DueDate.svelte";

  let { data }: { data: unknown } = $props();

  interface Row {
    id: string;
    title: string;
    due_date: string | null;
    assignee_contact_name?: string | null;
  }
  interface Payload {
    items: Row[];
    total: number;
    companyId: string | null;
  }
  const EMPTY: Payload = { items: [], total: 0, companyId: null };
  const payload = $derived((data ?? EMPTY) as Payload);
  const today = orgToday();
  // The list this tile totals: the client's task board, on this company, assigned to their
  // own people — the same filter the rows were read with, so the destination confirms the count.
  const href = $derived(
    `/tasks?assigned_to=contact${payload.companyId ? `&company_id=${payload.companyId}` : ""}`,
  );
</script>

<DashboardWidgetCard title={t("dashboard.widget.tasks.portal")} {href} linkLabel={t("nav.tasks")}>
  {#if payload.items.length === 0}
    <p class="text-sm text-text-muted">{t("tasks.portal.empty")}</p>
  {:else}
    <PanelRows rows={payload.items} collapsed={8} total={payload.total} {href}>
      {#snippet children(shown)}
        <ul class="divide-y divide-border">
          {#each shown as task (task.id)}
            <li class="flex items-center justify-between gap-3 py-2">
              <span class="min-w-0 flex-1">
                <a
                  href={`/tasks/${task.id}`}
                  class="block truncate text-sm text-text hover:text-brand">{task.title}</a
                >
                {#if task.assignee_contact_name}
                  <span class="block truncate text-xs text-text-muted"
                    >{task.assignee_contact_name}</span
                  >
                {/if}
              </span>
              <DueDate due={task.due_date} {today} />
            </li>
          {/each}
        </ul>
      {/snippet}
    </PanelRows>
  {/if}
</DashboardWidgetCard>
