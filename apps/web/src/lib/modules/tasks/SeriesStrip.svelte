<script lang="ts">
  /**
   * What a folded series row stands for, unfolded under it: the rule, and the occurrences the
   * board left out as dated links — the shape the task card's Planning section already draws
   * ("Komende taken in deze reeks"), so a series reads the same on the list and on the card.
   *
   * Fetched when it opens, never with the page: a board of fifty rows with six series on it
   * must not pay six reads for strips most visits never unfold (docs/PERFORMANCE.md). The read
   * is the series itself (`?series_id=`, any member names it), and what is drawn is the fold's
   * own complement — the *unfinished* occurrences due after today, other than the row that
   * carries them — so the strip and the count on the chip describe the same rows. Capped and
   * counted (CLAUDE.md §17): a weekly series is fifty-one chips, and "hele reeks bekijken" is
   * the view that shows every one of them, finished ones included.
   */
  import { fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { orgToday } from "$lib/core/today";
  import { recurrenceSentence, type Recurrence } from "$lib/modules/tasks/recurrence";

  interface Occurrence {
    id: string;
    title: string;
    due_date: string | null;
    completed_at: string | null;
    recurrence: Recurrence | null;
  }

  let {
    taskId,
    seriesId,
    today = orgToday(),
  }: {
    /** The row this strip sits under — left out of the list, it being the one already drawn. */
    taskId: string;
    /** The series' root (`series_root_id`), what "hele reeks bekijken" filters on. */
    seriesId: string;
    today?: string;
  } = $props();

  /** How many dated links before the rest folds into "en nog N". */
  const SHOWN = 12;

  let rows = $state<Occurrence[] | null>(null);
  let failed = $state(false);

  $effect(() => {
    const id = taskId;
    void (async () => {
      try {
        const response = await fetch(
          `/api/v1/tasks?series_id=${id}&limit=200&count=false&meta=false&sort=due_date`,
          { headers: { accept: "application/json" } },
        );
        if (!response.ok) throw new Error(String(response.status));
        rows = ((await response.json()).items ?? []) as Occurrence[];
      } catch {
        failed = true;
      }
    })();
  });

  // The rule lives on the root, and the root is in the series it roots.
  const rule = $derived(rows?.find((row) => row.recurrence)?.recurrence ?? null);
  const ahead = $derived(
    (rows ?? []).filter(
      (row) =>
        row.id !== taskId && !row.completed_at && row.due_date != null && row.due_date > today,
    ),
  );
</script>

<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" data-testid="series-strip">
  {#if failed}
    <span class="text-text-muted">{t("errors.server")}</span>
  {:else if rows === null}
    <span class="text-text-muted">{t("common.loading")}</span>
  {:else}
    {#if rule}
      <span class="text-text-muted">↻ {recurrenceSentence(rule, { compact: true })}</span>
    {/if}
    <span class="font-medium text-text-muted">
      {t("tasks.series.upcoming", { count: String(ahead.length) })}
    </span>
    <ul class="flex flex-wrap items-center gap-1.5">
      {#each ahead.slice(0, SHOWN) as row (row.id)}
        <li>
          <a
            href="/tasks/{row.id}"
            class="rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-muted ring-1 ring-inset ring-border hover:text-brand"
            title={row.title}
          >
            {row.due_date ? fmtPeriod(row.due_date) : "—"}
          </a>
        </li>
      {/each}
      {#if ahead.length > SHOWN}
        <li class="text-[11px] text-text-muted">
          {t("tasks.series.more", { count: String(ahead.length - SHOWN) })}
        </li>
      {/if}
    </ul>
    <a href="/tasks?series={seriesId}" class="font-medium text-brand hover:underline">
      {t("tasks.series.show_all")} →
    </a>
  {/if}
</div>
