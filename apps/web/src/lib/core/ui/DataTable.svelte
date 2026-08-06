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
  import { rangeSelection } from "$lib/core/table/selection";

  let {
    rows,
    columns,
    sort = null,
    widths = {},
    definitions = [],
    locale = "nl",
    rowHref,
    onRowClick,
    actions,
    actionsWidth = 52,
    mobileRow,
    empty,
    selectable = false,
    selected = $bindable([]),
    selection,
    groups,
    groupBy,
    groupSummary,
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
    /** Open a row without navigating — e.g. a detail modal (#184). Takes precedence over
     *  `rowHref` for the whole-row click; inner links/buttons keep their own action. */
    onRowClick?: (row: T) => void;
    /** Trailing ⋯ cell (docs/UX.md: record actions live behind the overflow menu). */
    actions?: Snippet<[T]>;
    /**
     * Width of that trailing cell, in px. 52 is the ⋯ trigger (34) plus the cell's own `px-2`,
     * which is all it holds on almost every list — the `w-10` it used to be never fitted, and
     * auto layout was silently widening it. A list that also puts a labelled button there has
     * to say so: under `table-fixed` a column no longer grows to its content, it paints over
     * its neighbour.
     */
    actionsWidth?: number;
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
    /**
     * The section(s) a row belongs to. Returning **several** keys lists the same record under
     * each — a contact linked to two clients belongs under both, and picking one would be a lie
     * either way. It is one record drawn twice, not two: the id repeats *across* sections and
     * never within one, so the keyed `{#each}` and the id-keyed selection both stay correct.
     */
    groupBy?: (row: T) => string | string[];
    /** Per-group figures in the header row (#277) — the caller's, from the API's own
     *  aggregate, for exactly the reason the footer never sums `rows`. */
    groupSummary?: Snippet<[string]>;
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

  /** The last row ticked on its own — where a shift-click measures its range from. */
  let anchor = $state<string | null>(null);

  // A new page, filter or sort is a different set of rows; a selection made against the old one
  // is meaningless and must not survive into a bulk action.
  $effect(() => {
    void rows;
    selected = [];
    anchor = null;
  });

  function toggleAll() {
    selected = allSelected ? [] : rows.map((row) => row.id);
    anchor = null;
  }

  function toggleRow(id: string) {
    selected = selectedSet.has(id) ? selected.filter((s) => s !== id) : [...selected, id];
    anchor = id;
  }

  /**
   * The order a shift-click measures its range in: the sections as drawn, minus the collapsed
   * ones, each row once. `rangeSelection` documents why it is the visible order and not `rows`.
   */
  function visibleIds(): string[] {
    const source = grouped
      ? grouped.filter((group) => !collapsedSet.has(group.key)).flatMap((group) => group.rows)
      : rows;
    return [...new Set(source.map((row) => row.id))];
  }

  function extendTo(id: string) {
    selected = rangeSelection(visibleIds(), anchor, id, selected);
    // The anchor stays put, so dragging the range wider or narrower keeps measuring from the same
    // end — moving it would make the next shift-click select from the wrong row.
  }

  /**
   * One entry point for every way a row gets ticked, and it hangs on the **label**, not the box.
   * A click that lands in the padding never reaches the box on its own: chrome suppresses a
   * label's forward-to-its-control when shift makes the click a text-selection gesture, so the
   * enlarged hit area would have swallowed exactly the shift-click it exists to catch.
   *
   * Which leaves the two paths to tell apart, and `preventDefault` is the difference:
   * - on the box itself (or the space bar, which fires a click there too) the browser has
   *   already flipped it, and that flip always agrees with the state below — the clicked row
   *   ends on `!checked` whether it toggled or led a range — so it stands. Cancelling it here
   *   would be worse than redundant: the browser restores the *old* value after the handler
   *   returns, leaving a ticked box over an unselected row.
   * - anywhere else in the label it has not, so the forward is cancelled (it would arrive as a
   *   second click on the box) and the work is done here.
   */
  function pickRow(event: MouseEvent, id: string) {
    if ((event.target as HTMLElement).tagName !== "INPUT") event.preventDefault();
    if (event.shiftKey) extendTo(id);
    else toggleRow(id);
  }

  /** The same two paths, for the header's select-all — which has no range to extend. */
  function pickAll(event: MouseEvent) {
    if ((event.target as HTMLElement).tagName !== "INPUT") event.preventDefault();
    toggleAll();
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
      const keys = groupBy(row);
      let placed = false;
      for (const key of typeof keys === "string" ? [keys] : keys) {
        const bucket = buckets.get(key);
        if (bucket) {
          bucket.push(row);
          placed = true;
        }
      }
      // Undeclared *for every one of its keys* — a row that landed in at least one section is
      // not a stray, or a two-client contact whose second client fell outside the page would be
      // listed twice: once where it belongs and once under "Other".
      if (!placed) strays.push(row);
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

  /**
   * The column that absorbs whatever the fixed ones leave — see the `table-fixed` note below.
   * A column that says `flex` wins; otherwise the primary one, which is the long column on most
   * lists; otherwise the first. Always exactly one, so the declared widths always sum to less
   * than the table.
   */
  const flexKey = $derived(
    (columns.find((c) => c.flex) ?? columns.find((c) => c.primary) ?? columns[0])?.key,
  );

  function headerWidth(column: ColumnSpec<T>): number | undefined {
    // A width the user dragged is authoritative even on the flexible column: they asked for
    // that number, and under a fixed layout they now actually get it.
    return widths[column.key] ?? (column.key === flexKey ? undefined : column.width);
  }

  /**
   * The floor under the flexible column, in px.
   *
   * Absorbing the slack is fine until there is none: switch enough optional columns on and the
   * declared widths exceed the table, at which point the one column with no width of its own
   * gets what is left — nothing. The record's own name, the only cell that links out of the
   * row, then renders as an empty gap. A floor turns that into an honest sideways scroll, which
   * is what a grid genuinely too wide for its screen should do.
   */
  const FLEX_MIN = 160;

  function cellStyle(column: ColumnSpec<T>): string | undefined {
    const width = headerWidth(column);
    return width ? `width:${width}px` : undefined;
  }

  /**
   * The floor, expressed where a fixed layout will actually honour it: on the **table**.
   *
   * It used to be `min-width` on the flexible column's own cells, and that did nothing at all.
   * Fixed layout sizes a column from the `width` of its first-row cell and from nothing else —
   * `min-width` on a table cell does not enter the algorithm — so once the declared widths
   * summed past the box, the table grew to that sum and the one auto column was allotted what
   * remained: zero. Measured on a 820px screen: `Naam` 0px wide, `Klantnummer` through
   * `Beschikbare uren` all at their full width, and an 812px table in a 532px box. The identity
   * column — the only cell that links out of the row — was not merely narrow, it was *gone*,
   * while every optional column to its right survived and the user scrolled sideways looking
   * for the name. The exact failure the floor was written to prevent.
   *
   * On the table it is one value the fixed algorithm does read: the used width becomes
   * `max(100%, min-width)`, so the auto column is handed `min-width − Σ declared` = `FLEX_MIN`
   * at its worst and every pixel of the slack when there is any. Computed from widths we
   * already hold, so it is right in the SSR HTML rather than after a measurement on mount.
   */
  /** The `w-10` checkbox gutter, in px — declared in Tailwind rather than in a width style. */
  const SELECT_COL = 40;

  const tableMinWidth = $derived(
    columns.reduce(
      // A width the user dragged onto the flexible column is its floor; otherwise FLEX_MIN is.
      (sum, column) => sum + (headerWidth(column) ?? (column.key === flexKey ? FLEX_MIN : 0)),
      0,
    ) +
      (selectable ? SELECT_COL : 0) +
      (actions ? actionsWidth : 0),
  );

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
            <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
              {@render groupToggle(group.key, group.label, group.rows.length, group.collapsible)}
              {#if groupSummary}{@render groupSummary(group.key)}{/if}
            </div>
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
    <!--
      `table-fixed`, and it is what stops the grid scrolling sideways on an ordinary laptop.

      Under the default auto layout a declared `width` is only a hint: the used width is
      `max(width, min-content)`, and every cell here truncates with `white-space: nowrap`, which
      makes a column's min-content its whole unbroken line. `overflow: hidden` does not reduce
      that — it clips only once a definite width exists, which auto layout never gives. So the
      table grew past `w-full` (measured: 1423px of content in a 1150px box on a 1440 screen),
      the ellipsis never appeared, and dragging a column narrower did nothing, because the width
      being written was the one the layout was ignoring. A fixed layout makes the declared widths
      real, `truncate` truncate, and the resize handle mean something.

      The one thing fixed layout cannot do is invent slack, so exactly one column carries no
      declared width and absorbs it (`flexKey`) — otherwise a list whose columns sum past the
      viewport would trade a scrollbar for an overflow. When there is no slack to absorb,
      `min-width` on the table (`tableMinWidth`) is what stops that column being handed zero.
    -->
    <table class="w-full table-fixed text-sm" style="min-width:{tableMinWidth}px">
      <thead>
        <tr class="border-b border-border text-left text-xs text-text-muted">
          {#if selectable}
            <th scope="col" class="w-10 p-0">
              <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
              <label
                class="flex cursor-pointer items-center justify-center px-3 py-2"
                onclick={pickAll}
              >
                <input
                  type="checkbox"
                  class={checkboxClass}
                  checked={allSelected}
                  indeterminate={someSelected}
                  aria-label={t("table.select_all")}
                />
              </label>
            </th>
          {/if}
          {#each columns as column (column.key)}
            {@const direction = column.sortKey ? sortDirection(sort, column.sortKey) : null}
            <th
              scope="col"
              class="relative overflow-hidden px-4 py-2 font-medium {column.align === 'right'
                ? 'text-right'
                : 'text-left'}"
              style={cellStyle(column)}
              aria-sort={direction === "asc"
                ? "ascending"
                : direction === "desc"
                  ? "descending"
                  : undefined}
            >
              {#if column.sortKey}
                <button
                  type="button"
                  class="inline-flex max-w-full cursor-pointer items-center gap-1 hover:text-text"
                  onclick={() => onsort?.(nextSort(sort, column.sortKey!))}
                >
                  <span class="truncate">{column.label}</span>
                  {#if direction === "asc"}<ArrowUp
                      size={12}
                    />{:else if direction === "desc"}<ArrowDown size={12} />{/if}
                </button>
              {:else}
                <!-- `block`, because `overflow` does not apply to an inline box: a bare
                     `truncate` span sets `nowrap` and nothing else, so a long header spills
                     into the next column instead of ellipsizing. -->
                <span class="block truncate">{column.label}</span>
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
            <th scope="col" class="px-2 py-2" style="width:{actionsWidth}px"
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
                <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                  {@render groupToggle(
                    group.key,
                    group.label,
                    group.rows.length,
                    group.collapsible,
                  )}
                  {#if groupSummary}{@render groupSummary(group.key)}{/if}
                </div>
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
            {#if selectable}<td class="w-10 py-2.5"></td>{/if}
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
  <!-- A whole-row click that opens a modal rather than navigating (#184). Ignore clicks that
       land on an inner link/button/input so chips and the ⋯ menu keep their own action.
       Shift extends the selection instead of opening the record: on a selectable list that is
       what the gesture means everywhere else, and answering it with a modal is the surprise. -->
  <tr
    class="hover:bg-surface {onRowClick ? 'cursor-pointer' : ''} {selectedSet.has(row.id)
      ? 'bg-brand/5'
      : ''}"
    onclick={onRowClick
      ? (e) => {
          if ((e.target as HTMLElement).closest("a,button,input,label,select")) return;
          if (selectable && e.shiftKey && anchor !== null) {
            // A shift-click drags a text selection across the rows on the way; clear it, or the
            // range the user asked for arrives under a blue smear of highlighted cells.
            window.getSelection()?.removeAllRanges();
            extendTo(row.id);
            return;
          }
          onRowClick(row);
        }
      : undefined}
  >
    {#if selectable}
      <!-- The whole gutter cell is the box's label, not just the 16 px box itself: a click that
           landed a few pixels off used to miss the checkbox, fall through to the row, and open
           the record the user was trying to tick. A stretched `<label>` is also what keeps the
           near-miss out of the row handler below, which already ignores clicks on one. -->
      <td class="relative w-10 p-0">
        <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
        <label
          class="absolute inset-0 flex cursor-pointer items-center justify-center"
          onclick={(e) => pickRow(e, row.id)}
        >
          <input
            type="checkbox"
            class={checkboxClass}
            checked={selectedSet.has(row.id)}
            aria-label={t("table.select_row")}
          />
        </label>
      </td>
    {/if}
    {#each columns as column, index (column.key)}
      <!-- `overflow-hidden`: a column no longer grows to its content under the fixed layout, so
           anything wider has to be clipped by the cell or it paints over its neighbour. Cells
           that want an ellipsis rather than a hard edge put `truncate` on their own content —
           which only works because it is clipped here. -->
      <td
        class="overflow-hidden px-4 py-2.5 {column.align === 'right'
          ? 'text-right tabular-nums'
          : 'text-left'}"
      >
        {#if column.cell}
          {@render column.cell(row)}
        {:else if column.key.startsWith(CUSTOM_PREFIX)}
          <span class="block truncate text-text-muted"
            >{customCellText(column.key, row, definitions, locale)}</span
          >
        {:else if index === 0 && rowHref}
          <a href={rowHref(row)} class="block truncate font-medium text-text hover:text-brand"
            >{String((row as Record<string, unknown>)[column.key] ?? "—")}</a
          >
        {:else}
          <span class="block truncate"
            >{String((row as Record<string, unknown>)[column.key] ?? "—")}</span
          >
        {/if}
      </td>
    {/each}
    {#if actions}
      <td class="px-2 py-2.5 text-right">{@render actions(row)}</td>
    {/if}
  </tr>
{/snippet}

{#snippet mobileItem(row: T)}
  {@const href = rowHref?.(row)}
  <li
    class="relative flex items-center gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl hover:bg-surface
      {selectedSet.has(row.id) ? 'bg-brand/5' : ''}"
  >
    {#if onRowClick}
      <!-- Same stretched-overlay trick as the link below, but it opens a modal instead of
           navigating (#184). Inner controls lifted with `relative z-10` keep their own tap. -->
      <button
        type="button"
        class="absolute inset-0"
        aria-label={t("table.open_row")}
        onclick={() => onRowClick(row)}
      ></button>
    {:else if href}
      <!-- A `<tr>` can't be wrapped in an `<a>`, but a `<li>` can: on the phone the whole row taps
           through to its record (#59). This is a stretched-link overlay, so plain content is
           covered and navigates, while positioned inline controls — the ⋯ menu (already
           `relative`), the checkbox and toggles lifted with `relative z-10` — paint above it and
           keep their own tap. -->
      <a {href} class="absolute inset-0" aria-label={t("table.open_row")}></a>
    {/if}
    {#if selectable}
      <!-- A phone gets the same bulk actions; it has rows, it just has no header row. The label's
           negative margin cancels its own padding, so the tap target grows past the box in every
           direction while the row is laid out exactly as before — and it paints above the
           stretched overlay, so a near-miss ticks the row instead of opening it. -->
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
      <label
        class="relative z-10 -m-2 flex cursor-pointer items-center p-2"
        onclick={(e) => pickRow(e, row.id)}
      >
        <input
          type="checkbox"
          class={checkboxClass}
          checked={selectedSet.has(row.id)}
          aria-label={t("table.select_row")}
        />
      </label>
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
