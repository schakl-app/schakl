<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import { t } from "$lib/core/i18n";
  import { companyPanelComponent } from "$lib/core/registry";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { COMPANY_STATUSES, statusPillClass } from "$lib/modules/companies/status";

  let { data, form } = $props();

  // Panels are contributed by enabled modules and composed here — the "attach to company" hub.
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const company = $derived(data.company);

  const memberItems = $derived(
    data.members.map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );
  const responsibleName = $derived(
    company.responsible_user_id
      ? (data.members.find((m) => m.user_id === company.responsible_user_id)?.full_name ??
          data.members.find((m) => m.user_id === company.responsible_user_id)?.email ??
          null)
      : null,
  );

  let showEdit = $state(false);
  let confirmDelete = $state(false);

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{company.name}</title>
</svelte:head>

<div class="mb-6">
  <a href="/companies" class="text-sm text-neutral-500 hover:text-neutral-900">
    ← {t("companies.title")}
  </a>
  <div class="mt-2 flex flex-wrap items-start justify-between gap-3">
    <div>
      <div class="flex items-center gap-3">
        <h1 class="text-xl font-semibold text-neutral-900">{company.name}</h1>
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
          class="mt-1 inline-block text-sm text-neutral-500 hover:text-brand">{company.website} ↗</a
        >
      {/if}
      {#if responsibleName}
        <p class="mt-1 text-sm text-neutral-500">
          {t("companies.field.responsible")}:
          <span class="text-neutral-700">{responsibleName}</span>
        </p>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <a
        href={`/tasks?company_id=${company.id}`}
        class="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-600 hover:border-brand hover:text-brand"
      >
        {t("companies.actions.new_task")}
      </a>
      <a
        href="/time"
        class="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-600 hover:border-brand hover:text-brand"
      >
        {t("companies.actions.log_time")}
      </a>
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
        class="rounded-lg border border-neutral-300 px-2 py-1.5 text-sm"
        required
      >
        {#each data.templates as template (template.id)}
          <option value={template.id}>{template.name}</option>
        {/each}
      </select>
      <button
        class="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-600 hover:border-brand hover:text-brand"
      >
        {t("companies.actions.apply_template")}
      </button>
      {#if form?.templateApplied}
        <span class="text-xs text-green-600">{t("companies.template_applied")}</span>
      {/if}
    </form>
  {/if}
</div>

<div class="grid gap-4">
  {#each data.panels as panel (panel.key)}
    {@const spec = companyPanelComponent(enabled, panel.key)}
    <section class="rounded-xl border border-neutral-200 bg-white p-5">
      <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t(panel.title_key)}</h2>
      {#if spec}
        {@const PanelComponent = spec.component}
        <PanelComponent companyId={company.id} data={panel.data} />
      {:else}
        <pre class="overflow-x-auto text-xs text-neutral-500">{JSON.stringify(
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
    use:enhance={() =>
      ({ update }) => {
        showEdit = false;
        void update();
      }}
    class="space-y-3"
  >
    <div>
      <label for="edit-name" class="mb-1 block text-sm font-medium text-neutral-700"
        >{t("companies.name")}</label
      >
      <input id="edit-name" name="name" value={company.name} required class={inputClass} />
    </div>
    <div class="grid grid-cols-2 gap-3">
      <div>
        <label for="edit-website" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("companies.website")}</label
        >
        <input id="edit-website" name="website" value={company.website ?? ""} class={inputClass} />
      </div>
      <div>
        <label for="edit-status" class="mb-1 block text-sm font-medium text-neutral-700"
          >{t("companies.field.status")}</label
        >
        <select id="edit-status" name="status" class={inputClass}>
          {#each COMPANY_STATUSES as status (status)}
            <option value={status} selected={company.status === status}
              >{t(`companies.status.${status}`)}</option
            >
          {/each}
        </select>
      </div>
    </div>
    <div>
      <label for="edit-responsible" class="mb-1 block text-sm font-medium text-neutral-700"
        >{t("companies.field.responsible")}</label
      >
      <Combobox
        items={memberItems}
        name="responsible_user_id"
        id="edit-responsible"
        value={company.responsible_user_id ?? ""}
        placeholder={t("common.unassigned")}
      />
    </div>
    <div>
      <label for="edit-notes" class="mb-1 block text-sm font-medium text-neutral-700"
        >{t("companies.notes")}</label
      >
      <textarea id="edit-notes" name="notes" rows="3" class={inputClass}
        >{company.notes ?? ""}</textarea
      >
    </div>
    {#if data.definitions.length > 0}
      <CustomFieldsForm
        definitions={data.definitions}
        values={company.custom ?? {}}
        locale={data.locale}
      />
    {:else}
      <input type="hidden" name="custom" value={JSON.stringify(company.custom ?? {})} />
    {/if}
    <p class="text-xs text-neutral-400">{t("companies.status_hint")}</p>
    {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
    <div class="flex justify-end gap-2 pt-1">
      <button
        type="button"
        class="rounded-lg border border-neutral-300 px-4 py-2 text-sm"
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
