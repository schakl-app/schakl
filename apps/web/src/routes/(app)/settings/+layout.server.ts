import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { settingsShellData } from "$lib/core/settings/shell.server";

import type { LayoutServerLoad } from "./$types";

/**
 * The roles and the permission catalog are shared by several screens under `settings/`, so they
 * are fetched once here and not per page: a layout load does not rerun when the user moves between
 * tabs under `settings/` (docs/PERFORMANCE.md).
 *
 * They are only fetched for someone who can actually reach a screen that uses them. `/settings/
 * account` lives under this layout too, and every member may open it — asking the API for roles
 * on their behalf would be two guaranteed 403s on every visit.
 *
 * The deployment posture rides along for the same reason: the index grid and the section rail both
 * render the Service-toegang entry from it, and a layout load answers both once.
 */

/**
 * Who needs the permission catalog. Three screens render it — Rollen, Mijn account (personal API
 * keys) and Service-toegang — and each used to fetch its own copy, so an admin who opened two of
 * them in a session paid for the same static, tenant-free document twice (#290). The gate is the
 * *union* of their permissions rather than one of them: narrowing it to `settings.roles.manage`
 * would have left the other two screens with nothing.
 */
const CATALOG_CONSUMERS = [
  "settings.roles.manage",
  "apikeys.personal.manage",
  "apikeys.service_account.manage",
] as const;

export const load: LayoutServerLoad = async (event) => {
  const locale = event.locals.locale;
  const api = apiFor(event);

  /**
   * Cloud posture (epic #199) decides whether Service-toegang exists — and permission alone cannot
   * answer that: `settings.service_access.manage` is absent from a self-hosted catalog, yet the
   * owner's `*` satisfies a check for it. It lives on the layout so the index grid and the section
   * rail read the same answer, and it is only asked for by someone who could see that card at all,
   * so an ordinary admin opening Mijn account pays nothing for it. Shared with the three screens
   * that carry the rail from outside this subtree (#229), so every rail lists the same entries.
   */
  const mayRoles = can(event.locals.user, "settings.roles.manage");
  const needsCatalog = CATALOG_CONSUMERS.some((permission) => can(event.locals.user, permission));

  const [roles, catalog, shell] = await Promise.all([
    mayRoles ? api.GET("/api/v1/roles") : undefined,
    needsCatalog ? api.GET("/api/v1/permissions/catalog") : undefined,
    settingsShellData(event),
  ]);
  return {
    roles: roles?.data ?? [],
    permissionCatalog: catalog?.data ?? null,
    locale,
    ...shell,
  };
};
