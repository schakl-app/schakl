<script lang="ts">
  /**
   * The linked Drive files/folders on a record. Unlinking removes the *reference* — the
   * confirm dialog says in as many words that the Drive file itself is never touched (#21).
   *
   * **Two acts, two items** (#394). "Ontkoppelen" is unchanged, wording included; "Verwijderen
   * uit Drive" moves the file itself to Drive's bin. Collapsing them would be wrong in both
   * directions: tidying a record's attachments would bin a client's document, and binning a bad
   * upload would report that it is still there. So each dialog states where the file ends up —
   * untouched, or in the bin for thirty days — which is what makes the pair readable.
   *
   * **Host contract:** the page exposes `?/unlinkDriveFile` and `?/deleteDriveFile`
   * (spread `driveActions`).
   */
  import { ExternalLink, Link2Off, Trash2 } from "@lucide/svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";

  import { driveKind } from "./mime";

  export interface DriveLinkItem {
    id: string;
    drive_file_id: string;
    drive_url: string;
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
    links: DriveLinkItem[];
    /**
     * How many files are attached in total. The company panel's provider counts them (#407);
     * a record's own list holds every row it has, so it passes nothing and the notice never
     * appears.
     */
    total?: number;
    canWrite?: boolean;
    /** A file left Drive: any live listing the host renders beside this one is now stale. */
    ontrashed?: () => void;
  } = $props();

  // Binning a record's *own* folder is `google.drive.manage` at the API — detaching one
  // already is, and binning it is strictly the larger act — so the item is not drawn for a
  // colleague who would only be refused (CLAUDE.md §15: the API's own key, never `!isPortal`).
  const canManage = $derived(can(page.data.user, "google.drive.manage"));

  let unlinkId = $state("");
  let confirmUnlink = $state(false);
  // What Drive said when it refused. Its own strip above the list, because "insufficient
  // permissions" and "this folder is not empty" have different cures and neither is
  // "er ging iets mis" — and a destructive control that reports nothing reads as one that
  // silently worked.
  let trashErrorKey = $state("");
  let trashId = $state("");
  let trashIsFolder = $state(false);
  let confirmTrash = $state(false);

  //: Five attachments is a record's shape; the rest is one press of the expander. This list had
  //: no cap on either surface it appears on (#407).
  const COLLAPSED = 5;
</script>

{#if trashErrorKey}
  <p class="mb-2 rounded-lg bg-surface px-3 py-2 text-sm text-text">{t(trashErrorKey)}</p>
{/if}

{#if links.length === 0}
  <p class="py-2 text-sm text-text-muted">{t("google.drive.no_links")}</p>
{:else}
  <PanelRows rows={links} collapsed={COLLAPSED} {total}>
    {#snippet children(shown)}
      <ul class="divide-y divide-border">
        {#each shown as link (link.id)}
          {@const kind = driveKind(link.mime_type, link.is_folder)}
          {@const KindIcon = kind.icon}
          <li class="flex items-center gap-2 py-2">
            <KindIcon size={15} class="shrink-0 text-text-muted" aria-hidden="true" />
            <a
              href={link.drive_url}
              target="_blank"
              rel="noopener noreferrer"
              class="min-w-0 flex-1 truncate text-sm text-text hover:underline"
              title={link.created_by_name
                ? t("google.drive.linked_by", { name: link.created_by_name })
                : link.name}
            >
              {link.name}
            </a>
            <span class="hidden shrink-0 text-xs text-text-muted sm:inline">{t(kind.labelKey)}</span
            >
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
                  ...(link.is_root && !canManage
                    ? []
                    : [
                        {
                          label: t("google.drive.trash"),
                          icon: Trash2,
                          danger: true,
                          onclick: () => {
                            trashErrorKey = "";
                            trashId = link.drive_file_id;
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
  title={t("google.drive.unlink_title")}
  message={t("google.drive.unlink_message")}
  action="?/unlinkDriveFile"
  confirmLabel={t("google.drive.unlink")}
  fields={{ link_id: unlinkId }}
/>

<!-- The mirror of the dialog above: this one says where the file goes, because that is the
     single fact separating the two controls. A folder additionally names the refusal it can
     meet, so "niet leeg" arrives as a rule and not as a broken button. -->
<ConfirmDialog
  bind:open={confirmTrash}
  title={t("google.drive.trash_title")}
  message={trashIsFolder ? t("google.drive.trash_folder_message") : t("google.drive.trash_message")}
  action="?/deleteDriveFile"
  confirmLabel={t("google.drive.trash")}
  fields={{ drive_file_id: trashId }}
  onfailure={(key) => (trashErrorKey = key)}
  onsuccess={() => ontrashed?.()}
/>
