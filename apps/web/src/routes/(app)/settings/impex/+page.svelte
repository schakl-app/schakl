<script lang="ts">
  import { Download, Upload } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import ImportWizard from "$lib/core/impex/ImportWizard.svelte";
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();

  // One modal, armed for the entity whose Import was clicked; the action carries the slug.
  let importEntity = $state("");
  let importOpen = $state(false);

  function openImport(entity: string) {
    importEntity = entity;
    importOpen = true;
  }

  /**
   * Entities grouped the way the app is: the records an agency works in, then the catalogs
   * that configure them. Twelve flat rows read as a list to scroll; the split is what makes
   * "where is the rate card" answerable at a glance.
   *
   * Membership is spelled out rather than inferred from the slug — `subscription` and
   * `subscription_type` share a prefix and belong on opposite sides of the line.
   */
  const CATALOGS = ["subscription_type", "subscription_template", "domain_tld_price"];
  const sections = $derived([
    {
      key: "records",
      entities: data.entities.filter((e) => !CATALOGS.includes(e.entity_type)),
    },
    {
      key: "catalogs",
      entities: data.entities.filter((e) => CATALOGS.includes(e.entity_type)),
    },
  ]);
</script>

<svelte:head>
  <title>{pageTitle(t("impex.settings.title"))}</title>
</svelte:head>

<div class="mb-6">
  <h1 class="mt-2 text-xl font-semibold text-text">{t("impex.settings.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("impex.settings.subtitle")}</p>
</div>

{#if data.entities.length === 0}
  <!-- The bulk permission alone is not enough: it says *may bulk*, each entity's own read says
       *what*. Someone holding only the former sees this rather than an empty card. -->
  <p
    class="max-w-2xl rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted"
  >
    {t("impex.settings.no_entities")}
  </p>
{/if}

{#each sections as section (section.key)}
  {#if section.entities.length > 0}
    <h2 class="mb-2 mt-6 text-sm font-medium text-text-muted first:mt-0">
      {t(`impex.section.${section.key}`)}
    </h2>
    <section class="max-w-2xl rounded-xl border border-border bg-surface-raised">
      <ul class="divide-y divide-border">
        {#each section.entities as entity (entity.entity_type)}
          <li class="flex flex-wrap items-center gap-3 p-4">
            <div class="min-w-0 flex-1">
              <p class="text-sm font-medium text-text">
                {t(`impex.entity.${entity.entity_type}`)}
              </p>
              <p class="text-xs text-text-muted">
                {t(`impex.entity.${entity.entity_type}_help`)}
              </p>
            </div>
            <!-- data-sveltekit-reload: a download endpoint, never a client-side route. The hub
                 exports the whole set on purpose — the filtered download is the list's own. -->
            <a
              href={`/impex/${entity.entity_type}/export`}
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
  {/if}
{/each}

<p class="mt-3 max-w-2xl text-xs text-text-muted">{t("impex.settings.hint")}</p>

<ImportWizard
  bind:open={importOpen}
  action={`?/impex&entity=${importEntity}`}
  locale={data.locale}
  report={form?.impex ?? null}
  inspect={form?.impexInspect ?? null}
  columns={form?.impexColumns ?? null}
  error={form?.impexError ?? null}
/>
