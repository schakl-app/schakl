<script lang="ts">
  /**
   * The project detail page's activity panel. Registered as an `EntityPanelSpec`, so it loads
   * through the typed client inside the host's `Promise.all` and the host never imports it.
   */
  import ActivityFeed from "./ActivityFeed.svelte";
  import type { NotificationLike } from "./format";

  interface ActivityItem extends NotificationLike {
    id: string;
    actor_name: string | null;
    created_at: string;
  }

  let { data }: { data: unknown } = $props();

  const payload = $derived((data ?? { items: [], limit: 0 }) as {
    items: ActivityItem[];
    limit: number;
  });
</script>

<ActivityFeed items={payload.items} limit={payload.limit} />
