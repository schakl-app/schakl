<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{t("settings.modules.title")}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-neutral-500 hover:text-neutral-900"
    >← {t("settings.title")}</a
  >
  <h1 class="mt-2 text-xl font-semibold text-neutral-900">{t("settings.modules.title")}</h1>
  <p class="mt-1 text-sm text-neutral-500">{t("settings.modules.subtitle")}</p>
</div>

<form
  method="POST"
  action="?/update"
  use:enhance
  class="max-w-lg rounded-xl border border-neutral-200 bg-white p-5"
>
  <ul class="space-y-2">
    {#each data.available as moduleName (moduleName)}
      {@const isHub = moduleName === "companies"}
      <li>
        <label
          class="flex items-center gap-3 rounded-lg border border-neutral-200 px-3 py-2.5 {isHub
            ? 'opacity-70'
            : 'hover:border-brand/50'}"
        >
          <input
            type="checkbox"
            name="modules"
            value={moduleName}
            checked={data.enabled.includes(moduleName)}
            disabled={isHub}
            class="h-4 w-4 rounded border-neutral-300 text-brand focus:ring-brand"
          />
          {#if isHub}<input type="hidden" name="modules" value="companies" />{/if}
          <span class="flex-1 text-sm font-medium text-neutral-800">{t(`nav.${moduleName}`)}</span>
          {#if isHub}
            <span class="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-500">
              {t("settings.modules.always_on")}
            </span>
          {/if}
        </label>
      </li>
    {/each}
  </ul>
  <p class="mt-3 text-xs text-neutral-400">{t("settings.modules.hint")}</p>
  {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
  {#if form?.updated}<p class="mt-2 text-sm text-green-600">{t("settings.account.saved")}</p>{/if}
  <button
    class="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
  >
    {t("common.save")}
  </button>
</form>
