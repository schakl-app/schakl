<script lang="ts">
  /**
   * The client's Drive panel (#21): the linked client folder browsed in place, plus loose
   * linked files. The company page composes API panel *providers* (opaque dicts); the
   * listing itself loads browser-side (see DriveBrowser) so SSR never waits on Google.
   *
   * **Host contract:** `?/linkDriveFile`, `?/unlinkDriveFile`, `?/provisionDriveFolder`.
   */
  import { FolderPlus } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  import DriveBrowser from "./DriveBrowser.svelte";
  import DriveLinkList, { type DriveLinkItem } from "./DriveLinkList.svelte";

  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  const links = $derived((data.links ?? []) as DriveLinkItem[]);
  const folder = $derived((data.folder ?? null) as DriveLinkItem | null);
  const viewerConnected = $derived(Boolean(data.viewer_connected));
  const canProvision = $derived(Boolean(data.can_provision));
  const disabled = $derived(Boolean(data.disabled) || Boolean(data.forbidden));
  const canWrite = $derived(can(page.data.user, "google.drive.write"));
  const looseLinks = $derived(links.filter((link) => link.id !== folder?.id));
</script>

{#if !disabled}
  {#if folder}
    {#if viewerConnected}
      <DriveBrowser
        rootFolderId={folder.drive_file_id}
        entityType="company"
        entityId={companyId}
        canWrite={canWrite && viewerConnected}
      />
    {:else}
      <p class="py-2 text-sm text-text-muted">
        {t("google.drive.connect_to_browse")}
        <a href="/settings/account" class="font-medium text-brand hover:underline"
          >{t("google.account.connect")}</a
        >
      </p>
    {/if}
  {:else}
    <div class="flex flex-wrap items-center justify-between gap-2 py-2">
      <p class="text-sm text-text-muted">{t("google.drive.no_folder_yet")}</p>
      {#if canProvision}
        <form method="POST" action="?/provisionDriveFolder" use:enhance>
          <input type="hidden" name="entity_type" value="company" />
          <input type="hidden" name="entity_id" value={companyId} />
          <button
            type="submit"
            class="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:border-brand"
          >
            <FolderPlus size={13} aria-hidden="true" />
            {t("google.drive.create_folder")}
          </button>
        </form>
      {/if}
    </div>
  {/if}

  {#if looseLinks.length > 0}
    <div class="mt-3">
      <h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-text-muted">
        {t("google.drive.linked_files")}
      </h3>
      <DriveLinkList links={looseLinks} {canWrite} />
    </div>
  {/if}
{:else}
  <p class="py-2 text-sm text-text-muted">{t("google.drive.disabled")}</p>
{/if}
