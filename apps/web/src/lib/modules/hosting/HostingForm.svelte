<script lang="ts">
  /** The hosting field set (issue #93). Caller owns the <form> so create and edit share it. */
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import PartyPicker from "$lib/core/ui/PartyPicker.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { components } from "$lib/core/api/schema";

  type Hosting = components["schemas"]["HostingRead"];
  type Provider = components["schemas"]["ProviderRead"];
  type Definition = components["schemas"]["CustomFieldDefinitionRead"];
  type Member = components["schemas"]["MemberLookup"];

  let {
    hosting = null,
    companies,
    providers,
    employees,
    contacts,
    agencyLabel,
    definitions,
    locale,
    idPrefix = "hosting",
    nameDefault = "",
    oncreatecompany,
    oncreateprovider,
    created = null,
  }: {
    hosting?: Hosting | null;
    companies: { id: string; name: string }[];
    providers: Provider[];
    employees: Member[];
    contacts: { id: string; name: string }[];
    agencyLabel: string;
    definitions: Definition[];
    locale: string;
    idPrefix?: string;
    /** Prefills the name on create — for quick-create from another form's picker (#115). */
    nameDefault?: string;
    /** Inline-create (#115, docs/UX.md): typing an unknown name offers "＋ … toevoegen". */
    oncreatecompany?: (name: string) => void;
    oncreateprovider?: (kind: "hosting", name: string) => void;
    /** The entity a quick-create modal just made; auto-selected in the matching picker. */
    created?: { slot: string; id: string } | null;
  } = $props();

  // Derived, not consts: a quick-create refreshes these lists mid-life and the new
  // entity must resolve to its label in the picker.
  const providerItems = $derived(
    providers.filter((p) => p.kind === "hosting").map((p) => ({ value: p.id, label: p.name })),
  );
  const companyItems = $derived(companies.map((c) => ({ value: c.id, label: c.name })));

  // Per-slot memory so a manual re-pick after a quick-create is never overridden (see DomainForm).
  let createdBySlot = $state<Record<string, string>>({});
  $effect(() => {
    if (created) createdBySlot[created.slot] = created.id;
  });
</script>

<div class="space-y-4">
  <div>
    <label for="{idPrefix}-name" class="mb-1 block text-sm text-text">{t("hosting.name")}</label>
    <input
      id="{idPrefix}-name"
      name="name"
      required
      value={hosting?.name ?? nameDefault}
      class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
    />
  </div>
  <div class="grid gap-4 sm:grid-cols-2">
    <div>
      <label for="{idPrefix}-company" class="mb-1 block text-sm text-text"
        >{t("hosting.company")}</label
      >
      <Combobox
        items={companyItems}
        name="company_id"
        value={createdBySlot.company ?? hosting?.company_id ?? ""}
        id="{idPrefix}-company"
        placeholder={t("common.none")}
        oncreate={oncreatecompany}
      />
    </div>
    <div>
      <label for="{idPrefix}-provider" class="mb-1 block text-sm text-text"
        >{t("hosting.provider")}</label
      >
      <Combobox
        items={providerItems}
        name="provider_id"
        value={createdBySlot.hosting ?? hosting?.provider_id ?? ""}
        id="{idPrefix}-provider"
        placeholder={t("common.none")}
        oncreate={oncreateprovider ? (q) => oncreateprovider("hosting", q) : undefined}
      />
    </div>
  </div>
  <div>
    <label for="{idPrefix}-ip" class="mb-1 block text-sm text-text">{t("hosting.ip_address")}</label
    >
    <input
      id="{idPrefix}-ip"
      name="ip_address"
      value={hosting?.ip_address ?? ""}
      placeholder="203.0.113.7"
      class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
    />
  </div>
  <div>
    <span class="mb-1 block text-sm text-text">{t("hosting.contact")}</span>
    <PartyPicker
      name="contact"
      value={hosting?.contact ?? { type: "agency", id: null }}
      {agencyLabel}
      {companies}
      {employees}
      {contacts}
      id="{idPrefix}-contact"
    />
  </div>
  {#if definitions.length > 0}
    <CustomFieldsForm {definitions} values={hosting?.custom ?? {}} {locale} />
  {:else}
    <input type="hidden" name="custom" value={JSON.stringify(hosting?.custom ?? {})} />
  {/if}
</div>
