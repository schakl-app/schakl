<script lang="ts">
  /** Right-hand slide-over panel (full-screen sheet on mobile); closes on backdrop click or
   *  Escape. The assistant's home (#127), but generic — Modal's conventions, docked right. */
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
  <div class="fixed inset-0 z-40 flex justify-end">
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
      class="relative z-50 flex h-full w-full flex-col border-l border-border bg-surface-raised shadow-xl sm:max-w-md"
    >
      <div class="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 class="text-base font-semibold text-text">{title}</h2>
        <button
          type="button"
          class="text-text-muted hover:text-text"
          aria-label="Close"
          onclick={() => (open = false)}>✕</button
        >
      </div>
      <div class="min-h-0 flex-1 overflow-y-auto">
        {@render children()}
      </div>
    </div>
  </div>
{/if}
