import { redirect } from "@sveltejs/kit";

import { LOCALE_COOKIE, LOCALES } from "$lib/core/i18n";

import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async ({ request, cookies }) => {
  const form = await request.formData();
  const locale = String(form.get("locale") ?? "");
  const back = String(form.get("redirect") ?? "/");

  if ((LOCALES as readonly string[]).includes(locale)) {
    cookies.set(LOCALE_COOKIE, locale, {
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
      sameSite: "lax",
    });
  }
  throw redirect(303, back || "/");
};
