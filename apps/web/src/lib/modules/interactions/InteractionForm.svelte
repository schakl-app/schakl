<script lang="ts">
  /**
   * The manual contactmoment form (meeting / call / note), rendered inside a `Modal` by the
   * panel body. One save button; posts to the **host page's** `?/createInteraction` or
   * `?/updateInteraction` action (a panel edits through its host, docs/UX.md).
   *
   * The date+time post as the tenant's wall clock (naive); the API attaches the org zone, so
   * a hand-typed 14:00 lands on the same timeline instant the reader sees.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import RichTextEditor from "$lib/core/ui/RichTextEditor.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import ContactQuickCreate from "$lib/modules/contacts/ContactQuickCreate.svelte";

  import {
    type InteractionItem,
    type InteractionKindDef,
    instantToLocal,
    kindLabel,
    manualKinds,
  } from "./format";

  let {
    interaction = null,
    prefill = {},
    mentions = [],
    onsaved,
  }: {
    /** Existing row when editing; null for create. */
    interaction?: InteractionItem | null;
    /** The host entity's link, stamped on new rows (e.g. `{ company_id }`). */
    prefill?: Record<string, string | null | undefined>;
    /** Org members offered by the note editor's @ autocomplete (#151). */
    mentions?: { id: string; name: string }[];
    onsaved?: () => void;
  } = $props();

  const local = interaction ? instantToLocal(interaction.occurred_at) : null;
  let kind = $state(interaction?.kind ?? "");
  let date = $state(local?.date ?? new Date().toISOString().slice(0, 10));
  let time = $state(local?.time ?? "");
  let error = $state("");

  // Kinds are tenant-defined (#174), fetched once per session (module-level cache). The
  // list shows the active ones, plus the row's own kind when editing — a deactivated kind
  // must stay pickable on the rows that already carry it.
  const locale = $derived((page.data.locale as string | undefined) ?? "nl");
  let allKinds = $state<InteractionKindDef[]>([]);
  $effect(() => {
    void manualKinds().then((fetched) => {
      allKinds = fetched;
      if (!kind) kind = fetched.find((k) => k.active)?.key ?? "";
    });
  });
  const kinds = $derived(allKinds.filter((k) => k.active || k.key === kind));

  const DIRECTIONS = ["none", "inbound", "outbound"] as const;

  // contact_id is a visible picker now (#173), so it leaves the hidden-prefill spread.
  const links = $derived(
    interaction
      ? {}
      : Object.fromEntries(
          Object.entries(prefill).filter(
            ([field, v]) => field !== "contact_id" && typeof v === "string" && v.length > 0,
          ),
        ),
  );

  // --- contact person (#173): pick, clear, or inline-create — never leave the form ------- //
  const hostCompanyId = $derived(
    interaction?.company_id ??
      (typeof prefill.company_id === "string" ? prefill.company_id : null),
  );
  // Deliberate initial capture: the host keys this form per row, so props never swap in place.
  // svelte-ignore state_referenced_locally
  let contactId = $state(
    interaction?.contact_id ??
      (typeof prefill.contact_id === "string" ? prefill.contact_id : "") ??
      "",
  );
  let contactOptions = $state<{ value: string; label: string; hint?: string }[]>([]);
  $effect(() => {
    // Host company's roster first; an org without links there falls back to all contacts.
    const scope = hostCompanyId ? `&company_id=${hostCompanyId}` : "";
    void (async () => {
      let response = await fetch(`/api/v1/contacts?limit=200${scope}`, {
        headers: { accept: "application/json" },
      });
      let items: { id: string; first_name: string; last_name?: string | null; email?: string | null }[] =
        response.ok ? ((await response.json()).items ?? []) : [];
      if (items.length === 0 && scope) {
        response = await fetch("/api/v1/contacts?limit=200", {
          headers: { accept: "application/json" },
        });
        items = response.ok ? ((await response.json()).items ?? []) : [];
      }
      contactOptions = items.map((c) => ({
        value: c.id,
        label: `${c.first_name} ${c.last_name ?? ""}`.trim(),
        hint: c.email ?? undefined,
      }));
      // The row's own contact stays pickable even when outside the fetched scope.
      if (contactId && interaction?.contact_name && !items.some((c) => c.id === contactId)) {
        contactOptions = [{ value: contactId, label: interaction.contact_name }, ...contactOptions];
      }
    })();
  });

  let qcOpen = $state(false);
  let qcName = $state("");
  let contactDefinitions = $state<CustomFieldDefinition[] | null>(null);
  async function quickCreateContact(query: string) {
    qcName = query;
    if (contactDefinitions === null) {
      const response = await fetch("/api/v1/custom-fields/definitions?entity_type=contact", {
        headers: { accept: "application/json" },
      });
      contactDefinitions = response.ok ? await response.json() : [];
    }
    qcOpen = true;
  }
  // The quick-create action answers with the new contact's id; auto-select it (docs/UX.md).
  let handledCreate = $state("");
  $effect(() => {
    const created = page.form?.inlineCreated as { slot: string; id: string } | undefined;
    if (created?.slot !== "interaction_contact" || created.id === handledCreate) return;
    handledCreate = created.id;
    if (!contactOptions.some((c) => c.value === created.id)) {
      contactOptions = [...contactOptions, { value: created.id, label: qcName || "—" }];
    }
    contactId = created.id;
  });
