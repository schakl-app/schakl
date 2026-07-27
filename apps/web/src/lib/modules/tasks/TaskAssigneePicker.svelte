<script lang="ts">
  /**
   * Assign a task to an **employee** or — new in #273 — to a **contact of the task's own client
   * company** ("waiting on the client to send the materials"). Mirrors PartyPicker's pattern
   * (a small type button-group over a searchable Combobox, docs/UX.md — comboboxes, never native
   * selects), narrowed to `employee | contact`: no agency/company options apply to an assignee.
   *
   * Unlike PartyPicker it posts the two real body fields directly — `assignee_user_id` and
   * `assignee_contact_id` — as two hidden inputs that are *always* present (one carries the id,
   * the other posts empty). That matters: the API enforces mutual exclusivity, so switching kind
   * has to actively clear the other field, and a Combobox that unmounts on toggle would instead
   * simply omit its field and leave the stale value untouched. The inner Combobox is UI only.
   *
   * The contact toggle appears only when the task has a client (`contactsEnabled`) — an internal
   * task has no "the client" to draw from. Contacts are select-only here: they are a client's
   * managed roster (created on the company/contacts screens), not invented from a task.
   */
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  type Kind = "employee" | "contact";

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
    formId,
    employees = [],
    contacts = [],
    contactsEnabled = false,
    userValue = "",
    contactValue = "",
    id = "assignee",
  }: {
    formId?: string;
    employees?: Employee[];
    contacts?: Contact[];
    /** The task has a client company, so its contacts may be assigned. */
    contactsEnabled?: boolean;
    userValue?: string;
    contactValue?: string;
    id?: string;
  } = $props();

  // Start on whichever kind the stored task uses; a contact assignee only if one is set.
  // svelte-ignore state_referenced_locally
  let kind = $state<Kind>(contactValue ? "contact" : "employee");
  // svelte-ignore state_referenced_locally
  let entityId = $state<string>(contactValue || userValue);

  // If the client is cleared while a contact was picked (company removed in the same edit), fall
  // back to the employee toggle — the contact option is gone and its hidden field must post empty.
  $effect(() => {
    if (!contactsEnabled && kind === "contact") {
      kind = "employee";
      entityId = "";
    }
  });

  // The two body fields, always emitted. Only the active kind carries the picked id.
  const userField = $derived(kind === "employee" ? entityId : "");
  const contactField = $derived(kind === "contact" ? entityId : "");

  const employeeItems = $derived(
    employees.map((e) => ({ value: e.user_id, label: e.full_name || e.email })),
  );
  const contactItems = $derived(contacts.map((c) => ({ value: c.id, label: c.name })));
  const items = $derived(kind === "employee" ? employeeItems : contactItems);

  const KINDS: { key: Kind; label: () => string }[] = [
    { key: "employee", label: () => t("party.employee") },
    { key: "contact", label: () => t("party.contact") },
  ];
  // Keep the contact toggle visible when it is already the stored value, even if the client list
  // hasn't loaded yet, so an existing assignment never silently flips to employee on first paint.
  const visibleKinds = $derived(
    KINDS.filter((k) => k.key === "employee" || contactsEnabled || kind === "contact"),
  );

  function pickKind(next: Kind) {
    if (next === kind) return;
    kind = next;
    entityId = "";
  }
</script>

<div class="space-y-2">
  <input type="hidden" name="assignee_user_id" value={userField} form={formId} />
  <input type="hidden" name="assignee_contact_id" value={contactField} form={formId} />

  {#if visibleKinds.length > 1}
    <div class="flex flex-wrap gap-1">
      {#each visibleKinds as option (option.key)}
        <button
          type="button"
          class="rounded-lg border px-2.5 py-1 text-xs
            {kind === option.key
            ? 'border-brand bg-brand/10 font-medium text-brand'
            : 'border-border text-text-muted hover:text-text'}"
          onclick={() => pickKind(option.key)}>{option.label()}</button
        >
      {/each}
    </div>
  {/if}

  <Combobox
    {items}
    name="{id}__pick"
    bind:value={entityId}
    id="{id}-entity"
    placeholder={t("party.select")}
  />
</div>
