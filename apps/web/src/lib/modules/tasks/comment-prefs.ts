/**
 * How this user reads a conversation (#312 follow-up).
 *
 * The task card rendered comments oldest-first, which is right for a thread of three and wrong
 * for a task an agency has been talking on for a year: the thing you came back for is the last
 * thing said, and it was fifty rows down. Two rules came out of fixing it, and only one of them
 * is a preference.
 *
 * **The order of *threads* is a reading preference; the order of *answers* inside one is not.**
 * A reply has to follow the question it answers or the conversation stops being one, so
 * `TaskComments` sorts openers by this and always renders their replies oldest-first.
 *
 * **The default is newest-first.** A chat pins its viewport to the bottom, so oldest-first costs
 * its reader nothing; a section on a record page does not, and inheriting the chat convention is
 * what put the news at the bottom of the page. Someone who reads a task as a story flips it, and
 * the choice follows them across devices — it lives in the same per-user blob as the column
 * layouts and the theme (`/api/v1/prefs`, namespace `comments`), never in the URL: this is how
 * one person reads, not which records are on screen (docs/UX.md, §9's "the URL is the view" is
 * about *what* is listed).
 */

export type CommentSort = "newest" | "oldest";

export const DEFAULT_COMMENT_SORT: CommentSort = "newest";

/** Anything but the two tokens is "no choice made" — an old blob, or a hand-edited one. */
export function asCommentSort(value: unknown): CommentSort | null {
  return value === "newest" || value === "oldest" ? value : null;
}

/** This user's thread order out of the free-form prefs blob, falling back to the default. */
export function readCommentSort(prefs: unknown): CommentSort {
  if (!prefs || typeof prefs !== "object") return DEFAULT_COMMENT_SORT;
  const section = (prefs as Record<string, unknown>).comments;
  if (!section || typeof section !== "object") return DEFAULT_COMMENT_SORT;
  return asCommentSort((section as Record<string, unknown>).sort) ?? DEFAULT_COMMENT_SORT;
}
