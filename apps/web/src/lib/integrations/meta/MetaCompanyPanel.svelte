<script lang="ts">
  /**
   * Social, on a client's page: the channels we manage for them and what is going out next.
   *
   * Everything drawn came down with the page in the panel provider's payload — no fetch of
   * its own (docs/PERFORMANCE.md). The one control is "new post", which opens the planner's
   * own dialog with this client already chosen: the page where a post is written is the
   * planner's, and a second composer here would be a second set of rules.
   */
  import Plus from "@lucide/svelte/icons/plus";

  import { page } from "$app/state";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import PanelRow from "$lib/core/ui/PanelRow.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import ChannelMark from "./ChannelMark.svelte";
  import { channelLabel, statusLabel, statusState } from "./format";

  interface PanelChannel {
    id: string;
    kind: string;
    name: string;
    username: string | null;
  }
  interface PanelPost {
    id: string;
    title: string;
    status: string;
    format: string;
    scheduled_at: string | null;
    published_at: string | null;
    channels: string[];
  }

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  const channels = $derived((data.channels ?? []) as PanelChannel[]);
  const items = $derived((data.items ?? []) as PanelPost[]);
  const hasMore = $derived(Boolean(data.has_more));
  const mayWrite = $derived(can(page.data.user, "meta.post.write"));
  const mayManage = $derived(can(page.data.user, "meta.settings.manage"));

  const when = (post: PanelPost) => post.published_at ?? post.scheduled_at;
  const meta = (post: PanelPost) =>
    [
      when(post) ? fmtDateTime(when(post)!) : t("meta.list.no_time"),
      post.channels.map(channelLabel).join(" + "),
    ]
      .filter(Boolean)
      .join(" · ");
</script>

<div class="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
  {#each channels as channel (channel.id)}
    <span class="inline-flex min-w-0 items-center gap-1.5 text-sm text-text">
      <ChannelMark
        channel={channel.kind === "instagram" ? "instagram" : "facebook"}
        size={14}
        class="text-text-muted"
      />
      <span class="truncate">{channel.username ? `@${channel.username}` : channel.name}</span>
    </span>
  {:else}
    <span class="text-sm text-text-muted">{t("meta.panel.no_channels")}</span>
    {#if mayManage}
      <a href="/marketing/social/channels" class="text-sm text-brand hover:underline"
        >{t("meta.setup.link_action")}</a
      >
    {/if}
  {/each}
  {#if channels.length > 0 && mayWrite}
    <a
      href={`/marketing/social?company=${companyId}&new=1`}
      class="ml-auto inline-flex items-center gap-1 text-sm text-brand hover:underline"
    >
      <Plus size={14} aria-hidden="true" />
      {t("meta.list.new")}
    </a>
  {/if}
</div>

{#if items.length === 0}
  {#if channels.length > 0}
    <p class="text-sm text-text-muted">{t("meta.panel.no_posts")}</p>
  {/if}
{:else}
  <PanelRows
    rows={items}
    {hasMore}
    href={`/marketing/social?company=${companyId}&status=all`}
    linkLabel={t("meta.panel.view_all")}
  >
    {#snippet children(rows)}
      <ul class="divide-y divide-border">
        {#each rows as post (post.id)}
          <PanelRow
            href={`/marketing/social/${post.id}`}
            title={post.title || t("meta.list.untitled")}
            meta={meta(post)}
            chip={statusLabel(post.status)}
            chipState={statusState(post.status)}
          />
        {/each}
      </ul>
    {/snippet}
  </PanelRows>
{/if}
