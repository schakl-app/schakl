import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "@sveltejs/kit";

/**
 * What `SettingsShell` needs from the server, for a screen that carries the rail.
 *
 * The settings layout answers this once for its whole subtree; the three screens that live outside
 * `/settings/` (Taaksjablonen, Abonnementen, Domeinen — #229) each ask for it in their own load, so
 * their rail lists exactly what the section's own screens list.
 *
 * It is only asked for by someone who could see the entry it decides, so an ordinary admin opening
 * one of those screens pays nothing for it (docs/PERFORMANCE.md).
 */
export async function settingsShellData(event: RequestEvent): Promise<{ cloud: boolean }> {
  if (!can(event.locals.user, "settings.service_access.manage")) return { cloud: false };
  const instance = await apiFor(event).GET("/api/v1/meta/instance");
  return { cloud: instance.data?.deployment === "cloud" };
}
