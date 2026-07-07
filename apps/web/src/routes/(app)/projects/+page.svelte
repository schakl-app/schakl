<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";

  let { data, form } = $props();

  let showCreate = $state(false);

  const STATUSES = ["active", "on_hold", "completed", "archived"] as const;

  const companyName = $derived((id: string | null | undefined) =>
    id ? (data.companies.find((c) => c.id === id)?.name ?? "") : "",
  );

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{t("projects.title")}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-neutral-900">{t("projects.title")}</h1>
    <p class="mt-1 text-sm text-neutral-500">{t("projects.count", { count: data.total })}</p>
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}
  >
    {t("projects.new")}
  </button>
</div>

{#if showCreate}
  <form
    method="POST"
    action="?/create"
    use:enhance={() =>
      ({ update }) => {
        void update().then(() => (showCreate = false));
      }}
    class="mb-6 rounded-xl border border-neutral-200 bg-white p-4"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div class="sm:col-span-2">
        <label for="name" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("projects.field.name")}
        </label>
        <input id="name" name="name" required class={inputClass} />
      </div>
      <div>
        <label for="company_id" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("projects.field.company")}
        </label>
        <select id="company_id" name="company_id" class={inputClass}>
          <option value="">{t("common.none")}</option>
          {#each data.companies as company (company.id)}
            <option value={company.id}>{company.name}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="status" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("projects.field.status")}
        </label>
        <select id="status" name="status" class={inputClass}>
          {#each STATUSES as s (s)}
            <option value={s}>{t(`projects.status.${s}`)}</option>
          {/each}
        </select>
      </div>
      <div>
        <label for="budget_hours" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("projects.field.budget_hours")}
        </label>
        <input id="budget_hours" name="budget_hours" type="number" min="0" step="0.5" class={inputClass} />
      </div>
      <div>
        <label for="budget_amount" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("projects.field.budget_amount")}
        </label>
        <input id="budget_amount" name="budget_amount" type="number" min="0" step="0.01" class={inputClass} />
      </div>
      <div>
        <label for="hourly_rate" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("projects.field.hourly_rate")}
        </label>
        <input id="hourly_rate" name="hourly_rate" type="number" min="0" step="0.01" class={inputClass} />
      </div>
      <div>
        <label for="start_date" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("projects.field.start_date")}
        </label>
        <input id="start_date" name="start_date" type="date" class={inputClass} />
      </div>
      <div>
        <label for="end_date" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("projects.field.end_date")}
        </label>
        <input id="end_date" name="end_date" type="date" class={inputClass} />
      </div>
      <div class="flex items-center gap-2 pt-6">
        <input id="billable_default" name="billable_default" type="checkbox" checked
          class="h-4 w-4 rounded border-neutral-300 text-brand focus:ring-brand" />
        <label for="billable_default" class="text-sm font-medium text-neutral-700">
          {t("projects.field.billable_default")}
        </label>
      </div>
    </div>

    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-neutral-100 pt-4">
        <CustomFieldsForm definitions={data.definitions} locale={data.locale} />
      </div>
    {/if}

    {#if form?.error}
      <p class="mt-2 text-sm text-red-600">{t(form.error)}</p>
    {/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
      <button type="button" class="rounded-lg border border-neutral-300 px-4 py-2 text-sm"
        onclick={() => (showCreate = false)}>
        {t("common.cancel")}
      </button>
    </div>
  </form>
{/if}

{#if data.projects.length === 0}
  <div class="rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center">
    <p class="font-medium text-neutral-900">{t("projects.empty")}</p>
    <p class="mt-1 text-sm text-neutral-500">{t("projects.empty_hint")}</p>
  </div>
{:else}
  <ul class="divide-y divide-neutral-200 overflow-hidden rounded-xl border border-neutral-200 bg-white">
    {#each data.projects as project (project.id)}
      <li class="flex items-center justify-between px-4 py-3 hover:bg-neutral-50">
        <a href="/projects/{project.id}" class="min-w-0 flex-1">
          <span class="font-medium text-neutral-900">{project.name}</span>
          {#if companyName(project.company_id)}
            <span class="ml-2 text-sm text-neutral-500">· {companyName(project.company_id)}</span>
          {/if}
        </a>
        <span class="mr-4 rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs font-medium text-neutral-600">
          {t(`projects.status.${project.status}`)}
        </span>
        <form method="POST" action="?/delete" use:enhance>
          <input type="hidden" name="id" value={project.id} />
          <button class="text-sm text-neutral-400 hover:text-red-600">{t("common.delete")}</button>
        </form>
      </li>
    {/each}
  </ul>
{/if}
