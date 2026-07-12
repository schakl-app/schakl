<script lang="ts">
  /**
   * The Drive panel a project/task page composes through a typed `EntityPanelSpec` load:
   * linked files (task links roll up onto the project, #21) plus an on-demand browser to
   * link more.
   *
   * Where do people put the file for this project? One click from here (#150): a project
   * with its own folder browses there; a project whose *client* has a folder starts in that
   * folder, with "Projectmap aanmaken" (nests under the client) and "In klantmap werken"
   * (links the client folder to the project) one click away. The client folder is looked up
   * lazily, when the browser opens, and only when the project has no folder of its own — the
   * panel's SSR load stays one call (docs/PERFORMANCE.md).
   *
   * **Host contract:** `?/linkDriveFile`, `?/unlinkDriveFile`, `?/provisionDriveFolder`
   * (spread `driveActions`).
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import type { EntityPanelContext, EntityPanelLookups } from "$lib/core/registry";

  import DriveBrowser from "./DriveBrowser.svelte";
  import DriveLinkList, { type DriveLinkItem } from "./DriveLinkList.svelte";

  let {
    data,
    context,
    lookups,
  }: { data: unknown; context: EntityPanelContext; lookups: EntityPanelLookups } = $props();

  const panel = $derived(
    (data ?? { links: [], entityType: "project" }) as {
      links: DriveLinkItem[];
      entityType: string;
    },
  );
  const canWrite = $derived(can(page.data.user, "google.drive.write"));

  const ownFolder = $derived(panel.links.find((link) => link.is_folder) ?? null);
  // The host already holds the projects lookup — the project's client costs no fetch.
  const companyId = $derived(
    panel.entityType === "project"
      ? (lookups.projects.find((project) => project.id === context.entityId)?.company_id ?? null)
      : null,
  );

  let clientFolder = $state<DriveLinkItem | null>(null);
  let clientFolderLoaded = $state(false);

  async function loadClientFolder() {
    if (clientFolderLoaded || !companyId) return;
    clientFolderLoaded = true;
    try {
      const response = await fetch(
        `/api/v1/google/drive/links?entity_type=company&entity_id=${companyId}`,
        { headers: { accept: "application/json" } },
      );
      if (!response.ok) return;
      const links = (await response.json()) as DriveLinkItem[];
      clientFolder = links.find((link) => link.is_folder) ?? null;
    } catch {
      clientFolder = null;
    }
  }

  // The browser mounts on demand: no Google (or Redis) traffic for a panel nobody opened.
  // Connection state surfaces inside it — an unconnected viewer reads the reconnect hint there.
  let browsing = $state(false);

  async function startBrowsing() {
    if (!ownFolder) await loadClientFolder();
    browsing = true;
  }

  const rootFolderId = $derived(
    ownFolder?.drive_file_id ?? clientFolder?.drive_file_id ?? null,
  );
</script>

<DriveLinkList links={panel.links} {canWrite} />

{#if canWrite}
  {#if browsing}
    {#if !ownFolder && clientFolder && panel.entityType === "project"}
      <!-- Starting in the client's folder: make the two sensible next steps one click. -->
      <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <span class="text-text-muted">
          {t("google.drive.in_client_folder", { name: clientFolder.name })}
        </span>
        <form method="POST" action="?/provisionDriveFolder" use:enhance>
          <input type="hidden" name="entity_type" value="project" />
          <input type="hidden" name="entity_id" value={context.entityId} />
          <button
            class="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:border-brand"
          >
            {t("google.drive.create_project_folder")}
          </button>
        </form>
        <form method="POST" action="?/linkDriveFile" use:enhance>
          <input type="hidden" name="entity_type" value="project" />
          <input type="hidden" name="entity_id" value={context.entityId} />
          <input type="hidden" name="drive_file_id" value={clientFolder.drive_file_id} />
          <button
            class="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:border-brand"
          >
            {t("google.drive.work_in_client_folder")}
          </button>
        </form>
      </div>
    {/if}
    <div class="mt-3">
      {#key rootFolderId}
        <DriveBrowser
          {rootFolderId}
          entityType={panel.entityType}
          entityId={context.entityId}
          canWrite
        />
      {/key}
    </div>
  {:else}
    <button
      type="button"
      class="mt-2 text-sm font-medium text-brand hover:underline"
      onclick={() => void startBrowsing()}
    >
      {t("google.drive.browse_and_link")}
    </button>
  {/if}
{/if}
