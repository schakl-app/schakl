<script lang="ts">
  import { Check, Pencil, X } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { dashboardWidgetsFor } from "$lib/core/registry";
  import WidgetGallery from "$lib/core/ui/WidgetGallery.svelte";

  let { data, form } = $props();

  const user = $derived(page.data.user);
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  // Audience-aware (#254): a portal login resolves the portal gallery, staff the staff one.
  const allWidgets = $derived(dashboardWidgetsFor(enabled, page.data.user));

  // The portal homepage (#193, #254) is the same board with portal chrome: a company switcher
  // above it, and the curated-marketing widget's data riding the URL it controls.
  const portalCompanies = $derived(data.portal?.companies ?? []);

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

  // Two independent columns instead of a grid: grid rows are as tall as their tallest tile,
  // so a short tile next to a tall one left a hole and the vertical rhythm drifted. Each
  // column is a flex stack with a constant gap. The saved order reads down the left column
  // first, then the right — which is also exactly the flat order a phone shows.
  const splitAt = $derived(Math.ceil(tiles.length / 2));
  const useColumns = $derived([tiles.slice(0, splitAt), tiles.slice(splitAt)]);
  // Edit mode mirrors the same two stacks as drag zones; a drop rebuilds the flat order.
  let editColumns = $state<Tile[][]>([[], []]);
  $effect(() => {
    const half = Math.ceil(tiles.length / 2);
    editColumns = [tiles.slice(0, half), tiles.slice(half)];
  });
  function considerColumn(column: number) {
    return (e: CustomEvent<{ items: Tile[] }>) => {
      editColumns[column] = e.detail.items;
    };
  }
  function finalizeColumn(column: number) {
    return (e: CustomEvent<{ items: Tile[] }>) => {
      editColumns[column] = e.detail.items;
      tiles = [...editColumns[0], ...editColumns[1]];
      persist();
    };
  }

  // Use mode vs edit mode (UX §3): the board is static until an explicit edit affordance turns on
  // dragging, the gallery and the per-tile remove; "Klaar" turns it back off.
  let editMode = $state(false);

  let layoutForm: HTMLFormElement | undefined = $state();
  let layoutValue = $state("");
  function persist() {
    layoutValue = tiles.map((tile) => tile.id).join(",");
    setTimeout(() => layoutForm?.requestSubmit(), 0);
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
  <title>{pageTitle(t(data.portal ? "portal.home.title" : "dashboard.my_day.title"))}</title>
</svelte:head>

{#snippet companyLogo(company: { name: string; logoUrl: string | null })}
  <!-- The company's logo on their own dashboard (#196), initials when unset — the tenant's
       (agency) branding stays in the shell, untouched. -->
  {#if company.logoUrl}
    <img
      src={company.logoUrl}
      alt=""
      class="h-9 w-9 shrink-0 rounded-lg border border-border object-contain"
    />
  {:else}
    <span
      class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface text-sm font-semibold text-text-muted ring-1 ring-inset ring-border"
      aria-hidden="true"
    >
      {company.name.slice(0, 2).toUpperCase()}
    </span>
  {/if}
{/snippet}

<div class="mb-6 flex items-start justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">
      {t(data.portal ? "portal.home.title" : "dashboard.my_day.title")}
    </h1>
    <p class="mt-1 text-sm text-text-muted">
      {t("dashboard.welcome", { name: user?.full_name || user?.email || "" })}
    </p>
  </div>
  {#if !data.portal || portalCompanies.length > 0}
    <button
      class="flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm {editMode
        ? 'border-brand text-brand'
        : 'text-text-muted hover:border-brand hover:text-brand'}"
      onclick={() => (editMode = !editMode)}
    >
      {#if editMode}<Check size={15} /> {t("dashboard.done")}{:else}<Pencil size={15} />
        {t("dashboard.edit")}{/if}
    </button>
  {/if}
</div>

{#if data.portal}
  {#if portalCompanies.length > 1}
    <!-- Several companies: a switcher; one: straight to it. -->
    <div class="mb-5 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
      {#each portalCompanies as company (company.id)}
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
  {/if}
  {#if portalCompanies.length > 0}
    {@const current =
      portalCompanies.find((c) => c.id === data.portal?.selected) ?? portalCompanies[0]}
    <h2 class="mb-4 flex items-center gap-2.5 text-base font-semibold text-text">
      {@render companyLogo(current)}
      {current.name}
    </h2>
  {/if}
{/if}

{#if data.portal && portalCompanies.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
    <p class="text-sm text-text-muted">{t("portal.home.empty")}</p>
  </div>
{:else}
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
    <!-- The same two stacks as use mode, each a drag zone; dragging between them works and a
         drop rebuilds the flat saved order (left column first, then right). -->
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start">
      {#each editColumns as column, columnIndex (columnIndex)}
        <div
          class="flex min-h-24 w-full min-w-0 flex-col gap-4 sm:flex-1"
          use:dndzone={{
            items: column,
            flipDurationMs: 150,
            dropTargetStyle: {},
            type: "dashboard",
          }}
          onconsider={considerColumn(columnIndex)}
          onfinalize={finalizeColumn(columnIndex)}
        >
          {#each column as tile (tile.id)}
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
      {/each}
    </div>
  {:else}
    <!-- Two independent flex stacks: every tile sits gap-4 under its neighbour whatever the
         heights, instead of grid rows stretching to the tallest tile and leaving holes. -->
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start">
      {#each useColumns as column, columnIndex (columnIndex)}
        <div class="flex w-full min-w-0 flex-col gap-4 sm:flex-1">
          {#each column as tile (tile.id)}
            {@const widget = widgetFor(tile.id)}
            {#if widget}
              {@const WidgetComponent = widget.component}
              <div><WidgetComponent data={data.widgetData[tile.id]} /></div>
            {/if}
          {/each}
        </div>
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
