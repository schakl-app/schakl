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
   * - **An upload links itself to the record it was uploaded from** (#328), by the same
   *   same-origin POST the ＋ on a row makes. The listing is live and the link list is what
   *   survives the page: without this, a file uploaded from a task's panel was attached to
   *   nothing and was gone from the record the moment you navigated away. Unconditional on
   *   purpose — "it landed in this record's own folder" is not the same fact as "this record
   *   has this file", and only the second one is still true tomorrow.
   *
   * In **pick mode** (`pick`) the same browser chooses a *folder* instead of linking files:
   * every folder row offers "choose", and so does the folder you are standing in — the folder
   * you want is usually the one you just navigated into, not one you can see listed.
   *
   * **Searching is the API's job** (#336). The listing is one page of 100 items, so a filter
   * box over `listing.items` would answer "geen resultaten" for a file that is merely 101st
   * alphabetically — the truncation-that-looks-like-it-worked §17 already names. `q` therefore
   * goes to Drive, this folder only, and the header says in words what is on screen. Where the
   * page really is a prefix of the folder, the list says so rather than presenting as complete.
   *
   * **A file's name links the file** (#336, reversing #150 for files). You are in this browser
   * because you came to attach something; opening it in Drive is the incidental act and the row
   * already carries ↗ for it. The name submits the *same* `?/linkDriveFile` form the 🔗 button
   * posts — one action, one code path — via the `form=` attribute, so the row keeps its flex
   * layout instead of being wrapped in a form. Where there is no link action to take (`pick`
   * mode, or a caller who cannot write) the name falls back to opening Drive: a control that
   * always refuses is worse than no control (#253).
   *
   * **A file can be removed from where it was uploaded** (#394). This is where uploads happen
   * and therefore where upload mistakes are noticed, so every row carries the same ⋯ item the
   * panel's link list does: *Verwijderen uit Drive*, which bins the file itself (30 days,
   * recoverable) and drops every link naming it. It posts the same-origin DELETE this browser
   * already uses for its other live acts, and a refusal — Drive's, or "deze map is niet leeg" —
   * is shown as its own strip **above the list rather than instead of it**: the list is what
   * tells you which file you just failed to remove.
   *
   * **Host contract:** the page exposes `?/linkDriveFile`, and `?/setDriveFolder` when the
   * host renders this in pick mode (spread `driveActions`).
   */
  import {
    Check,
    ChevronLeft,
    ExternalLink,
    FolderPlus,
    Link2,
    RefreshCw,
    Search,
    Trash2,
    Upload,
  } from "@lucide/svelte";
  import { onMount, untrack } from "svelte";

  import { enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import { fmtNumericDate } from "$lib/core/format";
  import { t, tn } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";

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
    /** The term this list answers — the API's echo, never what is in the box right now. */
    query: string | null;
    /** Drive had a page two nobody follows: this list is a prefix of the folder. */
    truncated: boolean;
  }

  let {
    rootFolderId = null,
    entityType,
    entityId,
    canWrite = false,
    pick = false,
    onpicked,
    reloadToken = 0,
  }: {
    /** Where browsing starts; null = the org's client-folders parent. */
    rootFolderId?: string | null;
    /** The record rows get linked to via the host's `?/linkDriveFile` action. */
    entityType: string;
    entityId: string;
    canWrite?: boolean;
    /** Choose a folder for the record (`?/setDriveFolder`) instead of linking files. */
    pick?: boolean;
    /** Fired once a folder was actually chosen, so the host can leave pick mode. */
    onpicked?: () => void;
    /**
     * Bumped by the host when something else on the page changed this folder — today, a file
     * binned from the link list beside us. The listing is live and belongs to no `load`, so an
     * `invalidateAll` cannot reach it, and a browser still showing a file that no longer exists
     * is the exact fault #394 set out to fix, one panel over.
     */
    reloadToken?: number;
  } = $props();

  // svelte-ignore state_referenced_locally
  let seenToken = reloadToken;

  // Only a *successful* pick closes the picker: a refused one (a member without
  // `google.drive.manage` re-pointing a folder) must leave the browser where it stands.
  const picked =
    () =>
    async ({ result, update }: { result: { type: string }; update: () => Promise<void> }) => {
      await update();
      if (result.type === "success") onpicked?.();
    };

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
  // A refusal from an *act* on a row, kept apart from `errorKey`: that one means the listing
  // itself could not be read, and blanking the list is the honest answer only for that.
  let actionErrorKey = $state("");
  let trashTarget = $state<BrowseItem | null>(null);
  let confirmTrash = $state(false);
  let trashing = $state(false);
  // What is typed in the box; `listing.query` is what the list on screen actually answers.
  let search = $state("");
  let searchTimer: ReturnType<typeof setTimeout> | undefined;

  const current = $derived(crumbs[crumbs.length - 1]);
  const searching = $derived(!!listing?.query);

  // Read off the *response*, never off the box: with a debounce in between, the two disagree
  // for a moment, and the sentence has to describe the rows underneath it.
  const searchSummary = $derived.by(() => {
    if (!listing?.query) return "";
    const params = {
      count: listing.items.length,
      term: listing.query,
      folder: listing.folder.name ?? t("google.drive.root"),
    };
    if (listing.items.length === 0) return t("google.drive.search_none", params);
    return tn("google.drive.search_results", listing.items.length, params);
  });

  async function load(refresh = false) {
    loading = true;
    errorKey = "";
    const params = new URLSearchParams();
    if (current.id) params.set("folder_id", current.id);
    if (search.trim()) params.set("q", search.trim());
    if (refresh) params.set("refresh", "true");
    // Which crumb this response will describe, captured before the round trip: a descent that
    // happens while it is in flight must not be named by the folder we were standing in.
    const target = crumbs.length - 1;
    const targetId = current.id;
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
      // Name the crumb from the listing that *is* it. The root is the one crumb created without a
      // name — it cannot have one before the first read — and the render used to resolve that
      // `null` against whatever folder was on screen, so descending into the client folder drew
      // "H2Booster / H2Booster" and the crumb that walks back up was labelled with the folder you
      // were already in (#363). A name is written once and never re-read.
      const name = listing.folder.name;
      if (name && crumbs[target] && crumbs[target].id === targetId && !crumbs[target].name) {
        crumbs = crumbs.map((crumb, index) => (index === target ? { ...crumb, name } : crumb));
      }
    } catch {
      errorKey = "errors.google_drive_unavailable";
      listing = null;
    } finally {
      loading = false;
    }
  }

  // A search belongs to the folder it was typed in, so descending or jumping drops it —
  // otherwise you arrive in a folder already filtered by a term you typed somewhere else.
  function open(item: BrowseItem) {
    if (!item.is_folder) return;
    crumbs = [...crumbs, { id: item.id, name: item.name }];
    clearSearch(false);
    void load();
  }

  function jump(index: number) {
    crumbs = crumbs.slice(0, index + 1);
    clearSearch(false);
    void load();
  }

  /** Debounced so typing costs one Drive round-trip, not one per keystroke. */
  function onSearchInput() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => void load(), 250);
  }

  function clearSearch(reload = true) {
    clearTimeout(searchTimer);
    search = "";
    if (reload) void load();
  }

  /**
   * A crumb's label. Only the crumb we are *standing on* may borrow the listing's name — that is
   * the one case where "we don't know it yet" and "it is the folder on screen" are the same
   * sentence. An ancestor borrowing it renders somewhere else's name (#363).
   */
  function crumbLabel(crumb: { name: string | null }, index: number): string {
    if (crumb.name) return crumb.name;
    if (index === crumbs.length - 1 && listing?.folder?.name) return listing.folder.name;
    return t("google.drive.root");
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

  /**
   * Attach a just-uploaded file to the record this browser hangs off. The upload itself has
   * already succeeded by the time this runs, so a refusal here reports *that* — telling
   * someone their upload failed when the file is sitting in Drive sends them to look for a
   * problem that isn't there.
   */
  async function linkUploaded(driveFileId: string) {
    try {
      const response = await fetch("/api/v1/google/drive/links", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({
          entity_type: entityType,
          entity_id: entityId,
          drive_file_id: driveFileId,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        errorKey = body?.error?.message ?? "errors.google_drive_link_failed";
        return;
      }
      // The link list is page data (the panel's SSR load), so it only redraws on an
      // invalidation — the same one `use:enhance` performs for the ＋ on a row.
      await invalidateAll();
    } catch {
      errorKey = "errors.google_drive_link_failed";
    }
  }

  /**
   * Bin the file itself. Drive's permissions answer (the API acts as this user), so a refusal
   * here is Google's own and is reported as such; a non-empty folder is refused by the API
   * before anything is touched. The dialog closes either way — its own backdrop would hide
   * the sentence explaining what happened.
   */
  async function trash() {
    const item = trashTarget;
    if (!item || trashing) return;
    trashing = true;
    actionErrorKey = "";
    try {
      const response = await fetch(`/api/v1/google/drive/files/${encodeURIComponent(item.id)}`, {
        method: "DELETE",
        headers: { accept: "application/json" },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        actionErrorKey = body?.error?.message ?? "errors.google_drive_unavailable";
        return;
      }
      // The listing is live; the record's link list is page data, and the file may have been
      // linked to this record or to another one entirely — so both are refreshed.
      await invalidateAll();
      await load(true);
    } catch {
      actionErrorKey = "errors.google_drive_unavailable";
    } finally {
      trashing = false;
      confirmTrash = false;
      trashTarget = null;
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
        // The API's own key when it has one: minting the session is where "reconnect your
        // account" and "the Drive API is off" surface, and both are actionable where the
        // generic upload failure is not.
        const body = await session.json().catch(() => null);
        errorKey = body?.error?.message ?? "errors.google_upload_failed";
        return;
      }
      const { session_uri } = (await session.json()) as { session_uri: string };
      const put = await fetch(session_uri, { method: "PUT", body: file });
      if (!put.ok) {
        errorKey = "errors.google_upload_failed";
        return;
      }
      // The completed resumable session answers with the file resource; its id is what the
      // record gets attached to. A body we cannot read is not an upload failure — the bytes
      // are in Drive and the listing below will show them.
      const uploaded = (await put.json().catch(() => null)) as { id?: string } | null;
      if (uploaded?.id) await linkUploaded(uploaded.id);
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

  // The body is untracked: `load` reads (and writes) `listing`, `crumbs` and `search`, so a
  // tracked call would re-run itself on its own result.
  $effect(() => {
    const token = reloadToken;
    untrack(() => {
      if (token === seenToken) return;
      seenToken = token;
      void load(true);
    });
  });
</script>

<!-- The whole browser is the drop target: what a dragged file is aimed at is the folder you
     are looking at, not the toolbar button that would have opened a dialog for it. -->
<div
  class="rounded-lg border border-border"
  use:filedrop={{
    input: () => fileInput,
    // `pick` for the same reason the Upload button hides in it: while you are choosing a
    // folder, a dropped file would land in whichever one you happen to be passing through.
    // `searching` for the same reason again: a result set is not a folder to drop into.
    disabled: pick || searching || !canWrite || uploading || !listing?.folder?.id,
  }}
>
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
          {crumbLabel(crumb, index)}
        </button>
      {/each}
    </nav>
    <!-- Server-side, this folder only (#336). A box that filtered the array below it would be
         a worse answer than none: the array is one capped page. -->
    <div class="relative shrink-0">
      <Search
        size={13}
        class="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-muted"
        aria-hidden="true"
      />
      <input
        bind:value={search}
        type="search"
        maxlength="100"
        oninput={onSearchInput}
        onkeydown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            clearSearch();
          }
        }}
        placeholder={t("google.drive.search_placeholder")}
        aria-label={t("google.drive.search_placeholder")}
        class="w-36 rounded-lg border border-border py-1 pl-7 pr-2 text-xs outline-none focus:border-brand focus:ring-1 focus:ring-brand sm:w-44"
      />
    </div>
    {#if pick && listing?.folder?.id}
      <!-- The folder you are standing in. Navigating into "Klanten/Acme" and then hunting for
           an "Acme" row that is one level up was the whole trap this avoids. -->
      <form method="POST" action="?/setDriveFolder" use:enhance={picked}>
        <input type="hidden" name="entity_type" value={entityType} />
        <input type="hidden" name="entity_id" value={entityId} />
        <input type="hidden" name="drive_file_id" value={listing.folder.id} />
        <button
          type="submit"
          class="inline-flex items-center gap-1 rounded-lg bg-brand px-2.5 py-1 text-xs font-medium text-white hover:opacity-90"
        >
          <Check size={13} aria-hidden="true" />
          {t("google.drive.choose_this_folder")}
        </button>
      </form>
    {/if}
    {#if canWrite && !searching}
      <!-- Hidden while filtered: uploading into a search result set is not a thing, and a new
           folder would be created in a list that is not a folder. -->
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
      {#if !pick}
        <!-- Picking a folder is not the moment to drop a file into whatever you are passing. -->
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-text hover:border-brand disabled:opacity-50"
          onclick={() => fileInput?.click()}
          disabled={uploading || !listing?.folder?.id}
        >
          <Upload size={13} aria-hidden="true" />
          {uploading ? t("google.drive.uploading") : t("google.drive.upload")}
        </button>
        <span class="hidden text-xs text-text-muted sm:inline">{t("common.drop_hint")}</span>
      {/if}
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

  {#if canWrite && creatingFolder && !searching}
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

  {#if actionErrorKey}
    <!-- Above the list, never instead of it: the list is what names the file that stayed. -->
    <p class="border-b border-border bg-surface px-3 py-2 text-sm text-text">
      {t(actionErrorKey)}
    </p>
  {/if}

  {#if loading && !listing}
    <p class="px-3 py-4 text-sm text-text-muted">{t("common.loading")}</p>
  {:else if errorKey}
    <p class="px-3 py-4 text-sm text-text-muted">{t(errorKey)}</p>
  {:else if listing}
    {#if listing.query}
      <!-- A list that is silently a subset of a folder tells the same lie the cap does, so
           the header states the term, the count and the folder it searched. -->
      <p class="border-b border-border px-3 py-1.5 text-xs text-text-muted">{searchSummary}</p>
    {/if}
    {#if listing.truncated}
      <p class="border-b border-border px-3 py-1.5 text-xs text-text-muted">
        {t("google.drive.truncated")}
      </p>
    {/if}
    {#if listing.items.length === 0}
      {#if !listing.query}
        <p class="px-3 py-4 text-sm text-text-muted">{t("google.drive.empty_folder")}</p>
      {/if}
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
            {:else if canWrite && !pick}
              <!-- The name *is* the link action (#336): same form, same action, same code path
                   as the 🔗 button further along the row, associated by `form=` so the row
                   keeps its layout. ↗ at the end is now the only thing that opens Drive. -->
              <button
                type="submit"
                form={`drive-link-${item.id}`}
                class="group flex min-w-0 flex-1 cursor-pointer items-center gap-1 text-left text-sm text-text hover:underline"
                aria-label={t("google.drive.link_file")}
                title={t("google.drive.link_file")}
              >
                <span class="min-w-0 truncate">{item.name}</span>
                <Link2
                  size={12}
                  class="shrink-0 text-brand opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
                  aria-hidden="true"
                />
              </button>
            {:else if item.web_view_link}
              <!-- Nothing to link here (pick mode, or a caller who cannot write), so the name
                   keeps #150's behaviour: open it in Drive. A control that always refuses is
                   worse than no control (#253). -->
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
            {#if pick}
              {#if item.is_folder}
                <!-- Choose this folder without descending into it first. -->
                <form method="POST" action="?/setDriveFolder" use:enhance={picked}>
                  <input type="hidden" name="entity_type" value={entityType} />
                  <input type="hidden" name="entity_id" value={entityId} />
                  <input type="hidden" name="drive_file_id" value={item.id} />
                  <button
                    type="submit"
                    class="rounded px-2 py-1 text-xs font-medium text-brand hover:underline"
                  >
                    {t("google.drive.choose_folder")}
                  </button>
                </form>
              {/if}
            {:else if canWrite}
              <!-- Link this file/folder to the record the panel hangs off. The row's name
                   button submits this very form (`form={id}`) — one write path, not two. -->
              <form id={`drive-link-${item.id}`} method="POST" action="?/linkDriveFile" use:enhance>
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
            {#if canWrite && !pick}
              <!-- The same ⋯ item the panel's link list carries (#394). Destructive, so it
                   lives in the overflow menu and confirms — never a naked icon in the row. -->
              <ActionsMenu
                compact
                items={[
                  {
                    label: t("google.drive.trash"),
                    icon: Trash2,
                    danger: true,
                    onclick: () => {
                      actionErrorKey = "";
                      trashTarget = item;
                      confirmTrash = true;
                    },
                  },
                ]}
              />
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</div>

<!-- A callback confirm rather than `ConfirmDialog`: this browser writes through same-origin
     fetches (the listing is live and belongs to no page load), and `ConfirmDialog` posts a
     form action. Same wording, same red button. -->
<Modal bind:open={confirmTrash} title={t("google.drive.trash_title")}>
  <p class="text-sm text-text-muted">
    {trashTarget?.is_folder
      ? t("google.drive.trash_folder_message")
      : t("google.drive.trash_message")}
  </p>
  <p class="mt-3 truncate rounded-lg bg-surface px-3 py-2 text-sm text-text">
    {trashTarget?.name}
  </p>
  <div class="mt-5 flex justify-end gap-2">
    <button
      type="button"
      class="rounded-lg border border-border px-4 py-2 text-sm text-text"
      onclick={() => (confirmTrash = false)}>{t("common.cancel")}</button
    >
    <Button type="button" variant="danger" loading={trashing} onclick={() => void trash()}>
      {t("google.drive.trash")}
    </Button>
  </div>
</Modal>
