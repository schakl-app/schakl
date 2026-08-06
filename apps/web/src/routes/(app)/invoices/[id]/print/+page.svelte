<script lang="ts">
  /**
   * The print surface: the rendered document plus a toolbar.
   *
   * The document arrives from the API as HTML — the same page `/pdf` prints — so this asks
   * the *frame* to print rather than the window. That prints the document's own `@page` rules
   * (A4, its margins, its running page numbers) instead of a screenshot of the app around it,
   * which is why the `@media print` block that used to hide the shell here is gone: nothing
   * of the shell reaches the printer any more.
   *
   * The server-rendered PDF is the better artefact and the one the send path attaches; this
   * page stays for whoever wants their own printer's dialog.
   */
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import DocumentFrame from "$lib/core/ui/DocumentFrame.svelte";

  let { data } = $props();

  const invoice = $derived(data.invoice);
  let frame = $state<ReturnType<typeof DocumentFrame> | null>(null);
</script>

<svelte:head>
  <title>{pageTitle(`${t("invoicing.kind.invoice")} ${invoice.number ?? ""}`)}</title>
</svelte:head>

<div class="mb-4 flex items-center gap-3">
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => frame?.print()}>{t("invoicing.action.print")}</button
  >
</div>

<DocumentFrame
  bind:this={frame}
  src="/invoices/{invoice.id}/preview"
  version={invoice.updated_at}
  title={`${t("invoicing.kind.invoice")} ${invoice.number ?? ""}`}
  class="mx-auto max-w-4xl"
/>
