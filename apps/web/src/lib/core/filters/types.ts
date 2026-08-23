/**
 * One description of what a list can be narrowed by, rendered by `FilterBar.svelte`.
 *
 * Every list here had grown its own copy of the same twelve lines — a `setFilter(key, value)`
 * that rebuilt the URL, a `resetPage`, a `goto`, a pill loop, a "wissen" link and, on exactly
 * one screen, the mobile collapse. Copies drift: the domains list read `?q=` in its load and
 * never rendered a search box for it, and the subscriptions list's clear button hand-listed
 * three keys, so a fourth filter would have survived being cleared. This is that shape stated
 * once, as data, the way `BulkConfig` and `ImpexColumns` already state theirs.
 *
 * Two rules the type enforces rather than documents:
 *
 * **The URL is the view** (`core/table/paging.ts`). A def carries no value: the bar reads the
 * current one off `page.url` — the same place the `+page.server.ts` load reads it — so the two
 * cannot disagree about what is on screen, and the back button, a shared link and a reload all
 * land on the same list. `value` exists only for the one case the URL genuinely cannot express:
 * a filter with a non-empty default (tasks opens on *your* tasks, absent ≠ "everyone").
 *
 * **A filter drops the page.** Applying one goes through a single `goto` that calls `resetPage`,
 * so page 7 of the old filter can never be served as page 7 of the new one.
 */
import type { Snippet } from "svelte";

import { resetPage } from "$lib/core/table/paging";

/** One choice in a `select` or `pills` filter. `value` is what lands in the URL. */
export interface FilterOption {
  value: string;
  label: string;
  /**
   * The chip's own colours, for a vocabulary where the colour *is* information — a client's
   * lifecycle, a project's status, a tenant's label. Only meaningful on `pills`, and only the
   * unselected look: the selected treatment is the bar's one ring, on every list, so "which of
   * these is on" never depends on which screen you are reading (#354).
   *
   * Leave it off and the chip is the neutral one. Most filters want that — a colour that means
   * nothing is a colour a reader has to learn not to read.
   */
  class?: string;
}

interface FilterBase<K extends string> {
  /**
   * The query parameter this filter owns. Short and readable — it is in the address bar and in
   * links people paste (`?company=…`, not `?company_id=…`); the load maps it to whatever the
   * API calls the same thing.
   *
   * A module declares its keys as a `const` tuple and passes that union in, so the load (which
   * reads them off the URL) and the bar (which renders them) cannot drift apart on a typo —
   * a drift whose symptom is a control that silently never filters.
   */
  key: K;
  /**
   * Override the value read from the URL. Only for a filter whose *absent* state is not its
   * empty state — otherwise leave it off, or a stale prop and the address bar will disagree.
   */
  value?: string;
  /** Hide this control without disturbing the others (a permission the caller resolved). */
  hidden?: boolean;
}

/** The debounced `?q=` box. At most one per bar, and it renders first. */
export interface SearchFilter<K extends string = string> extends FilterBase<K> {
  kind: "search";
  placeholder?: string;
}

/**
 * A type-ahead picker over a closed vocabulary (a client, a provider, a hosting account).
 *
 * Options come from what the page already loaded — a filter that fetched its own would be the
 * bug `docs/PERFORMANCE.md` names. Labels must be self-describing, because a Combobox shows the
 * *selected* label and nothing else: "Wel facturabel" survives being read on its own,
 * "Ja" under a placeholder nobody can see any more does not.
 */
export interface SelectFilter<K extends string = string> extends FilterBase<K> {
  kind: "select";
  /** Shown while nothing is picked; this is the control's name to the user. */
  placeholder: string;
  options: FilterOption[];
  /**
   * Options that exist but are not on offer — an archived client, a finished project — shown
   * only once the user types, under `archivedLabel` (`Combobox`'s own `archived` bucket).
   *
   * A filter *may* legitimately point at a retired row, so these are never dropped; they are
   * merely not suggested, and they arrive already split by the owning module
   * (`$lib/modules/companies/picker`), because which status retires a row is its vocabulary.
   */
  archived?: FilterOption[];
  /** Heading above the archived rows. Required in practice whenever `archived` is non-empty. */
  archivedLabel?: string;
}

/**
 * A short row of toggle chips — one click to apply, the same click to clear.
 *
 * For a small, stable vocabulary the user recognises on sight (a status). Anything longer than
 * about six belongs in a `select`: a pill row that wraps to three lines is a wall, not a control.
 */
export interface PillsFilter<K extends string = string> extends FilterBase<K> {
  kind: "pills";
  options: FilterOption[];
}

/**
 * A control this bar cannot express, rendered by the page and *placed* by the bar.
 *
 * The escape hatch exists so a screen with one unusual filter can still be the shared bar rather
 * than a hand-rolled row that re-types the ordering, the clear link and the mobile collapse — the
 * copies #354 found. It is deliberately thin: the snippet renders a control, and everything
 * *around* it (where it sits, whether the bar is open on a phone, whether it counts towards
 * "wissen") stays the bar's, which is the whole point.
 *
 * `active` is what the bar cannot work out for itself when the control does not own a plain URL
 * key — a member picker whose "me" state is a user id, a date pair. Say it, or the count and the
 * clear link quietly disagree with the screen.
 */
export interface CustomFilter<K extends string = string> extends FilterBase<K> {
  kind: "custom";
  render: Snippet;
  /** Is this filter currently narrowing the list? Defaults to "the URL has this key". */
  active?: boolean;
  /** Extra keys this control owns, cleared by "wissen" along with `key`. */
  extraKeys?: readonly string[];
}

export type FilterDef<K extends string = string> =
  SearchFilter<K> | SelectFilter<K> | PillsFilter<K> | CustomFilter<K>;

/** The filter keys a bar owns — what "wissen" clears, and nothing else (never the sort or size). */
export function filterKeys(defs: FilterDef[]): string[] {
  return defs
    .filter((def) => !def.hidden)
    .flatMap((def) => [def.key, ...(def.kind === "custom" ? (def.extraKeys ?? []) : [])]);
}

/**
 * What each of this bar's filters is currently set to, read off the URL.
 *
 * The `+page.server.ts` load calls this so the load and the bar read the same source, then maps
 * the keys onto the API's own parameter names. Empty values are dropped, so `Object.keys` is
 * "what is actually filtering" — the count on the mobile badge, and the test for whether to
 * offer "wissen" at all.
 */
export function readFilters(url: URL, keys: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of keys) {
    const value = url.searchParams.get(key)?.trim();
    if (value) out[key] = value;
  }
  return out;
}

/**
 * A URL with one filter applied, the page dropped, and everything else (sort, size, tab) kept.
 *
 * Exported for the handful of controls that live outside the bar and still narrow the list — a
 * chip on a summary tile, a row that filters to its own group. They must go through this rather
 * than build a URL by hand, or they are the control that forgets `resetPage`.
 */
export function filterUrl(url: URL, key: string, value: string): URL {
  const next = new URL(url);
  if (value) next.searchParams.set(key, value);
  else next.searchParams.delete(key);
  return resetPage(next);
}
