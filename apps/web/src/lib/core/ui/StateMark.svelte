<script lang="ts">
  /**
   * One semantic state, drawn (#404) — and the reason the palette is a component rather than
   * only a class map.
   *
   * "Never colour alone" is a rule nobody can keep by remembering it: a class helper hands back
   * a red, the caller spreads it onto a `<span>`, and the glyph is the part that gets left out
   * every time under deadline. So the glyph is not the caller's to forget. `variant="text"`
   * puts the mark in front of the words, `variant="chip"` wraps the whole thing in the pill,
   * and `neutral` draws no glyph in either (see `core/state.ts` — the absence *is* the mark).
   *
   * The label is a word, never a bare figure: a state is a claim, and a claim needs saying. A
   * caller that wants the figure coloured too passes it as `children`, which sits after the
   * label in the same colour.
   */
  import type { Snippet } from "svelte";

  import { stateChipClass, stateTextClass, type UiState } from "$lib/core/state";
  import { stateIcon } from "$lib/core/ui/state-icons";

  let {
    state,
    label,
    variant = "text",
    size = 14,
    class: extra = "",
    children,
  }: {
    state: UiState;
    /** What the state *says*. Read out by screen readers as the state itself. */
    label: string;
    variant?: "text" | "chip";
    size?: number;
    class?: string;
    /** Anything that should carry the same colour — usually the figure the state is about. */
    children?: Snippet;
  } = $props();

  const Icon = $derived(stateIcon(state));
</script>

<span
  class="inline-flex items-center gap-1.5 {variant === 'chip'
    ? `rounded-full px-2 py-0.5 text-xs font-medium ${stateChipClass(state)}`
    : `text-sm font-medium ${stateTextClass(state)}`} {extra}"
>
  {#if Icon}
    <Icon size={variant === "chip" ? size - 2 : size} aria-hidden="true" class="shrink-0" />
  {/if}
  <span class="min-w-0">{label}</span>
  {#if children}{@render children()}{/if}
</span>
