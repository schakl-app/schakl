import { redirect } from "@sveltejs/kit";

import {
  asClock,
  asDateFormat,
  DEFAULT_CLOCK,
  DEFAULT_DATE_FORMAT,
  FORMAT_COOKIE,
  FORMAT_COOKIE_OPTIONS,
  type FormatPrefs,
  parseFormatCookie,
  serializeFormatCookie,
} from "$lib/core/dateformat";
import { apiFor } from "$lib/core/session";

import type { RequestHandler } from "./$types";

// One endpoint for both formatting choices; the form posts whichever field changed, and the other
// keeps its current value (read back from the cookie) so a partial submit never resets it.
export const POST: RequestHandler = async (event) => {
  const { request, cookies } = event;
  const form = await request.formData();
  const back = String(form.get("redirect") ?? "/");

  const current = parseFormatCookie(request.headers.get("cookie"));
  const next: FormatPrefs = {
    clock: asClock(String(form.get("clock") ?? "")) ?? current.clock ?? DEFAULT_CLOCK,
    date: asDateFormat(String(form.get("date") ?? "")) ?? current.date ?? DEFAULT_DATE_FORMAT,
  };

  // Persist so it follows the user across devices — the cookie is only this browser's fast-path
  // cache for SSR stamping (see dateformat.ts).
  await apiFor(event).PUT("/api/v1/prefs", { body: { prefs: { format: next } } });
  cookies.set(FORMAT_COOKIE, serializeFormatCookie(next), FORMAT_COOKIE_OPTIONS);
  // Only a same-origin relative path — never an absolute or protocol-relative URL (audit F26).
  throw redirect(303, back.startsWith("/") && !back.startsWith("//") ? back : "/");
};
