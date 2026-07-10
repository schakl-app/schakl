<script lang="ts">
  /**
   * Choose, order and **sort** a list's columns (#24). A personal, in-view option — it only ever
   * touches this user's own view (docs/UX.md principle 6), so it lives here on the list, not in
   * Settings.
   *
   * Sorting lives here as well as on the headers because **a phone has no headers**: below `sm`
   * the table gives way to the concept's row, and a sort you can only reach by clicking a `<th>`
   * is a sort mobile users do not have. This menu is the one surface both sizes share.
   *
   * Reordering offers drag *and* arrow buttons. The arrows are not a fallback nobody uses: they
   * are the only reorder a keyboard or a screen reader can reach, and `settings/dashboard`
   * already sets that precedent. The primary column is pinned — a row with no link out is a dead
   * end — so it is neither hideable nor movable.
   */
  import { ArrowDown, ArrowDownUp, ArrowUp, Columns3, GripVertical } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { t } from "$lib/core/i18n";
  import { nextSort, sortDirection } from "$lib/core/table/columns";

  /** All the picker needs of a column: what to call it, whether it pins, whether it sorts. */
  interface PickerColumn {
    key: string;
    label: string;
    primary?: boolean;
    /** The API's sort key. Absent ⇒ the server cannot order by it, so no control is offered. */
    sortKey?: string;
  }

  let {
    all,
    visible,
    sort = null,
    onchange,
    onsort,
  }: {
    /** Every column this list can show, in declaration order, labels already translated. */
    all: PickerColumn[];
    /** Keys currently shown, in display order. */
    visible: string[];
    /** The active sort, e.g. `"-name"`. */
    sort?: string | null;
    onchange: (keys: string[]) => void;
    onsort?: (sort: string | null) => void;
  } = $props();

  let open = $state(false);
  let root: HTMLElement | undefined = $state();

  const primaryKey = $derived(all.find((c) => c.primary)?.key ?? "");
  const columnOf = (key: string) => all.find((c) => c.key === key);
  const labelOf = (key: string) => columnOf(key)?.label ?? key;

  // Chosen columns first (in their order), then the rest — so the list reads as "what you see,
  // then what you could add" rather than an alphabetical soup.
  const rows = $derived([
    ...visible.map((key) => ({ id: key, shown: true })),
    ...all.filter((c) => !visible.includes(c.key)).map((c) => ({ id: c.key, shown: false })),
  ]);

  /** What the active sort is called, for the summary line. */
  const sortedColumn = $derived(
    sort ? all.find((c) => c.sortKey && c.sortKey === sort.replace(/^-/, "")) : undefined,
  );

  function toggle(key: string) {
    if (key === primaryKey) return;
    onchange(visible.includes(key) ? visible.filter((k) => k !== key) : [...visible, key]);
  }

  function move(key: string, delta: number) {
    const index = visible.indexOf(key);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= visible.length) return;
    const next = [...visible];
    [next[index], next[target]] = [next[target], next[index]];
    onchange(next);
  }

  function cycleSort(sortKey: string) {
    onsort?.(nextSort(sort, sortKey));
  }

  // Dragging reorders only the shown columns; a hidden one has no position to occupy.
  // A writable `$derived`: it tracks `rows` until a drag overwrites it mid-gesture.
  let dndRows = $derived(rows);
  function onconsider(e: CustomEvent<{ items: { id: string; shown: boolean }[] }>) {
    dndRows = e.detail.items;
  }
  function onfinalize(e: CustomEvent<{ items: { id: string; shown: boolean }[] }>) {
    dndRows = e.detail.items;
    onchange(dndRows.filter((r) => r.shown).map((r) => r.id));
  }
</script>

<svelte:window
  onclick={(e) => {
    if (open && root && !root.contains(e.target as Node)) open = false;
  }}
  onkeydown={(e) => {
    if (e.key === "Escape") open = false;
  }}
/>

<div class="relative" bind:this={root}>
  <button
    type="button"
    class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
    aria-expanded={open}
    aria-haspopup="true"
    onclick={() => (open = !open)}
  >
    <Columns3 size={15} />
    {t("table.columns")}
  </button>

  {#if open}
    <div
      class="absolute right-0 z-30 mt-1 w-72 rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
    >
      <p class="px-3 pt-1.5 text-xs text-text-muted">{t("table.columns_hint")}</p>
      {#if sortedColumn}
        <p class="flex items-center gap-1 px-3 pb-1.5 pt-1 text-xs text-brand">
          {#if sortDirection(sort, sortedColumn.sortKey!) === "asc"}
            <ArrowUp size={11} />
          {:else}
            <ArrowDown size={11} />
          {/if}
          {t("table.sorted_by", { column: sortedColumn.label })}
        </p>
      {:else}
        <p class="px-3 pb-1.5 pt-1 text-xs text-text-muted">{t("table.sort_hint")}</p>
      {/if}

      <ul
        class="max-h-80 overflow-y-auto border-t border-border pt-1"
        use:dndzone={{ items: dndRows, flipDurationMs: 120, dropTargetStyle: {} }}
        {onconsider}
        {onfinalize}
      >
        {#each dndRows as row (row.id)}
          {@const column = columnOf(row.id)}
          {@const pinned = row.id === primaryKey}
          {@const direction = column?.sortKey ? sortDirection(sort, column.sortKey) : null}
          <li class="flex items-center gap-1 px-2 py-1 text-sm hover:bg-surface">
            <span class="text-text-muted/60" aria-hidden="true"><GripVertical size={13} /></span>
            <label class="flex flex-1 cursor-pointer items-center gap-2 truncate">
              <input
                type="checkbox"
                checked={row.shown}
                disabled={pinned}
                class="h-3.5 w-3.5 rounded border-border text-brand focus:ring-brand disabled:opacity-50"
                onchange={() => toggle(row.id)}
              />
              <span class="truncate text-text">{labelOf(row.id)}</span>
            </label>

            {#if column?.sortKey && onsort}
              <!-- The only sort control a phone can reach. Cycles ascending → descending → off. -->
              <button
                type="button"
                class="cursor-pointer rounded p-0.5 {direction
                  ? 'text-brand'
                  : 'text-text-muted hover:text-brand'}"
                title={t("table.sort_by", { column: labelOf(row.id) })}
                aria-label={t("table.sort_by", { column: labelOf(row.id) })}
                aria-pressed={direction !== null}
                onclick={() => cycleSort(column.sortKey!)}
              >
                {#if direction === "asc"}<ArrowUp size={13} />
                {:else if direction === "desc"}<ArrowDown size={13} />
                {:else}<ArrowDownUp size={13} />{/if}
              </button>
            {:else}
              <span class="w-[22px]" aria-hidden="true"></span>
            {/if}

            {#if row.shown && !pinned}
              <button
                type="button"
                class="cursor-pointer rounded p-0.5 text-text-muted hover:text-brand disabled:opacity-30"
                title={t("table.move_up")}
                aria-label={t("table.move_up")}
                disabled={visible.indexOf(row.id) <= 0}
                onclick={() => move(row.id, -1)}><ArrowUp size={13} /></button
              >
              <button
                type="button"
                class="cursor-pointer rounded p-0.5 text-text-muted hover:text-brand disabled:opacity-30"
                title={t("table.move_down")}
                aria-label={t("table.move_down")}
                disabled={visible.indexOf(row.id) >= visible.length - 1}
                onclick={() => move(row.id, 1)}><ArrowDown size={13} /></button
              >
            {:else}
              <span class="w-[44px]" aria-hidden="true"></span>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</div>
