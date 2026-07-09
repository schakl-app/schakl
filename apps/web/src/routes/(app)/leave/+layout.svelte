<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  let { children } = $props();

  const path = $derived(page.url.pathname);
  const canManage = $derived(page.data.user?.canManage ?? false);
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-neutral-600 hover:bg-neutral-100"
    }`;
</script>

{#if canManage}
  <div class="mb-4 flex items-center gap-1" data-sveltekit-preload-data="hover">
    <a href="/leave" class={tabClass(path === "/leave")}>{t("leave.tab.mine")}</a>
    <a href="/leave/team" class={tabClass(path.startsWith("/leave/team"))}>
      {t("leave.tab.team")}
    </a>
  </div>
{/if}

{@render children()}
