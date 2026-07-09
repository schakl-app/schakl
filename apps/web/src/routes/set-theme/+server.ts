import { redirect } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";
import { asThemeMode, THEME_COOKIE, THEME_COOKIE_OPTIONS } from "$lib/core/theme-mode";

import type { RequestHandler } from "./$types";

export const POST: RequestHandler = async (event) => {
  const { request, cookies } = event;
  const form = await request.formData();
  const mode = asThemeMode(String(form.get("theme") ?? ""));
  const back = String(form.get("redirect") ?? "/");

  if (mode) {
    // Persist so it follows the user across devices — the cookie below is only this
    // browser's fast-path cache for SSR stamping (see theme-mode.ts).
    await apiFor(event).PUT("/api/v1/prefs", { body: { prefs: { appearance: { theme: mode } } } });
    cookies.set(THEME_COOKIE, mode, THEME_COOKIE_OPTIONS);
  }
  throw redirect(303, back || "/");
};
