<script lang="ts">
  /**
   * The Instellingen section shell: the rail on the left, the screen on the right.
   *
   * Thirty-five settings screens were thirty-five islands: the only way from Huisstijl to Modules
   * was back up through the index, and nothing on a settings screen said what else lived in the
   * section. The rail is that missing orientation — the same registry the index grid renders, with
   * the current screen marked.
   *
   * It lives here rather than in `settings/+layout.svelte` because **three settings screens are not
   * under `/settings/`**. Taaksjablonen, Abonnementen and Domeinen are administered on their own
   * module's working page (#229, so the catalog staff touch daily is where they work), and the rail
   * lists them all the same — so clicking one dropped you out of the section and took the menu with
   * it. A route layout can only wrap its own subtree; a component can travel to the screen.
   *
   * It appears from `xl` up, where there is width to spare beside the content. Below that the
   * content keeps the full column and the app-wide breadcrumb row is the way back — a 13 rem rail
   * on a laptop would cost every settings form a fifth of its width to save one click.
   *
   * The Instellingen index renders no shell: its cards *are* the navigation, with subtitles the
   * rail has no room for.
   */
  import type { Snippet } from "svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import {
    SETTINGS_GROUPS,
    SETTINGS_SECTIONS,
    visibleSettingsScreens,
  } from "$lib/core/settings-nav";

  let {
    cloud = false,
    children,
  }: {
    /**
     * Cloud posture (epic #199) — it alone decides whether Service-toegang exists, and permission
     * cannot answer it (the owner's `*` satisfies a check for a capability a self-hosted box does
     * not have). Comes from `settingsShellData()`, so every screen carrying the rail shows the
     * same entries.
     */
    cloud?: boolean;
    children: Snippet;
  } = $props();

  const screens = $derived(
    visibleSettingsScreens({
      user: page.data.user,
      enabledModules: page.data.theme?.enabledModules ?? [],
      cloud,
    }),
  );

  const sections = $derived(
    SETTINGS_SECTIONS.map((section) => ({
      ...section,
      groups: SETTINGS_GROUPS.filter((g) => g.section === section.key)
        .map((group) => ({ ...group, items: screens.filter((s) => s.group === group.key) }))
        .filter((group) => group.items.length > 0),
    })).filter((section) => section.groups.length > 0),
  );

  // A deep link (`/settings/roles/<id>`, `/settings/automation/runs`) still marks its section.
  const activeHref = $derived.by(() => {
    const path = page.url.pathname;
    const hit = screens
      .filter((s) => path === s.href || path.startsWith(`${s.href}/`))
      .sort((a, b) => b.href.length - a.href.length)[0];
    return hit?.href ?? null;
  });
</script>

<div class="xl:grid xl:grid-cols-[13rem_minmax(0,1fr)] xl:gap-8">
  <nav aria-label={t("settings.title")} class="hidden xl:block">
    <div class="sticky top-6 max-h-[calc(100vh-3rem)] overflow-y-auto pb-4">
      <a
        href="/settings"
        class="mb-3 block text-sm font-semibold text-text hover:text-brand"
        data-sveltekit-preload-data="hover"
      >
        {t("settings.title")}
      </a>
      {#each sections as section (section.key)}
        <p class="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
          {t(section.labelKey)}
        </p>
        {#each section.groups as group (group.key)}
          {#if group.labelKey}
            <p class="mb-1 mt-3 px-2 text-xs font-medium text-text-muted">
              {t(group.labelKey)}
            </p>
          {/if}
          <ul>
            {#each group.items as screen (screen.key)}
              <li>
                <a
                  href={screen.href}
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
      {/each}
    </div>
  </nav>
  <!-- min-w-0: a grid item is otherwise sized by its widest descendant, and one wide table
       would grow the shell instead of scrolling (docs/UX.md). -->
  <div class="min-w-0">
    {@render children()}
  </div>
</div>
