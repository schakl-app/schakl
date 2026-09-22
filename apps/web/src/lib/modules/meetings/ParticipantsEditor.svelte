<script lang="ts" module>
  /** One person in the room — the API's `MeetingParticipant`, with the nulls spelled out. */
  export interface Participant {
    name: string;
    user_id: string | null;
    contact_id: string | null;
    speaker: string | null;
  }
  /** The slot a contact quick-create echoes back, so only this editor picks it up. */
  export const PARTICIPANT_SLOT = "meeting-participant";
</script>

<script lang="ts">
  /**
   * Who was in the meeting: a colleague, a contact of the client, or somebody known by name
   * alone — and, once the transcript is back, which speaker label (S1, S2 …) each of them is.
   *
   * One editor for both places it is asked, because the two screens must agree about what a
   * participant *is*: the recorder collects the roster before the recording (the person knows who
   * is at the table), and the review screen finishes it — pairing labels with people, adding the
   * one who walked in late. A colleague comes from the shared `MemberPicker`, a contact from the
   * client's own roster (fetched once the client is known, the task dialogs' pattern) with the
   * ordinary inline-create behind it (docs/UX.md), and anybody else is a name. The label is a
   * property of the person, never the other way round: that is what lets an action item be
   * grounded in *who* said it and a client's promise become a task assigned to that contact.
   */
  import Plus from "@lucide/svelte/icons/plus";
  import X from "@lucide/svelte/icons/x";

  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import { memberLabel, type PickerMember } from "$lib/core/members";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import MemberPicker from "$lib/core/ui/MemberPicker.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";

  type Kind = "employee" | "contact" | "other";

  let {
    participants = $bindable([]),
    members = [],
    companyId = "",
    companyName = null,
    speakerLabels = [],
    editable = true,
    definitions = [],
    locale,
    created = null,
    qcError = null,
    createAction = "?/createContact",
  }: {
    participants?: Participant[];
    members?: readonly PickerMember[];
    /** The meeting's client: its contacts are what the contact picker offers. */
    companyId?: string;
    companyName?: string | null;
    /** The provider's labels found in the transcript; empty before there is one. */
    speakerLabels?: string[];
    editable?: boolean;
    /** The tenant's contact custom fields, for the full create dialog. */
    definitions?: CustomFieldDefinition[];
    locale: string;
    /** The page's `form?.inlineCreated` — a contact made from this editor's ＋ lands here. */
    created?: { slot: string; id: string } | null;
    qcError?: string | null;
    createAction?: string;
  } = $props();

  let kind = $state<Kind>("employee");
  let pickedUser = $state("");
  let pickedContact = $state("");
  let otherName = $state("");
  let qcOpen = $state(false);
  let qcName = $state("");

  // The client's contacts, fetched once the row says which client — never shipped on every
  // render of a page whose picker nobody opened (docs/PERFORMANCE.md).
  let contacts = $state<{ id: string; name: string }[]>([]);
  let contactsFor = $state("");
  async function loadContacts(target: string): Promise<{ id: string; name: string }[]> {
    const response = await fetch(`/api/v1/contacts?limit=200&count=false&company_id=${target}`, {
      headers: { accept: "application/json" },
    });
    if (!response.ok) return [];
    interface ContactRow {
      id: string;
      first_name: string;
      last_name?: string | null;
    }
    const rows: ContactRow[] = (await response.json()).items ?? [];
    return rows.map((c) => ({
      id: c.id,
      name: [c.first_name, c.last_name].filter(Boolean).join(" "),
    }));
  }
  $effect(() => {
    const target = companyId;
    if (!target) {
      contacts = [];
      contactsFor = "";
      return;
    }
    if (target === contactsFor) return;
    void loadContacts(target).then((rows) => {
      contacts = rows;
      contactsFor = target;
    });
  });

  // A contact created from the ＋ joins the roster: re-read the client's list (it holds the
  // new name) and add the one row that came back.
  let handledCreate = $state<string | null>(null);
  $effect(() => {
    const hit = created;
    if (!hit || hit.slot !== PARTICIPANT_SLOT || hit.id === handledCreate) return;
    handledCreate = hit.id;
    void loadContacts(companyId).then((rows) => {
      contacts = rows;
      contactsFor = companyId;
      const row = rows.find((c) => c.id === hit.id);
      if (row && !participants.some((p) => p.contact_id === row.id)) {
        participants = [
          ...participants,
          { name: row.name, user_id: null, contact_id: row.id, speaker: null },
        ];
      }
    });
  });

  const contactItems = $derived(
    contacts
      .filter((c) => !participants.some((p) => p.contact_id === c.id))
      .map((c) => ({ value: c.id, label: c.name })),
  );
  const takenUsers = $derived(
    participants.map((p) => p.user_id).filter((id): id is string => !!id),
  );
  const KINDS: { key: Kind; label: () => string }[] = [
    { key: "employee", label: () => t("party.employee") },
    { key: "contact", label: () => t("party.contact") },
    { key: "other", label: () => t("meetings.participants.other") },
  ];

  function add() {
    let next: Participant | null = null;
    if (kind === "employee" && pickedUser) {
      const member = members.find((m) => m.user_id === pickedUser);
      next = {
        name: memberLabel(member) || pickedUser,
        user_id: pickedUser,
        contact_id: null,
        speaker: null,
      };
      pickedUser = "";
    } else if (kind === "contact" && pickedContact) {
      const contact = contacts.find((c) => c.id === pickedContact);
      if (!contact) return;
      next = { name: contact.name, user_id: null, contact_id: contact.id, speaker: null };
      pickedContact = "";
    } else if (kind === "other" && otherName.trim()) {
      next = { name: otherName.trim(), user_id: null, contact_id: null, speaker: null };
      otherName = "";
    }
    if (!next) return;
    participants = [...participants, next];
  }
  function remove(index: number) {
    participants = participants.filter((_, i) => i !== index);
  }
  /** A label names one person: picking it here takes it off whoever held it. */
  function setLabel(index: number, label: string) {
    participants = participants.map((p, i) => {
      if (i === index) return { ...p, speaker: label || null };
      if (label && p.speaker === label) return { ...p, speaker: null };
      return p;
    });
  }
  const unnamed = $derived(
    speakerLabels.filter((label) => !participants.some((p) => p.speaker === label)),
  );
  function kindOf(p: Participant): string {
    if (p.user_id) return t("party.employee");
    if (p.contact_id) return t("party.contact");
    return t("meetings.participants.other");
  }

  const smallInput =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-1.5 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<div class="space-y-3">
  {#if participants.length}
    <ul class="space-y-1.5">
      {#each participants as p, i (i)}
        <li class="flex flex-wrap items-center gap-2 text-sm">
          {#if speakerLabels.length}
            <select
              class="w-20 shrink-0 rounded-lg border border-border bg-surface-raised px-2 py-1 font-mono text-xs text-text"
              aria-label={t("meetings.participants.speaker_label")}
              value={p.speaker ?? ""}
              disabled={!editable}
              onchange={(e) => setLabel(i, (e.currentTarget as HTMLSelectElement).value)}
            >
              <option value="">—</option>
              {#each speakerLabels as label (label)}
                <option value={label}>{label}</option>
              {/each}
            </select>
          {/if}
          <span class="min-w-0 flex-1 truncate text-text">
            {#if p.contact_id}
              <a href={`/contacts/${p.contact_id}`} class="hover:underline">{p.name}</a>
            {:else}
              {p.name}
            {/if}
            <span class="ml-1 text-xs text-text-muted">{kindOf(p)}</span>
          </span>
          {#if editable}
            <button
              type="button"
              class="shrink-0 rounded p-1 text-text-muted hover:text-red-600"
              aria-label={t("common.remove")}
              onclick={() => remove(i)}><X size={14} /></button
            >
          {/if}
        </li>
      {/each}
    </ul>
  {:else}
    <p class="text-sm text-text-muted">{t("meetings.participants.empty")}</p>
  {/if}

  {#if speakerLabels.length && unnamed.length}
    <p class="text-xs text-text-muted">
      {t("meetings.participants.unnamed", { labels: unnamed.join(", ") })}
    </p>
  {/if}

  {#if editable}
    <div class="rounded-lg border border-dashed border-border p-3">
      <div class="mb-2 flex flex-wrap gap-1">
        {#each KINDS as option (option.key)}
          {#if option.key !== "contact" || companyId}
            <button
              type="button"
              class="rounded-lg border px-2.5 py-1 text-xs
                {kind === option.key
                ? 'border-brand bg-brand/10 font-medium text-brand'
                : 'border-border text-text-muted hover:text-text'}"
              onclick={() => (kind = option.key)}>{option.label()}</button
            >
          {/if}
        {/each}
      </div>
      <div class="flex items-start gap-2">
        <div class="min-w-0 flex-1">
          {#if kind === "employee"}
            <MemberPicker
              name="_participant_user"
              {members}
              bind:value={pickedUser}
              exclude={takenUsers}
              placeholder={t("tasks.assignees.add")}
              onselect={() => add()}
            />
          {:else if kind === "contact" && companyId}
            <Combobox
              items={contactItems}
              name="_participant_contact"
              bind:value={pickedContact}
              placeholder={t("meetings.participants.contact_placeholder")}
              onselect={() => add()}
              oncreate={(name) => {
                qcName = name;
                qcOpen = true;
              }}
            />
          {:else}
            <input
              class={smallInput}
              bind:value={otherName}
              placeholder={t("meetings.participants.other_placeholder")}
              onkeydown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  add();
                }
              }}
            />
          {/if}
        </div>
        {#if kind === "other"}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onclick={add}
            disabled={!otherName.trim()}
          >
            <Plus size={14} />
            {t("common.add")}
          </Button>
        {/if}
      </div>
      {#if kind === "contact" && !companyId}
        <p class="mt-2 text-xs text-text-muted">
          {t("meetings.participants.contact_needs_client")}
        </p>
      {/if}
    </div>
  {/if}
</div>

<ContactQuickCreate
  bind:open={qcOpen}
  name={qcName}
  linkCompany={companyId ? { id: companyId, name: companyName ?? "" } : null}
  pickerSlot={PARTICIPANT_SLOT}
  {definitions}
  {locale}
  action={createAction}
  error={qcError}
/>
