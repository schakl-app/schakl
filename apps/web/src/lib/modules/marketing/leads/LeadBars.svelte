<script lang="ts">
  /**
   * A horizontal bar list — the form a category breakdown takes when the category names are
   * words (a service, a language, a channel group) and would not fit under a column.
   *
   * A row is a control when the widget names a dimension: clicking it narrows every compatible
   * widget on the page to that value (Looker's cross-filter), through the URL rather than
   * component state, so the narrowed view is a link.
   */
  import { fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { channelGroupLabel } from "./format";
  import type { LeadWidget } from "./types";

  let {
    widget,
    activeKeys = [],
    filterHref,
  }: {
    widget: LeadWidget;
    activeKeys?: string[];
    /** A link that toggles this row's value as a page filter; absent = not filterable. */
    filterHref?: ((dimension: string, key: string) => string) | undefined;
  } = $props();

  const rows = $derived(widget.rows);
  const max = $derived(Math.max(...rows.map((r) => r.values.count ?? 0), 1));
  // Which widgets' rows narrow the page is the API's answer (`filterable`), not a list of
  // exceptions kept here: the list said "not a channel" and so drew an error reason as a
  // filter, which no request carries — one click and every tile on the page read zero.
  const filterable = $derived(Boolean(filterHref && widget.filterable && widget.dimension));
  const label = (row: (typeof rows)[number]) =>
    widget.dimension === "channel_group" ? channelGroupLabel(row.key) : row.label;
</script>

{#if rows.length === 0}
  <p class="text-sm text-text-muted">{t("marketing.no_data")}</p>
{:else}
  <ul class="space-y-1">
    {#each rows as row (row.key)}
      {@const value = row.values.count ?? 0}
      {@const active = activeKeys.includes(row.key)}
      <li>
        <svelte:element
          this={filterable ? "a" : "div"}
          href={filterable && widget.dimension
            ? filterHref?.(widget.dimension, row.key)
            : undefined}
          data-sveltekit-noscroll={filterable ? true : undefined}
          class="flex items-center gap-2 rounded px-1 py-0.5 text-sm {filterable
            ? 'cursor-pointer hover:bg-surface-tint'
            : ''} {active ? 'bg-brand/10 font-medium' : ''}"
          aria-current={active ? "true" : undefined}
          title={filterable ? t("marketing.leads.filter.click_to_filter") : undefined}
        >
          <span
            class="w-36 shrink-0 truncate {active ? 'text-text' : 'text-text-muted'}"
            title={label(row)}>{label(row)}</span
          >
          <!-- The track is the tint, not the page's ground: on a white card `bg-surface` is a
               track nobody can see, and a bar with no track has no scale. -->
          <span class="h-2 flex-1 overflow-hidden rounded-full bg-surface-tint">
            <span
              class="block h-full rounded-full {active ? 'bg-brand' : 'bg-brand/70'}"
              style="width: {(value / max) * 100}%"
            ></span>
          </span>
          <span class="w-14 shrink-0 text-right tabular-nums text-text">{fmtNumber(value, 0)}</span>
        </svelte:element>
      </li>
    {/each}
  </ul>
{/if}
