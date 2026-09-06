<script lang="ts">
  /**
   * The OneDrive panel a project/task page composes through a typed `EntityPanelSpec` load:
   * linked files (task links roll up onto the project) plus an on-demand browser to link more.
   *
   * Where do people put the file for this project? One click from here (#150's rule): a
   * project with its own folder browses there; a project whose *client* has a folder starts in
   * that folder, with "Projectmap aanmaken" (nests under the client) and "In klantmap werken"
   * (links the client folder to the project) one click away. The client folder is looked up
   * lazily, when the browser opens, and only when the project has no folder of its own — the
   * panel's SSR load stays one fan of two cheap DB reads (docs/PERFORMANCE.md).
   *
   * A **task** gets the same two controls (#328). Auto-provisioning stays off for tasks —
   * numerous and short-lived — so this button is the only way one appears, exactly as it is for
   * a project.
   *
   * **Host contract:** `?/linkOneDriveFile`, `?/unlinkOneDriveFile`, `?/deleteOneDriveFile`,
   * `?/provisionOneDriveFolder`, `?/setOneDriveFolder` (spread `oneDriveActions`).
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import type { EntityPanelContext, EntityPanelLookups } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  import OneDriveBrowser from "./OneDriveBrowser.svelte";
  import OneDriveLinkList, { type OneDriveLinkItem } from "./OneDriveLinkList.svelte";
  import OneDriveProvisionStatus from "./OneDriveProvisionStatus.svelte";

  let {
    data,
    context,
    lookups,
  }: { data: unknown; context: EntityPanelContext; lookups: EntityPanelLookups } = $props();

  const busy = new InFlight();

  const panel = $derived(
    (data ?? { links: [], entityType: "project" }) as {
      links: OneDriveLinkItem[];
      entityType: string;
      state?: {
        enabled: boolean;
        viewer_connected: boolean;
        can_provision: boolean;
      } | null;
    },
  );
  // Provisioning readiness, from the server (#444): the button used to be drawn off the
  // caller's permission alone, so with no automation account or root it was a control that
  // could only 409 (#253). `null` (an old payload, a failed fan) draws nothing extra.
  const oneDriveState = $derived(panel.state ?? null);
  const canWrite = $derived(can(page.data.user, "microsoft.onedrive.write"));

  // This record's *own* folder. Both halves matter: `is_root` because a subfolder linked as an
  // attachment is not the record's folder, and the entity match because a project's list rolls
  // up its tasks' links — a task's folder is not the project's.
  const ownFolder = $derived(
    panel.links.find((link) => link.is_root && link.entity_id === context.entityId) ?? null,
  );

  // Both parents of this record, off the lookups the host page already holds — no fetch.
  const currentTask = $derived(
    panel.entityType === "task"
      ? (lookups.tasks.find((task) => task.id === context.entityId) ?? null)
      : null,
  );
  const projectId = $derived(currentTask?.project_id ?? null);
  // A task's client is its **own** `company_id` first (#363); the project walk stays as the
  // fallback, for a host that hands a task down without its client.
  const companyId = $derived(
    panel.entityType === "project"
      ? (lookups.projects.find((project) => project.id === context.entityId)?.company_id ?? null)
      : (currentTask?.company_id ??
          (projectId
            ? (lookups.projects.find((project) => project.id === projectId)?.company_id ?? null)
            : null)),
  );

  // Where the browser should start when this entity has no folder of its own: for a task, its
  // project's folder if provisioned (the work lives there), else the client's; for a project,
  // the client's. Without this a task opens at the library root instead of the client folder.
  let parentFolder = $state<OneDriveLinkItem | null>(null);
  let parentFolderKind = $state<"project" | "client" | null>(null);
  let parentLoaded = $state(false);

  async function fetchFolder(
    entityType: string,
    entityId: string,
  ): Promise<OneDriveLinkItem | null> {
    try {
      const response = await fetch(
        `/api/v1/microsoft/onedrive/links?entity_type=${entityType}&entity_id=${entityId}`,
        { headers: { accept: "application/json" } },
      );
      if (!response.ok) return null;
      const links = (await response.json()) as OneDriveLinkItem[];
      return links.find((link) => link.is_root) ?? null;
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

  // The browser mounts on demand: no Graph (or Redis) traffic for a panel nobody opened.
  // Connection state surfaces inside it — an unconnected viewer reads the reconnect hint there.
  let browsing = $state(false);
  // Bumped when the list above bins a file: the browser's listing is live and no page
  // invalidation reaches it, so it would keep showing a file that has left OneDrive.
  let driveVersion = $state(0);

  async function startBrowsing() {
    if (!ownFolder) await loadParentFolder();
    browsing = true;
  }

  const rootDriveId = $derived(ownFolder?.drive_id ?? parentFolder?.drive_id ?? null);
  const rootFolderId = $derived(ownFolder?.item_id ?? parentFolder?.item_id ?? null);
  // A record with no folder of its own, browsing somebody else's: say whose, and offer the two
  // ways out. Both halves are the same for a project sitting in its client's folder and a task
  // sitting in its project's — only the wording differs, so only the wording branches.
  const showParentActions = $derived(!ownFolder && parentFolder !== null);
  const createFolderLabel = $derived(
    panel.entityType === "task"
      ? t("microsoft.onedrive.create_task_folder")
      : t("microsoft.onedrive.create_project_folder"),
  );
  const noFolderLabel = $derived(
    panel.entityType === "task"
      ? t("microsoft.onedrive.no_task_folder")
      : t("microsoft.onedrive.no_project_folder"),
  );
  const adoptFolderLabel = $derived(
    parentFolderKind === "project"
      ? t("microsoft.onedrive.work_in_project_folder")
      : t("microsoft.onedrive.work_in_client_folder"),
  );
  const parentFolderLabel = $derived(
    parentFolderKind === "project"
      ? t("microsoft.onedrive.in_project_folder", { name: parentFolder?.name ?? "" })
      : t("microsoft.onedrive.in_client_folder", { name: parentFolder?.name ?? "" }),
  );

  // A OneDrive action's refusal renders *here*, beside the button that fired it — not as the
  // host page's `form.error`, two thousand lines below the fold (#444).
  const oneDriveError = $derived((page.form?.oneDriveError ?? null) as string | null);
</script>

<!-- Two things live on this card and nothing said which was which: the files coupled to this
     record, and a browser over the whole folder. Each gets its own heading, and the browser its
     own rule, so the boundary is visible. -->
<h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-text-muted">
  {t("microsoft.onedrive.linked_files")}
</h3>
<OneDriveLinkList links={panel.links} {canWrite} ontrashed={() => (driveVersion += 1)} />

{#if oneDriveError}
  <p class="mt-2 text-sm text-red-600 dark:text-red-400" role="alert">{t(oneDriveError)}</p>
{/if}

<!-- The record's own folder, or the way to one (#444). The create button stands whenever the
     server says the org can provision — a task whose project and client have no folder
     included, since the worker walks the chain — and its absence is a sentence naming what is
     missing, never a blank. -->
{#if canWrite && !ownFolder && oneDriveState?.enabled}
  <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
    <p class="text-sm text-text-muted">{noFolderLabel}</p>
    {#if oneDriveState.can_provision}
      <form method="POST" action="?/provisionOneDriveFolder" use:enhance={busy.wrap("provision")}>
        <input type="hidden" name="entity_type" value={panel.entityType} />
        <input type="hidden" name="entity_id" value={context.entityId} />
        <Button variant="secondary" size="xs" loading={busy.is("provision")} disabled={busy.active}>
          {createFolderLabel}
        </Button>
      </form>
    {/if}
  </div>
  {#if !oneDriveState.can_provision}
    <p class="mt-1 text-xs text-text-muted">{t("microsoft.onedrive.not_provisionable")}</p>
  {/if}
{/if}

<OneDriveProvisionStatus entityType={panel.entityType} entityId={context.entityId} />

{#if canWrite}
  {#if browsing}
    <div class="mt-4 border-t border-border pt-3">
      <h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("microsoft.onedrive.browser_title")}
      </h3>
    </div>
    {#if showParentActions && parentFolder}
      <!-- Say where the browser landed — so it's clear it isn't at the library root. What
           belongs to the *browsed* folder is adopting it, the same act the picker performs
           (`?/setOneDriveFolder`). -->
      <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <span class="text-text-muted">{parentFolderLabel}</span>
        <form method="POST" action="?/setOneDriveFolder" use:enhance={busy.wrap("link")}>
          <input type="hidden" name="entity_type" value={panel.entityType} />
          <input type="hidden" name="entity_id" value={context.entityId} />
          <input type="hidden" name="drive_id" value={parentFolder.drive_id} />
          <input type="hidden" name="item_id" value={parentFolder.item_id} />
          <Button variant="secondary" size="xs" loading={busy.is("link")} disabled={busy.active}>
            {adoptFolderLabel}
          </Button>
        </form>
      </div>
    {/if}
    <div class="mt-3">
      {#key `${rootDriveId}:${rootFolderId}`}
        <OneDriveBrowser
          {rootDriveId}
          {rootFolderId}
          entityType={panel.entityType}
          entityId={context.entityId}
          canWrite
          reloadToken={driveVersion}
        />
      {/key}
    </div>
  {:else}
    <button
      type="button"
      class="mt-2 text-sm font-medium text-brand hover:underline"
      onclick={() => void startBrowsing()}
    >
      {t("microsoft.onedrive.browse_and_link")}
    </button>
  {/if}
{/if}
