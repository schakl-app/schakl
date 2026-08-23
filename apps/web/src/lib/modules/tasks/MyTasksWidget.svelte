<script lang="ts">
  /** My Day widget: overdue / due-today / upcoming partitions of my open tasks. */
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { stateTextClass, type UiState } from "$lib/core/state";
  import { orgToday } from "$lib/core/today";
  import { stateIcon } from "$lib/core/ui/state-icons";
  import Card from "$lib/core/ui/Card.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  let { data }: { data: unknown } = $props();

  interface MyTask {
    id: string;
    title: string;
    priority: string;
    due_date: string | null;
    company_name?: string | null;
  }
  interface MinePayload {
    items: MyTask[];
    total: number;
    overdue: number;
    due_today: number;
    upcoming: number;
  }
  const payload = $derived(
    (data ?? { items: [], total: 0, overdue: 0, due_today: 0, upcoming: 0 }) as MinePayload,
  );
  const tasks = $derived(payload.items ?? []);
  const today = orgToday();

  // The rows are a page; the numbers beside the headings are the **whole** set (#407). Derived
  // in the browser off twenty fetched rows they were three wrong numbers rather than three
  // partial ones — and a wrong number reads as measured, which is worse than saying nothing.
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

{#snippet taskList(rows: MyTask[], state: UiState, whole: number, href: string)}
  <PanelRows {rows} collapsed={5} total={whole} {href} alwaysLink={rows.length === 0}>
    {#snippet children(shown)}
      <ul class="divide-y divide-border">
        {#each shown as task (task.id)}
          <li class="flex items-center justify-between gap-2 py-1.5">
            <a href={`/tasks/${task.id}`} class="group min-w-0 flex-1">
              <span class="block truncate text-sm text-text group-hover:text-brand"
                >{task.title}</span
              >
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
  </PanelRows>
{/snippet}

<Card title={t("dashboard.my_day.tasks")} href="/tasks" linkLabel={t("nav.tasks")}>
  {#if payload.total === 0}
    <p class="text-sm text-text-muted">{t("dashboard.my_day.no_tasks")}</p>
  {:else}
    <!-- A partition is drawn on its **whole** count, never on how many of its rows landed on
         this page (#407). The page is ordered by deadline, so somebody with eighteen overdue
         tasks had every row spent before "Later" was reached — and a bucket of twenty-two that
         renders nothing at all is the silent truncation this issue is about, one level in. With
         no rows to draw, the partition is its heading and its way through. -->
    {#if payload.overdue > 0}
      {@render partition(
        "late",
        t("dashboard.my_day.overdue"),
        "/tasks?due=overdue",
        payload.overdue,
      )}
      {@render taskList(overdue, "late", payload.overdue, "/tasks?due=overdue")}
    {/if}
    {#if payload.due_today > 0}
      {@render partition(
        "today",
        t("dashboard.my_day.due_today"),
        "/tasks?due=today",
        payload.due_today,
      )}
      {@render taskList(dueToday, "today", payload.due_today, "/tasks?due=today")}
    {/if}
    {#if payload.upcoming > 0}
      {@render partition("neutral", t("dashboard.my_day.upcoming"), null, payload.upcoming)}
      {@render taskList(upcoming, "neutral", payload.upcoming, "/tasks")}
    {/if}
  {/if}
</Card>
