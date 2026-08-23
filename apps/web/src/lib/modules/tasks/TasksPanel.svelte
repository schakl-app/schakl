<script lang="ts">
  /** Company-detail panel: the client's task overview (CLAUDE.md §6). */
  import { page } from "$app/state";
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { orgToday } from "$lib/core/today";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
  import { labelChipClass } from "$lib/modules/tasks/labels";
  import TaskQuickCreate from "$lib/modules/tasks/TaskQuickCreate.svelte";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelLabel {
    id: string;
    name: string;
    color: string;
  }
  interface PanelTask {
    id: string;
    title: string;
    status: string;
    priority: string;
    due_date: string | null;
    labels?: PanelLabel[];
    checklist_done?: number;
    checklist_total?: number;
    comment_count?: number;
    visible_to_client?: boolean;
    // The indirect client anchor: on this panel every row already has `companyId`, so it only
    // ever decides whether an *unattached* tick is inert — but the marker asks every caller the
    // same question, and a panel that answered "no project" for all of them would be lying.
    project_id?: string | null;
  }
  const tasks = $derived((data.tasks ?? []) as PanelTask[]);
  // The API has always counted the client's whole open backlog and this component has never
  // read it (#407) — so a client with fifty open tasks and one with five drew the same footer,
  // under a list that used to be fifty rows long on the page above the client's phone number.
  const total = $derived((data.open_count as number | undefined) ?? tasks.length);
  const today = orgToday();

  // The client page's ＋ opens the same dialog as every other create path (#391).
  let creating = $state(false);
  const me = $derived((page.data.user?.id as string | undefined) ?? "");
  const members = $derived(
    (page.data.members as
      | { user_id: string; full_name: string | null; email: string | null; is_active?: boolean }[]
      | undefined) ?? [],
  );
</script>

<!-- The tasks list defaults its person switcher to "yourself" — override it here so the
     hand-over still means every assignee on this client, matching this panel's own list. -->
<PanelRows
  rows={tasks}
  {total}
  alwaysLink
  href={`/tasks?company_id=${companyId}&assignee_user_id=${ALL_ASSIGNEES}`}
  linkLabel={total > tasks.length
    ? t("tasks.panel.view_all_count", { count: total })
    : t("tasks.panel.view_all")}
>
  {#snippet children(shown)}
    {#if shown.length === 0}
      <p class="text-sm text-text-muted">{t("tasks.empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each shown as task (task.id)}
          {@const overdue = task.due_date != null && task.due_date < today}
          <li class="flex items-center gap-2 py-2">
            <!-- Title and marker share the flexible cell: left to the row's own `flex-1`, the icon
             drifted to the far right edge and read as one more badge beside the deadline. Every
             row here hangs off this panel's client, so it reads against a real audience: this
             client's portal contacts. -->
            <span class="flex min-w-0 flex-1 items-center gap-1.5">
              <a
                href={`/tasks/${task.id}`}
                class="min-w-0 truncate text-sm font-medium text-text hover:text-brand"
              >
                {task.title}
              </a>
              <ClientVisibilityIcon
                visible={task.visible_to_client ?? false}
                {companyId}
                projectId={task.project_id}
                size={13}
              />
            </span>
            {#each task.labels ?? [] as label (label.id)}
              <span
                class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(
                  label.color,
                )}">{label.name}</span
              >
            {/each}
            {#if (task.checklist_total ?? 0) > 0}
              <span class="text-[11px] tabular-nums text-text-muted"
                >☑ {task.checklist_done}/{task.checklist_total}</span
              >
            {/if}
            {#if task.status === "in_progress"}
              <span class="rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
                >{t("tasks.status.in_progress")}</span
              >
            {/if}
            {#if task.due_date}
              <span
                class="text-xs tabular-nums {overdue
                  ? 'font-semibold text-red-600 dark:text-red-400'
                  : 'text-text-muted'}"
              >
                {fmtDayMonth(task.due_date)}
              </span>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  {/snippet}
  {#snippet actions()}
    {#if can(page.data.user, "tasks.task.create")}
      <!-- Quick-create from the client page (#230, #391): the shared dialog asks for the name,
           pre-linked to this client, and its action lands on the new task in edit mode. -->
      <button type="button" class="text-brand hover:underline" onclick={() => (creating = true)}
        >＋ {t("tasks.new")}</button
      >
    {/if}
  {/snippet}
</PanelRows>

{#if can(page.data.user, "tasks.task.create")}
  <TaskQuickCreate
    bind:open={creating}
    {companyId}
    {members}
    assignees={me ? [{ user_id: me, is_primary: true }] : []}
    action="/tasks?/create"
    error={(page.form?.error as string | undefined) ?? null}
    pickerSlot="company_tasks_panel"
  />
{/if}
