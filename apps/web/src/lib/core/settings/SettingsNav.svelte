<script lang="ts">
  /**
   * The Instellingen navigation itself: a search box over the section → group → screen tree.
   *
   * Extracted from `SettingsShell` so the same list can be rendered twice at different widths —
   * as the sticky rail from `xl` up, and as a disclosure above the content below it — without the
   * markup, the filtering or the active-item rule existing in two versions that drift.
   *
   * The search is here rather than only on the index because that is where the question is asked.
   * Instellingen has 38 screens; the index has had a search box since the grid grew past scanning,
   * but a settings *screen* had none, so the only way to reach Mollie from Huisstijl was to read a
   * rail of 38 links or go back to the index and type there. The rail is on screen either way —
   * putting the box on top of it costs one input and removes that round trip.
   */
  import { Search } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import {
    groupSettingsScreens,
    matchSettingsScreens,
    type SettingsScreen,
  } from "$lib/core/settings-nav";

  let {
    screens,
    activeHref = null,
    onnavigate,
  }: {
    screens: SettingsScreen[];
    /** The screen the reader is on, already resolved by the host (a deep link still marks it). */
    activeHref?: string | null;
    /** Called when a link is followed — the narrow layout closes its disclosure on it. */
    onnavigate?: () => void;
  } = $props();

  let query = $state("");

  const matches = $derived(matchSettingsScreens(screens, query, t));
  const sections = $derived(groupSettingsScreens(matches));
</script>

<div class="relative mb-3">
  <span class="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-text-muted">
    <Search size={15} />
  </span>
  <input
    type="search"
    bind:value={query}
    placeholder={t("settings.search_placeholder")}
    aria-label={t("settings.search_placeholder")}
    class="w-full min-w-0 rounded-lg border border-border py-1.5 pl-8 pr-3 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
  />
</div>

{#each sections as section (section.key)}
  <p class="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
    {t(section.labelKey)}
  </p>
  {#each section.groups as group (group.key)}
    {#if group.labelKey}
      <p class="mb-1 mt-3 px-2 text-xs font-medium text-text-muted">{t(group.labelKey)}</p>
    {/if}
    <ul>
      {#each group.items as screen (screen.key)}
        <li>
          <a
            href={screen.href}
            onclick={() => onnavigate?.()}
            aria-current={activeHref === screen.href ? "page" : undefined}
            class="block truncate rounded-lg px-2 py-1.5 text-sm {activeHref === screen.href
              ? 'bg-brand/10 font-medium text-brand'
              : 'text-text-muted hover:bg-surface hover:text-text'}"
            data-sveltekit-preload-data="hover"
          >
            {t(screen.titleKey)}
          </a>
        </li>
      {/each}
    </ul>
  {/each}
{:else}
  <p class="px-2 py-3 text-sm text-text-muted">{t("common.no_results")}</p>
{/each}
