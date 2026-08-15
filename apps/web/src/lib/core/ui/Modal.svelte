<script lang="ts" module>
  /**
   * Every currently-open Modal, in the order they opened. Escape belongs to the **last** one.
   *
   * The listener is on `window` and every mounted Modal registers one, open or not, so a screen
   * with two of them answered a single Escape twice (#361). Which one should answer is not "the
   * one declared first" — that is the parent, and a confirmation raised *on top of* it must be
   * what the keystroke dismisses.
   *
   * Membership is maintained by an effect, so it settles a tick after `open` does. That is the
   * behaviour the double-handling needs: a dialog opened *by* this very keystroke is not on the
   * stack yet when the later listener runs, so it cannot close itself in the same breath.
   */
  const openStack: object[] = [];
</script>

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

  /** This instance's identity on `openStack` — an object, so it is unique per Modal. */
  const token = {};

  $effect(() => {
    if (!open) return;
    openStack.push(token);
    return () => {
      const i = openStack.lastIndexOf(token);
      if (i >= 0) openStack.splice(i, 1);
    };
  });

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

  /**
   * Escape, and the two ways one keystroke used to be answered twice (#361).
   *
   * The listener is on `window` and is registered for **every mounted Modal**, open or not. A
   * screen with two of them — the import wizard and the discard confirmation it raises — had
   * both handlers run on one Escape: the wizard's opened the confirmation, and the
   * confirmation's, registered later and therefore running second, closed it again. Svelte's
   * signals settle synchronously, so the second handler already saw itself as open. The net
   * effect was an Escape that did nothing at all, which reads exactly like a dead key.
   *
   * So Escape goes to the **topmost open dialog and nowhere else**: `openStack` decides which
   * that is, and `preventDefault` marks the keystroke handled — whether the dialog closed or
   * its guard blocked the close — so nothing downstream answers it a second time. A control
   * inside that already dismissed its own dropdown claims it the same way, before this runs.
   */
  function onkeydown(e: KeyboardEvent) {
    if (!open || e.key !== "Escape" || e.defaultPrevented) return;
    if (openStack[openStack.length - 1] !== token) return;
    e.preventDefault();
    close();
  }

  /**
   * Lock the document behind the dialog while it is open (#364).
   *
   * Without this the page under a modal scrolls, and on the tallest dialog in the app — the
   * client edit form, 1445 px on a 900 px laptop — that was the difference between reaching
   * Opslaan and not: the wheel over the dim area scrolled the *page* by 600 px while the dialog
   * stood still, so the button below the fold stayed below the fold. `position: fixed` would
   * jump the page to the top; `overflow: hidden` on the element that actually scrolls does not.
   */
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
  <div class="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto p-4 sm:p-8">
    <!-- `absolute`, not `fixed`: a fixed backdrop is positioned against the viewport rather than
         against this wrapper, so it is not part of the wrapper's scroll chain and the wheel over
         it fell through to the document. It covers the same rectangle either way. -->
    <button
      type="button"
      class="absolute inset-0 min-h-full bg-neutral-900/40"
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
