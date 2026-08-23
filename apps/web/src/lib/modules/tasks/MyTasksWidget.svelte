<script lang="ts">
  /** My Day widget: overdue / due-today / upcoming partitions of my open tasks. */
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { stateTextClass, type UiState } from "$lib/core/state";
  import { orgToday } from "$lib/core/today";
  import { stateIcon } from "$lib/core/ui/state-icons";
  import Card from "$lib/core/ui/Card.svelte";

  let { data }: { data: unknown } = $props();

  interface MyTask {
    id: string;
    title: string;
    priority: string;
    due_date: string | null;
    company_name?: string | null;
  }
  const tasks = $derived((data ?? []) as MyTask[]);
  const today = orgToday();

  const overdue = $derived(tasks.filter((task) => task.due_date != null && task.due_date < today));
  const dueToday = $derived(tasks.filter((task) => task.due_date === today));
  const upcoming = $derived(tasks.filter((task) => task.due_date == null || task.due_date > today));
</script>

<!-- A partition heading is an aggregate, and an aggregate opens the list it totals (issue #15):
     /tasks defaults to the signed-in user, so these are the same tasks. The *state* is the
     palette's (#404) — "vandaag" used to be `text-brand`, which on the tenant whose brand is
     gold drew it beside the red "over tijd" as a second warning, and on a blue-branded tenant
     as a link. Brand is identity and navigation; urgency is `late` / `today` / neutral. -->
{#snippet partition(state: UiState, label: string, href: string | null, count: number)}
  {@const Mark = stateIcon(state)}
  {@const body = `mt-3 mb-1 flex items-center gap-1.5 text-sm font-semibold ${stateTextClass(state)}`}
  <svelte:element
    this={href ? "a" : "h3"}
    href={href ?? undefined}
    class="{body} {href ? 'hover:underline' : ''} first:mt-0"
  >
    {#if Mark}<Mark size={14} aria-hidden="true" class="shrink-0" />{/if}
    {label}
    {#if count > 0 && state !== "neutral"}
      <span class="text-xs font-normal tabular-nums opacity-80">({count})</span>
    {/if}
  </svelte:element>
{/snippet}

{#snippet taskList(rows: MyTask[], state: UiState)}
  <ul class="divide-y divide-border">
    {#each rows as task (task.id)}
      <li class="flex items-center justify-between gap-2 py-1.5">
        <a href={`/tasks/${task.id}`} class="group min-w-0 flex-1">
          <span class="block truncate text-sm text-text group-hover:text-brand">{task.title}</span>
          {#if task.company_name}
            <!-- Which client's work this is. "Nieuwsbrief plannen" is four indistinguishable
                 rows on a list spanning four clients, and only opening one tells them apart. -->
            <span class="block truncate text-xs text-text-muted">{task.company_name}</span>
          {/if}
        </a>
        <span
          class="shrink-0 text-xs tabular-nums {state === 'late'
            ? `font-semibold ${stateTextClass('late')}`
            : 'text-text-muted'}"
        >
          {#if task.due_date}
            {fmtDayMonth(task.due_date)}
          {:else}
            {t(`tasks.priority.${task.priority}`)}
          {/if}
        </span>
      </li>
    {/each}
  </ul>
{/snippet}

<Card title={t("dashboard.my_day.tasks")} href="/tasks" linkLabel={t("nav.tasks")}>
  {#if tasks.length === 0}
    <p class="text-sm text-text-muted">{t("dashboard.my_day.no_tasks")}</p>
  {:else}
    {#if overdue.length > 0}
      {@render partition(
        "late",
        t("dashboard.my_day.overdue"),
        "/tasks?due=overdue",
        overdue.length,
      )}
      {@render taskList(overdue, "late")}
    {/if}
    {#if dueToday.length > 0}
      {@render partition(
        "today",
        t("dashboard.my_day.due_today"),
        "/tasks?due=today",
        dueToday.length,
      )}
      {@render taskList(dueToday, "today")}
    {/if}
    {#if upcoming.length > 0}
      {@render partition("neutral", t("dashboard.my_day.upcoming"), null, upcoming.length)}
      {@render taskList(upcoming, "neutral")}
    {/if}
  {/if}
</Card>
