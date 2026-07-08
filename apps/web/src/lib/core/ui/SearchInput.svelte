<script lang="ts">
  /** Debounced search box that syncs `?q=` — the SSR load does the actual filtering. */
  import { Search } from "@lucide/svelte";

  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let { placeholder = t("common.search") }: { placeholder?: string } = $props();

  let value = $state(page.url.searchParams.get("q") ?? "");
  let timer: ReturnType<typeof setTimeout> | undefined;

  function apply() {
    const url = new URL(page.url);
    if (value.trim()) url.searchParams.set("q", value.trim());
    else url.searchParams.delete("q");
    void goto(url, { keepFocus: true, noScroll: true });
  }

  function oninput() {
    clearTimeout(timer);
    timer = setTimeout(apply, 300);
  }
</script>

<div class="relative w-56">
  <span class="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-neutral-400">
    <Search size={15} />
  </span>
  <input
    type="search"
    bind:value
    {oninput}
    onkeydown={(e) => e.key === "Enter" && (clearTimeout(timer), apply())}
    {placeholder}
    class="w-full rounded-lg border border-neutral-300 py-2 pl-8 pr-3 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
  />
</div>
