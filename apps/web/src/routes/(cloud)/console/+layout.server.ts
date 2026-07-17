import { redirect } from "@sveltejs/kit";

import { fetchInstanceMe, fetchInstanceMeta } from "$lib/cloud/instance";

import type { LayoutServerLoad } from "./$types";

// The instance console (epic #199) exists only on the cloud apex host; anywhere else the
// path space belongs to the tenant app. Access mirrors the API's own gate (superuser +
// cloud posture) — the API stays the boundary.
export const load: LayoutServerLoad = async (event) => {
  const meta = await fetchInstanceMeta(event);
  if (!meta?.isInstanceHost) throw redirect(303, "/");
  const me = await fetchInstanceMe(event);
  const onLogin = event.url.pathname.startsWith("/console/login");
  if (!me?.isInstanceAdmin && !onLogin) throw redirect(303, "/console/login");
  if (me?.isInstanceAdmin && onLogin) throw redirect(303, "/console");
  return { meta, me };
};
