import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The roles and the permission catalog are shared by two screens (Rollen and Gebruikers), so they
 * are fetched once here and not per page: a layout load does not rerun when the user moves between
 * tabs under `settings/` (docs/PERFORMANCE.md).
 *
 * They are only fetched for someone who can actually manage roles. `/settings/account` lives under
 * this layout too, and every member may open it — asking the API for roles on their behalf would be
 * two guaranteed 403s on every visit.
 */
export const load: LayoutServerLoad = async (event) => {
  const locale = event.locals.locale;
  if (!can(event.locals.user, "settings.roles.manage")) {
    return { roles: [], permissionCatalog: null, locale };
  }
  const api = apiFor(event);
  const [roles, catalog] = await Promise.all([
    api.GET("/api/v1/roles"),
    api.GET("/api/v1/permissions/catalog"),
  ]);
  return {
    roles: roles.data ?? [],
    permissionCatalog: catalog.data ?? null,
    locale,
  };
};
