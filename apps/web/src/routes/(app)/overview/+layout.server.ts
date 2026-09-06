import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The Overzicht section — one manager surface with six tabs (Overzicht, Omzet, Projecten,
 * Medewerkers, Uren, Marketing). A manager reaches it holding any one of the two keys that open a
 * tab (the time report, or — epic #134 — the marketing overview); each subpage re-guards its own.
 *
 * Only what *every* tab reads lives here: the member lookup, which names a colleague on the
 * landing page's team card, the employees tab's rows and the hours report's filter. The hours
 * report's five other lookups (clients, projects, tasks, statuses, entry types) moved with it to
 * `hours/+layout.server.ts` — a layout load does not rerun on filter navigation, which is why
 * they sit in a layout at all, and the landing dashboard should not pay for them.
 */
export const load: LayoutServerLoad = async (event) => {
  if (
    !can(event.locals.user, "time.report.read") &&
    !can(event.locals.user, "marketing.overview.read")
  ) {
    throw redirect(303, "/");
  }
  const members = await apiFor(event).GET("/api/v1/members/lookup");
  return {
    members: members.data ?? [],
  };
};
