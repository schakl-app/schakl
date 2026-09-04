<script lang="ts">
  import { page } from "$app/state";
  /**
   * The client picker on Marketing: one tile per client that actually has a source linked.
   *
   * It replaced a dropdown over *every* company, which could not answer the only question
   * being asked at that moment — which of these clients has a dashboard behind their name.
   * Most of the list led to an empty screen, and nothing on it said so beforehand. A tile can
   * say it: the chips are the connection, so "Acme has Analytics and Search Console, and one
   * of the two is failing" is readable before anybody clicks.
   *
   * Each tile is an `<a href>`, not a click handler: the client is in the URL (`?company=`),
   * so the back button lands where the user left and a link to a client's dashboard is
   * shareable (CLAUDE.md §9, the URL is the view).
   */
  import { AlertTriangle, Clock, Plus } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  import { namedSourceLabel } from "./format";
  import type { MarketingClientRow } from "./types";

  let {
    rows,
    total,
    hrefFor,
    onconnect,
    sourceLabels = {},
  }: {
    rows: MarketingClientRow[];
    /** The tenant's own source names (#446), so a tile reads the word the dashboard prints. */
    sourceLabels?: Record<string, string>;
    /** Linked clients in view — `rows` is capped, and a cap must never read as everything. */
    total: number;
    hrefFor: (companyId: string) => string;
    /**
     * Open the connect dialog (#338). Absent when the caller may not link, in which case the
     * old sentence stands: it still says where connecting happens, which is all a reader
     * without the permission can act on.
     *
     * It replaced a link to `/companies` — the whole client list, from which the actual gesture
     * (open the client, ⋯ → Bewerken, then the picker) was still two undiscoverable steps away.
     */
    onconnect?: () => void;
  } = $props();

  let query = $state("");

  // Filtered in the browser, not through a round trip: the whole set is already here, and the
  // only thing an `?q=` would add is a load per keystroke over rows nobody removed. It stays
  // honest because the cap is stated below rather than hidden behind the filter.
  const shown = $derived(
    query.trim()
      ? rows.filter((row) => row.company_name.toLowerCase().includes(query.trim().toLowerCase()))
      : rows,
  );
  // A filter box over six tiles is more chrome than list.
  const filterable = $derived(rows.length > 8);
</script>

{#if rows.length === 0}
  <div class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center">
    <p class="text-sm text-text-muted">{t("marketing.clients.empty")}</p>
    {#if onconnect}
      <button
        type="button"
        class="mt-3 inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
        onclick={onconnect}
      >
        <Plus size={15} aria-hidden="true" />
        {t("marketing.connect.open")}
      </button>
    {:else if !page.data.user?.isPortal}
      <!-- "Koppel een bron op de klantpagina" is a sentence about our desk: a client is told
           there is nothing here yet, never where the agency would go to attach something. -->
      <a href="/companies" class="mt-2 inline-block text-sm font-medium text-brand hover:underline">
        {t("marketing.clients.missing_cta")}
      </a>
    {/if}
  </div>
{:else}
  {#if filterable}
    <div class="mb-3">
      <input
        type="search"
        bind:value={query}
        placeholder={t("marketing.clients.filter")}
        aria-label={t("marketing.clients.filter")}
        class="w-full max-w-xs rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      />
    </div>
  {/if}

  {#if shown.length === 0}
    <p
      class="rounded-xl border border-dashed border-border p-6 text-center text-sm text-text-muted"
    >
      {t("marketing.clients.no_match")}
    </p>
  {:else}
    <ul class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {#each shown as row (row.company_id)}
        <li>
          <a
            href={hrefFor(row.company_id)}
            class="block h-full rounded-xl border border-border bg-surface-raised p-4 hover:border-brand"
          >
            <span class="block truncate text-sm font-medium text-text">{row.company_name}</span>
            <span class="mt-2 flex flex-wrap gap-1.5">
              {#each row.sources as source (source.source)}
                <!-- The glyph carries the state, never the colour: `text-brand` is gold on some
                     tenants, so a coloured chip would read as a warning on every tile. -->
                {@const state = page.data.user?.isPortal ? "ok" : source.state}
                <span
                  class="flex items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5 text-xs {state ===
                  'ok'
                    ? 'text-text-muted'
                    : 'text-text'}"
                  title={state === "ok" ? undefined : t(`marketing.health.${state}`)}
                >
                  <!-- A source's health ("synchronisatiefout") is the agency's to act on; on a
                       client's own screen the chip names the source and nothing about our sync. -->
                  {#if state === "error"}
                    <AlertTriangle size={12} class="shrink-0" aria-hidden="true" />
                  {:else if state === "pending"}
                    <Clock size={12} class="shrink-0" aria-hidden="true" />
                  {/if}
                  {namedSourceLabel(source.source, sourceLabels)}
                  {#if source.links > 1}
                    <span class="tabular-nums"
                      >{t("marketing.clients.count", { count: String(source.links) })}</span
                    >
                  {/if}
                  {#if state !== "ok"}
                    <span class="sr-only">{t(`marketing.health.${state}`)}</span>
                  {/if}
                </span>
              {/each}
            </span>
          </a>
        </li>
      {/each}
    </ul>
  {/if}

  <p class="mt-3 text-xs text-text-muted" class:hidden={page.data.user?.isPortal}>
    {#if rows.length < total}
      <!-- No silent caps (docs/UX.md): a picker showing a prefix of the clients must say so,
           or the one that is missing reads as one that is not connected. -->
      {t("marketing.clients.showing", { shown: String(rows.length), total: String(total) })} ·
    {/if}
    {t("marketing.clients.missing")}
    {#if onconnect}
      <button type="button" class="font-medium text-brand hover:underline" onclick={onconnect}>
        {t("marketing.connect.open")}
      </button>
    {:else}
      <a href="/companies" class="font-medium text-brand hover:underline">
        {t("marketing.clients.missing_cta")}
      </a>
    {/if}
  </p>
{/if}
