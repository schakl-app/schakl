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
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
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
    companies?: { id: string; name: string }[];
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
     *  ContactQuickCreate dialog (slot "contact"); the ＋ only shows when passed. */
    oncreatecontact?: (name: string) => void;
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

  // Auto-fill a fresh invoice with the client's unbilled time (owner request): every
  // approved, billable, not-yet-invoiced entry becomes a line, its own description in the
  // description field. The user edits or removes any before saving; a removed line's entry
  // simply stays unbilled. Invoices only — quotes never bill time.
  type UnbilledEntry = {
    id: string;
    minutes: number;
    description: string | null;
    project_name: string;
    rate: string | number;
  };
  let autoAddedCount = $state(0);
  //: The company we last prefilled for — so re-picking the same client doesn't refetch, and
  //: switching clients replaces *only* the previous auto lines, never hand-typed ones.
  let prefilledFor = $state<string | null>(null); // null: first resolved client always prefills

  async function fetchUnbilled(target: string): Promise<UnbilledEntry[]> {
    try {
      const res = await fetch(`/invoices/unbilled?company_id=${encodeURIComponent(target)}`);
      if (!res.ok) return [];
      const data = (await res.json()) as { entries?: UnbilledEntry[] };
      return data.entries ?? [];
    } catch {
      return []; // no permission / offline: the form just doesn't prefill
    }
  }

  function toLine(entry: UnbilledEntry): EditableLine {
    return {
      description:
        entry.description?.trim() || entry.project_name || t("invoicing.new.time_line_fallback"),
      quantity: (entry.minutes / 60).toFixed(2),
      unit: t("invoicing.from_time.hours_unit"),
      unit_price: String(Number(entry.rate)),
      tax_rate_id: settings?.default_tax_rate_id ?? "",
      time_entry_id: entry.id,
      auto: true,
    };
  }

  $effect(() => {
    if (kind !== "invoice" || !isNew) return;
    const target = createdCompanyId || companyId;
    if (target === prefilledFor) return;
    prefilledFor = target;
    if (!target) {
      // Client cleared: drop any auto lines, keep what was typed by hand.
      lines = lines.filter((line) => !line.auto);
      autoAddedCount = 0;
      return;
    }
    void fetchUnbilled(target).then((entries) => {
      // A slower earlier fetch must not clobber a later client pick.
      if (prefilledFor !== target) return;
      const manual = lines.filter((line) => !line.auto);
      lines = [...entries.map(toLine), ...manual];
      autoAddedCount = entries.length;
    });
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

  // Show the inherited defaults, don't hide them behind empty fields (docs/UX.md #81): a
  // fresh document pre-fills today and the org's payment term / quote validity — visibly,
  // exactly what the API would fall back to at issue time.
  function isoInDays(days: number): string {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  }
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
          oncreate={oncreatecontact}
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
        oncreate={oncreatecontact}
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
    {#if autoAddedCount > 0}
      <p class="text-sm text-text-muted">
        {t("invoicing.new.time_prefill_note", { count: autoAddedCount })}
      </p>
    {/if}
    <LinesEditor
      bind:lines
      {taxRates}
      {products}
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
