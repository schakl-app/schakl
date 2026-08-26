<script lang="ts">
  import { Check, Pencil, X } from "@lucide/svelte";
  import { untrack } from "svelte";
  import { dndzone } from "svelte-dnd-action";
  import type { SubmitFunction } from "@sveltejs/kit";

  import { applyAction, enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { dashboardWidgetsFor } from "$lib/core/registry";
  import Card from "$lib/core/ui/Card.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import { PANEL_HEADING } from "$lib/core/ui/headings";
  import WidgetGallery from "$lib/core/ui/WidgetGallery.svelte";

  let { data, form } = $props();

  const user = $derived(page.data.user);
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  // Audience-aware (#254): a portal login resolves the portal gallery, staff the staff one.
  const allWidgets = $derived(dashboardWidgetsFor(enabled, page.data.user));

  // The portal homepage (#193, #254) is the same board with portal chrome: a company switcher
  // above it, and the curated-marketing widget's data riding the URL it controls.
  const portalCompanies = $derived(data.portal?.companies ?? []);

  // Two independent columns instead of a grid: grid rows are as tall as their tallest tile,
  // so a short tile next to a tall one left a hole and the vertical rhythm drifted. Each
  // column is a flex stack with a constant gap. The flat order reads down the left column
  // first, then the right — which is also exactly what a phone shows, and what the API stores
  // beside the columns.
  //
  // The columns themselves are **stored** (#325). They used to be cut out of the flat list at
  // ceil(n/2) on every render, which made a tile's column a function of its index and nothing
  // else: a drag across only took if it also crossed that index, and crossing it shoved
  // whatever sat on the boundary the other way. The load resolves the fallback split for a
  // layout saved before columns existed; the drag handlers overwrite this until `data` changes.
  interface Tile {
    id: string;
  }
  let columns = $derived<Tile[][]>(
    data.columns.map((column: string[]) => column.map((key) => ({ id: key }))),
  );
  const widgetFor = (key: string) => allWidgets.find((w) => w.key === key);
  const activeKeys = $derived(columns.flat().map((tile) => tile.id));

  // Streamed tile data. A reload hands the board a *new* promise for data it already drew, and
  // a new promise identity sends every {#await} back to its skeleton — which is the flash in
  // #325. So a tile keeps the promise it has; only a key we hold none for (a widget just added
  // from the gallery) adopts the incoming one.
  let tileData = $state<Record<string, Promise<unknown>>>({ ...data.widgetData });
  $effect(() => {
    const incoming = data.widgetData;
    untrack(() => {
      for (const [key, promise] of Object.entries(incoming)) tileData[key] ??= promise;
    });
  });

  function considerColumn(index: number) {
    return (e: CustomEvent<{ items: Tile[] }>) => {
      columns = columns.map((column, i) => (i === index ? e.detail.items : column));
    };
  }
  function finalizeColumn(index: number) {
    return (e: CustomEvent<{ items: Tile[] }>) => {
      columns = columns.map((column, i) => (i === index ? e.detail.items : column));
      persist();
    };
  }

  // Use mode vs edit mode (UX §3): the board is static until an explicit edit affordance turns on
  // dragging, the gallery and the per-tile remove; "Klaar" turns it back off.
  let editMode = $state(false);

  let layoutForm: HTMLFormElement | undefined = $state();
  let layoutValue = $state("");
  let saveQueued = false;
  let reloadAfterSave = false;

  function persist() {
    layoutValue = columns.map((column) => column.map((tile) => tile.id).join(",")).join("|");
    // svelte-dnd-action dispatches `finalize` on **both** the origin and the target zone (its
    // README, v0.9.70), so one cross-column drop calls this twice in the same tick. One drop is
    // one save: coalescing into a single submit also lets the second call's columns be the ones
    // that travel, because the input's value is flushed before this timeout runs.
    if (saveQueued) return;
    saveQueued = true;
    setTimeout(() => {
      saveQueued = false;
      layoutForm?.requestSubmit();
    }, 0);
  }

  // The board is already showing what it just saved, so the default success path — `update()`,
  // which is `invalidateAll` — re-ran the whole page load, refetched every widget's API calls
  // and blinked every tile back to its skeleton to persist an order the browser had on screen
  // (#325). The one thing the browser genuinely lacks is data for a widget it has never drawn.
  const saveLayout: SubmitFunction =
    () =>
    async ({ result }) => {
      if (result.type === "failure") {
        await applyAction(result);
        return;
      }
      if (reloadAfterSave) {
        reloadAfterSave = false;
        await invalidateAll();
      }
    };

  function addWidget(key: string) {
    if (activeKeys.includes(key)) return;
    // Under the shorter column — where a new tile visually belongs, and the only placement that
    // doesn't disturb what is already arranged.
    const target = columns[0].length <= columns[1].length ? 0 : 1;
    columns = columns.map((column, i) => (i === target ? [...column, { id: key }] : column));
    // Its data is the one thing the page does not have — unless this widget was on the board
    // earlier in the session and its promise is still here.
    reloadAfterSave = !(key in tileData);
    persist();
  }
  function removeWidget(key: string) {
    columns = columns.map((column) => column.filter((tile) => tile.id !== key));
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

{#snippet tileBody(key: string)}
  {@const widget = widgetFor(key)}
  {#if widget}
    {@const WidgetComponent = widget.component}
    {#if tileData[key] === undefined}
      <!-- Just added from the gallery: nothing has ever loaded this one, so the skeleton is the
           honest answer until the reload brings it back. -->
      <div
        class="h-32 animate-pulse rounded-xl border border-border bg-surface-raised"
        aria-busy="true"
      ></div>
    {:else}
      {#await tileData[key]}
        <div
          class="h-32 animate-pulse rounded-xl border border-border bg-surface-raised"
          aria-busy="true"
        ></div>
      {:then widgetData}
        <WidgetComponent data={widgetData} />
      {/await}
    {/if}
  {/if}
{/snippet}

<!-- The title band (#404). The dashboard used to open with a bespoke flex row and no shared
     shape at all, which is what let three screens the team lives in disagree about where a
     page's own name sits. `PageHeader` is that shape; what it contains is still this page's. -->
<PageHeader title={t(data.portal ? "portal.home.title" : "dashboard.my_day.title")}>
  {#snippet subtitle()}
    {t("dashboard.welcome", { name: user?.full_name || user?.email || "" })}
  {/snippet}
  {#snippet actions()}
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
  {/snippet}
</PageHeader>

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
  <Card kind="strip" class="p-8 text-center">
    <p class="text-sm text-text-muted">{t("portal.home.empty")}</p>
  </Card>
{:else}
  <form
    method="POST"
    action="?/saveLayout"
    use:enhance={saveLayout}
    bind:this={layoutForm}
    class="hidden"
  >
    <input type="hidden" name="columns" value={layoutValue} />
  </form>

  {#if form?.error}<p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}

  <!-- The board. In use mode it is a plain grid — tiles are not draggable and a stray drag can't
       disturb the layout (UX §3). Edit mode turns on the drag zone and the per-tile remove. -->
  {#if activeKeys.length === 0}
    <Card kind="strip" class="p-10 text-center">
      <p class="text-sm text-text-muted">{t("dashboard.my_day.empty")}</p>
    </Card>
  {:else if editMode}
    <!-- The same two stacks as use mode, each a drag zone. A drop writes that column and saves
         it; the columns are the layout, so a tile crossing does not move anything else. -->
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start">
      {#each columns as column, columnIndex (columnIndex)}
        <div
          class="flex min-h-24 w-full min-w-0 flex-col gap-4 sm:flex-1 {columnIndex > 0
            ? 'sm:border-l sm:border-border sm:pl-4'
            : ''}"
          data-dashboard-column={columnIndex}
          use:dndzone={{
            items: column,
            flipDurationMs: 150,
            dropTargetStyle: {},
            type: "dashboard",
            // Hit-test on the cursor, not on the centre of the tile being dragged. The default
            // is the tile's centre, which only agrees with the pointer when the tile is small
            // relative to its target — and a widget is 130 px tall against an empty column's
            // 96 px, so emptying a column left a board you could not drag anything back into:
            // the cursor was over the empty stack and the tile's centre was below it. Nothing
            // moves differently, only what counts as "over".
            useCursorForDetection: true,
          }}
          onconsider={considerColumn(columnIndex)}
          onfinalize={finalizeColumn(columnIndex)}
        >
          {#each column as tile (tile.id)}
            <div
              class="relative cursor-grab rounded-xl ring-1 ring-border active:cursor-grabbing"
              data-widget={tile.id}
            >
              <button
                type="button"
                onclick={() => removeWidget(tile.id)}
                data-remove-widget={tile.id}
                class="absolute -right-2 -top-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface-raised text-text-muted shadow hover:border-red-400 hover:text-red-500"
                aria-label={t("dashboard.remove_widget")}
              >
                <X size={13} />
              </button>
              {@render tileBody(tile.id)}
            </div>
          {/each}
        </div>
      {/each}
    </div>
  {:else}
    <!-- Two independent flex stacks: every tile sits gap-4 under its neighbour whatever the
         heights, instead of grid rows stretching to the tallest tile and leaving holes. -->
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start">
      {#each columns as column, columnIndex (columnIndex)}
        <!-- A visible column rule (#438): the board is two stacks of same-shaped cards, and a
             hairline between the stacks is what lets the card edges read as a grid at a glance
             — structure as shape, not something recovered by parsing each card. -->
        <div
          class="flex w-full min-w-0 flex-col gap-4 sm:flex-1 {columnIndex > 0
            ? 'sm:border-l sm:border-border sm:pl-4'
            : ''}"
          data-dashboard-column={columnIndex}
        >
          {#each column as tile (tile.id)}
            {#if widgetFor(tile.id)}
              <div data-widget={tile.id}>
                {@render tileBody(tile.id)}
              </div>
            {/if}
          {/each}
        </div>
      {/each}
    </div>
  {/if}

  {#if editMode}
    <Card class="mt-6">
      {#snippet header()}
        <div class="mb-3 flex items-center justify-between">
          <div>
            <h2 class={PANEL_HEADING}>{t("dashboard.gallery.title")}</h2>
            <p class="mt-0.5 text-xs text-text-muted">{t("dashboard.gallery.hint")}</p>
          </div>
          {#if data.prefsSource === "user"}
            <form
              method="POST"
              action="?/resetLayout"
              use:enhance={() =>
                ({ update }) =>
                  void update()}
            >
              <button class="text-xs text-text-muted hover:text-text">
                {t("dashboard.customize.reset")}
              </button>
            </form>
          {/if}
        </div>
      {/snippet}
      <WidgetGallery widgets={allWidgets} {activeKeys} onadd={addWidget} />
    </Card>
  {/if}
{/if}
