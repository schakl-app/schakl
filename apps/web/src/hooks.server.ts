/**
 * Server hooks (CLAUDE.md §7, §8).
 *
 * 1. Context hook resolves the tenant theme + current user via the API and puts them on
 *    `event.locals` for layouts, guards, and the manifest/theme routes; stamps the brand
 *    custom properties onto <html> (Golden Rule 4); and decides the request's locale.
 * 2. Paraglide middleware binds that locale (AsyncLocalStorage) so `m.*()` render in the right
 *    language during SSR, and stamps <html lang> + <html data-locale>.
 *
 * Context runs *first*: the locale decision needs the signed-in user, and the user is fetched
 * here anyway, so the answer is handed down rather than looked up twice (locale-context.server).
 */
import { sequence } from "@sveltejs/kit/hooks";
import type { Handle } from "@sveltejs/kit";

import "$lib/core/paraglide-strategy.server"; // register custom locale strategy (server)
import "$lib/modules"; // self-register web modules

import { asLocale, LOCALE_COOKIE, LOCALE_COOKIE_OPTIONS, parseLocaleCookie } from "$lib/core/i18n";
import { withRequestLocale } from "$lib/core/locale-context.server";
import { fetchTenant, fetchUser } from "$lib/core/session";
import { themeStyle } from "$lib/core/theme";
import { parseThemeCookie } from "$lib/core/theme-mode";
import { paraglideMiddleware } from "$lib/paraglide/server";

// The <html lang> steers how Chromium formats native date pickers, so map each UI locale to its
// *European* BCP-47 tag ("en" alone would give US month-day-year). It is a formatting tag, NOT a
// locale code — never read it back as one. That is what `data-locale` is for.
const HTML_LANG: Record<string, string> = { nl: "nl", en: "en-GB" };

const handleContext: Handle = async ({ event, resolve }) => {
  const [theme, user] = await Promise.all([fetchTenant(event), fetchUser(event)]);
  event.locals.theme = theme;
  event.locals.user = user;

  // `users.locale` is the source of truth; the cookie is a per-browser cache of it (and the
  // only signal Paraglide can read while hydrating). Order: personal preference → this
  // browser's cookie → the tenant default.
  const cookieLocale = parseLocaleCookie(event.request.headers.get("cookie"));
  const userLocale = asLocale(user?.locale);
  const locale = userLocale ?? cookieLocale ?? asLocale(theme.defaultLocale) ?? undefined;

  // Re-sync a stale cookie (language changed on another device, or cleared) so the client
  // hydrates in the language the server just rendered. Written only from a *personal*
  // preference: caching the tenant default here would pin every browser to today's org setting.
  if (userLocale && userLocale !== cookieLocale) {
    event.cookies.set(LOCALE_COOKIE, userLocale, LOCALE_COOKIE_OPTIONS);
  }

  // Explicit "light"/"dark" are stamped as-is; "system" (no cookie yet, or the cookie says
  // "system") is left for the inline no-flash script in app.html to resolve client-side against
  // the OS preference — there is no reliable way to know that server-side, and (unlike locale)
  // this isn't worth an extra `/api/v1/prefs` fetch on every request just to try (see
  // docs/PERFORMANCE.md). The Settings → Account page reconciles the cookie when visited.
  const themeCookie = parseThemeCookie(event.request.headers.get("cookie"));
  const colorScheme = themeCookie ?? "system";

  // Stamped server-side so the brand colours are on <html> at first paint (no flash of the
  // fallback), and so inherited properties like `accent-color` compute against them. For
  // "system" this assumes light (unknowable server-side); the client re-stamps once the real
  // scheme resolves, same as the live re-stamp on a Huisstijl colour save.
  const style = themeStyle(theme, colorScheme === "dark" ? "dark" : "light");
  return withRequestLocale(locale, () =>
    resolve(event, {
      transformPageChunk: ({ html }) =>
        html.replace("%theme%", () => style).replace("%colorScheme%", () => colorScheme),
    }),
  );
};

const handleParaglide: Handle = ({ event, resolve }) =>
  paraglideMiddleware(event.request, ({ request, locale }) => {
    event.request = request;
    event.locals.locale = locale;
    return resolve(event, {
      transformPageChunk: ({ html }) =>
        html.replace("%lang%", HTML_LANG[locale] ?? locale).replace("%locale%", locale),
    });
  });

export const handle = sequence(handleContext, handleParaglide);
