<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let { children } = $props();

  const path = $derived(page.url.pathname);
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<div class="mb-4 flex items-center gap-1" data-sveltekit-preload-data="hover">
  <a href="/overview" class={tabClass(path === "/overview")}>{t("overview.tab.hours")}</a>
  <a href="/overview/productivity" class={tabClass(path.startsWith("/overview/productivity"))}>
    {t("overview.tab.productivity")}
  </a>
  <a href="/overview/revenue" class={tabClass(path.startsWith("/overview/revenue"))}>
    {t("overview.tab.revenue")}
  </a>
</div>

{@render children()}
