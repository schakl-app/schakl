/**
 * How a website's monitoring state is drawn (#356).
 *
 * The column headed *Uptime* used to paint a green pill reading "Uptime" whenever
 * `uptime_enabled` was true — the chip's text was the column's own name, and its colour was this
 * app's healthy state. A site that had been down for two hours looked exactly like one that was
 * up, because the only input was a tick in a box. Two rules come out of fixing it, and both live
 * here so no screen can answer differently:
 *
 * - **The colour follows the observation, never the flag.** Green only for a site something has
 *   actually seen answering; red for one it has seen failing.
 * - **"Monitored, nothing measured yet" is its own state**, not a quiet green. It reads as a
 *   neutral outline and says so in words, because collapsing it into either colour is a claim
 *   nobody made.
 */
import { t } from "$lib/core/i18n";

export type UptimeState = "up" | "down" | "pending" | "maintenance" | "unknown";

/** The chip's state for a row. `null` when the site is not monitored — draw nothing. */
export function uptimeState(site: {
  uptime_enabled?: boolean;
  uptime_status?: string | null;
}): UptimeState | null {
  if (!site.uptime_enabled) return null;
  const status = site.uptime_status;
  if (status === "up" || status === "down" || status === "pending" || status === "maintenance") {
    return status;
  }
  return "unknown";
}

const CLASSES: Record<UptimeState, string> = {
  up: "bg-green-500/10 text-green-700 dark:text-green-400",
  down: "bg-red-500/10 text-red-700 dark:text-red-400",
  pending: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  maintenance: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
  // Outline, not a fill: nothing has been measured, so nothing is being claimed.
  unknown: "border border-border text-text-muted",
};

export function uptimeChipClass(state: UptimeState): string {
  return CLASSES[state];
}

export function uptimeLabel(state: UptimeState): string {
  return t(`websites.uptime_state.${state}`);
}
