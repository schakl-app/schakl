<script lang="ts">
  /**
   * Searchable type-ahead select that plays nicely with SSR form actions: the picked value
   * is posted through a hidden input under `name`. Hand-rolled (like all UI here) so it can
   * match the app's Tailwind idiom exactly.
   */
  import { ChevronDown, ChevronRight } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  interface Item {
    value: string;
    label: string;
    hint?: string;
    /** Stands for several (`$lib/core/picker`): a chevron unfolds the rest under it. */
    expandable?: boolean;
  }
  /** A row as drawn: a child sits one level in and is searched only through its parent. */
  interface Row extends Item {
    depth: 0 | 1;
  }

  let {
    items,
    name,
    value = $bindable(""),
    placeholder = "",
    allowEmpty = true,
    id = name,
    formId,
    ariaLabel,
    listClass = "w-full",
    onselect,
    keepOpenOnSelect = false,
    oncreate,
    onsearch,
    searching = false,
    archived = [],
    archivedLabel,
    onexpand,
  }: {
    items: Item[];
    name: string;
    value?: string;
    placeholder?: string;
    allowEmpty?: boolean;
    id?: string;
    /** Associate the posted value with an external <form id=…> (single-save layouts). */
    formId?: string;
    /** Accessible name for pickers that have no visible <label> of their own (PhoneInput). */
    ariaLabel?: string;
    /** Dropdown width; a narrow trigger (the phone country picker) passes a wider one. */
    listClass?: string;
    onselect?: (value: string) => void;
    /**
     * This picker *adds to a list* — chips, invoice lines — so the list stays open after a pick
     * and the field goes back to empty, ready for the next one.
     *
     * The host is what makes that true: it takes the value in `onselect`, appends it somewhere
     * and clears the binding, so the picker never holds a selection to display. A single-value
     * picker leaves this off — there the pick *is* the answer, and closing says so.
     */
    keepOpenOnSelect?: boolean;
    /** When provided, typing an unknown name offers a "add …" option in the dropdown. */
    oncreate?: (query: string) => void;
    /**
     * Search server-side instead of filtering `items` in the browser (#290).
     *
     * Opt-in: a picker that omits it keeps the client-side filter below, unchanged. Provide it
     * where the full option set is too large to ship on every page render — the company page's
     * contact picker sent up to 500 people to fill a dropdown nobody had opened yet. `items`
     * then holds the *current* options and the host replaces them from the returned rows;
     * debouncing and stale-response ordering are handled here so every caller inherits them.
     */
    onsearch?: (query: string) => void;
    /** A search is in flight — shows the dropdown's loading row (server-search pickers only). */
    searching?: boolean;
    /**
     * Options that exist but are not on offer: shown **only once the user types**, below the
     * live ones and under `archivedLabel`.
     *
     * The list a picker opens with is a suggestion, and a finished task, a retired hosting plan
     * or a deactivated type is not one — offering it beside the live rows is how a time entry
     * lands on a task that was closed three months ago. Hiding it outright is the other mistake:
     * the record still exists, people still log against it, and a picker that cannot name it
     * sends them to a different screen. So it is reachable by searching for it and it says what
     * it is, which is the same rule the app applies to a page it will not draw a control for.
     *
     * Still selectable, still keyboard-reachable, and never re-ranked above a live option.
     * Ignored by server-search pickers (`onsearch`), where the API decides what came back.
     */
    archived?: Item[];
    /** Heading above the archived rows, e.g. "Afgerond". Core holds no module vocabulary. */
    archivedLabel?: string;
    /**
     * The rest of what an `expandable` option stands for, asked for the first time its chevron
     * is pressed and kept for the life of the list.
     *
     * A repeating task lays a year of occurrences out, and a picker that lists all twelve beside
     * every other task is a picker in which the *other* tasks cannot be found. So the host offers
     * the current one as a row and the rest nest under it — still pickable, one chevron away,
     * each labelled with what tells it apart (its date), and never ranked as a row of their own
     * among the live options. Children are drawn only while their parent is; the search matches
     * the parent's words, because the parent is the thing with a name.
     */
    onexpand?: (item: Item) => Promise<Item[]> | Item[];
  } = $props();

  let query = $state("");
  let open = $state(false);
  // -1 = nothing highlighted. Starting at 0 made Tab/Enter on a merely-focused picker
  // commit its first option — tabbing through a form must never change a selection.
  let highlighted = $state(-1);
  let inputEl: HTMLInputElement | undefined = $state();

  // An archived option is still *this* picker's option: the field has to be able to say what is
  // in it, or editing a time entry booked on a finished task shows an empty box.
  const selectedLabel = $derived(
    [...items, ...archived].find((i) => i.value === value)?.label ?? "",
  );
  // The hint is searchable too: a person is found by their email, a country ("NL +31")
  // by its full name in the hint. Create-detection stays label-only below.
  // Prefix matches outrank substring hits — typing "nederland" must offer Nederland before
  // Caribisch Nederland, or Enter picks the wrong one. Stable within each tier.
  function rank(list: Item[], q: string): Item[] {
    const starts = (s?: string) => s?.toLowerCase().startsWith(q) ?? false;
    const matches = list.filter(
      (i) => i.label.toLowerCase().includes(q) || (i.hint?.toLowerCase().includes(q) ?? false),
    );
    return [
      ...matches.filter((i) => starts(i.label) || starts(i.hint)),
      ...matches.filter((i) => !starts(i.label) && !starts(i.hint)),
    ];
  }
  const filtered = $derived.by(() => {
    // Server-searched pickers show what came back, in the order it came back: re-filtering it
    // here would hide rows the API matched on a field the label doesn't show.
    if (onsearch) return items;
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return rank(items, q);
  });
  // Ranked separately and appended, never merged: an archived row must not outrank a live one
  // on a better prefix match. Empty until the user types — that is the whole point of the bucket.
  const filteredArchived = $derived.by(() => {
    if (onsearch || archived.length === 0) return [];
    const q = query.trim().toLowerCase();
    return q ? rank(archived, q) : [];
  });
  // Which expandable options are unfolded, and what each one unfolded to (`null` = loading).
  // Keyed by value, so a re-render of `items` keeps the fold where the user left it.
  let expanded = $state<string[]>([]);
  let children = $state<Record<string, Item[] | null>>({});

  async function toggleExpand(item: Item) {
    if (expanded.includes(item.value)) {
      expanded = expanded.filter((value) => value !== item.value);
      return;
    }
    expanded = [...expanded, item.value];
    if (!(item.value in children)) {
      children = { ...children, [item.value]: null };
      const rows = await (onexpand?.(item) ?? []);
      children = { ...children, [item.value]: rows };
    }
  }

  /** The live rows as drawn: each unfolded parent followed by its children, one level in. */
  const liveRows = $derived.by(() => {
    const out: Row[] = [];
    for (const item of filtered) {
      out.push({ ...item, depth: 0 });
      if (item.expandable && expanded.includes(item.value)) {
        for (const child of children[item.value] ?? []) out.push({ ...child, depth: 1 });
      }
    }
    return out;
  });
  /** One flat list, so the keyboard walks live and archived rows as one sequence. */
  const options = $derived<Row[]>([
    ...liveRows,
    ...filteredArchived.map((item) => ({ ...item, depth: 0 as const })),
  ]);
  const canCreate = $derived(
    Boolean(oncreate) &&
      query.trim().length > 0 &&
      ![...items, ...archived].some((i) => i.label.toLowerCase() === query.trim().toLowerCase()),
  );

  function startCreate() {
    const draft = query.trim();
    open = false;
    query = selectedLabel;
    oncreate?.(draft);
  }

  /** The persistent ＋ next to the field: create without typing first. Prefills only a
   * live typed draft — never the already-selected label, which would seed the dialog with
   * an entity that exists. */
  function startCreateFromButton() {
    const draft = open ? query.trim() : "";
    open = false;
    query = selectedLabel;
    oncreate?.(draft && draft !== selectedLabel ? draft : "");
  }

  // Keep the visible text in sync when the selection changes from outside.
  $effect(() => {
    if (!open) query = selectedLabel;
  });

  /**
   * Show the list, and show all of it. The visible text is the current selection's label, so
   * opening without clearing it would filter the list down to the one option already picked.
   * Idempotent: a click on an unfocused field runs this from `mousedown` and then again from
   * `focus`, and the second must not wipe anything.
   */
  function openList() {
    if (open) return;
    open = true;
    query = "";
    highlighted = -1;
  }

  function choose(item: Item | null) {
    value = item?.value ?? "";
    query = item?.label ?? "";
    onselect?.(value);
    if (keepOpenOnSelect && item) {
      // The host has taken the pick and cleared the binding, so there is nothing to display and
      // the next one is what the user is here for. Closing would strand them: the mouse never
      // left the input, so no `focus` event is coming to reopen it.
      query = "";
      highlighted = -1;
      // The rows on screen answer a query that is no longer in the field.
      if (onsearch) search("");
      return;
    }
    open = false;
  }

  function onkeydown(e: KeyboardEvent) {
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      open = true;
      e.preventDefault();
      return;
    }
    if (!open) return;
    if (e.key === "ArrowDown") {
      highlighted = Math.min(highlighted + 1, options.length - 1);
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      highlighted = Math.max(highlighted - 1, 0);
      e.preventDefault();
    } else if (e.key === "Enter") {
      if (options[highlighted]) choose(options[highlighted]);
      else if (canCreate) startCreate();
      e.preventDefault();
    } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      // Unfold / fold the highlighted row, the way a tree does; a row with nothing behind it
      // keeps the keys for the caret, which is what they mean in a text field.
      const row = options[highlighted];
      if (row?.expandable && expanded.includes(row.value) === (e.key === "ArrowLeft")) {
        void toggleExpand(row);
        e.preventDefault();
      }
    } else if (e.key === "Escape") {
      open = false;
      query = selectedLabel;
      // Escape closed *this* list and nothing else (#361). Without stopping it, the event
      // reached the enclosing Modal's window handler and the reflex "dismiss the dropdown"
      // threw away the whole import wizard — the pasted table and every mapping decision —
      // with no confirmation and no way back.
      e.stopPropagation();
      e.preventDefault();
    } else if (e.key === "Tab") {
      // Commit the highlighted option and let focus move on naturally — never
      // preventDefault here. `highlighted` only ever indexes `options`, so this
      // can't accidentally trigger the create-row action.
      if (options[highlighted]) choose(options[highlighted]);
    }
  }

  // Debounce the server search so a typist causes one request, not one per keystroke
  // (docs/PERFORMANCE.md). Cleared on destroy so a pending timer can't fire into a dead
  // component. Inert unless `onsearch` was provided.
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => () => clearTimeout(searchTimer));

  function search(draft: string) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => onsearch?.(draft), 200);
  }

  function oninput() {
    open = true;
    highlighted = 0;
    // Clearing the text clears the selection (when allowed).
    if (query.trim() === "" && allowEmpty) value = "";
    if (onsearch) search(query.trim());
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
      aria-label={ariaLabel}
      {placeholder}
      class="w-full rounded-lg border border-border px-3 py-2 {oncreate
        ? 'pr-14'
        : 'pr-8'} text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      onfocus={openList}
      onmousedown={openList}
      {oninput}
      {onkeydown}
      {onblur}
    />
    {#if value && allowEmpty}
      <button
        type="button"
        tabindex="-1"
        class="absolute inset-y-0 {oncreate
          ? 'right-8'
          : 'right-2'} text-text-muted hover:text-text"
        aria-label={t("common.clear")}
        onmousedown={(e) => {
          e.preventDefault();
          choose(null);
          inputEl?.focus();
        }}>×</button
      >
    {:else}
      <span
        class="pointer-events-none absolute inset-y-0 {oncreate
          ? 'right-8'
          : 'right-2'} flex items-center text-text-muted">▾</span
      >
    {/if}
    {#if oncreate}
      <!-- Inline-create is per-picker definition of done (docs/UX.md); the ＋ makes the
           path visible without typing an unknown name first. -->
      <button
        type="button"
        tabindex="-1"
        class="absolute inset-y-0 right-2 flex items-center font-medium text-text-muted hover:text-brand"
        aria-label={t("common.create")}
        title={t("common.create")}
        onmousedown={(e) => {
          e.preventDefault();
          startCreateFromButton();
        }}>＋</button
      >
    {/if}
  </div>

  {#if open}
    <ul
      id="{id}-listbox"
      role="listbox"
      class="absolute z-20 mt-1 max-h-56 {listClass} overflow-auto rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
    >
      {#if allowEmpty}
        <li>
          <button
            type="button"
            class="w-full px-3 py-1.5 text-left text-sm text-text-muted hover:bg-surface"
            onmousedown={(e) => {
              e.preventDefault();
              choose(null);
            }}>{t("common.none")}</button
          >
        </li>
      {/if}
      {#each options as item, i (item.value)}
        {#if archivedLabel && i === liveRows.length}
          <!-- The archived rows are a different kind of answer, so they are labelled as one
               rather than blending into the list above them. -->
          <li
            class="mt-1 border-t border-border px-3 pb-0.5 pt-1.5 text-xs font-medium text-text-muted"
          >
            {archivedLabel}
          </li>
        {/if}
        <li class="flex items-stretch">
          <button
            type="button"
            role="option"
            aria-selected={item.value === value}
            class="min-w-0 flex-1 py-1.5 pr-3 text-left text-sm hover:bg-surface
              {item.depth === 1 ? 'pl-8' : 'pl-3'}
              {i === highlighted ? 'bg-surface' : ''}
              {item.value === value
              ? 'font-medium text-brand'
              : i >= liveRows.length
                ? 'text-text-muted'
                : 'text-text'}"
            onmousedown={(e) => {
              e.preventDefault();
              choose(item);
            }}
          >
            {item.label}
            {#if item.hint}<span class="ml-1 text-xs text-text-muted">{item.hint}</span>{/if}
          </button>
          {#if item.expandable && onexpand}
            <!-- Beside the option, never inside it: a button cannot nest in a button, and the
                 chevron must be pressable without picking the row it unfolds. -->
            <button
              type="button"
              tabindex="-1"
              class="flex w-8 shrink-0 items-center justify-center text-text-muted hover:bg-surface hover:text-text"
              aria-label={expanded.includes(item.value) ? t("common.collapse") : t("common.expand")}
              aria-expanded={expanded.includes(item.value)}
              data-testid="combobox-expand"
              onmousedown={(e) => {
                e.preventDefault();
                void toggleExpand(item);
              }}
            >
              {#if expanded.includes(item.value)}
                <ChevronDown size={14} />
              {:else}
                <ChevronRight size={14} />
              {/if}
            </button>
          {/if}
        </li>
        {#if item.expandable && expanded.includes(item.value) && children[item.value] === null}
          <li class="py-1 pl-8 text-xs text-text-muted">{t("common.loading")}</li>
        {/if}
      {:else}
        {#if searching}
          <li class="px-3 py-1.5 text-sm text-text-muted">{t("common.loading")}</li>
        {:else if !canCreate}
          <li class="px-3 py-1.5 text-sm text-text-muted">{t("common.no_results")}</li>
        {/if}
      {/each}
      {#if canCreate}
        <li class={options.length > 0 ? "border-t border-border" : ""}>
          <button
            type="button"
            class="w-full px-3 py-1.5 text-left text-sm font-medium text-brand hover:bg-surface"
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
