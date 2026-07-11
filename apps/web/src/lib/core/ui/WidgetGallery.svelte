<script lang="ts">
  /**
   * The dashboard widget gallery (issue #15): browse the widgets not yet on the board, grouped by
   * category, and add one. Each card shows a **static, data-free** preview — the gallery never
   * calls a widget's `load()`, so opening it costs zero API calls (docs/PERFORMANCE.md). A widget
   * with no bespoke preview renders a size-shaped skeleton.
   *
   * The "Add" button is the accessible, mobile-friendly path the issue requires; drag reorder on
   * the board itself stays as it was. Used by both the personal dashboard (in edit mode) and the
   * org default template under Instellingen → Dashboard.
   */
  import { Plus } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { type DashboardWidgetSpec, widgetTitleKey, type WidgetSize } from "$lib/core/registry";

  let {
    widgets,
    activeKeys,
    onadd,
  }: {
    widgets: DashboardWidgetSpec[];
    activeKeys: string[];
    onadd: (key: string) => void;
  } = $props();

  const offWidgets = $derived(widgets.filter((w) => !activeKeys.includes(w.key)));

  // Cards grouped by category, categories in first-seen order (widgets already arrive sorted).
  const groups = $derived.by(() => {
    const map = new Map<string, DashboardWidgetSpec[]>();
    for (const w of offWidgets) {
      const cat = w.category ?? "dashboard.category.other";
      const list = map.get(cat) ?? [];
      list.push(w);
      map.set(cat, list);
    }
    return [...map.entries()];
  });

  // Skeleton bar rows per size — a hint of the widget's shape without any data.
  const skeletonRows: Record<WidgetSize, number> = { sm: 2, md: 3, lg: 5 };
</script>

{#if offWidgets.length === 0}
  <p class="text-sm text-text-muted">{t("dashboard.gallery.all_added")}</p>
{:else}
  <div class="space-y-4">
    {#each groups as [category, catWidgets] (category)}
      <div>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
          {t(category)}
        </h3>
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {#each catWidgets as widget (widget.key)}
            <div class="flex flex-col overflow-hidden rounded-xl border border-border bg-surface">
              <!-- Preview: the widget's own static one, or a size-shaped skeleton. -->
              <div class="border-b border-border bg-surface-raised p-3">
                {#if widget.preview}
                  {@const Preview = widget.preview}
                  <Preview />
                {:else}
                  <div class="space-y-1.5" aria-hidden="true">
                    {#each Array(skeletonRows[widget.size ?? "md"]) as _, i (i)}
                      <div
                        class="h-2 rounded bg-border"
                        style="width: {90 - i * 12}%"
                      ></div>
                    {/each}
                  </div>
                {/if}
              </div>
              <div class="flex flex-1 flex-col p-3">
                <p class="text-sm font-medium text-text">{t(widgetTitleKey(widget))}</p>
                {#if widget.descriptionKey}
                  <p class="mt-0.5 flex-1 text-xs text-text-muted">{t(widget.descriptionKey)}</p>
                {/if}
                <button
                  type="button"
                  onclick={() => onadd(widget.key)}
                  class="mt-2 flex items-center justify-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-text hover:border-brand hover:text-brand"
                >
                  <Plus size={14} />
                  {t("dashboard.gallery.add")}
                </button>
              </div>
            </div>
          {/each}
        </div>
      </div>
    {/each}
  </div>
{/if}
