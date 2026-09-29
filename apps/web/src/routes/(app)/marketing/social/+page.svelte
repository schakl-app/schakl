<script lang="ts">
  /**
   * The planner: what is being written, what waits for a yes, what goes out when, and what
   * did not. The shared table, the shared bar, the shared pager (CLAUDE.md §9).
   *
   * **It opens on what is still going on**, and says so with a pill that shows itself
   * selected: a list that silently leaves the archive out looks exactly like one that has no
   * archive (#329). The two states that ask something of somebody — *wacht op goedkeuring*,
   * *mislukt* — print their count on the pill, because a number nobody has to go looking for
   * is the whole point of a queue.
   */
  import CalendarClock from "@lucide/svelte/icons/calendar-clock";
  import Plus from "@lucide/svelte/icons/plus";
  import Settings from "@lucide/svelte/icons/settings-2";
  import Share2 from "@lucide/svelte/icons/share-2";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";

  import { invalidate } from "$app/navigation";
  import { page } from "$app/state";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import { fmtDateTime, fmtRelativeTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pollWhile } from "$lib/core/poll.svelte";
  import { createTableLayout } from "$lib/core/table/layout.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ColumnPicker from "$lib/core/ui/ColumnPicker.svelte";
  import DataTable from "$lib/core/ui/DataTable.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import ChannelMark from "$lib/integrations/meta/ChannelMark.svelte";
  import { POST_COLUMNS, type PostFilterKey, STATUS_ALL } from "$lib/integrations/meta/columns";
  import { inFlight, statusLabel } from "$lib/integrations/meta/format";
  import NewPostDialog from "$lib/integrations/meta/NewPostDialog.svelte";
  import PostStatusPill from "$lib/integrations/meta/PostStatusPill.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";

  let { data, form } = $props();

  type Post = (typeof data.posts)[number];

  const posts = $derived(data.posts);
  const title = $derived(navLabel("social", t("nav.social")));
  const narrowed = $derived(
    Boolean(
      data.filters.q || data.filters.company_id || data.filters.channel || data.filters.status,
    ),
  );
  const hasChannels = $derived(data.channels.length > 0);

  // A worker is publishing one of these rows: re-read the list until it is not.
  pollWhile(
    () => posts.some((post) => inFlight(post.status)),
    () => invalidate("meta:posts"),
    5000,
  );

  // `?new=1` opens the dialog: how a client's own page hands over "a new post for this
  // client" without growing a second composer of its own.
  let creating = $state(page.url.searchParams.get("new") === "1");
  // A refused create keeps the dialog open over the refusal, rather than closing on it.
  $effect(() => {
    if (form?.createError) creating = true;
  });

  const count = (status: string) => data.counts.by_status[status] ?? 0;
  /** A pill's label, with its count where the count is a call to action. */
  const withCount = (status: string) =>
    count(status) > 0 ? `${statusLabel(status)} (${count(status)})` : statusLabel(status);

  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.filters.company_id }),
  );
  const filterDefs: FilterDef<PostFilterKey>[] = $derived([
    { kind: "search", key: "q", placeholder: t("meta.list.search_placeholder") },
    {
      kind: "select",
      key: "company",
      placeholder: t("meta.list.all_clients"),
      options: companyPicker.live,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    {
      kind: "select",
      key: "channel",
      placeholder: t("meta.list.all_channels"),
      options: [
        { value: "facebook", label: "Facebook" },
        { value: "instagram", label: "Instagram" },
      ],
    },
    {
      kind: "pills",
      key: "status",
      options: [
        // Absent is the working set, and it is a pill of its own so the narrowing shows.
        { value: "", label: t("meta.filter.working") },
        { value: "review", label: withCount("review") },
        { value: "scheduled", label: statusLabel("scheduled") },
        { value: "failed,partial", label: withCount("failed") },
        { value: "published", label: statusLabel("published") },
        { value: STATUS_ALL, label: t("meta.filter.all") },
      ],
    },
  ]);

  const table = createTableLayout<Post>({
    all: () => POST_COLUMNS,
    pref: () => data.table.pref,
    sort: () => data.table.sort,
    cells: () => ({
      post: postCell,
      company: companyCell,
      channels: channelsCell,
      when: whenCell,
      status: statusCell,
      author: authorCell,
      approver: approverCell,
      created: createdCell,
    }),
  });

  const when = (post: Post) => post.published_at ?? post.scheduled_at;
  const thumb = (post: Post) => {
    const first = post.media.find((item) => item.kind === "image" && item.file_id);
    return first ? `/api/v1/files/${first.file_id}/thumbnail?size=160` : null;
  };
</script>

<svelte:head>
  <title>{pageTitle(title)}</title>
</svelte:head>

<PageHeader {title}>
  {#snippet subtitle()}{t("meta.list.subtitle")}{/snippet}
  {#snippet actions()}
    <a
      href="/marketing/social/channels"
      class="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text hover:border-text-muted"
      data-sveltekit-preload-data="hover"
    >
      <Settings size={15} aria-hidden="true" />
      {t("meta.channels.title")}
    </a>
    {#if data.canWrite && hasChannels}
      <Button type="button" onclick={() => (creating = true)}>
        <Plus size={15} aria-hidden="true" />
        {t("meta.list.new")}
      </Button>
    {/if}
  {/snippet}
</PageHeader>

{#if data.loadError}
  <p
    class="mb-4 flex items-start gap-2 rounded-lg border border-border bg-surface-raised p-3 text-sm text-text"
    role="alert"
  >
    <TriangleAlert size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
    {t(data.loadError)}
  </p>
{/if}

{#if !data.status.writes_enabled}
  <!-- The kill switch is on. Said here, where the work is, because a post that stays
       "ingepland" past its time with nothing on the screen explaining why reads as a fault. -->
  <p class="mb-4 rounded-lg bg-surface-tint px-4 py-3 text-sm text-text">
    {t("meta.list.writes_disabled")}
    {#if data.canManage}
      <a href="/settings/meta" class="text-brand hover:underline">{t("meta.list.open_settings")}</a>
    {/if}
  </p>
{:else if data.status.needs_attention && data.canManage}
  <p
    class="mb-4 flex items-start gap-2 rounded-lg border border-border bg-surface-raised px-4 py-3 text-sm text-text"
  >
    <TriangleAlert size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
    <span>
      {t("meta.list.token_attention")}
      <a href="/settings/meta" class="text-brand hover:underline">{t("meta.list.open_settings")}</a>
    </span>
  </p>
{/if}

{#if hasChannels || data.total > 0 || narrowed}
  <FilterBar filters={filterDefs} idPrefix="post-filter">
    {#snippet actions()}
      <ColumnPicker
        all={table.pickerColumns}
        visible={table.visibleKeys}
        sort={table.sort}
        onchange={table.onColumnsChange}
        onsort={table.onSort}
      />
    {/snippet}
  </FilterBar>
{/if}

{#snippet postCell(post: Post)}
  <a
    href={`/marketing/social/${post.id}`}
    class="flex min-w-0 items-center gap-2.5"
    data-sveltekit-preload-data="hover"
  >
    {#if thumb(post)}
      <img
        src={thumb(post)}
        alt=""
        class="h-9 w-9 shrink-0 rounded-md border border-border object-cover"
        loading="lazy"
      />
    {/if}
    <span class="min-w-0">
      <span
        class="block truncate font-medium hover:text-brand {post.title
          ? 'text-text'
          : 'italic text-text-muted'}"
      >
        {post.title || t("meta.list.untitled")}
      </span>
      {#if post.format === "reel" || post.media.length > 1}
        <span class="block truncate text-xs text-text-muted">
          {post.format === "reel"
            ? t("meta.format.reel")
            : t("meta.list.images", { count: String(post.media.length) })}
        </span>
      {/if}
    </span>
  </a>
{/snippet}

{#snippet companyCell(post: Post)}
  {#if post.company_id && post.company_name}
    <a
      href={`/companies/${post.company_id}`}
      class="block truncate text-text-muted hover:text-brand">{post.company_name}</a
    >
  {:else}
    <span class="block truncate text-text-muted">{t("meta.own_channels")}</span>
  {/if}
{/snippet}

{#snippet channelsCell(post: Post)}
  <span class="flex items-center gap-2 truncate text-text-muted">
    {#each post.targets as target (target.id)}
      <span
        class="inline-flex items-center gap-1 {target.status === 'failed'
          ? 'text-red-600 dark:text-red-400'
          : ''}"
        title={`${target.asset_name}: ${t(`meta.target.${target.status}`)}`}
      >
        <ChannelMark channel={target.channel} />
        {#if target.status === "failed"}<TriangleAlert size={12} aria-hidden="true" />{/if}
      </span>
    {/each}
  </span>
{/snippet}

{#snippet whenCell(post: Post)}
  {@const at = when(post)}
  {#if at}
    <span class="block truncate tabular-nums text-text-muted" title={fmtRelativeTime(at)}>
      {fmtDateTime(at)}
    </span>
  {:else}
    <span class="text-text-muted">{t("meta.list.no_time")}</span>
  {/if}
{/snippet}

{#snippet statusCell(post: Post)}
  <PostStatusPill status={post.status} />
{/snippet}

{#snippet authorCell(post: Post)}
  <span class="block truncate text-text-muted">{post.created_by_name || "—"}</span>
{/snippet}

{#snippet approverCell(post: Post)}
  <span class="block truncate text-text-muted">{post.approved_by_name || "—"}</span>
{/snippet}

{#snippet createdCell(post: Post)}
  <span class="whitespace-nowrap tabular-nums text-text-muted">{fmtDateTime(post.created_at)}</span>
{/snippet}

{#snippet mobileRow(post: Post)}
  {@const at = when(post)}
  <a href={`/marketing/social/${post.id}`} class="min-w-0 flex-1">
    <span
      class="block truncate text-sm font-medium {post.title
        ? 'text-text'
        : 'italic text-text-muted'}">{post.title || t("meta.list.untitled")}</span
    >
    <span class="mt-0.5 flex items-center gap-1.5 truncate text-xs text-text-muted">
      {#each post.targets as target (target.id)}<ChannelMark
          channel={target.channel}
          size={12}
        />{/each}
      {[post.company_name ?? t("meta.own_channels"), at ? fmtDateTime(at) : ""]
        .filter(Boolean)
        .join(" · ")}
    </span>
  </a>
  <PostStatusPill status={post.status} />
{/snippet}

{#snippet emptyState()}
  <div class="px-6 py-12 text-center">
    {#if narrowed && data.filters.status !== STATUS_ALL}
      <CalendarClock size={28} class="mx-auto mb-3 text-text-muted" aria-hidden="true" />
      <p class="text-sm text-text-muted">{t("common.no_results")}</p>
    {:else}
      <Share2 size={28} class="mx-auto mb-3 text-text-muted" aria-hidden="true" />
      <p class="text-sm text-text">{t("meta.list.empty")}</p>
      {#if data.canWrite}
        <p class="mx-auto mt-2 max-w-md text-sm text-text-muted">{t("meta.list.empty_hint")}</p>
        <div class="mt-4">
          <Button type="button" onclick={() => (creating = true)}>
            <Plus size={15} aria-hidden="true" />
            {t("meta.list.new")}
          </Button>
        </div>
      {/if}
    {/if}
  </div>
{/snippet}

{#if !hasChannels && data.total === 0 && !narrowed}
  <!-- Nothing can be planned yet, and the screen says which of the two reasons it is: nothing
       was ever connected (the settings screen fixes that, for whoever may open it), or
       channels were found and nobody has said whose they are (one screen away, for the same
       person). A planner that opens empty with a "new" button that cannot work is the broken
       control #253 names. -->
  <section
    class="rounded-xl border border-dashed border-border px-6 py-12 text-center"
    aria-labelledby="social-setup"
  >
    <Share2 size={28} class="mx-auto mb-3 text-text-muted" aria-hidden="true" />
    <h2 id="social-setup" class="text-sm font-medium text-text">
      {data.status.channels_unlinked > 0
        ? t("meta.setup.link_title")
        : t("meta.setup.connect_title")}
    </h2>
    <p class="mx-auto mt-2 max-w-lg text-sm text-text-muted">
      {#if data.status.channels_unlinked > 0}
        {t("meta.setup.link_hint", { count: String(data.status.channels_unlinked) })}
      {:else if data.canManage}
        {t("meta.setup.connect_hint")}
      {:else}
        {t("meta.setup.ask_admin")}
      {/if}
    </p>
    {#if data.canManage}
      <a
        href={data.status.channels_unlinked > 0 ? "/marketing/social/channels" : "/settings/meta"}
        class="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {data.status.channels_unlinked > 0
          ? t("meta.setup.link_action")
          : t("meta.setup.connect_action")}
      </a>
    {/if}
  </section>
{:else}
  <DataTable
    rows={posts}
    columns={table.columns}
    sort={table.sort}
    widths={table.widths}
    locale={data.locale}
    {mobileRow}
    empty={emptyState}
    onsort={table.onSort}
    onresize={table.onResize}
  />

  <Pagination
    total={data.total}
    page={data.paging.page}
    limit={data.paging.limit}
    onsize={table.onPageSize}
  />
{/if}

<NewPostDialog
  bind:open={creating}
  channels={data.channels}
  companyId={data.filters.company_id || page.url.searchParams.get("company") || ""}
  day={page.url.searchParams.get("day") ?? ""}
  error={form?.createError ?? null}
/>
