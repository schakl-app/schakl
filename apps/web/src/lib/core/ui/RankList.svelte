<script lang="ts">
  /**
   * A ranking drawn as bars: the biggest clients, revenue by kind, the team's hours. Each row is
   * a name, a figure and a bar that is the row's share of the *largest* row — so the first bar
   * is always full and the rest read against it, which is what a ranking is for. The share of
   * the whole, where the caller knows it, rides beside the figure in words.
   *
   * Every name opens its record and every figure may open the rows behind it (docs/UX.md
   * Principle 7); a row with no `href` is drawn as a plain row, never as a control that goes
   * nowhere. The bar is one tint — a ranking has no verdict to colour, and a palette of ten
   * hues over ten clients tells the reader nothing the order does not.
   */
  import { stateFromTone, stateTextClass } from "$lib/core/state";
  import { stateIcon } from "$lib/core/ui/state-icons";

  export interface RankRow {
    key: string;
    label: string;
    /** Drives the bar. */
    value: number;
    /** The figure as the caller prints it. */
    valueText: string;
    /** A second line under the name — the client, "12 dagen · 80% declarabel". */
    meta?: string | null;
    /** A change or a share, beside the figure, in the tone the caller decided. */
    badge?: { text: string; tone?: "good" | "bad" | "neutral" } | null;
    href?: string | null;
  }

  let { rows, emptyText }: { rows: RankRow[]; emptyText: string } = $props();

  const max = $derived(Math.max(...rows.map((r) => r.value), 0));
</script>

{#if rows.length === 0}
  <p class="text-sm text-text-muted">{emptyText}</p>
{:else}
  <ol class="space-y-2.5">
    {#each rows as row (row.key)}
      {@const width = max > 0 ? Math.max(2, (Math.max(row.value, 0) / max) * 100) : 0}
      {@const state = stateFromTone(row.badge?.tone)}
      {@const Mark = row.badge ? stateIcon(state) : null}
      <li>
        <div class="mb-1 flex items-baseline justify-between gap-3">
          <span class="min-w-0 truncate">
            <svelte:element
              this={row.href ? "a" : "span"}
              href={row.href ?? undefined}
              class="text-sm font-medium text-text {row.href ? 'hover:text-brand' : ''}"
            >
              {row.label}
            </svelte:element>
            {#if row.meta}
              <span class="ml-2 text-xs text-text-muted">{row.meta}</span>
            {/if}
          </span>
          <span class="flex shrink-0 items-baseline gap-2 text-sm tabular-nums">
            {#if row.badge}
              <span class="flex items-center gap-0.5 text-xs {stateTextClass(state)}">
                {#if Mark}<Mark size={12} aria-hidden="true" />{/if}
                {row.badge.text}
              </span>
            {/if}
            <span class="font-semibold text-text">{row.valueText}</span>
          </span>
        </div>
        <div class="h-1.5 overflow-hidden rounded-full bg-surface">
          <div class="h-full rounded-full bg-brand/70" style="width:{width}%"></div>
        </div>
      </li>
    {/each}
  </ol>
{/if}
