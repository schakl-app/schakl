<script lang="ts">
  /** The print surface: the rendered document plus a screen-only toolbar. `@media print`
   * hides the app shell, so the browser's Save-as-PDF produces a clean document — the
   * pragmatic PDF path until a server-side renderer ships (#207's stated follow-up). */
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import DocumentView from "$lib/modules/invoicing/DocumentView.svelte";

  let { data } = $props();

  const invoice = $derived(data.invoice);
  const template = $derived(data.templates.find((tpl) => tpl.id === invoice.template_id) ?? null);
  const theme = $derived(page.data.theme);
</script>

<svelte:head>
  <title>{pageTitle(`${t("invoicing.kind.invoice")} ${invoice.number ?? ""}`)}</title>
</svelte:head>

<div class="print-hide mb-4 flex items-center justify-between gap-3">
  <a href="/invoices/{invoice.id}" class="text-sm text-text-muted hover:text-text">←</a>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => window.print()}>{t("invoicing.action.print")}</button
  >
</div>

<DocumentView
  doc={invoice}
  kind="invoice"
  {template}
  seller={data.settings?.company_details ?? {}}
  brandName={theme?.brandName ?? ""}
  logoUrl={theme?.logoUrl ?? null}
  brandColor={theme?.primaryColor ?? "#4f46e5"}
/>

<style>
  @media print {
    /* Only the document leaves the printer: the shell (sidebar, header) and this page's
       own toolbar disappear, and the content column loses its padding. */
    :global(aside),
    :global(header),
    .print-hide {
      display: none !important;
    }
    :global(main) {
      padding: 0 !important;
    }
  }
</style>
