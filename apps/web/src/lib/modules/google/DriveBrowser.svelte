<script lang="ts">
  /**
   * The embedded Drive folder browser (issue #21): breadcrumb, folders-first list, open in
   * Drive, upload, and link-to-record — our own browse-and-pick, no Google Picker (that
   * would drag in a browser API key; we already have an authenticated API).
   *
   * Two deliberate exceptions to the SSR-only data path, both from docs/GOOGLE.md §5:
   * - The **listing** loads from the browser after mount. A live Google round trip inside a
   *   page's `Promise.all` would tax every company view; here the page renders instantly and
   *   the (Redis-cached, viewer-scoped) listing fills in.
   * - **Upload bytes go straight to Google**: the API only mints the resumable session URI;
   *   the browser PUTs the file to googleusercontent. File contents never transit our API.
   * - **Creating a folder** posts to `/api/v1/google/drive/folders` from here (same viewer-scoped
   *   API, same-origin cookie), then re-lists — it needs no host action and no page data.
   *
   * **Host contract:** the page exposes `?/linkDriveFile` (spread `driveActions`).
   */
  import {
    ChevronLeft,
    ExternalLink,
    FolderPlus,
    Link2,
    RefreshCw,
    Upload,
  } from "@lucide/svelte";
  import { onMount } from "svelte";

  import { enhance } from "$app/forms";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { driveKind } from "./mime";

  interface BrowseItem {
    id: string;
    name: string;
    mime_type: string | null;
    is_folder: boolean;
    web_view_link: string | null;
    modified_at: string | null;
    size: number | null;
  }

  interface Listing {
    folder: { id: string | null; name: string | null; web_view_link: string | null };
    items: BrowseItem[];
  }

  let {
    rootFolderId = null,
    entityType,
    entityId,
    canWrite = false,
  }: {
    /** Where browsing starts; null = the org's client-folders parent. */
    rootFolderId?: string | null;
    /** The record rows get linked to via the host's `?/linkDriveFile` action. */
    entityType: string;
    entityId: string;
    canWrite?: boolean;
  } = $props();

  // Deliberately the *initial* root: the breadcrumb is navigation state owned here, and a
  // remount (the {#key} the host controls) is how a new root arrives.
  // svelte-ignore state_referenced_locally
  let crumbs = $state<{ id: string | null; name: string | null }[]>([
    { id: rootFolderId, name: null },
  ]);
  let listing = $state<Listing | null>(null);
  let loading = $state(false);
  let errorKey = $state("");
  let uploading = $state(false);
  // "New folder" affordance: an inline name field, opened from the header (issue #150 follow-up).
  let creatingFolder = $state(false);
  let newFolderName = $state("");
  let savingFolder = $state(false);
  let folderNameInput = $state<HTMLInputElement | null>(null);

  const current = $derived(crumbs[crumbs.length - 1]);

  async function load(refresh = false) {
    loading = true;
    errorKey = "";
    const params = new URLSearchParams();
    if (current.id) params.set("folder_id", current.id);
    if (refresh) params.set("refresh", "true");
    try {
      const response = await fetch(`/api/v1/google/drive/browse?${params}`, {
        headers: { accept: "application/json" },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        errorKey = body?.error?.message ?? "errors.google_drive_unavailable";
        listing = null;
        return;
      }
      listing = (await response.json()) as Listing;
    } catch {
      errorKey = "errors.google_drive_unavailable";
      listing = null;
    } finally {
      loading = false;
    }
  }

  function open(item: BrowseItem) {
    if (!item.is_folder) return;
    crumbs = [...crumbs, { id: item.id, name: item.name }];
    void load();
  }

  function jump(index: number) {
    crumbs = crumbs.slice(0, index + 1);
    void load();
  }

  // Go back to the folder one level up in the trail we descended (the breadcrumb does the same
  // for any ancestor; this is the explicit one-tap "back" people expect).
  function goUp() {
    if (crumbs.length < 2) return;
    jump(crumbs.length - 2);
  }

  function toggleCreateFolder() {
    creatingFolder = !creatingFolder;
    newFolderName = "";
    if (creatingFolder) {
      // Focus after the input renders.
      void Promise.resolve().then(() => folderNameInput?.focus());
    }
  }

  async function createFolder() {
    const name = newFolderName.trim();
    const parentId = listing?.folder?.id;
    if (!name || !parentId || savingFolder) return;
    savingFolder = true;
    errorKey = "";
    try {
      const response = await fetch("/api/v1/google/drive/folders", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ parent_id: parentId, name }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        errorKey = body?.error?.message ?? "errors.google_drive_folder_failed";
        return;
      }
      newFolderName = "";
      creatingFolder = false;
      await load(true);
    } catch {
      errorKey = "errors.google_drive_folder_failed";
    } finally {
      savingFolder = false;
    }
  }

  async function upload(input: HTMLInputElement) {
    const file = input.files?.[0];
    if (!file || !listing?.folder?.id) return;
    uploading = true;
    errorKey = "";
    try {
      const session = await fetch("/api/v1/google/drive/upload-session", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({
          folder_id: listing.folder.id,
          name: file.name,
          mime_type: file.type || null,
        }),
      });
      if (!session.ok) {
        errorKey = "errors.google_upload_failed";
        return;
      }
      const { session_uri } = (await session.json()) as { session_uri: string };
      const put = await fetch(session_uri, { method: "PUT", body: file });
      if (!put.ok) {
        errorKey = "errors.google_upload_failed";
        return;
      }
      await load(true);
    } catch {
      errorKey = "errors.google_upload_failed";
    } finally {
      uploading = false;
      input.value = "";
    }
  }

  let fileInput = $state<HTMLInputElement | null>(null);

  onMount(() => {
    void load();
  });
