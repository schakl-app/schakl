<script lang="ts">
  /**
   * Searchable type-ahead select that plays nicely with SSR form actions: the picked value
   * is posted through a hidden input under `name`. Hand-rolled (like all UI here) so it can
   * match the app's Tailwind idiom exactly.
   */
  import { t } from "$lib/core/i18n";

  interface Item {
    value: string;
    label: string;
    hint?: string;
  }

  let {
    items,
    name,
    value = $bindable(""),
    placeholder = "",
    allowEmpty = true,
    id = name,
    formId,
    onselect,
    oncreate,
  }: {
    items: Item[];
    name: string;
    value?: string;
    placeholder?: string;
    allowEmpty?: boolean;
    id?: string;
    /** Associate the posted value with an external <form id=…> (single-save layouts). */
    formId?: string;
    onselect?: (value: string) => void;
    /** When provided, typing an unknown name offers a "add …" option in the dropdown. */
    oncreate?: (query: string) => void;
  } = $props();

  let query = $state("");
  let open = $state(false);
  let highlighted = $state(0);
  let inputEl: HTMLInputElement | undefined = $state();

  const selectedLabel = $derived(items.find((i) => i.value === value)?.label ?? "");
  const filtered = $derived(
    query.trim()
      ? items.filter((i) => i.label.toLowerCase().includes(query.trim().toLowerCase()))
      : items,
  );
  const canCreate = $derived(
    Boolean(oncreate) &&
      query.trim().length > 0 &&
      !items.some((i) => i.label.toLowerCase() === query.trim().toLowerCase()),
  );

  function startCreate() {
    const draft = query.trim();
    open = false;
    query = selectedLabel;
    oncreate?.(draft);
  }

  // Keep the visible text in sync when the selection changes from outside.
  $effect(() => {
    if (!open) query = selectedLabel;
  });

  function choose(item: Item | null) {
    value = item?.value ?? "";
    query = item?.label ?? "";
    open = false;
    onselect?.(value);
  }

  function onkeydown(e: KeyboardEvent) {
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      open = true;
      e.preventDefault();
      return;
    }
    if (!open) return;
    if (e.key === "ArrowDown") {
      highlighted = Math.min(highlighted + 1, filtered.length - 1);
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      highlighted = Math.max(highlighted - 1, 0);
      e.preventDefault();
    } else if (e.key === "Enter") {
      if (filtered[highlighted]) choose(filtered[highlighted]);
      else if (canCreate) startCreate();
      e.preventDefault();
    } else if (e.key === "Escape") {
      open = false;
      query = selectedLabel;
    }
  }

  function oninput() {
    open = true;
    highlighted = 0;
    // Clearing the text clears the selection (when allowed).
    if (query.trim() === "" && allowEmpty) value = "";
  }

  function onblur() {
    // Delay so an option mousedown can run first.
    setTimeout(() => {
      open = false;
      query = selectedLabel;
    }, 120);
  }
</script>

<div class="relative">
  <input type="hidden" {name} {value} form={formId} />
  <div class="relative">
    <input
      {id}
      bind:this={inputEl}
      bind:value={query}
      type="text"
      autocomplete="off"
      role="combobox"
      aria-expanded={open}
      aria-controls="{id}-listbox"
      {placeholder}
      class="w-full rounded-lg border border-neutral-300 px-3 py-2 pr-8 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      onfocus={() => {
        open = true;
        query = "";
      }}
      {oninput}
      {onkeydown}
      {onblur}
    />
    {#if value && allowEmpty}
      <button
        type="button"
        tabindex="-1"
        class="absolute inset-y-0 right-2 text-neutral-400 hover:text-neutral-700"
        aria-label={t("common.clear")}
        onmousedown={(e) => {
          e.preventDefault();
          choose(null);
          inputEl?.focus();
        }}>×</button
      >
    {:else}
      <span
        class="pointer-events-none absolute inset-y-0 right-2 flex items-center text-neutral-400"
        >▾</span
      >
    {/if}
  </div>

  {#if open}
    <ul
      id="{id}-listbox"
      role="listbox"
      class="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg"
    >
      {#if allowEmpty}
        <li>
          <button
            type="button"
            class="w-full px-3 py-1.5 text-left text-sm text-neutral-400 hover:bg-neutral-50"
            onmousedown={(e) => {
              e.preventDefault();
              choose(null);
            }}>{t("common.none")}</button
          >
        </li>
      {/if}
      {#each filtered as item, i (item.value)}
        <li>
          <button
            type="button"
            role="option"
            aria-selected={item.value === value}
            class="w-full px-3 py-1.5 text-left text-sm hover:bg-neutral-50
              {i === highlighted ? 'bg-neutral-50' : ''}
              {item.value === value ? 'font-medium text-brand' : 'text-neutral-800'}"
            onmousedown={(e) => {
              e.preventDefault();
              choose(item);
            }}
          >
            {item.label}
            {#if item.hint}<span class="ml-1 text-xs text-neutral-400">{item.hint}</span>{/if}
          </button>
        </li>
      {:else}
        {#if !canCreate}
          <li class="px-3 py-1.5 text-sm text-neutral-400">{t("common.no_results")}</li>
        {/if}
      {/each}
      {#if canCreate}
        <li class={filtered.length > 0 ? "border-t border-neutral-100" : ""}>
          <button
            type="button"
            class="w-full px-3 py-1.5 text-left text-sm font-medium text-brand hover:bg-neutral-50"
            onmousedown={(e) => {
              e.preventDefault();
              startCreate();
            }}
          >
            ＋ {t("common.create_named", { name: query.trim() })}
          </button>
        </li>
      {/if}
    </ul>
  {/if}
</div>
