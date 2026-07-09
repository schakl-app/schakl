<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CustomFieldsView from "$lib/core/customfields/CustomFieldsView.svelte";
  import type { CustomFieldDefinition } from "$lib/core/customfields/types";
  import LinkField from "$lib/core/ui/LinkField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import CompanyForm from "$lib/modules/companies/CompanyForm.svelte";

  let { data, form } = $props();

  let editing = $state(false);
  let confirmDelete = $state(false);
  const contact = $derived(data.contact);
  const custom = $derived((contact.custom ?? {}) as Record<string, unknown>);
  const fullName = $derived([contact.first_name, contact.last_name].filter(Boolean).join(" "));
  const companyDefinitions = $derived((data.companyDefinitions ?? []) as CustomFieldDefinition[]);

  // --- linked clients (many-to-many) ----------------------------------------
  interface CompanyLink {
    company_id: string;
    name: string;
    is_primary: boolean;
  }
  const companyLinks = $derived((contact.companies ?? []) as CompanyLink[]);
  const links = $derived(
    companyLinks.map((c) => ({ id: c.company_id, label: c.name, is_primary: c.is_primary })),
  );
  const linkedIds = $derived(new Set(companyLinks.map((c) => c.company_id)));
  const candidateCompanies = $derived(
    data.companies.filter((c) => !linkedIds.has(c.id)).map((c) => ({ value: c.id, label: c.name })),
  );

  let showCreateCompany = $state(false);
  let draftCompanyName = $state("");
  function openCreateCompany(query: string) {
    draftCompanyName = query.trim();
    showCreateCompany = true;
  }
</script>

<svelte:head>
  <title>{fullName}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <a href="/contacts" class="text-sm text-text-muted hover:text-text">← {t("contacts.title")}</a>
    <h1 class="mt-2 text-xl font-semibold text-text">{fullName}</h1>
  </div>
  <ActionsMenu
    items={[
      {
        label: editing ? t("common.cancel") : t("common.edit"),
        icon: Pencil,
        onclick: () => (editing = !editing),
      },
      {
        label: t("common.delete"),
        icon: Trash2,
        danger: true,
        onclick: () => (confirmDelete = true),
      },
    ]}
  />
</div>

<!-- Linked clients: chips + type-ahead, immediate actions (docs/UX.md use-mode). -->
<section class="mb-4 rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-4 text-sm font-semibold text-text">{t("contacts.companies")}</h2>
  <LinkField
    {links}
    candidates={candidateCompanies}
    idField="company_id"
    linkAction="?/linkCompany"
    unlinkAction="?/unlinkCompany"
    primaryAction="?/setPrimaryCompany"
    id="contact-company-picker"
    placeholder={t("contacts.add_client")}
    chipHref={(cid) => `/companies/${cid}`}
    labels={{
      primary: t("contacts.primary"),
      makePrimary: t("contacts.make_primary"),
      remove: t("contacts.unlink"),
    }}
    oncreate={openCreateCompany}
  />
</section>

{#if editing}
  <form
    method="POST"
    action="?/update"
    use:enhance={() =>
      ({ update }) => {
        void update().then(() => (editing = false));
      }}
    class="rounded-xl border border-border bg-surface-raised p-5"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="first_name" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.first_name")}</label
        >
        <input
          id="first_name"
          name="first_name"
          required
          value={contact.first_name}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="last_name" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.last_name")}</label
        >
        <input
          id="last_name"
          name="last_name"
          value={contact.last_name ?? ""}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.email")}</label
        >
        <input
          id="email"
          name="email"
          type="email"
          value={contact.email ?? ""}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="phone" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.phone")}</label
        >
        <input
          id="phone"
          name="phone"
          value={contact.phone ?? ""}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
      <div class="sm:col-span-2">
        <label for="job_title" class="mb-1 block text-sm font-medium text-text"
          >{t("contacts.job_title")}</label
        >
        <input
          id="job_title"
          name="job_title"
          value={contact.job_title ?? ""}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm"
        />
      </div>
    </div>

    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-border pt-4">
        <CustomFieldsForm definitions={data.definitions} values={custom} locale={data.locale} />
      </div>
    {/if}

    {#if form?.error}<p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >{t("common.save")}</button
      >
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (editing = false)}>{t("common.cancel")}</button
      >
    </div>
  </form>
{:else}
  <div class="grid gap-4">
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <dl class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("contacts.email")}
          </dt>
          <dd class="mt-1 text-sm text-text">{contact.email ?? "—"}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("contacts.phone")}
          </dt>
          <dd class="mt-1 text-sm text-text">{contact.phone ?? "—"}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("contacts.job_title")}
          </dt>
          <dd class="mt-1 text-sm text-text">{contact.job_title ?? "—"}</dd>
        </div>
      </dl>
    </section>

    {#if data.definitions.length > 0}
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <h2 class="mb-4 text-sm font-semibold text-text">{t("contacts.panel.custom")}</h2>
        <CustomFieldsView definitions={data.definitions} values={custom} locale={data.locale} />
      </section>
    {/if}
  </div>
{/if}

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("contacts.delete_confirm", { name: fullName })}
  action="?/delete"
/>

<!-- Quick-create a new client and attach it to this contact, without leaving the page. -->
<Modal bind:open={showCreateCompany} title={t("contacts.new_client")}>
  {#key draftCompanyName + String(showCreateCompany)}
    <form
      method="POST"
      action="?/createCompany"
      use:enhance={() =>
        ({ update }) => {
          showCreateCompany = false;
          void update();
        }}
      class="space-y-3"
    >
      <!-- The full client form, prefilled with what was typed — never a name-only stub. -->
      <CompanyForm
        company={{ name: draftCompanyName }}
        members={data.members}
        definitions={companyDefinitions}
        locale={data.locale}
        idPrefix="quick-company"
      />
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2 pt-1">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm"
          onclick={() => (showCreateCompany = false)}>{t("common.cancel")}</button
        >
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >{t("common.create")}</button
        >
      </div>
    </form>
  {/key}
</Modal>