</script>

<div class="rounded-lg border border-border">
  <div class="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
    <button
      type="button"
      class="shrink-0 rounded-lg border border-border p-1.5 text-text-muted hover:border-brand disabled:opacity-40"
      onclick={goUp}
      disabled={crumbs.length < 2}
      aria-label={t("google.drive.up")}
      title={t("google.drive.up")}
    >
      <ChevronLeft size={13} aria-hidden="true" />
    </button>
    <nav class="min-w-0 flex-1 truncate text-sm" aria-label={t("google.drive.breadcrumb")}>
      {#each crumbs as crumb, index (index)}
        {#if index > 0}<span class="text-text-muted">/</span>{/if}
        <button
          type="button"
          class="max-w-40 truncate align-bottom text-text hover:underline disabled:no-underline"
          onclick={() => jump(index)}
          disabled={index === crumbs.length - 1}
        >
          {crumb.name ?? listing?.folder?.name ?? t("google.drive.root")}
        </button>
      {/each}
    </nav>
    {#if canWrite}
      <input
        bind:this={fileInput}
        type="file"
        class="hidden"
        onchange={(e) => upload(e.currentTarget)}
      />
      <button
        type="button"
        class="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:border-brand disabled:opacity-50"
        onclick={toggleCreateFolder}
        disabled={!listing?.folder?.id}
      >
        <FolderPlus size={13} aria-hidden="true" />
        {t("google.drive.new_folder")}
      </button>
      <button
        type="button"
        class="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:border-brand disabled:opacity-50"
        onclick={() => fileInput?.click()}
        disabled={uploading || !listing?.folder?.id}
      >
        <Upload size={13} aria-hidden="true" />
        {uploading ? t("google.drive.uploading") : t("google.drive.upload")}
      </button>
    {/if}
    <button
      type="button"
      class="rounded-lg border border-border p-1.5 text-text-muted hover:border-brand"
      onclick={() => load(true)}
      aria-label={t("google.drive.refresh")}
    >
      <RefreshCw size={13} aria-hidden="true" />
    </button>
  </div>

  {#if canWrite && creatingFolder}
    <!-- Inline "new folder" row: create inside the folder currently shown. -->
    <form
      class="flex items-center gap-2 border-b border-border bg-surface px-3 py-2"
      onsubmit={(e) => {
        e.preventDefault();
        void createFolder();
      }}
    >
      <FolderPlus size={14} class="shrink-0 text-text-muted" aria-hidden="true" />
      <input
        bind:this={folderNameInput}
        bind:value={newFolderName}
        type="text"
        maxlength="255"
        placeholder={t("google.drive.folder_name")}
        aria-label={t("google.drive.folder_name")}
        class="min-w-0 flex-1 rounded-lg border border-border px-2.5 py-1 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      />
      <button
        type="submit"
        class="shrink-0 rounded-lg bg-brand px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        disabled={savingFolder || !newFolderName.trim()}
      >
        {savingFolder ? t("common.saving") : t("google.drive.create_folder")}
      </button>
      <button
        type="button"
        class="shrink-0 rounded-lg border border-border px-3 py-1 text-xs font-medium text-text hover:border-brand"
        onclick={toggleCreateFolder}
      >
        {t("common.cancel")}
      </button>
    </form>
  {/if}

  {#if loading && !listing}
    <p class="px-3 py-4 text-sm text-text-muted">{t("common.loading")}</p>
  {:else if errorKey}
    <p class="px-3 py-4 text-sm text-text-muted">{t(errorKey)}</p>
  {:else if listing}
    {#if listing.items.length === 0}
      <p class="px-3 py-4 text-sm text-text-muted">{t("google.drive.empty_folder")}</p>
    {:else}
      <ul class="max-h-72 divide-y divide-border overflow-y-auto">
        {#each listing.items as item (item.id)}
          {@const kind = driveKind(item.mime_type, item.is_folder)}
          {@const KindIcon = kind.icon}
          <li class="flex items-center gap-2 px-3 py-2">
            <KindIcon size={15} class="shrink-0 text-text-muted" aria-hidden="true" />
            {#if item.is_folder}
              <button
                type="button"
                class="min-w-0 flex-1 truncate text-left text-sm text-text hover:underline"
                onclick={() => open(item)}
              >
                {item.name}
              </button>
            {:else if item.web_view_link}
              <!-- A file's name opens it in Drive (#150) — the icon column already says what
                   it is; making people hunt for the tiny external-link icon was the bug. -->
              <a
                href={item.web_view_link}
                target="_blank"
                rel="noopener noreferrer"
                class="min-w-0 flex-1 truncate text-sm text-text hover:underline"
              >
                {item.name}
              </a>
            {:else}
              <span class="min-w-0 flex-1 truncate text-sm text-text">{item.name}</span>
            {/if}
            {#if item.modified_at}
              <span class="hidden shrink-0 text-xs tabular-nums text-text-muted sm:inline">
                {fmtNumericDate(item.modified_at.slice(0, 10))}
              </span>
            {/if}
            {#if canWrite}
              <!-- Link this file/folder to the record the panel hangs off. -->
              <form method="POST" action="?/linkDriveFile" use:enhance>
                <input type="hidden" name="entity_type" value={entityType} />
                <input type="hidden" name="entity_id" value={entityId} />
                <input type="hidden" name="drive_file_id" value={item.id} />
                <button
                  type="submit"
                  class="rounded p-1 text-text-muted hover:text-brand"
                  aria-label={t("google.drive.link_file")}
                  title={t("google.drive.link_file")}
                >
                  <Link2 size={14} aria-hidden="true" />
                </button>
              </form>
            {/if}
            {#if item.web_view_link}
              <a
                href={item.web_view_link}
                target="_blank"
                rel="noopener noreferrer"
                class="rounded p-1 text-text-muted hover:text-brand"
                aria-label={t("google.drive.open_in_drive")}
                title={t("google.drive.open_in_drive")}
              >
                <ExternalLink size={14} aria-hidden="true" />
              </a>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</div>
