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

  // A task walks up to its project (the host page hands the current task down in `lookups.tasks`);
  // both a project and a task then reach their client through `lookups.projects` — no fetch (#150).
  const projectId = $derived(
    panel.entityType === "task"
      ? (lookups.tasks.find((task) => task.id === context.entityId)?.project_id ?? null)
      : null,
  );
  const companyId = $derived(
    panel.entityType === "project"
      ? (lookups.projects.find((project) => project.id === context.entityId)?.company_id ?? null)
      : projectId
        ? (lookups.projects.find((project) => project.id === projectId)?.company_id ?? null)
        : null,
  );

  // Where the browser should start when this entity has no folder of its own: for a task, its
  // project's folder if provisioned (the work lives there), else the client's; for a project, the
  // client's. Without this a task opened at the shared-drive **root** instead of the client folder.
  let parentFolder = $state<DriveLinkItem | null>(null);
  let parentFolderKind = $state<"project" | "client" | null>(null);
  let parentLoaded = $state(false);

  async function fetchFolder(entityType: string, entityId: string): Promise<DriveLinkItem | null> {
    try {
      const response = await fetch(
        `/api/v1/google/drive/links?entity_type=${entityType}&entity_id=${entityId}`,
        { headers: { accept: "application/json" } },
      );
      if (!response.ok) return null;
      const links = (await response.json()) as DriveLinkItem[];
      return links.find((link) => link.is_folder) ?? null;
    } catch {
      return null;
    }
  }

  async function loadParentFolder() {
    if (parentLoaded) return;
    parentLoaded = true;
    if (panel.entityType === "task" && projectId) {
      const folder = await fetchFolder("project", projectId);
      if (folder) {
        parentFolder = folder;
        parentFolderKind = "project";
        return;
      }
    }
    if (companyId) {
      const folder = await fetchFolder("company", companyId);
      if (folder) {
        parentFolder = folder;
        parentFolderKind = "client";
      }
    }
  }

  // The browser mounts on demand: no Google (or Redis) traffic for a panel nobody opened.
  // Connection state surfaces inside it — an unconnected viewer reads the reconnect hint there.
  let browsing = $state(false);

  async function startBrowsing() {
    if (!ownFolder) await loadParentFolder();
    browsing = true;
  }

  const rootFolderId = $derived(ownFolder?.drive_file_id ?? parentFolder?.drive_file_id ?? null);
  // The client-folder actions (create a project folder / work in the client folder) only make
  // sense on a project sitting in its client's folder. A task just gets told where it landed.
  const showClientActions = $derived(
    !ownFolder && parentFolderKind === "client" && panel.entityType === "project",
  );
</script>

<DriveLinkList links={panel.links} {canWrite} />

{#if canWrite}
  {#if browsing}
    {#if showClientActions && parentFolder}
      <!-- A project starting in the client's folder: make the two sensible next steps one click. -->
      <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <span class="text-text-muted">
          {t("google.drive.in_client_folder", { name: parentFolder.name })}
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
          <input type="hidden" name="drive_file_id" value={parentFolder.drive_file_id} />
          <button
            class="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:border-brand"
          >
            {t("google.drive.work_in_client_folder")}
          </button>
        </form>
      </div>
    {:else if !ownFolder && parentFolder && panel.entityType === "task"}
      <!-- A task with no folder of its own: say where it landed — the project's folder, else the
           client's — so it's clear the browser isn't at the shared-drive root (#150). -->
      <p class="mt-3 text-sm text-text-muted">
        {parentFolderKind === "project"
          ? t("google.drive.in_project_folder", { name: parentFolder.name })
          : t("google.drive.in_client_folder", { name: parentFolder.name })}
      </p>
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
