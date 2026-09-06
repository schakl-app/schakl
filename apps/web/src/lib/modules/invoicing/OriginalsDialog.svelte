<script lang="ts">
  /**
   * A zip of the PDFs an imported back catalogue was sent as, matched by invoice number
   * (docs/INVOICING.md, "Bringing the back catalogue in").
   *
   * Its own dialog rather than a step of the import wizard: the spreadsheet and the archive
   * come from different places (the ledger export, a mail folder) and rarely on the same day,
   * and the report it answers with is a different shape from a row report. One component with
   * two hosts — the Facturen list, where it sits beside Importeren, and Instellingen →
   * Facturatie, where the migration is *explained* — so the two cannot drift apart.
   *
   * Gated by its hosts on the write key the API route declares (`invoicing.invoice.write`) —
   * no bulk permission, because attaching forty PDFs you may each attach is the same act
   * repeated (§18). The host page supplies the `originals` action; the API answers a report
   * (attached, already attached, unmatched, ambiguous, not a PDF) rather than refusing the
   * archive over one stray file, and this prints it whole.
   */
  import { enhance } from "$app/forms";

  import type { components } from "$lib/core/api/schema";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import { filedrop } from "$lib/core/ui/filedrop";
  import Modal from "$lib/core/ui/Modal.svelte";

  type Report = components["schemas"]["OriginalsBatchReport"];

  let {
    open = $bindable(false),
    action = "?/originals",
    report = null,
    error = null,
  }: {
    open?: boolean;
    /** The host page's form action that relays the zip to the API. */
    action?: string;
    /** The host page's `form?.originals`. */
    report?: Report | null;
    /** The host page's `form?.originalsError`. */
    error?: string | null;
  } = $props();

  const busy = new InFlight();
  let input = $state<HTMLInputElement | null>(null);
  let dropError = $state<string | null>(null);

  const counts = $derived(
    report
      ? (
          [
            ["matched", report.matched?.length ?? 0],
            ["already_attached", report.already_attached?.length ?? 0],
            ["unmatched", report.unmatched?.length ?? 0],
            ["ambiguous", report.ambiguous?.length ?? 0],
            ["not_pdf", report.not_pdf?.length ?? 0],
          ] as const
        ).filter(([, count]) => count > 0)
      : [],
  );
  const failed = $derived(
    report
      ? [...(report.unmatched ?? []), ...(report.ambiguous ?? []), ...(report.not_pdf ?? [])]
      : [],
  );
</script>

<Modal bind:open title={t("invoicing.originals.title")}>
  <form
    method="POST"
    {action}
    enctype="multipart/form-data"
    use:enhance={busy.keep("originals")}
    class="space-y-3"
  >
    <p class="text-sm text-text-muted">{t("invoicing.originals.hint")}</p>
    <div
      class="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-border p-3"
      use:filedrop={{
        input: () => input,
        disabled: busy.active,
        onerror: (key) => (dropError = key),
      }}
    >
      <input
        bind:this={input}
        type="file"
        name="file"
        accept="application/zip,.zip"
        required
        class="text-sm text-text"
        onchange={() => (dropError = null)}
      />
      <span class="text-xs text-text-muted">{t("common.drop_hint")}</span>
    </div>
    {#if dropError || error}
      <p class="text-sm text-red-600 dark:text-red-400">{t(dropError ?? error ?? "")}</p>
    {/if}
    {#if report}
      <!-- The whole report, counts first and then every file that did not land, by name:
           "3 gekoppeld" alone leaves the reader guessing which of the forty were not. -->
      <div class="rounded-lg bg-surface p-3 text-sm" data-testid="originals-report">
        {#if counts.length === 0}
          <p class="text-text-muted">{t("invoicing.originals.nothing")}</p>
        {:else}
          <ul class="space-y-0.5">
            {#each counts as [key, count] (key)}
              <li class={key === "matched" ? "text-text" : "text-text-muted"}>
                {t(`invoicing.originals.${key}`, { count })}
              </li>
            {/each}
          </ul>
        {/if}
        {#each failed as name (name)}
          <p class="mt-1 truncate font-mono text-xs text-text-muted">{name}</p>
        {/each}
      </div>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (open = false)}>{t("common.close")}</button
      >
      <Button loading={busy.is("originals")} disabled={busy.active}>
        {t("invoicing.originals.upload")}
      </Button>
    </div>
  </form>
</Modal>
