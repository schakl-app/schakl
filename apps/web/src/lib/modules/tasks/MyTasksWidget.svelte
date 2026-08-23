<script lang="ts">
  /**
   * "Mijn openstaande taken" — four urgency sections, with today as the tile's subject (#397).
   *
   * It used to partition into three (over tijd / vandaag / everything else, labelled *Binnenkort*)
   * and render the third as a plain heading over rows of identical weight: this afternoon's week,
   * next month and every undated task in one list, with the difference between "in three days" and
   * "in eight days" left to the reader to work out from an 11 px muted date. The team's ask was the
   * other way round — today first and loudest, the week and the rest separated from it — and the
   * three-bucket partition could not express it.
   *
   * Four things changed, and three of them are rules rather than layout.
   *
   * **The boundaries are not this file's.** They come from `./due.ts`, which the task board (#395)
   * reads too, and they are the same four names the API's `?due=` filter uses — so a heading opens
   * the list it totals and both screens count the same rows.
   *
   * **Vandaag renders even when it is empty.** It is the heading the tile exists for, and an absent
   * heading is a different sentence from a zero: a colleague opening the dashboard could not tell
   * "nothing due today" from "this tile does not do today". Every other section still hides when
   * empty, because those are not the question the tile is answering.
   *
   * **The weight is on the sections, not the rows.** Four headings with counts is hierarchy; four
   * differently-coloured rows in a five-row tile is noise. The tints are the state palette's
   * (#404) — never `--brand`, which is gold on one tenant we run and would draw *vandaag* as a
   * second warning beside the red one.
   *
   * **A date is printed with its distance.** `24 aug · over 3 dagen`. Absolute alone is arithmetic
   * the reader has to do; relative alone cannot be checked against a calendar.
   *
   * The counts beside the headings are the API's, over the whole set rather than over this page
   * (#407) — which is also what lets *Later* start folded honestly: `PanelRows` expands the rows
   * that arrived and hands over for the ones that did not.
   */
  import { dateLocale, fmtDayMonth, fmtDayMonthYear } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { stateTextClass, type UiState } from "$lib/core/state";
  import { orgToday } from "$lib/core/today";
  import { stateIcon } from "$lib/core/ui/state-icons";
  import Card from "$lib/core/ui/Card.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { dayDistance, dueHref, dueLabelKey, dueState, groupByDue, type DueBucket } from "./due";

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
    due_week: number;
    later: number;
  }
  const EMPTY: MinePayload = {
    items: [],
    total: 0,
    overdue: 0,
    due_today: 0,
    due_week: 0,
    later: 0,
  };
  const payload = $derived((data ?? EMPTY) as MinePayload);
  const tasks = $derived(payload.items ?? []);
  const today = orgToday();

  // The rows are a page; the numbers beside the headings are the **whole** set (#407). Derived
  // in the browser off twenty fetched rows they were wrong numbers rather than partial ones —
  // and a wrong number reads as measured, which is worse than saying nothing.
  const groups = $derived(groupByDue(tasks, today));
  const whole = $derived<Record<DueBucket, number>>({
    overdue: payload.overdue,
    today: payload.due_today,
    week: payload.due_week,
    later: payload.later,
  });

  /** "24 aug" — with its year once the deadline leaves the current one, or it reads as this year. */
  function dueDay(iso: string): string {
    return iso.slice(0, 4) === today.slice(0, 4) ? fmtDayMonth(iso) : fmtDayMonthYear(iso);
  }

  /**
   * "over 3 dagen" / "morgen" / "vandaag" / "3 dagen te laat".
   *
   * `Intl.RelativeTimeFormat` with `numeric: "auto"` gives the near future its words for free, but
   * it has no notion of a *deadline*: it would say "3 dagen geleden", which is true of the date and
   * says nothing about the task. Overdue therefore gets its own message. Past a month the distance
   * is dropped entirely — "over 214 dagen" is arithmetic nobody asked for, and the date beside it
   * already carries its year.
   */
  function dueDistance(iso: string): string | null {
    const days = dayDistance(today, iso);
    if (days < 0) {
      const late = -days;
      return late === 1 ? t("tasks.due.days_late_one") : t("tasks.due.days_late", { days: late });
    }
    if (days > 31) return null;
    return new Intl.RelativeTimeFormat(dateLocale(), { numeric: "auto" }).format(days, "day");
  }
</script>

<!-- A partition heading is an aggregate, and an aggregate opens the list it totals (issue #15):
     /tasks defaults to the signed-in user, so these are the same tasks, and the four `?due=`
     values are the four buckets — same boundaries, so the two counts agree. The *state* is the
     palette's (#404) — "vandaag" used to be `text-brand`, which on the tenant whose brand is
     gold drew it beside the red "over tijd" as a second warning, and on a blue-branded tenant
     as a link. Brand is identity and navigation; urgency is `late` / `today` / `soon` /
     neutral. -->
{#snippet partition(bucket: DueBucket)}
  {@const state = dueState(bucket) as UiState}
  {@const Mark = stateIcon(state)}
  {@const body = `mt-4 mb-1 flex items-center gap-1.5 text-sm font-semibold ${stateTextClass(state)}`}
  <a href={dueHref(bucket)} class="{body} first:mt-0 hover:underline">
    {#if Mark}<Mark size={14} aria-hidden="true" class="shrink-0" />{/if}
    {t(dueLabelKey(bucket))}
    {#if whole[bucket] > 0}
      <span class="text-xs font-normal tabular-nums opacity-80">({whole[bucket]})</span>
    {/if}
  </a>
{/snippet}

{#snippet taskList(bucket: DueBucket, collapsed?: number)}
  {@const state = dueState(bucket) as UiState}
  <PanelRows
    rows={groups[bucket]}
    {collapsed}
    total={whole[bucket]}
    href={dueHref(bucket)}
    alwaysLink={groups[bucket].length === 0}
  >
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
            <span class="shrink-0 text-right text-xs tabular-nums">
              {#if task.due_date}
                {@const distance = dueDistance(task.due_date)}
                <span
                  class="block {state === 'late'
                    ? `font-semibold ${stateTextClass('late')}`
                    : 'text-text'}">{dueDay(task.due_date)}</span
                >
                {#if distance}
                  <span
                    class="block text-[11px] {state === 'late'
                      ? stateTextClass('late')
                      : 'text-text-muted'}">{distance}</span
                  >
                {/if}
              {:else}
                <!-- A deadline is required now (#392); what is left is the backlog that predates
                     the rule, and its priority is the only urgency it has to offer. -->
                <span class="block text-text-muted">{t(`tasks.priority.${task.priority}`)}</span>
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
         renders nothing at all is the silent truncation that issue is about, one level in. With
         no rows to draw, the partition is its heading and its way through. -->
    {#if whole.overdue > 0}
      {@render partition("overdue")}
      {@render taskList("overdue")}
    {/if}

    <!-- Always drawn, empty or not: this is the heading the tile is for. -->
    {@render partition("today")}
    {#if whole.today > 0}
      {@render taskList("today")}
    {:else}
      <p class="py-1.5 text-sm text-text-muted">{t("dashboard.my_day.nothing_today")}</p>
    {/if}

    {#if whole.week > 0}
      {@render partition("week")}
      {@render taskList("week")}
    {/if}

    {#if whole.later > 0}
      {@render partition("later")}
      <!-- *Later* is the one section that starts folded: the tile is a working surface for the
           next few days, and a scroll of November is what made it read as uniform. -->
      {@render taskList("later", 3)}
    {/if}
  {/if}
</Card>
