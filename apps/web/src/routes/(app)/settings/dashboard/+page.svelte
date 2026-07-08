<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";

  let { data, form } = $props();

  let draft = $state<string[]>(data.defaultWidgets ?? [...data.availableWidgetKeys]);
  function toggle(key: string) {
    draft = draft.includes(key) ? draft.filter((k) => k !== key) : [...draft, key];
  }
  function move(key: string, delta: number) {
    const index = draft.indexOf(key);
    const next = index + delta;
    if (index === -1 || next < 0 || next >= draft.length) return;
    const copy = [...draft];
    [copy[index], copy[next]] = [copy[next], copy[index]];
    draft = copy;
  }
  const offKeys = $derived(data.availableWidgetKeys.filter((k) => !draft.includes(k)));
</script>

<svelte:head>
  <title>{t("settings.dashboard.title")}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-neutral-500 hover:text-neutral-900">← {t("settings.title")}</a>
  <h1 class="mt-2 text-xl font-semibold text-neutral-900">{t("settings.dashboard.title")}</h1>
  <p class="mt-1 text-sm text-neutral-500">{t("settings.dashboard.subtitle")}</p>
</div>

<form method="POST" action="?/saveDefault" use:enhance
  class="max-w-lg rounded-xl border border-neutral-200 bg-white p-5">
  <input type="hidden" name="widgets" value={draft.join(",")} />
  <ul class="space-y-1">
    {#each draft as key (key)}
      <li class="flex items-center gap-2 rounded-lg border border-neutral-200 px-3 py-2">
        <input type="checkbox" checked onchange={() => toggle(key)}
          class="h-4 w-4 rounded border-neutral-300 text-brand focus:ring-brand" />
        <span class="flex-1 text-sm text-neutral-800">{t(`dashboard.widget.${key}`)}</span>
        <button type="button" class="rounded border border-neutral-200 px-1.5 py-0.5 text-xs text-neutral-500 hover:border-brand" onclick={() => move(key, -1)} aria-label="↑">↑</button>
        <button type="button" class="rounded border border-neutral-200 px-1.5 py-0.5 text-xs text-neutral-500 hover:border-brand" onclick={() => move(key, 1)} aria-label="↓">↓</button>
      </li>
    {/each}
    {#each offKeys as key (key)}
      <li class="flex items-center gap-2 rounded-lg border border-dashed border-neutral-200 px-3 py-2 opacity-60">
        <input type="checkbox" onchange={() => toggle(key)}
          class="h-4 w-4 rounded border-neutral-300 text-brand focus:ring-brand" />
        <span class="flex-1 text-sm text-neutral-600">{t(`dashboard.widget.${key}`)}</span>
      </li>
    {/each}
  </ul>
  <p class="mt-3 text-xs text-neutral-400">{t("settings.dashboard.hint")}</p>
  {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
  {#if form?.saved}<p class="mt-2 text-sm text-green-600">{t("settings.account.saved")}</p>{/if}
  <button class="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
    {t("common.save")}
  </button>
</form>
