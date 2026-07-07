<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { companyPanelComponent } from "$lib/core/registry";

  let { data } = $props();

  // Panels are contributed by enabled modules and composed here — the "attach to company" hub.
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
</script>

<svelte:head>
  <title>{data.company.name}</title>
</svelte:head>

<div class="mb-6">
  <a href="/companies" class="text-sm text-neutral-500 hover:text-neutral-900">
    ← {t("companies.title")}
  </a>
  <h1 class="mt-2 text-xl font-semibold text-neutral-900">{data.company.name}</h1>
</div>

<div class="grid gap-4">
  {#each data.panels as panel (panel.key)}
    {@const spec = companyPanelComponent(enabled, panel.key)}
    <section class="rounded-xl border border-neutral-200 bg-white p-5">
      <h2 class="mb-4 text-sm font-semibold text-neutral-900">{t(panel.title_key)}</h2>
      {#if spec}
        {@const PanelComponent = spec.component}
        <PanelComponent companyId={data.company.id} data={panel.data} />
      {:else}
        <pre class="overflow-x-auto text-xs text-neutral-500">{JSON.stringify(
            panel.data,
            null,
            2,
          )}</pre>
      {/if}
    </section>
  {/each}
</div>
