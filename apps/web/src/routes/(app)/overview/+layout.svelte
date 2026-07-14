<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  let { children } = $props();

  const path = $derived(page.url.pathname);
  const canTime = $derived(can(page.data.user, "time.report.read"));
  const canMarketing = $derived(can(page.data.user, "marketing.report.read"));
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

<div class="mb-4 flex flex-wrap items-center gap-1" data-sveltekit-preload-data="hover">
  {#if canTime}
    <a href="/overview" class={tabClass(path === "/overview")}>{t("overview.tab.hours")}</a>
    <a href="/overview/productivity" class={tabClass(path.startsWith("/overview/productivity"))}>
      {t("overview.tab.productivity")}
    </a>
    <a href="/overview/revenue" class={tabClass(path.startsWith("/overview/revenue"))}>
      {t("overview.tab.revenue")}
    </a>
  {/if}
  {#if canMarketing}
    <a href="/overview/marketing" class={tabClass(path.startsWith("/overview/marketing"))}>
      {t("overview.tab.marketing")}
    </a>
  {/if}
</div>

{@render children()}
