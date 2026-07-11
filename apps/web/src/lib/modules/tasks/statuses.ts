/**
 * Tenant-configurable task statuses on the web (issue #62).
 *
 * The board's sections, the pill on every row and the status picker all read from the org's
 * configured list (loaded once in the `/tasks` layout load) instead of the old hardcoded
 * `open`/`in_progress`/`done`. Colors are shared palette tokens, so a status chip renders with the
 * same classes as a label.
 */
import type { components } from "$lib/core/api/schema";

export type TaskStatusDef = components["schemas"]["StatusRead"];

/** Board sections in the tenant's configured order (a terminal section starts folded). */
export function statusGroups(
  statuses: TaskStatusDef[],
): { key: string; label: string; collapsible: boolean }[] {
  return statuses.map((s) => ({ key: s.key, label: s.name, collapsible: true }));
}

/** Keys of terminal (finished) statuses — folded away by default, like "done" always was. */
export function terminalKeys(statuses: TaskStatusDef[]): string[] {
  return statuses.filter((s) => s.is_terminal).map((s) => s.key);
}

/** The status a fresh task starts in: the configured default, else the first in order. */
export function defaultStatusKey(statuses: TaskStatusDef[]): string {
  return (statuses.find((s) => s.is_default) ?? statuses[0])?.key ?? "open";
}

/** The first terminal status — where "mark complete" moves a task. */
export function terminalStatusKey(statuses: TaskStatusDef[]): string {
  return statuses.find((s) => s.is_terminal)?.key ?? defaultStatusKey(statuses);
}

export function statusByKey(statuses: TaskStatusDef[], key: string): TaskStatusDef | undefined {
  return statuses.find((s) => s.key === key);
}

/** Is this key a finished state? Drives the row's complete-toggle and struck-through title. */
export function isTerminalKey(statuses: TaskStatusDef[], key: string): boolean {
  return statusByKey(statuses, key)?.is_terminal ?? false;
}
