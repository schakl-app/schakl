<script lang="ts">
  /**
   * The company hub's contactmomenten panel. The company page composes API panel *providers*
   * (opaque dicts), so this narrows that dict to the shared panel body.
   */
  import type { PanelMember } from "$lib/core/registry";

  import type { InteractionItem } from "./format";
  import InteractionsPanelBody from "./InteractionsPanelBody.svelte";

  let {
    companyId,
    data,
    members = [],
  }: { companyId: string; data: Record<string, unknown>; members?: PanelMember[] } = $props();

  const items = $derived((data.items ?? []) as InteractionItem[]);
  const total = $derived((data.total ?? 0) as number);
</script>

<InteractionsPanelBody {items} {total} prefill={{ company_id: companyId }} {members} />
