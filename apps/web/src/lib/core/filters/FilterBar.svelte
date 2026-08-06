<script lang="ts">
  /**
   * The filter row as it appears on every list that has one (`types.ts` has the why).
   *
   * It owns the whole strip, not just the filters: the controls read left-to-right and what you
   * can *do* with the list — Export/Import, Kolommen, the ✎ — sits at the far end, pushed right
   * (docs/UX.md). That order was already the rule and was already re-typed per screen; a bar
   * that renders the filters but leaves the toolbar to the caller would keep letting it drift,
   * so `actions` is a snippet inside this component rather than a sibling `<div>` beside it.
   *
   * Below `sm` the filters collapse behind one toggle carrying a count. A phone is not a smaller
   * desktop: six stacked controls push the actual rows a full screen down, and the count is what
   * keeps a *hidden* filter from silently explaining an empty list. The actions stay visible —
   * they are not filters, and hiding Kolommen behind "Filters" would be a lie about both.
   */
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  import { filterKeys, filterUrl, type FilterDef } from "./types";
  import type { Snippet } from "svelte";

  let {
    filters,
    actions,
    idPrefix = "filter",
  }: {
    filters: FilterDef[];
    /** The list's own controls (ImpexBar, ColumnPicker, BulkToggle), pushed to the far end. */
    actions?: Snippet;
    /** Disambiguates the generated control ids when two bars share a page. */
    idPrefix?: string;
  } = $props();

  const shown = $derived(filters.filter((def) => !def.hidden));

  /** What each filter is set to. A def's own `value` wins; otherwise the URL is the view. */
  function current(def: FilterDef): string {
    return def.value ?? page.url.searchParams.get(def.key)?.trim() ?? "";
  }

  // A number, not the array it comes from: the effect below must re-run when the count changes
  // and *not* on every navigation, or sorting a filtered list would re-open the bar the user
  // just collapsed (a fresh array is never equal to the last one; a number often is).
  const activeCount = $derived(shown.filter((def) => current(def) !== "").length);

  function apply(key: string, value: string) {
    // `keepFocus` so typing in a picker survives the navigation, `noScroll` so applying a
    // filter does not throw the user back to the top of a list they were reading.
    void goto(filterUrl(page.url, key, value), { keepFocus: true, noScroll: true });
  }

  /**
   * Clear exactly the keys this bar owns.
   *
   * Not `href="/domains"`, which also drops the sort and the saved-size override the user
   * chose — those are not filters. Not a hand-written list of three `delete`s either: that is
   * the copy that goes stale the day a fourth filter is added, and its failure is invisible
   * (the button still works, it just quietly leaves one filter on).
   */
  function clearAll() {
    const url = new URL(page.url);
    for (const key of filterKeys(shown)) url.searchParams.delete(key);
    url.searchParams.delete("page");
    void goto(url, { keepFocus: true, noScroll: true });
  }

  // Open on arrival when a filter is already set — landing on a deep link from a client card
  // with the reason hidden behind a toggle is how a narrowed list reads as a broken one.
  let open = $state(false);
  $effect(() => {
    if (activeCount > 0) open = true;
  });
</script>

<div class="mb-4 flex flex-wrap items-center gap-2">
  {#if shown.length > 0}
    <button
      type="button"
      class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text sm:hidden {open
        ? 'border-brand text-brand'
        : ''}"
      aria-expanded={open}
      onclick={() => (open = !open)}
    >
      {t("common.filters")}
      {#if activeCount > 0}
        <!-- The count is what stops a collapsed bar from silently explaining an empty list. -->
        <span class="rounded-full bg-brand px-1.5 py-0.5 text-[10px] font-semibold text-white">
          {activeCount}
        </span>
      {/if}
    </button>
  {/if}

  <div class="w-full flex-wrap items-center gap-2 sm:flex sm:w-auto {open ? 'flex' : 'hidden'}">
    {#each shown as def (def.key)}
      {#if def.kind === "search"}
        <SearchInput
          key={def.key}
          placeholder={def.placeholder ?? t("common.search")}
          wrapperClass="relative w-full sm:w-56"
        />
      {:else if def.kind === "select"}
        <div class="w-full sm:w-44">
          <Combobox
            items={def.options}
            name="_filter_{def.key}"
            value={current(def)}
            placeholder={def.placeholder}
            onselect={(value) => apply(def.key, value)}
            id="{idPrefix}-{def.key}"
          />
        </div>
      {:else}
        {#each def.options as option (option.value)}
          {@const on = current(def) === option.value}
          <button
            type="button"
            class="rounded-full px-3 py-1 text-xs font-medium {on
              ? 'bg-brand/10 text-brand ring-2 ring-brand'
              : 'bg-surface text-text-muted hover:text-text'}"
            aria-pressed={on}
            onclick={() => apply(def.key, on ? "" : option.value)}
          >
            {option.label}
          </button>
        {/each}
      {/if}
    {/each}

    {#if activeCount > 0}
      <button
        type="button"
        class="text-xs text-text-muted underline hover:text-text"
        onclick={clearAll}
      >
        {t("common.filters_clear")}
      </button>
    {/if}
  </div>

  {#if actions}
    <!-- The list's own controls, pushed right: the filters read left-to-right, what you can
         *do* with the list sits at the far end, and that is the same on every list here. -->
    <div class="ml-auto flex flex-wrap items-center gap-2">{@render actions()}</div>
  {/if}
</div>
