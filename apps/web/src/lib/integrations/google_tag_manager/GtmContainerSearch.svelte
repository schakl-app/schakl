<script lang="ts">
  /**
   * Find a Tag Manager container: a search box, not a list.
   *
   * The first version of this screen asked for a hand-typed `GTM-XXXXXXX`, which is the one
   * gesture that cannot fail helpfully — a typo reads as "that container does not exist" and the
   * id itself has to be dug out of the client's website first. The obvious fix, a combobox over
   * everything, is the one Tag Manager's quota forbids: listing every account's containers is one
   * request per account, and against a real 44-account agency grant Google answered
   * `RESOURCE_EXHAUSTED` rather than a list.
   *
   * So the API answers a **search** and this is its face. Two ways in, because those are the two
   * ways anybody identifies a container: the id off the site (one request, exact) or the client's
   * name, which at an agency is also the Tag Manager account's name. What was *not* opened is
   * printed under the box — "8 van 44 accounts" — because a short list that looks complete reads
   * as "we are not in that account", which is a different and wrong fact.
   *
   * No inline-create: a Tag Manager container is somebody else's resource, the documented picker
   * exception (docs/UX.md).
   */
  import { AlertTriangle, Check, ExternalLink, Search, Tags } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  import type { GtmSearchHit, GtmSearchResponse } from "./types";

  let {
    selected = $bindable<GtmSearchHit | null>(null),
    /** Where a reconnect should land the user afterwards — the screen they were looking at. */
    connectNext = "/settings/gtm",
    /** Rendered under the box so a host can say what picking one will do. */
    hint = "",
  }: {
    selected?: GtmSearchHit | null;
    connectNext?: string;
    hint?: string;
  } = $props();

  let query = $state("");
  let loading = $state(false);
  let response = $state<GtmSearchResponse | null>(null);
  /** Which keystroke this component is showing, so a slow answer never overwrites a newer one. */
  let inFlight = 0;

  async function search(term: string) {
    const ticket = ++inFlight;
    loading = true;
    try {
      const res = await fetch(`/marketing/tag-manager/available?q=${encodeURIComponent(term)}`, {
        headers: { accept: "application/json" },
      });
      const body = (await res.json()) as GtmSearchResponse;
      // A late answer to an earlier keystroke is discarded rather than rendered: the box would
      // otherwise flicker back to results for a word the user has already finished changing.
      if (ticket === inFlight) response = body;
    } catch {
      if (ticket === inFlight) response = { containers: [], error: "gtm.search.failed" };
    } finally {
      if (ticket === inFlight) loading = false;
    }
  }

  // Debounced, because every keystroke is a live call to Google and the quota is the reason this
  // component exists. 350 ms is long enough that typing a client's name costs one search.
  let timer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const term = query;
    clearTimeout(timer);
    timer = setTimeout(() => void search(term), 350);
    return () => clearTimeout(timer);
  });

  const hits = $derived(response?.containers ?? []);
  // `narrow_search` is dropped: the API states it as a flag for callers with no screen (MCP), and
  // this one says the same thing with the numbers in it — "8 van 44" is actionable where "narrow
  // your search" is a slogan. Two sentences about one fact, one screen, is one too many.
  const warnings = $derived(
    (response?.warnings ?? []).filter((key) => key !== "gtm.warning.narrow_search"),
  );
  const total = $derived(response?.accounts_total ?? 0);
  const read = $derived(response?.accounts_read ?? 0);
  // The one refusal with a cure the picker can offer: this Google grant predates the Tag Manager
  // consent. Everything else is reported as it arrived.
  const needsConsent = $derived(response?.error === "errors.gtm_not_configured");

  // The query is deliberately left alone. Overwriting it with the container's name would throw
  // away the search that found it — and, because the host clears `selected` after a successful
  // link, would leave the box holding the name of a container that is no longer chosen.
  function choose(hit: GtmSearchHit) {
    selected = hit;
  }
</script>

<div class="space-y-2">
  <div class="relative">
    <Search
      size={15}
      class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
      aria-hidden="true"
    />
    <input
      type="search"
      bind:value={query}
      oninput={() => (selected = null)}
      placeholder={t("gtm.search.placeholder")}
      aria-label={t("gtm.search.placeholder")}
      class="w-full rounded-lg border border-border py-2 pl-9 pr-3 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
    />
  </div>

  {#if hint}
    <p class="text-xs text-text-muted">{hint}</p>
  {/if}

  {#if needsConsent}
    <!-- Reconnecting is the whole cure, and it is incremental: what was already granted stays. -->
    <p class="text-sm text-text-muted">{t("gtm.reconnect_hint")}</p>
    <a
      class="inline-flex items-center gap-1 text-sm text-brand hover:underline"
      data-sveltekit-preload-data="off"
      href="/api/v1/google/oauth/connect?include_tag_manager=true&next={encodeURIComponent(
        connectNext,
      )}"
    >
      {t("gtm.reconnect")}
      <ExternalLink size={14} aria-hidden="true" />
    </a>
  {:else if response?.error}
    <p class="text-sm text-text-muted">{t(response.error)}</p>
  {:else if loading && hits.length === 0}
    <p class="text-sm text-text-muted">{t("gtm.search.loading")}</p>
  {:else if hits.length === 0}
    <!-- "Nothing here" and "we did not look everywhere" are different sentences with different
         next moves, and only the counts can tell them apart. -->
    <p class="text-sm text-text-muted">
      {total > read ? t("gtm.search.none_searched", { read, total }) : t("gtm.search.none")}
    </p>
  {:else}
    <ul class="max-h-64 divide-y divide-border overflow-y-auto rounded-lg border border-border">
      {#each hits as hit (hit.gtm_container_id)}
        <li>
          <button
            type="button"
            onclick={() => choose(hit)}
            class="flex w-full items-start gap-2 px-3 py-2 text-left transition-colors hover:bg-surface"
            class:bg-surface={selected?.gtm_container_id === hit.gtm_container_id}
          >
            <!-- The glyph carries the choice, not the background alone: a tinted row is easy to
                 miss and brand gold reads as a warning on some tenants. -->
            {#if selected?.gtm_container_id === hit.gtm_container_id}
              <Check size={15} class="mt-0.5 shrink-0 text-text" aria-hidden="true" />
            {:else}
              <Tags size={15} class="mt-0.5 shrink-0 text-text-muted" aria-hidden="true" />
            {/if}
            <span class="min-w-0 flex-1">
              <span class="block truncate text-sm text-text">{hit.name}</span>
              <span class="block truncate text-xs text-text-muted">
                {hit.public_id} · {hit.account_name}
                <!-- Shown, never disabled: a container on the wrong client is corrected by
                     picking it again, and the link route reattaches rather than refusing. -->
                {#if hit.already_linked}· {t("gtm.search.already_linked")}{/if}
              </span>
            </span>
          </button>
        </li>
      {/each}
    </ul>
    {#if total > read}
      <p class="text-xs text-text-muted">{t("gtm.search.partial", { read, total })}</p>
    {/if}
  {/if}

  {#each warnings as warning (warning)}
    <p class="flex items-start gap-1.5 text-xs text-text">
      <AlertTriangle size={13} class="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{t(warning)}</span>
    </p>
  {/each}
</div>
