/**
 * What the task board is grouped by (#395) — the URL token, its default, and the sort each
 * grouping implies.
 *
 * The board used to offer exactly two orderings and neither of them was *when is this due*: the
 * sections were the tenant's statuses and the rows inside them were in whatever order somebody
 * last dragged them. So this week's work sat below a fortnight of later work whenever it
 * happened to carry a different status, and the two overdue tasks were at positions 5 and 7.
 *
 * Deadline is what the board **opens** on; status stays one click away, because a status board
 * is a real way to run a week and the hand-dragged order inside it is the whole point of one.
 *
 * The choice lives in the **URL**, not in the saved table preference. It is a view (CLAUDE.md
 * §9): a colleague pasting `/tasks?group=status` into a chat must get the board they are looking
 * at, and the back button must undo a switch. The page size next to it is a saved default
 * precisely because it is *not* a view — two tabs would fight over one number.
 */
export const TASK_GROUPINGS = ["due", "status"] as const;
export type TaskGrouping = (typeof TASK_GROUPINGS)[number];

/** The board's own default. Absent means deadline; `?group=status` is the other view. */
export const DEFAULT_GROUPING: TaskGrouping = "due";

/**
 * The API sort a deadline-grouped board asks for: the composite "deadline first, then priority,
 * highest first" (`apps/api/app/modules/tasks/service.py::SORTABLE`).
 *
 * A plain `due_date` would answer only the first half of *"eerst naar de datum en daarna naar de
 * prioriteit"* — ties would fall back to the board's dragged order, which is the thing this
 * whole change is about.
 */
export const DUE_SORT = "due";

/**
 * Read the token off the URL. Anything unrecognised falls back rather than raising: a grouping
 * arrives from a query string anyone can edit and an old bookmark can carry, and a stale link
 * should show the default board, not an error page (#316's rule for `?period=`).
 */
export function resolveGrouping(value: string | null | undefined): TaskGrouping {
  return (TASK_GROUPINGS as readonly string[]).includes(value ?? "")
    ? (value as TaskGrouping)
    : DEFAULT_GROUPING;
}
