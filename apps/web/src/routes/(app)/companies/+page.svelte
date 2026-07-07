<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";

  let { data, form } = $props();

  let showCreate = $state(false);
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
      <div>
        <label for="name" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("companies.name")}
        </label>
        <input
          id="name"
          name="name"
          required
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="website" class="mb-1 block text-sm font-medium text-neutral-700">
          {t("companies.website")}
        </label>
        <input
          id="website"
          name="website"
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
    </div>
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

{#if data.companies.length === 0}
  <div class="rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center">
    <p class="font-medium text-neutral-900">{t("companies.empty")}</p>
    <p class="mt-1 text-sm text-neutral-500">{t("companies.empty_hint")}</p>
  </div>
{:else}
  <ul class="divide-y divide-neutral-200 overflow-hidden rounded-xl border border-neutral-200 bg-white">
    {#each data.companies as company (company.id)}
      <li class="flex items-center justify-between px-4 py-3 hover:bg-neutral-50">
        <a href="/companies/{company.id}" class="min-w-0 flex-1">
          <span class="font-medium text-neutral-900">{company.name}</span>
          {#if company.website}
            <span class="ml-2 truncate text-sm text-neutral-500">{company.website}</span>
          {/if}
        </a>
        <form method="POST" action="?/delete" use:enhance>
          <input type="hidden" name="id" value={company.id} />
          <button
            class="text-sm text-neutral-400 hover:text-red-600"
            aria-label={t("common.delete")}
          >
            {t("common.delete")}
          </button>
        </form>
      </li>
    {/each}
  </ul>
{/if}
