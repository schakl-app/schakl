<script lang="ts">
  /**
   * The import wizard (issue #77): **source → mapping → preview → done**, one form, one modal.
   *
   * One form for all three steps because the source has to survive them: the browser's own
   * file input still holds the file, so every step re-posts it and nothing is staged
   * server-side (no upload table, no Redis blob, nothing to expire or clean up). The
   * fingerprint from the inspect step rides along and the API refuses a mismatch, which is
   * what closes the hole that trade would otherwise open — a mapping is positional, so
   * applying it to a *different* file writes the wrong columns into the right fields with
   * every row valid.
   *
   * There is no stepper component: four `{#if}` blocks in one form is the whole state machine,
   * and a shared stepper that exists for one caller is a component to maintain, not a pattern.
   *
   * One primary action per step (docs/UX.md): "Volgende" reads the file, "Controleren" is the
   * dry run, "Importeren" is the save — and it only arms after a clean preview of the mapping
   * as it stands. The server re-validates regardless; the arming is courtesy, not the gate.
   */
  import { enhance } from "$app/forms";

  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  import MappingTable from "./MappingTable.svelte";
  import { keyLabel } from "./labels";
  import type { ImpexColumns, ImportReport, InspectReport } from "./actions.server";

  let {
    open = $bindable(false),
    action = "?/impex",
    locale = "nl",
    report = null,
    inspect = null,
    columns = null,
    error = null,
  }: {
    open?: boolean;
    action?: string;
    locale?: string;
    /** The page's form result for this action (`form?.impex`). */
    report?: ImportReport | null;
    inspect?: InspectReport | null;
    columns?: ImpexColumns | null;
    /** Top-level failure key for this action (`form?.impexError`). */
    error?: string | null;
  } = $props();

  const SHOWN_ERRORS = 10;

  const busy = new InFlight();
  /** Results belong to this modal session and to the picked source. */
  let submitted = $state(false);
  let stale = $state(false);
  let mapping = $state<Record<number, string>>({});
  let matchKey = $state("");
  let sheet = $state("");
  let hasHeader = $state(true);
  let pasted = $state("");
  let fileName = $state("");
  /**
   * The inspect result has to outlive the submit that produced it: `preview` and `commit`
   * return an import report and nothing else, so the incoming prop goes `null` and the
   * mapping table would vanish mid-wizard. Held here rather than re-inspected on every step —
   * re-reading the file to redraw a table the user is already looking at is a wasted round
   * trip on every click (docs/PERFORMANCE.md).
   */
  let held = $state<InspectReport | null>(null);
  let heldColumns = $state<ImpexColumns["columns"]>([]);

  $effect(() => {
    if (open) reset();
  });

  function reset() {
    submitted = false;
    stale = false;
    mapping = {};
    matchKey = "";
    sheet = "";
    hasHeader = true;
    pasted = "";
    fileName = "";
    held = null;
    heldColumns = [];
  }

  /** Picking another file or editing the paste voids what an earlier step reported. */
  function invalidate() {
    stale = true;
    held = null;
  }

  // A fresh inspect result: hold it, and seed the mapping from the API's suggestions so the
  // user confirms or corrects a filled-in table rather than an empty one. Only fires when an
  // inspect actually returned, so a later preview never overwrites a hand-made correction.
  $effect(() => {
    if (!inspect) return;
    held = inspect;
    heldColumns = columns?.columns ?? [];
    const next: Record<number, string> = {};
    for (const column of inspect.columns) next[column.index] = column.suggested_key ?? "";
    mapping = next;
    matchKey = inspect.suggested_match_key ?? "";
    sheet = inspect.sheet ?? "";
  });

  const live = $derived(submitted && !stale);
  const source = $derived(live ? held : null);
  const current = $derived(live ? report : null);
  const catalog = $derived(live ? heldColumns : []);
  const step = $derived(current?.applied ? "done" : source ? "map" : "source");
  const canCommit = $derived(
    current != null && !current.applied && current.error_count === 0 && !busy.active,
  );

  const unmapped = $derived(
    source ? source.columns.filter((column) => !mapping[column.index]).length : 0,
  );

  /**
   * Singular/plural as a key pair rather than an ICU plural: this project's Paraglide setup
   * does not parse `{n, plural, …}` — it compiles the whole construct into a broken message —
   * so a plural written the ICU way silently ships as garbage. Two keys and a ternary is the
   * shape that actually works here.
   */
  function plural(key: string, count: number, params: Record<string, unknown>): string {
    return t(count === 1 ? `${key}_one` : key, params);
  }
</script>

