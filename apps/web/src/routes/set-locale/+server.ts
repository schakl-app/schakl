import { redirect } from "@sveltejs/kit";

import { LOCALE_COOKIE, LOCALES } from "$lib/core/i18n";
import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async (event) => {
  const { request, cookies } = event;
  const form = await request.formData();
  const locale = String(form.get("locale") ?? "");
  const back = String(form.get("redirect") ?? "/");

  if ((LOCALES as readonly string[]).includes(locale)) {
    // Cookie = the fast per-request signal Paraglide reads during SSR.
    cookies.set(LOCALE_COOKIE, locale, {
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
      sameSite: "lax",
    });
    // Persist as the user's own preference so it follows them across devices (best-effort:
    // harmless 401 if not signed in — the cookie still applies for this browser).
    await apiFor(event).PATCH("/api/v1/meta/me", { body: { locale } });
  }
  throw redirect(303, back || "/");
};
