<script lang="ts">
  /**
   * The inbox (issue #16).
   *
   * The shared `DataTable`, like every list (docs/UX.md): server-side sort and paging, a column
   * picker, a mobile row instead of a sideways-scrolling grid. Rows are grouped by their local
   * Amsterdam day — the only sort the API offers is chronological, so the sections and the sort
   * move along the same axis and never disagree. The sections are not collapsible: a day key is
   * meaningless tomorrow, so persisting "which days were folded" would be persisting noise.
   *
   * Marking read is a non-destructive, reversible toggle, so it stays inline rather than hiding
   * behind the ⋯ menu (docs/UX.md).
   *
   * **A row opens its record, and opening it marks it read.** Both halves were missing. The row
   * link was `rowHref`, which `DataTable` only draws when the primary column has *no* cell
   * snippet — this list gives all four columns one, so the desktop inbox was a wall of plain
   * text you could not click at all, while the phone (whose stretched overlay reads `rowHref`
   * directly) could. And a notification you have acted on stayed bold for ever unless you
   * remembered the ✓ beside it, which is the bell's #164 lesson never applied to the page the
   * bell links to. The sentence itself is the link now, in both layouts, and a plain left click
   * clears the row on the way out — a modifier-click (open in a new tab) deliberately does not,
   * because a row you sent to a background tab has not been read yet.
   */
  import { SvelteSet } from "svelte/reactivity";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { Check, Undo2 } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { resetPage } from "$lib/core/table/paging";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import { ENTITY_TYPES, NOTIFICATION_COLUMNS } from "$lib/modules/notifications/columns";
  import {
    dayLabel,
    localDay,
    notificationHref,
    notificationSubject,
    notificationText,
  } from "$lib/modules/notifications/format";

  let { data, form } = $props();

  const busy = new InFlight();

  type Item = (typeof data.items)[number];

  const table = createTableLayout<Item>({
    all: () => NOTIFICATION_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      message: messageCell,
      record: recordCell,
      actor: actorCell,
      when: whenCell,
    }),
  });

  // Sections in the order the rows arrive: the API already sorted them chronologically, so the
  // sections and the sort move along the same axis and can never disagree.
  const groups = $derived.by(() => {
    const out: { key: string; label: string; collapsible: boolean }[] = [];
    for (const item of data.items) {
      const key = localDay(item.created_at);
      // A page holds a handful of days — a scan beats the Set this would otherwise need.
      if (out.some((group) => group.key === key)) continue;
      out.push({ key, label: dayLabel(key), collapsible: false });
    }
    return out;
  });

  const hasUnread = $derived(data.items.some((item) => item.read_at === null));

  /**
   * Rows this session has cleared by opening them, so the dot goes out immediately.
   *
   * The PATCH is fire-and-forget through the bell's own proxy (the one seam the browser has to
   * the API, Golden Rule 6) — awaiting it would hold up the navigation the click is actually
   * for. `read_at` on the row is the server's answer and stays authoritative on the next load;
   * this set only covers the frame between the click and it.
   */
  const opened = new SvelteSet<string>();
  const isRead = (item: Item) => item.read_at !== null || opened.has(item.id);

  /**
   * A left click without modifiers is "I am reading this"; ⌘/ctrl/shift/middle is "later, in
   * another tab", which is exactly the case where marking it read loses the reminder.
   */
  function openRow(event: MouseEvent, item: Item): void {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return;
    }
    if (item.read_at !== null || opened.has(item.id)) return;
    opened.add(item.id);
    void fetch("/notifications/bell", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: item.id }),
    });
  }

  function filterHref(patch: Record<string, string | null>): string {
    const url = new URL(page.url);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    resetPage(url); // a new filter starts at the first page
    return url.pathname + url.search;
  }

  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm ${
      active ? "bg-surface font-medium text-text" : "text-text-muted hover:text-text"
    }`;
</script>

<svelte:head>
  <title>{pageTitle(t("notifications.title"))}</title>
</svelte:head>

<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{t("notifications.title")}</h1>
  {#if hasUnread}
    <form method="POST" action="?/markAllRead" use:enhance={busy.wrap()}>
      <Button variant="secondary" size="sm" loading={busy.active}>
        <Check size={15} />
        {t("notifications.mark_all_read")}
      </Button>
    </form>
  {/if}
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<div class="mb-3 flex flex-wrap items-center justify-between gap-3">
  <div class="flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    <a href={filterHref({ unread: null })} class={tabClass(!data.unreadOnly)}>
      {t("notifications.filter.all")}
    </a>
    <a href={filterHref({ unread: "1" })} class={tabClass(data.unreadOnly)}>
      {t("notifications.filter.unread")}
    </a>
    <span class="mx-1 h-4 w-px bg-border"></span>
    <a href={filterHref({ entity_type: null })} class={tabClass(data.entityType === null)}>
      {t("notifications.filter.everything")}
    </a>
    {#each ENTITY_TYPES as entity (entity)}
      <a href={filterHref({ entity_type: entity })} class={tabClass(data.entityType === entity)}>
        {t(`notifications.entity.${entity}`)}
      </a>
    {/each}
  </div>
  <ColumnPicker
    all={table.pickerColumns}
    visible={table.visibleKeys}
    sort={table.sort}
    onchange={table.onColumnsChange}
    onsort={table.onSort}
  />
</div>

{#snippet sentence(item: Item)}
  <!-- A person's event is a predicate after their name; a system reminder stands alone. -->
  {#if item.actor_name}
    {item.actor_name}
    {notificationText(item)}
  {:else}
    {notificationText(item)}
  {/if}
  {#if !isRead(item)}
    <span class="sr-only">{t("notifications.unread")}</span>
  {/if}
{/snippet}

{#snippet messageCell(item: Item)}
  {@const href = notificationHref(item)}
  <span class="flex items-start gap-2">
    <span
      class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full {isRead(item)
        ? 'bg-transparent'
        : 'bg-brand'}"
      aria-hidden="true"
    ></span>
    <!-- The sentence wraps rather than ellipsizing: it *is* the content of this list, and a
         notification cut at one line is one you have to open to read. `min-w-0` is what lets
         it wrap inside the flex row instead of overflowing it. -->
    {#if href}
      <a
        {href}
        class="min-w-0 hover:text-brand hover:underline {isRead(item)
          ? 'text-text-muted'
          : 'font-medium text-text'}"
        onclick={(event) => openRow(event, item)}
      >
        {@render sentence(item)}
      </a>
    {:else}
      <!-- No destination: a plain span, never an empty `href`, which navigates to the page you
           are already on and reads as a control that refuses (docs/UX.md, #253). -->
      <span class="min-w-0 {isRead(item) ? 'text-text-muted' : 'font-medium text-text'}">
        {@render sentence(item)}
      </span>
    {/if}
  </span>
{/snippet}

{#snippet recordCell(item: Item)}
  <!-- `block`, or `truncate` sets `nowrap` and nothing else: `overflow` and `text-overflow` do
       not apply to an inline box, so the name is cut mid-glyph with no ellipsis (#370). -->
  <span class="block truncate text-text-muted">{notificationSubject(item)}</span>
{/snippet}

{#snippet actorCell(item: Item)}
  <span class="block truncate text-text-muted">{item.actor_name ?? t("notifications.system")}</span>
{/snippet}

{#snippet whenCell(item: Item)}
  <span class="whitespace-nowrap text-text-muted">{fmtDateTime(item.created_at)}</span>
{/snippet}

{#snippet readToggle(item: Item)}
  <!-- `relative z-10` keeps mark-read tappable above the mobile row's stretched-link overlay (#59);
       harmless in the desktop actions cell where there is no overlay. -->
  <form method="POST" action="?/markRead" use:enhance class="relative z-10">
    <input type="hidden" name="id" value={item.id} />
    <input type="hidden" name="read" value={isRead(item) ? "false" : "true"} />
    <button
      class="rounded p-1.5 text-text-muted hover:bg-surface hover:text-brand"
      title={isRead(item) ? t("notifications.mark_unread") : t("notifications.mark_read")}
      aria-label={isRead(item) ? t("notifications.mark_unread") : t("notifications.mark_read")}
    >
      {#if isRead(item)}
        <Undo2 size={15} />
      {:else}
        <Check size={15} />
      {/if}
    </button>
  </form>
{/snippet}

{#snippet mobileRow(item: Item)}
  {@const href = notificationHref(item)}
  <div class="flex items-start gap-3">
    {#if href}
      <!-- The phone's whole-row tap (#59), rendered here rather than through `DataTable`'s
           `rowHref`: this row needs the click as well as the destination, and `rowHref` is a
           bare URL. `absolute inset-0` resolves against the `<li>`, which is already
           `relative`, so it stretches the same way — and the ✓ above it keeps its own tap. -->
      <a
        {href}
        class="absolute inset-0"
        aria-label={t("table.open_row")}
        onclick={(event) => openRow(event, item)}
      ></a>
    {/if}
    <span class="min-w-0 flex-1">
      <span class="block text-sm {isRead(item) ? 'text-text-muted' : 'font-medium text-text'}">
        {#if item.actor_name}{item.actor_name}{/if}
        {notificationText(item)}
      </span>
      <span class="mt-0.5 block text-xs text-text-muted">
        {item.actor_name ?? t("notifications.system")} · {fmtDateTime(item.created_at)}
      </span>
    </span>
    {@render readToggle(item)}
  </div>
{/snippet}

{#snippet empty()}
  <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
    {t("notifications.empty")}
  </p>
{/snippet}

<DataTable
  rows={data.items}
  columns={table.columns}
  sort={table.sort}
  widths={table.widths}
  locale={data.locale}
  actions={readToggle}
  {mobileRow}
  {empty}
  {groups}
  groupBy={(item) => localDay(item.created_at)}
  onsort={table.onSort}
  onresize={table.onResize}
/>

<Pagination
  total={data.total}
  page={data.paging.page}
  limit={data.paging.limit}
  onsize={table.onPageSize}
/>
