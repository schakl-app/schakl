import { error, redirect } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

// Instance diagnostics, not org config — but still manager-only: exact versions and the
// dependency topology are reconnaissance on an internet-facing, self-hosted box.
// One API call; the endpoint assembles every probe server-side (docs/PERFORMANCE.md).
export const load: PageServerLoad = async (event) => {
  if (!event.locals.user?.canManage) throw redirect(303, "/");

  const { data, error: apiError } = await apiFor(event).GET("/api/v1/system/info");
  if (apiError || !data) throw error(502, "settings.system.unavailable");

  return { info: data };
};
