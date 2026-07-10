<script lang="ts">
  /**
   * The client detail page's activity panel. The company hub composes API panel providers, which
   * hand back an opaque dict — so this narrows it and delegates the rendering.
   */
  import ActivityFeed from "./ActivityFeed.svelte";
  import type { NotificationLike } from "./format";

  interface ActivityItem extends NotificationLike {
    id: string;
    actor_name: string | null;
    created_at: string;
  }

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

  const items = $derived((data.items ?? []) as ActivityItem[]);
  const limit = $derived(typeof data.limit === "number" ? data.limit : items.length);
</script>

<ActivityFeed {items} {limit} />
