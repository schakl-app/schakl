/**
 * Personal date/time *formatting* preferences (issue #13), independent of the UI language.
 *
 * Locale and formatting are different axes: an English-speaking employee in the Netherlands wants
 * an English UI with Dutch date conventions (CLAUDE.md §8 already maps `en → en-GB` for exactly
 * this reason). This lets a user override the two formatting choices a locale would otherwise
 * dictate — 24h vs 12h clock, and the numeric date order — without touching their language.
 *
 * Mirrors the timezone/theme plumbing so `format.ts` (synchronous, runs during SSR *and* on the
 * client) can read the current choice the same way it reads the zone:
 * - **Server:** `hooks.server.ts` reads the per-browser cookie and wraps `resolve()` in
 *   `withRequestFormat` (AsyncLocalStorage) — so concurrent users never share module state.
 * - **Client:** the choice is stamped on `<html data-clock data-date-format>` and read from there.
 *
 * The cross-device source of truth is the `format` namespace on `/api/v1/prefs`; the cookie is
 * only this browser's fast-path cache (same tradeoff as theme-mode.ts — no extra `/prefs` fetch
 * per request, reconciled on the Settings → Account page). Kept free of server-only imports so
 * importing it into `format.ts` never pulls `node:*` into the browser bundle; the server half
 * lives in `dateformat-context.server.ts`.
 */

export const CLOCKS = ["24h", "12h"] as const;
export type Clock = (typeof CLOCKS)[number];

export const DATE_FORMATS = ["dd-mm-yyyy", "yyyy-mm-dd", "mm-dd-yyyy"] as const;
export type DateFormat = (typeof DATE_FORMATS)[number];

export type FormatPrefs = { clock: Clock; date: DateFormat };

// European defaults, matching the app's `en → en-GB` mapping (CLAUDE.md §8). Nothing changes for
// an existing user until they pick otherwise.
export const DEFAULT_CLOCK: Clock = "24h";
export const DEFAULT_DATE_FORMAT: DateFormat = "dd-mm-yyyy";
export const DEFAULT_FORMAT: FormatPrefs = { clock: DEFAULT_CLOCK, date: DEFAULT_DATE_FORMAT };

export const FORMAT_COOKIE = "format";

/** Same shape as THEME_COOKIE_OPTIONS — must stay readable by client JS. */
export const FORMAT_COOKIE_OPTIONS = {
  path: "/",
  maxAge: 60 * 60 * 24 * 365,
  sameSite: "lax",
  httpOnly: false,
} as const;

export function asClock(value: string | null | undefined): Clock | null {
  return value && (CLOCKS as readonly string[]).includes(value) ? (value as Clock) : null;
}

export function asDateFormat(value: string | null | undefined): DateFormat | null {
  return value && (DATE_FORMATS as readonly string[]).includes(value)
    ? (value as DateFormat)
    : null;
}

/** `"12h|yyyy-mm-dd"` — one cookie carries both choices. Unknown parts fall back to the default. */
export function serializeFormatCookie(prefs: FormatPrefs): string {
  return `${prefs.clock}|${prefs.date}`;
}

/** Read the format choice out of a raw `Cookie:` header (server) or `document.cookie` (client). */
export function parseFormatCookie(cookieHeader: string | null | undefined): FormatPrefs {
  if (!cookieHeader) return DEFAULT_FORMAT;
  const prefix = FORMAT_COOKIE + "=";
  const raw = cookieHeader
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(prefix))
    ?.slice(prefix.length);
  const [clock, date] = decodeURIComponent(raw ?? "").split("|");
  return {
    clock: asClock(clock) ?? DEFAULT_CLOCK,
    date: asDateFormat(date) ?? DEFAULT_DATE_FORMAT,
  };
}

// --- the read seam, used by format.ts (client-safe both halves) --------------

// Filled in by `dateformat-context.server.ts` (server only); stays null in the browser bundle.
let serverResolver: (() => FormatPrefs | undefined) | null = null;

/** Registered once, server-side, from `dateformat-context.server.ts`. Not for app code. */
export function registerServerFormatResolver(fn: () => FormatPrefs | undefined): void {
  serverResolver = fn;
}

function current(): FormatPrefs {
  if (typeof document !== "undefined") {
    const el = document.documentElement;
    return {
      clock: asClock(el.dataset.clock) ?? DEFAULT_CLOCK,
      date: asDateFormat(el.dataset.dateFormat) ?? DEFAULT_DATE_FORMAT,
    };
  }
  return serverResolver?.() ?? DEFAULT_FORMAT;
}

/** The clock the current user reads times in: 24-hour (default) or 12-hour. */
export function getClock(): Clock {
  return current().clock;
}

/** The numeric date order the current user reads dates in. */
export function getDateFormat(): DateFormat {
  return current().date;
}
