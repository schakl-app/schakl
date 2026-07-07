import { redirect } from "@sveltejs/kit";

import type { PageServerLoad } from "./$types";

// Settings is a manager-only area (owner/admin).
export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.canManage) throw redirect(303, "/");
  return {};
};
