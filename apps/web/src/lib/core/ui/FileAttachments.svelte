<script lang="ts">
  /**
   * Documents attached to a record through the storage core (#123): images as a strip of
   * thumbnails, everything else as a list, with an upload control and a per-row ⋯ → delete
   * (confirmed). The host page owns the form actions (`$lib/core/files/actions.server`) — this
   * component only renders, mirroring how panels post to their host.
   *
   * Three ways in, one form. A **click** on the button, a **drop** onto the strip
   * (`use:filedrop`, the house convention) and a **paste** from the clipboard all land files on
   * the same `<input type="file">` and submit the same multipart form, so the server sees one
   * kind of upload however it arrived. The paste is what a screenshot actually wants: the
   * gesture is *print screen, open the task, Ctrl+V*, and a route that first needs the image
   * saved to disk and dragged out of a folder is the three steps this component exists to
   * remove. It listens on the document while the strip is mounted and editable, and steps aside
   * when the paste is text bound for a field the user is typing in — an image pasted with a
   * selection of text is that text, not a screenshot.
   *
   * An image is **shown, not spelled out** (docs/UX.md): `shot-2026-08-31.png` says nothing and
   * the picture says everything, so the strip draws the API's thumbnail and a click opens the
   * original in a lightbox. A file the browser cannot draw stays a filename with a paperclip.
   *
   * The client's view of a file is a per-file bit (`client_visible`), toggled here with an eye
   * and stated in words on hover. It defaults to hidden — a screenshot pinned to a bug is the
   * team's working material until somebody decides otherwise — and the API enforces it on every
   * path (list, bytes, thumbnail), so the eye is the control and never the gate.
   */
  import { Eye, EyeOff, ExternalLink, Paperclip, Trash2, X } from "@lucide/svelte";
  import { onMount } from "svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { pastedImageName } from "$lib/core/files/paste";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";

  interface StoredFile {
    id: string;
    filename: string;
    content_type: string;
    size_bytes: number;
    client_visible?: boolean;
  }

  let {
    files,
    uploadAction,
    deleteAction,
    visibilityAction = null,
    error = null,
    readonly = false,
    paste = true,
  }: {
    files: StoredFile[];
    uploadAction: string;
    deleteAction: string;
    /** The host action that flips `client_visible`; absent hides the eye. */
    visibilityAction?: string | null;
    error?: string | null;
    /** Download-only: no upload control, no per-row delete, no eye. */
    readonly?: boolean;
    /** Take a pasted screenshot from anywhere on the page while mounted (and editable). */
    paste?: boolean;
  } = $props();

  // A client-portal login only ever receives the files it may see, so the eye would say the
  // same thing on every row — a relevance gate, never the security one (CLAUDE.md §15).
  const isPortal = $derived(page.data.user?.isPortal ?? false);

  const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
  const isImage = (file: StoredFile) => IMAGE_TYPES.has(file.content_type);
  const images = $derived(files.filter(isImage));
  const documents = $derived(files.filter((file) => !isImage(file)));

  let confirmOpen = $state(false);
  let confirmFileId = $state("");
  let uploading = $state(false);
  let localError = $state<string | null>(null);
  let lightbox = $state<StoredFile | null>(null);
  let dialog = $state<HTMLDialogElement | null>(null);
  let input = $state<HTMLInputElement | null>(null);

  function askDelete(fileId: string) {
    confirmFileId = fileId;
    confirmOpen = true;
  }

  function fmtSize(bytes: number): string {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    if (bytes >= 1024) return `${Math.round(bytes / 1024)} kB`;
    return `${bytes} B`;
  }

  function open(file: StoredFile) {
    lightbox = file;
    dialog?.showModal();
  }

  function close() {
    dialog?.close();
    lightbox = null;
  }

  function onPaste(event: ClipboardEvent) {
    // A rich-text editor with an upload target already took this paste and put the image
    // *inline* where the caret is (`richtext/editor.ts` prevents the default) — the person
    // was writing, so the words get the picture, not the attachment strip.
    if (event.defaultPrevented) return;
    if (readonly || !paste || uploading || !input) return;
    const data = event.clipboardData;
    if (!data) return;
    const pasted = Array.from(data.files).filter((file) => file.type.startsWith("image/"));
    if (pasted.length === 0) return;
    // Text bound for a field the user is typing in wins: a copy from a web page carries the
    // selection *and* a rendering of it, and the person meant the words.
    const target = event.target as HTMLElement | null;
    const typing =
      !!target &&
      (target.closest("input, textarea, [contenteditable=''], [contenteditable='true']") !==
        null) &&
      data.types.includes("text/plain");
    if (typing) return;
    event.preventDefault();
    const transfer = new DataTransfer();
    for (const file of pasted) {
      transfer.items.add(new File([file], pastedImageName(file), { type: file.type }));
    }
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  onMount(() => {
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  });
</script>

{#if images.length > 0}
  <ul class="mb-3 grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5">
    {#each images as file (file.id)}
      <li class="group relative">
        <button
          type="button"
          class="block w-full overflow-hidden rounded-lg border border-border bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-brand"
          title={file.filename}
          onclick={() => open(file)}
        >
          <img
            src={`/api/v1/files/${file.id}/thumbnail?size=480`}
            alt={file.filename}
            loading="lazy"
            class="aspect-square w-full object-cover"
          />
        </button>
        <div class="mt-1 flex items-center gap-1">
          <span class="min-w-0 flex-1 truncate text-xs text-text-muted" title={file.filename}>
            {file.filename}
          </span>
          {@render visibility(file)}
          {@render menu(file)}
        </div>
      </li>
    {/each}
  </ul>
{/if}

{#if documents.length > 0}
  <ul class="mb-3 space-y-1">
    {#each documents as file (file.id)}
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
        {@render visibility(file)}
        {@render menu(file)}
      </li>
    {/each}
  </ul>
{/if}

{#snippet visibility(file: StoredFile)}
  {#if !isPortal}
    {#if !readonly && visibilityAction}
      <!-- The eye is the control: one click flips who may read this file. The colours follow
           the task's own visibility marker — solid eye for visible, faint struck eye for hidden. -->
      <form method="POST" action={visibilityAction} use:enhance class="contents">
        <input type="hidden" name="file_id" value={file.id} />
        <input type="hidden" name="client_visible" value={file.client_visible ? "false" : "true"} />
        <button
          type="submit"
          class="inline-flex shrink-0 items-center rounded p-0.5 hover:bg-surface-raised {file.client_visible
            ? 'text-text'
            : 'text-text-muted opacity-60'}"
          title={file.client_visible ? t("files.hide_from_client") : t("files.show_to_client")}
          aria-label={file.client_visible ? t("files.hide_from_client") : t("files.show_to_client")}
          aria-pressed={file.client_visible ?? false}
        >
          {#if file.client_visible}<Eye size={14} />{:else}<EyeOff size={14} />{/if}
        </button>
      </form>
    {:else if file.client_visible}
      <span
        role="img"
        class="inline-flex shrink-0 items-center text-text"
        title={t("files.client_visible")}
        aria-label={t("files.client_visible")}
      >
        <Eye size={14} />
      </span>
    {/if}
  {/if}
{/snippet}

{#snippet menu(file: StoredFile)}
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
{/snippet}

{#if !readonly}
  <!-- A document dragged straight out of a mail client is how most of these arrive; the drop
       lands on the input, so it submits through the same form the button does — and so does a
       pasted screenshot. The input stays `sr-only` rather than `hidden` — a display:none control
       cannot be focused, and the drop is only ever an accelerator for the click (docs/UX.md). -->
  <form
    method="POST"
    action={uploadAction}
    enctype="multipart/form-data"
    use:enhance={() => {
      uploading = true;
      localError = null;
      return async ({ update }) => {
        await update();
        uploading = false;
      };
    }}
    use:filedrop={{ onerror: (key) => (localError = key) }}
    class="flex flex-wrap items-center gap-2"
  >
    <label
      class="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:border-brand hover:text-brand focus-within:border-brand"
      aria-busy={uploading}
    >
      <Paperclip size={14} />
      {uploading ? t("files.uploading") : t("files.upload")}
      <input
        bind:this={input}
        type="file"
        name="file"
        multiple
        class="sr-only"
        disabled={uploading}
        onchange={(e) => e.currentTarget.form?.requestSubmit()}
      />
    </label>
    <span class="text-xs text-text-muted">
      {paste ? t("files.drop_or_paste_hint") : t("common.drop_hint")}
    </span>
  </form>
{/if}
{#if error || localError}
  <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">{t(error ?? localError ?? "")}</p>
{/if}

<!-- The lightbox: the original bytes, at the size the screen allows, and a way out. A native
     <dialog> so Escape, the backdrop and focus containment come for free. -->
<dialog
  bind:this={dialog}
  class="m-auto max-h-[92vh] max-w-[92vw] rounded-xl border border-border bg-surface-raised p-0 shadow-xl backdrop:bg-black/70"
  onclose={() => (lightbox = null)}
  onclick={(e) => {
    if (e.target === dialog) close();
  }}
>
  {#if lightbox}
    <div class="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
      <span class="min-w-0 truncate text-sm text-text" title={lightbox.filename}>
        {lightbox.filename}
        <span class="ml-2 text-xs text-text-muted">{fmtSize(lightbox.size_bytes)}</span>
      </span>
      <div class="flex shrink-0 items-center gap-1">
        <a
          href={`/api/v1/files/${lightbox.id}`}
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-text-muted hover:bg-surface hover:text-text"
        >
          <ExternalLink size={14} />
          {t("files.open_original")}
        </a>
        <button
          type="button"
          class="inline-flex items-center rounded p-1 text-text-muted hover:bg-surface hover:text-text"
          aria-label={t("common.close")}
          onclick={close}
        >
          <X size={16} />
        </button>
      </div>
    </div>
    <img
      src={`/api/v1/files/${lightbox.id}`}
      alt={lightbox.filename}
      class="block max-h-[calc(92vh-3rem)] max-w-[92vw] object-contain"
    />
  {/if}
</dialog>

<ConfirmDialog
  bind:open={confirmOpen}
  title={t("files.delete")}
  message={t("files.delete_confirm")}
  action={deleteAction}
  fields={{ file_id: confirmFileId }}
/>
