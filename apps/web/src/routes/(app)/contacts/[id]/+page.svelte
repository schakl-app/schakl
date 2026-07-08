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
  import { COMPANY_STATUSES } from "$lib/modules/companies/status";

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

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{fullName}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <a href="/contacts" class="text-sm text-neutral-500 hover:text-neutral-900"
      >← {t("contacts.title")}</a
    >
    <h1 class="mt-2 text-xl font-semibold text-neutral-900">{fullName}</h1>
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
<section class="mb-4 rounded-xl border border-neutral-200 bg-white p-5">
  <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t("contacts.companies")}</h2>
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
    class="rounded-xl border border-neutral-200 bg-white p-5"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="first_name" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.first_name")}</label
        >
        <input
          id="first_name"
          name="first_name"
          required
          value={contact.first_name}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="last_name" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.last_name")}</label
        >
        <input
          id="last_name"
          name="last_name"
          value={contact.last_name ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.email")}</label
        >
        <input
          id="email"
          name="email"
          type="email"
          value={contact.email ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label for="phone" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.phone")}</label
        >
        <input
          id="phone"
          name="phone"
          value={contact.phone ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
      </div>
      <div class="sm:col-span-2">
        <label for="job_title" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("contacts.job_title")}</label
        >
        <input
          id="job_title"
          name="job_title"
          value={contact.job_title ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
      </div>
    </div>

    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-neutral-100 pt-4">
        <CustomFieldsForm definitions={data.definitions} values={custom} locale={data.locale} />
      </div>
    {/if}

    {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >{t("common.save")}</button
      >
      <button
        type="button"
        class="rounded-lg border border-neutral-300 px-4 py-2 text-sm"
        onclick={() => (editing = false)}>{t("common.cancel")}</button
      >
    </div>
  </form>
{:else}
  <div class="grid gap-4">
    <section class="rounded-xl border border-neutral-200 bg-white p-5">
      <dl class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
            {t("contacts.email")}
          </dt>
          <dd class="mt-1 text-sm text-neutral-900">{contact.email ?? "—"}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
            {t("contacts.phone")}
          </dt>
          <dd class="mt-1 text-sm text-neutral-900">{contact.phone ?? "—"}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
            {t("contacts.job_title")}
          </dt>
          <dd class="mt-1 text-sm text-neutral-900">{contact.job_title ?? "—"}</dd>
        </div>
      </dl>
    </section>

    {#if data.definitions.length > 0}
      <section class="rounded-xl border border-neutral-200 bg-white p-5">
        <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t("contacts.panel.custom")}</h2>
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
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label for="cc-name" class="mb-1 block text-sm font-medium text-neutral-700"
            >{t("companies.name")}</label
          >
          <input id="cc-name" name="name" value={draftCompanyName} required class={inputClass} />
        </div>
        <div>
          <label for="cc-status" class="mb-1 block text-sm font-medium text-neutral-700"
            >{t("companies.field.status")}</label
          >
          <select id="cc-status" name="status" class={inputClass}>
            {#each COMPANY_STATUSES as status (status)}
              <option value={status} selected={status === "active"}
                >{t(`companies.status.${status}`)}</option
              >
            {/each}
          </select>
        </div>
        <div class="sm:col-span-2">
          <label for="cc-website" class="mb-1 block text-sm font-medium text-neutral-700"
            >{t("companies.website")}</label
          >
          <input id="cc-website" name="website" placeholder="https://…" class={inputClass} />
        </div>
      </div>
      {#if companyDefinitions.length > 0}
        <CustomFieldsForm definitions={companyDefinitions} locale={data.locale} />
      {:else}
        <input type="hidden" name="custom" value={"{}"} />
      {/if}
      {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2 pt-1">
        <button
          type="button"
          class="rounded-lg border border-neutral-300 px-4 py-2 text-sm"
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
