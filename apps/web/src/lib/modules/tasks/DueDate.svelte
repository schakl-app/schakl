<script lang="ts">
  /**
   * A deadline, the way every task list prints one (#395): the absolute date, and beside it how
   * far away it is.
   *
   * `18 aug` on its own requires the reader to know today's date and subtract; `18 aug · 3 dagen
   * te laat` does not. Relative-only would be worse — a distance cannot be matched against a
   * calendar, a client's mail or anything else — so both, with the relative half muted and one
   * size down.
   *
   * **Only two buckets colour the row.** `DUE_STATE` tints the section *headings*, where the
   * hierarchy the team asked for lives; a date drawn in its bucket's colour would put an amber
   * row under an amber heading and turn the whole board into the "wash of tinted cards" the
   * palette exists to prevent (#404). So over tijd and vandaag are loud here and everything else
   * is quiet — and a finished task is quiet whatever its date, because that deadline is history
   * rather than a claim.
   */
  import { fmtDayMonth } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { stateTextClass, type UiState } from "$lib/core/state";
  import { orgToday } from "$lib/core/today";
  import { dueBucket, dueDistance, DUE_STATE } from "$lib/modules/tasks/due";

  let {
    due,
    today = orgToday(),
    /** A finished task has no urgency: the caller says so and the row stays grey. */
    muted = false,
    /** Hide the relative half where the row is already crowded (the narrow dashboard tiles). */
    relative = true,
    class: extra = "",
  }: {
    due: string | null | undefined;
    today?: string;
    muted?: boolean;
    relative?: boolean;
    class?: string;
  } = $props();

  const bucket = $derived(dueBucket(due, today));
  const loud = $derived(!muted && (bucket === "overdue" || bucket === "today"));
  const state = $derived<UiState>(loud ? (DUE_STATE[bucket] ?? "neutral") : "neutral");
  const distance = $derived(due && relative ? dueDistance(due, today) : null);
  // `late` is the one state on this row worth weight: an overdue deadline is the only thing here
  // that is already wrong. The rest read as ordinary text at ordinary weight.
  const emphasis = $derived(!muted && bucket === "overdue" ? "font-semibold" : "");
</script>

{#if due}
  <span class="inline-flex items-baseline gap-1.5 whitespace-nowrap {extra}">
    <span class="text-xs tabular-nums {emphasis} {loud ? stateTextClass(state) : 'text-text-muted'}"
      >{fmtDayMonth(due)}</span
    >
    {#if distance}
      <span class="text-[11px] text-text-muted">
        {t(distance.key, { count: distance.count })}
      </span>
    {/if}
  </span>
{/if}
