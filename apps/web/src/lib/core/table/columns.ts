/**
 * Column descriptors and the personal layout that selects them (#24).
 *
 * A list declares every column it *can* show; the user's saved preference decides which of them
 * it *does* show, in what order, at what width, sorted how. Everything here is pure — no Svelte,
 * no fetch — so the rules that decide what a user sees can be reasoned about and tested.
 */
import type { Snippet } from "svelte";

import { customFieldAlign, formatCustomValue } from "$lib/core/customfields/format";
import type { CustomFieldDefinition } from "$lib/core/customfields/types";
import { fieldLabel } from "$lib/core/customfields/types";

/** Prefix marking a column that came from a tenant's custom-field definition, not from code. */
export const CUSTOM_PREFIX = "custom:";

/**
 * What a column *is*, with no Svelte in it.
 *
 * Kept separate from its renderer on purpose: a page's `load` has to know which columns are
 * visible — that is how it decides whether to ask the API for an expensive aggregate — and a
 * server load cannot import a snippet. So metadata lives in a plain module both sides read.
 */
export interface ColumnMeta {
  /** Stable id — persisted in the user's prefs, so never rename one casually. */
  key: string;
  /** i18n key for the header. Custom-field columns carry a literal `label` instead. */
  labelKey?: string;
  /** Already-translated header, for labels that are tenant data rather than app strings. */
  label?: string;
  /**
   * The API's `?sort=` key. Absent ⇒ the header is not clickable. Deliberately not a boolean:
   * sorting happens on the server, so a column is sortable only if the API can order by it.
   * Aggregates and custom fields cannot, and a `sortable: true` that silently does nothing is
   * worse than an honest header.
   */
  sortKey?: string;
  defaultVisible?: boolean;
  width?: number;
  align?: "left" | "right";
  /** The row's identity column. Always shown, never reorderable away, carries the row link. */
  primary?: boolean;
}

/** A column ready to render: metadata, a resolved label, and optionally its own cell renderer. */
export interface ColumnSpec<T = Record<string, unknown>> extends ColumnMeta {
  label: string;
  /** Renders the cell. Omitted for custom-field columns, which the table formats generically. */
  cell?: Snippet<[T]>;
}

/** What we persist per list under `prefs.tables.<listId>`. Every field optional — old blobs. */
export interface TablePref {
  columns?: string[];
  sort?: string | null;
  widths?: Record<string, number>;
}

export interface ResolvedTable<C> {
  columns: C[];
  sort: string | null;
  widths: Record<string, number>;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Read one list's preference out of the free-form `prefs` blob.
 *
 * The blob is untyped and user-owned, may predate any given column, and is written by other
 * releases. Treat every field as hostile: a string where an array belongs must degrade to the
 * defaults, not throw on a page load.
 */
export function readTablePref(prefs: unknown, listId: string): TablePref {
  if (!isPlainObject(prefs)) return {};
  const tables = prefs.tables;
  if (!isPlainObject(tables)) return {};
  const pref = tables[listId];
  if (!isPlainObject(pref)) return {};

  const columns = Array.isArray(pref.columns)
    ? pref.columns.filter((c): c is string => typeof c === "string")
    : undefined;
  const sort = typeof pref.sort === "string" ? pref.sort : null;
  const widths: Record<string, number> = {};
  if (isPlainObject(pref.widths)) {
    for (const [key, value] of Object.entries(pref.widths)) {
      if (typeof value === "number" && Number.isFinite(value) && value > 0) widths[key] = value;
    }
  }
  return { columns, sort, widths };
}

/**
 * Apply a saved preference to the columns a list declares.
 *
 * Unknown keys are dropped rather than rendered: a tenant deleting a custom field must not break
 * every saved layout that mentioned it (#24). The primary column is forced back in at the front
 * for the same reason — a layout saved before it existed, or with it somehow removed, would
 * otherwise render rows with no link out.
 */
export function resolveColumns<C extends ColumnMeta>(
  declared: C[],
  pref: TablePref,
): ResolvedTable<C> {
  const byKey = new Map(declared.map((c) => [c.key, c]));
  const primary = declared.find((c) => c.primary);

  let chosen: C[];
  if (pref.columns && pref.columns.length > 0) {
    chosen = pref.columns.map((key) => byKey.get(key)).filter((c): c is C => !!c);
  } else {
    chosen = declared.filter((c) => c.defaultVisible ?? c.primary ?? false);
  }
  if (primary && !chosen.some((c) => c.key === primary.key)) chosen = [primary, ...chosen];

  // A sort saved against a column that no longer exists (or was never sortable) is not honoured:
  // the API would reject it, and the user would see an error instead of a list.
  const sortKey = pref.sort?.replace(/^-/, "");
  const sortable = declared.some((c) => c.sortKey && c.sortKey === sortKey);

  return {
    columns: chosen,
    sort: sortable ? (pref.sort ?? null) : null,
    widths: pref.widths ?? {},
  };
}

/** Cycle a header: unsorted → ascending → descending → unsorted. */
export function nextSort(current: string | null, sortKey: string): string | null {
  if (current === sortKey) return `-${sortKey}`;
  if (current === `-${sortKey}`) return null;
  return sortKey;
}

export function sortDirection(current: string | null, sortKey: string): "asc" | "desc" | null {
  if (current === sortKey) return "asc";
  if (current === `-${sortKey}`) return "desc";
  return null;
}

/**
 * A tenant's custom fields, as columns. This is the point of a generic table: defining a custom
 * field makes it selectable with no per-module code. Hidden by default — a tenant with twenty
 * custom fields should not get a twenty-column list — and never sortable (the API cannot order
 * by a JSONB key today, so the header stays quiet rather than lying).
 */
export function customFieldColumns(
  definitions: CustomFieldDefinition[],
  locale: string,
): ColumnMeta[] {
  return definitions
    .filter((def) => def.active)
    .toSorted((a, b) => a.position - b.position)
    .map((def) => ({
      key: `${CUSTOM_PREFIX}${def.key}`,
      label: fieldLabel(def, locale),
      defaultVisible: false,
      align: customFieldAlign(def),
    }));
}

/** Render a custom-field cell for a row, given the definitions the page loaded. */
export function customCellText(
  key: string,
  row: { custom?: Record<string, unknown> | null },
  definitions: CustomFieldDefinition[],
  locale: string,
): string {
  const fieldKey = key.slice(CUSTOM_PREFIX.length);
  const def = definitions.find((d) => d.key === fieldKey);
  if (!def) return "—";
  return formatCustomValue(def, row.custom?.[fieldKey], locale);
}
