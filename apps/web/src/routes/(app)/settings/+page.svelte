<script lang="ts">
  import { Search } from "@lucide/svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import {
    SETTINGS_GROUPS,
    SETTINGS_SECTIONS,
    visibleSettingsScreens,
    type SettingsScreen,
  } from "$lib/core/settings-nav";
  import { pageTitle } from "$lib/core/title";

  let { data } = $props();

  // Cards render from the registry, gated (docs/UX.md: a control that renders without checking
  // `can()` is a bug). The grid used to show all thirty-odd screens to anyone who could open one
  // of them, so a branding-only admin got a wall of cards that bounced them to the dashboard.
  const screens = $derived(
    visibleSettingsScreens({
      user: data.user,
      enabledModules: page.data.theme?.enabledModules ?? [],
      cloud: data.cloud,
    }),
  );

  // Thirty-five screens is past the point where scanning five groups beats typing. Matching runs
  // over the title, the subtitle *and* the screen's hidden keywords, so "btw" finds Facturatie and
  // "wachtwoord" finds Mijn account — neither word appears on its card.
  let query = $state("");
  function haystack(screen: SettingsScreen): string {
    const extra = screen.keywordsKey ? ` ${t(screen.keywordsKey)}` : "";
    return `${t(screen.titleKey)} ${t(screen.subtitleKey)}${extra}`.toLowerCase();
  }
  const terms = $derived(query.trim().toLowerCase().split(/\s+/).filter(Boolean));
  const matches = $derived(
    terms.length === 0
      ? screens
      : screens.filter((s) => terms.every((w) => haystack(s).includes(w))),
  );

  const sections = $derived(
    SETTINGS_SECTIONS.map((section) => ({
      ...section,
      groups: SETTINGS_GROUPS.filter((g) => g.section === section.key)
        .map((group) => ({ ...group, items: matches.filter((s) => s.group === group.key) }))
        .filter((group) => group.items.length > 0),
    })).filter((section) => section.groups.length > 0),
  );
</script>

<svelte:head>
  <title>{pageTitle(t("settings.title"))}</title>
</svelte:head>

{#snippet card(screen: SettingsScreen)}
  <a
    href={screen.href}
    class="rounded-xl border border-border bg-surface-raised p-5 hover:border-brand"
  >
    <h3 class="text-sm font-semibold text-text">{t(screen.titleKey)}</h3>
    <p class="mt-1 text-sm text-text-muted">{t(screen.subtitleKey)}</p>
  </a>
{/snippet}

<div class="mb-6 flex flex-wrap items-center justify-between gap-3">
  <h1 class="text-xl font-semibold text-text">{t("settings.title")}</h1>
  <div class="relative w-full sm:w-64">
    <span class="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-text-muted">
      <Search size={15} />
    </span>
    <input
      type="search"
      bind:value={query}
      placeholder={t("settings.search_placeholder")}
      aria-label={t("settings.search_placeholder")}
      class="w-full min-w-0 rounded-lg border border-border py-2 pl-8 pr-3 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
    />
  </div>
</div>

{#each sections as section (section.key)}
  <section class="mb-8">
    <h2 class="mb-3 text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t(section.labelKey)}
    </h2>
    {#each section.groups as group (group.key)}
      {#if group.labelKey}
        <h3 class="mb-2 text-sm font-medium text-text">{t(group.labelKey)}</h3>
      {/if}
      <div class="mb-6 grid gap-4 sm:grid-cols-2">
        {#each group.items as screen (screen.key)}
          {@render card(screen)}
        {/each}
      </div>
    {/each}
  </section>
{:else}
  <p class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted">
    {terms.length ? t("common.no_results") : t("settings.none_available")}
  </p>
{/each}
