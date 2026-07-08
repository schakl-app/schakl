<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { fieldLabel } from "$lib/core/customfields/types";

  let { data, form } = $props();

  const TYPES = [
    "text", "long_text", "number", "boolean", "date", "datetime",
    "select", "multi_select", "email", "url", "phone",
  ] as const;

  let selectedType = $state("text");
  const showOptions = $derived(selectedType === "select" || selectedType === "multi_select");
</script>

<svelte:head>
  <title>{t("settings.custom_fields.title")}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-neutral-500 hover:text-neutral-900">← {t("settings.title")}</a>
  <h1 class="mt-2 text-xl font-semibold text-neutral-900">{t("settings.custom_fields.title")}</h1>
  <p class="mt-1 text-sm text-neutral-500">{t("settings.custom_fields.subtitle")}</p>
</div>

<!-- Entity type switcher -->
<div class="mb-4 flex flex-wrap gap-2">
  {#each data.entityTypes as et (et)}
    <a href={`?entity_type=${et}`}
      class="rounded-lg border px-3 py-1.5 text-sm"
      class:border-brand={et === data.entityType}
      class:text-brand={et === data.entityType}
      class:border-neutral-300={et !== data.entityType}>
      {t(`customfields.entity.${et}`)}
    </a>
  {/each}
</div>

<!-- Existing definitions -->
<div class="mb-6 overflow-hidden rounded-xl border border-neutral-200 bg-white">
  {#if data.definitions.length === 0}
    <p class="p-6 text-center text-sm text-neutral-500">{t("settings.custom_fields.empty")}</p>
  {:else}
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
          <th class="px-4 py-2 font-medium">{t("settings.custom_fields.label")}</th>
          <th class="px-4 py-2 font-medium">{t("settings.custom_fields.key")}</th>
          <th class="px-4 py-2 font-medium">{t("settings.custom_fields.type")}</th>
          <th class="px-4 py-2 font-medium">{t("common.required")}</th>
          <th class="px-4 py-2 text-right font-medium">{t("common.actions")}</th>
        </tr>
      </thead>
      <tbody>
        {#each data.definitions as def (def.id)}
          <tr class="border-b border-neutral-100" class:opacity-50={!def.active}>
            <td class="px-4 py-2 text-neutral-900">{fieldLabel(def, data.locale)}</td>
            <td class="px-4 py-2 font-mono text-xs text-neutral-500">{def.key}</td>
            <td class="px-4 py-2 text-neutral-600">{t(`customfields.type.${def.data_type}`)}</td>
            <td class="px-4 py-2 text-neutral-600">{def.required ? t("common.required") : "—"}</td>
            <td class="px-4 py-2">
              <div class="flex items-center justify-end gap-3">
                <form method="POST" action="?/toggleActive" use:enhance>
                  <input type="hidden" name="id" value={def.id} />
                  <input type="hidden" name="active" value={String(def.active)} />
                  <button class="text-xs text-neutral-500 hover:text-neutral-900">
                    {def.active ? t("settings.custom_fields.deactivate") : t("settings.custom_fields.activate")}
                  </button>
                </form>
                <form method="POST" action="?/delete" use:enhance>
                  <input type="hidden" name="id" value={def.id} />
                  <button class="text-xs text-neutral-400 hover:text-red-600">{t("common.delete")}</button>
                </form>
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<!-- Create definition -->
<form method="POST" action="?/create" use:enhance class="rounded-xl border border-neutral-200 bg-white p-5">
  <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t("settings.custom_fields.new")}</h2>
  <input type="hidden" name="entity_type" value={data.entityType} />
  <div class="grid gap-3 sm:grid-cols-2">
    <div>
      <label for="key" class="mb-1 block text-sm font-medium text-neutral-700">{t("settings.custom_fields.key")}</label>
      <input id="key" name="key" required pattern="[a-z][a-z0-9_]*" placeholder="vat_number"
        class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
      <p class="mt-1 text-xs text-neutral-400">{t("settings.custom_fields.key_hint")}</p>
    </div>
    <div>
      <label for="data_type" class="mb-1 block text-sm font-medium text-neutral-700">{t("settings.custom_fields.type")}</label>
      <select id="data_type" name="data_type" bind:value={selectedType}
        class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm">
        {#each TYPES as ty (ty)}<option value={ty}>{t(`customfields.type.${ty}`)}</option>{/each}
      </select>
    </div>
    <div>
      <label for="label_nl" class="mb-1 block text-sm font-medium text-neutral-700">{t("settings.custom_fields.label_nl")}</label>
      <input id="label_nl" name="label_nl" class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
    </div>
    <div>
      <label for="label_en" class="mb-1 block text-sm font-medium text-neutral-700">{t("settings.custom_fields.label_en")}</label>
      <input id="label_en" name="label_en" class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm" />
    </div>
    {#if showOptions}
      <div class="sm:col-span-2">
        <label for="options" class="mb-1 block text-sm font-medium text-neutral-700">{t("settings.custom_fields.options")}</label>
        <textarea id="options" name="options" rows="3" placeholder={"gold|Gold\nsilver|Silver"}
          class="w-full rounded-lg border border-neutral-300 px-3 py-2 font-mono text-xs"></textarea>
        <p class="mt-1 text-xs text-neutral-400">{t("settings.custom_fields.options_hint")}</p>
      </div>
    {/if}
    <div class="flex items-center gap-4">
      <label class="flex items-center gap-2 text-sm text-neutral-700">
        <input type="checkbox" name="required" class="h-4 w-4 rounded border-neutral-300" />
        {t("common.required")}
      </label>
      <div class="flex items-center gap-2">
        <label for="position" class="text-sm text-neutral-700">{t("settings.custom_fields.position")}</label>
        <input id="position" name="position" type="number" value="0" class="w-20 rounded-lg border border-neutral-300 px-2 py-1 text-sm" />
      </div>
    </div>
  </div>
  {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
  <div class="mt-4">
    <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">{t("common.create")}</button>
  </div>
</form>
