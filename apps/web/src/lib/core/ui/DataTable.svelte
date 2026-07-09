<script lang="ts" generics="T extends { id: string; custom?: Record<string, unknown> | null }">
  /**
   * One table for every list (#24). The list declares its columns; this owns visibility, order,
   * width, sort and persistence, so adding a column somewhere is a descriptor, not a new table.
   *
   * **Sorting and paging are the server's job** (docs/PERFORMANCE.md): the page shows 200 of
   * possibly thousands of rows, and sorting the slice you happen to hold sorts the wrong set. A
   * header is clickable only when the API can order by it — hence `sortKey`, not `sortable`.
   *
   * **A grid is not a mobile UI** (docs/UX.md): below `sm` this renders the concept's own row
   * snippet instead of asking a phone to scroll twelve columns sideways.
   *
   * A `<tr>` cannot be wrapped in an `<a>`, so `rowHref` links the primary cell and the row
   * merely highlights — the same compromise the time-overview table already makes.
   */
  import { ArrowDown, ArrowUp } from "@lucide/svelte";
  import type { Snippet } from "svelte";

  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import type { ColumnSpec } from "$lib/core/table/columns";
  import { CUSTOM_PREFIX, customCellText, nextSort, sortDirection } from "$lib/core/table/columns";

  let {
    rows,
    columns,
    sort = null,
    widths = {},
    definitions = [],
    locale = "nl",
    rowHref,
    actions,
    mobileRow,
    empty,
    onsort,
    onresize,
  }: {
    rows: T[];
    /** The resolved, visible columns in display order. */
    columns: ColumnSpec<T>[];
    sort?: string | null;
    widths?: Record<string, number>;
    definitions?: CustomFieldDefinition[];
    locale?: string;
    rowHref?: (row: T) => string;
    /** Trailing ⋯ cell (docs/UX.md: record actions live behind the overflow menu). */
    actions?: Snippet<[T]>;
    /** Rendered instead of the grid below `sm`. */
    mobileRow?: Snippet<[T]>;
    empty?: Snippet;
    onsort?: (sort: string | null) => void;
    onresize?: (widths: Record<string, number>) => void;
  } = $props();

  // --- column resize ---------------------------------------------------------
  // Pointer events, not mouse: a drag that leaves the header (or the window) must still end, and
  // setPointerCapture is what guarantees the release lands back here.
  let resizing = $state<{ key: string; startX: number; startWidth: number } | null>(null);

  function startResize(event: PointerEvent, key: string, current: number) {
    event.preventDefault();
    event.stopPropagation(); // never let a resize drag read as a sort click
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
    resizing = { key, startX: event.clientX, startWidth: current };
  }

  function onPointerMove(event: PointerEvent) {
    if (!resizing) return;
    const width = Math.max(64, resizing.startWidth + (event.clientX - resizing.startX));
    widths = { ...widths, [resizing.key]: width };
  }

  function endResize() {
    if (!resizing) return;
    resizing = null;
    onresize?.(widths);
  }

  function headerWidth(column: ColumnSpec<T>): number | undefined {
    return widths[column.key] ?? column.width;
  }
</script>

<svelte:window onpointermove={onPointerMove} onpointerup={endResize} />

{#if rows.length === 0}
  {@render empty?.()}
{:else}
  <!-- Phone: the concept's shared row, never a sideways-scrolling grid. -->
  {#if mobileRow}
    <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised sm:hidden">
      {#each rows as row (row.id)}
        <li class="px-4 py-3 first:rounded-t-xl last:rounded-b-xl hover:bg-surface">
          {@render mobileRow(row)}
        </li>
      {/each}
    </ul>
  {/if}

  <div
    class="overflow-x-auto rounded-xl border border-border bg-surface-raised
      {mobileRow ? 'hidden sm:block' : ''}"
  >
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border text-left text-xs text-text-muted">
          {#each columns as column (column.key)}
            {@const direction = column.sortKey ? sortDirection(sort, column.sortKey) : null}
            <th
              scope="col"
              class="relative px-4 py-2 font-medium {column.align === 'right'
                ? 'text-right'
                : 'text-left'}"
              style={headerWidth(column) ? `width:${headerWidth(column)}px` : undefined}
              aria-sort={direction === "asc"
                ? "ascending"
                : direction === "desc"
                  ? "descending"
                  : undefined}
            >
              {#if column.sortKey}
                <button
                  type="button"
                  class="inline-flex cursor-pointer items-center gap-1 hover:text-text"
                  onclick={() => onsort?.(nextSort(sort, column.sortKey!))}
                >
                  <span class="truncate">{column.label}</span>
                  {#if direction === "asc"}<ArrowUp
                      size={12}
                    />{:else if direction === "desc"}<ArrowDown size={12} />{/if}
                </button>
              {:else}
                <span class="truncate">{column.label}</span>
              {/if}

              <!-- Resize handle. Not focusable: it moves a cosmetic width, and a keyboard user
                   already has every column via the picker. -->
              <span
                class="absolute inset-y-0 right-0 w-1.5 cursor-col-resize hover:bg-brand/40"
                role="presentation"
                onpointerdown={(e) => startResize(e, column.key, headerWidth(column) ?? 160)}
              ></span>
            </th>
          {/each}
          {#if actions}
            <th scope="col" class="w-10 px-2 py-2"
              ><span class="sr-only">{t("common.actions")}</span></th
            >
          {/if}
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        {#each rows as row (row.id)}
          <tr class="hover:bg-surface">
            {#each columns as column, index (column.key)}
              <td
                class="px-4 py-2.5 {column.align === 'right'
                  ? 'text-right tabular-nums'
                  : 'text-left'}"
              >
                {#if column.cell}
                  {@render column.cell(row)}
                {:else if column.key.startsWith(CUSTOM_PREFIX)}
                  <span class="text-text-muted"
                    >{customCellText(column.key, row, definitions, locale)}</span
                  >
                {:else if index === 0 && rowHref}
                  <a href={rowHref(row)} class="font-medium text-text hover:text-brand"
                    >{String((row as Record<string, unknown>)[column.key] ?? "—")}</a
                  >
                {:else}
                  {String((row as Record<string, unknown>)[column.key] ?? "—")}
                {/if}
              </td>
            {/each}
            {#if actions}
              <td class="px-2 py-2.5 text-right">{@render actions(row)}</td>
            {/if}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
