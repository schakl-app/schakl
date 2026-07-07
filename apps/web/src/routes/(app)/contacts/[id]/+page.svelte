<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import CustomFieldsForm from "$lib/core/customfields/CustomFieldsForm.svelte";
  import CustomFieldsView from "$lib/core/customfields/CustomFieldsView.svelte";

  let { data, form } = $props();

  let editing = $state(false);
  const contact = $derived(data.contact);
  const custom = $derived((contact.custom ?? {}) as Record<string, unknown>);
  const fullName = $derived(
    [contact.first_name, contact.last_name].filter(Boolean).join(" "),
  );
</script>

<svelte:head>
  <title>{fullName}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <a href="/contacts" class="text-sm text-neutral-500 hover:text-neutral-900">← {t("contacts.title")}</a>
    <h1 class="mt-2 text-xl font-semibold text-neutral-900">{fullName}</h1>
  </div>
  <button class="rounded-lg border border-neutral-300 px-4 py-2 text-sm" onclick={() => (editing = !editing)}>
    {editing ? t("common.cancel") : t("common.edit")}
  </button>
</div>

{#if editing}
  <form method="POST" action="?/update"
    use:enhance={() => ({ update }) => { void update().then(() => (editing = false)); }}
    class="rounded-xl border border-neutral-200 bg-white p-5">
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="first_name" class="mb-1 block text-sm font-medium text-neutral-700">{t("contacts.first_name")}</label>
        <input id="first_name" name="first_name" required value={contact.first_name}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
      </div>
      <div>
        <label for="last_name" class="mb-1 block text-sm font-medium text-neutral-700">{t("contacts.last_name")}</label>
        <input id="last_name" name="last_name" value={contact.last_name ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
      </div>
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-neutral-700">{t("contacts.email")}</label>
        <input id="email" name="email" type="email" value={contact.email ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
      </div>
      <div>
        <label for="phone" class="mb-1 block text-sm font-medium text-neutral-700">{t("contacts.phone")}</label>
        <input id="phone" name="phone" value={contact.phone ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
      </div>
      <div>
        <label for="job_title" class="mb-1 block text-sm font-medium text-neutral-700">{t("contacts.job_title")}</label>
        <input id="job_title" name="job_title" value={contact.job_title ?? ""}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
      </div>
      <div>
        <label for="company_id" class="mb-1 block text-sm font-medium text-neutral-700">{t("contacts.company")}</label>
        <select id="company_id" name="company_id" class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm">
          <option value="">{t("common.none")}</option>
          {#each data.companies as company (company.id)}
            <option value={company.id} selected={company.id === contact.company_id}>{company.name}</option>
          {/each}
        </select>
      </div>
    </div>

    {#if data.definitions.length > 0}
      <div class="mt-4 border-t border-neutral-100 pt-4">
        <CustomFieldsForm definitions={data.definitions} values={custom} locale={data.locale} />
      </div>
    {/if}

    {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
    <div class="mt-4 flex gap-2">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">{t("common.save")}</button>
    </div>
  </form>
{:else}
  <div class="grid gap-4">
    <section class="rounded-xl border border-neutral-200 bg-white p-5">
      <dl class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">{t("contacts.email")}</dt>
          <dd class="mt-1 text-sm text-neutral-900">{contact.email ?? "—"}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">{t("contacts.phone")}</dt>
          <dd class="mt-1 text-sm text-neutral-900">{contact.phone ?? "—"}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">{t("contacts.job_title")}</dt>
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

    <form method="POST" action="?/delete" use:enhance>
      <button class="text-sm text-neutral-400 hover:text-red-600">{t("common.delete")}</button>
    </form>
  </div>
{/if}
