<script lang="ts">
  /**
   * The domain field set (issue #90). The caller owns the <form>, action and buttons, so create
   * and edit share identical fields. Providers come as one list and are filtered per slot by kind.
   */
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import PartyPicker from "$lib/core/ui/PartyPicker.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { components } from "$lib/core/api/schema";

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
  } = $props();

  const STATUSES = ["active", "redirect", "parked", "expired", "inactive"] as const;

  const byKind = (kind: string) =>
    providers.filter((p) => p.kind === kind).map((p) => ({ value: p.id, label: p.name }));

  let emailEnabled = $state(domain?.email_enabled ?? false);
  const companyItems = companies.map((c) => ({ value: c.id, label: c.name }));
</script>

<div class="space-y-4">
  <div>
    <label for="{idPrefix}-name" class="mb-1 block text-sm text-text">{t("domains.name")}</label>
    <input
      id="{idPrefix}-name"
      name="name"
      required
      value={domain?.name ?? ""}
      placeholder="example.nl"
      class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
    />
  </div>

  <div>
    <label for="{idPrefix}-company" class="mb-1 block text-sm text-text"
      >{t("domains.company")}</label
    >
    <Combobox
      items={companyItems}
      name="company_id"
      value={domain?.company_id ?? ""}
      allowEmpty={false}
      id="{idPrefix}-company"
      placeholder={t("domains.company")}
    />
  </div>

  <div>
    <span class="mb-1 block text-sm text-text">{t("domains.status")}</span>
    <div class="flex flex-wrap gap-2">
      {#each STATUSES as status (status)}
        <label class="flex items-center gap-1.5 text-sm text-text">
          <input
            type="radio"
            name="status"
            value={status}
            checked={(domain?.status ?? "active") === status}
          />
          {t(`domains.status.${status}`)}
        </label>
      {/each}
    </div>
  </div>

  <div class="grid gap-4 sm:grid-cols-2">
    <div>
      <label for="{idPrefix}-registrar" class="mb-1 block text-sm text-text"
        >{t("domains.registrar")}</label
      >
      <Combobox
        items={byKind("registrar")}
        name="registrar_provider_id"
        value={domain?.registrar_provider_id ?? ""}
        id="{idPrefix}-registrar"
        placeholder={t("common.none")}
      />
    </div>
    <div>
      <label for="{idPrefix}-dns" class="mb-1 block text-sm text-text">{t("domains.dns")}</label>
      <Combobox
        items={byKind("dns")}
        name="dns_provider_id"
        value={domain?.dns_provider_id ?? ""}
        id="{idPrefix}-dns"
        placeholder={t("common.none")}
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
            value={domain?.email_provider_id ?? ""}
            id="{idPrefix}-email-provider"
            placeholder={t("common.none")}
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
