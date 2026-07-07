import { redirect } from "@sveltejs/kit";

import { AUTH_COOKIE_NAME } from "$lib/core/auth.server";

import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async ({ cookies }) => {
  cookies.delete(AUTH_COOKIE_NAME, { path: "/" });
  throw redirect(303, "/login");
};

export const GET = POST;
