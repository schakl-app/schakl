<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  let { children } = $props();

  const path = $derived(page.url.pathname);
  const enabled = $derived(page.data.theme?.enabledModules ?? []);
  const canTime = $derived(can(page.data.user, "time.report.read"));
  // Projecten reads the projects module's budgets beside the time module's hours, so it needs
  // both modules' keys — the tab mirrors the calls it makes, not the screen it is about (#310).
  const canProjects = $derived(
    canTime && enabled.includes("projects") && can(page.data.user, "projects.project.read"),
  );
  const canMarketing = $derived(can(page.data.user, "marketing.overview.read"));
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
  const tabs = $derived(
    [
      canTime && { href: "/overview", key: "overview.tab.overview", exact: true },
      canTime && { href: "/overview/revenue", key: "overview.tab.revenue" },
      canProjects && { href: "/overview/projects", key: "overview.tab.projects" },
      canTime && { href: "/overview/employees", key: "overview.tab.employees" },
      canTime && { href: "/overview/hours", key: "overview.tab.hours" },
      canMarketing && { href: "/overview/marketing", key: "overview.tab.marketing" },
    ].filter((tab): tab is { href: string; key: string; exact?: boolean } => Boolean(tab)),
  );
  const active = (tab: { href: string; exact?: boolean }) =>
    tab.exact ? path === tab.href : path.startsWith(tab.href);
</script>

<!-- A viewer whose permissions leave only one tab gets no tab row at all (docs/UX.md). -->
{#if tabs.length > 1}
  <div class="mb-4 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
    {#each tabs as tab (tab.href)}
      <a href={tab.href} class={tabClass(active(tab))}>{t(tab.key)}</a>
    {/each}
  </div>
{/if}

{@render children()}
