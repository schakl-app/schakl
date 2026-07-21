<script lang="ts">
  /**
   * Company-detail panel: the contact persons attached to this client (CLAUDE.md §6, docs/UX.md).
   * A chip field ({@link LinkField}) attaches existing contacts by type-ahead — the primary one is
   * brand-coloured — and typing an unknown name opens the *full* new-contact dialog (real fields +
   * the tenant's custom fields), which creates the contact and attaches it in one step.
   *
   * The panel owns its own use/edit toggle rather than riding the page's: the client page's ⋯ →
   * Bewerken edits the *client's* fields, a different surface. Attaching, detaching and promoting
   * a contact are definition changes, so they only appear once this panel is in edit mode.
   */
  import { Check, Pencil } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";

  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import LinkField from "$lib/core/ui/LinkField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import PhoneInput from "$lib/core/ui/PhoneInput.svelte";

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelContact {
    id: string;
    first_name: string;
    last_name: string | null;
    email: string | null;
    phone?: string | null;
    job_title: string | null;
    is_primary?: boolean;
  }

  const contacts = $derived((data.contacts ?? []) as PanelContact[]);
  const candidates = $derived((data.candidates ?? []) as PanelContact[]);
  const definitions = $derived((data.definitions ?? []) as CustomFieldDefinition[]);
  const locale = $derived((page.data.locale as string) ?? "nl");

  const fullName = (c: PanelContact) => [c.first_name, c.last_name].filter(Boolean).join(" ");

  const links = $derived(
    contacts.map((c) => ({
      id: c.id,
      label: fullName(c),
      hint: c.job_title ?? c.email ?? undefined,
      is_primary: Boolean(c.is_primary),
    })),
  );
  const pickItems = $derived(
    candidates.map((c) => ({ value: c.id, label: fullName(c), hint: c.email ?? undefined })),
  );

  // Use mode is the default; the ⋯ menu opens edit mode (docs/UX.md §3).
  let editing = $state(false);

  // --- quick-create dialog (opened by typing an unknown name) ------------------
  let showCreate = $state(false);
  let draftFirst = $state("");
  let draftLast = $state("");

  function openCreate(query: string) {
    const parts = query.trim().split(/\s+/);
    draftFirst = parts.shift() ?? "";
    draftLast = parts.join(" ");
    showCreate = true;
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<!-- The panel's <h2> is rendered by the host page, so the toggle sits at the top of the body. -->
<div class="mb-3 flex justify-end">
  <ActionsMenu
    compact
    items={[
      {
        label: editing ? t("common.done") : t("common.edit"),
        icon: editing ? Check : Pencil,
        onclick: () => (editing = !editing),
      },
    ]}
  />
</div>

{#if links.length === 0}
  <p class="mb-3 text-sm text-text-muted">{t("contacts.empty")}</p>
{/if}

<LinkField
  {links}
  {editing}
  candidates={pickItems}
  idField="contact_id"
  linkAction="?/linkContact"
  unlinkAction="?/unlinkContact"
  primaryAction="?/setPrimaryContact"
  id="company-contact-picker"
  placeholder={t("contacts.add_person")}
  chipHref={(cid) => `/contacts/${cid}`}
  labels={{
    primary: t("contacts.primary"),
    makePrimary: t("contacts.make_primary"),
    remove: t("contacts.unlink"),
  }}
  oncreate={openCreate}
/>

<!-- Quick-add without entering edit mode (owner feedback): the same full create-and-attach
     dialog the type-ahead opens, one click away like every other panel's add button. -->
{#if !editing && can(page.data.user, "contacts.contact.write")}
  <button
    type="button"
    class="mt-3 inline-block text-xs text-brand hover:underline"
    onclick={() => openCreate("")}
  >
    ＋ {t("contacts.new")}
  </button>
{/if}

<Modal bind:open={showCreate} title={t("contacts.new")}>
  {#key draftFirst + draftLast + String(showCreate)}
    <form
      method="POST"
      action="?/createContact"
      use:enhance={() =>
        ({ update }) => {
          showCreate = false;
          void update();
        }}
      class="space-y-3"
    >
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="qc-contact-first" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.first_name")}</label
          >
          <input
            id="qc-contact-first"
            name="first_name"
            value={draftFirst}
            required
            class={inputClass}
          />
        </div>
        <div>
          <label for="qc-contact-last" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.last_name")}</label
          >
          <input id="qc-contact-last" name="last_name" value={draftLast} class={inputClass} />
        </div>
        <div>
          <label for="qc-contact-email" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.email")}</label
          >
          <input id="qc-contact-email" name="email" type="email" class={inputClass} />
        </div>
        <div>
          <label for="qc-contact-phone" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.phone")}</label
          >
          <PhoneInput id="qc-contact-phone" name="phone" />
        </div>
        <div class="sm:col-span-2">
          <label for="qc-contact-job" class="mb-1 block text-sm font-medium text-text"
            >{t("contacts.job_title")}</label
          >
          <input id="qc-contact-job" name="job_title" class={inputClass} />
        </div>
      </div>
      {#if definitions.length > 0}
        <CustomFieldsForm {definitions} {locale} />
      {:else}
        <input type="hidden" name="custom" value={"{}"} />
      {/if}
      <div class="flex justify-end gap-2 pt-1">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showCreate = false)}
        >
          {t("common.cancel")}
        </button>
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.create")}
        </button>
      </div>
    </form>
  {/key}
</Modal>
