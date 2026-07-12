<script lang="ts">
  import { Download, Upload } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import ImportCsvModal from "$lib/core/impex/ImportCsvModal.svelte";
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();

  // One modal, armed for the entity whose Import was clicked; the action carries the slug.
  let importEntity = $state("");
  let importOpen = $state(false);

  function openImport(entity: string) {
    importEntity = entity;
    importOpen = true;
  }
</script>

<svelte:head>
  <title>{pageTitle(t("impex.settings.title"))}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
  <h1 class="mt-2 text-xl font-semibold text-text">{t("impex.settings.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("impex.settings.subtitle")}</p>
</div>

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised">
  <ul class="divide-y divide-border">
    {#each data.entities as entity (entity.entity_type)}
      <li class="flex flex-wrap items-center gap-3 p-4">
        <div class="min-w-0 flex-1">
          <p class="text-sm font-medium text-text">
            {t(`impex.entity.${entity.entity_type}`)}
          </p>
          <p class="text-xs text-text-muted">
            {t(`impex.entity.${entity.entity_type}_help`)}
          </p>
        </div>
        <!-- data-sveltekit-reload: a download endpoint, never a client-side route. -->
        <a
          href={`/settings/impex/${entity.entity_type}/export`}
          data-sveltekit-reload
          data-sveltekit-preload-data="off"
          class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
        >
          <Download class="h-4 w-4" />
          {t("impex.export")}
        </a>
        {#if entity.importable}
          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
            onclick={() => openImport(entity.entity_type)}
          >
            <Upload class="h-4 w-4" />
            {t("impex.import")}
          </button>
        {/if}
      </li>
    {/each}
  </ul>
</section>
<p class="mt-3 max-w-2xl text-xs text-text-muted">{t("impex.settings.hint")}</p>

<ImportCsvModal
  bind:open={importOpen}
  action={`?/importCsv&entity=${importEntity}`}
  report={form?.impex ?? null}
  error={form?.impexError ?? null}
/>
