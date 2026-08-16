<script lang="ts">
  /** Right-hand slide-over panel (full-screen sheet on mobile); closes on backdrop click or
   *  Escape. The assistant's home (#127), but generic — Modal's conventions, docked right. */
  import type { Snippet } from "svelte";

  let {
    open = $bindable(false),
    title,
    size = "md",
    children,
  }: {
    open?: boolean;
    title: string;
    /**
     * Docked width above `sm`. `md` is the assistant's column; `2xl` is what a long *form* wants
     * — the client's thirty fields (#364), which as a centred `Modal` rendered 1445 px tall on a
     * 900 px laptop with Opslaan below the fold. Docked right and full height, the same form
     * fits and the record you are editing against stays visible beside it.
     */
    size?: "md" | "lg" | "xl" | "2xl";
    children: Snippet;
  } = $props();

  const maxWidth = {
    md: "sm:max-w-md",
    lg: "sm:max-w-lg",
    xl: "sm:max-w-xl",
    "2xl": "sm:max-w-2xl",
  } as const;

  function onkeydown(e: KeyboardEvent) {
    if (e.key === "Escape") open = false;
  }

  /** Same lock as `Modal` (#364): the page behind a docked sheet must not take the wheel. */
  $effect(() => {
    if (!open || typeof document === "undefined") return;
    const root = document.documentElement;
    const previous = root.style.overflow;
    root.style.overflow = "hidden";
    return () => {
      root.style.overflow = previous;
    };
  });
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
      class="relative z-50 flex h-full w-full flex-col border-l border-border bg-surface-raised shadow-xl {maxWidth[
        size
      ]}"
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
