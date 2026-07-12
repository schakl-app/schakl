<script lang="ts">
  /**
   * The activity panel a project/contact page composes through a typed `EntityPanelSpec` load
   * (issue #67). Its `load` fetched `/api/v1/activity`; this narrows the result to `ActivityFeed`.
   */
  import type { EntityPanelContext, EntityPanelLookups } from "$lib/core/registry";

  import ActivityFeed from "./ActivityFeed.svelte";

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  let {
    data,
    context,
    lookups,
  }: { data: unknown; context: EntityPanelContext; lookups: EntityPanelLookups } = $props();

  const feed = $derived(data as { items: never[]; limit: number });
</script>

<ActivityFeed items={feed.items ?? []} limit={feed.limit ?? 10} />
