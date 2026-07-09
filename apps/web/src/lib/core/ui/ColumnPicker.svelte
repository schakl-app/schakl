<script lang="ts">
  /**
   * Choose and order a list's columns (#24). A personal, in-view option — it only ever touches
   * this user's own view (docs/UX.md principle 6), so it lives here on the list, not in Settings.
   *
   * Reordering offers drag *and* arrow buttons. The arrows are not a fallback nobody uses: they
   * are the only reorder a keyboard or a screen reader can reach, and `settings/dashboard`
   * already sets that precedent. The primary column is pinned — a row with no link out is a dead
   * end — so it is neither hideable nor movable.
   */
  import { ArrowDown, ArrowUp, Columns3, GripVertical } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { t } from "$lib/core/i18n";

  /** All the picker needs of a column: what to call it, and whether it can be turned off. */
  interface PickerColumn {
    key: string;
    label: string;
    primary?: boolean;
  }

  let {
    all,
    visible,
    onchange,
  }: {
    /** Every column this list can show, in declaration order, labels already translated. */
    all: PickerColumn[];
    /** Keys currently shown, in display order. */
    visible: string[];
    onchange: (keys: string[]) => void;
  } = $props();

  let open = $state(false);
  let root: HTMLElement | undefined = $state();

  const primaryKey = $derived(all.find((c) => c.primary)?.key ?? "");
  const labelOf = (key: string) => all.find((c) => c.key === key)?.label ?? key;

  // Chosen columns first (in their order), then the rest — so the list reads as "what you see,
  // then what you could add" rather than an alphabetical soup.
  const rows = $derived([
    ...visible.map((key) => ({ id: key, shown: true })),
    ...all.filter((c) => !visible.includes(c.key)).map((c) => ({ id: c.key, shown: false })),
  ]);

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
      class="absolute right-0 z-30 mt-1 w-64 rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
    >
      <p class="px-3 py-1.5 text-xs text-text-muted">{t("table.columns_hint")}</p>
      <ul
        class="max-h-80 overflow-y-auto"
        use:dndzone={{ items: dndRows, flipDurationMs: 120, dropTargetStyle: {} }}
        {onconsider}
        {onfinalize}
      >
        {#each dndRows as row (row.id)}
          {@const pinned = row.id === primaryKey}
          <li class="flex items-center gap-1.5 px-2 py-1 text-sm hover:bg-surface">
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
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</div>
