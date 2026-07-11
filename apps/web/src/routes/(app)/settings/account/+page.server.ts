import { fail } from "@sveltejs/kit";

import {
  asClock,
  asDateFormat,
  CLOCKS,
  DATE_FORMATS,
  DEFAULT_CLOCK,
  DEFAULT_DATE_FORMAT,
  FORMAT_COOKIE,
  FORMAT_COOKIE_OPTIONS,
  parseFormatCookie,
  serializeFormatCookie,
} from "$lib/core/dateformat";
import { apiErrorKey } from "$lib/core/errors";
import { LOCALES } from "$lib/core/i18n";
import { apiFor } from "$lib/core/session";
import {
  asThemeMode,
  parseThemeCookie,
  THEME_COOKIE,
  THEME_COOKIE_OPTIONS,
} from "$lib/core/theme-mode";

import type { Actions, PageServerLoad } from "./$types";

// Personal account — reachable by every member (NOT manager-gated, unlike org settings).
export const load: PageServerLoad = async (event) => {
  const prefs = await apiFor(event).GET("/api/v1/prefs");
  const appearance = prefs.data?.prefs?.appearance as { theme?: string } | undefined;
  const persistedTheme = asThemeMode(appearance?.theme) ?? "system";

  // The cookie is only this browser's cache of the persisted preference (see theme-mode.ts) —
  // reconcile it here, the one place a stale/missing cookie is cheap to notice and fix, rather
  // than fetching prefs on every request just to keep it fresh (docs/PERFORMANCE.md).
  const cookieTheme = parseThemeCookie(event.request.headers.get("cookie"));
  if (persistedTheme !== (cookieTheme ?? "system")) {
    event.cookies.set(THEME_COOKIE, persistedTheme, THEME_COOKIE_OPTIONS);
  }

  // Same reconcile for the personal date/time formatting choice (issue #13): prefs are the
  // cross-device truth, the cookie is this browser's SSR cache (see dateformat.ts).
  const format = prefs.data?.prefs?.format as { clock?: string; date?: string } | undefined;
  const persistedFormat = {
    clock: asClock(format?.clock) ?? DEFAULT_CLOCK,
    date: asDateFormat(format?.date) ?? DEFAULT_DATE_FORMAT,
  };
  const cookieFormat = parseFormatCookie(event.request.headers.get("cookie"));
  if (
    persistedFormat.clock !== cookieFormat.clock ||
    persistedFormat.date !== cookieFormat.date
  ) {
    event.cookies.set(
      FORMAT_COOKIE,
      serializeFormatCookie(persistedFormat),
      FORMAT_COOKIE_OPTIONS,
    );
  }

  return {
    account: event.locals.user,
    locales: LOCALES,
    currentLocale: event.locals.locale,
    currentTheme: persistedTheme,
    currentFormat: persistedFormat,
    clocks: CLOCKS,
    dateFormats: DATE_FORMATS,
  };
};

export const actions: Actions = {
  updateProfile: async (event) => {
    const form = await event.request.formData();
    const full_name = String(form.get("full_name") ?? "").trim();
    const { error } = await apiFor(event).PATCH("/api/v1/meta/me", {
      body: { full_name: full_name || null },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },
};
