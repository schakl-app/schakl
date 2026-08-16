<script lang="ts">
  /**
   * Assign a task to **employees** — several of them since #375, one starred as the
   * verantwoordelijke — or to a **contact of the task's own client company** (#273, "waiting on
   * the client to send the materials"). Mirrors PartyPicker's pattern (a small type button-group
   * over the picker, docs/UX.md — comboboxes, never native selects), narrowed to
   * `employee | contact`: no agency/company options apply to an assignee.
   *
   * The employee side *is* the shared `AssigneePicker`, so a task's roster looks and behaves
   * exactly like a client's or a project's: chips, a ★ on the primary, click another to promote.
   * The contact side stays a single Combobox, because the API's exclusivity is not "one of each" —
   * a client contact holds the task alone.
   *
   * Both body keys are *always* posted, because switching kind has to actively clear the other
   * side. A control that unmounts on toggle would simply omit its field and leave the stale value
   * untouched, and the API would answer 422 for a pair the user believes they replaced. In
   * employee mode that is `assignees` (the JSON roster) with an empty `assignee_contact_id`; in
   * contact mode it is the contact id with an explicitly empty roster — `[]` is a real sentence
   * ("no employees"), distinct from the absent field that means "I didn't say".
   *
   * The contact toggle appears only when the task has a client (`contactsEnabled`) — an internal
   * task has no "the client" to draw from. Contacts are select-only here: they are a client's
   * managed roster (created on the company/contacts screens), not invented from a task.
   */
  import { t } from "$lib/core/i18n";
  import AssigneePicker from "$lib/core/ui/AssigneePicker.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  type Kind = "employee" | "contact";

  interface Employee {
    user_id: string;
    full_name?: string | null;
    email: string | null;
    is_active?: boolean;
  }
  interface Contact {
    id: string;
    name: string;
  }
  interface Assignee {
    user_id: string;
    is_primary: boolean;
  }

  let {
    formId,
    employees = [],
    contacts = [],
    contactsEnabled = false,
    assignees = [],
    contactValue = "",
    id = "assignee",
  }: {
    formId?: string;
    employees?: Employee[];
    contacts?: Contact[];
    /** The task has a client company, so its contacts may be assigned. */
    contactsEnabled?: boolean;
    /** The saved roster, primary first, as the API returns it. */
    assignees?: Assignee[];
    contactValue?: string;
    id?: string;
  } = $props();

  // Start on whichever kind the stored task uses; a contact assignee only if one is set.
  // svelte-ignore state_referenced_locally
  let kind = $state<Kind>(contactValue ? "contact" : "employee");
  // svelte-ignore state_referenced_locally
  let contactId = $state<string>(contactValue);
  // The roster the employee picker starts from — held here, not read straight off the prop, so
  // toggling to "contact" and back cannot resurrect people the toggle just cleared.
  // svelte-ignore state_referenced_locally
  let roster = $state<Assignee[]>(kind === "employee" ? assignees : []);

  // If the client is cleared while a contact was picked (company removed in the same edit), fall
  // back to the employee toggle — the contact option is gone and its hidden field must post empty.
  $effect(() => {
    if (!contactsEnabled && kind === "contact") {
      kind = "employee";
      contactId = "";
    }
  });

  const contactField = $derived(kind === "contact" ? contactId : "");
  const contactItems = $derived(contacts.map((c) => ({ value: c.id, label: c.name })));

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
    // Switching kind empties the side being left: the API refuses a task held by both, so the
    // toggle has to *mean* the exclusivity rather than merely hide the other control.
    if (next === "contact") roster = [];
    else contactId = "";
  }
</script>

<div class="space-y-2">
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

  {#if kind === "employee"}
    <!-- Keyed on the toggle so switching away and back remounts the picker against the emptied
         roster rather than keeping its own stale internal state. -->
    {#key kind}
      <AssigneePicker
        members={employees}
        value={roster}
        name="assignees"
        id="{id}-employees"
        {formId}
        placeholder={t("tasks.assignees.add")}
      />
    {/key}
  {:else}
    <!-- Contact mode posts an explicitly empty roster: the task is the client's, and no colleague
         rides along on it. -->
    <input type="hidden" name="assignees" value="[]" form={formId} />
    <Combobox
      items={contactItems}
      name="{id}__pick"
      bind:value={contactId}
      id="{id}-contact"
      placeholder={t("party.select")}
    />
  {/if}
</div>
