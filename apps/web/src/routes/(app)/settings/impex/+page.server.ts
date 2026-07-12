import { redirect } from "@sveltejs/kit";

import { importCsvActionFor } from "$lib/core/impex/actions.server";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Import & export (issue #77): every CSV-capable entity in one place. The
 * catalog comes from the API's impex registry, filtered to what this user may actually read;
 * the per-list Export/Import buttons on companies/contacts remain the filtered accelerators.
 */
export const load: PageServerLoad = async (event) => {
  const { data } = await apiFor(event).GET("/api/v1/impex/entities");
  const entities = (data ?? []).filter((e) => can(event.locals.user, e.read_permission));
  if (entities.length === 0) throw redirect(303, "/settings");
  return {
    entities: entities.map((e) => ({
      entity_type: e.entity_type,
      importable: e.importable && can(event.locals.user, e.write_permission),
    })),
  };
};

export const actions: Actions = {
  importCsv: async (event) =>
    importCsvActionFor(event, event.url.searchParams.get("entity") ?? ""),
};
