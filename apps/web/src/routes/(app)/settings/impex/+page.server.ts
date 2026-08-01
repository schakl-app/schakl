import { error as httpError } from "@sveltejs/kit";

import { impexActionFor } from "$lib/core/impex/actions.server";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Import & export (issue #77): every CSV-capable entity in one place.
 *
 * The catalog comes from the API's own impex registry, so an entity a module contributes shows
 * up here with no edit — and only the entities this user may actually read. The per-list
 * Export/Import pair (`ImpexBar`) is the everyday route and carries that list's filters; this
 * screen is the overview: what can travel by spreadsheet at all, and the whole set of it.
 */
export const load: PageServerLoad = async (event) => {
  // The bulk capability gates the screen, matching the settings-nav entry. Before, someone who
  // held entity reads but not `impex.export` was hidden the card and could still deep-link in,
  // see every row, and get a bare error page on the first Download; and someone who held the
  // bulk key but no entity reads was bounced to /settings with no explanation. Refusing here
  // says which permission is missing, once.
  if (!can(event.locals.user, "impex.export")) throw httpError(403, "errors.forbidden");

  const { data } = await apiFor(event).GET("/api/v1/impex/entities");
  return {
    locale: event.locals.locale,
    entities: (data ?? [])
      .filter((e) => can(event.locals.user, e.read_permission))
      .map((e) => ({
        entity_type: e.entity_type,
        importable:
          e.importable &&
          can(event.locals.user, "impex.import") &&
          can(event.locals.user, e.write_permission),
      })),
  };
};

export const actions: Actions = {
  impex: async (event) => impexActionFor(event, event.url.searchParams.get("entity") ?? ""),
};
