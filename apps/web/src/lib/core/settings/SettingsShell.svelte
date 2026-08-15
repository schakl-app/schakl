<script lang="ts">
  /**
   * The Instellingen section shell: the navigation on the left, the screen on the right.
   *
   * Thirty-eight settings screens were thirty-eight islands: the only way from Huisstijl to
   * Modules was back up through the index, and nothing on a settings screen said what else lived
   * in the section. The rail is that missing orientation — the same registry the index grid
   * renders, with the current screen marked, and now with the same search box (`SettingsNav`).
   *
   * It lives here rather than in `settings/+layout.svelte` because **three settings screens are not
   * under `/settings/`**. Taaksjablonen, Abonnementen and Domeinen are administered on their own
   * module's working page (#229, so the catalog staff touch daily is where they work), and the rail
   * lists them all the same — so clicking one dropped you out of the section and took the menu with
   * it. A route layout can only wrap its own subtree; a component can travel to the screen.
   *
   * **Below `xl` it is a disclosure, not nothing.** It used to disappear under that breakpoint, on
   * the reasoning that a 13 rem rail would cost every settings form a fifth of its width on a
   * laptop. That is still right about the *rail* and was wrong about the *navigation*: a laptop and
   * a phone got a section of 38 screens with no way to move between them but the browser's back
   * button — the island problem the rail was built to fix, left in place at the two widths most
   * people read on. A collapsed `<details>` costs one row of chrome, opens *over* the content
   * instead of beside it, and closes itself on navigation.
   *
   * The Instellingen index renders no shell: its cards *are* the navigation, with subtitles the
   * rail has no room for.
   */
  import { ChevronDown, Menu } from "@lucide/svelte";
  import type { Snippet } from "svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import SettingsNav from "$lib/core/settings/SettingsNav.svelte";
  import { visibleSettingsScreens } from "$lib/core/settings-nav";

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

  // A deep link (`/settings/roles/<id>`, `/settings/automation/runs`) still marks its section.
  const activeHref = $derived.by(() => {
    const path = page.url.pathname;
    const hit = screens
      .filter((s) => path === s.href || path.startsWith(`${s.href}/`))
      .sort((a, b) => b.href.length - a.href.length)[0];
    return hit?.href ?? null;
  });

  // What the collapsed disclosure is labelled: the screen you are on, so the closed state is a
  // breadcrumb rather than the word "Instellingen" repeated under the breadcrumb that says it.
  const activeTitle = $derived(
    screens.find((s) => s.href === activeHref)?.titleKey ?? "settings.title",
  );

  // Bound rather than left to the browser, so following a link closes it. An open menu covering
  // the screen you just navigated to is the one failure this control has.
  let open = $state(false);
</script>

<div class="xl:grid xl:grid-cols-[13rem_minmax(0,1fr)] xl:gap-8">
  <!-- Below xl: a disclosure above the content. `details` rather than a dialog because it needs
       no focus trap, no scroll lock, and no JavaScript to be usable if hydration is late. -->
  <details bind:open class="mb-4 rounded-xl border border-border bg-surface-raised xl:hidden">
    <summary
      class="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-text"
    >
      <Menu size={16} />
      <span class="flex-1 truncate">{t(activeTitle)}</span>
      <ChevronDown size={16} class="text-text-muted" />
    </summary>
    <div class="max-h-[60vh] overflow-y-auto border-t border-border px-3 pb-3 pt-3">
      <SettingsNav {screens} {activeHref} onnavigate={() => (open = false)} />
    </div>
  </details>

  <nav aria-label={t("settings.title")} class="hidden xl:block">
    <div class="sticky top-6 max-h-[calc(100vh-3rem)] overflow-y-auto pb-4">
      <a
        href="/settings"
        class="mb-3 block text-sm font-semibold text-text hover:text-brand"
        data-sveltekit-preload-data="hover"
      >
        {t("settings.title")}
      </a>
      <SettingsNav {screens} {activeHref} />
    </div>
  </nav>
  <!-- min-w-0: a grid item is otherwise sized by its widest descendant, and one wide table
       would grow the shell instead of scrolling (docs/UX.md). -->
  <div class="min-w-0">
    {@render children()}
  </div>
</div>

<style>
  /* Safari draws its own disclosure triangle without this, beside our chevron. */
  summary::-webkit-details-marker {
    display: none;
  }
</style>
