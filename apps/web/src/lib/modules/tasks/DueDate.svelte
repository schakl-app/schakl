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
   *
   * **A finished task's note is when it was finished, not how late it is.** "3 dagen te laat" is
   * a sentence about today, and on a task closed last week it is simply false — and it kept
   * counting up, so a finished row read *142 dagen te laat* months later. The muted half becomes
   * "afgerond 20 aug" (`dueNote`), with the exact moment in the `title`, and says nothing at all
   * on a row shape that carries no `completed_at`.
   */
  import { fmtDayMonth, fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { stateTextClass, type UiState } from "$lib/core/state";
  import { orgToday } from "$lib/core/today";
  import { dueBucket, dueNote, DUE_STATE } from "$lib/modules/tasks/due";

  let {
    due,
    today = orgToday(),
    /** A finished task has no urgency: the caller says so and the row stays grey. */
    muted = false,
    /** When the finished task was completed (an instant) — printed instead of a distance. */
    completedAt = null,
    /** Hide the relative half where the row is already crowded (the narrow dashboard tiles). */
    relative = true,
    class: extra = "",
  }: {
    due: string | null | undefined;
    today?: string;
    muted?: boolean;
    completedAt?: string | null;
    relative?: boolean;
    class?: string;
  } = $props();

  const bucket = $derived(dueBucket(due, today));
  const loud = $derived(!muted && (bucket === "overdue" || bucket === "today"));
  const state = $derived<UiState>(loud ? (DUE_STATE[bucket] ?? "neutral") : "neutral");
  const note = $derived(due && relative ? dueNote(due, today, muted, completedAt) : null);
  // `late` is the one state on this row worth weight: an overdue deadline is the only thing here
  // that is already wrong. The rest read as ordinary text at ordinary weight.
  const emphasis = $derived(!muted && bucket === "overdue" ? "font-semibold" : "");
</script>

{#if due}
  <span class="inline-flex items-baseline gap-1.5 whitespace-nowrap {extra}">
    <span class="text-xs tabular-nums {emphasis} {loud ? stateTextClass(state) : 'text-text-muted'}"
      >{fmtDayMonth(due)}</span
    >
    {#if note && "on" in note}
      <span class="text-[11px] text-text-muted" title={fmtDateTime(note.on)}>
        {t(note.key, { date: fmtDayMonth(note.on) })}
      </span>
    {:else if note}
      <span class="text-[11px] text-text-muted">
        {t(note.key, { count: note.count })}
      </span>
    {/if}
  </span>
{/if}
