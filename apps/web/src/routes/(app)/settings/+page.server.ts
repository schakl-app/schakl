import { redirect } from "@sveltejs/kit";

import { canAccessSettings } from "$lib/core/permissions";

import type { PageServerLoad } from "./$types";

// Instellingen is reachable by anyone who can open at least one screen inside it: an agency may
// grant `settings.branding.write` and nothing else. Each screen guards itself (issue #19).
export const load: PageServerLoad = async (event) => {
  if (!canAccessSettings(event.locals.user?.permissions)) throw redirect(303, "/");
  return {};
};
