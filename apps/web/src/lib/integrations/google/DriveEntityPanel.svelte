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
   * panel's SSR load stays one fan of two cheap DB reads (docs/PERFORMANCE.md).
   *
   * A **task** gets the same two controls (#328). It used to get only a sentence saying where
   * its uploads had landed, which was the whole bug: with no route to a folder of its own, a
   * task's files sat in the project's folder among everything that project ever produced.
   * Auto-provisioning stays off for tasks — numerous and short-lived — so this button is the
   * only way one appears, exactly as it is for a project.
   *
   * **Host contract:** `?/linkDriveFile`, `?/unlinkDriveFile`, `?/deleteDriveFile`,
   * `?/provisionDriveFolder` (spread `driveActions`).
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import type { EntityPanelContext, EntityPanelLookups } from "$lib/core/registry";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  import DriveBrowser from "./DriveBrowser.svelte";
  import DriveLinkList, { type DriveLinkItem } from "./DriveLinkList.svelte";
  import DriveProvisionStatus from "./DriveProvisionStatus.svelte";

  let {
    data,
    context,
    lookups,
  }: { data: unknown; context: EntityPanelContext; lookups: EntityPanelLookups } = $props();

  const busy = new InFlight();

  const panel = $derived(
    (data ?? { links: [], entityType: "project" }) as {
      links: DriveLinkItem[];
      entityType: string;
      state?: {
        enabled: boolean;
        viewer_connected: boolean;
        can_provision: boolean;
      } | null;
    },
  );
  // Provisioning readiness, from the server (#444): the button used to be drawn off the
  // caller's permission alone, so with no automation account or Drive root it was a control
  // that could only 409 (#253). `null` (an old payload, a failed fan) draws nothing extra.
  const driveState = $derived(panel.state ?? null);
  const canWrite = $derived(can(page.data.user, "google.drive.write"));

  // This record's *own* folder. Both halves matter: `is_root` because a subfolder linked as an
  // attachment is not the record's folder, and the entity match because a project's list rolls
  // up its tasks' links (#21) — a task's folder is not the project's.
  const ownFolder = $derived(
    panel.links.find((link) => link.is_root && link.entity_id === context.entityId) ?? null,
  );

  // Both parents of this record, off the lookups the host page already holds — no fetch (#150).
  const currentTask = $derived(
    panel.entityType === "task"
      ? (lookups.tasks.find((task) => task.id === context.entityId) ?? null)
      : null,
  );
  const projectId = $derived(currentTask?.project_id ?? null);
  // A task's client is its **own** `company_id` first. Walking task → project → client was the
  // only route there, and a task attached straight to a client has no project to walk: `companyId`
  // came back null, `rootFolderId` with it, and the browser opened at the shared-drive root while
  // the client's connected folder sat one lookup away (#363). The project walk stays as the
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

  // The browser mounts on demand: no Google (or Redis) traffic for a panel nobody opened.
  // Connection state surfaces inside it — an unconnected viewer reads the reconnect hint there.
  let browsing = $state(false);
  // Bumped when the list above bins a file: the browser's listing is live and no page
  // invalidation reaches it, so it would keep showing a file that has left Drive.
  let driveVersion = $state(0);

  async function startBrowsing() {
    if (!ownFolder) await loadParentFolder();
    browsing = true;
  }

  const rootFolderId = $derived(ownFolder?.drive_file_id ?? parentFolder?.drive_file_id ?? null);
  // A record with no folder of its own, browsing somebody else's: say whose, and offer the two
  // ways out. Both halves are the same for a project sitting in its client's folder and a task
  // sitting in its project's — only the wording differs, so only the wording branches.
  const showParentActions = $derived(!ownFolder && parentFolder !== null);
  const createFolderLabel = $derived(
    panel.entityType === "task"
      ? t("google.drive.create_task_folder")
      : t("google.drive.create_project_folder"),
  );
  const noFolderLabel = $derived(
    panel.entityType === "task"
      ? t("google.drive.no_task_folder")
      : t("google.drive.no_project_folder"),
  );
  const adoptFolderLabel = $derived(
    parentFolderKind === "project"
      ? t("google.drive.work_in_project_folder")
      : t("google.drive.work_in_client_folder"),
  );
  const parentFolderLabel = $derived(
    parentFolderKind === "project"
      ? t("google.drive.in_project_folder", { name: parentFolder?.name ?? "" })
      : t("google.drive.in_client_folder", { name: parentFolder?.name ?? "" }),
  );

  // A drive action's refusal renders *here*, beside the button that fired it — not as the
  // host page's `form.error`, two thousand lines below the fold (#444).
  const driveError = $derived((page.form?.driveError ?? null) as string | null);
</script>

<!-- Two things live on this card and nothing said which was which: the files coupled to this
     record, and a browser over the whole Drive folder. Each gets its own heading, and the
     browser its own rule, so the boundary is visible (the company panel already named its list
     with the same key). -->
<h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-text-muted">
  {t("google.drive.linked_files")}
</h3>
<DriveLinkList links={panel.links} {canWrite} ontrashed={() => (driveVersion += 1)} />

{#if driveError}
  <p class="mt-2 text-sm text-red-600 dark:text-red-400" role="alert">{t(driveError)}</p>
{/if}

<!-- The record's own folder, or the way to one (#444). The create button no longer hides
     behind an opened browser and a found parent: it stands whenever the server says the org
     can provision — a task whose project and client have no folder included, since the worker
     walks the chain — and its absence is a sentence naming what is missing, never a blank. -->
{#if canWrite && !ownFolder && driveState?.enabled}
  <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
    <p class="text-sm text-text-muted">{noFolderLabel}</p>
    {#if driveState.can_provision}
      <form method="POST" action="?/provisionDriveFolder" use:enhance={busy.wrap("provision")}>
        <input type="hidden" name="entity_type" value={panel.entityType} />
        <input type="hidden" name="entity_id" value={context.entityId} />
        <Button variant="secondary" size="xs" loading={busy.is("provision")} disabled={busy.active}>
          {createFolderLabel}
        </Button>
      </form>
    {/if}
  </div>
  {#if !driveState.can_provision}
    <p class="mt-1 text-xs text-text-muted">{t("google.drive.not_provisionable")}</p>
  {/if}
{/if}

<DriveProvisionStatus entityType={panel.entityType} entityId={context.entityId} />

{#if canWrite}
  {#if browsing}
    <div class="mt-4 border-t border-border pt-3">
      <h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("google.drive.browser_title")}
      </h3>
    </div>
    {#if showParentActions && parentFolder}
      <!-- Say where the browser landed — so it's clear it isn't at the shared-drive root (#150).
           The create button lives above with the folder strip now (#444); what belongs to the
           *browsed* folder is adopting it, the same act the picker performs (`?/setDriveFolder`). -->
      <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <span class="text-text-muted">{parentFolderLabel}</span>
        <form method="POST" action="?/setDriveFolder" use:enhance={busy.wrap("link")}>
          <input type="hidden" name="entity_type" value={panel.entityType} />
          <input type="hidden" name="entity_id" value={context.entityId} />
          <input type="hidden" name="drive_file_id" value={parentFolder.drive_file_id} />
          <Button variant="secondary" size="xs" loading={busy.is("link")} disabled={busy.active}>
            {adoptFolderLabel}
          </Button>
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
      {t("google.drive.browse_and_link")}
    </button>
  {/if}
{/if}
