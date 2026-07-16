<script lang="ts">
  import { Check, Pencil, X } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { dashboardWidgetsFor } from "$lib/core/registry";
  import WidgetGallery from "$lib/core/ui/WidgetGallery.svelte";
  import MarketingSourceSection from "$lib/modules/marketing/MarketingSourceSection.svelte";
  import type { CompanyMarketing } from "$lib/modules/marketing/types";

  let { data, form } = $props();

  const user = $derived(page.data.user);
  // Generated OpenAPI types are looser than the module's own (#193) — narrow once here.
  const portalMetrics = $derived((data.portal?.metrics ?? null) as CompanyMarketing | null);
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const allWidgets = $derived(dashboardWidgetsFor(enabled, page.data.user));

  // Tiles are draggable in edit mode: mirror the loaded order locally; a change persists it.
  interface Tile {
    id: string;
  }
  let tiles = $state<Tile[]>([]);
  $effect(() => {
    tiles = data.widgetKeys.map((key: string) => ({ id: key }));
  });
  const widgetFor = (key: string) => allWidgets.find((w) => w.key === key);
  const activeKeys = $derived(tiles.map((tile) => tile.id));

  // Use mode vs edit mode (UX §3): the board is static until an explicit edit affordance turns on
  // dragging, the gallery and the per-tile remove; "Klaar" turns it back off.
  let editMode = $state(false);

  let layoutForm: HTMLFormElement | undefined = $state();
  let layoutValue = $state("");
  function persist() {
    layoutValue = tiles.map((tile) => tile.id).join(",");
    setTimeout(() => layoutForm?.requestSubmit(), 0);
  }
  function handleDndConsider(e: CustomEvent<{ items: Tile[] }>) {
    tiles = e.detail.items;
  }
  function handleDndFinalize(e: CustomEvent<{ items: Tile[] }>) {
    tiles = e.detail.items;
    persist();
  }
  function addWidget(key: string) {
    if (!activeKeys.includes(key)) {
      tiles = [...tiles, { id: key }];
      persist();
    }
  }
  function removeWidget(key: string) {
    tiles = tiles.filter((tile) => tile.id !== key);
    persist();
  }
</script>

<svelte:head>
  <title>{pageTitle(t("dashboard.my_day.title"))}</title>
</svelte:head>

{#if data.portal}
  <!-- The client portal homepage (#193): the marketing dashboard as the agency curated it
       (#192 layouts, enforced by the API) for each company in this contact's horizon. -->
  <div class="mb-6">
    <h1 class="text-xl font-semibold text-text">{t("portal.home.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {t("dashboard.welcome", { name: user?.full_name || user?.email || "" })}
    </p>
  </div>

  {#if data.portal.companies.length === 0}
    <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
      <p class="text-sm text-text-muted">{t("portal.home.empty")}</p>
    </div>
  {:else}
    {#if data.portal.companies.length > 1}
      <!-- Several companies: a switcher; one: straight to it. -->
      <div class="mb-5 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
        {#each data.portal.companies as company (company.id)}
          <a
            href={`?company=${company.id}`}
            data-sveltekit-noscroll
            class="rounded-lg px-3 py-1.5 text-sm font-medium {company.id === data.portal.selected
              ? 'bg-brand text-white'
              : 'text-text-muted hover:bg-surface'}"
          >
            {company.name}
          </a>
        {/each}
      </div>
    {:else}
      <h2 class="mb-4 text-base font-semibold text-text">{data.portal.companies[0].name}</h2>
    {/if}

    {#if portalMetrics && portalMetrics.sources.length > 0}
      <div class="space-y-5">
        {#each portalMetrics.sources as src (src.link_id)}
          <MarketingSourceSection
            companyId={data.portal.selected ?? ""}
            {src}
            rangeDays={30}
          />
        {/each}
      </div>
    {:else}
      <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
        <p class="text-sm text-text-muted">{t("portal.home.no_data")}</p>
      </div>
    {/if}
  {/if}
{:else}
<div class="mb-6 flex items-start justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("dashboard.my_day.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {t("dashboard.welcome", { name: user?.full_name || user?.email || "" })}
    </p>
  </div>
  <button
    class="flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm {editMode
      ? 'border-brand text-brand'
      : 'text-text-muted hover:border-brand hover:text-brand'}"
    onclick={() => (editMode = !editMode)}
  >
    {#if editMode}<Check size={15} /> {t("dashboard.done")}{:else}<Pencil size={15} />
      {t("dashboard.edit")}{/if}
  </button>
</div>

<form method="POST" action="?/saveLayout" use:enhance bind:this={layoutForm} class="hidden">
  <input type="hidden" name="widgets" value={layoutValue} />
</form>

{#if form?.error}<p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}

<!-- The board. In use mode it is a plain grid — tiles are not draggable and a stray drag can't
     disturb the layout (UX §3). Edit mode turns on the drag zone and the per-tile remove. -->
{#if tiles.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="text-sm text-text-muted">{t("dashboard.my_day.empty")}</p>
  </div>
{:else if editMode}
  <div
    class="grid gap-4 sm:grid-cols-2"
    use:dndzone={{ items: tiles, flipDurationMs: 150, dropTargetStyle: {} }}
    onconsider={handleDndConsider}
    onfinalize={handleDndFinalize}
  >
    {#each tiles as tile (tile.id)}
      {@const widget = widgetFor(tile.id)}
      <div class="relative cursor-grab rounded-xl ring-1 ring-border active:cursor-grabbing">
        <button
          type="button"
          onclick={() => removeWidget(tile.id)}
          class="absolute -right-2 -top-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface-raised text-text-muted shadow hover:border-red-400 hover:text-red-500"
          aria-label={t("dashboard.remove_widget")}
        >
          <X size={13} />
        </button>
        {#if widget}
          {@const WidgetComponent = widget.component}
          <WidgetComponent data={data.widgetData[tile.id]} />
        {/if}
      </div>
    {/each}
  </div>
{:else}
  <div class="grid gap-4 sm:grid-cols-2">
    {#each tiles as tile (tile.id)}
      {@const widget = widgetFor(tile.id)}
      {#if widget}
        {@const WidgetComponent = widget.component}
        <div><WidgetComponent data={data.widgetData[tile.id]} /></div>
      {/if}
    {/each}
  </div>
{/if}

{#if editMode}
  <section class="mt-6 rounded-xl border border-border bg-surface-raised p-5">
    <div class="mb-3 flex items-center justify-between">
      <div>
        <h2 class="text-sm font-semibold text-text">{t("dashboard.gallery.title")}</h2>
        <p class="mt-0.5 text-xs text-text-muted">{t("dashboard.gallery.hint")}</p>
      </div>
      {#if data.prefsSource === "user"}
        <form
          method="POST"
          action="?/resetLayout"
          use:enhance={() =>
            ({ update }) => void update()}
        >
          <button class="text-xs text-text-muted hover:text-text">
            {t("dashboard.customize.reset")}
          </button>
        </form>
      {/if}
    </div>
    <WidgetGallery widgets={allWidgets} {activeKeys} onadd={addWidget} />
  </section>
{/if}
{/if}
