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
  <!-- The scroll port. It is transparent and carries no padding of its own: everything that has
       to grow with a tall dialog lives in the wrapper below, which is the element the backdrop
       is measured against. `overscroll-contain` keeps a flick past the end from reaching the
       page, which the overflow lock above already froze. -->
  <div class="fixed inset-0 z-40 overflow-y-auto overscroll-contain">
    <!-- `min-h-full` so a short dialog still dims the whole viewport, and ordinary flow so a tall
         one makes this taller than the port. The backdrop is `absolute` against *this* — not
         against the port and not `fixed` — because those two both measure one viewport and stop:
         on a 2 555 px meeting note the dim ended 720 px down and the rest of the page sat there
         at full brightness, reading as a broken dialog rather than a long one. And of those two
         wrong answers, `fixed` is the worse one: it is outside the port's scroll chain, so the
         wheel over the dim area fell through to the document (#364). -->
    <div class="relative flex min-h-full items-start justify-center p-4 sm:p-8">
      <button
        type="button"
        class="absolute inset-0 bg-neutral-900/40"
        aria-label="Close"
        onclick={close}
      ></button>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        class="relative z-50 mt-8 w-full {maxWidth[
          size
        ]} rounded-xl border border-border bg-surface-raised shadow-xl"
      >
        <!-- Sticky, so the title says what you are reading and the ✕ stays reachable however far
             down the body you are — the alternative on a long one is scrolling back up to close
             it. Opaque and ruled, or the body scrolling underneath shows through it; the same
             header shape `SlideOver` already uses. `line-clamp-2` because this title is an
             e-mail subject on the surface that needed the sticky header in the first place, and
             an unbounded one would push the body off the screen it is pinned to. -->
        <div
          class="sticky top-0 z-10 flex items-start justify-between gap-3 rounded-t-xl border-b border-border bg-surface-raised px-5 py-3"
        >
          <h2 class="line-clamp-2 break-words text-base font-semibold text-text" {title}>
            {title}
          </h2>
          <button
            type="button"
            class="shrink-0 leading-6 text-text-muted hover:text-text"
            aria-label="Close"
            onclick={close}>✕</button
          >
        </div>
        <div class="p-5">
          {@render children()}
        </div>
      </div>
    </div>
  </div>
{/if}
