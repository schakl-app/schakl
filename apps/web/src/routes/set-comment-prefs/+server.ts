/**
 * Persist how this user reads a conversation (`$lib/modules/tasks/comment-prefs`).
 *
 * Its own root route rather than a form action on the task page, for the reason `set-theme` and
 * `set-format` are: this is a personal preference that follows the user, so whichever screen
 * shows a discussion can set it without owning an action for it.
 *
 * Answers `204` and nothing else. The caller already reordered its own list — a reorder that
 * waits for a round trip is a control that feels broken — so a body here would only be an
 * invitation to re-render from it, and a failed save leaves the screen right and the next load
 * back where it was, which is the harmless direction.
 */
import { error } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";
import { asCommentSort } from "$lib/modules/tasks/comment-prefs";

import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async (event) => {
  const body: unknown = await event.request.json().catch(() => null);
  const sort = asCommentSort(
    body && typeof body === "object" ? (body as Record<string, unknown>).sort : null,
  );
  // An unknown token is a caller bug, not a preference to store: writing it back would leave a
  // blob whose next read silently falls back and looks like the save was ignored.
  if (!sort) throw error(400, "errors.invalid");
  await apiFor(event).PUT("/api/v1/prefs", { body: { prefs: { comments: { sort } } } });
  return new Response(null, { status: 204 });
};
