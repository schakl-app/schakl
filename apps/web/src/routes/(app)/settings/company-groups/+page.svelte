<script lang="ts">
  /**
   * Instellingen → Klantgroepen (issue #191): the company data horizon's admin screen.
   *
   * A group is a tenant-defined set of companies; assigning a member to groups restricts what
   * they can *see* to the union of those groups' companies. No assignment = sees everything —
   * so this screen is also where "who is restricted" is visible at a glance.
   */
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  const memberName = (m: { full_name: string | null; email: string }) => m.full_name || m.email;

  // One group edits at a time; the edit surface has exactly one save (docs/UX.md).
  let editingId = $state<string | null>(null);
  let confirmDelete = $state(false);
  let deleteId = $state("");

  const inputClass =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.company_groups.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="text-xl font-semibold text-text">{t("settings.company_groups.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.company_groups.subtitle")}</p>
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<!-- New group -->
<form
  method="POST"
  action="?/create"
  use:enhance
  class="mb-6 flex max-w-md flex-wrap items-end gap-2"
>
  <div class="min-w-0 flex-1">
    <label for="new-group-name" class="mb-1 block text-sm font-medium text-text"
      >{t("settings.company_groups.new")}</label
    >
    <input
      id="new-group-name"
      name="name"
      required
      placeholder={t("settings.company_groups.name_placeholder")}
      class={inputClass}
    />
  </div>
  <button class="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:opacity-90">
    {t("common.add")}
  </button>
</form>

{#if data.groups.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
    <p class="text-sm text-text-muted">{t("settings.company_groups.empty")}</p>
  </div>
{/if}

<div class="space-y-4">
  {#each data.groups as group (group.id)}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      {#if editingId === group.id}
        <form method="POST" action="?/save" use:enhance class="space-y-4">
          <input type="hidden" name="id" value={group.id} />
          <div class="max-w-md">
            <label for="name-{group.id}" class="mb-1 block text-sm font-medium text-text"
              >{t("settings.company_groups.name")}</label
            >
            <input id="name-{group.id}" name="name" value={group.name} required class={inputClass} />
          </div>
          <div class="grid gap-4 md:grid-cols-2">
            <fieldset>
              <legend class="mb-2 text-sm font-medium text-text">
                {t("settings.company_groups.companies")}
              </legend>
              <div class="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-border p-3">
                {#each data.companies as company (company.id)}
                  <label class="flex items-center gap-2 text-sm text-text">
                    <input
                      type="checkbox"
                      name="company_ids"
                      value={company.id}
                      checked={group.company_ids?.includes(company.id)}
                    />
                    <span class="min-w-0 truncate">{company.name}</span>
                  </label>
                {:else}
                  <p class="text-sm text-text-muted">{t("settings.company_groups.no_companies")}</p>
                {/each}
              </div>
            </fieldset>
            <fieldset>
              <legend class="mb-2 text-sm font-medium text-text">
                {t("settings.company_groups.members")}
              </legend>
              <p class="mb-2 text-xs text-text-muted">
                {t("settings.company_groups.members_hint")}
              </p>
              <div class="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-border p-3">
                {#each data.members as member (member.membership_id)}
                  <label class="flex items-center gap-2 text-sm text-text">
                    <input
                      type="checkbox"
                      name="membership_ids"
                      value={member.membership_id}
                      checked={group.membership_ids?.includes(member.membership_id)}
                    />
                    <span class="min-w-0 truncate">{memberName(member)}</span>
                  </label>
                {/each}
              </div>
            </fieldset>
          </div>
          <div class="flex gap-2">
            <button
              class="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:opacity-90"
              onclick={() => setTimeout(() => (editingId = null), 0)}
            >
              {t("common.save")}
            </button>
            <button
              type="button"
              class="rounded-lg border border-border px-3 py-2 text-sm text-text"
              onclick={() => (editingId = null)}
            >
              {t("common.cancel")}
            </button>
          </div>
        </form>
      {:else}
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <h2 class="text-sm font-semibold text-text">{group.name}</h2>
            <p class="mt-1 text-sm text-text-muted">
              {t("settings.company_groups.summary", {
                companies: group.company_ids?.length ?? 0,
                members: group.membership_ids?.length ?? 0,
              })}
            </p>
          </div>
          <ActionsMenu
            items={[
              {
                label: t("common.edit"),
                icon: Pencil,
                onclick: () => (editingId = group.id),
              },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => {
                  deleteId = group.id;
                  confirmDelete = true;
                },
              },
            ]}
          />
        </div>
      {/if}
    </section>
  {/each}
</div>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("settings.company_groups.delete_title")}
  message={t("settings.company_groups.delete_message")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
