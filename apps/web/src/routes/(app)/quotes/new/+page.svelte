<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";
  import DocumentForm from "$lib/modules/invoicing/DocumentForm.svelte";

  let { data, form } = $props();

  // Inline-create from the contact picker (#115): "＋ … toevoegen" opens this dialog.
  let qcContactOpen = $state(false);
  let qcContactName = $state("");
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
    oncreatecontact={(name) => {
      qcContactName = name;
      qcContactOpen = true;
    }}
  />
</div>

<ContactQuickCreate
  bind:open={qcContactOpen}
  name={qcContactName}
  pickerSlot="contact"
  definitions={data.contactDefinitions}
  locale={data.locale}
  error={form?.qcError ?? null}
/>
