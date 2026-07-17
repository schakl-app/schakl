<script lang="ts">
  /**
   * Documents attached to a record through the storage core (#123): a list of stored files
   * with an upload button and a per-row ⋯ → delete (confirmed). The host page owns the form
   * actions (upload posts multipart to `uploadAction`, delete posts `file_id` to
   * `deleteAction`) — this component only renders, mirroring how panels post to their host.
   */
  import { Paperclip } from "@lucide/svelte";
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  interface StoredFile {
    id: string;
    filename: string;
    content_type: string;
    size_bytes: number;
  }

  let {
    files,
    uploadAction,
    deleteAction,
    error = null,
    readonly = false,
  }: {
    files: StoredFile[];
    uploadAction: string;
    deleteAction: string;
    error?: string | null;
    /** Download-only: no upload button, no per-row delete — a record's use mode (docs/UX.md §3). */
    readonly?: boolean;
  } = $props();

  let confirmOpen = $state(false);
  let confirmFileId = $state("");

  function askDelete(fileId: string) {
    confirmFileId = fileId;
    confirmOpen = true;
  }

  function fmtSize(bytes: number): string {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    if (bytes >= 1024) return `${Math.round(bytes / 1024)} kB`;
    return `${bytes} B`;
  }
</script>

{#if files.length > 0}
  <ul class="mb-3 space-y-1">
    {#each files as file (file.id)}
      <li class="group flex items-center gap-2">
        <Paperclip size={14} class="shrink-0 text-text-muted" />
        <a
          href={`/api/v1/files/${file.id}`}
          target="_blank"
          rel="noopener noreferrer"
          class="min-w-0 flex-1 truncate text-sm text-brand hover:underline"
        >
          {file.filename}
        </a>
        <span class="shrink-0 text-xs text-text-muted">{fmtSize(file.size_bytes)}</span>
        {#if !readonly}
          <ActionsMenu
            compact
            items={[
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => askDelete(file.id),
              },
            ]}
          />
        {/if}
      </li>
    {/each}
  </ul>
{/if}

{#if !readonly}
  <form method="POST" action={uploadAction} enctype="multipart/form-data" use:enhance>
    <label
      class="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:border-brand hover:text-brand"
    >
      <Paperclip size={14} />
      {t("files.upload")}
      <input
        type="file"
        name="file"
        class="hidden"
        onchange={(e) => e.currentTarget.form?.requestSubmit()}
      />
    </label>
  </form>
{/if}
{#if error}
  <p class="mt-1 text-sm text-red-600 dark:text-red-400">{t(error)}</p>
{/if}

<ConfirmDialog
  bind:open={confirmOpen}
  title={t("files.delete")}
  message={t("files.delete_confirm")}
  action={deleteAction}
  fields={{ file_id: confirmFileId }}
/>
