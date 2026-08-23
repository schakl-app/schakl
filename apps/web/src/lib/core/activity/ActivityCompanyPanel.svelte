<script lang="ts">
  /**
   * The company hub's activity panel (issue #67). The company detail page composes API panel
   * *providers* (opaque dicts), so this narrows that dict to the shared `ActivityFeed`.
   */
  import ActivityFeed from "./ActivityFeed.svelte";

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  const items = $derived((data.items ?? []) as never[]);
  // The provider counts the whole trail (#407), so this panel says "10 van 137" rather than
  // "de 10 meest recente", which reads the same for a record with eleven changes.
  const total = $derived(data.total as number | undefined);
</script>

<ActivityFeed {items} {total} />
