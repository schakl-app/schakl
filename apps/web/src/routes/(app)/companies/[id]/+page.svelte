<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import CompanyAIActions from "$lib/core/ai/CompanyAIActions.svelte";
  import { editIntent } from "$lib/core/edit-intent";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { companyPanelComponent } from "$lib/core/registry";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import AvatarStack from "$lib/core/ui/AvatarStack.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import CompanyForm from "$lib/modules/companies/CompanyForm.svelte";
  import { statusPillClass } from "$lib/modules/companies/status";
  import ContactDraftField from "$lib/modules/contacts/ContactDraftField.svelte";

  let { data, form } = $props();

  // Panels are contributed by enabled modules and composed here — the "attach to company" hub.
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const company = $derived(data.company);
  const assignees = $derived(company.assignees ?? []);

  // The contact persons currently on this client, primary first — derived from the org's contacts
  // rather than fetched again, since each one carries the companies it is linked to.
  const companyContacts = $derived(
    data.contacts
      .map((c) => ({ id: c.id, link: c.companies?.find((l) => l.company_id === company.id) }))
      .filter((c) => c.link !== undefined)
      .map((c) => ({ contact_id: c.id, is_primary: Boolean(c.link?.is_primary) }))
      .sort((a, b) => Number(b.is_primary) - Number(a.is_primary)),
  );

  // Opened straight into edit when reached from the overview's ⋯ → Bewerken (#78).
  let showEdit = $state(editIntent());
  let confirmDelete = $state(false);

  // AI digest + report drafts (#130): rendered only when the reporting feature is on.
  const hasReporting = $derived(aiEnabled(page.data.user, "reporting"));
</script>

<svelte:head>
  <title>{pageTitle(company.name)}</title>
</svelte:head>

<div class="mb-6">
  <div class="mt-2 flex flex-wrap items-start justify-between gap-3">
    <div>
      <div class="flex items-center gap-3">
        {#if company.logo_file_id}
          <!-- The client's own logo (#196) — client data, never tenant branding. -->
          <img
            src={`/api/v1/companies/${company.id}/logo`}
            alt=""
            class="h-9 w-9 shrink-0 rounded-lg border border-border object-contain"
          />
        {/if}
        <h1 class="text-xl font-semibold text-text">{company.name}</h1>
        <span
          class="rounded-full px-2.5 py-0.5 text-xs font-medium {statusPillClass(company.status)}"
        >
          {t(`companies.status.${company.status}`)}
        </span>
      </div>
      {#if company.website}
        <a
          href={company.website.startsWith("http") ? company.website : `https://${company.website}`}
          target="_blank"
          rel="noopener noreferrer"
          class="mt-1 inline-block text-sm text-text-muted hover:text-brand">{company.website} ↗</a
        >
      {/if}
      {#if assignees.length > 0}
        <p class="mt-1 flex flex-wrap items-center gap-2 text-sm text-text-muted">
          <span>{t("companies.field.responsible")}:</span>
          <AvatarStack {assignees} members={data.members} />
        </p>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <a
        href={`/tasks?company_id=${company.id}`}
        class="rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
      >
        {t("companies.actions.new_task")}
      </a>
      <a
        href="/time"
        class="rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
      >
        {t("companies.actions.log_time")}
      </a>
      {#if hasReporting}
        <CompanyAIActions companyId={company.id} companyName={company.name} />
      {/if}
      <ActionsMenu
        items={[
          { label: t("common.edit"), icon: Pencil, onclick: () => (showEdit = true) },
          {
            label: t("common.delete"),
            icon: Trash2,
            danger: true,
            onclick: () => (confirmDelete = true),
          },
        ]}
      />
    </div>
  </div>
  {#if data.templates.length > 0}
    <form method="POST" action="?/applyTemplate" use:enhance class="mt-3 flex items-center gap-2">
      <select
        name="template_id"
        class="rounded-lg border border-border px-2 py-1.5 text-sm"
        required
      >
        {#each data.templates as template (template.id)}
          <option value={template.id}>{template.name}</option>
        {/each}
      </select>
      <button
        class="rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
      >
        {t("companies.actions.apply_template")}
      </button>
      {#if form?.templateApplied}
        <span class="text-xs text-green-600 dark:text-green-400"
          >{t("companies.template_applied")}</span
        >
      {/if}
    </form>
  {/if}
</div>

<div class="grid gap-4">
  {#each data.panels as panel (panel.key)}
    {@const spec = companyPanelComponent(enabled, panel.key)}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="mb-4 text-sm font-semibold text-text">{t(panel.title_key)}</h2>
      {#if spec}
        {@const PanelComponent = spec.component}
        <PanelComponent companyId={company.id} data={panel.data} members={data.members} />
      {:else}
        <pre class="overflow-x-auto text-xs text-text-muted">{JSON.stringify(
            panel.data,
            null,
            2,
          )}</pre>
      {/if}
    </section>
  {/each}
</div>

<Modal bind:open={showEdit} title={t("companies.edit")}>
  <form
    method="POST"
    action="?/update"
    enctype="multipart/form-data"
    use:enhance={() =>
      ({ update }) => {
        showEdit = false;
        void update();
      }}
    class="space-y-3"
  >
    <!-- Same component the create form uses: one definition of a client's fields. Every editable
         field is here, contact persons included — an edit surface that hides a field the view
         shows sends you hunting for it (docs/UX.md). -->
    <CompanyForm
      {company}
      members={data.members}
      definitions={data.definitions}
      locale={data.locale}
      idPrefix="edit-company"
    >
      <ContactDraftField
        contacts={data.contacts}
        definitions={data.contactDefinitions}
        locale={data.locale}
        value={companyContacts}
        id="edit-company-contacts"
      />
    </CompanyForm>
    <div>
      <!-- Per-client logo (#196): shown on this page's header and on the client's portal
           dashboard. Not the agency's branding — that lives under Instellingen. -->
      <label for="edit-company-logo" class="mb-1 block text-sm font-medium text-text"
        >{t("companies.logo.label")}</label
      >
      <input
        id="edit-company-logo"
        name="logo_file"
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        class="block w-full text-sm text-text-muted file:mr-3 file:cursor-pointer file:rounded-lg file:border file:border-solid file:border-border file:bg-transparent file:px-3 file:py-1.5 file:text-sm file:text-text hover:file:border-brand"
      />
      {#if company.logo_file_id}
        <label class="mt-2 flex items-center gap-2 text-sm text-text">
          <input type="checkbox" name="logo_remove" value="1" />
          {t("companies.logo.remove")}
        </label>
      {/if}
      <p class="mt-1 text-xs text-text-muted">{t("companies.logo.hint")}</p>
    </div>
    {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
    <div class="flex justify-end gap-2 pt-1">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (showEdit = false)}
      >
        {t("common.cancel")}
      </button>
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
    </div>
  </form>
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("companies.delete_confirm", { name: company.name })}
  action="?/delete"
/>
