<script lang="ts">
  /**
   * The dashboard tile chrome (#166): the bordered card + title row every My Day widget wears.
   * The dashboard grid wraps tiles in a bare <div>, so the card look is the widget's own job —
   * compose this instead of re-typing the four Tailwind classes (docs/UX.md, widget convention).
   */
  import type { Snippet } from "svelte";

  let {
    title,
    href,
    linkLabel,
    children,
  }: {
    title: string;
    /** Optional "show all" link in the header, e.g. the module's own list page. */
    href?: string;
    linkLabel?: string;
    children: Snippet;
  } = $props();
</script>

<div class="rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-3 flex items-center justify-between gap-2">
    <h2 class="text-sm font-semibold text-text">{title}</h2>
    {#if href && linkLabel}
      <a {href} class="shrink-0 text-xs text-brand hover:underline">{linkLabel}</a>
    {/if}
  </div>
  {@render children()}
</div>
