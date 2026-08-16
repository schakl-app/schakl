/**
 * What a contactmomenten view is **scoped to** (#323) — one vocabulary, shared by the panel
 * that links out of itself and the list page that reads the link.
 *
 * A panel is a summary and its 8-row cap is right (`docs/PERFORMANCE.md`); what was missing is
 * the paged list it is a summary *of*. `GET /api/v1/interactions` has taken all four record
 * filters since #147 — the page simply never asked, so "de 8 meest recente van 137" was the end
 * of the road and a hand-typed `?company_id=` listed everything.
 *
 * The parameter names are the API's own. They are the only set that covers all four records
 * without inventing a mapping (`task` would have to become `task_id` somewhere), `/tasks`
 * already reads `?company_id=`, and a panel therefore builds its link straight out of the
 * `prefill` it already holds instead of translating it.
 */

/** The four records a contact moment can hang on — the API's own query parameters. */
export const RECORD_FIELDS = ["company_id", "project_id", "contact_id", "task_id"] as const;

export type RecordField = (typeof RECORD_FIELDS)[number];

const RECORD_ROUTE: Record<RecordField, string> = {
  company_id: "/companies",
  project_id: "/projects",
  contact_id: "/contacts",
  task_id: "/tasks",
};

export function isRecordField(value: string): value is RecordField {
  return (RECORD_FIELDS as readonly string[]).includes(value);
}

/** The record's own page — the chip on the list links back to where the reader came from. */
export function recordHref(field: RecordField, id: string): string {
  return `${RECORD_ROUTE[field]}/${id}`;
}

/**
 * The label key naming this kind of record — `interactions.field.company` and friends, already
 * written for the form, so the chip reads exactly like the filters beside it ("Klant: Acme").
 */
export function recordLabelKey(field: RecordField): string {
  return `interactions.field.${field.slice(0, -"_id".length)}`;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The records a URL narrows the list to, in the order the chips render.
 *
 * A non-uuid is dropped rather than 422'd — these arrive from a query string anyone can edit and
 * an old bookmark can carry. It is here rather than in the load because `interactionView` below
 * has to answer "is this list scoped?" with *exactly* the same rule: a `?company_id=nonsense`
 * that the load throws away and the view rule believes would open the firehose under no chip.
 */
export function scopedRecords(params: URLSearchParams): { field: RecordField; id: string }[] {
  return RECORD_FIELDS.flatMap((field) => {
    const id = params.get(field);
    return id && UUID_RE.test(id) ? [{ field, id }] : [];
  });
}

/** The two views the list page has: the review queue, and every contact moment. */
export type InteractionView = "pending" | "all";

/**
 * Which of the two views a URL asks for — the one rule the load, the tabs and the tests share.
 *
 * It lives here rather than inline in `+page.server.ts` because it is the sort of default that
 * regresses silently: nothing crashes when it flips, the list simply stops being the queue, and
 * only somebody who already knew would notice. Three clauses, and each is a decision:
 *
 * - **Absent means the queue.** The screen opens on the work that is waiting; the *endpoint*
 *   keeps answering every status, so only this page narrows (CLAUDE.md §9, #329).
 * - **Unless the URL names a record.** A panel's "8 van 137" must land on 137, and nobody
 *   reviews a client — the same carve-out #323 made for the owner default, decided off the same
 *   fact so the two cannot disagree about which link is the firehose.
 * - **Anything that is not `pending` is everything.** `all` is what the tab writes; a stale
 *   bookmark carrying some third token gets the whole list rather than a 422, because this
 *   arrives from a query string anyone can edit.
 */
export function interactionView(params: URLSearchParams): InteractionView {
  const asked = params.get("status");
  if (asked === null) return scopedRecords(params).length > 0 ? "all" : "pending";
  return asked === "pending" ? "pending" : "all";
}

/**
 * The paged list a panel is a summary of, filtered to the panel's own record.
 *
 * `include` rides along because a project's panel rolls its tasks' moments in (#147): a link
 * that dropped it would answer 42 under a notice that had just said 137, which is the same
 * "a prefix presented as the whole answer" this exists to fix.
 *
 * Returns `null` for a host that names no record — the caller then keeps the plain notice
 * rather than drawing a link to an unfiltered list.
 */
export function interactionsListHref(
  prefill: Record<string, string | null | undefined>,
  include?: string | null,
): string | null {
  const params = new URLSearchParams();
  for (const field of RECORD_FIELDS) {
    const value = prefill[field];
    if (value) params.set(field, value);
  }
  if ([...params.keys()].length === 0) return null;
  if (include) params.set("include", include);
  return `/interactions?${params.toString()}`;
}
