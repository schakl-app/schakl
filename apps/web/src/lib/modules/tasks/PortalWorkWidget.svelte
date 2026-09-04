<script lang="ts">
  /**
   * "Werkzaamheden" — what the agency is doing for the client, on the client's homepage.
   *
   * The open, client-visible tasks on the selected company that are **not** assigned to one of
   * the client's own people (`?assigned_to=agency`): the work we do ourselves, assigned to a
   * colleague or to nobody yet — still our queue. Partitioned by urgency the way the staff tile
   * is (`MyTasksWidget`, #397): the same four buckets from `./due.ts`, today drawn even when
   * empty because it is the heading the tile exists for, *Later* starting folded.
   *
   * The counts are derived from the page, unlike the staff tile's, because the API's four
   * whole-set numbers are computed for the *viewer's* roster and this list is by company. The
   * page is deliberately big (a hundred rows, the endpoint's cap is two hundred) and the total
   * beside the heading says when it was not enough (#407) — a bucket is never a wrong number,
   * at worst a stated partial one.
   */
  import { dateLocale, fmtDayMonth, fmtDayMonthYear } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { stateBandClass, stateTextClass, type UiState } from "$lib/core/state";
  import { orgToday } from "$lib/core/today";
  import { stateIcon } from "$lib/core/ui/state-icons";
  import Card from "$lib/core/ui/Card.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { dayDistance, dueLabelKey, dueState, groupByDue, type DueBucket } from "./due";

  let { data }: { data: unknown } = $props();

  interface Row {
    id: string;
    title: string;
    priority: string;
    due_date: string | null;
  }
  interface Payload {
    items: Row[];
    total: number;
    companyId: string | null;
  }
  const EMPTY: Payload = { items: [], total: 0, companyId: null };
  const payload = $derived((data ?? EMPTY) as Payload);
  const today = orgToday();
  const groups = $derived(groupByDue(payload.items, today));
  // Rows beyond the page are unbucketed: named once, on the card, never spread as a guess.
  const beyondPage = $derived(Math.max(0, payload.total - payload.items.length));

  const listHref = $derived(
    `/tasks?assigned_to=agency${payload.companyId ? `&company_id=${payload.companyId}` : ""}`,
  );
  const bucketHref = (bucket: DueBucket) => `${listHref}&due=${bucket}`;

  function dueDay(iso: string): string {
    return iso.slice(0, 4) === today.slice(0, 4) ? fmtDayMonth(iso) : fmtDayMonthYear(iso);
  }
  function dueDistance(iso: string): string | null {
    const days = dayDistance(today, iso);
    if (days < 0) {
      const late = -days;
      return late === 1 ? t("tasks.due.days_late_one") : t("tasks.due.days_late", { days: late });
    }
    if (days > 31) return null;
    return new Intl.RelativeTimeFormat(dateLocale(), { numeric: "auto" }).format(days, "day");
  }
  function sectionClass(bucket: DueBucket): string {
    const band = stateBandClass(dueState(bucket) as UiState);
    return band
      ? `-mx-2.5 mt-3 rounded-lg px-2.5 py-2 first:mt-0 ${band}`
      : "mt-3 border-t border-border pt-2.5 first:mt-0 first:border-t-0 first:pt-0";
  }
</script>

{#snippet partition(bucket: DueBucket)}
  {@const state = dueState(bucket) as UiState}
  {@const Mark = stateIcon(state)}
  {@const body = `mb-1 flex items-center gap-1.5 text-sm font-semibold ${stateTextClass(state)}`}
  <a href={bucketHref(bucket)} class="{body} hover:underline">
    {#if Mark}<Mark size={14} aria-hidden="true" class="shrink-0" />{/if}
    {t(dueLabelKey(bucket))}
    {#if groups[bucket].length > 0}
      <span class="text-xs font-normal tabular-nums opacity-80">({groups[bucket].length})</span>
    {/if}
  </a>
{/snippet}

{#snippet taskList(bucket: DueBucket, collapsed?: number)}
  {@const state = dueState(bucket) as UiState}
  <PanelRows rows={groups[bucket]} {collapsed} href={bucketHref(bucket)}>
    {#snippet children(shown)}
      <ul class="divide-y divide-border">
        {#each shown as task (task.id)}
          <li class="flex items-center justify-between gap-2 py-1.5">
            <span class="min-w-0 flex-1">
              <a
                href={`/tasks/${task.id}`}
                class="block truncate text-sm text-text hover:text-brand">{task.title}</a
              >
            </span>
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
                <span class="block text-text-muted">{t(`tasks.priority.${task.priority}`)}</span>
              {/if}
            </span>
          </li>
        {/each}
      </ul>
    {/snippet}
  </PanelRows>
{/snippet}

<Card title={t("dashboard.widget.tasks.portal_work")} href={listHref} linkLabel={t("nav.tasks")}>
  {#if payload.total === 0}
    <p class="text-sm text-text-muted">{t("tasks.portal.work_empty")}</p>
  {:else}
    {#if groups.overdue.length > 0}
      <section class={sectionClass("overdue")}>
        {@render partition("overdue")}
        {@render taskList("overdue")}
      </section>
    {/if}

    <section class={sectionClass("today")}>
      {@render partition("today")}
      {#if groups.today.length > 0}
        {@render taskList("today")}
      {:else}
        <p class="py-1.5 text-sm text-text-muted">{t("dashboard.my_day.nothing_today")}</p>
      {/if}
    </section>

    {#if groups.week.length > 0}
      <section class={sectionClass("week")}>
        {@render partition("week")}
        {@render taskList("week")}
      </section>
    {/if}

    {#if groups.later.length > 0}
      <section class={sectionClass("later")}>
        {@render partition("later")}
        {@render taskList("later", 3)}
      </section>
    {/if}

    {#if beyondPage > 0}
      <p class="mt-3 text-xs text-text-muted">
        <a href={listHref} class="hover:underline"
          >{t("tasks.portal.work_more", { count: beyondPage })}</a
        >
      </p>
    {/if}
  {/if}
</Card>
