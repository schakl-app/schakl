<script lang="ts">
  /**
   * The CSV import flow (issue #77): pick a file → dry-run preview (counts + the first row
   * errors, numbered the way a spreadsheet user counts) → commit. One primary save per
   * surface (docs/UX.md): the preview is a check, "Importeren" is the save, and it only
   * arms after a clean preview of the file as picked — the server re-validates regardless.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import Modal from "$lib/core/ui/Modal.svelte";

  import type { components } from "$lib/core/api/schema";

  type ImportReport = components["schemas"]["ImportReport"];

  let {
    open = $bindable(false),
    action = "?/importCsv",
    report = null,
    error = null,
  }: {
    open?: boolean;
    action?: string;
    /** The page's form result for this action (`form?.impex`). */
    report?: ImportReport | null;
    /** Top-level failure key for this action (`form?.impexError`). */
    error?: string | null;
  } = $props();

  const SHOWN_ERRORS = 10;

  let submitting = $state(false);
  // Results belong to this modal session and to the picked file: reopening the modal or
  // picking another file voids what an earlier run reported.
  let submitted = $state(false);
  let stale = $state(false);

  $effect(() => {
    if (open) {
      submitted = false;
      stale = false;
    }
  });

  const current = $derived(submitted && !stale ? report : null);
  const canCommit = $derived(
    current != null && !current.applied && current.error_count === 0 && !submitting,
  );
</script>

<Modal bind:open title={t("impex.import_title")}>
  <form
    method="POST"
    {action}
    enctype="multipart/form-data"
    use:enhance={() => {
      submitting = true;
      return async ({ update }) => {
        submitting = false;
        submitted = true;
        stale = false;
        // Keep the picked file standing: the commit posts the same input again.
        await update({ reset: false });
      };
    }}
  >
    <label for="impex-file" class="mb-1 block text-sm font-medium text-text">
      {t("impex.file")}
    </label>
    <input
      id="impex-file"
      name="file"
      type="file"
      accept=".csv,text/csv"
      required
      onchange={() => (stale = true)}
      class="w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-surface file:px-3 file:py-1 file:text-sm file:text-text"
    />
    <p class="mt-2 text-xs text-text-muted">{t("impex.file_hint")}</p>

    {#if submitted && !stale && error}
      <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(error)}</p>
    {/if}

    {#if current}
      <div class="mt-3 rounded-lg border border-border bg-surface p-3 text-sm">
        {#if current.applied}
          <p class="font-medium text-text">
            {t("impex.applied", { creates: current.creates, updates: current.updates })}
          </p>
        {:else}
          <p class="text-text">
            {t("impex.preview_summary", {
              creates: current.creates,
              updates: current.updates,
              errors: current.error_count,
            })}
          </p>
        {/if}
        {#if current.errors.length > 0}
          <ul class="mt-2 space-y-1">
            {#each current.errors.slice(0, SHOWN_ERRORS) as rowError, index (index)}
              <li class="text-red-600 dark:text-red-400">
                {rowError.row === 0 ? t("impex.header_row") : t("impex.row", { row: rowError.row })}{#if rowError.field}&nbsp;·
                  <span class="font-mono text-xs">{rowError.field}</span>{/if}: {t(
                  rowError.message_key,
                )}
              </li>
            {/each}
          </ul>
          {#if current.error_count > SHOWN_ERRORS}
            <p class="mt-1 text-xs text-text-muted">
              {t("impex.more_errors", { count: current.error_count - SHOWN_ERRORS })}
            </p>
          {/if}
        {/if}
      </div>
    {/if}

    <div class="mt-4 flex flex-wrap gap-2">
      {#if !current?.applied}
        <button
          name="mode"
          value="preview"
          class="rounded-lg border border-border px-4 py-2 text-sm disabled:opacity-50"
          disabled={submitting}
        >
          {t("impex.preview")}
        </button>
        <button
          name="mode"
          value="commit"
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          disabled={!canCommit}
        >
          {t("impex.commit")}
        </button>
      {/if}
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (open = false)}
      >
        {current?.applied ? t("common.close") : t("common.cancel")}
      </button>
    </div>
  </form>
</Modal>
