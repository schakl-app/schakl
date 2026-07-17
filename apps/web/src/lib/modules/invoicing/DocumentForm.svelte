<script lang="ts">
  /**
   * The document editor (issue #207): one form for a new or draft invoice/quote. After
   * issue the money fields disappear (the API refuses them anyway — issued money is
   * immutable) and only process fields remain. One save button (docs/UX.md).
   */
  import { enhance } from "$app/forms";
  import { COMMON_CURRENCIES, otherCurrencies } from "$lib/core/currencies";
  import { getCurrency } from "$lib/core/currency";
  import { LOCALES, t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";

  import LinesEditor from "./LinesEditor.svelte";
  import type { EditableLine } from "./calc";
  import type { DocTemplate, Invoice, InvoicingSettings, Quote, TaxRate } from "./types";
  import type { components } from "$lib/core/api/schema";

  type FieldDefinition = components["schemas"]["CustomFieldDefinitionRead"];

  let {
    kind,
    doc = null,
    action,
    companies = [],
    companyDefinitions = [],
    contacts = [] as { id: string; name: string; company_ids: string[] }[],
    taxRates,
    templates,
    settings,
    locale,
    form,
    oncancel,
    initialCompanyId = "",
  }: {
    kind: "invoice" | "quote";
    doc?: Invoice | Quote | null;
    action: string;
    companies?: { id: string; name: string }[];
    companyDefinitions?: FieldDefinition[];
    contacts?: { id: string; name: string; company_ids: string[] }[];
    taxRates: TaxRate[];
    templates: DocTemplate[];
    settings: InvoicingSettings | null;
    locale: string;
    form: Record<string, unknown> | null;
    oncancel?: () => void;
    /** Preset client for a fresh document (the client page's "＋ nieuwe factuur"). */
    initialCompanyId?: string;
  } = $props();

  const isNew = $derived(doc === null);
  const locked = $derived(doc !== null && doc.status !== "draft");
  const orgCurrency = getCurrency();

  // Deliberate initial capture: the preset only seeds a fresh form.
  // svelte-ignore state_referenced_locally
  let companyId = $state(initialCompanyId);
  let createdCompanyId = $state("");
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  $effect(() => {
    const created = (form as { inlineCreated?: { slot: string; id: string } } | null)
      ?.inlineCreated;
    if (created?.slot === "company") createdCompanyId = created.id;
  });

  let currency = $state("");
  const effectiveCurrency = $derived(currency || doc?.currency || orgCurrency);

  let lines = $state<EditableLine[]>(
    (doc?.lines ?? []).map((line) => ({
      description: line.description,
      quantity: String(Number(line.quantity)),
      unit: line.unit ?? "",
      unit_price: String(Number(line.unit_price)),
      tax_rate_id: line.tax_rate_id ?? "",
    })),
  );
  // A new document starts with one line ready to type into.
  $effect(() => {
    if (isNew && lines.length === 0) {
      lines = [
        {
          description: "",
          quantity: "1",
          unit: "",
          unit_price: "",
          tax_rate_id: settings?.default_tax_rate_id ?? "",
        },
      ];
    }
  });

  // Bound state, not a one-way checked (docs/UX.md): the mark must survive hydration, and
  // the line calculations must follow the toggle live — a derived-only value did neither.
  // svelte-ignore state_referenced_locally
  let includeTax = $state(
    (doc?.prices_include_tax ?? settings?.prices_include_tax ?? false) as boolean,
  );
  const companyItems = $derived(companies.map((c) => ({ value: c.id, label: c.name })));
  const contactItems = $derived(
    contacts
      .filter((c) => {
        const target = createdCompanyId || companyId || doc?.company_id;
        return !target || c.company_ids.length === 0 || c.company_ids.includes(target);
      })
      .map((c) => ({ value: c.id, label: c.name })),
  );

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const FORM_ID = `doc-form-${kind}`;
</script>

<form
  id={FORM_ID}
  method="POST"
  {action}
  use:enhance={() =>
    ({ update }) => {
      void update({ reset: false });
    }}
  class="space-y-4"
>
  {#if isNew}
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="doc-company" class="mb-1 block text-sm font-medium text-text"
          >{t("invoicing.field.company")}</label
        >
        <Combobox
          items={companyItems}
          name="company_id"
          value={createdCompanyId || companyId}
          id="doc-company"
          placeholder={t("invoicing.field.company")}
          onselect={(v) => (companyId = v)}
          oncreate={(name) => {
            qcCompanyName = name;
            qcCompanyOpen = true;
          }}
        />
      </div>
      <div>
        <label for="doc-contact" class="mb-1 block text-sm font-medium text-text"
          >{t("invoicing.field.contact")}</label
        >
        <Combobox
          items={contactItems}
          name="contact_id"
          value=""
          id="doc-contact"
          placeholder={t("invoicing.field.contact")}
        />
      </div>
    </div>
  {:else if !locked}
    <div>
      <label for="doc-contact" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.field.contact")}</label
      >
      <Combobox
        items={contactItems}
        name="contact_id"
        value={doc?.contact_id ?? ""}
        id="doc-contact"
        placeholder={t("invoicing.field.contact")}
      />
    </div>
  {/if}

  <div class="grid gap-3 sm:grid-cols-3">
    <div>
      <label for="doc-issue-date" class="mb-1 block text-sm font-medium text-text"
        >{kind === "invoice"
          ? t("invoicing.field.issue_date")
          : t("invoicing.field.quote_date")}</label
      >
      <DateInput name="issue_date" id="doc-issue-date" value={doc?.issue_date ?? ""} />
    </div>
    <div>
      <label for="doc-deadline" class="mb-1 block text-sm font-medium text-text"
        >{kind === "invoice"
          ? t("invoicing.field.due_date")
          : t("invoicing.field.valid_until")}</label
      >
      <DateInput
        name={kind === "invoice" ? "due_date" : "valid_until"}
        id="doc-deadline"
        value={(kind === "invoice"
          ? (doc as Invoice | null)?.due_date
          : (doc as Quote | null)?.valid_until) ?? ""}
      />
    </div>
    <div>
      <label for="doc-reference" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.field.reference")}</label
      >
      <input id="doc-reference" name="reference" value={doc?.reference ?? ""} class={inputClass} />
    </div>
  </div>

  <div class="grid gap-3 sm:grid-cols-3">
    {#if !locked}
      <div>
        <label for="doc-currency" class="mb-1 block text-sm font-medium text-text"
          >{t("invoicing.field.currency")}</label
        >
        <select
          id="doc-currency"
          name="currency"
          class={inputClass}
          value={doc?.currency ?? orgCurrency}
          onchange={(e) => (currency = e.currentTarget.value)}
        >
          {#each COMMON_CURRENCIES as code (code)}
            <option value={code}>{code}</option>
          {/each}
          {#each otherCurrencies() as code (code)}
            <option value={code}>{code}</option>
          {/each}
        </select>
      </div>
    {/if}
    {#if effectiveCurrency !== orgCurrency}
      <div>
        <label for="doc-rate" class="mb-1 block text-sm font-medium text-text"
          >{t("invoicing.field.exchange_rate")}</label
        >
        <input
          id="doc-rate"
          name="exchange_rate"
          type="number"
          step="0.000001"
          min="0"
          value={doc?.exchange_rate ?? ""}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">
          {t("invoicing.field.exchange_rate_help", { currency: effectiveCurrency })}
        </p>
      </div>
    {/if}
    <div>
      <label for="doc-locale" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.field.locale")}</label
      >
      <select id="doc-locale" name="locale" class={inputClass} value={doc?.locale ?? locale}>
        {#each LOCALES as code (code)}
          <option value={code}>{t(`locale.${code}`)}</option>
        {/each}
      </select>
    </div>
    <div>
      <label for="doc-template" class="mb-1 block text-sm font-medium text-text"
        >{t("invoicing.field.template")}</label
      >
      <select
        id="doc-template"
        name="template_id"
        class={inputClass}
        value={doc?.template_id ?? settings?.default_template_id ?? ""}
      >
        <option value="">—</option>
        {#each templates as template (template.id)}
          <option value={template.id}>{template.name}</option>
        {/each}
      </select>
    </div>
  </div>

  {#if !locked}
    <label class="flex items-center gap-2 text-sm text-text">
      <input
        type="checkbox"
        name="prices_include_tax"
        value="1"
        bind:checked={includeTax}
        class="rounded border-border"
      />
      {t("invoicing.field.prices_include_tax")}
    </label>
  {/if}

  <div>
    <label for="doc-intro" class="mb-1 block text-sm font-medium text-text"
      >{t("invoicing.field.intro")}</label
    >
    <textarea id="doc-intro" name="intro" rows="2" class={inputClass}>{doc?.intro ?? ""}</textarea>
  </div>

  {#if !locked}
    <LinesEditor
      bind:lines
      {taxRates}
      defaultTaxRateId={settings?.default_tax_rate_id ?? ""}
      currency={effectiveCurrency}
      {locale}
      pricesIncludeTax={includeTax}
      formId={FORM_ID}
    />
  {/if}

  <div>
    <label for="doc-notes" class="mb-1 block text-sm font-medium text-text"
      >{t("invoicing.field.notes")}</label
    >
    <textarea id="doc-notes" name="notes" rows="2" class={inputClass}>{doc?.notes ?? ""}</textarea>
  </div>

  {#if form?.error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(String(form.error))}</p>
  {/if}
  <div class="flex justify-end gap-2">
    {#if oncancel}
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={oncancel}>{t("common.cancel")}</button
      >
    {/if}
    <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >{t("common.save")}</button
    >
  </div>
</form>

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  definitions={companyDefinitions}
  {locale}
  error={(form as { qcError?: string } | null)?.qcError ?? null}
/>
