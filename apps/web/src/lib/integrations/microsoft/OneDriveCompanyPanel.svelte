<script lang="ts">
  /**
   * The client's OneDrive panel: the linked client folder browsed in place, plus loose linked
   * files. The company page composes API panel *providers* (opaque dicts); the listing itself
   * loads browser-side (see OneDriveBrowser) so SSR never waits on Graph.
   *
   * A client's folder usually already exists — agencies do not start their document library
   * here — so "Map kiezen" browses the library and points this client at an existing folder,
   * beside the "Map aanmaken" that provisions a new one. Changing or detaching a folder that is
   * already set is the API's `microsoft.onedrive.manage`, and the panel draws those two
   * controls on that gate: a control that would 403 is never drawn (CLAUDE.md §15).
   *
   * **Host contract:** `?/linkOneDriveFile`, `?/unlinkOneDriveFile`, `?/deleteOneDriveFile`,
   * `?/provisionOneDriveFolder`, `?/setOneDriveFolder`.
   */
  import { FolderPlus, FolderSearch, Link2Off } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  import OneDriveBrowser from "./OneDriveBrowser.svelte";
  import OneDriveLinkList, { type OneDriveLinkItem } from "./OneDriveLinkList.svelte";
  import OneDriveProvisionStatus from "./OneDriveProvisionStatus.svelte";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  // A OneDrive action's refusal renders here, beside the button that fired it (#444).
  const oneDriveError = $derived((page.form?.oneDriveError ?? null) as string | null);

  const busy = new InFlight();

  const links = $derived((data.links ?? []) as OneDriveLinkItem[]);
  const total = $derived(data.total as number | undefined);
  const folder = $derived((data.folder ?? null) as OneDriveLinkItem | null);
  const viewerConnected = $derived(Boolean(data.viewer_connected));
  const canProvision = $derived(Boolean(data.can_provision));
  const canPick = $derived(Boolean(data.can_pick));
  const canManage = $derived(Boolean(data.can_manage));
  const disabled = $derived(Boolean(data.disabled) || Boolean(data.forbidden));
  const canWrite = $derived(can(page.data.user, "microsoft.onedrive.write"));
  const looseLinks = $derived(links.filter((link) => link.id !== folder?.id));

  // The picker is the same browser in pick mode, started at the org's root (the configured
  // library and client-folders parent) rather than at this client's folder.
  let picking = $state(false);
  let confirmDetach = $state(false);
  // Bumped when the list below bins a file: the browser's listing is live and no page
  // invalidation reaches it, so it would keep showing a file that has left OneDrive.
  let driveVersion = $state(0);
</script>

{#if !disabled}
  {#if oneDriveError}
    <p class="mb-2 text-sm text-red-600 dark:text-red-400" role="alert">{t(oneDriveError)}</p>
  {/if}
  <OneDriveProvisionStatus entityType="company" entityId={companyId} />
  {#if folder && !picking}
    <div class="flex items-center gap-2 py-1">
      <p class="min-w-0 flex-1 truncate text-sm text-text-muted">
        {t("microsoft.onedrive.in_client_folder", { name: folder.name })}
      </p>
      {#if canManage}
        <ActionsMenu
          compact
          items={[
            {
              label: t("microsoft.onedrive.change_folder"),
              icon: FolderSearch,
              onclick: () => {
                picking = true;
              },
            },
            {
              label: t("microsoft.onedrive.detach_folder"),
              icon: Link2Off,
              danger: true,
              onclick: () => {
                confirmDetach = true;
              },
            },
          ]}
        />
      {/if}
    </div>
  {/if}

  {#if picking}
    <!-- Pick mode: browse the library and choose a folder for this client. -->
    <div class="flex items-center justify-between gap-2 py-1">
      <p class="text-sm text-text-muted">{t("microsoft.onedrive.pick_folder_hint")}</p>
      <button
        type="button"
        class="shrink-0 text-sm font-medium text-text-muted hover:underline"
        onclick={() => (picking = false)}
      >
        {t("common.cancel")}
      </button>
    </div>
    {#if viewerConnected}
      <OneDriveBrowser
        rootDriveId={null}
        rootFolderId={null}
        entityType="company"
        entityId={companyId}
        canWrite={canWrite && viewerConnected}
        pick
        onpicked={() => (picking = false)}
      />
    {:else}
      <p class="py-2 text-sm text-text-muted">
        {t("microsoft.onedrive.connect_to_browse")}
        <a href="/settings/account" class="font-medium text-brand hover:underline"
          >{t("microsoft.account.connect")}</a
        >
      </p>
    {/if}
  {:else if folder}
    {#if viewerConnected}
      <OneDriveBrowser
        rootDriveId={folder.drive_id}
        rootFolderId={folder.item_id}
        entityType="company"
        entityId={companyId}
        canWrite={canWrite && viewerConnected}
        reloadToken={driveVersion}
      />
    {:else}
      <p class="py-2 text-sm text-text-muted">
        {t("microsoft.onedrive.connect_to_browse")}
        <a href="/settings/account" class="font-medium text-brand hover:underline"
          >{t("microsoft.account.connect")}</a
        >
      </p>
    {/if}
  {:else}
    <div class="flex flex-wrap items-center justify-between gap-2 py-2">
      <p class="text-sm text-text-muted">{t("microsoft.onedrive.no_folder_yet")}</p>
      <div class="flex flex-wrap items-center gap-2">
        {#if canPick}
          <Button type="button" variant="secondary" size="xs" onclick={() => (picking = true)}>
            <FolderSearch size={13} aria-hidden="true" />
            {t("microsoft.onedrive.pick_folder")}
          </Button>
        {/if}
        {#if canProvision}
          <form method="POST" action="?/provisionOneDriveFolder" use:enhance={busy.wrap()}>
            <input type="hidden" name="entity_type" value="company" />
            <input type="hidden" name="entity_id" value={companyId} />
            <Button type="submit" variant="secondary" size="xs" loading={busy.active}>
              <FolderPlus size={13} aria-hidden="true" />
              {t("microsoft.onedrive.create_folder")}
            </Button>
          </form>
        {/if}
      </div>
    </div>
  {/if}

  {#if looseLinks.length > 0}
    <div class="mt-3">
      <h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("microsoft.onedrive.linked_files")}
      </h3>
      <!-- The provider counts what it capped (#407); the client's own folder is drawn above,
           so the notice is about the loose attachments beside it. -->
      <OneDriveLinkList
        links={looseLinks}
        total={total != null ? total - (folder ? 1 : 0) : undefined}
        {canWrite}
        ontrashed={() => (driveVersion += 1)}
      />
    </div>
  {/if}
{:else}
  <p class="py-2 text-sm text-text-muted">{t("microsoft.onedrive.disabled")}</p>
{/if}

<ConfirmDialog
  bind:open={confirmDetach}
  title={t("microsoft.onedrive.detach_folder")}
  message={t("microsoft.onedrive.detach_folder_message")}
  action="?/unlinkOneDriveFile"
  confirmLabel={t("microsoft.onedrive.detach_folder")}
  fields={{ link_id: folder?.id ?? "" }}
/>
