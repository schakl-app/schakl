<script lang="ts">
  import { Pencil, Power, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import type { components } from "$lib/core/api/schema";

  type Provider = components["schemas"]["ProviderRead"];

  let { data, form } = $props();

  const KINDS = ["email", "dns", "registrar", "hosting"] as const;

  let showModal = $state(false);
  let editing = $state<Provider | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleActive = $state("");

  // Radio selection is component state, never a one-way checked (docs/UX.md): a mark
  // rendered one-way can vanish on hydration and the save then posts nothing.
  let newKind = $state("email");

  function openCreate() {
    editing = null;
    newKind = "email";
    showModal = true;
  }
  function openEdit(p: Provider) {
    editing = p;
    showModal = true;
  }
  function requestToggle(p: Provider) {
    toggleId = p.id;
    toggleActive = String(!p.active);
    toggleForm?.requestSubmit();
  }
  function requestDelete(p: Provider) {
    deleteId = p.id;
    confirmDelete = true;
  }
</script>

<svelte:head>
  <title>{t("settings.providers.title")}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
    <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
    <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.providers.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.providers.subtitle")}</p>
  </div>
  <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white" onclick={openCreate}
    >{t("settings.providers.new")}</button
  >
</div>

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.providers.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("settings.providers.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.providers as provider (provider.id)}
        <li class="flex items-center gap-3 px-4 py-3 {provider.active ? '' : 'opacity-50'}">
          <span class="rounded-md bg-surface px-2 py-0.5 text-xs font-medium text-text-muted"
            >{t(`providers.kind.${provider.kind}`)}</span
          >
          <span class="flex-1 text-sm text-text">{provider.name}</span>
          <ActionsMenu
            items={[
              { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(provider) },
              {
                label: provider.active ? t("common.deactivate") : t("common.activate"),
                icon: Power,
                onclick: () => requestToggle(provider),
              },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => requestDelete(provider),
              },
            ]}
          />
        </li>
      {/each}
    </ul>
  {/if}
</section>

<Modal
  bind:open={showModal}
  title={editing ? t("settings.providers.edit") : t("settings.providers.new")}
>
  {#key editing?.id ?? "new"}
    <form
      method="POST"
      action="?/save"
      class="space-y-4"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") showModal = false;
          void update({ reset: false });
        }}
    >
      {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
      <div>
        <label for="p-name" class="mb-1 block text-sm text-text"
          >{t("settings.providers.name")}</label
        >
        <input
          id="p-name"
          name="name"
          required
          value={editing?.name ?? ""}
          class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
        />
      </div>
      <div>
        <label for="p-kind" class="mb-1 block text-sm text-text"
          >{t("settings.providers.kind")}</label
        >
        {#if editing}
          <p class="text-sm text-text-muted">{t(`providers.kind.${editing.kind}`)}</p>
        {:else}
          <div class="flex flex-wrap gap-2">
            {#each KINDS as kind (kind)}
              <label class="flex items-center gap-1.5 text-sm text-text">
                <input type="radio" name="kind" value={kind} bind:group={newKind} />
                {t(`providers.kind.${kind}`)}
              </label>
            {/each}
          </div>
        {/if}
      </div>
      <input
        type="hidden"
        name="position"
        value={editing?.position ?? data.providers.length * 10 + 10}
      />
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showModal = false)}>{t("common.cancel")}</button
        >
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
      </div>
    </form>
  {/key}
</Modal>

<form bind:this={toggleForm} method="POST" action="?/toggle" use:enhance class="hidden">
  <input type="hidden" name="id" value={toggleId} />
  <input type="hidden" name="active" value={toggleActive} />
</form>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("settings.providers.delete")}
  message={t("settings.providers.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
