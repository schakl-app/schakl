<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { LABEL_COLORS, labelChipClass, labelDotClass } from "$lib/modules/tasks/labels";

  let { data, form } = $props();

  let newColor = $state("blue");
  let editingId = $state<string | null>(null);
</script>

<svelte:head>
  <title>{t("settings.task_labels.title")}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-neutral-500 hover:text-neutral-900">← {t("settings.title")}</a>
  <h1 class="mt-2 text-xl font-semibold text-neutral-900">{t("settings.task_labels.title")}</h1>
  <p class="mt-1 text-sm text-neutral-500">{t("settings.task_labels.subtitle")}</p>
</div>

<div class="grid gap-4 lg:grid-cols-[1fr_320px]">
  <section class="rounded-xl border border-neutral-200 bg-white">
    {#if data.labels.length === 0}
      <p class="p-6 text-sm text-neutral-500">{t("tasks.labels.empty")}</p>
    {:else}
      <ul class="divide-y divide-neutral-100">
        {#each data.labels as label (label.id)}
          <li class="flex items-center gap-3 px-4 py-3">
            {#if editingId === label.id}
              <form method="POST" action="?/update"
                use:enhance={() => ({ update }) => { editingId = null; void update(); }}
                class="flex flex-1 items-center gap-2">
                <input type="hidden" name="id" value={label.id} />
                <input name="name" value={label.name} required
                  class="w-48 rounded-lg border border-neutral-300 px-2 py-1 text-sm" />
                <select name="color" class="rounded-lg border border-neutral-300 px-2 py-1 text-sm">
                  {#each LABEL_COLORS as color (color)}
                    <option value={color} selected={label.color === color}>{color}</option>
                  {/each}
                </select>
                <button class="rounded-lg bg-brand px-3 py-1 text-xs font-medium text-white">{t("common.save")}</button>
                <button type="button" class="text-xs text-neutral-500" onclick={() => (editingId = null)}>{t("common.cancel")}</button>
              </form>
            {:else}
              <span class="h-3 w-3 rounded-full {labelDotClass(label.color)}"></span>
              <span class="flex-1 text-sm font-medium text-neutral-900">{label.name}</span>
              <span class="rounded-full px-2 py-0.5 text-[11px] font-medium {labelChipClass(label.color)}">{label.name}</span>
              <button type="button" class="text-xs text-neutral-500 hover:text-brand" onclick={() => (editingId = label.id)}>
                {t("common.edit")}
              </button>
              <form method="POST" action="?/delete" use:enhance>
                <input type="hidden" name="id" value={label.id} />
                <button class="text-xs text-neutral-400 hover:text-red-600">{t("common.delete")}</button>
              </form>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <aside class="h-fit rounded-xl border border-neutral-200 bg-white p-5">
    <h2 class="mb-3 text-sm font-semibold text-neutral-900">{t("tasks.labels.create")}</h2>
    <form method="POST" action="?/create" use:enhance={() => ({ update }) => void update({ reset: true })} class="space-y-3">
      <input name="name" required placeholder={t("tasks.labels.new_placeholder")}
        class="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand" />
      <input type="hidden" name="color" value={newColor} />
      <div class="flex flex-wrap gap-1.5">
        {#each LABEL_COLORS as color (color)}
          <button type="button" aria-label={color}
            class="h-6 w-6 rounded-full {labelDotClass(color)} {newColor === color ? 'ring-2 ring-neutral-800 ring-offset-1' : ''}"
            onclick={() => (newColor = color)}></button>
        {/each}
      </div>
      {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
      <button class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.create")}
      </button>
    </form>
  </aside>
</div>
