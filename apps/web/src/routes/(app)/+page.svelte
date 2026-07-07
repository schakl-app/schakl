<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { dashboardWidgetsFor } from "$lib/core/registry";

  let { data } = $props();

  const user = $derived(page.data.user);
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  // Resolve the widget components for the keys the server loaded data for.
  const widgets = $derived(
    dashboardWidgetsFor(enabled).filter((w) => data.widgetKeys.includes(w.key)),
  );
</script>

<svelte:head>
  <title>{t("dashboard.my_day.title")}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="text-xl font-semibold text-neutral-900">{t("dashboard.my_day.title")}</h1>
  <p class="mt-1 text-sm text-neutral-500">
    {t("dashboard.welcome", { name: user?.full_name || user?.email || "" })}
  </p>
</div>

{#if widgets.length === 0}
  <div class="rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center">
    <p class="text-sm text-neutral-500">{t("dashboard.my_day.empty")}</p>
  </div>
{:else}
  <div class="grid gap-4 sm:grid-cols-2">
    {#each widgets as widget (widget.key)}
      {@const WidgetComponent = widget.component}
      <WidgetComponent data={data.widgetData[widget.key]} />
    {/each}
  </div>
{/if}
