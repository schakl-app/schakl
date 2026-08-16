/**
 * "May this viewer edit *this* task?" — the browser's mirror of
 * `TaskService._ensure_task_writable` (apps/api/app/modules/tasks/service.py).
 *
 * `tasks.task.write` is scoped, and `:own` means **assignee** (#12). The seeded `member` role
 * holds `:own`, so on any ordinary agency the *base-key* check — which is what docs/UX.md's
 * #253 rule prescribes, and rightly, for a list's "Nieuw" button — answers `true` on every row
 * in the list, including forty colleagues' tasks. The complete-toggle, the ⋯ → Bewerken and
 * every checklist tick then rendered for a member who gets a 403 the moment they use one.
 *
 * So a control the API refines **per row** is gated per row. The base key still decides the
 * *list-level* controls (Nieuw, the bulk ✎), because those are not about any one record.
 *
 * The relative import is deliberate, and is the same accommodation `core/permissions.ts` makes
 * for `settings-nav.ts`: `tests/unit` runs on node's own test runner, which resolves neither
 * the `$lib` alias nor an extensionless specifier.
 */
import { can, type PermissionHolder } from "../../core/permissions.ts";

/** All a write check needs from a task. Every task shape the web holds carries this. */
export interface TaskOwnership {
  /**
   * The whole roster (#375). `:own` means **any** assignee, not the starred one —
   * `caller_may_write_task` says so in as many words, and a second assignee's PATCH of the
   * task succeeds — so a mirror that reads only the primary hides controls the API would
   * have allowed. Optional because a row loaded before the roster existed carries only the
   * mirror below; absent here falls back to it rather than refusing.
   */
  assignees?: { user_id: string }[] | null;
  /** The primary, mirrored by the API. The compatibility half of the pair. */
  assignee_user_id?: string | null;
}

/** All a write check needs from the viewer: who they are, and what they hold. */
export interface TaskViewer extends PermissionHolder {
  id?: string | null;
}

/**
 * Mirrors the API exactly: `:any` writes anything, `:own` writes the task assigned to you,
 * and an unassigned task is nobody's — so only `:any` reaches it.
 */
export function canWriteTask(
  user: TaskViewer | null | undefined,
  task: TaskOwnership | null | undefined,
): boolean {
  if (can(user, "tasks.task.write", "any")) return true;
  if (!user?.id || !task) return false;
  const roster = task.assignees?.length
    ? task.assignees.map((entry) => entry.user_id)
    : task.assignee_user_id
      ? [task.assignee_user_id]
      : [];
  return roster.includes(user.id) && can(user, "tasks.task.write", "own");
}
