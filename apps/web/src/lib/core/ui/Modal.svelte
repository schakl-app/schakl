<script lang="ts">
  /** Simple centered modal; closes on backdrop click or Escape. */
  import type { Snippet } from "svelte";

  let {
    open = $bindable(false),
    title,
    children,
  }: {
    open?: boolean;
    title: string;
    children: Snippet;
  } = $props();

  function onkeydown(e: KeyboardEvent) {
    if (e.key === "Escape") open = false;
  }
</script>

<svelte:window {onkeydown} />

{#if open}
  <div class="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto p-4 sm:p-8">
    <button
      type="button"
      class="fixed inset-0 bg-neutral-900/40"
      aria-label="Close"
      onclick={() => (open = false)}
    ></button>
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      class="relative z-50 mt-8 w-full max-w-lg rounded-xl border border-border bg-surface-raised p-5 shadow-xl"
    >
      <div class="mb-4 flex items-center justify-between">
        <h2 class="text-base font-semibold text-text">{title}</h2>
        <button
          type="button"
          class="text-text-muted hover:text-text"
          aria-label="Close"
          onclick={() => (open = false)}>✕</button
        >
      </div>
      {@render children()}
    </div>
  </div>
{/if}
