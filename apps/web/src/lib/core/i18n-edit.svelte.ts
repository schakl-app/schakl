/**
 * The language a tenant's **own** labels are being edited in (owner feedback, 2026-08-05).
 *
 * Tenant-entered translations — leave types, roles, custom fields, nav labels, tile names, mail
 * templates — are edited one language at a time (docs/UX.md). That choice used to live on each
 * field, so a screen carrying eight translatable labels drew eight NL/EN switchers and you flipped
 * them one at a time to write the English column. It is **one choice about the whole surface**, so
 * it is one piece of state: `core/ui/I18nLocaleSwitcher` renders it once at the top of the page or
 * dialog and every editor below follows.
 *
 * Deliberately a module singleton rather than a Svelte context. A dialog opened from a page is its
 * own component tree, so a context provider would have to be threaded into every modal that holds a
 * label field — and carrying the choice *across* screens is what someone filling in the English
 * column of six settings pages actually wants. It is safe on the server because nothing there ever
 * writes it: the restore below is client-only and `set` runs from a click.
 */
import { getLocale } from "$lib/paraglide/runtime";

import { asLocale, LOCALES } from "$lib/core/i18n";

const STORAGE_KEY = "schakl.i18n-edit-locale";

let chosen = $state<string | null>(null);

if (typeof localStorage !== "undefined") {
  chosen = asLocale(localStorage.getItem(STORAGE_KEY));
}

/**
 * Every locale the app ships, the reader's own UI language first — so the tab that opens is the
 * one they think in, and adding a locale still costs nothing but a JSON file (CLAUDE.md §8).
 */
export function editLocales(): string[] {
  const ui = getLocale();
  const rest = (LOCALES as readonly string[]).filter((locale) => locale !== ui);
  return rest.length === LOCALES.length ? [...LOCALES] : [ui, ...rest];
}

/**
 * The shared choice, narrowed to what a given surface can actually offer. A surface whose locales
 * come from data (the mail templates, one row per `(kind, locale)`) may not carry all of them, and
 * an editor asked for a language it has no input for would render nothing at all.
 */
export function resolveEditLocale(available: readonly string[]): string {
  if (available.length === 0) return getLocale();
  return chosen && available.includes(chosen) ? chosen : available[0];
}

export const editLocale = {
  /** The language every translatable field on screen is currently showing. */
  get current(): string {
    return resolveEditLocale(editLocales());
  },
  set(locale: string): void {
    chosen = locale;
    if (typeof localStorage !== "undefined") localStorage.setItem(STORAGE_KEY, locale);
  },
};
