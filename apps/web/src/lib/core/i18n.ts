/**
 * Web i18n helper (CLAUDE.md §8).
 *
 * Our message keys are flat + **dotted** (`companies.title`), so Paraglide exports them under
 * their exact dotted names (`m["companies.title"]`), which aren't valid `m.x` accessors. `t()`
 * is the single bridge used across the app: it looks the dotted key up directly. It also
 * translates **dynamic** keys the API hands us (error-envelope messages, panel `title_key`s).
 */
import * as messages from "$lib/paraglide/messages";
import { cookieName, locales } from "$lib/paraglide/runtime";

export const LOCALES = locales;
export const LOCALE_COOKIE = cookieName;

type MessageFn = (params?: Record<string, unknown>) => string;

export function t(key: string, params?: Record<string, unknown>): string {
  const fn = (messages as unknown as Record<string, MessageFn>)[key];
  return fn ? fn(params) : key;
}

export function localeLabel(locale: string): string {
  return t(`locale.${locale}`);
}
