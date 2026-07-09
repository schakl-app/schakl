/**
 * The wiring every `DataTable` page repeats: resolve the saved layout, label the columns, attach
 * their cell renderers, and persist a change back to `/api/v1/prefs` (#24).
 *
 * It exists because the fifth list was about to be the fifth copy of it. A list should declare
 * its columns and its cells and be done — the sort round-trip, the "did this change what the
 * server must compute" reload, and the shape of the saved blob are not each page's business.
 *
 * Saving posts straight to the page's own `?/saveTable` action rather than through a hidden
 * `<form>`: there is nothing to submit and nothing to reset, and a cosmetic preference should not
 * make the page flicker.
 */
import { goto, invalidateAll } from "$app/navigation";
import { page } from "$app/state";
import type { Snippet } from "svelte";

import { t } from "$lib/core/i18n";

import type { ColumnMeta, ColumnSpec, TablePref } from "./columns";
import { resolveColumns } from "./columns";

export interface TableLayoutInput<T> {
  /** Every column the list can show, custom fields included, in declaration order. */
  all: () => ColumnMeta[];
  /** The saved preference, straight from the page load. */
  pref: () => TablePref;
  /** The sort actually in force (URL wins over the saved default; the load decides). */
  sort: () => string | null;
  /** Cell renderers by column key. A key with no snippet falls back to the generic cell. */
  cells: () => Record<string, Snippet<[T]> | undefined>;
  /** Footer figures by column key — from the API, never summed from the page (#37). */
  totals?: () => Record<string, Snippet<[]> | undefined>;
  /**
   * Columns whose *visibility* changes what the API must compute (an opt-in aggregate). Toggling
   * one reloads the page; toggling any other column just redraws, because nothing new is needed.
   */
  reloadOn?: string[];
}

export function createTableLayout<T>(input: TableLayoutInput<T>) {
  /**
   * A **writable** `$derived`: it tracks the saved preference until the user changes something,
   * and the change wins until the next load hands us a fresh one. Without it, hiding a column
   * would do nothing visible until the page had round-tripped to the server and back.
   *
   * Seeded with the *effective* sort, not the saved one — the URL wins, and the load already
   * decided that.
   */
  let pref = $derived<TablePref>({ ...input.pref(), sort: input.sort() });

  const resolved = $derived(resolveColumns(input.all(), pref));

  const columns = $derived(
    resolved.columns.map((meta) => ({
      ...meta,
      label: meta.label ?? t(meta.labelKey ?? meta.key),
      cell: input.cells()[meta.key],
      total: input.totals?.()[meta.key],
    })) as ColumnSpec<T>[],
  );

  const visibleKeys = $derived(columns.map((column) => column.key));
  const collapsed = $derived(pref.collapsed ?? []);

  /** What the column picker needs: labels resolved, sortability declared. */
  const pickerColumns = $derived(
    input.all().map((column) => ({
      key: column.key,
      label: column.label ?? t(column.labelKey ?? column.key),
      primary: column.primary,
      sortKey: column.sortKey,
    })),
  );

  async function save(reload = false): Promise<void> {
    const body = new FormData();
    body.set("columns", visibleKeys.join(","));
    body.set("sort", pref.sort ?? "");
    body.set("widths", JSON.stringify(resolved.widths));
    body.set("collapsed", collapsed.join(","));

    // `/api/v1/prefs` replaces a list's entry wholesale, so every field goes every time — a
    // partial write would erase the ones it left out.
    await fetch("?/saveTable", {
      method: "POST",
      headers: { "x-sveltekit-action": "true" },
      body,
    });
    if (reload) await invalidateAll();
  }

  return {
    get columns() {
      return columns;
    },
    get visibleKeys() {
      return visibleKeys;
    },
    get widths() {
      return resolved.widths;
    },
    get collapsed() {
      return collapsed;
    },
    get pickerColumns() {
      return pickerColumns;
    },
    get sort() {
      return pref.sort ?? null;
    },

    onColumnsChange(keys: string[]): void {
      // Reload only when the change alters what the server has to compute. A handful of keys —
      // an array scan is cheaper than the Sets it would take to make this look clever.
      const reload = (input.reloadOn ?? []).some(
        (key) => visibleKeys.includes(key) !== keys.includes(key),
      );
      pref = { ...pref, columns: keys };
      void save(reload);
    },

    onSort(next: string | null): void {
      const url = new URL(page.url);
      if (next) url.searchParams.set("sort", next);
      else url.searchParams.delete("sort");
      // The URL drives the fetch — server-side sort, shareable, and the back button works. The
      // saved preference only remembers it for next time.
      void goto(url, { keepFocus: true, noScroll: true });
      pref = { ...pref, sort: next };
      void save();
    },

    onResize(widths: Record<string, number>): void {
      pref = { ...pref, widths };
      void save();
    },

    onCollapse(keys: string[]): void {
      pref = { ...pref, collapsed: keys };
      void save();
    },
  };
}
