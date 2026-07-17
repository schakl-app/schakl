import { redirect } from "@sveltejs/kit";

import { asLocale, LOCALE_COOKIE, LOCALE_COOKIE_OPTIONS } from "$lib/core/i18n";
import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async (event) => {
  const { request, cookies } = event;
  const form = await request.formData();
  const locale = asLocale(String(form.get("locale") ?? ""));
  const back = String(form.get("redirect") ?? "/");

  if (locale) {
    // Persist as the user's own preference — that is the source of truth, and it follows them
    // across devices (best-effort: a harmless 401 when signed out, where the cookie is all
    // there is). `hooks.server.ts` reads it back on every request.
    await apiFor(event).PATCH("/api/v1/meta/me", { body: { locale } });
    // Cookie = the per-browser cache Paraglide reads during SSR and while hydrating.
    cookies.set(LOCALE_COOKIE, locale, LOCALE_COOKIE_OPTIONS);
  }
  // Only a same-origin relative path — never an absolute or protocol-relative URL (audit F26).
  throw redirect(303, back.startsWith("/") && !back.startsWith("//") ? back : "/");
};
