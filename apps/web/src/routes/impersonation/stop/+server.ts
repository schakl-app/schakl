import { redirect } from "@sveltejs/kit";

import type { RequestEvent } from "@sveltejs/kit";

import { IMPERSONATION_COOKIE } from "$lib/core/impersonation";
import { apiFor } from "$lib/core/session";

/** Ends an impersonation: audits the stop API-side, then drops the grant cookie here. */
export const POST = async (event: RequestEvent) => {
  await apiFor(event).POST("/api/v1/instance/impersonation/stop");
  event.cookies.delete(IMPERSONATION_COOKIE, { path: "/" });
  throw redirect(303, "/");
};
