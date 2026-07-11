<script lang="ts">
  import { Copy, Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { localeName } from "$lib/core/roles/name";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let { data, form } = $props();

  let showCreate = $state(false);
  let duplicateFrom = $state("");
  let deleteId = $state("");
  let confirmDelete = $state(false);

  const roles = $derived(data.roles);
  const locale = $derived(data.locale ?? "nl");

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  function openDuplicate(id: string) {
    duplicateFrom = id;
    showCreate = true;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.roles.title"))}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between gap-4">
  <div>
    <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
    <h1 class="mt-1 text-xl font-semibold text-text">{t("settings.roles.title")}</h1>
    <p class="mt-1 max-w-2xl text-sm text-text-muted">{t("settings.roles.subtitle")}</p>
  </div>
  <button
    class="shrink-0 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => {
      duplicateFrom = "";
      showCreate = true;
    }}
  >
    {t("settings.roles.new")}
  </button>
</div>

{#if form?.error}
  <p
    class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-400"
  >
    {t(form.error)}
  </p>
{/if}

{#if roles.length === 0}
  <p
    class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center text-sm text-text-muted"
  >
    {t("settings.roles.empty")}
  </p>
{:else}
  <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised">
    {#each roles as role (role.id)}
      <li class="flex items-center gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl">
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <a
              href="/settings/roles/{role.id}"
              data-sveltekit-preload-data="hover"
              class="truncate font-medium text-text hover:text-brand">{localeName(role, locale)}</a
            >
            {#if role.is_system}
              <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
                {t("settings.roles.system")}
              </span>
            {/if}
          </div>
          <p class="mt-0.5 text-sm text-text-muted">
            {role.permissions.includes("*")
              ? t("settings.users.effective_all")
              : t("settings.roles.permission_count", { count: role.permissions.length })}
            ·
            {t("settings.roles.member_count", { count: role.member_count })}
          </p>
        </div>

        <ActionsMenu
          items={[
            {
              label: t("common.edit"),
              icon: Pencil,
              href: `/settings/roles/${role.id}`,
            },
            {
              label: t("settings.roles.duplicate"),
              icon: Copy,
              onclick: () => openDuplicate(role.id),
            },
            ...(role.is_system
              ? []
              : [
                  {
                    label: t("common.delete"),
                    icon: Trash2,
                    danger: true,
                    onclick: () => {
                      deleteId = role.id;
                      confirmDelete = true;
                    },
                  },
                ]),
          ]}
        />
      </li>
    {/each}
  </ul>
{/if}

<p class="mt-4 text-xs text-text-muted">{t("settings.roles.system_hint")}</p>

<Modal
  bind:open={showCreate}
  title={duplicateFrom ? t("settings.roles.duplicate") : t("settings.roles.new")}
>
  <form
    method="POST"
    action="?/create"
    use:enhance
    class="space-y-4"
    data-testid="role-create-form"
  >
    <input type="hidden" name="from" value={duplicateFrom} />
    <div>
      <label for="key" class="mb-1 block text-sm font-medium text-text"
        >{t("settings.roles.key")}</label
      >
      <input id="key" name="key" required pattern="[a-z0-9][a-z0-9_-]*" class={inputClass} />
      <p class="mt-1 text-xs text-text-muted">{t("settings.roles.key_hint")}</p>
    </div>
    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label for="name_nl" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.roles.name_nl")}</label
        >
        <input id="name_nl" name="name_nl" class={inputClass} />
      </div>
      <div>
        <label for="name_en" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.roles.name_en")}</label
        >
        <input id="name_en" name="name_en" class={inputClass} />
      </div>
    </div>
    <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
      {t("settings.roles.create")}
    </button>
  </form>
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("settings.roles.delete_confirm")}
  action="?/delete"
  fields={{ role_id: deleteId }}
/>
