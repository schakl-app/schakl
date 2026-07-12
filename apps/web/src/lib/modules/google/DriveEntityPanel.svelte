<script lang="ts">
  /**
   * The Drive panel a project/task page composes through a typed `EntityPanelSpec` load:
   * linked files (task links roll up onto the project, #21) plus an on-demand browser to
   * link more, rooted at the org's client-folders parent.
   *
   * **Host contract:** `?/linkDriveFile`, `?/unlinkDriveFile` (spread `driveActions`).
   */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import type { EntityPanelContext, EntityPanelLookups } from "$lib/core/registry";

  import DriveBrowser from "./DriveBrowser.svelte";
  import DriveLinkList, { type DriveLinkItem } from "./DriveLinkList.svelte";

  let {
    data,
    context,
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    lookups,
  }: { data: unknown; context: EntityPanelContext; lookups: EntityPanelLookups } = $props();

  const panel = $derived(
    (data ?? { links: [], entityType: "project" }) as {
      links: DriveLinkItem[];
      entityType: string;
    },
  );
  const canWrite = $derived(can(page.data.user, "google.drive.write"));

  // The browser mounts on demand: no Google (or Redis) traffic for a panel nobody opened.
  // Connection state surfaces inside it — an unconnected viewer reads the reconnect hint there.
  let browsing = $state(false);
</script>

<DriveLinkList links={panel.links} {canWrite} />

{#if canWrite}
  {#if browsing}
    <div class="mt-3">
      <DriveBrowser entityType={panel.entityType} entityId={context.entityId} canWrite />
    </div>
  {:else}
    <button
      type="button"
      class="mt-2 text-sm font-medium text-brand hover:underline"
      onclick={() => (browsing = true)}
    >
      {t("google.drive.browse_and_link")}
    </button>
  {/if}
{/if}
