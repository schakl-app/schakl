<script lang="ts">
  /** Instellingen → Uren-typen (#176): rename, add and deactivate the org's time-entry
   *  types — the contact-types / interaction-kinds screen shape. */
  import { Pencil, Power, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { entryTypeLabel, type TimeEntryTypeDef } from "$lib/modules/time/format";

  let { data, form } = $props();

  let showModal = $state(false);
  let editing = $state<TimeEntryTypeDef | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleActive = $state("");

  function openCreate() {
    editing = null;
    showModal = true;
  }
  function openEdit(entryType: TimeEntryTypeDef) {
    editing = entryType;
    showModal = true;
  }
  function requestToggle(entryType: TimeEntryTypeDef) {
    toggleId = entryType.id;
    toggleActive = String(!entryType.active);
    toggleForm?.requestSubmit();
  }
  function requestDelete(entryType: TimeEntryTypeDef) {
    deleteId = entryType.id;
    confirmDelete = true;
  }
</script>

<svelte:head>
  <title>{t("settings.time_entry_types.title")}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
    <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.time_entry_types.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.time_entry_types.subtitle")}</p>
  </div>
  <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white" onclick={openCreate}
    >{t("settings.time_entry_types.new")}</button
  >
</div>

{#if form?.error}<p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.types.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("settings.time_entry_types.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.types as entryType (entryType.id)}
        <li class="flex items-center gap-3 px-4 py-3 {entryType.active ? '' : 'opacity-50'}">
          <span class="flex-1 text-sm text-text">{entryTypeLabel(entryType, data.locale)}</span>
          <span class="text-xs text-text-muted">{entryType.key}</span>
          <ActionsMenu
            items={[
              { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(entryType) },
              {
                label: entryType.active ? t("common.deactivate") : t("common.activate"),
                icon: Power,
                onclick: () => requestToggle(entryType),
              },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => requestDelete(entryType),
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
  title={editing ? t("settings.time_entry_types.edit") : t("settings.time_entry_types.new")}
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
      {#if !editing}
        <div>
          <label for="tet-key" class="mb-1 block text-sm text-text"
            >{t("settings.time_entry_types.key")}</label
          >
          <input
            id="tet-key"
            name="key"
            required
            pattern="[a-z0-9_]+"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.time_entry_types.key_hint")}</p>
        </div>
      {/if}
      {#key editing?.id ?? "new"}
        <I18nTextField
          label={t("common.label_field")}
          basename="label"
          values={editing?.label_i18n ?? {}}
          idPrefix="tet"
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
  title={t("settings.time_entry_types.delete")}
  message={t("settings.time_entry_types.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
