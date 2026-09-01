<script lang="ts">
  /**
   * The company hub's documents panel. The hub composes API panel *providers* (opaque dicts),
   * so this narrows that dict to the shared attachment strip and posts through the host page's
   * `fileActions("company")` — the same contract every panel on this page follows.
   *
   * Write controls self-gate on `files.file.write` (the key `POST`/`DELETE`/`PATCH /files`
   * declare), never on `!isPortal` (docs/UX.md): a client-portal login holds no such key and
   * the API hands it only the files ticked visible, so the strip reads as a download list.
   */
  import { page } from "$app/state";
  import { can } from "$lib/core/permissions";
  import FileAttachments from "$lib/core/ui/FileAttachments.svelte";

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  let { companyId, data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface StoredFile {
    id: string;
    filename: string;
    content_type: string;
    size_bytes: number;
    client_visible?: boolean;
  }

  const files = $derived((data.items ?? []) as StoredFile[]);
  const canWrite = $derived(can(page.data.user, "files.file.write"));
  const error = $derived((page.form?.fileError ?? null) as string | null);
</script>

<FileAttachments
  {files}
  uploadAction="?/uploadFile"
  deleteAction="?/deleteFile"
  visibilityAction="?/setFileVisibility"
  readonly={!canWrite}
  {error}
/>
