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
import { can } from "$lib/core/permissions";
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
  const api = apiFor(event);
  const prefs = await api.GET("/api/v1/prefs");
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

  // Personal API keys (#20). A member may hold apikeys.personal.manage; the offerable scopes are
  // the ones they actually hold (a key can never grant more than its owner), built from the
  // code-defined catalog. The catalog ships in the open-source repo — no tenant data.
  const canManageKeys = can(event.locals.user, "apikeys.personal.manage");
  const [keys, catalog] = await Promise.all([
    canManageKeys ? api.GET("/api/v1/api-keys") : Promise.resolve({ data: null }),
    canManageKeys ? api.GET("/api/v1/permissions/catalog") : Promise.resolve({ data: null }),
  ]);

  const scopeOptions: { value: string; label_key: string }[] = [];
  for (const perm of catalog.data?.permissions ?? []) {
    const variants =
      perm.scopes.length > 0 ? perm.scopes.map((s) => `${perm.key}:${s}`) : [perm.key];
    for (const value of variants) {
      const [base, suffix] = value.split(":");
      if (can(event.locals.user, base, suffix as "own" | "any" | undefined)) {
        scopeOptions.push({ value, label_key: perm.label_key });
      }
    }
  }

  return {
    account: event.locals.user,
    locales: LOCALES,
    currentLocale: event.locals.locale,
    currentTheme: persistedTheme,
    currentFormat: persistedFormat,
    clocks: CLOCKS,
    dateFormats: DATE_FORMATS,
    canManageKeys,
    apiKeys: keys.data ?? [],
    scopeOptions,
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

  createKey: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const scopes = form.getAll("scopes").map(String).filter(Boolean);
    const expires = String(form.get("expires_at") ?? "").trim();
    if (!name || scopes.length === 0 || !expires) return fail(400, { error: "errors.required" });
    // A date input gives a day; store it as end-of-day UTC so "expires 2026-08-01" lasts that day.
    const expires_at = new Date(`${expires}T23:59:59Z`).toISOString();
    const { data, error } = await apiFor(event).POST("/api/v1/api-keys", {
      body: { name, scopes, expires_at },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // The full secret is returned exactly once — hand it straight to the page to reveal.
    return { createdSecret: data?.secret, createdName: data?.name };
  },

  revokeKey: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("key_id") ?? "");
    if (id) {
      const { error } = await apiFor(event).POST("/api/v1/api-keys/{key_id}/revoke", {
        params: { path: { key_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { revoked: true };
  },
};
