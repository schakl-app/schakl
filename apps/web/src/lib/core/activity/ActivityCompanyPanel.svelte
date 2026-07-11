<script lang="ts">
  /**
   * The company hub's activity panel (issue #67). The company detail page composes API panel
   * *providers* (opaque dicts), so this narrows that dict to the shared `ActivityFeed`.
   */
  import ActivityFeed from "./ActivityFeed.svelte";

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  const items = $derived((data.items ?? []) as never[]);
  const limit = $derived((data.limit ?? 10) as number);
</script>

<ActivityFeed {items} {limit} />
