<script lang="ts" generics="T extends { id: string; custom?: Record<string, unknown> | null }">
  /**
   * One table for every list (docs/UX.md, #24). The list declares its columns; this owns
   * visibility, order, width, sort, selection and totals, so a list that needs more grows the
   * table rather than forking a bespoke grid.
   *
   * **Sorting and paging are the server's job** (docs/PERFORMANCE.md): the page shows 200 of
   * possibly thousands of rows, and sorting the slice you happen to hold sorts the wrong set. A
   * header is clickable only when the API can order by it — hence `sortKey`, not `sortable`.
   *
   * **Totals come from the API, never from `rows`.** Summing the page would silently produce the
   * total *of the page*, which looks exactly like the right answer (#37).
   *
   * **A grid is not a mobile UI** (docs/UX.md): below `sm` this renders the concept's own row
   * snippet instead of asking a phone to scroll twelve columns sideways.
   *
   * A `<tr>` cannot be wrapped in an `<a>`, so `rowHref` links the primary cell and the row
   * merely highlights — the same compromise the time-overview table already made.
   */
  import { ArrowDown, ArrowUp, ChevronDown, ChevronRight } from "@lucide/svelte";
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
    selectable = false,
    selected = $bindable([]),
    selection,
    groups,
    groupBy,
    collapsed = [],
    oncollapse,
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
    /** Adds a leading checkbox column and select-all. Costs nothing when false. */
    selectable?: boolean;
    /** Ids of the selected rows. Bindable, so the caller can post them. */
    selected?: string[];
    /** The bulk bar, shown above the table while anything is selected. */
    selection?: Snippet<[string[]]>;
    /**
     * Row groups, **in display order** (#38). `open, in_progress, done` is a workflow, not an
     * alphabet, so the caller's order wins and no sort may disturb it.
     */
    groups?: { key: string; label: string; collapsible?: boolean }[];
    groupBy?: (row: T) => string;
    /** Keys of the collapsed groups. A personal view option, persisted with the columns. */
    collapsed?: string[];
    oncollapse?: (keys: string[]) => void;
    onsort?: (sort: string | null) => void;
    onresize?: (widths: Record<string, number>) => void;
  } = $props();

  // --- selection -------------------------------------------------------------
  // Selection is **per page**: "select all" can only mean the rows that were fetched. Anything
  // else would let a bulk approve reach records the user never saw.
  const selectedSet = $derived(new Set(selected));
  const allSelected = $derived(rows.length > 0 && selected.length === rows.length);
  const someSelected = $derived(selected.length > 0 && !allSelected);

  // A new page, filter or sort is a different set of rows; a selection made against the old one
  // is meaningless and must not survive into a bulk action.
  $effect(() => {
    void rows;
    selected = [];
  });

  function toggleAll() {
    selected = allSelected ? [] : rows.map((row) => row.id);
  }

  function toggleRow(id: string) {
    selected = selectedSet.has(id) ? selected.filter((s) => s !== id) : [...selected, id];
  }

  // --- grouping --------------------------------------------------------------
  // Rows arrive from the API already in sort order. Bucketing preserves that order *inside* each
  // group and never touches the order *of* the groups — a sort that reshuffled the sections would
  // quietly turn a board into a list (#38).
  const grouped = $derived.by(() => {
    if (!groups || !groupBy) return null;
    const buckets = new Map<string, T[]>(groups.map((group) => [group.key, []]));
    // A row whose group was never declared must not silently disappear. It gets a trailing
    // section of its own rather than being dropped on the floor — silent truncation reads as
    // "that's all of them" (docs/PERFORMANCE.md), and here it would read as "that task is gone".
    const strays: T[] = [];
    for (const row of rows) {
      const bucket = buckets.get(groupBy(row));
      if (bucket) bucket.push(row);
      else strays.push(row);
    }
    const declared = groups.map((group) => ({
      ...group,
      rows: buckets.get(group.key) ?? [],
    }));
    return strays.length > 0
      ? [
          ...declared,
          { key: "__ungrouped", label: t("table.ungrouped"), collapsible: false, rows: strays },
        ]
      : declared;
  });

  const collapsedSet = $derived(new Set(collapsed));

  function toggleGroup(key: string) {
    oncollapse?.(collapsedSet.has(key) ? collapsed.filter((k) => k !== key) : [...collapsed, key]);
  }

  /** Columns + the checkbox and ⋯ gutters, so a group header can span the whole row. */
  const columnCount = $derived(columns.length + (selectable ? 1 : 0) + (actions ? 1 : 0));

  // --- totals ----------------------------------------------------------------
  const hasTotals = $derived(columns.some((column) => column.total));

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

  const checkboxClass = "h-4 w-4 cursor-pointer rounded border-border text-brand focus:ring-brand";
</script>

<svelte:window onpointermove={onPointerMove} onpointerup={endResize} />

