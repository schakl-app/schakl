<script lang="ts">
  import "$lib/modules"; // ensure the web-module registry is populated
  import {
    BarChart3,
    CalendarDays,
    Menu,
    ChevronDown,
    ChevronRight,
    ChevronsLeft,
    ChevronsRight,
    Handshake,
    LayoutDashboard,
    LogOut,
    Settings,
    UserRound,
  } from "@lucide/svelte";

  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { navItemsFor, type NavItem } from "$lib/core/registry";

  let { children } = $props();

  const theme = $derived(page.data.theme);
  const user = $derived(page.data.user);
  const nav = $derived(navItemsFor(theme?.enabledModules ?? []));
  const path = $derived(page.url.pathname);
  const canManage = $derived(user?.canManage ?? false);

  // Grouped nav: a group renders once, where its first item would sit, holding all members.
  type NavEntry =
    { kind: "item"; item: NavItem } | { kind: "group"; key: string; items: NavItem[] };
  const navEntries = $derived.by<NavEntry[]>(() => {
    const entries: NavEntry[] = [];
    const seen = new Set<string>();
    for (const item of nav) {
      if (!item.group) {
        entries.push({ kind: "item", item });
      } else if (!seen.has(item.group)) {
        seen.add(item.group);
        entries.push({
          kind: "group",
          key: item.group,
          items: nav.filter((i) => i.group === item.group),
        });
      }
    }
    return entries;
  });

  // Group disclosure state (persisted; a group with an active child always opens).
  let groupOpen = $state<Record<string, boolean>>({});
  $effect(() => {
    try {
      groupOpen = JSON.parse(localStorage.getItem("vlotr:navgroups") ?? "{}");
    } catch {
      groupOpen = {};
    }
  });
  function isGroupOpen(entry: NavEntry & { kind: "group" }): boolean {
    if (entry.items.some((i) => path.startsWith(i.href))) return true;
    return groupOpen[entry.key] ?? true;
  }
  function toggleGroup(key: string) {
    groupOpen = { ...groupOpen, [key]: !(groupOpen[key] ?? true) };
    localStorage.setItem("vlotr:navgroups", JSON.stringify(groupOpen));
  }

  // --- mobile nav drawer -------------------------------------------------------
  let mobileNavOpen = $state(false);
  // Close the drawer on navigation.
  $effect(() => {
    void path;
    mobileNavOpen = false;
  });

  // --- collapsible sidebar (persisted per browser) ---------------------------
  let collapsed = $state(false);
  $effect(() => {
    collapsed = localStorage.getItem("vlotr:sidebar") === "collapsed";
  });
  function toggleSidebar() {
    collapsed = !collapsed;
    localStorage.setItem("vlotr:sidebar", collapsed ? "collapsed" : "open");
  }

  // --- profile menu -----------------------------------------------------------
  let profileOpen = $state(false);
  function initials(name: string | null | undefined, email: string | undefined): string {
    const source = name || email || "?";
    const parts = source.split(/[\s@._-]+/).filter(Boolean);
    return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
  }

  const itemClass = (active: boolean) =>
    `flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-100 ${
      active ? "bg-neutral-100 font-medium" : ""
    }`;
</script>

<svelte:window
  onclick={(e) => {
    if (profileOpen && !(e.target as HTMLElement).closest?.("[data-profile-menu]")) {
      profileOpen = false;
    }
  }}
/>

