<script lang="ts">
  /**
   * A card is not one thing (#404).
   *
   * Measured before this existed: every card on every screen was the same object — white fill,
   * `#e5e5e5` hairline, 12 px radius, 20 px padding — thirteen of them on the dashboard, twelve
   * on the client hub, eight on a task. "Uren vandaag" (one figure) and "Openstaande taken per
   * project" (twelve rows) were drawn identically, so the reader had to *parse* each box to
   * find out what kind of thing it was. That is the whole complaint: not too much information,
   * but no shape to it.
   *
   * Four kinds, with stated uses. The set is deliberately small and closed — five would be a
   * palette to choose from, and a palette is how thirteen screens end up with thirteen answers.
   *
   * | kind       | for                                    | treatment                       |
   * |------------|----------------------------------------|---------------------------------|
   * | `stat`     | one figure and its context             | no border, tinted fill          |
   * | `panel`    | a list or a form — the working surface | today's card, and the default   |
   * | `register` | occasionally consulted reference       | no fill, a hairline rule on top |
   * | `strip`    | grouped ＋ affordances                 | dashed outline, no fill         |
   *
   * **`register` is the one that carries the argument.** It is the only kind that is *not* a
   * box: a register is correct, occasionally consulted and never news, so it gets a rule and
   * the page's own background rather than a bordered rectangle competing with the working
   * surfaces above it. That is what makes the client hub's two lanes look like two lanes
   * (#364 already told the page which panels are which; it drew both identically).
   *
   * **The heading is the card's, not the caller's.** It is `PANEL_HEADING` — 14 px, dark — and
   * the band above it is 16 px, so a container can never be quieter than its contents. That
   * inversion was live on two screens: the client hub banded its registers in 12 px muted over
   * 14 px dark panel titles, and a task's seven section headings were the least legible
   * treatment in the system applied to the page's own skeleton.
   *
   * `title` is optional because a card whose first child draws its own heading row
   * (`PanelHeader`, a widget with a link) still wants the chrome. Pass `header` for that.
   */
  import type { Snippet } from "svelte";

  import { PANEL_HEADING } from "$lib/core/ui/headings";

  let {
    kind = "panel",
    title,
    level = 2,
    href,
    linkLabel,
    id,
    class: extra = "",
    header,
    children,
  }: {
    kind?: "stat" | "panel" | "register" | "strip";
    /** The card's own heading. Omit when `header` draws one, or when the card is untitled. */
    title?: string;
    /** Matches whatever the host would have rendered, so the document outline stays right. */
    level?: 2 | 3;
    /** "show all" — the list this card is a window onto. */
    href?: string;
    linkLabel?: string;
    id?: string;
    class?: string;
    /** A whole heading row of the caller's own, in place of `title`. */
    header?: Snippet;
    children: Snippet;
  } = $props();

  const CHROME: Record<string, string> = {
    // A figure needs no box: the fill is the object. `--surface-tint` separates it from the page
    // *and* from the panels beside it, which neither existing surface token could do alone.
    stat: "rounded-xl bg-surface-tint p-5",
    panel: "rounded-xl border border-border bg-surface-raised p-5",
    // No fill, no radius, no box — a rule and the page's own ground. `pt-4` reads as "under the
    // line" where a card's `p-5` reads as "inside the box", which is exactly the difference.
    //
    // **No horizontal inset, deliberately**, and it was tried the other way first. A card's text
    // sits 20 px in because it is inside a box; a register is not inside anything, so its text
    // belongs on the column's own margin — which is where the band heading above it sits, and a
    // band whose contents are indented from it by 20 px is the pair reading wrong again. The
    // cost is a small jag where a register is interleaved *between* two panels (a task's Drive
    // between Planning and Reacties), which is the honest consequence of one ordered list of
    // sections and not worth reordering the page to hide.
    register: "border-t border-border pt-4",
    strip: "rounded-xl border border-dashed border-border p-4",
  };
</script>

<section {id} class="{CHROME[kind] ?? CHROME.panel} {extra}">
  {#if header}
    {@render header()}
  {:else if title}
    <div class="mb-3 flex items-center justify-between gap-2">
      {#if level === 3}
        <h3 class={PANEL_HEADING}>{title}</h3>
      {:else}
        <h2 class={PANEL_HEADING}>{title}</h2>
      {/if}
      {#if href && linkLabel}
        <!-- "show all" is navigation, so it *is* the brand colour — the one thing brand is
             still for once states have a palette of their own (`core/state.ts`). -->
        <a {href} class="shrink-0 text-xs text-brand hover:underline">{linkLabel}</a>
      {/if}
    </div>
  {/if}
  {@render children()}
</section>
