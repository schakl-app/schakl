<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { moduleLabel } from "$lib/core/registry";

  let { data, form } = $props();

  // Component state via bind:group, never one-way checked={…} (docs/UX.md): a checkbox
  // rendered one-way loses its mark on hydration, and the next save then silently strips
  // every module the user never touched — only the freshly ticked ones survived.
  let selected = $state<string[]>([...data.enabled]);
</script>

<svelte:head>
  <title>{pageTitle(t("settings.modules.title"))}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.modules.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.modules.subtitle")}</p>
</div>

<form
  method="POST"
  action="?/update"
  use:enhance={() =>
    async ({ update }) => {
      // Keep the ticked state after save (docs/UX.md): the default reset would wipe the
      // checkboxes back to their SSR attributes.
      await update({ reset: false });
    }}
  class="max-w-lg rounded-xl border border-border bg-surface-raised p-5"
>
  <ul class="space-y-2">
    {#each data.available as moduleName (moduleName)}
      {@const isHub = moduleName === "companies"}
      <!-- Locked (issue #137): needs a license, isn't covered, and isn't already enabled —
           an enabled-but-uncovered module stays toggleable so it can at least be dropped. -->
      {@const locked =
        data.licensed.includes(moduleName) &&
        !data.entitled.includes(moduleName) &&
        !data.enabled.includes(moduleName)}
      <li>
        <label
          class="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5 {isHub ||
          locked
            ? 'opacity-70'
            : 'hover:border-brand/50'}"
        >
          <input
            type="checkbox"
            name="modules"
            value={moduleName}
            bind:group={selected}
            disabled={isHub || locked}
            class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
          />
          {#if isHub}<input type="hidden" name="modules" value="companies" />{/if}
          <span class="flex-1 text-sm font-medium text-text">{moduleLabel(moduleName)}</span>
          {#if isHub}
            <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
              {t("settings.modules.always_on")}
            </span>
          {:else if locked}
            <span
              class="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-400"
              title={t("settings.modules.locked_hint")}
            >
              {t("settings.modules.locked")}
            </span>
          {/if}
        </label>
      </li>
    {/each}
  </ul>
  <p class="mt-3 text-xs text-text-muted">{t("settings.modules.hint")}</p>
  {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
  {#if form?.updated}<p class="mt-2 text-sm text-green-600">{t("settings.account.saved")}</p>{/if}
  <button
    class="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
  >
    {t("common.save")}
  </button>
</form>
