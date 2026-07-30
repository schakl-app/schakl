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
 *
 * The deployment posture rides along for the same reason: the index grid and the section rail both
 * render the Service-toegang entry from it, and a layout load answers both once.
 */
export const load: LayoutServerLoad = async (event) => {
  const locale = event.locals.locale;
  const api = apiFor(event);

  /**
   * Cloud posture (epic #199) decides whether Service-toegang exists — and permission alone cannot
   * answer that: `settings.service_access.manage` is absent from a self-hosted catalog, yet the
   * owner's `*` satisfies a check for it. It lives on the layout so the index grid and the section
   * rail read the same answer, and it is only asked for by someone who could see that card at all,
   * so an ordinary admin opening Mijn account pays nothing for it.
   */
  const mayServiceAccess = can(event.locals.user, "settings.service_access.manage");

  if (!can(event.locals.user, "settings.roles.manage")) {
    const instance = mayServiceAccess ? await api.GET("/api/v1/meta/instance") : undefined;
    return {
      roles: [],
      permissionCatalog: null,
      locale,
      cloud: instance?.data?.deployment === "cloud",
    };
  }
  const [roles, catalog, instance] = await Promise.all([
    api.GET("/api/v1/roles"),
    api.GET("/api/v1/permissions/catalog"),
    mayServiceAccess ? api.GET("/api/v1/meta/instance") : undefined,
  ]);
  return {
    roles: roles.data ?? [],
    permissionCatalog: catalog.data ?? null,
    locale,
    cloud: instance?.data?.deployment === "cloud",
  };
};
