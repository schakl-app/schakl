<script lang="ts">
  /** Debounced search box that syncs `?q=` — the SSR load does the actual filtering. */
  import { Search } from "@lucide/svelte";

  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { resetPage } from "$lib/core/table/paging";

  let {
    placeholder = t("common.search"),
    key = "q",
    wrapperClass = "relative w-56",
  }: {
    placeholder?: string;
    /** The query parameter this box owns. Only a list with two search boxes needs to say. */
    key?: string;
    /** Width, so the shared filter bar can go full-width on a phone without forking this. */
    wrapperClass?: string;
  } = $props();

  let value = $state(page.url.searchParams.get(key) ?? "");
  let timer: ReturnType<typeof setTimeout> | undefined;

  function apply() {
    const url = new URL(page.url);
    if (value.trim()) url.searchParams.set(key, value.trim());
    else url.searchParams.delete(key);
    // A new search is a new set — page 4 of the old one would land on an empty page and read
    // as "nothing found" (`paging.ts`).
    resetPage(url);
    void goto(url, { keepFocus: true, noScroll: true });
  }

  function oninput() {
    clearTimeout(timer);
    timer = setTimeout(apply, 300);
  }
</script>

<div class={wrapperClass}>
  <span class="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-text-muted">
    <Search size={15} />
  </span>
  <input
    type="search"
    bind:value
    {oninput}
    onkeydown={(e) => e.key === "Enter" && (clearTimeout(timer), apply())}
    {placeholder}
    class="w-full rounded-lg border border-border py-2 pl-8 pr-3 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
  />
</div>
