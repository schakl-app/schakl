<script lang="ts">
  import { Pencil, PowerOff, Power, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { fieldLabel } from "$lib/core/customfields/types";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let { data, form } = $props();

  let deleteId = $state("");
  let confirmDelete = $state(false);

  // Deactivate/activate is a non-destructive toggle; submitted from the ⋯ menu via one shared
  // hidden form (the kebab item can't post a form itself).
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleActiveValue = $state("true");
  function requestToggle(id: string, active: boolean) {
    toggleId = id;
    toggleActiveValue = String(active);
    setTimeout(() => toggleForm?.requestSubmit(), 0);
  }

  // Edit modal: pre-filled from the picked definition. Key + type are locked (shown read-only).
  type Def = (typeof data.definitions)[number];
  let showEdit = $state(false);
  let editDef = $state<Def | null>(null);
  const editIsSelect = $derived(
    editDef?.data_type === "select" || editDef?.data_type === "multi_select",
  );
  function optionsText(def: Def): string {
    return (def.options_json ?? [])
      .map((o) => `${o.value}|${o.label_i18n?.nl ?? o.label_i18n?.en ?? o.value}`)
      .join("\n");
  }
  function openEdit(def: Def) {
    editDef = def;
    showEdit = true;
  }

  const TYPES = [
    "text",
    "long_text",
    "number",
    "boolean",
    "date",
    "datetime",
    "select",
    "multi_select",
    "email",
    "url",
    "phone",
  ] as const;

  let selectedType = $state("text");
  const showOptions = $derived(selectedType === "select" || selectedType === "multi_select");

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.custom_fields.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.custom_fields.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.custom_fields.subtitle")}</p>
</div>

<!-- Entity type switcher -->
<div class="mb-4 flex flex-wrap gap-2">
  {#each data.entityTypes as et (et)}
    <a
      href={`?entity_type=${et}`}
      class="rounded-lg border px-3 py-1.5 text-sm"
      class:border-brand={et === data.entityType}
      class:text-brand={et === data.entityType}
      class:border-border={et !== data.entityType}
    >
      {t(`customfields.entity.${et}`)}
    </a>
  {/each}
</div>

<!-- Existing definitions -->
<div class="mb-6 rounded-xl border border-border bg-surface-raised">
  {#if data.definitions.length === 0}
    <p class="p-6 text-center text-sm text-text-muted">{t("settings.custom_fields.empty")}</p>
  {:else}
    <table class="w-full text-sm">
      <thead>
        <tr
          class="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted"
        >
          <th class="px-4 py-2 font-medium">{t("settings.custom_fields.label")}</th>
          <th class="px-4 py-2 font-medium">{t("settings.custom_fields.key")}</th>
          <th class="px-4 py-2 font-medium">{t("settings.custom_fields.type")}</th>
          <th class="px-4 py-2 font-medium">{t("common.required")}</th>
          <th class="px-4 py-2 text-right font-medium">{t("common.actions")}</th>
        </tr>
      </thead>
      <tbody>
        {#each data.definitions as def (def.id)}
          <tr class="border-b border-border" class:opacity-50={!def.active}>
            <td class="px-4 py-2 text-text">{fieldLabel(def, data.locale)}</td>
            <td class="px-4 py-2 font-mono text-xs text-text-muted">{def.key}</td>
            <td class="px-4 py-2 text-text-muted">{t(`customfields.type.${def.data_type}`)}</td>
            <td class="px-4 py-2 text-text-muted">{def.required ? t("common.required") : "—"}</td>
            <td class="px-4 py-2">
              <div class="flex items-center justify-end">
                <ActionsMenu
                  items={[
                    { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(def) },
                    {
                      label: def.active
                        ? t("settings.custom_fields.deactivate")
                        : t("settings.custom_fields.activate"),
                      icon: def.active ? PowerOff : Power,
                      onclick: () => requestToggle(def.id, def.active),
                    },
                    {
                      label: t("common.delete"),
                      icon: Trash2,
                      danger: true,
                      onclick: () => {
                        deleteId = def.id;
                        confirmDelete = true;
                      },
                    },
                  ]}
                />
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<!-- Create definition -->
<form
  method="POST"
  action="?/create"
  use:enhance
  class="rounded-xl border border-border bg-surface-raised p-5"
>
  <h2 class="mb-4 text-sm font-semibold text-text">{t("settings.custom_fields.new")}</h2>
  <input type="hidden" name="entity_type" value={data.entityType} />
  <div class="grid gap-3 sm:grid-cols-2">
    <div>
      <label for="key" class="mb-1 block text-sm font-medium text-text"
        >{t("settings.custom_fields.key")}</label
      >
      <input
        id="key"
        name="key"
        required
        pattern="[a-z][a-z0-9_]*"
        placeholder="vat_number"
        class="w-full rounded-lg border border-border px-3 py-2 text-sm"
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.custom_fields.key_hint")}</p>
    </div>
    <div>
      <label for="data_type" class="mb-1 block text-sm font-medium text-text"
        >{t("settings.custom_fields.type")}</label
      >
      <select
        id="data_type"
        name="data_type"
        bind:value={selectedType}
        class="w-full rounded-lg border border-border px-3 py-2 text-sm"
      >
        {#each TYPES as ty (ty)}<option value={ty}>{t(`customfields.type.${ty}`)}</option>{/each}
      </select>
    </div>
    <div class="sm:col-span-2">
      <I18nTextField label={t("common.label_field")} basename="label" idPrefix="label" />
    </div>
    {#if showOptions}
      <div class="sm:col-span-2">
        <label for="options" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.custom_fields.options")}</label
        >
        <!-- eslint-disable svelte/no-useless-mustaches -- \n needs JS-string escaping; a bare attribute would render it literally -->
        <textarea
          id="options"
          name="options"
          rows="3"
          placeholder={"gold|Gold\nsilver|Silver"}
          class="w-full rounded-lg border border-border px-3 py-2 font-mono text-xs"></textarea>
        <!-- eslint-enable svelte/no-useless-mustaches -->
        <p class="mt-1 text-xs text-text-muted">{t("settings.custom_fields.options_hint")}</p>
      </div>
    {/if}
    <div class="flex items-center gap-4">
      <label class="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" name="required" class="h-4 w-4 rounded border-border" />
        {t("common.required")}
      </label>
      <div class="flex items-center gap-2">
        <label for="position" class="text-sm text-text"
          >{t("settings.custom_fields.position")}</label
        >
        <input
          id="position"
          name="position"
          type="number"
          value="0"
          class="w-20 rounded-lg border border-border px-2 py-1 text-sm"
        />
      </div>
    </div>
  </div>
  {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
  <div class="mt-4">
    <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >{t("common.create")}</button
    >
  </div>
</form>

<!-- Shared hidden form: the ⋯ Deactiveren/Activeren item submits this. -->
<form method="POST" action="?/toggleActive" use:enhance bind:this={toggleForm} class="hidden">
  <input type="hidden" name="id" value={toggleId} />
  <input type="hidden" name="active" value={toggleActiveValue} />
</form>

<!-- Edit definition (key + type locked) -->
<Modal bind:open={showEdit} title={t("settings.custom_fields.edit")}>
  {#if editDef}
    {#key editDef.id}
      <form
        method="POST"
        action="?/update"
        use:enhance={() =>
          ({ update }) => {
            showEdit = false;
            void update();
          }}
        class="space-y-3"
      >
        <input type="hidden" name="id" value={editDef.id} />
        <input type="hidden" name="key" value={editDef.key} />
        <input type="hidden" name="data_type" value={editDef.data_type} />
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="mb-1 block text-sm font-medium text-text">
              {t("settings.custom_fields.key")}
            </div>
            <div
              class="flex items-center gap-1 rounded-lg border border-border bg-surface px-3 py-2 font-mono text-xs text-text-muted"
            >
              🔒 {editDef.key}
            </div>
            <p class="mt-1 text-xs text-text-muted">{t("settings.custom_fields.key_locked")}</p>
          </div>
          <div>
            <div class="mb-1 block text-sm font-medium text-text">
              {t("settings.custom_fields.type")}
            </div>
            <div
              class="flex items-center gap-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-muted"
            >
              🔒 {t(`customfields.type.${editDef.data_type}`)}
            </div>
            <p class="mt-1 text-xs text-text-muted">{t("settings.custom_fields.type_locked")}</p>
          </div>
        </div>
        {#key editDef.id}
          <I18nTextField
            label={t("common.label_field")}
            basename="label"
            values={editDef.label_i18n ?? {}}
            idPrefix="edit-label"
          />
        {/key}
        {#if editIsSelect}
          <div>
            <label for="edit-options" class="mb-1 block text-sm font-medium text-text"
              >{t("settings.custom_fields.options")}</label
            >
            <textarea
              id="edit-options"
              name="options"
              rows="3"
              class="{inputClass} font-mono text-xs">{optionsText(editDef)}</textarea
            >
            <p class="mt-1 text-xs text-text-muted">{t("settings.custom_fields.options_hint")}</p>
          </div>
        {/if}
        <div class="flex items-center gap-4">
          <label class="flex items-center gap-2 text-sm text-text">
            <FormCheckbox
              name="required"
              checked={editDef.required}
              class="h-4 w-4 rounded border-border"
            />
            {t("common.required")}
          </label>
          <div class="flex items-center gap-2">
            <label for="edit-position" class="text-sm text-text"
              >{t("settings.custom_fields.position")}</label
            >
            <input
              id="edit-position"
              name="position"
              type="number"
              value={editDef.position}
              class="w-20 rounded-lg border border-border px-2 py-1 text-sm"
            />
          </div>
        </div>
        {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
        <div class="flex justify-end gap-2 pt-1">
          <button
            type="button"
            class="rounded-lg border border-border px-4 py-2 text-sm"
            onclick={() => (showEdit = false)}
          >
            {t("common.cancel")}
          </button>
          <button
            class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            >{t("common.save")}</button
          >
        </div>
      </form>
    {/key}
  {/if}
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("settings.custom_fields.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
