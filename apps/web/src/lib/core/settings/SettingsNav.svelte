<script lang="ts">
  /**
   * The Instellingen navigation itself: a search box over the section → group → screen tree.
   *
   * Extracted from `SettingsShell` so the same list can be rendered twice at different widths —
   * as the sticky rail from `xl` up, and as a disclosure above the content below it — without the
   * markup, the filtering or the active-item rule existing in two versions that drift.
   *
   * **The groups collapse, and that is what makes the rail work at all** (issue #378). Rendered
   * flat, its forty-one links are 1628 px of content inside a box that is 902 px tall on a
   * 1440 × 950 laptop and 752 px on a 1280 × 800 one — so 45–54% of the settings tree sat below the
   * fold of a *nested* scroller, which the page's own scrollbar does not move and overlay
   * scrollbars do not advertise. Standing on Instellingen → Modules, the rail beside you ended at
   * Import & export: the group that screen belongs to was not in its own navigation, and neither
   * was Integraties or Systeem. Seven headings fit anywhere; forty-one links fit nowhere, and the
   * forty-second only made it worse.
   *
   * So a group shows its heading always and its items when it is the one you are in, or you asked
   * for it. Three rules keep that from hiding things:
   *
   *  - **The group holding the active screen is always open**, unconditionally — it is not a
   *    remembered preference, it is where you are, and a reader who collapsed it last week must not
   *    arrive somewhere the rail refuses to admit they are.
   *  - **A search opens every group that matches.** A result you have to go and reveal is not a
   *    result, and the box is right here.
   *  - **What you open by hand is remembered** (`localStorage`), because comparing two groups is an
   *    ordinary thing to want and re-opening on every navigation would punish it.
   *
   * A group with no heading (Mijn instellingen, Systeem) cannot collapse and does not: there is
   * nothing to click and nothing to label the closed state with. They are three items and two.
   *
   * The search is here rather than only on the index because that is where the question is asked.
   * The one screen that owns its own search over the same list is the index, whose *content* is
   * that list — so it renders the rail with `showSearch={false}`, and there is exactly one search
   * box per screen everywhere.
   */
  import { ChevronRight, Search } from "@lucide/svelte";

  import { browser } from "$app/environment";
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
    showSearch = true,
  }: {
    screens: SettingsScreen[];
    /** The screen the reader is on, already resolved by the host (a deep link still marks it). */
    activeHref?: string | null;
    /** Called when a link is followed — the narrow layout closes its disclosure on it. */
    onnavigate?: () => void;
    /** Off on the Instellingen index, whose own content is this list and owns the search over it. */
    showSearch?: boolean;
  } = $props();

  const STORE_KEY = "schakl.settings-nav.open";

  let query = $state("");

  /** Groups the reader opened by hand. A preference, so it is read once rather than tracked. */
  let opened = $state<string[]>(readOpened());

  function readOpened(): string[] {
    if (!browser) return [];
    try {
      const raw: unknown = JSON.parse(localStorage.getItem(STORE_KEY) ?? "[]");
      return Array.isArray(raw) ? raw.filter((k): k is string => typeof k === "string") : [];
    } catch {
      return [];
    }
  }

  const matches = $derived(matchSettingsScreens(screens, query, t));
  const sections = $derived(groupSettingsScreens(matches));
  const searching = $derived(query.trim().length > 0);

  /** The group the reader is standing in — always open, whatever the stored preference says. */
  const activeGroup = $derived(screens.find((s) => s.href === activeHref)?.group ?? null);

  /**
   * Held open by something other than the reader's choice: the group they are standing in, or a
   * search with a match in it. Such a header is **not rendered as a control** — clicking it would
   * change the stored preference and change nothing on screen, which is a button that does
   * nothing, and would quietly rearrange the rail on some later page where it *is* collapsible.
   */
  function isForced(groupKey: string, labelKey: string | null): boolean {
    return !labelKey || searching || groupKey === activeGroup;
  }

  function isOpen(groupKey: string, labelKey: string | null): boolean {
    return isForced(groupKey, labelKey) || opened.includes(groupKey);
  }

  function toggle(groupKey: string): void {
    opened = opened.includes(groupKey)
      ? opened.filter((k) => k !== groupKey)
      : [...opened, groupKey];
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(opened));
    } catch {
      /* private mode, a full quota — the nav still works, it just forgets */
    }
  }
</script>

{#if showSearch}
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
{/if}

{#each sections as section (section.key)}
  <p class="mb-0.5 mt-3 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
    {t(section.labelKey)}
  </p>
  {#each section.groups as group (group.key)}
    {@const open = isOpen(group.key, group.labelKey)}
    {@const forced = isForced(group.key, group.labelKey)}
    {#if group.labelKey && forced}
      <p class="mt-1 flex items-center gap-1 px-2 py-1 text-xs font-medium text-text-muted">
        <ChevronRight size={13} class="shrink-0 rotate-90" aria-hidden="true" />
        <span class="flex-1 truncate">{t(group.labelKey)}</span>
      </p>
    {:else if group.labelKey}
      <!-- A button rather than <details>: the open state is decided by three things at once
           (where you are, what you searched, what you opened) and `details` owns its own. -->
      <button
        type="button"
        onclick={() => toggle(group.key)}
        aria-expanded={open}
        class="mt-1 flex w-full items-center gap-1 rounded-lg px-2 py-1 text-left text-xs
          font-medium text-text-muted hover:bg-surface hover:text-text"
      >
        <ChevronRight
          size={13}
          class="shrink-0 transition-transform {open ? 'rotate-90' : ''}"
          aria-hidden="true"
        />
        <span class="flex-1 truncate">{t(group.labelKey)}</span>
        {#if !open}
          <!-- What the closed state is hiding. Without it a collapsed group reads as an empty
               one, which is the one failure this control could plausibly introduce. -->
          <span class="tabular-nums text-[11px] text-text-muted/70">{group.items.length}</span>
        {/if}
      </button>
    {/if}
    {#if open}
      <ul class={group.labelKey ? "ml-1 border-l border-border pl-2" : ""}>
        {#each group.items as screen (screen.key)}
          <li>
            <a
              href={screen.href}
              onclick={() => onnavigate?.()}
              aria-current={activeHref === screen.href ? "page" : undefined}
              class="block truncate rounded-lg px-2 py-1 text-sm {activeHref === screen.href
                ? 'bg-brand/10 font-medium text-brand'
                : 'text-text-muted hover:bg-surface hover:text-text'}"
              data-sveltekit-preload-data="hover"
            >
              {t(screen.titleKey)}
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  {/each}
{:else}
  <p class="px-2 py-3 text-sm text-text-muted">{t("common.no_results")}</p>
{/each}
