<script lang="ts">
  /** Simple centered modal; closes on backdrop click or Escape. */
  import type { Snippet } from "svelte";

  let {
    open = $bindable(false),
    title,
    size = "lg",
    closeGuard,
    children,
  }: {
    open?: boolean;
    title: string;
    /** Max width; wider ones exist for surfaces that carry a table (the schedule picker, #188)
     *  or a rendered document beside its controls (the invoice template editor). */
    size?: "lg" | "xl" | "2xl" | "3xl" | "5xl";
    /**
     * Asked before every way out — Escape, the backdrop, the ✕ — and a `false` blocks it (#361).
     *
     * For the one kind of modal where closing destroys work that cannot be recovered: the
     * import wizard holds a pasted table of up to 2 000 rows and a column mapping the user
     * built by hand, none of it staged anywhere. The guard is what lets the host raise its own
     * "are you sure?" instead of losing all of it to a mis-aimed click. Modals with nothing to
     * lose leave it off and keep closing the moment they are asked to.
     */
    closeGuard?: () => boolean;
    children: Snippet;
  } = $props();

  /** Every exit goes through here, so the guard cannot be bypassed by one of the three. */
  function close() {
    if (closeGuard && !closeGuard()) return;
    open = false;
  }

  const maxWidth = {
    lg: "max-w-lg",
    xl: "max-w-xl",
    "2xl": "max-w-2xl",
    "3xl": "max-w-3xl",
    "5xl": "max-w-5xl",
  } as const;

  function onkeydown(e: KeyboardEvent) {
    // `defaultPrevented` is how a control inside says it already answered this Escape — a
    // combobox dismissing its own dropdown, say. Closing the dialog on top of that makes the
    // reflex keystroke destroy the surface instead of the popup (#361).
    if (e.key === "Escape" && !e.defaultPrevented) close();
  }
</script>

<svelte:window {onkeydown} />

{#if open}
  <div class="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto p-4 sm:p-8">
    <button
      type="button"
      class="fixed inset-0 bg-neutral-900/40"
      aria-label="Close"
      onclick={close}
    ></button>
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      class="relative z-50 mt-8 w-full {maxWidth[
        size
      ]} rounded-xl border border-border bg-surface-raised p-5 shadow-xl"
    >
      <div class="mb-4 flex items-center justify-between">
        <h2 class="text-base font-semibold text-text">{title}</h2>
        <button
          type="button"
          class="text-text-muted hover:text-text"
          aria-label="Close"
          onclick={close}>✕</button
        >
      </div>
      {@render children()}
    </div>
  </div>
{/if}
