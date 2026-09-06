<script lang="ts">
  /**
   * The linked OneDrive files/folders on a record. Unlinking removes the *reference* — the
   * confirm dialog says in as many words that the item itself is never touched.
   *
   * **Two acts, two items** (#394's rule, one drive over). "Ontkoppelen" is unchanged, wording
   * included; "Verwijderen uit OneDrive" moves the item itself to the recycle bin. Collapsing
   * them would be wrong in both directions: tidying a record's attachments would bin a client's
   * document, and binning a bad upload would report that it is still there. So each dialog
   * states where the file ends up — untouched, or in the recycle bin — which is what makes the
   * pair readable.
   *
   * **Host contract:** the page exposes `?/unlinkOneDriveFile` and `?/deleteOneDriveFile`
   * (spread `oneDriveActions`).
   */
  import { ExternalLink, Link2Off, Trash2 } from "@lucide/svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { oneDriveKind } from "./mime";

  export interface OneDriveLinkItem {
    id: string;
    /** A Graph item lives in a drive; both halves name it. */
    drive_id: string;
    item_id: string;
    web_url: string;
    name: string;
    mime_type?: string | null;
    is_folder: boolean;
    /** This link *is* the record's folder — a decision, not "the first folder linked here". */
    is_root?: boolean;
    entity_id?: string;
    created_by_name?: string | null;
  }

  let {
    links,
    total,
    canWrite = false,
    ontrashed,
  }: {
    links: OneDriveLinkItem[];
    /**
     * How many files are attached in total. The company panel's provider counts them (#407);
     * a record's own list holds every row it has, so it passes nothing and the notice never
     * appears.
     */
    total?: number;
    canWrite?: boolean;
    /** A file left OneDrive: any live listing the host renders beside this one is now stale. */
    ontrashed?: () => void;
  } = $props();

  // Binning a record's *own* folder is `microsoft.onedrive.manage` at the API — detaching one
  // already is, and binning it is strictly the larger act — so the item is not drawn for a
  // colleague who would only be refused (CLAUDE.md §15: the API's own key, never `!isPortal`).
  const canManage = $derived(can(page.data.user, "microsoft.onedrive.manage"));

  let unlinkId = $state("");
  let confirmUnlink = $state(false);
  // What Graph said when it refused. Its own strip above the list, because "insufficient
  // permissions" and "this folder is not empty" have different cures and neither is
  // "er ging iets mis" — and a destructive control that reports nothing reads as one that
  // silently worked.
  let trashErrorKey = $state("");
  let trashDriveId = $state("");
  let trashItemId = $state("");
  let trashIsFolder = $state(false);
  let confirmTrash = $state(false);

  //: Five attachments is a record's shape; the rest is one press of the expander (#407).
  const COLLAPSED = 5;
</script>

{#if trashErrorKey}
  <p class="mb-2 rounded-lg bg-surface px-3 py-2 text-sm text-text">{t(trashErrorKey)}</p>
{/if}

{#if links.length === 0}
  <p class="py-2 text-sm text-text-muted">{t("microsoft.onedrive.no_links")}</p>
{:else}
  <PanelRows rows={links} collapsed={COLLAPSED} {total}>
    {#snippet children(shown)}
      <ul class="divide-y divide-border">
        {#each shown as link (link.id)}
          {@const kind = oneDriveKind(link.mime_type, link.is_folder)}
          {@const KindIcon = kind.icon}
          <li class="flex items-center gap-2 py-2">
            <KindIcon size={15} class="shrink-0 text-text-muted" aria-hidden="true" />
            <a
              href={link.web_url}
              target="_blank"
              rel="noopener noreferrer"
              class="min-w-0 flex-1 truncate text-sm text-text hover:underline"
              title={link.created_by_name
                ? t("microsoft.onedrive.linked_by", { name: link.created_by_name })
                : link.name}
            >
              {link.name}
            </a>
            <span class="hidden shrink-0 text-xs text-text-muted sm:inline">{t(kind.labelKey)}</span
            >
            <a
              href={link.web_url}
              target="_blank"
              rel="noopener noreferrer"
              class="rounded p-1 text-text-muted hover:text-brand"
              aria-label={t("microsoft.onedrive.open_in_onedrive")}
              title={t("microsoft.onedrive.open_in_onedrive")}
            >
              <ExternalLink size={14} aria-hidden="true" />
            </a>
            {#if canWrite}
              <ActionsMenu
                compact
                items={[
                  {
                    label: t("microsoft.onedrive.unlink"),
                    icon: Link2Off,
                    danger: true,
                    onclick: () => {
                      unlinkId = link.id;
                      confirmUnlink = true;
                    },
                  },
                  ...(link.is_root && !canManage
                    ? []
                    : [
                        {
                          label: t("microsoft.onedrive.trash"),
                          icon: Trash2,
                          danger: true,
                          onclick: () => {
                            trashErrorKey = "";
                            trashDriveId = link.drive_id;
                            trashItemId = link.item_id;
                            trashIsFolder = link.is_folder;
                            confirmTrash = true;
                          },
                        },
                      ]),
                ]}
              />
            {/if}
          </li>
        {/each}
      </ul>
    {/snippet}
  </PanelRows>
{/if}

<ConfirmDialog
  bind:open={confirmUnlink}
  title={t("microsoft.onedrive.unlink_title")}
  message={t("microsoft.onedrive.unlink_message")}
  action="?/unlinkOneDriveFile"
  confirmLabel={t("microsoft.onedrive.unlink")}
  fields={{ link_id: unlinkId }}
/>

<!-- The mirror of the dialog above: this one says where the file goes, because that is the
     single fact separating the two controls. A folder additionally names the refusal it can
     meet, so "niet leeg" arrives as a rule and not as a broken button. -->
<ConfirmDialog
  bind:open={confirmTrash}
  title={t("microsoft.onedrive.trash_title")}
  message={trashIsFolder
    ? t("microsoft.onedrive.trash_folder_message")
    : t("microsoft.onedrive.trash_message")}
  action="?/deleteOneDriveFile"
  confirmLabel={t("microsoft.onedrive.trash")}
  fields={{ drive_id: trashDriveId, item_id: trashItemId }}
  onfailure={(key) => (trashErrorKey = key)}
  onsuccess={() => ontrashed?.()}
/>