{#if rows.length === 0}
  {@render empty?.()}
{:else}
  {#if selectable && selection && selected.length > 0}
    {@render selection(selected)}
  {/if}

  <!-- Phone: the concept's shared row, never a sideways-scrolling grid. Groups survive here;
       they are how the board reads. -->
  {#if mobileRow}
    <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised sm:hidden">
      {#if grouped}
        {#each grouped as group (group.key)}
          <li class="bg-surface px-4 py-2">
            {@render groupToggle(group.key, group.label, group.rows.length, group.collapsible)}
          </li>
          {#if !collapsedSet.has(group.key)}
            {#each group.rows as row (row.id)}
              {@render mobileItem(row)}
            {/each}
          {/if}
        {/each}
      {:else}
        {#each rows as row (row.id)}
          {@render mobileItem(row)}
        {/each}
      {/if}
    </ul>
  {/if}

  <div
    class="overflow-x-auto rounded-xl border border-border bg-surface-raised
      {mobileRow ? 'hidden sm:block' : ''}"
  >
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border text-left text-xs text-text-muted">
          {#if selectable}
            <th scope="col" class="w-8 px-3 py-2">
              <input
                type="checkbox"
                class={checkboxClass}
                checked={allSelected}
                indeterminate={someSelected}
                aria-label={t("table.select_all")}
                onchange={toggleAll}
              />
            </th>
          {/if}
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
      {#if grouped}
        {#each grouped as group (group.key)}
          <tbody class="divide-y divide-border">
            <tr class="bg-surface">
              <th scope="colgroup" colspan={columnCount} class="px-4 py-2 text-left">
                {@render groupToggle(group.key, group.label, group.rows.length, group.collapsible)}
              </th>
            </tr>
            {#if !collapsedSet.has(group.key)}
              {#each group.rows as row (row.id)}
                {@render bodyRow(row)}
              {/each}
            {/if}
          </tbody>
        {/each}
      {:else}
        <tbody class="divide-y divide-border">
          {#each rows as row (row.id)}
            {@render bodyRow(row)}
          {/each}
        </tbody>
      {/if}

      {#if hasTotals}
        <!-- Aligned under their own columns — that is the point of a grid. The figures are the
             API's; this never sums `rows`, which are only the page. -->
        <tfoot class="border-t-2 border-border text-sm font-medium">
          <tr>
            {#if selectable}<td class="px-3 py-2.5"></td>{/if}
            {#each columns as column, index (column.key)}
              <td
                class="px-4 py-2.5 {column.align === 'right'
                  ? 'text-right tabular-nums'
                  : 'text-left'}"
              >
                {#if column.total}
                  {@render column.total()}
                {:else if index === 0}
                  <span class="text-text-muted">{t("table.total")}</span>
                {/if}
              </td>
            {/each}
            {#if actions}<td class="px-2 py-2.5"></td>{/if}
          </tr>
        </tfoot>
      {/if}
    </table>
  </div>
{/if}

{#snippet bodyRow(row: T)}
  <tr class="hover:bg-surface {selectedSet.has(row.id) ? 'bg-brand/5' : ''}">
    {#if selectable}
      <td class="px-3 py-2.5">
        <input
          type="checkbox"
          class={checkboxClass}
          checked={selectedSet.has(row.id)}
          aria-label={t("table.select_row")}
          onchange={() => toggleRow(row.id)}
        />
      </td>
    {/if}
    {#each columns as column, index (column.key)}
      <td class="px-4 py-2.5 {column.align === 'right' ? 'text-right tabular-nums' : 'text-left'}">
        {#if column.cell}
          {@render column.cell(row)}
        {:else if column.key.startsWith(CUSTOM_PREFIX)}
          <span class="text-text-muted">{customCellText(column.key, row, definitions, locale)}</span
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
{/snippet}

{#snippet mobileItem(row: T)}
  <li
    class="flex items-center gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl hover:bg-surface
      {selectedSet.has(row.id) ? 'bg-brand/5' : ''}"
  >
    {#if selectable}
      <!-- A phone gets the same bulk actions; it has rows, it just has no header row. -->
      <input
        type="checkbox"
        class={checkboxClass}
        checked={selectedSet.has(row.id)}
        aria-label={t("table.select_row")}
        onchange={() => toggleRow(row.id)}
      />
    {/if}
    <div class="min-w-0 flex-1">{@render mobileRow?.(row)}</div>
  </li>
{/snippet}

{#snippet groupToggle(key: string, label: string, count: number, collapsible?: boolean)}
  {#if collapsible}
    <button
      type="button"
      class="inline-flex cursor-pointer items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted hover:text-text"
      aria-expanded={!collapsedSet.has(key)}
      onclick={() => toggleGroup(key)}
    >
      {#if collapsedSet.has(key)}<ChevronRight size={13} />{:else}<ChevronDown size={13} />{/if}
      {label}
      <span class="font-normal tabular-nums">({count})</span>
    </button>
  {:else}
    <span
      class="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted"
    >
      {label}
      <span class="font-normal tabular-nums">({count})</span>
    </span>
  {/if}
{/snippet}
