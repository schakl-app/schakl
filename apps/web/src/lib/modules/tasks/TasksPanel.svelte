<script lang="ts">
  /**
   * Company-detail panel: the client's task overview (CLAUDE.md §6).
   *
   * The deadline is drawn by the shared `DueDate` (#395) rather than by a fourth private copy of
   * `due_date < today` — the team asked for the board's urgency treatment on the client list by
   * name, and one component is how it arrives here without a rule to keep in step.
   *
   * Grouped by urgency like the My Day tile (#397): the four buckets come from `due.ts`, the
   * headings carry the state glyph and tint, and the counts beside them are the API's over the
   * client's whole open set — never derived from the five rows on this page. Each heading opens
   * the client's own filtered list (`?due=`), every assignee, so the two counts agree.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { fromHref } from "$lib/core/origin";
  import { can } from "$lib/core/permissions";
  import { stateBandClass, stateTextClass, type UiState } from "$lib/core/state";
  import { InFlight } from "$lib/core/submit.svelte";
  import { orgToday } from "$lib/core/today";
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";
  import { stateIcon } from "$lib/core/ui/state-icons";
  import ClientVisibilityIcon from "$lib/modules/tasks/ClientVisibilityIcon.svelte";
  import DueDate from "$lib/modules/tasks/DueDate.svelte";
  import {
    DUE_BUCKETS,
    dueLabelKey,
    dueState,
    groupByDue,
    type DueBucket,
  } from "$lib/modules/tasks/due";
  import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
  import { labelChipClass } from "$lib/modules/tasks/labels";
  import { priorityRailClass } from "$lib/modules/tasks/priority";

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
    assignees?: { user_id: string; is_primary: boolean }[];
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

  // The rows are a page of five; the numbers beside the headings are the whole set (#407).
  const groups = $derived(groupByDue(tasks, today));
  const whole = $derived<Record<DueBucket, number>>({
    overdue: (data.overdue as number | undefined) ?? groups.overdue.length,
    today: (data.due_today as number | undefined) ?? groups.today.length,
    week: (data.due_week as number | undefined) ?? groups.week.length,
    later: (data.later as number | undefined) ?? groups.later.length,
  });

  const listHref = $derived(`/tasks?company_id=${companyId}&assignee_user_id=${ALL_ASSIGNEES}`);

  // Who is on a row (#375): the roster resolves against the members the page already loaded.
  const members = $derived(
    (page.data.members as
      | {
          user_id: string;
          full_name: string | null;
          email: string | null;
          avatar_url?: string | null;
          is_active?: boolean;
        }[]
      | undefined) ?? [],
  );
  function faces(task: PanelTask) {
    return (task.assignees ?? [])
      .map((link) => ({ link, member: members.find((m) => m.user_id === link.user_id) }))
      .filter((entry) => entry.member !== undefined);
  }

  /** The partition as shape (#438) — the same bands the My Day tile draws; see there. */
  function sectionClass(bucket: DueBucket): string {
    const band = stateBandClass(dueState(bucket) as UiState);
    return band
      ? `-mx-2.5 mt-3 rounded-lg px-2.5 py-2 first:mt-0 ${band}`
      : "mt-3 border-t border-border pt-2.5 first:mt-0 first:border-t-0 first:pt-0";
  }

  // The client page's ＋ is create-then-edit, like every other primary create path: the tasks
  // action writes a placeholder row linked to this client and lands on it in edit mode.
  const creating = new InFlight();
</script>

{#snippet partition(bucket: DueBucket)}
  {@const state = dueState(bucket) as UiState}
  {@const Mark = stateIcon(state)}
  <a
    href={`${listHref}&due=${bucket}`}
    class="mb-1 flex items-center gap-1.5 text-sm font-semibold hover:underline {stateTextClass(
      state,
    )}"
  >
    {#if Mark}<Mark size={14} aria-hidden="true" class="shrink-0" />{/if}
    {t(dueLabelKey(bucket))}
    <span class="text-xs font-normal tabular-nums opacity-80">({whole[bucket]})</span>
  </a>
{/snippet}

{#snippet taskRow(task: PanelTask)}
  {@const roster = faces(task)}
  <!-- The rail is the row's own priority marker (#395): drawn for the exceptional values
       only, and transparent otherwise so nothing shifts. -->
  <li class="flex items-center gap-2 py-2 pl-2 {priorityRailClass(task.priority)}">
    <!-- Title and marker share the flexible cell: left to the row's own `flex-1`, the icon
         drifted to the far right edge and read as one more badge beside the deadline. Every
         row here hangs off this panel's client, so it reads against a real audience: this
         client's portal contacts. -->
    <span class="flex min-w-0 flex-1 items-center gap-1.5">
      <a
        href={fromHref(`/tasks/${task.id}`, page.url)}
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
      <span class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(label.color)}"
        >{label.name}</span
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
    <DueDate due={task.due_date} {today} />
    <!-- Who this row is on (#375): the row is narrow, so it draws up to two and counts the
         rest — the same rule as `TaskRow`, resolved from the members the page already loaded. -->
    {#each roster.slice(0, 2) as { link, member } (link.user_id)}
      <Avatar
        name={member?.full_name}
        email={member?.email ?? null}
        avatarUrl={member?.avatar_url ?? null}
        size="sm"
      />
    {/each}
    {#if roster.length > 2}
      <span
        class="text-xs text-text-muted"
        title={roster
          .slice(2)
          .map(({ member }) => member?.full_name || member?.email)
          .join(", ")}>+{roster.length - 2}</span
      >
    {/if}
  </li>
{/snippet}

<!-- The tasks list defaults its person switcher to "yourself" — override it here so the
     hand-over still means every assignee on this client, matching this panel's own list. -->
<PanelRows
  rows={tasks}
  {total}
  alwaysLink
  href={listHref}
  linkLabel={total > tasks.length
    ? t("tasks.panel.view_all_count", { count: total })
    : t("tasks.panel.view_all")}
>
  {#snippet children(shown)}
    {#if shown.length === 0}
      <p class="text-sm text-text-muted">{t("tasks.empty")}</p>
    {:else}
      {@const shownGroups = groupByDue(shown, today)}
      <!-- A partition is drawn on its **whole** count, never on how many of its rows landed on
           this page (#407): the page is ordered by deadline, so a client with six overdue tasks
           spends every row before "Vandaag" is reached — the heading with its count and its
           way through is then the whole section. -->
      {#each DUE_BUCKETS as bucket (bucket)}
        {#if whole[bucket] > 0}
          <section class={sectionClass(bucket)}>
            {@render partition(bucket)}
            {#if shownGroups[bucket].length > 0}
              <ul class="divide-y divide-border">
                {#each shownGroups[bucket] as task (task.id)}
                  {@render taskRow(task)}
                {/each}
              </ul>
            {/if}
          </section>
        {/if}
      {/each}
    {/if}
  {/snippet}
  {#snippet actions()}
    {#if can(page.data.user, "tasks.task.create")}
      <!-- Create-then-edit from the client page (#230): one click writes a placeholder row
           pre-linked to this client and lands on it in edit mode, where every field lives. -->
      <form method="POST" action="/tasks?/create" use:enhance={creating.wrap()}>
        <input type="hidden" name="company_id" value={companyId} />
        <button
          type="submit"
          class="text-brand hover:underline disabled:opacity-60"
          disabled={creating.active}>＋ {t("tasks.new")}</button
        >
      </form>
    {/if}
  {/snippet}
</PanelRows>
