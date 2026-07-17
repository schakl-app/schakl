<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import { LABEL_COLORS, labelChipClass, labelDotClass } from "$lib/modules/tasks/labels";

  let { data, form } = $props();

  let newColor = $state("blue");
  let editingId = $state<string | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);
</script>

<svelte:head>
  <title>{pageTitle(t("settings.task_labels.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.task_labels.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.task_labels.subtitle")}</p>
</div>

<div class="grid gap-4 lg:grid-cols-[1fr_320px]">
  <section class="rounded-xl border border-border bg-surface-raised">
    {#if data.labels.length === 0}
      <p class="p-6 text-sm text-text-muted">{t("tasks.labels.empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each data.labels as label (label.id)}
          <li class="flex items-center gap-3 px-4 py-3">
            {#if editingId === label.id}
              <form
                method="POST"
                action="?/update"
                use:enhance={() =>
                  ({ update }) => {
                    editingId = null;
                    void update();
                  }}
                class="flex flex-1 items-center gap-2"
              >
                <input type="hidden" name="id" value={label.id} />
                <input
                  name="name"
                  value={label.name}
                  required
                  class="w-48 rounded-lg border border-border px-2 py-1 text-sm"
                />
                <select name="color" class="rounded-lg border border-border px-2 py-1 text-sm">
                  {#each LABEL_COLORS as color (color)}
                    <option value={color} selected={label.color === color}>{color}</option>
                  {/each}
                </select>
                <button class="rounded-lg bg-brand px-3 py-1 text-xs font-medium text-white"
                  >{t("common.save")}</button
                >
                <button
                  type="button"
                  class="text-xs text-text-muted"
                  onclick={() => (editingId = null)}>{t("common.cancel")}</button
                >
              </form>
            {:else}
              <span class="h-3 w-3 rounded-full {labelDotClass(label.color)}"></span>
              <span class="flex-1 text-sm font-medium text-text">{label.name}</span>
              <span
                class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(
                  label.color,
                )}">{label.name}</span
              >
              <ActionsMenu
                items={[
                  { label: t("common.edit"), icon: Pencil, onclick: () => (editingId = label.id) },
                  {
                    label: t("common.delete"),
                    icon: Trash2,
                    danger: true,
                    onclick: () => {
                      deleteId = label.id;
                      confirmDelete = true;
                    },
                  },
                ]}
              />
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <aside class="h-fit rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-3 text-sm font-semibold text-text">{t("tasks.labels.create")}</h2>
    <form
      method="POST"
      action="?/create"
      use:enhance={() =>
        ({ update }) =>
          void update({ reset: true })}
      class="space-y-3"
    >
      <input
        name="name"
        required
        placeholder={t("tasks.labels.new_placeholder")}
        class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      />
      <input type="hidden" name="color" value={newColor} />
      <div class="flex flex-wrap gap-1.5">
        {#each LABEL_COLORS as color (color)}
          <button
            type="button"
            aria-label={color}
            class="h-6 w-6 rounded-full {labelDotClass(color)} {newColor === color
              ? 'ring-2 ring-text ring-offset-1'
              : ''}"
            onclick={() => (newColor = color)}
          ></button>
        {/each}
      </div>
      {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
      <button
        class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("common.create")}
      </button>
    </form>
  </aside>
</div>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("settings.task_labels.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
