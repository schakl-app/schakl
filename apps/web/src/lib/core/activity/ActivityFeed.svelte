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
   * system.
   *
   * Collapse, expand and the truncation notice are `PanelRows`' now (#407) — this file and
   * `InteractionsPanelBody` had written the same `COLLAPSED = 3` out twice, and the sentence
   * about what is hidden existed in four shapes that meant one thing. What the feed *kept* is
   * the honest count: the API sends `total`, so "de 10 meest recente worden getoond" no longer
   * reads the same for a record with eleven changes and one with eleven hundred.
   */
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { activityText, type ActivityLike } from "./format";

  interface ActivityItem extends ActivityLike {
    id: string;
    actor_name: string | null;
    actor_deleted: boolean;
    /** Who was signed in *as* the actor at the time (#296); null on every ordinary line. */
    impersonator_name?: string | null;
    created_at: string;
  }

  let {
    items,
    total,
    hasMore = false,
  }: {
    items: ActivityItem[];
    /**
     * The whole trail's length. The company hub's API panel counts it; the typed entity load
     * cannot (its endpoint answers a bare list), so that one asks for one row more than it
     * keeps and passes `hasMore` instead (#407).
     */
    total?: number;
    hasMore?: boolean;
  } = $props();

  // A busy record's trail grows without bound: show the most recent few, expand the rest in
  // place. Items arrive newest-first, so the head is the newest.
  const COLLAPSED = 3;

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
  <PanelRows rows={items} collapsed={COLLAPSED} {total} {hasMore}>
    {#snippet children(shown)}
      <ol class="space-y-3">
        {#each shown as item (item.id)}
          <li class="flex gap-3 text-sm">
            <span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-border" aria-hidden="true"
            ></span>
            <span class="min-w-0 flex-1">
              <span class="text-text">
                <span class="font-medium">{actorLabel(item)}</span>
                <!-- Someone was signed in as them (#296). Named right beside the actor, because
                     "the client changed this" and "one of us changed this as the client" are
                     different facts and only the second one is true here. -->
                {#if item.impersonator_name}
                  <span
                    class="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-950 dark:text-amber-300"
                    title={t("activity.impersonated_title", { actor: item.impersonator_name })}
                  >
                    {t("activity.via_impersonator", { actor: item.impersonator_name })}
                  </span>
                {/if}
                {activityText(item)}
              </span>
              <span class="mt-0.5 block text-xs text-text-muted"
                >{fmtDateTime(item.created_at)}</span
              >
            </span>
          </li>
        {/each}
      </ol>
    {/snippet}
  </PanelRows>
{/if}
