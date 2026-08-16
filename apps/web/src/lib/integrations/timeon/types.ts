/**
 * Types and the small vocabulary the Timeon screens share. Business-licensed — see LICENSE.
 *
 * Every shape is taken from the generated client rather than restated, so a field renamed at the
 * API breaks this file at build time instead of rendering `undefined` at a user.
 */
import type { components } from "$lib/core/api/schema";

export type TimeonAccount = components["schemas"]["TimeonAccountRead"];
export type TimeonVerify = components["schemas"]["TimeonVerifyResult"];
export type TimeonRun = components["schemas"]["TimeonSyncRunRead"];
export type TimeonLink = components["schemas"]["TimeonLinkRead"];
export type TimeonConflict = components["schemas"]["TimeonConflictRead"];
export type TimeonWorkspace = components["schemas"]["TimeonWorkspaceRead"];

export type Direction = "off" | "pull" | "push" | "two_way";
export type ConflictPolicy = "manual" | "schakl_wins" | "timeon_wins";

/**
 * The compared fields, in reading order.
 *
 * Fixed here rather than taken from the object's own key order, for #373's reason one screen
 * over: **a JSONB column has no key order.** Postgres sorts by length then bytes, so a diff
 * carefully built as `{minutes, description}` comes back `{minutes, description}` on a Python
 * dict and in some other order once it has been through the database — which is invisible in
 * every offline render and only shows on a page reading a stored row.
 */
export const DIFF_ORDER = [
  "started_on",
  "start_seconds",
  "minutes",
  "project",
  "company",
  "description",
  "billable",
] as const;

/**
 * Counters a run report leads with, in reading order, and which tone each carries.
 *
 * A run's `counts` is free-shaped on purpose (a projects run and an hours run count different
 * things), so the screen decides what is worth a tile and what belongs in the long tail. `good`
 * is something that happened, `warn` is something waiting for a person, `plain` is context.
 */
export const RUN_TILES: { key: string; tone: "good" | "warn" | "plain" }[] = [
  { key: "pulled_new", tone: "good" },
  { key: "pulled", tone: "good" },
  { key: "pushed_new", tone: "good" },
  { key: "pushed", tone: "good" },
  { key: "adopted", tone: "good" },
  { key: "deleted_local", tone: "warn" },
  { key: "deleted_remote", tone: "warn" },
  { key: "conflicts", tone: "warn" },
  { key: "skipped_user", tone: "warn" },
  { key: "protected_invoiced", tone: "plain" },
  { key: "protected_approved", tone: "plain" },
  { key: "drift_local", tone: "plain" },
  { key: "drift_remote", tone: "plain" },
  { key: "in_step", tone: "plain" },
  { key: "tolerated", tone: "plain" },
  { key: "remote_read", tone: "plain" },
  { key: "local_read", tone: "plain" },
];

/** Was anything actually written, or did the run only look? */
export function runChanged(run: TimeonRun): boolean {
  const counts = (run.counts ?? {}) as Record<string, number>;
  return RUN_TILES.filter((tile) => tile.tone !== "plain").some(
    (tile) => (counts[tile.key] ?? 0) > 0,
  );
}

/** `44100` → `12:15`. A start-of-day second, as a wall clock. */
export function clockOf(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.max(0, Math.round(seconds));
  return `${String(Math.floor(total / 3600)).padStart(2, "0")}:${String(
    Math.floor((total % 3600) / 60),
  ).padStart(2, "0")}`;
}

/** `135` → `2:15`. Minutes as the duration the rest of the app prints. */
export function durationOf(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "—";
  const total = Math.max(0, Math.round(minutes));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
