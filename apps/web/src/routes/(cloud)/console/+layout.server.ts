import { redirect } from "@sveltejs/kit";

import { fetchInstanceMe, fetchInstanceMeta } from "$lib/cloud/instance";
import { loginPath, safeInternalPath } from "$lib/core/redirect";

import type { LayoutServerLoad } from "./$types";

// The instance console (epic #199) exists only on the cloud apex host; anywhere else the
// path space belongs to the tenant app. Access mirrors the API's own gate (superuser +
// cloud posture) — the API stays the boundary.
//
// The turn-away carries where they were headed, exactly as the tenant app's does: a console
// deep link is if anything the more likely one to be pasted into a chat, because it is what one
// instance owner sends another to look at a particular org. `/console` is this area's landing
// page, so being turned away from it needs no `next`.
export const load: LayoutServerLoad = async (event) => {
  const meta = await fetchInstanceMeta(event);
  if (!meta?.isInstanceHost) throw redirect(303, "/");
  const me = await fetchInstanceMe(event);
  const onLogin = event.url.pathname.startsWith("/console/login");
  if (!me?.isInstanceAdmin && !onLogin) {
    throw redirect(303, loginPath(event.url, { base: "/console/login", home: "/console" }));
  }
  if (me?.isInstanceAdmin && onLogin) {
    // Already signed in and standing on the login screen — finish the journey the `next` on the
    // URL describes, so a deep link followed with a live session behaves like one followed
    // without it.
    throw redirect(303, safeInternalPath(event.url.searchParams.get("next")) ?? "/console");
  }
  return { meta, me };
};
