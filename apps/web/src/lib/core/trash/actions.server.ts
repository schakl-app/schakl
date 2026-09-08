/**
 * Restore and purge, as form actions any page may spread in (docs/TRASH.md).
 *
 * The generated client types every trash path per entity (`/api/v1/trash/company/{entity_id}`),
 * so a call over a *variable* entity is cast onto the one shape they all share — the bulk
 * actions' `as const` move. The API decides whether the entity exists (404) and whether the
 * caller may (403); this only carries the answer back as the key the dialog prints.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import { trashEntity } from "./entities";

type RestorePath = "/api/v1/trash/company/{entity_id}/restore";
type PurgePath = "/api/v1/trash/company/{entity_id}";

function target(form: FormData): { entity: string; id: string } | null {
  const entity = String(form.get("entity_type") ?? "");
  const id = String(form.get("id") ?? "");
  if (!id || !trashEntity(entity)) return null;
  return { entity, id };
}

export async function restoreAction(event: RequestEvent) {
  const form = await event.request.formData();
  const picked = target(form);
  if (!picked) return fail(400, { error: "errors.not_found" });
  const { error } = await apiFor(event).POST(
    `/api/v1/trash/${picked.entity}/{entity_id}/restore` as RestorePath,
    { params: { path: { entity_id: picked.id } } },
  );
  if (error) return fail(400, { error: apiErrorKey(error).key });
  return {
    restored: { entity_type: picked.entity, id: picked.id, label: String(form.get("label") ?? "") },
  };
}

export async function purgeAction(event: RequestEvent) {
  const form = await event.request.formData();
  const picked = target(form);
  if (!picked) return fail(400, { error: "errors.not_found" });
  const { error } = await apiFor(event).DELETE(
    `/api/v1/trash/${picked.entity}/{entity_id}` as PurgePath,
    { params: { path: { entity_id: picked.id } } },
  );
  if (error) return fail(400, { error: apiErrorKey(error).key });
  return { purged: { entity_type: picked.entity, id: picked.id } };
}

export const trashActions = {
  restore: restoreAction,
  purge: purgeAction,
};
