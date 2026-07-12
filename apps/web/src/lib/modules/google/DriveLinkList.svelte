<script lang="ts">
  /**
   * The linked Drive files/folders on a record. Unlinking removes the *reference* — the
   * confirm dialog says in as many words that the Drive file itself is never touched (#21).
   *
   * **Host contract:** the page exposes `?/unlinkDriveFile` (spread `driveActions`).
   */
  import { ExternalLink, File, Folder, Link2Off } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  export interface DriveLinkItem {
    id: string;
    drive_file_id: string;
    drive_url: string;
    name: string;
    mime_type?: string | null;
    is_folder: boolean;
    created_by_name?: string | null;
  }

  let { links, canWrite = false }: { links: DriveLinkItem[]; canWrite?: boolean } = $props();

  let unlinkId = $state("");
  let confirmUnlink = $state(false);
</script>

{#if links.length === 0}
  <p class="py-2 text-sm text-text-muted">{t("google.drive.no_links")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each links as link (link.id)}
      <li class="flex items-center gap-2 py-2">
        {#if link.is_folder}
          <Folder size={15} class="shrink-0 text-text-muted" aria-hidden="true" />
        {:else}
          <File size={15} class="shrink-0 text-text-muted" aria-hidden="true" />
        {/if}
        <a
          href={link.drive_url}
          target="_blank"
          rel="noopener noreferrer"
          class="min-w-0 flex-1 truncate text-sm text-text hover:underline"
        >
          {link.name}
        </a>
        <a
          href={link.drive_url}
          target="_blank"
          rel="noopener noreferrer"
          class="rounded p-1 text-text-muted hover:text-brand"
          aria-label={t("google.drive.open_in_drive")}
          title={t("google.drive.open_in_drive")}
        >
          <ExternalLink size={14} aria-hidden="true" />
        </a>
        {#if canWrite}
          <ActionsMenu
            compact
            items={[
              {
                label: t("google.drive.unlink"),
                icon: Link2Off,
                danger: true,
                onclick: () => {
                  unlinkId = link.id;
                  confirmUnlink = true;
                },
              },
            ]}
          />
        {/if}
      </li>
    {/each}
  </ul>
{/if}

<ConfirmDialog
  bind:open={confirmUnlink}
  title={t("google.drive.unlink_title")}
  message={t("google.drive.unlink_message")}
  action="?/unlinkDriveFile"
  fields={{ link_id: unlinkId }}
/>
