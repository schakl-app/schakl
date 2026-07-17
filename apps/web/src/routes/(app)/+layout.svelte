<script lang="ts">
  import "$lib/modules"; // ensure the web-module registry is populated
  import Avatar from "$lib/core/ui/Avatar.svelte";
  import {
    BarChart3,
    CalendarDays,
    Menu,
    ChevronDown,
    ChevronRight,
    ChevronsLeft,
    ChevronsRight,
    FileText,
    Handshake,
    LayoutDashboard,
    LogOut,
    ServerCog,
    Settings,
    Sparkles,
    UserRound,
    VenetianMask,
  } from "@lucide/svelte";

  import { setContext } from "svelte";

  import { page } from "$app/state";
  import { AI_CONTEXT_KEY, aiEnabled, type AIFeature, type AssistantEntity } from "$lib/core/ai";
  import AssistantPanel from "$lib/core/ai/AssistantPanel.svelte";
  import { breadcrumbsFor } from "$lib/core/breadcrumbs";
  import { t } from "$lib/core/i18n";
  import { can, canAccessSettings } from "$lib/core/permissions";
  import { navItemsFor, type NavItem } from "$lib/core/registry";
  import Breadcrumbs from "$lib/core/ui/Breadcrumbs.svelte";
  import SlideOver from "$lib/core/ui/SlideOver.svelte";
  import NotificationBell from "$lib/modules/notifications/NotificationBell.svelte";

  let { children } = $props();

  const theme = $derived(page.data.theme);
  const user = $derived(page.data.user);
  // The portal shell (#193): a contact-linked login gets a reduced frame — their homepage is
  // the curated dashboard, module nav shrinks to what the client role can read, no bell/AI.
  // UX only; the API's deny-by-default permissions and the company horizon are the boundary.
  const isPortal = $derived(user?.isPortal ?? false);
  const nav = $derived(
    isPortal
      ? []
      : navItemsFor(theme?.enabledModules ?? [], user, page.data.navPref?.items ?? null),
  );
  const path = $derived(page.url.pathname);
  const showOverview = $derived(!isPortal && can(user, "time.report.read"));
  const showSettings = $derived(!isPortal && canAccessSettings(user?.permissions));
  // The bell is a shell element, not a nav item, so it is gated here rather than by the registry.
  const hasNotifications = $derived(
    !isPortal && (theme?.enabledModules?.includes("notifications") ?? false),
  );

  // AI affordance gate (epic #131): shared components (RichTextEditor's assist) read this
  // through context so no consumer needs per-module wiring. Off means invisible (#126).
  setContext(AI_CONTEXT_KEY, { enabled: (feature: AIFeature) => aiEnabled(user, feature) });
  const hasAssistant = $derived(aiEnabled(user, "assistant"));
  let assistantOpen = $state(false);
  // The assistant inherits the page's entity (#127): derived from the route, labelled from
  // the data the page already loaded — no extra call.
  const assistantContext = $derived.by<AssistantEntity | null>(() => {
    const match = path.match(/^\/(companies|projects|tasks)\/([0-9a-f-]{36})/);
    if (!match) return null;
    const kind = { companies: "company", projects: "project", tasks: "task" }[match[1]]!;
    const data = page.data as Record<string, { name?: string; title?: string } | undefined>;
    const record = data[kind];
    return {
      entity_type: kind,
      entity_id: match[2],
      label: record?.name ?? record?.title ?? null,
    };
  });

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
      groupOpen = JSON.parse(localStorage.getItem("schakl:navgroups") ?? "{}");
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
    localStorage.setItem("schakl:navgroups", JSON.stringify(groupOpen));
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
    collapsed = localStorage.getItem("schakl:sidebar") === "collapsed";
  });
  function toggleSidebar() {
    collapsed = !collapsed;
    localStorage.setItem("schakl:sidebar", collapsed ? "collapsed" : "open");
  }

  // --- profile menu -----------------------------------------------------------
  let profileOpen = $state(false);

  const itemClass = (active: boolean) =>
    `flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-text hover:bg-surface ${
      active ? "bg-surface font-medium" : ""
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
      : 'hidden'} shrink-0 border-r border-border bg-surface-raised transition-[width] duration-150 sm:static sm:block
      {collapsed && !mobileNavOpen ? 'sm:w-16' : 'sm:w-60'}"
  >
    <div class="flex h-14 items-center gap-2 border-b border-border px-4">
      {#if theme?.logoUrl}
        <img src={theme.logoUrl} alt={theme.brandName} class="h-7 w-auto" />
      {/if}
      {#if !collapsed && theme?.showBrandName !== false}
        <span class="truncate font-semibold text-text">{theme?.brandName}</span>
      {/if}
    </div>
    <nav class="space-y-1 p-2">
      <a
        href="/"
        class={itemClass(path === "/")}
        title={collapsed ? t("nav.dashboard") : undefined}
      >
        <LayoutDashboard size={18} class="shrink-0 text-text-muted" />
        {#if !collapsed}<span class="truncate">{t("nav.dashboard")}</span>{/if}
      </a>
      {#if !isPortal}
        <!-- The team agenda is a staff surface; the portal shell (#193) has no business here. -->
        <a
          href="/calendar"
          class={itemClass(path.startsWith("/calendar"))}
          title={collapsed ? t("nav.calendar") : undefined}
        >
          <CalendarDays size={18} class="shrink-0 text-text-muted" />
          {#if !collapsed}<span class="truncate">{t("nav.calendar")}</span>{/if}
        </a>
      {/if}
      {#each navEntries as entry (entry.kind === "group" ? `g:${entry.key}` : entry.item.key)}
        {#if entry.kind === "item"}
          {@const Icon = entry.item.icon}
          <a
            href={entry.item.href}
            class={itemClass(path.startsWith(entry.item.href))}
            title={collapsed ? entry.item.label() : undefined}
          >
            {#if Icon}
              <Icon size={18} class="shrink-0 text-text-muted" />
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
              {#if Icon}<Icon size={18} class="shrink-0 text-text-muted" />{/if}
            </a>
          {/each}
        {:else}
          {@const open = isGroupOpen(entry)}
          <button
            type="button"
            class="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-text hover:bg-surface"
            onclick={() => toggleGroup(entry.key)}
            aria-expanded={open}
          >
            <Handshake size={18} class="shrink-0 text-text-muted" />
            <span class="flex-1 truncate text-left">{t(`nav.group.${entry.key}`)}</span>
            {#if open}
              <ChevronDown size={14} class="shrink-0 text-text-muted" />
            {:else}
              <ChevronRight size={14} class="shrink-0 text-text-muted" />
            {/if}
          </button>
          {#if open}
            <div class="space-y-0.5">
              {#each entry.items as item (item.key)}
                {@const Icon = item.icon}
                <a
                  href={item.href}
                  class="flex items-center gap-2.5 rounded-lg py-1.5 pl-9 pr-3 text-sm text-text-muted hover:bg-surface
                    {path.startsWith(item.href) ? 'bg-surface font-medium text-text' : ''}"
                >
                  {#if Icon}<Icon size={15} class="shrink-0 text-text-muted" />{/if}
                  <span class="truncate">{item.label()}</span>
                </a>
              {/each}
            </div>
          {/if}
        {/if}
      {/each}
      {#if showOverview}
        <a
          href="/overview"
          class={itemClass(path.startsWith("/overview"))}
          title={collapsed ? t("nav.overview") : undefined}
        >
          <BarChart3 size={18} class="shrink-0 text-text-muted" />
          {#if !collapsed}<span class="truncate">{t("nav.overview")}</span>{/if}
        </a>
      {/if}
      {#if showSettings}
        <a
          href="/settings"
          class={itemClass(path.startsWith("/settings") && !path.startsWith("/settings/account"))}
          title={collapsed ? t("nav.settings") : undefined}
        >
          <Settings size={18} class="shrink-0 text-text-muted" />
          {#if !collapsed}<span class="truncate">{t("nav.settings")}</span>{/if}
        </a>
      {/if}
      {#if user?.isInstanceAdmin}
        <a
          href="/instance"
          class={itemClass(path.startsWith("/instance"))}
          title={collapsed ? t("nav.instance") : undefined}
        >
          <ServerCog size={18} class="shrink-0 text-text-muted" />
          {#if !collapsed}<span class="truncate">{t("nav.instance")}</span>{/if}
        </a>
      {/if}
    </nav>
    <div class="p-2">
      <button
        type="button"
        class="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-text-muted hover:bg-surface hover:text-text"
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

  <!-- `min-w-0`, because a flex item defaults to `min-width: auto` and is therefore sized by its
       widest descendant, not by the row. Without it one over-wide page didn't scroll or clip — it
       *grew the shell*, so <body> laid out at 716px on a 360px phone while `initial-scale=1` kept
       one CSS pixel on one device pixel. The right half fell off screen, which reads as "the app
       loaded zoomed in", and pinch-zooming out revealed the whole (correct-looking) layout. That
       was issue #36. This also lets the inner `overflow-x-auto` wrappers do their job. -->
  <div class="flex min-w-0 flex-1 flex-col">
    {#if theme?.demoMode}
      <!-- Public demo (issue #141): persistent, no dismiss control — it re-renders every
           navigation, so it is dismissal-proof by construction. -->
      <div
        class="bg-sky-600 px-4 py-2 text-center text-sm font-medium text-white sm:px-6"
        role="status"
      >
        {t("demo.banner", { minutes: theme.demoResetMinutes })}
      </div>
    {/if}
    {#if user?.impersonatedBy}
      <!-- Impersonation is never silent (issue #26): banner on every screen, one-click stop. -->
      <div
        class="flex items-center justify-between gap-3 bg-amber-500 px-4 py-2 text-sm font-medium text-amber-950 sm:px-6"
      >
        <span class="flex min-w-0 items-center gap-2">
          <VenetianMask size={16} class="shrink-0" />
          <span class="truncate">
            {t("instance.impersonation_banner", {
              actor: user.impersonatedBy,
              target: user.full_name || user.email,
            })}
          </span>
        </span>
        <form method="POST" action="/impersonation/stop">
          <button
            class="shrink-0 rounded-lg bg-amber-950/10 px-3 py-1 font-semibold hover:bg-amber-950/20"
          >
            {t("instance.impersonation_stop")}
          </button>
        </form>
      </div>
    {/if}
    <header
      class="flex h-14 items-center justify-between gap-4 border-b border-border bg-surface-raised px-4 text-sm sm:justify-end sm:px-6"
    >
      <button
        type="button"
        class="rounded-lg p-2 text-text-muted hover:bg-surface sm:hidden"
        onclick={() => (mobileNavOpen = true)}
        aria-label={t("nav.expand")}
      >
        <Menu size={20} />
      </button>
      <div class="flex items-center gap-1">
        {#if hasAssistant}
          <button
            type="button"
            class="rounded-lg p-2 text-text-muted hover:bg-surface hover:text-brand"
            aria-label={t("ai.assistant.title")}
            title={t("ai.assistant.title")}
            onclick={() => (assistantOpen = true)}
          >
            <Sparkles size={18} />
          </button>
        {/if}
        {#if hasNotifications}
          <NotificationBell count={page.data.unreadCount ?? 0} />
        {/if}
        <div class="relative" data-profile-menu>
          <button
            type="button"
            class="flex items-center gap-2 rounded-full py-1 pl-1 pr-3 hover:bg-surface"
            onclick={() => (profileOpen = !profileOpen)}
            aria-haspopup="menu"
            aria-expanded={profileOpen}
          >
            <Avatar
              name={user?.full_name}
              email={user?.email}
              avatarUrl={user?.avatarUrl ?? null}
              size="md"
            />
            <span class="hidden font-medium text-text md:inline">
              {user?.full_name || user?.email}
            </span>
          </button>

          {#if profileOpen}
            <div
              role="menu"
              class="absolute right-0 z-30 mt-1 w-64 rounded-xl border border-border bg-surface-raised py-1 shadow-lg"
            >
              <div class="border-b border-border px-4 py-3">
                <p class="truncate text-sm font-semibold text-text">
                  {user?.full_name || user?.email}
                </p>
                {#if user?.full_name}
                  <p class="truncate text-xs text-text-muted">{user?.email}</p>
                {/if}
              </div>
              {#if !isPortal && theme?.enabledModules?.includes("hr") && can(user, "hr.dossier.read")}
                <!-- The personal page (hr module): leave, contract, dossier documents. -->
                <a
                  href="/me"
                  class="flex items-center gap-2 px-4 py-2 text-sm text-text hover:bg-surface"
                  onclick={() => (profileOpen = false)}
                >
                  <FileText size={16} class="text-text-muted" />
                  {t("header.my_page")}
                </a>
              {/if}
              <a
                href="/settings/account"
                class="flex items-center gap-2 px-4 py-2 text-sm text-text hover:bg-surface"
                onclick={() => (profileOpen = false)}
              >
                <UserRound size={16} class="text-text-muted" />
                {t("header.my_settings")}
              </a>
              <!-- Personal notification preferences moved to the bell popover's gear (#163);
                   the redundant profile-menu entry is gone. The bell's gear is ungated the same
                   way this was (tied to hasNotifications, not settings.*). -->
              <form method="POST" action="/logout" class="border-t border-border">
                <button
                  class="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-text hover:bg-surface"
                >
                  <LogOut size={16} class="text-text-muted" />
                  {t("auth.sign_out")}
                </button>
              </form>
            </div>
          {/if}
        </div>
      </div>
    </header>

    <main class="flex-1 p-6">
      {#if !isPortal}
        <!-- Breadcrumbs on every page (owner request): rendered once here, derived from the
             path and the page's own loaded data — no screen can ship without them. -->
        <Breadcrumbs crumbs={breadcrumbsFor(path, page.data)} />
      {/if}
      {@render children()}
    </main>
  </div>
</div>

{#if hasAssistant}
  <SlideOver bind:open={assistantOpen} title={t("ai.assistant.title")}>
    <AssistantPanel context={assistantContext} />
  </SlideOver>
{/if}
