import { redirect } from "@sveltejs/kit";

import type { PageServerLoad } from "./$types";

/**
 * The old pending-email review queue (#156) grew into the full Interacties page (#168);
 * its URL survives — the dashboard widget and email_pending notifications deep-link here —
 * as the same page's pending filter state.
 */
export const load: PageServerLoad = async () => {
  throw redirect(301, "/interactions?status=pending");
};
