/**
 * The app's one toast queue (#364).
 *
 * The app had no toast primitive at all, and the gap showed on the client page: saving closed a
 * dialog, changed one value somewhere in a 4116 px document, and said nothing — if you had
 * scrolled, the save was indistinguishable from a no-op. **A save must say so**, and the place it
 * says so cannot be "next to the field", because the field is usually off screen by then.
 *
 * Rules this deliberately keeps small:
 *
 * - **A toast reports, it never asks.** No buttons, no confirmations, nothing that must be read
 *   before the user may continue — that is a dialog, and a dialog it is not. The one exception the
 *   shape allows is an `undo` action, which is an *offer*: ignoring it is a valid answer.
 * - **It is not an error channel.** A form's own error text stays inside the form beside the
 *   control that produced it (docs/UX.md); an `error` toast is for something the user cannot see
 *   from where they are, like a background save that failed after they scrolled away.
 * - **It never carries the only copy of anything.** Everything a toast says is also true of the
 *   page behind it, which is what makes auto-dismissal safe.
 *
 * A module store, not a context: one queue per document, reachable from a `+page.svelte`, a panel
 * five components down and a `use:enhance` callback alike, none of which share a component tree
 * with the renderer in the app shell.
 */

export type ToastTone = "success" | "info" | "error";

export interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
  /** An offer, never a requirement — see the module docstring. */
  undo?: () => void;
}

/** How long a toast stays. An error lingers: the reader did not go looking for it. */
const DURATION: Record<ToastTone, number> = {
  success: 3500,
  info: 4000,
  error: 7000,
};

let seq = 0;
const items = $state<Toast[]>([]);

/** The live queue, oldest first — read by `ToastHost` and by nothing else. */
export function toasts(): Toast[] {
  return items;
}

export function dismissToast(id: number): void {
  const at = items.findIndex((toast) => toast.id === id);
  if (at !== -1) items.splice(at, 1);
}

function push(message: string, tone: ToastTone, undo?: () => void): number {
  const id = ++seq;
  items.push({ id, message, tone, undo });
  // SSR has no timers worth setting and no one to read the toast; the browser owns dismissal.
  if (typeof window !== "undefined") {
    window.setTimeout(() => dismissToast(id), DURATION[tone]);
  }
  return id;
}

/** "Opgeslagen." — the confirmation a save owes the reader. */
export function toastSuccess(message: string, undo?: () => void): number {
  return push(message, "success", undo);
}

export function toastInfo(message: string): number {
  return push(message, "info");
}

export function toastError(message: string): number {
  return push(message, "error");
}
