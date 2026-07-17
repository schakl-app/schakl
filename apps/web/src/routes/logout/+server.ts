import { redirect } from "@sveltejs/kit";

import { AUTH_COOKIE_NAME } from "$lib/core/auth.server";

import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async ({ cookies }) => {
  cookies.delete(AUTH_COOKIE_NAME, { path: "/" });
  throw redirect(303, "/login");
};

// POST-only on purpose (audit F26): a GET handler let `<img src=".../logout">` force-logout a
// user cross-site. Logging out is a state change and rides the same Origin protection as any form.
