<script lang="ts">
  /**
   * The domain field set (issue #90). The caller owns the <form>, action and buttons, so create
   * and edit share identical fields. Providers come as one list and are filtered per slot by kind.
   */
  import { fmtMoney } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import PartyPicker from "$lib/core/ui/PartyPicker.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { components } from "$lib/core/api/schema";
  import { normalizeDomainName, tldOf } from "$lib/modules/domains/normalize";

  type Domain = components["schemas"]["DomainRead"];
  type Provider = components["schemas"]["ProviderRead"];
  type Definition = components["schemas"]["CustomFieldDefinitionRead"];
  type Member = components["schemas"]["MemberLookup"];

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
    oncreatecompany,
    oncreatecontact,
    oncreateprovider,
    created = null,
  }: {
    domain?: Domain | null;
    companies: { id: string; name: string }[];
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
  const companyItems = $derived(companies.map((c) => ({ value: c.id, label: c.name })));

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
        value={domain?.start_date ?? new Date().toISOString().slice(0, 10)}
      />
      <p class="mt-1 text-xs text-text-muted">{t("domains.start_date_hint")}</p>
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

  <div>
    <label for="{idPrefix}-company" class="mb-1 block text-sm text-text"
      >{t("domains.company")}</label
    >
    <Combobox
      items={companyItems}
      name="company_id"
      value={createdBySlot.company ?? domain?.company_id ?? initialCompanyId}
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
