<script lang="ts">
  /**
   * A composed panel's own heading row, for the panels that carry a control beside the title
   * (#364).
   *
   * The host page draws `<h2>{t(panel.title_key)}</h2>` for every panel it composes, so a panel
   * with its own ✎ or ⋯ had nowhere to put it and pushed a control row *underneath* the heading
   * — a band of empty card with one button floating in it, which is what the contactpersonen and
   * gegevens cards looked like. A panel that declares `ownsHeader` renders this instead and the
   * page draws no heading of its own, so the title and its controls share one line.
   *
   * The title still comes from the API's `title_key` and still arrives as a prop: a panel does
   * not get to rename itself just because it draws its own header.
   */
  import type { Snippet } from "svelte";

  let {
    title,
    level = 2,
    children,
  }: {
    title: string;
    /** Matches whatever the host would have rendered, so the document outline stays right. */
    level?: 2 | 3;
    /** The controls, right-aligned on the heading line. */
    children?: Snippet;
  } = $props();
</script>

<div class="mb-4 flex items-start justify-between gap-3">
  {#if level === 3}
    <h3 class="text-sm font-semibold text-text">{title}</h3>
  {:else}
    <h2 class="text-sm font-semibold text-text">{title}</h2>
  {/if}
  {#if children}
    <div class="flex shrink-0 items-center gap-2">{@render children()}</div>
  {/if}
</div>
