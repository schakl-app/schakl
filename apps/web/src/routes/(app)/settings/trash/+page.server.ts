import { error as httpError } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { trashActions } from "$lib/core/trash/actions.server";
import { TRASH_ENTITIES } from "$lib/core/trash/entities";
import type { components } from "$lib/core/api/schema";

import type { Actions, PageServerLoad } from "./$types";

type TrashItem = components["schemas"]["TrashItem"];
type ListPath = "/api/v1/trash/company";

/** How many of one type the screen reads. A trash is small by construction (it empties itself);
 *  past this it says how many it is not showing rather than paging a list nobody sorts. */
const PAGE = 200;

/**
 * Instellingen → Prullenbak (docs/TRASH.md): every trashed record the caller may bring back.
 *
 * One read per trashable type the caller holds the delete permission for, merged newest-first.
 * The types come from `TRASH_ENTITIES`, the rows from the API's per-entity routes — the screen
 * names no module.
 */
export const load: PageServerLoad = async (event) => {
  const entities = TRASH_ENTITIES.filter((row) => can(event.locals.user, row.permission));
  if (entities.length === 0) throw httpError(403, "errors.forbidden");
  const api = apiFor(event);
  const pages = await Promise.all(
    entities.map((row) =>
      api.GET(`/api/v1/trash/${row.entity}` as ListPath, {
        params: { query: { limit: PAGE, offset: 0 } },
      }),
    ),
  );
  const items: TrashItem[] = [];
  let total = 0;
  let retentionDays = 30;
  for (const page of pages) {
    if (!page.data) continue;
    items.push(...page.data.items);
    total += page.data.total;
    retentionDays = page.data.retention_days;
  }
  items.sort((a, b) => (a.deleted_at < b.deleted_at ? 1 : -1));
  return {
    items,
    total,
    retentionDays,
    highlight: event.url.searchParams.get("highlight") ?? "",
    locale: event.locals.locale,
  };
};

export const actions: Actions = { ...trashActions };
