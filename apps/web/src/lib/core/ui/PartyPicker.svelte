<script lang="ts">
  /**
   * Pick a responsible "party" (issue #88): the agency, a client company, an employee or a
   * contact. Serialises `{ type, id }` into one hidden field under `name` so a server action
   * reads it with `parseParty`. Mirrors AssigneePicker: comboboxes, never native selects
   * (docs/UX.md). The type is a small button group; the entity is a searchable combobox.
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
  }: {
    name: string;
    value?: { type: PartyType; id?: string | null } | null;
    agencyLabel: string;
    companies?: Company[];
    employees?: Employee[];
    contacts?: Contact[];
    id?: string;
    formId?: string;
  } = $props();

  let type = $state<PartyType>(value?.type ?? "agency");
  let entityId = $state<string>(value?.id ?? "");

  const TYPES: { key: PartyType; label: () => string }[] = [
    { key: "agency", label: () => t("party.agency") },
    { key: "company", label: () => t("party.company") },
    { key: "employee", label: () => t("party.employee") },
    { key: "contact", label: () => t("party.contact") },
  ];

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
        onclick={() => pickType(option.key)}>{option.label()}</button
      >
    {/each}
  </div>

  {#if type === "agency"}
    <p class="text-xs text-text-muted">{agencyLabel}</p>
  {:else}
    <Combobox
      {items}
      name="{id}__entity"
      bind:value={entityId}
      {allowEmpty}
      id="{id}-entity"
      placeholder={type === "company" ? t("party.own_company") : t("party.select")}
    />
  {/if}
</div>
