<script lang="ts">
  /**
   * Pick a responsible "party" (issue #88): the agency, a client company, an employee or a
   * contact. Serialises `{ type, id }` into one hidden field under `name` so a server action
   * reads it with `parseParty`. Mirrors AssigneePicker: comboboxes, never native selects
   * (docs/UX.md). The type is a small button group; the entity is a searchable combobox.
   *
   * Inline-create (#115, docs/UX.md): companies and contacts have a create path, so typing an
   * unknown name offers "＋ … toevoegen" when the host page wires `oncreatecompany` /
   * `oncreatecontact`. The callbacks carry a slot unique to this picker (`<name>:company` /
   * `<name>:contact`); the page threads it through the quick-create dialog so the action's
   * `inlineCreated` answer auto-selects here — and only here, never in a sibling company field.
   * Employees are invited, not created (select-only); the agency is a static option.
   */
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import { t } from "$lib/core/i18n";
  import type { PartyType } from "$lib/core/party";

  interface Company {
    id: string;
    name: string;
  }
  interface Employee {
    user_id: string;
    full_name: string | null;
    email: string;
  }
  interface Contact {
    id: string;
    name: string;
  }

  let {
    name,
    value = null,
    agencyLabel,
    companies = [],
    employees = [],
    contacts = [],
    id = name,
    formId,
    types,
    typeLabels,
    companyPickable = true,
    oncreatecompany,
    oncreatecontact,
    created = null,
  }: {
    name: string;
    value?: { type: PartyType; id?: string | null } | null;
    agencyLabel: string;
    companies?: Company[];
    employees?: Employee[];
    contacts?: Contact[];
    id?: string;
    formId?: string;
    /** Which party types to offer (default all). A stored value outside the list still
     * renders, so restricting a picker never silently rewrites old records. */
    types?: PartyType[];
    /** Button-label overrides — e.g. the actual org and client names instead of the
     * generic "Bureau" / "Klant". */
    typeLabels?: Partial<Record<PartyType, string>>;
    /** false: "company" means the record's own client — a fixed choice (id null), no
     * entity combobox, labelled with the client's name via `typeLabels`. */
    companyPickable?: boolean;
    /** Inline-create (#115): typing an unknown company offers "＋ … toevoegen". */
    oncreatecompany?: (query: string, slot: string) => void;
    /** Inline-create (#115): typing an unknown contact offers "＋ … toevoegen". */
    oncreatecontact?: (query: string, slot: string) => void;
    /** The entity a quick-create modal just made; auto-selected when its slot is this picker's. */
    created?: { slot: string; id: string } | null;
  } = $props();

  let type = $state<PartyType>(value?.type ?? "agency");
  let entityId = $state<string>(value?.id ?? "");

  // Apply each `created` once: a later manual re-pick must never be overridden by a stale prop.
  let applied = $state<{ slot: string; id: string } | null>(null);
  $effect(() => {
    if (!created || created === applied) return;
    if (created.slot === `${name}:company`) {
      type = "company";
      entityId = created.id;
      applied = created;
    } else if (created.slot === `${name}:contact`) {
      type = "contact";
      entityId = created.id;
      applied = created;
    }
  });

  const ALL_TYPES: { key: PartyType; label: () => string }[] = [
    { key: "agency", label: () => t("party.agency") },
    { key: "company", label: () => t("party.company") },
    { key: "employee", label: () => t("party.employee") },
    { key: "contact", label: () => t("party.contact") },
  ];
  // Restricted pickers keep the stored value's type on offer so old data stays visible.
  const TYPES = $derived(
    ALL_TYPES.filter((o) => !types || types.includes(o.key) || o.key === type),
  );
  function typeLabel(option: { key: PartyType; label: () => string }): string {
    if (typeLabels?.[option.key]) return typeLabels[option.key]!;
    if (option.key === "company" && !companyPickable) return t("party.own_company");
    return option.label();
  }
  // A fixed company is like the agency: the button is the whole choice, id posts null.
  const fixedChoice = $derived(type === "agency" || (type === "company" && !companyPickable));

  const items = $derived(
    type === "company"
      ? companies.map((c) => ({ value: c.id, label: c.name }))
      : type === "employee"
        ? employees.map((e) => ({ value: e.user_id, label: e.full_name || e.email }))
        : type === "contact"
          ? contacts.map((c) => ({ value: c.id, label: c.name }))
          : [],
  );

  // Company allows "empty" = the record's own company; the others need a concrete pick.
  const allowEmpty = $derived(type === "company");
  const payload = $derived(JSON.stringify({ type, id: entityId || null }));

  // Employees are invited, not created — only company and contact get a create affordance.
  const oncreate = $derived(
    type === "company" && oncreatecompany
      ? (query: string) => oncreatecompany(query, `${name}:company`)
      : type === "contact" && oncreatecontact
        ? (query: string) => oncreatecontact(query, `${name}:contact`)
        : undefined,
  );

  function pickType(next: PartyType) {
    type = next;
    entityId = "";
  }
</script>

<div class="space-y-2">
  <input type="hidden" {name} value={payload} form={formId} />
  <div class="flex flex-wrap gap-1">
    {#each TYPES as option (option.key)}
      <button
        type="button"
        class="rounded-lg border px-2.5 py-1 text-xs
          {type === option.key
          ? 'border-brand bg-brand/10 font-medium text-brand'
          : 'border-border text-text-muted hover:text-text'}"
        onclick={() => pickType(option.key)}>{typeLabel(option)}</button
      >
    {/each}
  </div>

  {#if fixedChoice}
    {#if type === "agency" && !typeLabels?.agency}
      <p class="text-xs text-text-muted">{agencyLabel}</p>
    {/if}
  {:else}
    <Combobox
      {items}
      name="{id}__entity"
      bind:value={entityId}
      {allowEmpty}
      id="{id}-entity"
      placeholder={type === "company" ? t("party.own_company") : t("party.select")}
      {oncreate}
    />
  {/if}
</div>