</script>

<form
  method="POST"
  action={interaction ? "?/updateInteraction" : "?/createInteraction"}
  class="space-y-4"
  use:enhance={() =>
    async ({ result, update }) => {
      if (result.type === "failure") {
        error = String(result.data?.error ?? "errors.validation");
        return;
      }
      error = "";
      await update({ reset: false });
      onsaved?.();
    }}
>
  {#if interaction}
    <input type="hidden" name="id" value={interaction.id} />
  {/if}
  {#each Object.entries(links) as [field, value] (field)}
    <input type="hidden" name={field} {value} />
  {/each}

  <div class="grid gap-4 sm:grid-cols-2">
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.kind")}</span>
      <select
        name="kind"
        bind:value={kind}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
      >
        {#each kinds as option (option.key)}
          <option value={option.key}>{kindLabel(option, locale)}</option>
        {/each}
      </select>
    </label>
    <div class="grid grid-cols-2 gap-2">
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.date")}</span>
        <DateInput name="occurred_date" bind:value={date} required />
      </label>
      <label class="block text-sm">
        <span class="mb-1 block font-medium text-text">{t("interactions.field.time")}</span>
        <TimeInput name="occurred_time" bind:value={time} />
      </label>
    </div>
  </div>

  <label class="block text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.subject")}</span>
    <input
      name="subject"
      value={interaction?.subject ?? ""}
      required
      maxlength="500"
      class="w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
    />
  </label>

  <div class="block text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.contact")}</span>
    <Combobox
      items={contactOptions}
      name="contact_id"
      bind:value={contactId}
      placeholder={t("interactions.field.contact_placeholder")}
      oncreate={(query) => void quickCreateContact(query)}
    />
  </div>

  {#if kind === "call"}
    <label class="block text-sm">
      <span class="mb-1 block font-medium text-text">{t("interactions.field.direction")}</span>
      <select
        name="direction"
        value={interaction?.direction ?? "none"}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
      >
        {#each DIRECTIONS as option (option)}
          <option value={option}>{t(`interactions.direction.${option}`)}</option>
        {/each}
      </select>
    </label>
  {/if}

  <div class="text-sm">
    <span class="mb-1 block font-medium text-text">{t("interactions.field.notes")}</span>
    <RichTextEditor name="body_text" value={interaction?.body_text ?? ""} rows={4} {mentions} />
  </div>

  {#if error}
    <p class="text-sm text-red-600">{t(error)}</p>
  {/if}

  <div class="flex justify-end">
    <button
      type="submit"
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    >
      {t("common.save")}
    </button>
  </div>
</form>

<ContactQuickCreate
  bind:open={qcOpen}
  name={qcName}
  definitions={contactDefinitions ?? []}
  locale={(page.data.locale as string | undefined) ?? "nl"}
  action="?/createInteractionContact"
  pickerSlot="interaction_contact"
  error={(page.form?.qcError as string | undefined) ?? null}
/>
