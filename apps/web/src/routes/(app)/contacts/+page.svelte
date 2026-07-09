<script lang="ts">
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import SearchInput from "$lib/core/ui/SearchInput.svelte";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";

  let { data, form } = $props();

  let showCreate = $state(false);
  let deleteId = $state("");
  let deleteName = $state("");
  let confirmDelete = $state(false);

  function fullName(c: { first_name: string; last_name?: string | null }) {
    return [c.first_name, c.last_name].filter(Boolean).join(" ");
  }
</script>

<svelte:head>
  <title>{t("contacts.title")}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("contacts.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("contacts.count", { count: data.total })}</p>
  </div>
  <div class="ml-auto mr-3"><SearchInput /></div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showCreate = !showCreate)}
  >
    {t("contacts.new")}
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
    class="mb-6 rounded-xl border border-border bg-surface-raised p-4"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="first_name" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.first_name")}
        </label>
        <input
          id="first_name"
          name="first_name"
          required
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="last_name" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.last_name")}
        </label>
        <input
          id="last_name"
          name="last_name"
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.email")}
        </label>
        <input
          id="email"
          name="email"
          type="email"
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div>
        <label for="phone" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.phone")}
        </label>
        <input
          id="phone"
          name="phone"
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
      <div class="sm:col-span-2">
        <label for="job_title" class="mb-1 block text-sm font-medium text-text">
          {t("contacts.job_title")}
        </label>
        <input
          id="job_title"
          name="job_title"
          class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
      </div>
    </div>
    <p class="mt-2 text-xs text-text-muted">{t("contacts.link_client_hint")}</p>

    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-border pt-4">
        <CustomFieldsForm definitions={data.definitions} locale={data.locale} />
      </div>
    {/if}

    {#if form?.error}
      <p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm"
        onclick={() => (showCreate = false)}
      >
        {t("common.cancel")}
      </button>
    </div>
  </form>
{/if}

{#if data.contacts.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="font-medium text-text">{t("contacts.empty")}</p>
    <p class="mt-1 text-sm text-text-muted">{t("contacts.empty_hint")}</p>
  </div>
{:else}
  <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised">
    {#each data.contacts as contact (contact.id)}
      <li
        class="flex items-center justify-between gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl hover:bg-surface"
      >
        <a href="/contacts/{contact.id}" class="min-w-0 flex-1">
          <span class="font-medium text-text">{fullName(contact)}</span>
          {#if contact.email}
            <span class="ml-2 truncate text-sm text-text-muted">{contact.email}</span>
          {/if}
        </a>
        <ActionsMenu
          items={[
            {
              label: t("common.delete"),
              icon: Trash2,
              danger: true,
              onclick: () => {
                deleteId = contact.id;
                deleteName = fullName(contact);
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
  message={t("contacts.delete_confirm", { name: deleteName })}
  action="?/delete"
  fields={{ id: deleteId }}
/>
