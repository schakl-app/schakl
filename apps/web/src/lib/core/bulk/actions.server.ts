/**
 * The two form actions behind every list's bulk menu.
 *
 * The `impexAction(event, entity)` precedent, applied to a selection: one shared pair, spread
 * into each list's own `actions` object, so twelve screens post the same two things the same
 * way instead of growing twelve hand-written fan-outs. (The subscriptions list had one of those
 * — a per-id loop through the single-record endpoint — which meant a partial batch was however
 * far the loop got before it gave up.)
 *
 * Both answer `bulkResult`, never a bare `ok`: a batch's honest answer is "37 done, 3 skipped,
 * and here is why". The API reports the rows it could not do instead of rolling the good ones
 * back, and a UI that swallowed that would be claiming work it did not do (docs/UX.md, #299).
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { BulkDeleteEntity, BulkOutcome, BulkUpdateEntity } from "./types";

/**
 * The selection, however the form spelled it.
 *
 * A dialog posts one comma-joined field and a `ConfirmDialog` posts the same; a caller with one
 * hidden input per id also works. Being tolerant here is what let the subscriptions list keep
 * its old markup while its server half was replaced.
 */
function ids(form: FormData): string[] {
  return form
    .getAll("ids")
    .flatMap((value) => String(value).split(","))
    .map((id) => id.trim())
    .filter(Boolean);
}

/**
 * The API's `{succeeded, failed[]}` folded into what the banner renders.
 *
 * `kind` is narrowed to this pair here, though `BulkOutcome` allows any verb: these two actions
 * only ever do these two things, and the banner's `bulk.done_update` / `bulk.done_delete` are
 * the only sentences that exist for them.
 */
function outcome(
  kind: "update" | "delete",
  data: { succeeded: number; failed?: { id: string; error: string }[] | null },
): { bulkResult: BulkOutcome } {
  const failed = data.failed ?? [];
  return {
    bulkResult: {
      kind,
      succeeded: data.succeeded,
      failed: failed.length,
      // The distinct reasons, so the banner can say *why* rows were skipped rather than only
      // how many — "already gone" and "needs a reason for that date" want different answers.
      reasons: [...new Set(failed.map((row) => row.error))],
    },
  };
}

/**
 * One failure shape for the whole action, `bulkFields` and all.
 *
 * Not tidiness: `ActionData` is the union of what a page's actions return, and a key that
 * appears in only *some* branches is not a key the page may read — `form?.bulkFields` then
 * fails to type-check on the branches that never mention it. Saying `null` out loud is what
 * keeps the dialog able to ask.
 */
function refuse(error: string, bulkFields: Record<string, string> | null = null) {
  return fail(400, { error, bulkFields });
}

export async function bulkUpdateAction(event: RequestEvent, entity: BulkUpdateEntity) {
  const form = await event.request.formData();
  const selection = ids(form);
  if (selection.length === 0) return refuse("errors.required");
  let values: Record<string, string | null>;
  try {
    values = JSON.parse(String(form.get("values") ?? "{}"));
  } catch {
    return refuse("errors.validation");
  }
  // A save that names no field would report "12 updated" having changed nothing at all.
  if (Object.keys(values).length === 0) return refuse("errors.required");

  const { data, error } = await apiFor(event).POST(`/api/v1/bulk/${entity}/update` as const, {
    body: { ids: selection, values } as never,
  });
  if (error || !data) {
    // A rejected *value* names its field (the API answers 422 with `fields`), so the dialog
    // can put the message under the control that caused it instead of over the whole form.
    const parsed = apiErrorKey(error);
    return refuse(parsed.key, parsed.fields ?? null);
  }
  return outcome("update", data);
}

export async function bulkDeleteAction(event: RequestEvent, entity: BulkDeleteEntity) {
  const form = await event.request.formData();
  const selection = ids(form);
  if (selection.length === 0) return refuse("errors.required");
  const { data, error } = await apiFor(event).POST(`/api/v1/bulk/${entity}/delete` as const, {
    body: { ids: selection } as never,
  });
  if (error || !data) return refuse(apiErrorKey(error).key);
  return outcome("delete", data);
}
