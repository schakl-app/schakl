<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import DocumentView from "$lib/modules/invoicing/DocumentView.svelte";

  let { data } = $props();

  const quote = $derived(data.quote);
  const template = $derived(data.templates.find((tpl) => tpl.id === quote.template_id) ?? null);
  const theme = $derived(page.data.theme);
</script>

<svelte:head>
  <title>{pageTitle(`${t("invoicing.quotes")} ${quote.number ?? ""}`)}</title>
</svelte:head>

<div class="print-hide mb-4 flex items-center justify-between gap-3">
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => window.print()}>{t("invoicing.action.print")}</button
  >
</div>

<DocumentView
  doc={quote}
  kind="quote"
  {template}
  seller={data.settings?.company_details ?? {}}
  brandName={theme?.brandName ?? ""}
  logoUrl={theme?.logoUrl ?? null}
  brandColor={theme?.primaryColor ?? "#4f46e5"}
/>

<style>
  @media print {
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