<Modal bind:open title={t("impex.import_title")} size="lg">
  <form
    method="POST"
    {action}
    enctype="multipart/form-data"
    use:enhance={busy.wrap(
      ({ submitter }) => submitter?.getAttribute("value") ?? "inspect",
      () =>
        async ({ update }) => {
          submitted = true;
          stale = false;
          // Keep the picked source and the mapping standing: the next step posts them again.
          await update({ reset: false });
        },
    )}
  >
    <!-- Step 1 — where the data comes from. Always rendered: it holds the source for every
         later step, and hiding it behind an {#if} would drop the file input from the DOM. -->
    <div class:hidden={step !== "source"}>
      <label for="impex-file" class="mb-1 block text-sm font-medium text-text">
        {t("impex.file")}
      </label>
      <input
        id="impex-file"
        name="file"
        type="file"
        accept=".csv,.tsv,.txt,.xlsx,text/csv,text/tab-separated-values,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        onchange={(event) => {
          fileName = event.currentTarget.files?.[0]?.name ?? "";
          if (fileName) pasted = "";
          invalidate();
        }}
        class="w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-surface file:px-3 file:py-1 file:text-sm file:text-text"
      />
      <p class="mt-2 text-xs text-text-muted">{t("impex.file_hint")}</p>

      <div class="mt-4">
        <label for="impex-paste" class="mb-1 block text-sm font-medium text-text">
          {t("impex.paste")}
        </label>
        <textarea
          id="impex-paste"
          name="text"
          rows="4"
          bind:value={pasted}
          oninput={invalidate}
          disabled={Boolean(fileName)}
          placeholder={t("impex.paste_placeholder")}
          class="w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-xs text-text disabled:opacity-50"
        ></textarea>
        <p class="mt-1 text-xs text-text-muted">{t("impex.paste_hint")}</p>
      </div>

      <!-- The hidden "false" is what an unchecked box posts: a checkbox sends nothing at all,
           and "nothing" would otherwise be read as the default (true). -->
      <input type="hidden" name="has_header" value="false" />
      <label class="mt-3 flex items-center gap-2 text-sm text-text">
        <input type="checkbox" name="has_header" value="true" bind:checked={hasHeader} />
        {t("impex.has_header")}
      </label>
    </div>

    <!-- Step 2 — what each column of *their* file is. -->
    {#if step === "map" && source}
      <div class="mb-3 rounded-lg border border-border bg-surface p-3 text-xs text-text-muted">
        <p>
          {plural("impex.source_summary", source.rows, {
            format: source.source_format.toUpperCase(),
            rows: source.rows,
          })}{#if source.sheet}&nbsp;· {source.sheet}{/if}{#if source.encoding && source.encoding !== "utf-8-sig"}&nbsp;·
            {source.encoding}{/if}
        </p>
        {#if source.uncalculated_formulas > 0}
          <!-- Excel writes a formula plus its last cached result; a file generated by a
               library writes only the formula, and the cached result is what we can read.
               Those cells arrive empty, which is indistinguishable from deliberately blank
               unless we say so. -->
          <p class="mt-1 text-amber-700 dark:text-amber-400">
            {plural("impex.formula_warning", source.uncalculated_formulas, {
              count: source.uncalculated_formulas,
            })}
          </p>
        {/if}
        {#if unmapped > 0}
          <p class="mt-1">{plural("impex.mapping.unmapped", unmapped, { count: unmapped })}</p>
        {/if}
      </div>

      {#if (source.sheets ?? []).length > 1}
        <div class="mb-3 max-w-xs">
          <label for="impex-sheet" class="mb-1 block text-sm font-medium text-text">
            {t("impex.sheet")}
          </label>
          <select
            id="impex-sheet"
            name="sheet"
            bind:value={sheet}
            onchange={(event) => {
              // Re-read the file against the chosen worksheet. No submitter: the inspect
              // button belongs to the previous step and is no longer rendered, and a submit
              // without one is exactly what the action treats as an inspect.
              sheet = event.currentTarget.value;
              event.currentTarget.form?.requestSubmit();
            }}
            class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text"
          >
            {#each source.sheets ?? [] as name (name)}
              <option value={name} selected={name === source.sheet}>{name}</option>
            {/each}
          </select>
        </div>
      {/if}

      <MappingTable inspect={source} columns={catalog} {locale} bind:mapping bind:matchKey />
      <input type="hidden" name="fingerprint" value={source.fingerprint} />
    {/if}

    {#if live && error}
      <p class="mt-3 text-sm text-red-600 dark:text-red-400">{t(error)}</p>
    {/if}

    <!-- Step 3/4 — what the file would do, then what it did. -->
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
                <!-- The column is named the way the mapping step named it: a report that says
                     `client_number` after the user picked "Klantnummer" sends them hunting for
                     a column they already chose, under a name they never saw. -->
                {rowError.row === 0
                  ? t("impex.header_row")
                  : t("impex.row", { row: rowError.row })}{#if rowError.field}&nbsp;·
                  <span class="font-medium">{keyLabel(rowError.field, heldColumns, locale)}</span
                  >{/if}: {t(rowError.message_key)}
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
      {#if step === "source"}
        <Button name="mode" value="inspect" loading={busy.is("inspect")} disabled={busy.active}>
          {t("impex.read_source")}
        </Button>
      {:else if step === "map"}
        <Button
          variant="secondary"
          name="mode"
          value="preview"
          loading={busy.is("preview")}
          disabled={busy.active}
        >
          {t("impex.preview")}
        </Button>
        <Button name="mode" value="commit" loading={busy.is("commit")} disabled={!canCommit}>
          {t("impex.commit")}
        </Button>
        <!-- Back to the source step. The file input and the textarea were never unmounted (the
             source block is hidden, not removed — it has to keep holding the bytes for every
             later step), so this costs one flag and restores the picked source intact. Without
             it, "wrong file" meant closing the modal, which resets everything. -->
        <button
          type="button"
          class="rounded-lg px-3 py-2 text-sm text-text-muted hover:text-text"
          disabled={busy.active}
          onclick={() => (submitted = false)}
        >
          {t("impex.back")}
        </button>
      {/if}
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (open = false)}
      >
        {step === "done" ? t("common.close") : t("common.cancel")}
      </button>
    </div>
  </form>
</Modal>
