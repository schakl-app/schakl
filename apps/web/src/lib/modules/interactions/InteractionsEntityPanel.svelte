<script lang="ts">
  /**
   * The contactmomenten panel a project/contact/task page composes through a typed
   * `EntityPanelSpec` load. Its `load` fetched `/api/v1/interactions` filtered on the host
   * entity; this narrows the result to the shared panel body.
   */
  import type { EntityPanelContext, EntityPanelLookups } from "$lib/core/registry";

  import type { InteractionItem } from "./format";
  import InteractionsPanelBody from "./InteractionsPanelBody.svelte";

  let {
    data,
    context,
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    lookups,
  }: { data: unknown; context: EntityPanelContext; lookups: EntityPanelLookups } = $props();

  const panel = $derived(
    (data ?? { items: [], total: 0, entityField: "company_id" }) as {
      items: InteractionItem[];
      total: number;
      entityField: string;
    },
  );
</script>

<InteractionsPanelBody
  items={panel.items}
  total={panel.total}
  prefill={{ [panel.entityField]: context.entityId }}
/>
