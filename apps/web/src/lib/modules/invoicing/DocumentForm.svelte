<script lang="ts">
  /**
   * The document editor (issue #207): one form for a new or draft invoice/quote. After
   * issue the money fields disappear (the API refuses them anyway — issued money is
   * immutable) and only process fields remain. One save button (docs/UX.md).
   */
  import { enhance } from "$app/forms";
  import { COMMON_CURRENCIES, otherCurrencies } from "$lib/core/currencies";
  import { getCurrency } from "$lib/core/currency";
  import { isoAddDays } from "$lib/core/calendar";
  import { orgToday } from "$lib/core/today";
  import { LOCALES, t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import CompanyQuickCreate from "$lib/modules/companies/CompanyQuickCreate.svelte";
  import {
    companyArchivedLabel,
    splitCompanyOptions,
    type PickerCompany,
  } from "$lib/modules/companies/picker";

  import LinesEditor from "./LinesEditor.svelte";
  import { lineKey, type EditableLine } from "./calc";
  import type {
    DocTemplate,
    Invoice,
    InvoicingSettings,
    Outstanding,
    Quote,
    TaxRate,
  } from "./types";
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
    products = [],
    templates,
    settings,
    locale,
    form,
    oncancel,
    oncreatecontact,
    initialCompanyId = "",
  }: {
    kind: "invoice" | "quote";
    doc?: Invoice | Quote | null;
    action: string;
    companies?: PickerCompany[];
    companyDefinitions?: FieldDefinition[];
    contacts?: { id: string; name: string; company_ids: string[] }[];
    taxRates: TaxRate[];
    /** The tenant's default products for the line picker (owner request). */
    products?: {
      id: string;
      name: string;
      description?: string | null;
      unit?: string | null;
      unit_price: string | number;
      tax_rate_id?: string | null;
    }[];
    templates: DocTemplate[];
    settings: InvoicingSettings | null;
    locale: string;
    form: Record<string, unknown> | null;
    oncancel?: () => void;
    /** Inline-create for the contact picker (#115): the host wires this to its
     *  ContactQuickCreate dialog (slot "contact"); the ＋ only shows when passed. The
     *  document's own client rides along (#247) so the new-contact dialog links it by
     *  default instead of leaving the client blank. */
    oncreatecontact?: (name: string, company: { id: string; name: string } | null) => void;
    /** Preset client for a fresh document (the client page's "＋ nieuwe factuur"). */
    initialCompanyId?: string;
  } = $props();

  const isNew = $derived(doc === null);
  const locked = $derived(doc !== null && doc.status !== "draft");
  const orgCurrency = getCurrency();
  const busy = new InFlight();

  // Deliberate initial capture: the preset only seeds a fresh form.
  // svelte-ignore state_referenced_locally
  let companyId = $state(initialCompanyId);
  let createdCompanyId = $state("");
  // One "contact" slot: the two contact pickers below are the same field in mutually
  // exclusive states (new document vs editable draft), never rendered together.
  let createdContactId = $state("");
  let qcCompanyOpen = $state(false);
  let qcCompanyName = $state("");
  $effect(() => {
    const created = (form as { inlineCreated?: { slot: string; id: string } } | null)
      ?.inlineCreated;
    if (created?.slot === "company") createdCompanyId = created.id;
    else if (created?.slot === "contact") createdContactId = created.id;
  });

  let currency = $state("");
  const effectiveCurrency = $derived(currency || doc?.currency || orgCurrency);

  // Provenance rides back into the editor: the lines are replaced wholesale on save, so a
  // mapping that dropped what a line bills would post lines that had forgotten their claims —
  // and the API would dutifully release them, handing the period back to the cron that raised
  // it. Reading it back is half of that fix; `LineRead` echoing it is the other half.
  let lines = $state<EditableLine[]>(
    (doc?.lines ?? []).map((line) => ({
      key: lineKey(),
      description: line.description,
      line_kind: line.line_kind ?? "product",
      quantity: String(Number(line.quantity)),
      unit: line.unit ?? "",
      unit_price: String(Number(line.unit_price)),
      tax_rate_id: line.tax_rate_id ?? "",
      time_entry_ids: line.time_entry_ids ?? [],
      subscription_id: line.subscription_id ?? undefined,
      domain_id: line.domain_id ?? undefined,
      period_start: line.period_start ?? undefined,
      period_end: line.period_end ?? undefined,
    })),
  );

  // What this client still has to be invoiced for — the three sections' pickers. It adds
  // nothing on its own: which hours and which months go on *this* invoice is a decision, not
  // a default. (It used to be a default, and a fresh invoice arrived carrying every unbilled
  // hour the client had, which is a list to delete rather than a list to choose from.)
  //
  // Quotes bill no hours and claim no period, so they never ask.
  const currentCompanyId = $derived(createdCompanyId || companyId || doc?.company_id || "");
  const pickable = $derived(kind === "invoice" && !locked);
  let outstanding = $state<Outstanding | null>(null);
  let outstandingLoading = $state(false);
  $effect(() => {
    const target = currentCompanyId;
    if (!pickable || !target) {
      outstanding = null;
      return;
    }
    let current = true;
    outstandingLoading = true;
    void fetch(`/invoices/outstanding?company_id=${encodeURIComponent(target)}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: Outstanding | null) => {
        // A slower earlier fetch must not clobber a later client pick.
        if (current) outstanding = data;
      })
      .catch(() => {
        if (current) outstanding = null; // no permission / offline: no picker
      })
      .finally(() => {
        if (current) outstandingLoading = false;
      });
    return () => {
      current = false;
    };
  });

  // Bound state, not a one-way checked (docs/UX.md): the mark must survive hydration, and
  // the line calculations must follow the toggle live — a derived-only value did neither.
  // svelte-ignore state_referenced_locally
  let includeTax = $state(
    (doc?.prices_include_tax ?? settings?.prices_include_tax ?? false) as boolean,
  );
  // A client you archived is not one you are invoicing next, so it drops behind the search —
  // and stays there rather than vanishing, because a credit note against an ended relationship
  // is exactly the document somebody still has to write (`companies/picker.ts`).
  const companyPicker = $derived(
    splitCompanyOptions(companies, { selectedId: [createdCompanyId, companyId] }),
  );
  const companyItems = $derived(companyPicker.live);
  const contactItems = $derived(
    contacts
      .filter(
        (c) =>
          !currentCompanyId ||
          c.company_ids.length === 0 ||
          c.company_ids.includes(currentCompanyId),
      )
      .map((c) => ({ value: c.id, label: c.name })),
  );
  // The document's own client, resolved to {id, name} (#247): the contact quick-create dialog
  // links it by default. Name from the companies lookup (new docs) or the doc itself (edit).
  const contactLinkCompany = $derived.by(() => {
    const id = createdCompanyId || companyId || doc?.company_id || "";
    if (!id) return null;
    const name =
      (id === createdCompanyId ? qcCompanyName : "") ||
      companies.find((c) => c.id === id)?.name ||
      (id === doc?.company_id ? (doc?.company_name ?? "") : "") ||
      "";
    return name ? { id, name } : null;
  });

  // Show the inherited defaults, don't hide them behind empty fields (docs/UX.md #81): a
  // fresh document pre-fills today and the org's payment term / quote validity — visibly,
  // exactly what the API would fall back to at issue time.
  // Calendar arithmetic on the tenant's own today (§8): the old shape stepped a
  // browser-local `Date` and then printed it in UTC, so both halves could slip a day.
  const isoInDays = (days: number): string => isoAddDays(orgToday(), days);
  const defaultIssueDate = isoInDays(0);
  const defaultDeadline = isoInDays(
    kind === "invoice" ? (settings?.default_due_days ?? 14) : (settings?.quote_valid_days ?? 30),
  );

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const FORM_ID = `doc-form-${kind}`;
</script>

<form
  id={FORM_ID}
  method="POST"
  {action}
  use:enhance={busy.wrap("", () => ({ update }) => {
    void update({ reset: false });
  })}
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
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
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
          value={createdContactId}
          id="doc-contact"
          placeholder={t("invoicing.field.contact")}
          oncreate={oncreatecontact
            ? (name) => oncreatecontact(name, contactLinkCompany)
            : undefined}
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
        value={createdContactId || (doc?.contact_id ?? "")}
        id="doc-contact"
        placeholder={t("invoicing.field.contact")}
        oncreate={oncreatecontact ? (name) => oncreatecontact(name, contactLinkCompany) : undefined}
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
      <DateInput
        name="issue_date"
        id="doc-issue-date"
        value={doc?.issue_date ?? (isNew ? defaultIssueDate : "")}
      />
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
          : (doc as Quote | null)?.valid_until) ?? (isNew ? defaultDeadline : "")}
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
      {products}
      {pickable}
      {outstandingLoading}
      hours={outstanding?.hours ?? null}
      subscriptions={outstanding?.subscriptions ?? []}
      domains={outstanding?.domains ?? []}
      defaultTaxRateId={settings?.default_tax_rate_id ?? ""}
      defaultHourlyRate={settings?.default_hourly_rate ?? ""}
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
    <RichTextEditor
      id="doc-notes"
      name="notes"
      rows={2}
      value={doc?.notes ?? ""}
      scope={{ companyId: (createdCompanyId || companyId || doc?.company_id) ?? null }}
    />
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
    <Button loading={busy.active}>{t("common.save")}</Button>
  </div>
</form>

<CompanyQuickCreate
  bind:open={qcCompanyOpen}
  name={qcCompanyName}
  definitions={companyDefinitions}
  {locale}
  error={(form as { qcError?: string } | null)?.qcError ?? null}
/>
