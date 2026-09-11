<script lang="ts">
  /**
   * The ↻ beside a repeating task's title, on the one row that stands for its series.
   *
   * A folded board (`?collapse_series=true`) draws a series as its current occurrence and
   * nothing else, and a row that quietly stands for eleven more is a row that lies by omission —
   * so the number rides the row ("↻ +11"), and pressing it unfolds them where the host says.
   * Only the row carrying `series_pending` draws it: an overdue occurrence shown beside its
   * current one is in the same series and is not the one the count belongs to.
   *
   * A mark with nothing folded behind it (`series_pending === 0`) still says ↻: it is the only
   * place a list says a task repeats at all.
   */
  import { t, tn } from "$lib/core/i18n";

  let {
    task,
    open = false,
    onpress,
  }: {
    task: { series_root_id?: string | null; series_pending?: number | null };
    /** The host is drawing the series unfolded right now. */
    open?: boolean;
    /** Unfold / fold. Without one the mark is a label — a host that cannot unfold draws no button. */
    onpress?: () => void;
  } = $props();

  const pending = $derived(task.series_pending ?? null);
  const label = $derived(pending ? tn("tasks.series.pending", pending) : t("tasks.series.mark"));
</script>

{#if pending != null}
  {#if onpress && pending > 0}
    <button
      type="button"
      class="relative z-10 inline-flex shrink-0 items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium tabular-nums ring-1 ring-inset
        {open
        ? 'bg-brand/10 text-brand ring-brand/30'
        : 'bg-surface text-text-muted ring-border hover:text-brand hover:ring-brand/30'}"
      title={label}
      aria-label={label}
      aria-expanded={open}
      data-testid="series-mark"
      onclick={(e) => {
        e.preventDefault();
        onpress();
      }}
    >
      ↻ +{pending}
    </button>
  {:else}
    <span
      class="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-surface px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-text-muted ring-1 ring-inset ring-border"
      title={label}
      aria-label={label}
      data-testid="series-mark"
    >
      ↻{#if pending > 0}&nbsp;+{pending}{/if}
    </span>
  {/if}
{/if}
