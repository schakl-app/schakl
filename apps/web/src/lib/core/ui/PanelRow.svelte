<script lang="ts">
  /**
   * One row of a panel or register card: what it is, what it says about itself, what it is
   * worth, and what state it is in — in that order, on every card.
   *
   * Measured before this existed, on the client hub's register lane: six cards, six row
   * grammars. Two drew the name in the brand colour and four in dark text; one put the amount
   * inline after the name and one right-aligned it; three carried a status chip and one carried
   * the status as the *name* and then chipped it again beside itself ("Concept — Concept"); the
   * secondary fact sat under the name on one card and after it on the next. Each row read fine
   * alone. Side by side they read as six products, which is the complaint that got the lane
   * called disgusting.
   *
   * So the row is stated once. **Name** dark and medium, brand only on hover — brand is for
   * navigation chrome, and a column of brand-coloured names on a card whose every row is a link
   * says nothing a dark name does not (`docs/UX.md` §1). **Meta** under it, muted and small.
   * **Value** right-aligned and tabular, with its own muted second line where the value needs
   * qualifying (an outstanding balance under an invoice total). **Chip** last, quiet by default
   * and drawn through `StateMark` — glyph and colour together — the moment it is a claim.
   * `trailing` is for the one extra control a row may carry (a domain's link to its website),
   * placed between the meta and the value so the right edge stays value-then-chip everywhere.
   */
  import type { Snippet } from "svelte";

  import type { UiState } from "$lib/core/state";
  import StateMark from "$lib/core/ui/StateMark.svelte";

  let {
    href,
    onclick,
    title,
    meta,
    value,
    valueMeta,
    chip,
    chipState = "neutral",
    trailing,
  }: {
    /** Where the name leads. A row with neither `href` nor `onclick` draws a plain name. */
    href?: string;
    /** For a row that opens a dialog rather than a page (a list with no detail route). */
    onclick?: () => void;
    title: string;
    /** The secondary fact, under the name. */
    meta?: string | null;
    /** The figure, right-aligned. */
    value?: string | null;
    /** A qualifier under the figure. */
    valueMeta?: string | null;
    /** The status, last. Neutral is quiet; any other state carries its glyph. */
    chip?: string | null;
    chipState?: UiState;
    /** One extra control, between the name and the value. */
    trailing?: Snippet;
  } = $props();

  const nameClass = "block truncate text-sm font-medium text-text hover:text-brand";
</script>

<li class="flex items-center gap-3 py-2">
  <div class="min-w-0 flex-1">
    {#if href}
      <a {href} class={nameClass}>{title}</a>
    {:else if onclick}
      <button type="button" {onclick} class="{nameClass} w-full text-left">{title}</button>
    {:else}
      <span class="block truncate text-sm font-medium text-text">{title}</span>
    {/if}
    {#if meta}
      <span class="mt-0.5 block truncate text-xs text-text-muted">{meta}</span>
    {/if}
  </div>
  {#if trailing}{@render trailing()}{/if}
  {#if value}
    <div class="shrink-0 text-right tabular-nums">
      <span class="block text-sm text-text">{value}</span>
      {#if valueMeta}
        <span class="block text-xs text-text-muted">{valueMeta}</span>
      {/if}
    </div>
  {/if}
  {#if chip}
    {#if chipState === "neutral"}
      <span class="shrink-0 rounded-md bg-surface px-2 py-0.5 text-xs text-text-muted">{chip}</span>
    {:else}
      <StateMark state={chipState} label={chip} variant="chip" class="shrink-0" />
    {/if}
  {/if}
</li>
