import { redirect } from "@sveltejs/kit";

import { canAccessSettings } from "$lib/core/settings-nav";

import type { PageServerLoad } from "./$types";

// Instellingen is reachable by anyone who can open at least one screen inside it: an agency may
// grant `settings.branding.write` and nothing else. Each screen guards itself (issue #19), and the
// index only renders the cards this viewer can actually open (docs/UX.md) — the deployment posture
// the Service-toegang card needs comes from the section layout.
export const load: PageServerLoad = async (event) => {
  if (!canAccessSettings(event.locals.user?.permissions)) throw redirect(303, "/");
  return {};
};
