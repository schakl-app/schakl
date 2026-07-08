<script lang="ts">
  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import { COMPANY_STATUSES, statusPillClass } from "$lib/modules/companies/status";

  let { data, form } = $props();

  let showCreate = $state(false);
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

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
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
      onclick={() => setStatusFilter(status)}
    >{t(`companies.status.${status}`)}</button>
  {/each}
  {#if data.statusFilter}
    <button class="text-xs text-neutral-500 underline hover:text-neutral-900" onclick={() => setStatusFilter("")}>
      {t("tasks.filter.clear")}
    </button>
  {/if}
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
    <div class="grid gap-3 sm:grid-cols-3">
      <div>
        <label for="name" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("companies.name")}
        </label>
        <input id="name" name="name" required class={inputClass} />
      </div>
      <div>
        <label for="website" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("companies.website")}
        </label>
        <input id="website" name="website" class={inputClass} />
      </div>
      <div>
        <label for="status" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("companies.field.status")}
        </label>
        <select id="status" name="status" class={inputClass}>
          {#each COMPANY_STATUSES as status (status)}
            <option value={status} selected={status === "active"}>{t(`companies.status.${status}`)}</option>
          {/each}
        </select>
      </div>
    </div>
    <p class="mt-2 text-xs text-neutral-400">{t("companies.status_hint")}</p>
    {#if form?.error}
      <p class="mt-2 text-sm text-red-600">{t(form.error)}</p>
    {/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
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
{/if}

{#if filtered.length === 0}
  <div class="rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center">
    <p class="font-medium text-neutral-900">{t("companies.empty")}</p>
    <p class="mt-1 text-sm text-neutral-500">{t("companies.empty_hint")}</p>
  </div>
{:else}
  <ul class="divide-y divide-neutral-200 overflow-hidden rounded-xl border border-neutral-200 bg-white">
    {#each filtered as company (company.id)}
      <li class="flex items-center gap-3 px-4 py-3 hover:bg-neutral-50">
        <a href="/companies/{company.id}" class="min-w-0 flex-1">
          <span class="font-medium text-neutral-900">{company.name}</span>
          {#if company.website}
            <span class="ml-2 truncate text-sm text-neutral-500">{company.website}</span>
          {/if}
        </a>
        <span class="shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium {statusPillClass(company.status)}">
          {t(`companies.status.${company.status}`)}
        </span>
        <button
          class="text-sm text-neutral-400 hover:text-red-600"
          aria-label={t("common.delete")}
          onclick={() => {
            deleteId = company.id;
            deleteName = company.name;
            confirmDelete = true;
          }}
        >
          {t("common.delete")}
        </button>
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
