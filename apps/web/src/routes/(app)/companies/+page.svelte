<script lang="ts">
  import { applyAction, enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { Trash2 } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CompanyForm from "$lib/modules/companies/CompanyForm.svelte";
  import { COMPANY_STATUSES, statusPillClass } from "$lib/modules/companies/status";
  import ContactDraftField from "$lib/modules/contacts/ContactDraftField.svelte";

  let { data, form } = $props();

  let showCreate = $state(false);

  // The create form's lookups stream in behind the list. Held in state rather than awaited in the
  // markup: a re-run load hands us a *new* promise, and an `{#await}` would fall back to its
  // pending branch and remount the form, throwing away the contacts the user had picked.
  let createForm = $state<Awaited<typeof data.createForm> | null>(null);
  $effect(() => {
    void data.createForm.then((resolved) => (createForm = resolved));
  });

  let deleteId = $state("");
  let deleteName = $state("");
  let confirmDelete = $state(false);

  const filtered = $derived(
    data.statusFilter
      ? data.companies.filter((c) => c.status === data.statusFilter)
      : data.companies,
  );

  function setStatusFilter(status: string) {
    const url = new URL(page.url);
    if (status && status !== data.statusFilter) url.searchParams.set("status", status);
    else url.searchParams.delete("status");
    void goto(url, { keepFocus: true, noScroll: true });
  }
</script>

<svelte:head>
  <title>{t("companies.title")}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-neutral-900">{t("companies.title")}</h1>
    <p class="mt-1 text-sm text-neutral-500">{t("companies.count", { count: data.total })}</p>
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}
  >
    {t("companies.new")}
  </button>
</div>

<!-- Search + status filter pills -->
<div class="mb-4 flex flex-wrap items-center gap-2">
  <SearchInput placeholder={t("companies.search_placeholder")} />
  {#each COMPANY_STATUSES as status (status)}
    <button
      class="rounded-full px-3 py-1 text-xs font-medium
        {data.statusFilter === status
        ? 'ring-2 ring-brand ' + statusPillClass(status)
        : statusPillClass(status) + ' opacity-70 hover:opacity-100'}"
      onclick={() => setStatusFilter(status)}>{t(`companies.status.${status}`)}</button
    >
  {/each}
  {#if data.statusFilter}
    <button
      class="text-xs text-neutral-500 underline hover:text-neutral-900"
      onclick={() => setStatusFilter("")}
    >
      {t("tasks.filter.clear")}
    </button>
  {/if}
</div>

{#if showCreate}
  <!-- Same field set as the edit surface (CompanyForm), plus the contact persons — which only a
       not-yet-created client needs to pick up front. -->
  {#if createForm}
    <form
      method="POST"
      action="?/create"
      use:enhance={() =>
        async ({ result, update }) => {
          if (result.type === "success") {
            await update();
            showCreate = false;
            return;
          }
          // Leave the form standing on a rejected save: closing it would take the error message
          // down with it, along with everything typed and every contact picked.
          await applyAction(result);
        }}
      class="mb-6 rounded-xl border border-neutral-200 bg-white p-4"
    >
      <CompanyForm
        members={createForm.members}
        definitions={createForm.definitions}
        locale={data.locale}
        idPrefix="new-company"
      >
        <ContactDraftField
          contacts={createForm.contacts}
          definitions={createForm.contactDefinitions}
          locale={data.locale}
        />
      </CompanyForm>
      {#if form?.error}
        <p class="mt-2 text-sm text-red-600">{t(form.error)}</p>
      {/if}
      <div class="mt-4 flex gap-2">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
        <button
          type="button"
          class="rounded-lg border border-neutral-300 px-4 py-2 text-sm"
          onclick={() => (showCreate = false)}
        >
          {t("common.cancel")}
        </button>
      </div>
    </form>
  {:else}
    <div class="mb-6 h-64 animate-pulse rounded-xl border border-neutral-200 bg-white"></div>
  {/if}
{/if}

{#if filtered.length === 0}
  <div class="rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center">
    <p class="font-medium text-neutral-900">{t("companies.empty")}</p>
    <p class="mt-1 text-sm text-neutral-500">{t("companies.empty_hint")}</p>
  </div>
{:else}
  <ul class="divide-y divide-neutral-200 rounded-xl border border-neutral-200 bg-white">
    {#each filtered as company (company.id)}
      <li
        class="flex items-center gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl hover:bg-neutral-50"
      >
        <a href="/companies/{company.id}" class="min-w-0 flex-1">
          <span class="font-medium text-neutral-900">{company.name}</span>
          {#if company.website}
            <span class="ml-2 truncate text-sm text-neutral-500">{company.website}</span>
          {/if}
        </a>
        <span
          class="shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium {statusPillClass(
            company.status,
          )}"
        >
          {t(`companies.status.${company.status}`)}
        </span>
        <ActionsMenu
          items={[
            {
              label: t("common.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => {
                deleteId = company.id;
                deleteName = company.name;
                confirmDelete = true;
              },
            },
          ]}
        />
      </li>
    {/each}
  </ul>
{/if}

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("companies.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>