<div class="flex min-h-screen">
  {#if mobileNavOpen}
    <button
      type="button"
      class="fixed inset-0 z-40 bg-neutral-900/40 sm:hidden"
      aria-label={t("common.close")}
      onclick={() => (mobileNavOpen = false)}
    ></button>
  {/if}
  <aside
    class="{mobileNavOpen
      ? 'fixed inset-y-0 left-0 z-50 block w-64 overflow-y-auto shadow-xl'
      : 'hidden'} shrink-0 border-r border-neutral-200 bg-white transition-[width] duration-150 sm:static sm:block
      {collapsed && !mobileNavOpen ? 'sm:w-16' : 'sm:w-60'}"
  >
    <div class="flex h-14 items-center gap-2 border-b border-neutral-200 px-4">
      {#if theme?.logoUrl}
        <img src={theme.logoUrl} alt={theme.brandName} class="h-7 w-auto" />
      {/if}
      {#if !collapsed && theme?.showBrandName !== false}
        <span class="truncate font-semibold text-neutral-900">{theme?.brandName}</span>
      {/if}
    </div>
    <nav class="space-y-1 p-2">
      <a
        href="/"
        class={itemClass(path === "/")}
        title={collapsed ? t("nav.dashboard") : undefined}
      >
        <LayoutDashboard size={18} class="shrink-0 text-neutral-500" />
        {#if !collapsed}<span class="truncate">{t("nav.dashboard")}</span>{/if}
      </a>
      <a
        href="/calendar"
        class={itemClass(path.startsWith("/calendar"))}
        title={collapsed ? t("nav.calendar") : undefined}
      >
        <CalendarDays size={18} class="shrink-0 text-neutral-500" />
        {#if !collapsed}<span class="truncate">{t("nav.calendar")}</span>{/if}
      </a>
      {#each navEntries as entry (entry.kind === "group" ? `g:${entry.key}` : entry.item.key)}
        {#if entry.kind === "item"}
          {@const Icon = entry.item.icon}
          <a
            href={entry.item.href}
            class={itemClass(path.startsWith(entry.item.href))}
            title={collapsed ? entry.item.label() : undefined}
          >
            {#if Icon}
              <Icon size={18} class="shrink-0 text-neutral-500" />
            {:else}
              <span class="h-[18px] w-[18px] shrink-0"></span>
            {/if}
            {#if !collapsed}<span class="truncate">{entry.item.label()}</span>{/if}
          </a>
        {:else if collapsed}
          <!-- Collapsed rail: group members render flat as icons. -->
          {#each entry.items as item (item.key)}
            {@const Icon = item.icon}
            <a href={item.href} class={itemClass(path.startsWith(item.href))} title={item.label()}>
              {#if Icon}<Icon size={18} class="shrink-0 text-neutral-500" />{/if}
            </a>
          {/each}
        {:else}
          {@const open = isGroupOpen(entry)}
          <button
            type="button"
            class="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-100"
            onclick={() => toggleGroup(entry.key)}
            aria-expanded={open}
          >
            <Handshake size={18} class="shrink-0 text-neutral-500" />
            <span class="flex-1 truncate text-left">{t(`nav.group.${entry.key}`)}</span>
            {#if open}
              <ChevronDown size={14} class="shrink-0 text-neutral-400" />
            {:else}
              <ChevronRight size={14} class="shrink-0 text-neutral-400" />
            {/if}
          </button>
          {#if open}
            <div class="space-y-0.5">
              {#each entry.items as item (item.key)}
                {@const Icon = item.icon}
                <a
                  href={item.href}
                  class="flex items-center gap-2.5 rounded-lg py-1.5 pl-9 pr-3 text-sm text-neutral-600 hover:bg-neutral-100
                    {path.startsWith(item.href)
                    ? 'bg-neutral-100 font-medium text-neutral-900'
                    : ''}"
                >
                  {#if Icon}<Icon size={15} class="shrink-0 text-neutral-400" />{/if}
                  <span class="truncate">{item.label()}</span>
                </a>
              {/each}
            </div>
          {/if}
        {/if}
      {/each}
      {#if canManage}
        <a
          href="/overview"
          class={itemClass(path.startsWith("/overview"))}
          title={collapsed ? t("nav.overview") : undefined}
        >
          <BarChart3 size={18} class="shrink-0 text-neutral-500" />
          {#if !collapsed}<span class="truncate">{t("nav.overview")}</span>{/if}
        </a>
        <a
          href="/settings"
          class={itemClass(path.startsWith("/settings") && !path.startsWith("/settings/account"))}
          title={collapsed ? t("nav.settings") : undefined}
        >
          <Settings size={18} class="shrink-0 text-neutral-500" />
          {#if !collapsed}<span class="truncate">{t("nav.settings")}</span>{/if}
        </a>
      {/if}
    </nav>
    <div class="p-2">
      <button
        type="button"
        class="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
        onclick={toggleSidebar}
        aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
        title={collapsed ? t("nav.expand") : t("nav.collapse")}
      >
        {#if collapsed}
          <ChevronsRight size={18} class="shrink-0" />
        {:else}
          <ChevronsLeft size={18} class="shrink-0" />
          <span>{t("nav.collapse")}</span>
        {/if}
      </button>
    </div>
  </aside>

  <div class="flex flex-1 flex-col">
    <header
      class="flex h-14 items-center justify-between gap-4 border-b border-neutral-200 bg-white px-4 text-sm sm:justify-end sm:px-6"
    >
      <button
        type="button"
        class="rounded-lg p-2 text-neutral-500 hover:bg-neutral-100 sm:hidden"
        onclick={() => (mobileNavOpen = true)}
        aria-label={t("nav.expand")}
      >
        <Menu size={20} />
      </button>
      <div class="relative" data-profile-menu>
        <button
          type="button"
          class="flex items-center gap-2 rounded-full py-1 pl-1 pr-3 hover:bg-neutral-100"
          onclick={() => (profileOpen = !profileOpen)}
          aria-haspopup="menu"
          aria-expanded={profileOpen}
        >
          <span
            class="flex h-8 w-8 items-center justify-center rounded-full bg-brand/10 text-xs font-semibold text-brand"
          >
            {initials(user?.full_name, user?.email)}
          </span>
          <span class="hidden font-medium text-neutral-800 md:inline">
            {user?.full_name || user?.email}
          </span>
        </button>

        {#if profileOpen}
          <div
            role="menu"
            class="absolute right-0 z-30 mt-1 w-64 rounded-xl border border-neutral-200 bg-white py-1 shadow-lg"
          >
            <div class="border-b border-neutral-100 px-4 py-3">
              <p class="truncate text-sm font-semibold text-neutral-900">
                {user?.full_name || user?.email}
              </p>
              {#if user?.full_name}
                <p class="truncate text-xs text-neutral-500">{user?.email}</p>
              {/if}
            </div>
            <a
              href="/settings/account"
              class="flex items-center gap-2 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
              onclick={() => (profileOpen = false)}
            >
              <UserRound size={16} class="text-neutral-400" />
              {t("header.my_settings")}
            </a>
            <form method="POST" action="/logout" class="border-t border-neutral-100">
              <button
                class="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-neutral-700 hover:bg-neutral-50"
              >
                <LogOut size={16} class="text-neutral-400" />
                {t("auth.sign_out")}
              </button>
            </form>
          </div>
        {/if}
      </div>
    </header>

    <main class="flex-1 p-6">
      {@render children()}
    </main>
  </div>
</div>
