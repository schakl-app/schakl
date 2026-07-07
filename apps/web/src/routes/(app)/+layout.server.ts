import { redirect } from "@sveltejs/kit";

import type { LayoutServerLoad } from "./$types";

// Auth guard for the whole app area — anonymous users go to the login screen.
export const load: LayoutServerLoad = async ({ locals }) => {
  if (!locals.user) throw redirect(303, "/login");
  return { user: locals.user };
};
