<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import TypesTemplatesSections from "$lib/modules/subscriptions/TypesTemplatesSections.svelte";

  let { data, form } = $props();
</script>

<svelte:head>
  <title>{pageTitle(t("settings.subscriptions.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.subscriptions.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.subscriptions.subtitle")}</p>
</div>

<TypesTemplatesSections
  types={data.types}
  templates={data.templates}
  taskTemplates={data.taskTemplates}
  locale={data.locale}
  canManageTypes={data.canManageTypes}
  canManageTemplates={data.canManageTemplates}
  error={form?.error ?? null}
/>
