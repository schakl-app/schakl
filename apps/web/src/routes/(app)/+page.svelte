<script lang="ts">
  import { SlidersHorizontal } from "@lucide/svelte";
  import { dndzone } from "svelte-dnd-action";

  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { dashboardWidgetsFor } from "$lib/core/registry";

  let { data, form } = $props();

  const user = $derived(page.data.user);
  const enabled = $derived(page.data.theme?.enabledModules ?? []);

  // Tiles are draggable: mirror the loaded order locally; a drop persists the new order.
  interface Tile {
    id: string;
  }
  let tiles = $state<Tile[]>([]);
  $effect(() => {
    tiles = data.widgetKeys.map((key: string) => ({ id: key }));
  });
  const widgetFor = (key: string) =>
    dashboardWidgetsFor(enabled, page.data.user).find((w) => w.key === key);

  let layoutForm: HTMLFormElement | undefined = $state();
  let layoutValue = $state("");
  function handleDndConsider(e: CustomEvent<{ items: Tile[] }>) {
    tiles = e.detail.items;
  }
  function handleDndFinalize(e: CustomEvent<{ items: Tile[] }>) {
    tiles = e.detail.items;
    layoutValue = tiles.map((tile) => tile.id).join(",");
    setTimeout(() => layoutForm?.requestSubmit(), 0);
  }

  // --- customization panel (which widgets are on) --------------------------------
  let customizing = $state(false);
  let draft = $state<string[]>([]);
  function startCustomize() {
    draft = [...data.widgetKeys];
    customizing = true;
  }
  function toggle(key: string) {
    draft = draft.includes(key) ? draft.filter((k) => k !== key) : [...draft, key];
  }
  const offKeys = $derived(data.availableWidgetKeys.filter((k: string) => !draft.includes(k)));
</script>

<svelte:head>
  <title>{t("dashboard.my_day.title")}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("dashboard.my_day.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {t("dashboard.welcome", { name: user?.full_name || user?.email || "" })}
    </p>
  </div>
  <button
    class="flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:border-brand hover:text-brand"
    onclick={() => (customizing ? (customizing = false) : startCustomize())}
  >
    <SlidersHorizontal size={15} />
    {t("dashboard.customize.button")}
  </button>
</div>

<form method="POST" action="?/saveLayout" use:enhance bind:this={layoutForm} class="hidden">
  <input type="hidden" name="widgets" value={layoutValue} />
</form>

{#if customizing}
  <div class="mb-6 rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("dashboard.customize.title")}</h2>
    <p class="mb-3 text-xs text-text-muted">{t("dashboard.customize.hint_dnd")}</p>

    <ul class="space-y-1">
      {#each draft as key (key)}
        <li class="flex items-center gap-2 rounded-lg border border-border px-3 py-2">
          <input
            type="checkbox"
            checked
            onchange={() => toggle(key)}
            class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
          />
          <span class="flex-1 text-sm text-text">{t(`dashboard.widget.${key}`)}</span>
        </li>
      {/each}
      {#each offKeys as key (key)}
        <li
          class="flex items-center gap-2 rounded-lg border border-dashed border-border px-3 py-2 opacity-60"
        >
          <input
            type="checkbox"
            onchange={() => toggle(key)}
            class="h-4 w-4 rounded border-border text-brand focus:ring-brand"
          />
          <span class="flex-1 text-sm text-text-muted">{t(`dashboard.widget.${key}`)}</span>
        </li>
      {/each}
    </ul>

    {#if form?.error}<p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    <div class="mt-4 flex flex-wrap items-center gap-2">
      <form
        method="POST"
        action="?/saveLayout"
        use:enhance={() =>
          ({ update }) => {
            customizing = false;
            void update();
          }}
      >
        <input type="hidden" name="widgets" value={draft.join(",")} />
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("dashboard.customize.save")}
        </button>
      </form>
      {#if data.prefsSource === "user"}
        <form
          method="POST"
          action="?/resetLayout"
          use:enhance={() =>
            ({ update }) => {
              customizing = false;
              void update();
            }}
        >
          <button
            class="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text"
          >
            {t("dashboard.customize.reset")}
          </button>
        </form>
      {/if}
      <button
        type="button"
        class="px-2 py-2 text-sm text-text-muted hover:text-text"
        onclick={() => (customizing = false)}
      >
        {t("common.cancel")}
      </button>
    </div>
  </div>
{/if}

{#if tiles.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-10 text-center">
    <p class="text-sm text-text-muted">{t("dashboard.my_day.empty")}</p>
  </div>
{:else}
  <div
    class="grid gap-4 sm:grid-cols-2"
    use:dndzone={{ items: tiles, flipDurationMs: 150, dropTargetStyle: {} }}
    onconsider={handleDndConsider}
    onfinalize={handleDndFinalize}
  >
    {#each tiles as tile (tile.id)}
      {@const widget = widgetFor(tile.id)}
      <div class="cursor-grab active:cursor-grabbing">
        {#if widget}
          {@const WidgetComponent = widget.component}
          <WidgetComponent data={data.widgetData[tile.id]} />
        {/if}
      </div>
    {/each}
  </div>
{/if}
