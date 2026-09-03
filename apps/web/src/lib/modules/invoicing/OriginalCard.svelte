<script lang="ts">
  /**
   * The original PDF of an imported invoice (docs/INVOICING.md).
   *
   * Drawn only for an imported document — a native invoice *is* its own rendering and the API
   * refuses an original on one. What it shows is what the API guarantees: the file the client
   * received, its size, when it was attached, and the SHA-256 the API computed on arrival, so
   * "is this the real one" is a fingerprint the reader can compare rather than a promise.
   *
   * Attaching, replacing and removing are gated on the invoice write key the route declares
   * (`invoicing.invoice.write`), never on who the viewer is. The upload is one `<input>` the
   * button, the drop and the replace all land on (`filedrop`'s rule), submitting on change so
   * choosing a file *is* the act — there is nothing else on the form to fill in.
   */
  import { Download, Trash2, Upload } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtBytes, fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";

  interface Original {
    file_id: string;
    filename: string;
    size_bytes: number;
    sha256: string;
    uploaded_at: string;
  }

  let {
    invoiceId,
    original,
    canWrite,
    error = null,
  }: {
    invoiceId: string;
    original: Original | null | undefined;
    canWrite: boolean;
    error?: string | null;
  } = $props();

  const busy = new InFlight();
  let confirmRemove = $state(false);
  let dropError = $state<string | null>(null);
  let input = $state<HTMLInputElement | null>(null);
</script>

<div
  class="rounded-xl border border-border bg-surface-raised p-4"
  use:filedrop={{
    input: () => input,
    disabled: !canWrite || busy.active,
    onerror: (key) => (dropError = key),
  }}
>
  <h2 class="mb-1 text-sm font-semibold text-text">{t("invoicing.original.title")}</h2>
  {#if original}
    <p class="text-xs text-text-muted">{t("invoicing.original.hint")}</p>
    <a
      href="/invoices/{invoiceId}/pdf"
      class="mt-3 flex items-center gap-2 text-sm font-medium text-text hover:text-brand"
    >
      <Download class="size-4 shrink-0 text-text-muted" aria-hidden="true" />
      <span class="min-w-0 truncate">{original.filename}</span>
    </a>
    <p class="mt-0.5 text-xs text-text-muted">
      {fmtBytes(original.size_bytes)} ·
      {t("invoicing.original.uploaded_at", { date: fmtDateTime(original.uploaded_at) })}
    </p>
    <dl class="mt-3 text-xs">
      <dt class="text-text-muted">{t("invoicing.original.sha256")}</dt>
      <!-- Whole, never truncated: a fingerprint you can only read half of is decoration. -->
      <dd class="break-all font-mono text-text">{original.sha256}</dd>
    </dl>
  {:else}
    <p class="text-sm text-text-muted">{t("invoicing.original.empty")}</p>
  {/if}

  {#if canWrite}
    <form
      method="POST"
      action="?/attachOriginal"
      enctype="multipart/form-data"
      use:enhance={busy.keep("original")}
      class="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3"
    >
      <!-- `sr-only`, never `hidden`: a `display:none` control cannot take focus, and the
           upload would be unreachable by keyboard (docs/UX.md). -->
      <input
        bind:this={input}
        type="file"
        name="file"
        accept="application/pdf,.pdf"
        class="sr-only"
        id="original-file"
        onchange={(e) => {
          dropError = null;
          if (e.currentTarget.files?.length) e.currentTarget.form?.requestSubmit();
        }}
      />
      <label
        for="original-file"
        class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand {busy.active
          ? 'pointer-events-none opacity-60'
          : ''}"
      >
        <Upload class="size-4" aria-hidden="true" />
        {busy.is("original")
          ? t("common.loading")
          : original
            ? t("invoicing.original.replace")
            : t("invoicing.original.attach")}
      </label>
      <span class="text-xs text-text-muted">{t("common.drop_hint")}</span>
      {#if original}
        <button
          type="button"
          class="ml-auto inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/30"
          onclick={() => (confirmRemove = true)}
        >
          <Trash2 class="size-4" aria-hidden="true" />
          {t("invoicing.original.remove")}
        </button>
      {/if}
    </form>
    {#if dropError || error}
      <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(dropError ?? error ?? "")}</p>
    {/if}
  {/if}
</div>

<ConfirmDialog
  bind:open={confirmRemove}
  title={t("invoicing.original.remove")}
  message={t("invoicing.original.remove_confirm")}
  action="?/detachOriginal"
/>
