<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import DocumentForm from "$lib/modules/invoicing/DocumentForm.svelte";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("invoicing.new_quote"))}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{t("invoicing.new_quote")}</h1>
</div>

<div class="max-w-4xl rounded-xl border border-border bg-surface-raised p-6">
  <DocumentForm
    kind="quote"
    action="?/create"
    companies={data.companies}
    companyDefinitions={data.companyDefinitions}
    contacts={data.contacts}
    taxRates={data.taxRates}
    products={data.products}
    templates={data.templates}
    settings={data.settings}
    locale={data.locale}
    {form}
  />
</div>
