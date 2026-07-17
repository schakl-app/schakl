<script lang="ts">
  import { X } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { dashboardWidgetsFor, widgetTitleKey } from "$lib/core/registry";
  import { pageTitle } from "$lib/core/title";
  import WidgetGallery from "$lib/core/ui/WidgetGallery.svelte";

  let { data, form } = $props();

  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const allWidgets = $derived(dashboardWidgetsFor(enabled, page.data.user));
  const titleFor = (key: string) => {
    const spec = allWidgets.find((w) => w.key === key);
    return spec ? t(widgetTitleKey(spec)) : key;
  };

  // The org default template. A settings screen is already an editing surface (UX §6), so there is
  // no use/edit toggle — the same gallery, plus reorder/remove, then Save.
  let draft = $state<string[]>(data.defaultWidgets ?? [...data.availableWidgetKeys]);
  function addWidget(key: string) {
    if (!draft.includes(key)) draft = [...draft, key];
  }
  function removeWidget(key: string) {
    draft = draft.filter((k) => k !== key);
  }
  function move(key: string, delta: number) {
    const index = draft.indexOf(key);
    const next = index + delta;
    if (index === -1 || next < 0 || next >= draft.length) return;
    const copy = [...draft];
    [copy[index], copy[next]] = [copy[next], copy[index]];
    draft = copy;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.dashboard.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.dashboard.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.dashboard.subtitle")}</p>
</div>

<form
  method="POST"
  action="?/saveDefault"
  use:enhance
  class="max-w-lg rounded-xl border border-border bg-surface-raised p-5"
>
  <input type="hidden" name="widgets" value={draft.join(",")} />
  {#if draft.length === 0}
    <p class="text-sm text-text-muted">{t("dashboard.my_day.empty")}</p>
  {:else}
    <ul class="space-y-1">
      {#each draft as key (key)}
        <li class="flex items-center gap-2 rounded-lg border border-border px-3 py-2">
          <span class="flex-1 text-sm text-text">{titleFor(key)}</span>
          <button
            type="button"
            class="rounded border border-border px-1.5 py-0.5 text-xs text-text-muted hover:border-brand"
            onclick={() => move(key, -1)}
            aria-label={t("table.move_up")}>↑</button
          >
          <button
            type="button"
            class="rounded border border-border px-1.5 py-0.5 text-xs text-text-muted hover:border-brand"
            onclick={() => move(key, 1)}
            aria-label={t("table.move_down")}>↓</button
          >
          <button
            type="button"
            class="rounded p-1 text-text-muted hover:text-red-500"
            onclick={() => removeWidget(key)}
            aria-label={t("dashboard.remove_widget")}
          >
            <X size={14} />
          </button>
        </li>
      {/each}
    </ul>
  {/if}
  <p class="mt-3 text-xs text-text-muted">{t("settings.dashboard.hint")}</p>
  {#if form?.error}<p class="mt-2 text-sm text-red-600">{t(form.error)}</p>{/if}
  {#if form?.saved}<p class="mt-2 text-sm text-green-600">{t("settings.account.saved")}</p>{/if}
  <button
    class="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
  >
    {t("common.save")}
  </button>
</form>

<section class="mt-6 max-w-lg rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="mb-3 text-sm font-semibold text-text">{t("dashboard.gallery.title")}</h2>
  <WidgetGallery widgets={allWidgets} activeKeys={draft} onadd={addWidget} />
</section>
