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
   * original in the app's one viewer (`lightbox.svelte.ts`), handed the whole strip so ← → walk
   * the screenshots in the order they are drawn. A file the browser cannot draw stays a filename
   * with a paperclip.
   *
   * The client's view of a file is a per-file bit (`client_visible`), toggled here with an eye
   * and stated in words on hover. It defaults to hidden — a screenshot pinned to a bug is the
   * team's working material until somebody decides otherwise — and the API enforces it on every
   * path (list, bytes, thumbnail), so the eye is the control and never the gate.
   *
   * **Two ways to post.** A page about one record owns the form actions (`fileActions`) and
   * hands them in as `uploadAction` / `deleteAction`. A strip drawn *inside a dialog on some
   * other page* — the task review slide-over on the inbox — has no host action that knows the
   * task, so it names the record with `direct` and this component talks to `/api/v1/files`
   * itself, the way the rich-text editor already stores an inline image, and reports through
   * `onchange` that the list is stale. Same three gestures, same server, one component. A
   * direct strip also listens for the paste in the **capture** phase: a host page's own strip
   * registered first and would otherwise take the screenshot meant for the dialog.
   */
  import { Eye, EyeOff, Paperclip, Trash2 } from "@lucide/svelte";
  import { onMount } from "svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { pastedImageName } from "$lib/core/files/paste";
  import { fmtBytes } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";
  import { openLightbox } from "$lib/core/ui/lightbox.svelte";

  import Button from "./Button.svelte";
  import Modal from "./Modal.svelte";

  interface StoredFile {
    id: string;
    filename: string;
    content_type: string;
    size_bytes: number;
    client_visible?: boolean;
  }

  let {
    files,
    uploadAction = "",
    deleteAction = "",
    visibilityAction = null,
    direct = null,
    onchange,
    error = null,
    readonly = false,
    paste = true,
  }: {
    files: StoredFile[];
    uploadAction?: string;
    deleteAction?: string;
    /** The host action that flips `client_visible`; absent hides the eye. */
    visibilityAction?: string | null;
    /** Post to the storage API directly for this record instead of through host actions. */
    direct?: { entityType: string; entityId: string } | null;
    /** Direct mode: the list on the server changed; the host re-reads it. */
    onchange?: () => void | Promise<void>;
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
  let input = $state<HTMLInputElement | null>(null);

  function askDelete(fileId: string) {
    confirmFileId = fileId;
    confirmOpen = true;
  }

  function open(index: number) {
    openLightbox(
      images.map((file) => ({
        src: `/api/v1/files/${file.id}`,
        thumb: `/api/v1/files/${file.id}/thumbnail?size=480`,
        label: file.filename,
        sizeBytes: file.size_bytes,
      })),
      index,
    );
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
    // `instanceof Element`, not a truthiness check: a paste dispatched at the document itself
    // has a target with no `closest`, and a listener that throws there takes every later
    // paste handler down with it.
    const target = event.target instanceof Element ? event.target : null;
    // In the capture phase this runs *before* the editor's own handler, so an image pasted
    // into a rich-text editor is left to it — it is the words that get the picture there.
    if (direct && target?.closest("[contenteditable='true']")) return;
    const typing =
      target !== null &&
      target.closest("input, textarea, [contenteditable=''], [contenteditable='true']") !== null &&
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
    const capture = direct !== null;
    document.addEventListener("paste", onPaste, capture);
    return () => document.removeEventListener("paste", onPaste, capture);
  });

  // ---- direct mode: the same three gestures against /api/v1/files -----------------------
  async function directUpload(event: SubmitEvent) {
    event.preventDefault();
    if (!direct || !input?.files?.length) return;
    uploading = true;
    localError = null;
    try {
      for (const file of Array.from(input.files)) {
        if (file.size === 0) continue;
        const body = new FormData();
        body.append("file", file, file.name);
        const query = new URLSearchParams({
          entity_type: direct.entityType,
          entity_id: direct.entityId,
        });
        const res = await fetch(`/api/v1/files?${query}`, { method: "POST", body });
        if (!res.ok) {
          localError = res.status === 413 ? "errors.upload_too_large" : "errors.upload_type";
          break;
        }
      }
      input.value = "";
      await onchange?.();
    } catch {
      localError = "errors.server";
    } finally {
      uploading = false;
    }
  }

  let deleting = $state(false);
  async function directDelete() {
    if (!confirmFileId || deleting) return;
    deleting = true;
    try {
      const res = await fetch(`/api/v1/files/${confirmFileId}`, { method: "DELETE" });
      if (!res.ok) localError = "errors.forbidden";
      confirmOpen = false;
      await onchange?.();
    } finally {
      deleting = false;
    }
  }

  async function directVisibility(file: StoredFile) {
    const res = await fetch(`/api/v1/files/${file.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ client_visible: !file.client_visible }),
    });
    if (!res.ok) {
      localError = "errors.forbidden";
      return;
    }
    await onchange?.();
  }
</script>

{#if images.length > 0}
  <ul class="mb-3 grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5">
    {#each images as file, i (file.id)}
      <li class="group relative">
        <button
          type="button"
          class="block w-full overflow-hidden rounded-lg border border-border bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-brand"
          title={file.filename}
          onclick={() => open(i)}
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
        <span class="shrink-0 text-xs text-text-muted">{fmtBytes(file.size_bytes)}</span>
        {@render visibility(file)}
        {@render menu(file)}
      </li>
    {/each}
  </ul>
{/if}

{#snippet visibility(file: StoredFile)}
  {#if !isPortal}
    {#if !readonly && direct}
      <button
        type="button"
        class="inline-flex shrink-0 items-center rounded p-0.5 hover:bg-surface-raised {file.client_visible
          ? 'text-text'
          : 'text-text-muted opacity-60'}"
        title={file.client_visible ? t("files.hide_from_client") : t("files.show_to_client")}
        aria-label={file.client_visible ? t("files.hide_from_client") : t("files.show_to_client")}
        aria-pressed={file.client_visible ?? false}
        onclick={() => directVisibility(file)}
      >
        {#if file.client_visible}<Eye size={14} />{:else}<EyeOff size={14} />{/if}
      </button>
    {:else if !readonly && visibilityAction}
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
  {#if direct}
    <!-- No `use:enhance` here, and not merely a no-op one: the action would still post the
         form to the page as a nameless action (a 404 that navigates), beside the upload this
         handler makes itself. Two forms, one snippet, so the markup cannot drift. -->
    <form
      method="POST"
      enctype="multipart/form-data"
      onsubmit={directUpload}
      use:filedrop={{ onerror: (key) => (localError = key) }}
      class="flex flex-wrap items-center gap-2"
    >
      {@render uploadControl()}
    </form>
  {:else}
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
      {@render uploadControl()}
    </form>
  {/if}
{/if}
{#snippet uploadControl()}
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
{/snippet}

{#if error || localError}
  <p class="mt-1 text-sm text-red-600 dark:text-red-400" role="alert">
    {t(error ?? localError ?? "")}
  </p>
{/if}

{#if direct}
  <Modal bind:open={confirmOpen} title={t("files.delete")} size="lg">
    <p class="text-sm text-text-muted">{t("files.delete_confirm")}</p>
    <div class="mt-4 flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (confirmOpen = false)}>{t("common.cancel")}</button
      >
      <Button type="button" variant="danger" loading={deleting} onclick={directDelete}>
        {t("common.delete")}
      </Button>
    </div>
  </Modal>
{:else}
  <ConfirmDialog
    bind:open={confirmOpen}
    title={t("files.delete")}
    message={t("files.delete_confirm")}
    action={deleteAction}
    fields={{ file_id: confirmFileId }}
  />
{/if}
