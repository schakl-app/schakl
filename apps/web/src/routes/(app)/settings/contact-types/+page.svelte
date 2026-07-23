<script lang="ts">
  import { Pencil, Power, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { contactTypeLabel, type ContactType } from "$lib/modules/contacts/types";

  let { data, form } = $props();

  let showModal = $state(false);
  let editing = $state<ContactType | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleActive = $state("");
  const busy = new InFlight();

  function openCreate() {
    editing = null;
    showModal = true;
  }
  function openEdit(ct: ContactType) {
    editing = ct;
    showModal = true;
  }
  function requestToggle(ct: ContactType) {
    toggleId = ct.id;
    toggleActive = String(!ct.active);
    toggleForm?.requestSubmit();
  }
  function requestDelete(ct: ContactType) {
    deleteId = ct.id;
    confirmDelete = true;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.contact_types.title"))}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
    <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.contact_types.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.contact_types.subtitle")}</p>
  </div>
  <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white" onclick={openCreate}
    >{t("settings.contact_types.new")}</button
  >
</div>

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.types.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("settings.contact_types.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.types as ct (ct.id)}
        <li class="flex items-center gap-3 px-4 py-3 {ct.active ? '' : 'opacity-50'}">
          <span class="flex-1 text-sm text-text">{contactTypeLabel(ct, data.locale)}</span>
          <span class="text-xs text-text-muted">{ct.key}</span>
          <ActionsMenu
            items={[
              { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(ct) },
              {
                label: ct.active ? t("common.deactivate") : t("common.activate"),
                icon: Power,
                onclick: () => requestToggle(ct),
              },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => requestDelete(ct),
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
  title={editing ? t("settings.contact_types.edit") : t("settings.contact_types.new")}
>
  {#key editing?.id ?? "new"}
    <form
      method="POST"
      action="?/save"
      class="space-y-4"
      use:enhance={busy.wrap("", () => ({ result, update }) => {
        if (result.type === "success") showModal = false;
        void update({ reset: false });
      })}
    >
      {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
      {#key editing?.id ?? "new"}
        <I18nTextField
          label={t("common.label_field")}
          basename="label"
          values={editing?.label_i18n ?? {}}
          idPrefix="ct"
        />
      {/key}
      <input
        type="hidden"
        name="position"
        value={editing?.position ?? data.types.length * 10 + 10}
      />
      {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
      <div class="flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showModal = false)}>{t("common.cancel")}</button
        >
        <Button loading={busy.active}>{t("common.save")}</Button>
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
  title={t("settings.contact_types.delete")}
  message={t("settings.contact_types.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
