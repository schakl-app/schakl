<script lang="ts">
  /**
   * The domain field set (issue #90). The caller owns the <form>, action and buttons, so create
   * and edit share identical fields. Providers come as one list and are filtered per slot by kind.
   */
  import { fmtMoney, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import PartyPicker from "$lib/core/ui/PartyPicker.svelte";
  import { orgToday } from "$lib/core/today";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import AutoInvoiceModeField from "$lib/modules/invoicing/AutoInvoiceModeField.svelte";
  import type { components } from "$lib/core/api/schema";
  import InvoiceableField from "$lib/modules/domains/InvoiceableField.svelte";
  import {
    companyArchivedLabel,
    companyLifecycle,
    splitCompanyOptions,
    type PickerCompany,
  } from "$lib/modules/companies/picker";
  import { normalizeDomainName, tldOf } from "$lib/modules/domains/normalize";

  type Domain = components["schemas"]["DomainRead"];
  type Provider = components["schemas"]["ProviderRead"];
  type Definition = components["schemas"]["CustomFieldDefinitionRead"];
  type Member = components["schemas"]["MemberLookup"];
  type AutoInvoiceMode = components["schemas"]["AutoInvoiceMode"];

  let {
    domain = null,
    companies,
    providers,
    employees,
    contacts,
    agencyLabel,
    definitions,
    locale,
    idPrefix = "domain",
    nameDefault = "",
    initialCompanyId = "",
    tldPrices = [],
    orgMode = null,
    formId = undefined,
    oncreatecompany,
    oncreatecontact,
    oncreateprovider,
    created = null,
  }: {
    domain?: Domain | null;
    companies: PickerCompany[];
    providers: Provider[];
    employees: Member[];
    contacts: { id: string; name: string }[];
    agencyLabel: string;
    definitions: Definition[];
    locale: string;
    idPrefix?: string;
    /** Current TLD list prices (#250): the resolved rate shown while typing a name.
     * Empty when the viewer lacks `domains.tld_price.read` — the hint simply stays away. */
    tldPrices?: { tld: string; amount: string; currency: string }[];
    /** The org's automation level, named in the "follow the organisation" hint. */
    orgMode?: AutoInvoiceMode | null;
    /** Associate the radios with a form rendered outside this component. */
    formId?: string;
    /** Prefills the name on create — for quick-create from another form's picker (#115). */
    nameDefault?: string;
    /** Preselects the client on a fresh form (quick-create from a client page). */
    initialCompanyId?: string;
    /** Inline-create (#115, docs/UX.md): typing an unknown name offers "＋ … toevoegen".
     * `slot` is set when a PartyPicker asks, so the created entity auto-selects there. */
    oncreatecompany?: (name: string, slot?: string) => void;
    oncreatecontact?: (name: string, slot: string) => void;
    oncreateprovider?: (kind: "registrar" | "dns" | "email", name: string) => void;
    /** The entity a quick-create modal just made; auto-selected in the matching picker. */
    created?: { slot: string; id: string } | null;
  } = $props();

  // Radio selection is component state, never a one-way checked (docs/UX.md); seeded once
  // per mount — the host page keys this form per record.
  // svelte-ignore state_referenced_locally
  let statusChoice = $state(domain?.status ?? "active");

  const STATUSES = ["active", "redirect", "parked", "expired", "inactive"] as const;

  const byKind = (kind: string) =>
    providers.filter((p) => p.kind === kind).map((p) => ({ value: p.id, label: p.name }));

  // Remembered per slot so creating a DNS provider doesn't reset an earlier-created registrar,
  // and a later manual pick in the same slot is never overridden (the prop only changes on create).
  let createdBySlot = $state<Record<string, string>>({});
  $effect(() => {
    if (created) createdBySlot[created.slot] = created.id;
  });

  let emailEnabled = $state(domain?.email_enabled ?? false);
  // Derived, not a const: a quick-create refreshes `companies` mid-life and the new
  // entity must resolve to its label in the picker.
  const companySelected = $derived(
    createdBySlot.company ?? domain?.company_id ?? initialCompanyId ?? "",
  );
  // Archived clients keep out of the opening list and stay findable by typing; the one this
  // domain is already on is always offered, however its relationship ended.
  const companyPicker = $derived(splitCompanyOptions(companies, { selectedId: companySelected }));
  const companyItems = $derived(companyPicker.live);

  // What the two invoicing rows currently answer. Held here so the collapsed section can state
  // it: a disclosure that hides a decision without naming it is worse than no disclosure.
  const invoiceableRow = (v: boolean | null | undefined) =>
    v === true ? "yes" : v === false ? "no" : "";
  // svelte-ignore state_referenced_locally
  let invoiceableChoice = $state(invoiceableRow(domain?.invoiceable));
  // svelte-ignore state_referenced_locally
  let autoModeChoice = $state<string>(domain?.auto_invoice_mode ?? "");
  const invoicingSummary = $derived(
    [
      invoiceableChoice === "yes"
        ? t("domains.invoiceable.yes")
        : invoiceableChoice === "no"
          ? t("domains.invoiceable.no")
          : t("domains.invoiceable.follow"),
      autoModeChoice ? t(`invoicing.auto.${autoModeChoice}`) : t("invoicing.auto.inherit"),
    ].join(" · "),
  );

  // The renewal date, held so the "use the registrar's date" shortcut can fill it (#250). Blank
  // on create is not an empty answer: the API resolves the default — the register's expiry if
  // one has spoken, else the anniversary of the start date — so the placeholder says so rather
  // than the form guessing a date the server is about to work out properly.
  // svelte-ignore state_referenced_locally
  let renewalValue = $state(domain?.next_invoice_date ?? "");
  // What the registrar last observed, shown only when it is worth acting on: a date we already
  // hold is not news (CLAUDE.md §10 — decided and observed are separate, and drift is the whole
  // point of keeping them so).
  const registerExpiry = $derived(domain?.register_expires_on ?? null);
  const registerDiffers = $derived(registerExpiry != null && registerExpiry !== renewalValue);

  // Stateful so the TLD price hint follows what is typed (#250); still normalized on change.
  // svelte-ignore state_referenced_locally
  let nameValue = $state(domain?.name ?? normalizeDomainName(nameDefault));
  // The rate a renewal would draft at: the typed name's TLD in the list, else what the API
  // resolved for the stored record (covers a viewer whose list came back empty).
  const tldListPrice = $derived.by(() => {
    const tld = tldOf(normalizeDomainName(nameValue));
    const row = tld ? tldPrices.find((p) => p.tld === tld) : null;
    if (row) return row.amount;
    return domain && domain.price_override == null ? (domain.resolved_price ?? null) : null;
  });
</script>

<div class="space-y-4">
  <div>
    <label for="{idPrefix}-name" class="mb-1 block text-sm text-text">{t("domains.name")}</label>
    <input
      id="{idPrefix}-name"
      name="name"
      required
      bind:value={nameValue}
      placeholder="example.nl"
      class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
      onchange={() => (nameValue = normalizeDomainName(nameValue))}
    />
  </div>

  <div class="grid gap-4 sm:grid-cols-2">
    <div>
      <label for="{idPrefix}-start-date" class="mb-1 block text-sm text-text"
        >{t("domains.start_date")}</label
      >
      <DateInput
        name="start_date"
        id="{idPrefix}-start-date"
        required
        value={domain?.start_date ?? orgToday()}
      />
      <p class="mt-1 text-xs text-text-muted">{t("domains.start_date_hint")}</p>
    </div>
    <div>
      <label for="{idPrefix}-next-invoice-date" class="mb-1 block text-sm text-text"
        >{t("domains.next_invoice_date")}</label
      >
      <DateInput
        name="next_invoice_date"
        id="{idPrefix}-next-invoice-date"
        bind:value={renewalValue}
        {formId}
      />
      <p class="mt-1 text-xs text-text-muted">
        {t("domains.next_invoice_date_hint")}
        {#if registerDiffers}
          <!-- The registrar's own answer, offered rather than applied: an integration reports
               drift, it never silently overwrites what somebody decided (CLAUDE.md §10). -->
          <button
            type="button"
            class="text-brand hover:underline"
            onclick={() => (renewalValue = registerExpiry ?? "")}
          >
            {t("domains.register_expiry_use", { date: fmtNumericDate(registerExpiry ?? "") })}
          </button>
        {/if}
      </p>
    </div>
    <div>
      <label for="{idPrefix}-price-override" class="mb-1 block text-sm text-text"
        >{t("domains.price_override")}</label
      >
      <input
        id="{idPrefix}-price-override"
        name="price_override"
        type="number"
        step="0.01"
        min="0"
        value={domain?.price_override ?? ""}
        placeholder={tldListPrice != null ? fmtMoney(Number(tldListPrice)) : ""}
        class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
      />
      <p class="mt-1 text-xs text-text-muted">
        {t("domains.price_override_hint")}
        {#if tldListPrice != null}
          {t("domains.price_source_tld")}: {fmtMoney(Number(tldListPrice))}
        {/if}
      </p>
    </div>
  </div>

  <!-- Invoicing: whether the renewal is billed on at all (#298), then how far the cron takes
       that invoice. Nine radio cards is the right shape for the decision and the wrong shape
       for a *create* form, where both answers already default to "follow" and nobody has an
       opinion on a domain that does not exist yet — so it opens collapsed there and the
       summary states what those defaults resolve to. Editing an existing record opens it,
       because that is what someone came to the form for.
       A closed <details> still submits the inputs inside it, so nothing here depends on the
       section being open — collapsing it can never silently clear a stored decision. -->
  <details class="rounded-lg border border-border" open={domain !== null}>
    <summary class="cursor-pointer list-item px-3 py-2 text-sm text-text marker:text-text-muted">
      <span class="font-medium">{t("domains.invoicing.section")}</span>
      <span class="text-text-muted">: {invoicingSummary}</span>
    </summary>
    <div class="space-y-4 border-t border-border p-3">
      <!-- Above the automation level on purpose: "do we invoice this" comes before "how far
           does the cron take it". -->
      <InvoiceableField
        name="invoiceable"
        value={domain?.invoiceable ?? null}
        source={domain?.invoiceable_source ?? null}
        registers={domain?.registers ?? []}
        {formId}
        onchoose={(chosen) => (invoiceableChoice = chosen)}
      />

      <!-- Only about the paper: nothing here renews the registration. Defaults to following
           the organisation setting. -->
      <AutoInvoiceModeField
        name="auto_invoice_mode"
        value={domain?.auto_invoice_mode ?? ""}
        inheritable
        {orgMode}
        {formId}
        onchoose={(chosen) => (autoModeChoice = chosen)}
      />
    </div>
  </details>

  <div>
    <label for="{idPrefix}-company" class="mb-1 block text-sm text-text"
      >{t("domains.company")}</label
    >
    <Combobox
      items={companyItems}
      archived={companyPicker.retired}
      archivedLabel={companyArchivedLabel()}
      name="company_id"
      value={companySelected}
      allowEmpty={false}
      id="{idPrefix}-company"
      placeholder={t("domains.company")}
      oncreate={oncreatecompany}
    />
  </div>

  <div>
    <span class="mb-1 block text-sm text-text">{t("domains.status")}</span>
    <div class="flex flex-wrap gap-2">
      {#each STATUSES as status (status)}
        <label class="flex items-center gap-1.5 text-sm text-text">
          <input type="radio" name="status" value={status} bind:group={statusChoice} />
          {t(`domains.status.${status}`)}
        </label>
      {/each}
    </div>
  </div>

  {#if statusChoice === "redirect"}
    <div>
      <label for="{idPrefix}-redirect-url" class="mb-1 block text-sm text-text"
        >{t("domains.redirect_url")}</label
      >
      <input
        id="{idPrefix}-redirect-url"
        name="redirect_url"
        type="url"
        value={domain?.redirect_url ?? ""}
        placeholder="https://example.nl"
        class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
      />
      <p class="mt-1 text-xs text-text-muted">{t("domains.redirect_url_hint")}</p>
    </div>
  {/if}

  <div class="grid gap-4 sm:grid-cols-2">
    <div>
      <label for="{idPrefix}-registrar" class="mb-1 block text-sm text-text"
        >{t("domains.registrar")}</label
      >
      <Combobox
        items={byKind("registrar")}
        name="registrar_provider_id"
        value={createdBySlot.registrar ?? domain?.registrar_provider_id ?? ""}
        id="{idPrefix}-registrar"
        placeholder={t("common.none")}
        oncreate={oncreateprovider ? (q) => oncreateprovider("registrar", q) : undefined}
      />
    </div>
    <div>
      <label for="{idPrefix}-dns" class="mb-1 block text-sm text-text">{t("domains.dns")}</label>
      <Combobox
        items={byKind("dns")}
        name="dns_provider_id"
        value={createdBySlot.dns ?? domain?.dns_provider_id ?? ""}
        id="{idPrefix}-dns"
        placeholder={t("common.none")}
        oncreate={oncreateprovider ? (q) => oncreateprovider("dns", q) : undefined}
      />
    </div>
  </div>

  <div>
    <span class="mb-1 block text-sm text-text">{t("domains.registry_contact")}</span>
    <PartyPicker
      name="registry_contact"
      value={domain?.registry_contact}
      {agencyLabel}
      {companies}
      companyLifecycle={companyLifecycle()}
      {employees}
      {contacts}
      id="{idPrefix}-registry"
      {oncreatecompany}
      {oncreatecontact}
      {created}
    />
  </div>

  <div class="rounded-lg border border-border p-3">
    <label class="flex items-center gap-2 text-sm font-medium text-text">
      <input type="checkbox" name="email_enabled" bind:checked={emailEnabled} value="on" />
      {t("domains.email_enabled")}
    </label>
    {#if emailEnabled}
      <div class="mt-3 space-y-3">
        <div>
          <label for="{idPrefix}-email-provider" class="mb-1 block text-sm text-text"
            >{t("domains.email_provider")}</label
          >
          <Combobox
            items={byKind("email")}
            name="email_provider_id"
            value={createdBySlot.email ?? domain?.email_provider_id ?? ""}
            id="{idPrefix}-email-provider"
            placeholder={t("common.none")}
            oncreate={oncreateprovider ? (q) => oncreateprovider("email", q) : undefined}
          />
        </div>
        <div>
          <span class="mb-1 block text-sm text-text">{t("domains.email_contact")}</span>
          <PartyPicker
            name="email_contact"
            value={domain?.email_contact ?? { type: "agency", id: null }}
            {agencyLabel}
            {companies}
            companyLifecycle={companyLifecycle()}
            {employees}
            {contacts}
            id="{idPrefix}-email-contact"
            {oncreatecompany}
            {oncreatecontact}
            {created}
          />
        </div>
      </div>
    {/if}
  </div>

  {#if definitions.length > 0}
    <CustomFieldsForm {definitions} values={domain?.custom ?? {}} {locale} />
  {:else}
    <input type="hidden" name="custom" value={JSON.stringify(domain?.custom ?? {})} />
  {/if}
</div>
