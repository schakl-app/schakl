<script lang="ts">
  /** Instellingen → Contactmoment-typen (#174): rename, add and deactivate the org's
   *  interaction kinds — the contact-types screen's shape. `email` is system-owned: only
   *  the gmail feed writes it, so it can be relabelled but never deactivated or deleted. */
  import { Pencil, Power, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { kindLabel, type InteractionKindDef } from "$lib/modules/interactions/format";

  let { data, form } = $props();

  let showModal = $state(false);
  let editing = $state<InteractionKindDef | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleActive = $state("");

  function openCreate() {
    editing = null;
    showModal = true;
  }
  function openEdit(kind: InteractionKindDef) {
    editing = kind;
    showModal = true;
  }
  function requestToggle(kind: InteractionKindDef) {
    toggleId = kind.id;
    toggleActive = String(!kind.active);
    toggleForm?.requestSubmit();
  }
  function requestDelete(kind: InteractionKindDef) {
    deleteId = kind.id;
    confirmDelete = true;
  }

  function menuItems(kind: InteractionKindDef) {
    const items = [{ label: t("common.edit"), icon: Pencil, onclick: () => openEdit(kind) }];
    if (kind.key !== "email") {
      items.push(
        {
          label: kind.active ? t("common.deactivate") : t("common.activate"),
          icon: Power,
          onclick: () => requestToggle(kind),
        },
        {
          label: t("common.delete"),
          icon: Trash2,
          danger: true,
          onclick: () => requestDelete(kind),
        } as (typeof items)[number],
      );
    }
    return items;
  }
</script>

<svelte:head>
  <title>{t("settings.interaction_kinds.title")}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
    <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.interaction_kinds.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.interaction_kinds.subtitle")}</p>
  </div>
  <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white" onclick={openCreate}
    >{t("settings.interaction_kinds.new")}</button
  >
</div>

{#if form?.error}<p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.kinds.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("settings.interaction_kinds.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.kinds as kind (kind.id)}
        <li class="flex items-center gap-3 px-4 py-3 {kind.active ? '' : 'opacity-50'}">
          <span class="flex-1 text-sm text-text">{kindLabel(kind, data.locale)}</span>
          {#if kind.key === "email"}
            <span class="text-xs text-text-muted">{t("settings.interaction_kinds.system")}</span>
          {/if}
          <span class="text-xs text-text-muted">{kind.key}</span>
          <ActionsMenu items={menuItems(kind)} />
        </li>
      {/each}
    </ul>
  {/if}
</section>

<Modal
  bind:open={showModal}
  title={editing ? t("settings.interaction_kinds.edit") : t("settings.interaction_kinds.new")}
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
          <label for="ik-key" class="mb-1 block text-sm text-text"
            >{t("settings.interaction_kinds.key")}</label
          >
          <input
            id="ik-key"
            name="key"
            required
            pattern="[a-z0-9_]+"
            class="w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand"
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.interaction_kinds.key_hint")}</p>
        </div>
      {/if}
      {#key editing?.id ?? "new"}
        <I18nTextField
          label={t("common.label_field")}
          basename="label"
          values={editing?.label_i18n ?? {}}
          idPrefix="ik"
        />
      {/key}
      <input
        type="hidden"
        name="position"
        value={editing?.position ?? data.kinds.length * 10 + 10}
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
  title={t("settings.interaction_kinds.delete")}
  message={t("settings.interaction_kinds.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
