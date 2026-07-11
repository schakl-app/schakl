<script lang="ts">
  /**
   * A record's paper trail, rendered (issue #67).
   *
   * The presentational half shared by every activity panel — the company's (an API panel
   * provider hands it an opaque dict) and the project's/contact's (a typed `EntityPanelSpec`
   * load). Only the plumbing differs, so only the plumbing is duplicated.
   *
   * The actor is named from the snapshot the API resolved (issue #64): a live account shows its
   * current name, a departed one reads "Naam (verwijderd)", and a genuinely absent actor is the
   * system. If the feed truncates, it says so — silent truncation reads as "that's all of them"
   * (docs/PERFORMANCE.md).
   */
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { activityText, type ActivityLike } from "./format";

  interface ActivityItem extends ActivityLike {
    id: string;
    actor_name: string | null;
    actor_deleted: boolean;
    created_at: string;
  }

  let { items, limit }: { items: ActivityItem[]; limit: number } = $props();

  // A busy record's trail grows without bound: show the most recent few, expand the rest in
  // place. Items arrive newest-first, so the head is the newest.
  const COLLAPSED = 3;
  let expanded = $state(false);
  const collapsible = $derived(items.length > COLLAPSED);
  const shown = $derived(collapsible && !expanded ? items.slice(0, COLLAPSED) : items);

  function actorLabel(item: ActivityItem): string {
    if (!item.actor_name) return t("activity.system");
    return item.actor_deleted
      ? t("common.deleted_user", { name: item.actor_name })
      : item.actor_name;
  }
</script>

{#if items.length === 0}
  <p class="text-sm text-text-muted">{t("activity.empty")}</p>
{:else}
  <ol class="space-y-3">
    {#each shown as item (item.id)}
      <li class="flex gap-3 text-sm">
        <span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-border" aria-hidden="true"></span>
        <span class="min-w-0 flex-1">
          <span class="text-text">
            <span class="font-medium">{actorLabel(item)}</span>
            {activityText(item)}
          </span>
          <span class="mt-0.5 block text-xs text-text-muted">{fmtDateTime(item.created_at)}</span>
        </span>
      </li>
    {/each}
  </ol>
  {#if collapsible}
    <button
      type="button"
      class="mt-3 text-xs font-medium text-brand hover:underline"
      onclick={() => (expanded = !expanded)}
    >
      {expanded ? t("common.show_less") : t("common.show_all", { count: items.length })}
    </button>
  {/if}
  <!-- Only claim "these are all of them" once they are all on screen. -->
  {#if (!collapsible || expanded) && items.length >= limit}
    <p class="mt-3 text-xs text-text-muted">{t("activity.truncated", { limit })}</p>
  {/if}
{/if}
