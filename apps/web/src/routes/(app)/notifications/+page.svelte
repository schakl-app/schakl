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
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { Check, Undo2 } from "@lucide/svelte";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import { ENTITY_TYPES, NOTIFICATION_COLUMNS } from "$lib/modules/notifications/columns";
  import {
    dayLabel,
    localDay,
    notificationHref,
    notificationSubject,
    notificationText,
  } from "$lib/modules/notifications/format";

  let { data, form } = $props();

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

  function filterHref(patch: Record<string, string | null>): string {
    const url = new URL(page.url);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    url.searchParams.delete("offset"); // a new filter starts at the first page
    return url.pathname + url.search;
  }

  function pageHref(offset: number): string {
    const url = new URL(page.url);
    if (offset <= 0) url.searchParams.delete("offset");
    else url.searchParams.set("offset", String(offset));
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
    <form method="POST" action="?/markAllRead" use:enhance>
      <button
        class="flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand hover:text-brand"
      >
        <Check size={15} />
        {t("notifications.mark_all_read")}
      </button>
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
      <a
        href={filterHref({ entity_type: entity })}
        class={tabClass(data.entityType === entity)}
      >
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

{#snippet messageCell(item: Item)}
  <span class="flex items-start gap-2">
    <span
      class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full {item.read_at === null
        ? 'bg-brand'
        : 'bg-transparent'}"
      aria-hidden="true"
    ></span>
    <span class={item.read_at === null ? "font-medium text-text" : "text-text-muted"}>
      <!-- A person's event is a predicate after their name; a system reminder stands alone. -->
      {#if item.actor_name}
        {item.actor_name}
        {notificationText(item)}
      {:else}
        {notificationText(item)}
      {/if}
      {#if item.read_at === null}
        <span class="sr-only">{t("notifications.unread")}</span>
      {/if}
    </span>
  </span>
{/snippet}

{#snippet recordCell(item: Item)}
  <span class="truncate text-text-muted">{notificationSubject(item)}</span>
{/snippet}

{#snippet actorCell(item: Item)}
  <span class="truncate text-text-muted">{item.actor_name ?? t("notifications.system")}</span>
{/snippet}

{#snippet whenCell(item: Item)}
  <span class="whitespace-nowrap text-text-muted">{fmtDateTime(item.created_at)}</span>
{/snippet}

{#snippet readToggle(item: Item)}
  <form method="POST" action="?/markRead" use:enhance>
    <input type="hidden" name="id" value={item.id} />
    <input type="hidden" name="read" value={item.read_at === null ? "true" : "false"} />
    <button
      class="rounded p-1.5 text-text-muted hover:bg-surface hover:text-brand"
      title={item.read_at === null ? t("notifications.mark_read") : t("notifications.mark_unread")}
      aria-label={item.read_at === null
        ? t("notifications.mark_read")
        : t("notifications.mark_unread")}
    >
      {#if item.read_at === null}
        <Check size={15} />
      {:else}
        <Undo2 size={15} />
      {/if}
    </button>
  </form>
{/snippet}

{#snippet mobileRow(item: Item)}
  <div class="flex items-start gap-3">
    <span class="min-w-0 flex-1">
      <span
        class="block text-sm {item.read_at === null
          ? 'font-medium text-text'
          : 'text-text-muted'}"
      >
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
  rowHref={(item) => notificationHref(item) ?? ""}
  actions={readToggle}
  {mobileRow}
  {empty}
  {groups}
  groupBy={(item) => localDay(item.created_at)}
  onsort={table.onSort}
  onresize={table.onResize}
/>

{#if data.total > data.limit}
  <div class="mt-4 flex items-center justify-between text-sm" data-sveltekit-preload-data="hover">
    <span class="text-text-muted">
      {t("notifications.page_of", {
        from: data.offset + 1,
        to: Math.min(data.offset + data.limit, data.total),
        total: data.total,
      })}
    </span>
    <div class="flex gap-2">
      {#if data.offset > 0}
        <a
          href={pageHref(data.offset - data.limit)}
          class="rounded-lg border border-border px-3 py-1.5 text-text hover:border-brand hover:text-brand"
        >
          {t("common.previous")}
        </a>
      {/if}
      {#if data.offset + data.limit < data.total}
        <a
          href={pageHref(data.offset + data.limit)}
          class="rounded-lg border border-border px-3 py-1.5 text-text hover:border-brand hover:text-brand"
        >
          {t("common.next")}
        </a>
      {/if}
    </div>
  </div>
{/if}
